// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package sandbox

import (
	"gopkg.in/yaml.v3"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/security"
)

// This file is the THIRD ACC policy-translation emitter, alongside the
// Gatekeeper ConstraintTemplates (governance/gatekeeper.go) and the OVN/Cilium
// egress policies (security/fqdn_egress.go). It renders a corpus's Cat-A/B
// governance into an OpenShell SandboxPolicy document.
//
// Unlike the other two emitters — which ClusterUpsert a Kubernetes object —
// this one is a PURE FUNCTION. OpenShell ships NO Kubernetes policy CRD (the
// Phase-0 spike confirmed the K8s driver mounts no policy resource); a policy
// is delivered to the gateway out-of-band via
//
//	openshell policy set <sandbox> --policy <file>
//
// So the emitter's job is to produce the YAML document; DELIVERING it to the
// gateway is the Phase-3 pod-attach concern (not a ClusterUpsert).
//
// Category mapping (confirmed against the OpenShell policy schema on the spike):
//   - filesystem + process + landlock are LOCKED at sandbox creation → Cat-A
//   - the network endpoint allow-set is HOT-RELOADABLE (`policy set`) → Cat-B
//   - the egress enforcement posture (enforce vs `audit`) is the Cat-C
//     observe→propose axis: `audit` logs would-be denials without blocking
//     (never auto-enforce, `_NEVER_AUTOEXEC`), driven by NetworkPolicy.Mode.
//     Cat-C's learned RULES (arbiter-signed adaptive bundle) are dispatch-layer
//     and load in the runtime/OPA path — not kernel policy the operator emits.
//
// The struct tags mirror crates/openshell-policy/src/lib.rs (the serde schema
// `openshell policy set --policy` parses) and match the shipped example
// examples/sandbox-policy-quickstart/policy.yaml.

// --- OpenShell policy document (marshals to the gateway's --policy YAML) ------

type sandboxPolicyDoc struct {
	Version          int                      `yaml:"version"`
	FilesystemPolicy filesystemPolicy         `yaml:"filesystem_policy"`
	Landlock         landlockPolicy           `yaml:"landlock"`
	Process          processPolicy            `yaml:"process"`
	NetworkPolicies  map[string]networkPolicy `yaml:"network_policies,omitempty"`
}

type filesystemPolicy struct {
	IncludeWorkdir bool     `yaml:"include_workdir"`
	ReadOnly       []string `yaml:"read_only"`
	ReadWrite      []string `yaml:"read_write"`
}

type landlockPolicy struct {
	Compatibility string `yaml:"compatibility"`
}

type processPolicy struct {
	RunAsUser  string `yaml:"run_as_user"`
	RunAsGroup string `yaml:"run_as_group"`
}

type networkPolicy struct {
	Name      string           `yaml:"name"`
	Endpoints []policyEndpoint `yaml:"endpoints"`
	Binaries  []policyBinary   `yaml:"binaries"`
}

type policyEndpoint struct {
	Host        string `yaml:"host"`
	Port        int    `yaml:"port"`
	Protocol    string `yaml:"protocol,omitempty"`
	Enforcement string `yaml:"enforcement,omitempty"`
	Access      string `yaml:"access,omitempty"`
}

type policyBinary struct {
	Path string `yaml:"path"`
}

// --- Cat-A static containment (locked at sandbox creation) --------------------

const (
	// Landlock compatibility values (crates/openshell-core/src/policy.rs).
	landlockBestEffort      = "best_effort"
	landlockHardRequirement = "hard_requirement"

	// The sandbox-internal unprivileged user/group OpenShell runs the agent
	// process as — mirrors the operator's RunAsNonRoot AgentContainer
	// SecurityContext at the kernel process domain (Cat-A "no priv-esc").
	sandboxRunAsUser  = "sandbox"
	sandboxRunAsGroup = "sandbox"
)

// catAReadOnlyPaths is the system read-only allow-list: the standard
// distro/runtime trees an agent needs to execute but must never mutate.
func catAReadOnlyPaths() []string {
	return []string{
		"/usr", "/lib", "/lib64", "/bin", "/sbin",
		"/etc", "/app", "/var/log", "/proc", "/dev/urandom",
	}
}

// catAReadWritePaths mirrors acc/workspace.py's /workspace-only containment —
// but enforced at the Landlock KERNEL level rather than by userland path checks.
func catAReadWritePaths() []string {
	return []string{"/workspace", "/tmp", "/dev/null"}
}

// --- Cat-B egress (hot-reloadable) -------------------------------------------

const (
	inferencePolicyKey  = "acc_inference"
	inferencePolicyName = "acc-inference-egress"

	egressPort     = 443
	egressProtocol = "rest"
	// Enforcement postures — OpenShell honours exactly these two
	// (crates/openshell-cli/src/policy_update.rs). "enforce" blocks disallowed
	// egress (Cat-B); "audit" is the observe→propose mode — would-be denials are
	// LOGGED, not blocked (Cat-C / `_NEVER_AUTOEXEC`; a human/arbiter promotes
	// audit→enforce). Selected from NetworkPolicy.Mode.
	egressEnforce = "enforce"
	egressAudit   = "audit"
	// Inference POSTs prompts to the LLM, so the endpoint needs read-write;
	// the "read-only" preset would block POST/PUT.
	egressAccess = "read-write"
)

// agentEgressBinaries is the set of executables permitted to reach the
// inference allow-set. Everything else is default-denied by OpenShell's
// per-binary network model. (Refined against the concrete agent image in the
// Phase-3 live smoke; curl + python cover the agent HTTP client + tooling.)
func agentEgressBinaries() []policyBinary {
	return []policyBinary{
		{Path: "/usr/bin/curl"},
		{Path: "/usr/bin/python3"},
		{Path: "/usr/local/bin/python"},
	}
}

// sandboxFailClosed reports the effective FailClosed posture (D3). Tri-state:
// a nil block or nil FailClosed defaults to true (the +kubebuilder:default),
// matching how the API server defaults it at admission — so the emitter is
// faithful even when handed an un-defaulted object in a unit test.
func sandboxFailClosed(corpus *accv1alpha1.AgentCorpus) bool {
	s := corpus.Spec.Sandbox
	return s == nil || s.FailClosed == nil || *s.FailClosed
}

// endpointEnforcement selects the egress enforcement posture from
// NetworkPolicy.Mode — the SAME field the cluster NetworkPolicy audit/enforce
// canary uses, so the OpenShell sandbox egress and the K8s NetworkPolicy share
// one posture (no independent knob to drift). "audit" is OpenShell's
// observe→propose mode: would-be denials are logged, not blocked — the Cat-C /
// `_NEVER_AUTOEXEC` stance, promoted to enforce by a human/arbiter. This
// governs only the network egress (Cat-B/C); Cat-A filesystem/process/landlock
// stays enforced regardless — the constitutional floor never audits.
func endpointEnforcement(np *accv1alpha1.NetworkPolicySpec) string {
	if np != nil && np.Mode == egressAudit {
		return egressAudit
	}
	return egressEnforce
}

// buildSandboxPolicy renders the corpus's Cat-A/B governance into an OpenShell
// SandboxPolicy document.
func buildSandboxPolicy(corpus *accv1alpha1.AgentCorpus) *sandboxPolicyDoc {
	// Cat-A landlock posture follows fail-closed (D3): a mandatory Cat-A cage
	// means kernel filesystem enforcement must not silently degrade, so require
	// Landlock; otherwise best-effort (degrade cleanly where absent).
	compat := landlockBestEffort
	if sandboxFailClosed(corpus) {
		compat = landlockHardRequirement
	}

	// Cat-B: the egress allow-set from the SINGLE shared source, so the
	// OpenShell policy, the OVN EgressFirewall, and the Cilium FQDN policy
	// cannot drift (three-surface parity test).
	fqdns := security.ExternalEgressFQDNs(corpus.Spec.NetworkPolicy)
	enforcement := endpointEnforcement(corpus.Spec.NetworkPolicy)
	endpoints := make([]policyEndpoint, 0, len(fqdns))
	for _, host := range fqdns {
		endpoints = append(endpoints, policyEndpoint{
			Host:        host,
			Port:        egressPort,
			Protocol:    egressProtocol,
			Enforcement: enforcement,
			Access:      egressAccess,
		})
	}

	doc := &sandboxPolicyDoc{
		Version: 1,
		FilesystemPolicy: filesystemPolicy{
			IncludeWorkdir: true,
			ReadOnly:       catAReadOnlyPaths(),
			ReadWrite:      catAReadWritePaths(),
		},
		Landlock: landlockPolicy{Compatibility: compat},
		Process: processPolicy{
			RunAsUser:  sandboxRunAsUser,
			RunAsGroup: sandboxRunAsGroup,
		},
	}

	// There is always an allow-set (the built-in default), but guard for
	// clarity: an empty set emits no network_policies rather than an empty map.
	if len(endpoints) > 0 {
		doc.NetworkPolicies = map[string]networkPolicy{
			inferencePolicyKey: {
				Name:      inferencePolicyName,
				Endpoints: endpoints,
				Binaries:  agentEgressBinaries(),
			},
		}
	}
	return doc
}

// BuildSandboxPolicyYAML renders the corpus's OpenShell sandbox policy as the
// YAML the gateway consumes via `openshell policy set <sandbox> --policy
// <file>`. Exported for the Phase-3 pod-attach delivery seam and the tests.
func BuildSandboxPolicyYAML(corpus *accv1alpha1.AgentCorpus) ([]byte, error) {
	return yaml.Marshal(buildSandboxPolicy(corpus))
}

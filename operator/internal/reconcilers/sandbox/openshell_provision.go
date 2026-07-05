// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package sandbox

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// OpenShell Model 2 pod provisioning (proposal 051).
//
// Model 1 ("cage the whole agent pod") proved infeasible — an OpenShell sandbox
// is a single-image runtime and cannot carry ACC's rich multi-container agent
// pod. Model 2 keeps the agent as its normal StatefulSet pod and delegates CODE
// EXECUTION (shell/python_exec, the untrusted surface) into a per-agent
// gateway-created sandbox carrying the corpus's Cat-A/B/C policy. The runtime
// half is acc.sandbox (the exec skills call `openshell sandbox exec`); this is
// the operator half that provisions the sandbox + the env the runtime reads.
//
// Attach shape (mirrors ApplySpiffeSidecar): when a corpus opts into sandboxing
// AND a Gateway is configured, the agent pod gains
//   - an `openshell-sandbox-create` initContainer (reusing the agent image,
//     which ships the `openshell` CLI) that registers the gateway and runs
//     `openshell sandbox create --name <n> --from <img> --policy <file>`;
//   - the corpus's Cat-A/B/C policy mounted from a ConfigMap (BuildSandboxPolicyYAML);
//   - a shared emptyDir carrying the CLI's gateway registration from the
//     initContainer to the agent container (XDG_CONFIG_HOME) so the agent's
//     `sandbox exec` reaches the same gateway;
//   - env (ACC_SANDBOX_NAME + OPENSHELL_GATEWAY) that gates the runtime shim,
//     plus `envFrom` the by-name credentials Secret carrying the gateway's OIDC
//     client credentials.
//
// INERT until spec.sandbox.gatewayURL is set (SandboxWorkloadActive) — enabling
// the opt-in block alone changes nothing. The exact `openshell` CLI auth flags
// are pinned against the live gateway in the Phase-4 smoke; this wires the pod
// STRUCTURE (verified by unit tests + go build), which the smoke confirms.
const (
	// sandboxPolicyDir is where the rendered Cat-A/B/C policy is mounted for
	// `sandbox create --policy`.
	sandboxPolicyDir  = "/etc/acc/openshell"
	sandboxPolicyFile = "policy.yaml"

	// sandboxCLIConfigDir is the shared emptyDir that carries the openshell
	// CLI's gateway registration from the create initContainer to the agent
	// container. Set as XDG_CONFIG_HOME on both so the Python CLI (via
	// platformdirs) reads/writes its config there.
	sandboxCLIConfigDir = "/var/run/openshell-cli"

	// gatewayRegisteredName is the local name the create initContainer
	// registers the gateway under (`gateway add <url> --name`); the agent's
	// exec then selects it via OPENSHELL_GATEWAY.
	gatewayRegisteredName = "acc"

	// Volume + container names.
	sandboxCLIConfigVolume = "openshell-cli-config"
	sandboxPolicyVolume    = "openshell-policy"
	sandboxCreateInit      = "openshell-sandbox-create"

	// policyConfigMapSuffix is appended to the agent deployment name to form
	// the per-agent policy ConfigMap name.
	policyConfigMapSuffix = "-openshell-policy"
)

// SandboxName is the OpenShell sandbox an agent execs into — deterministic per
// agent deployment (caged agents are replicas=1; per-pod naming is a later
// refinement). It is the ACC_SANDBOX_NAME the runtime shim gates on.
func SandboxName(deployName string) string { return deployName }

// PolicyConfigMapName is the per-agent ConfigMap carrying the rendered
// Cat-A/B/C OpenShell policy the sandbox is created with.
func PolicyConfigMapName(deployName string) string {
	return deployName + policyConfigMapSuffix
}

// BuildPolicyConfigMap renders the corpus's Cat-A/B/C policy (BuildSandboxPolicyYAML,
// the Phase-2 emitter) into a ConfigMap the create initContainer mounts and
// passes to `sandbox create --policy`. The caller owns/labels it via Upsert.
func BuildPolicyConfigMap(corpus *accv1alpha1.AgentCorpus, name, namespace string) (*corev1.ConfigMap, error) {
	policy, err := BuildSandboxPolicyYAML(corpus)
	if err != nil {
		return nil, err
	}
	return &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: namespace},
		Data:       map[string]string{sandboxPolicyFile: string(policy)},
	}, nil
}

// ApplyOpenShellSandbox mutates a freshly-built agent pod template in place to
// provision + delegate into a per-agent OpenShell sandbox (Model 2). No-op
// unless the corpus opted in AND a Gateway is configured (SandboxWorkloadActive),
// so it is safe to call unconditionally after the base pod is built (mirrors
// ApplySpiffeSidecar). The agent container is expected at index 0.
//
// agentImage is the agent's own image — the default sandbox `--from` image so
// exec'd code has the agent's toolchain; spec.sandbox.image overrides.
func ApplyOpenShellSandbox(
	tmpl *corev1.PodTemplateSpec,
	corpus *accv1alpha1.AgentCorpus,
	sandboxName, policyConfigMapName, agentImage string,
) {
	if !SandboxWorkloadActive(corpus) {
		return
	}
	s := corpus.Spec.Sandbox
	podSpec := &tmpl.Spec

	fromImage := s.Image
	if fromImage == "" {
		fromImage = agentImage
	}

	// Env the openshell CLI + the runtime shim read. OPENSHELL_GATEWAY selects
	// the gateway the initContainer registered by name; OPENSHELL_GATEWAY_URL is
	// the create-time registration target. OIDC client credentials arrive via
	// envFrom the by-name Secret (operator stays agnostic to the var names).
	sandboxEnv := []corev1.EnvVar{
		{Name: "ACC_SANDBOX_NAME", Value: sandboxName},
		{Name: "OPENSHELL_GATEWAY", Value: gatewayRegisteredName},
		{Name: "XDG_CONFIG_HOME", Value: sandboxCLIConfigDir},
	}
	var credsEnvFrom []corev1.EnvFromSource
	if s.CredentialsSecret != "" {
		credsEnvFrom = []corev1.EnvFromSource{{
			SecretRef: &corev1.SecretEnvSource{
				LocalObjectReference: corev1.LocalObjectReference{Name: s.CredentialsSecret},
				Optional:             ptrBool(true),
			},
		}}
	}

	// Volumes: the shared CLI-config emptyDir + the policy ConfigMap.
	podSpec.Volumes = append(podSpec.Volumes,
		corev1.Volume{
			Name:         sandboxCLIConfigVolume,
			VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
		},
		corev1.Volume{
			Name: sandboxPolicyVolume,
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: policyConfigMapName},
				},
			},
		},
	)

	// The create initContainer: register the gateway, then create the sandbox
	// with the corpus's policy. `set -e` fails the pod (D3 fail-closed) if the
	// cage cannot be provisioned — the agent never starts un-caged. Reuses the
	// agent image (which ships the openshell CLI). The confirmed CLI surface is
	// `gateway add <url> --name` + `sandbox create --name --from --policy`; the
	// OIDC auth (via envFrom creds) + idempotency-on-restart are pinned in the
	// Phase-4 live smoke.
	createScript := "set -e\n" +
		`openshell gateway add "$OPENSHELL_GATEWAY_URL" --name "$OPENSHELL_GATEWAY"` + "\n" +
		`openshell sandbox create --name "$ACC_SANDBOX_NAME" --from "$OPENSHELL_SANDBOX_IMAGE" --policy ` +
		sandboxPolicyDir + "/" + sandboxPolicyFile + "\n"

	initEnv := append([]corev1.EnvVar{
		{Name: "OPENSHELL_GATEWAY_URL", Value: s.GatewayURL},
		{Name: "OPENSHELL_SANDBOX_IMAGE", Value: fromImage},
	}, sandboxEnv...)

	podSpec.InitContainers = append(podSpec.InitContainers, corev1.Container{
		Name:            sandboxCreateInit,
		Image:           agentImage,
		SecurityContext: nil, // inherits the pod SecurityContext (SCC-injected UID)
		Command:         []string{"/bin/sh", "-c", createScript},
		Env:             initEnv,
		EnvFrom:         credsEnvFrom,
		VolumeMounts: []corev1.VolumeMount{
			{Name: sandboxCLIConfigVolume, MountPath: sandboxCLIConfigDir},
			{Name: sandboxPolicyVolume, MountPath: sandboxPolicyDir, ReadOnly: true},
		},
	})

	// Agent container (index 0): the shared CLI config (so `sandbox exec` finds
	// the registered gateway) + the sandbox env + the creds envFrom.
	if len(podSpec.Containers) > 0 {
		agent := &podSpec.Containers[0]
		agent.VolumeMounts = append(agent.VolumeMounts, corev1.VolumeMount{
			Name:      sandboxCLIConfigVolume,
			MountPath: sandboxCLIConfigDir,
		})
		agent.Env = append(agent.Env, sandboxEnv...)
		agent.EnvFrom = append(agent.EnvFrom, credsEnvFrom...)
	}
}

// ptrBool is a tiny local helper (k8s.io/utils/ptr is a dep, but a one-liner
// keeps this file's import surface minimal — mirrors spiffe_sidecar.go).
func ptrBool(b bool) *bool { return &b }

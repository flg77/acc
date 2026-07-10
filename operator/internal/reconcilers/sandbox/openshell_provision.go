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

	// TLS/OIDC credential wiring (Phase-4 smoke, #172/#174): the credentials
	// Secret is mounted READ-ONLY at sandboxCredsSrcDir; the init container
	// copies the mТLS client cert (tls.crt/tls.key) + server CA (ca.crt) into a
	// WRITABLE shared emptyDir (sandboxCredsDir) that both the init and the
	// agent read. The openshell CLI reads its mTLS client cert from
	// OPENSHELL_LOCAL_TLS_DIR and trusts the gateway CA via SSL_CERT_FILE (a
	// combined bundle = system trust + the server ca.crt, so the agent's OTHER
	// TLS — e.g. the LLM endpoint — keeps working even when no ca.crt is
	// supplied). OIDC client credentials are projected via explicit secretKeyRef
	// (NOT envFrom — the operator-oidc Secret's keys are hyphenated and would be
	// dropped as invalid env-var names, #174).
	sandboxCredsSrcDir = "/var/run/openshell-creds-src"
	sandboxCredsDir    = "/var/run/openshell-creds"
	sandboxCABundle    = sandboxCredsDir + "/ca-bundle.crt"

	// Volume + container names.
	sandboxCLIConfigVolume = "openshell-cli-config"
	sandboxPolicyVolume    = "openshell-policy"
	sandboxCredsSrcVolume  = "openshell-creds-src"
	sandboxCredsVolume     = "openshell-creds"
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
	// the create-time registration target. XDG_CONFIG_HOME (shared emptyDir)
	// carries the gateway registration + cached OIDC token from the init to the
	// agent. The TLS/OIDC trust env (SSL_CERT_FILE, OPENSHELL_LOCAL_TLS_DIR,
	// OPENSHELL_NO_BROWSER) let the CLI do the headless mTLS + OIDC
	// client-credentials handshake against a TLS+OIDC gateway.
	sandboxEnv := []corev1.EnvVar{
		{Name: "ACC_SANDBOX_NAME", Value: sandboxName},
		{Name: "OPENSHELL_GATEWAY", Value: gatewayRegisteredName},
		{Name: "XDG_CONFIG_HOME", Value: sandboxCLIConfigDir},
		{Name: "OPENSHELL_NO_BROWSER", Value: "1"},
		{Name: "SSL_CERT_FILE", Value: sandboxCABundle},
		{Name: "OPENSHELL_LOCAL_TLS_DIR", Value: sandboxCredsDir},
	}
	// OIDC client credentials via EXPLICIT secretKeyRef (#174) — NOT envFrom,
	// which silently drops the operator-oidc Secret's hyphenated keys as invalid
	// env-var names. All Optional: a plaintext dev gateway leaves them empty and
	// the create script skips the --oidc-* flags.
	oidcEnv := oidcSecretEnv(s.CredentialsSecret)

	// Volumes: shared CLI-config emptyDir, policy ConfigMap, the read-only
	// credentials Secret source, and the writable creds emptyDir the init
	// populates (client cert + combined CA bundle).
	podSpec.Volumes = append(podSpec.Volumes,
		corev1.Volume{
			Name:         sandboxCLIConfigVolume,
			VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
		},
		corev1.Volume{
			Name:         sandboxCredsVolume,
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
	if s.CredentialsSecret != "" {
		podSpec.Volumes = append(podSpec.Volumes, corev1.Volume{
			Name: sandboxCredsSrcVolume,
			VolumeSource: corev1.VolumeSource{
				Secret: &corev1.SecretVolumeSource{
					SecretName: s.CredentialsSecret,
					Optional:   ptrBool(true),
				},
			},
		})
	}

	// The create initContainer: stage TLS material, register the gateway
	// (idempotent — #173), then create the sandbox with the corpus's policy.
	// `set -e` fails the pod (D3 fail-closed) if the cage cannot be provisioned
	// — the agent never starts un-caged. Reuses the agent image (which ships the
	// openshell CLI). Auth combo proven in the Phase-4 live smoke: headless mTLS
	// (client cert from OPENSHELL_LOCAL_TLS_DIR) + OIDC client-credentials
	// (--oidc-issuer/--oidc-client-id/--oidc-audience + OPENSHELL_OIDC_CLIENT_SECRET).
	createScript := "set -e\n" +
		"mkdir -p " + sandboxCredsDir + "\n" +
		// Combined CA bundle: system trust + the gateway's server CA (when supplied).
		"cat /etc/pki/tls/certs/ca-bundle.crt > " + sandboxCABundle + " 2>/dev/null || true\n" +
		"if [ -f " + sandboxCredsSrcDir + "/ca.crt ]; then " +
		"cat " + sandboxCredsSrcDir + "/ca.crt >> " + sandboxCABundle + "; " +
		"cp " + sandboxCredsSrcDir + "/ca.crt " + sandboxCredsDir + "/ca.crt; fi\n" +
		"for f in tls.crt tls.key; do [ -f " + sandboxCredsSrcDir + "/$f ] && " +
		"cp " + sandboxCredsSrcDir + "/$f " + sandboxCredsDir + "/$f || true; done\n" +
		// Idempotent gateway registration (#173): drop any stale entry first.
		`openshell gateway remove "$OPENSHELL_GATEWAY" >/dev/null 2>&1 || true` + "\n" +
		// OIDC flags only when an issuer is supplied (TLS+OIDC gateway); a
		// plaintext dev gateway registers without them.
		`OIDC_ARGS=""` + "\n" +
		`if [ -n "$OPENSHELL_OIDC_ISSUER" ]; then ` +
		`OIDC_ARGS="--oidc-issuer $OPENSHELL_OIDC_ISSUER --oidc-client-id $OPENSHELL_OIDC_CLIENT_ID --oidc-audience $OPENSHELL_OIDC_AUDIENCE"; fi` + "\n" +
		`openshell gateway add "$OPENSHELL_GATEWAY_URL" --name "$OPENSHELL_GATEWAY" $OIDC_ARGS` + "\n" +
		`openshell sandbox create --name "$ACC_SANDBOX_NAME" --from "$OPENSHELL_SANDBOX_IMAGE" --policy ` +
		sandboxPolicyDir + "/" + sandboxPolicyFile + "\n"

	initEnv := append([]corev1.EnvVar{
		{Name: "OPENSHELL_GATEWAY_URL", Value: s.GatewayURL},
		{Name: "OPENSHELL_SANDBOX_IMAGE", Value: fromImage},
	}, sandboxEnv...)
	initEnv = append(initEnv, oidcEnv...)

	initMounts := []corev1.VolumeMount{
		{Name: sandboxCLIConfigVolume, MountPath: sandboxCLIConfigDir},
		{Name: sandboxCredsVolume, MountPath: sandboxCredsDir},
		{Name: sandboxPolicyVolume, MountPath: sandboxPolicyDir, ReadOnly: true},
	}
	if s.CredentialsSecret != "" {
		initMounts = append(initMounts, corev1.VolumeMount{
			Name: sandboxCredsSrcVolume, MountPath: sandboxCredsSrcDir, ReadOnly: true,
		})
	}
	podSpec.InitContainers = append(podSpec.InitContainers, corev1.Container{
		Name:            sandboxCreateInit,
		Image:           agentImage,
		SecurityContext: nil, // inherits the pod SecurityContext (SCC-injected UID)
		Command:         []string{"/bin/sh", "-c", createScript},
		Env:             initEnv,
		VolumeMounts:    initMounts,
	})

	// Agent container (index 0): the shared CLI config + creds dir (so
	// `sandbox exec` reuses the registered gateway + cached token + client cert)
	// plus the sandbox + OIDC env.
	if len(podSpec.Containers) > 0 {
		agent := &podSpec.Containers[0]
		agent.VolumeMounts = append(agent.VolumeMounts,
			corev1.VolumeMount{Name: sandboxCLIConfigVolume, MountPath: sandboxCLIConfigDir},
			corev1.VolumeMount{Name: sandboxCredsVolume, MountPath: sandboxCredsDir},
		)
		agent.Env = append(agent.Env, sandboxEnv...)
		agent.Env = append(agent.Env, oidcEnv...)
	}
}

// oidcSecretEnv projects the OpenShell gateway's OIDC client credentials from
// the by-name credentials Secret as explicit, fixed env-var names the CLI reads
// — mapping the Secret's canonical hyphenated keys (client-id/client-secret/
// issuer/audience) to OPENSHELL_OIDC_* (#174). All Optional so a plaintext dev
// gateway (empty CredentialsSecret, or a Secret without these keys) is a no-op.
func oidcSecretEnv(secretName string) []corev1.EnvVar {
	if secretName == "" {
		return nil
	}
	ref := func(key string) *corev1.EnvVarSource {
		return &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{Name: secretName},
			Key:                  key,
			Optional:             ptrBool(true),
		}}
	}
	return []corev1.EnvVar{
		{Name: "OPENSHELL_OIDC_CLIENT_SECRET", ValueFrom: ref("client-secret")},
		{Name: "OPENSHELL_OIDC_CLIENT_ID", ValueFrom: ref("client-id")},
		{Name: "OPENSHELL_OIDC_ISSUER", ValueFrom: ref("issuer")},
		{Name: "OPENSHELL_OIDC_AUDIENCE", ValueFrom: ref("audience")},
	}
}

// ptrBool is a tiny local helper (k8s.io/utils/ptr is a dep, but a one-liner
// keeps this file's import surface minimal — mirrors spiffe_sidecar.go).
func ptrBool(b bool) *bool { return &b }

// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package sandbox

import (
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

const testAgentImage = "quay.io/flg77/acc_images/acc-agent:test"

// activeSandboxCorpus opts in AND configures a Gateway → SandboxWorkloadActive.
func activeSandboxCorpus() *accv1alpha1.AgentCorpus {
	return policyCorpus(&accv1alpha1.SandboxSpec{
		Enabled:           ptr.To(true),
		GatewayURL:        "https://openshell.openshell.svc:8080",
		CredentialsSecret: "openshell-oidc",
	}, nil)
}

func basePod() *corev1.PodTemplateSpec {
	return &corev1.PodTemplateSpec{
		Spec: corev1.PodSpec{
			Containers: []corev1.Container{{Name: "agent", Image: testAgentImage}},
		},
	}
}

func findContainer(cs []corev1.Container, name string) *corev1.Container {
	for i := range cs {
		if cs[i].Name == name {
			return &cs[i]
		}
	}
	return nil
}

func hasVolume(vs []corev1.Volume, name string) bool {
	for _, v := range vs {
		if v.Name == name {
			return true
		}
	}
	return false
}

func envValue(env []corev1.EnvVar, name string) (string, bool) {
	for _, e := range env {
		if e.Name == name {
			return e.Value, true
		}
	}
	return "", false
}

func hasMount(ms []corev1.VolumeMount, name string) bool {
	for _, m := range ms {
		if m.Name == name {
			return true
		}
	}
	return false
}

func TestSandboxName(t *testing.T) {
	if got := SandboxName("coding-agent"); got != "coding-agent" {
		t.Errorf("SandboxName = %q, want coding-agent", got)
	}
}

func TestPolicyConfigMapName(t *testing.T) {
	if got := PolicyConfigMapName("coding-agent"); got != "coding-agent-openshell-policy" {
		t.Errorf("PolicyConfigMapName = %q, want coding-agent-openshell-policy", got)
	}
}

func TestBuildPolicyConfigMap(t *testing.T) {
	cm, err := BuildPolicyConfigMap(activeSandboxCorpus(), "cm-name", "ns")
	if err != nil {
		t.Fatalf("BuildPolicyConfigMap: %v", err)
	}
	if cm.Name != "cm-name" || cm.Namespace != "ns" {
		t.Errorf("meta = %q/%q, want cm-name/ns", cm.Name, cm.Namespace)
	}
	policy, ok := cm.Data[sandboxPolicyFile]
	if !ok {
		t.Fatalf("ConfigMap missing key %q; keys=%v", sandboxPolicyFile, cm.Data)
	}
	// The rendered document is the Phase-2 emitter's output (Cat-A defaults).
	if !strings.Contains(policy, "filesystem_policy") {
		t.Errorf("policy does not look like a SandboxPolicy:\n%s", policy)
	}
}

func TestApplyOpenShellSandbox_Active(t *testing.T) {
	pod := basePod()
	ApplyOpenShellSandbox(pod, activeSandboxCorpus(), "coding-agent",
		"coding-agent-openshell-policy", testAgentImage)

	// InitContainer that creates the sandbox with the corpus policy.
	init := findContainer(pod.Spec.InitContainers, sandboxCreateInit)
	if init == nil {
		t.Fatalf("no %q initContainer; got %v", sandboxCreateInit, pod.Spec.InitContainers)
	}
	if init.Image != testAgentImage {
		t.Errorf("init image = %q, want the agent image %q", init.Image, testAgentImage)
	}
	if len(init.Command) != 3 || init.Command[0] != "/bin/sh" {
		t.Fatalf("init command = %v, want /bin/sh -c <script>", init.Command)
	}
	script := init.Command[2]
	for _, want := range []string{
		"gateway add", "sandbox create --name",
		sandboxPolicyDir + "/" + sandboxPolicyFile,
	} {
		if !strings.Contains(script, want) {
			t.Errorf("create script missing %q:\n%s", want, script)
		}
	}
	if v, _ := envValue(init.Env, "OPENSHELL_GATEWAY_URL"); v != "https://openshell.openshell.svc:8080" {
		t.Errorf("init OPENSHELL_GATEWAY_URL = %q", v)
	}
	// Default sandbox image reuses the agent image.
	if v, _ := envValue(init.Env, "OPENSHELL_SANDBOX_IMAGE"); v != testAgentImage {
		t.Errorf("init OPENSHELL_SANDBOX_IMAGE = %q, want agent image", v)
	}

	// Volumes: shared CLI-config emptyDir + the policy ConfigMap.
	if !hasVolume(pod.Spec.Volumes, sandboxCLIConfigVolume) {
		t.Errorf("missing %q volume", sandboxCLIConfigVolume)
	}
	if !hasVolume(pod.Spec.Volumes, sandboxPolicyVolume) {
		t.Errorf("missing %q volume", sandboxPolicyVolume)
	}

	// Agent container gains the runtime-shim env + creds + shared config mount.
	agent := findContainer(pod.Spec.Containers, "agent")
	if v, ok := envValue(agent.Env, "ACC_SANDBOX_NAME"); !ok || v != "coding-agent" {
		t.Errorf("agent ACC_SANDBOX_NAME = %q,%v", v, ok)
	}
	if v, _ := envValue(agent.Env, "OPENSHELL_GATEWAY"); v != gatewayRegisteredName {
		t.Errorf("agent OPENSHELL_GATEWAY = %q, want %q", v, gatewayRegisteredName)
	}
	if v, _ := envValue(agent.Env, "XDG_CONFIG_HOME"); v != sandboxCLIConfigDir {
		t.Errorf("agent XDG_CONFIG_HOME = %q", v)
	}
	if !hasMount(agent.VolumeMounts, sandboxCLIConfigVolume) {
		t.Errorf("agent missing %q mount", sandboxCLIConfigVolume)
	}
	// OIDC client secret is projected via explicit secretKeyRef (#174), NOT
	// envFrom (which drops the Secret's hyphenated keys).
	if len(agent.EnvFrom) != 0 {
		t.Errorf("agent should use secretKeyRef, not EnvFrom: %v", agent.EnvFrom)
	}
	if !hasSecretKeyEnv(agent.Env, "OPENSHELL_OIDC_CLIENT_SECRET", "openshell-oidc", "client-secret") {
		t.Errorf("agent missing OPENSHELL_OIDC_CLIENT_SECRET secretKeyRef -> openshell-oidc/client-secret: %v", agent.Env)
	}
	if v, _ := envValue(agent.Env, "SSL_CERT_FILE"); v != sandboxCABundle {
		t.Errorf("agent SSL_CERT_FILE = %q, want %q", v, sandboxCABundle)
	}
	if v, _ := envValue(agent.Env, "OPENSHELL_LOCAL_TLS_DIR"); v != sandboxCredsDir {
		t.Errorf("agent OPENSHELL_LOCAL_TLS_DIR = %q, want %q", v, sandboxCredsDir)
	}
}

// hasSecretKeyEnv reports whether env has a var of the given name sourced from
// the given Secret name + key via secretKeyRef.
func hasSecretKeyEnv(env []corev1.EnvVar, name, secretName, key string) bool {
	for _, e := range env {
		if e.Name == name && e.ValueFrom != nil && e.ValueFrom.SecretKeyRef != nil &&
			e.ValueFrom.SecretKeyRef.Name == secretName && e.ValueFrom.SecretKeyRef.Key == key {
			return true
		}
	}
	return false
}

func TestApplyOpenShellSandbox_InertWhenGateOff(t *testing.T) {
	// Opted in but NO GatewayURL → SandboxWorkloadActive is false → no-op.
	pod := basePod()
	ApplyOpenShellSandbox(pod, policyCorpus(&accv1alpha1.SandboxSpec{Enabled: ptr.To(true)}, nil),
		"n", "cm", testAgentImage)

	if len(pod.Spec.InitContainers) != 0 {
		t.Errorf("inert path added initContainers: %v", pod.Spec.InitContainers)
	}
	if len(pod.Spec.Volumes) != 0 {
		t.Errorf("inert path added volumes: %v", pod.Spec.Volumes)
	}
	agent := findContainer(pod.Spec.Containers, "agent")
	if len(agent.Env) != 0 || len(agent.EnvFrom) != 0 || len(agent.VolumeMounts) != 0 {
		t.Errorf("inert path mutated the agent container: %+v", agent)
	}
}

func TestApplyOpenShellSandbox_ImageOverride(t *testing.T) {
	corpus := activeSandboxCorpus()
	corpus.Spec.Sandbox.Image = "quay.io/acc/sandbox:slim"
	pod := basePod()
	ApplyOpenShellSandbox(pod, corpus, "n", "cm", testAgentImage)

	init := findContainer(pod.Spec.InitContainers, sandboxCreateInit)
	if init == nil {
		t.Fatal("no initContainer")
	}
	if v, _ := envValue(init.Env, "OPENSHELL_SANDBOX_IMAGE"); v != "quay.io/acc/sandbox:slim" {
		t.Errorf("OPENSHELL_SANDBOX_IMAGE = %q, want the override", v)
	}
}

func TestApplyOpenShellSandbox_NoCredsSecret(t *testing.T) {
	corpus := activeSandboxCorpus()
	corpus.Spec.Sandbox.CredentialsSecret = ""
	pod := basePod()
	ApplyOpenShellSandbox(pod, corpus, "n", "cm", testAgentImage)

	agent := findContainer(pod.Spec.Containers, "agent")
	if _, ok := envValue(agent.Env, "OPENSHELL_OIDC_CLIENT_SECRET"); ok {
		t.Errorf("no creds Secret configured but agent has OIDC env: %v", agent.Env)
	}
	init := findContainer(pod.Spec.InitContainers, sandboxCreateInit)
	if _, ok := envValue(init.Env, "OPENSHELL_OIDC_ISSUER"); ok {
		t.Errorf("no creds Secret configured but init has OIDC env: %v", init.Env)
	}
	// The creds-source Secret volume must NOT be added when unconfigured.
	for _, v := range pod.Spec.Volumes {
		if v.Name == sandboxCredsSrcVolume {
			t.Errorf("no creds Secret configured but creds-src volume present")
		}
	}
}

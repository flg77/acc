// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for the spiffe-helper sidecar wiring — proposal 011 PR-3.
package unit_test

import (
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
)

// minimalAgentDeployment returns a Deployment shaped like what
// reconcileRoleDeployment builds: one "agent" container, the standard
// acc-config volume.
func minimalAgentDeployment() *appsv1.Deployment {
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: "research-ingester", Namespace: "test-ns"},
		Spec: appsv1.DeploymentSpec{
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{"app": "acc"}},
				Spec: corev1.PodSpec{
					Containers: []corev1.Container{
						{
							Name: "agent",
							Env: []corev1.EnvVar{
								{Name: "ACC_AGENT_ROLE", Value: "ingester"},
							},
							VolumeMounts: []corev1.VolumeMount{
								{Name: "acc-config", MountPath: "/etc/acc"},
							},
						},
					},
					Volumes: []corev1.Volume{
						{Name: "acc-config"},
					},
				},
			},
		},
	}
}

func sidecarCollective(spiffe *accv1alpha1.SpiffeSpec) *accv1alpha1.AgentCollective {
	return &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "research", Namespace: "test-ns"},
		Spec: accv1alpha1.AgentCollectiveSpec{
			CollectiveID: "research-01",
			Spiffe:       spiffe,
		},
	}
}

// containerNames extracts the container names from a Deployment.
func containerNames(d *appsv1.Deployment) []string {
	var names []string
	for _, c := range d.Spec.Template.Spec.Containers {
		names = append(names, c.Name)
	}
	return names
}

func volumeNames(d *appsv1.Deployment) []string {
	var names []string
	for _, v := range d.Spec.Template.Spec.Volumes {
		names = append(names, v.Name)
	}
	return names
}

// contains() is shared with role_catalogue_test.go in this package
// (same []string, string signature) — reused, not redefined.

// -----------------------------------------------------------------------
// SpiffeEnabled
// -----------------------------------------------------------------------

func TestSpiffeEnabled(t *testing.T) {
	if collective.SpiffeEnabled(sidecarCollective(nil)) {
		t.Error("nil SpiffeSpec should not be enabled")
	}
	if collective.SpiffeEnabled(sidecarCollective(&accv1alpha1.SpiffeSpec{Enabled: false})) {
		t.Error("Enabled=false should not be enabled")
	}
	if !collective.SpiffeEnabled(sidecarCollective(&accv1alpha1.SpiffeSpec{Enabled: true})) {
		t.Error("Enabled=true should be enabled")
	}
}

// -----------------------------------------------------------------------
// RenderSpiffeHelperConfig
// -----------------------------------------------------------------------

func TestRenderSpiffeHelperConfig(t *testing.T) {
	cfg := collective.RenderSpiffeHelperConfig()
	for _, want := range []string{
		`agent_address = "/spiffe-workload-api/spire-agent.sock"`,
		`cert_dir = "/run/spire/sockets"`,
		`jwt_audience = "acc-role-update"`,
		`svid_file_name = "svid.pem"`,
		`jwt_svid_file_name = "jwt_svid.token"`,
		// proposal 011 PR-4 — the JWT trust bundle the agent verifies against.
		`jwt_bundle_file_name = "jwt_bundle.json"`,
	} {
		if !strings.Contains(cfg, want) {
			t.Errorf("helper.conf missing %q\n--- got ---\n%s", want, cfg)
		}
	}
}

// -----------------------------------------------------------------------
// applySpiffeSidecar — disabled is a strict no-op
// -----------------------------------------------------------------------

func TestApplySpiffeSidecar_DisabledIsNoop(t *testing.T) {
	for _, spiffe := range []*accv1alpha1.SpiffeSpec{
		nil,
		{Enabled: false},
	} {
		d := minimalAgentDeployment()
		before := len(d.Spec.Template.Spec.Containers)
		beforeVols := len(d.Spec.Template.Spec.Volumes)

		col := sidecarCollective(spiffe)
		collective.ApplySpiffeSidecar(d, col, collective.SpiffeHelperConfigMapName(col))

		if len(d.Spec.Template.Spec.Containers) != before {
			t.Errorf("disabled spiffe added containers: %v", containerNames(d))
		}
		if len(d.Spec.Template.Spec.Volumes) != beforeVols {
			t.Errorf("disabled spiffe added volumes: %v", volumeNames(d))
		}
		if d.Spec.Template.ObjectMeta.Annotations[
			"spiffe.io/spire-managed-identity"] != "" {
			t.Error("disabled spiffe set the SPIRE annotation")
		}
	}
}

// -----------------------------------------------------------------------
// applySpiffeSidecar — enabled injects the sidecar
// -----------------------------------------------------------------------

func TestApplySpiffeSidecar_EnabledInjectsSidecar(t *testing.T) {
	d := minimalAgentDeployment()
	col := sidecarCollective(&accv1alpha1.SpiffeSpec{
		Enabled:     true,
		TrustDomain: "acc-prod.example.com",
	})
	collective.ApplySpiffeSidecar(d, col, collective.SpiffeHelperConfigMapName(col))

	// The spiffe-helper sidecar is appended.
	if !contains(containerNames(d), "spiffe-helper") {
		t.Fatalf("spiffe-helper container not added: %v", containerNames(d))
	}
	// The agent container stays at index 0.
	if d.Spec.Template.Spec.Containers[0].Name != "agent" {
		t.Errorf("agent should remain container[0], got %q",
			d.Spec.Template.Spec.Containers[0].Name)
	}

	// Three SPIFFE volumes added.
	for _, v := range []string{"spiffe-svids", "spiffe-workload-api", "spiffe-helper-config"} {
		if !contains(volumeNames(d), v) {
			t.Errorf("volume %q not added: %v", v, volumeNames(d))
		}
	}

	// Pod annotation set for spire-controller-manager's webhook.
	if d.Spec.Template.ObjectMeta.Annotations["spiffe.io/spire-managed-identity"] != "true" {
		t.Error("SPIRE managed-identity annotation not set")
	}

	// Agent container gets the SVID mount + env vars.
	agent := d.Spec.Template.Spec.Containers[0]
	foundMount := false
	for _, m := range agent.VolumeMounts {
		if m.Name == "spiffe-svids" && m.MountPath == "/run/spire/sockets" {
			foundMount = true
			if !m.ReadOnly {
				t.Error("agent SVID mount should be read-only")
			}
		}
	}
	if !foundMount {
		t.Error("agent container missing spiffe-svids mount")
	}

	envWant := map[string]string{
		"ACC_SPIFFE_SVID_MOUNT_PATH": "/run/spire/sockets",
		"ACC_SVID_X509_PATH":         "/run/spire/sockets/svid.pem",
		"ACC_SVID_JWT_PATH":          "/run/spire/sockets/jwt_svid.token",
	}
	envGot := map[string]string{}
	for _, e := range agent.Env {
		envGot[e.Name] = e.Value
	}
	for k, want := range envWant {
		if envGot[k] != want {
			t.Errorf("agent env %s: got %q want %q", k, envGot[k], want)
		}
	}
	// The pre-existing ACC_AGENT_ROLE env survives.
	if envGot["ACC_AGENT_ROLE"] != "ingester" {
		t.Error("applySpiffeSidecar clobbered the existing agent env")
	}
}

func TestApplySpiffeSidecar_HelperContainerShape(t *testing.T) {
	d := minimalAgentDeployment()
	col := sidecarCollective(&accv1alpha1.SpiffeSpec{Enabled: true})
	collective.ApplySpiffeSidecar(d, col, collective.SpiffeHelperConfigMapName(col))

	var helper *corev1.Container
	for i := range d.Spec.Template.Spec.Containers {
		if d.Spec.Template.Spec.Containers[i].Name == "spiffe-helper" {
			helper = &d.Spec.Template.Spec.Containers[i]
		}
	}
	if helper == nil {
		t.Fatal("spiffe-helper container not found")
	}
	if !strings.HasPrefix(helper.Image, "ghcr.io/spiffe/spiffe-helper:") {
		t.Errorf("unexpected spiffe-helper image %q", helper.Image)
	}
	// It mounts all three volumes.
	mounts := map[string]bool{}
	for _, m := range helper.VolumeMounts {
		mounts[m.Name] = true
	}
	for _, v := range []string{"spiffe-svids", "spiffe-workload-api", "spiffe-helper-config"} {
		if !mounts[v] {
			t.Errorf("spiffe-helper missing mount %q", v)
		}
	}
}

func TestSpiffeHelperConfigMapName(t *testing.T) {
	col := sidecarCollective(&accv1alpha1.SpiffeSpec{Enabled: true})
	got := collective.SpiffeHelperConfigMapName(col)
	if got != "research-spiffe-helper" {
		t.Errorf("config map name: got %q want research-spiffe-helper", got)
	}
}

// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// The TUI and WebGUI must see what the agents see.
//
// On bb3 (operator 0.2.14) the TUI web terminal and the WebGUI showed only the
// roles baked into their images, no installed packs, no skills or MCPs and
// only the built-in catalog: their Deployments set four env vars and mounted
// nothing, while agent pods got the corpus's manifest ConfigMaps, the
// acc-catalogs system catalog and a writable packages root.
package unit_test

import (
	"context"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/ui"
)

// manifestCMs are the three corpus ConfigMaps ManifestDeliveryReconciler emits
// for the webguiCorpus fixture ("rhoai-corpus" in acc-system).
func manifestCMs() []client.Object {
	cm := func(suffix, key string) *corev1.ConfigMap {
		return &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{Name: "rhoai-corpus-" + suffix, Namespace: "acc-system"},
			Data:       map[string]string{key: "x"},
		}
	}
	return []client.Object{
		cm("acc-roles", "assistant__role.yaml"),
		cm("acc-skills", "echo__skill.yaml"),
		cm("acc-mcps", "echo__mcp.yaml"),
	}
}

func uiDeployment(t *testing.T, c client.Client, name string) *appsv1.Deployment {
	t.Helper()
	d := &appsv1.Deployment{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: name}, d); err != nil {
		t.Fatalf("expected Deployment %s: %v", name, err)
	}
	return d
}

func containerNamed(t *testing.T, d *appsv1.Deployment, name string) corev1.Container {
	t.Helper()
	for _, c := range d.Spec.Template.Spec.Containers {
		if c.Name == name {
			return c
		}
	}
	t.Fatalf("%s has no %q container: %v", d.Name, name, d.Spec.Template.Spec.Containers)
	return corev1.Container{}
}

// assertCorpusDelivery checks one container carries the agent-equivalent
// mounts + env, and that the pod declares the backing volumes.
func assertCorpusDelivery(t *testing.T, d *appsv1.Deployment, ctr corev1.Container, wantManifests bool) {
	t.Helper()
	mounts := map[string]corev1.VolumeMount{}
	for _, m := range ctr.VolumeMounts {
		mounts[m.MountPath] = m
	}
	env := map[string]string{}
	for _, e := range ctr.Env {
		env[e.Name] = e.Value
	}
	vols := map[string]corev1.Volume{}
	for _, v := range d.Spec.Template.Spec.Volumes {
		vols[v.Name] = v
	}

	if m, ok := mounts["/etc/acc"]; !ok || m.Name != "acc-catalogs" {
		t.Errorf("%s/%s: want acc-catalogs at /etc/acc (catalogs.yaml), got %v", d.Name, ctr.Name, ctr.VolumeMounts)
	}
	if v := vols["acc-catalogs"]; v.ConfigMap == nil || v.ConfigMap.Name != "acc-catalogs" ||
		v.ConfigMap.Optional == nil || !*v.ConfigMap.Optional {
		t.Errorf("%s: acc-catalogs volume must be the optional acc-catalogs ConfigMap, got %+v", d.Name, v)
	}
	if m, ok := mounts["/var/lib/acc"]; !ok || m.Name != "acc-packages" || m.ReadOnly {
		t.Errorf("%s/%s: want a writable acc-packages mount at /var/lib/acc, got %v", d.Name, ctr.Name, ctr.VolumeMounts)
	}
	if v := vols["acc-packages"]; v.EmptyDir == nil {
		t.Errorf("%s: acc-packages must be an emptyDir, got %+v", d.Name, v)
	}

	for _, tree := range []struct{ path, env, vol, cm string }{
		{"/etc/acc/roles", "ACC_ROLES_ROOT", "acc-roles", "rhoai-corpus-acc-roles"},
		{"/etc/acc/skills", "ACC_SKILLS_ROOT", "acc-skills", "rhoai-corpus-acc-skills"},
		{"/etc/acc/mcps", "ACC_MCPS_ROOT", "acc-mcps", "rhoai-corpus-acc-mcps"},
	} {
		m, mounted := mounts[tree.path]
		if !wantManifests {
			if mounted || env[tree.env] != "" {
				t.Errorf("%s/%s: manifestDelivery=none must not mount %s or set %s", d.Name, ctr.Name, tree.path, tree.env)
			}
			continue
		}
		if !mounted || m.Name != tree.vol || !m.ReadOnly {
			t.Errorf("%s/%s: want read-only %s at %s, got %v", d.Name, ctr.Name, tree.vol, tree.path, ctr.VolumeMounts)
		}
		if env[tree.env] != tree.path {
			t.Errorf("%s/%s: %s = %q, want %s", d.Name, ctr.Name, tree.env, env[tree.env], tree.path)
		}
		if v := vols[tree.vol]; v.ConfigMap == nil || v.ConfigMap.Name != tree.cm || len(v.ConfigMap.Items) == 0 {
			t.Errorf("%s: %s volume must project ConfigMap %s, got %+v", d.Name, tree.vol, tree.cm, v)
		}
	}
}

func assertInstallerSidecar(t *testing.T, d *appsv1.Deployment, wantManifests bool) {
	t.Helper()
	side := containerNamed(t, d, ui.PkgInstallerContainerName)
	if side.Image != "quay.io/flg77/acc_images:acc-agent-core-0.2.0" {
		t.Errorf("%s: pkg-installer must run the agent-core image (cosign + venv), got %q", d.Name, side.Image)
	}
	if len(side.Command) == 0 || side.Command[0] != "sleep" {
		t.Errorf("%s: pkg-installer should idle for exec, got %v", d.Name, side.Command)
	}
	if sc := side.SecurityContext; sc == nil || sc.AllowPrivilegeEscalation == nil || *sc.AllowPrivilegeEscalation {
		t.Errorf("%s: pkg-installer must use the restricted agent container security context, got %+v", d.Name, sc)
	}
	// It writes where the UI container reads.
	assertCorpusDelivery(t, d, side, wantManifests)
	if first := d.Spec.Template.Spec.Containers[0].Name; first == ui.PkgInstallerContainerName {
		t.Errorf("%s: sidecar must not be the first container (`oc rsh` attaches to it)", d.Name)
	}
}

// assertRegulatoryLayer checks the UI container reads the Rego rule inventory
// at /etc/acc/regulatory_layer from an emptyDir an init container on the
// agent-core image filled (0.2.19: the UI images do not ship
// regulatory_layer/, the Compliance screen reads it from that path when
// present). ACC_REGULATORY_ROOT must stay unset.
func assertRegulatoryLayer(t *testing.T, d *appsv1.Deployment, ctr corev1.Container) {
	t.Helper()
	var mount *corev1.VolumeMount
	for i := range ctr.VolumeMounts {
		if ctr.VolumeMounts[i].MountPath == ui.RegulatoryMountPath {
			mount = &ctr.VolumeMounts[i]
		}
	}
	if mount == nil || mount.Name != "acc-regulatory" || !mount.ReadOnly {
		t.Errorf("%s/%s: want read-only acc-regulatory at %s, got %v", d.Name, ctr.Name, ui.RegulatoryMountPath, ctr.VolumeMounts)
	}
	for _, e := range ctr.Env {
		if e.Name == "ACC_REGULATORY_ROOT" {
			t.Errorf("%s/%s: ACC_REGULATORY_ROOT must not be set (the runtime tries the path by default)", d.Name, ctr.Name)
		}
	}
	var vol *corev1.Volume
	for i := range d.Spec.Template.Spec.Volumes {
		if d.Spec.Template.Spec.Volumes[i].Name == "acc-regulatory" {
			vol = &d.Spec.Template.Spec.Volumes[i]
		}
	}
	if vol == nil || vol.EmptyDir == nil {
		t.Errorf("%s: acc-regulatory must be an emptyDir, got %+v", d.Name, vol)
	}

	var init *corev1.Container
	for i := range d.Spec.Template.Spec.InitContainers {
		if d.Spec.Template.Spec.InitContainers[i].Name == ui.RegulatoryInitContainerName {
			init = &d.Spec.Template.Spec.InitContainers[i]
		}
	}
	if init == nil {
		t.Fatalf("%s: no %s init container, got %v", d.Name, ui.RegulatoryInitContainerName, d.Spec.Template.Spec.InitContainers)
	}
	if init.Image != "quay.io/flg77/acc_images:acc-agent-core-0.2.0" {
		t.Errorf("%s: the init container must run the agent-core image (ships regulatory_layer/), got %q", d.Name, init.Image)
	}
	if cmd := strings.Join(init.Command, " "); !strings.Contains(cmd, "cp -R /app/regulatory_layer/. "+ui.RegulatoryMountPath+"/") {
		t.Errorf("%s: init container must copy /app/regulatory_layer into the mount, got %q", d.Name, cmd)
	}
	if len(init.VolumeMounts) != 1 || init.VolumeMounts[0].Name != "acc-regulatory" ||
		init.VolumeMounts[0].MountPath != ui.RegulatoryMountPath || init.VolumeMounts[0].ReadOnly {
		t.Errorf("%s: init container must mount acc-regulatory writable at %s, got %v", d.Name, ui.RegulatoryMountPath, init.VolumeMounts)
	}
	if sc := init.SecurityContext; sc == nil || sc.AllowPrivilegeEscalation == nil || *sc.AllowPrivilegeEscalation {
		t.Errorf("%s: init container must use the restricted agent container security context, got %+v", d.Name, sc)
	}
}

func TestTUIIdlePodGetsTheCorpusDelivery(t *testing.T) {
	c, _ := webguiClient(t, manifestCMs()...)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(true)})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	d := uiDeployment(t, c, "rhoai-corpus-tui")
	tui := containerNamed(t, d, "tui")
	assertCorpusDelivery(t, d, tui, true)
	assertInstallerSidecar(t, d, true)
	assertRegulatoryLayer(t, d, tui)
	if tui.SecurityContext != nil {
		t.Errorf("tui container security context must stay as it was (unset), got %+v", tui.SecurityContext)
	}
}

func TestTUIWebTerminalGetsTheCorpusDelivery(t *testing.T) {
	c, _ := webguiClient(t, manifestCMs()...)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(true), WebTerminal: ptr.To(true)})
	corpus.Spec.WebGUI = &accv1alpha1.WebGUISpec{Keycloak: fullKeycloak()}
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	d := uiDeployment(t, c, "rhoai-corpus-tui")
	tui := containerNamed(t, d, "tui")
	if len(tui.Command) == 0 || tui.Command[0] != "ttyd" {
		t.Fatalf("expected the ttyd web terminal, got %v", tui.Command)
	}
	assertCorpusDelivery(t, d, tui, true)
	assertInstallerSidecar(t, d, true)
	assertRegulatoryLayer(t, d, tui)
	// The auth proxy gets none of it.
	if proxy := containerNamed(t, d, "oauth2-proxy"); len(proxy.VolumeMounts) != 0 {
		t.Errorf("oauth2-proxy must not mount corpus data, got %v", proxy.VolumeMounts)
	}
}

func TestWebGUIGetsTheCorpusDelivery(t *testing.T) {
	c, _ := webguiClient(t, manifestCMs()...)
	r := &ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := webguiCorpus(&accv1alpha1.WebGUISpec{
		Enabled: ptr.To(true), Route: ptr.To(false), Keycloak: fullKeycloak(),
	})
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	d := uiDeployment(t, c, "rhoai-corpus-webgui")
	assertCorpusDelivery(t, d, containerNamed(t, d, "webgui"), true)
	assertInstallerSidecar(t, d, true)
	assertRegulatoryLayer(t, d, containerNamed(t, d, "webgui"))
}

// manifestDelivery=none: the image's baked roles stay in use (no ACC_*_ROOT
// override), exactly as agents get nothing; catalogs + packages still arrive,
// and so does the regulatory layer (image-sourced, not a manifest ConfigMap).
func TestUIManifestDeliveryNoneKeepsImageRoles(t *testing.T) {
	c, _ := webguiClient(t, manifestCMs()...)
	r := &ui.TUIReconciler{Client: c, Scheme: newScheme(t)}
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(true)})
	corpus.Spec.ManifestDelivery = "none"
	if _, err := r.Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	d := uiDeployment(t, c, "rhoai-corpus-tui")
	assertCorpusDelivery(t, d, containerNamed(t, d, "tui"), false)
	assertInstallerSidecar(t, d, false)
	assertRegulatoryLayer(t, d, containerNamed(t, d, "tui"))
}

// A corpus with the UIs disabled gets no UI Deployment, hence no installer
// sidecar and no UI install targets.
func TestUIDisabledCreatesNoDeliveryOrSidecar(t *testing.T) {
	c, _ := webguiClient(t, manifestCMs()...)
	corpus := tuiCorpus(&accv1alpha1.TUISpec{Enabled: ptr.To(false)})
	corpus.Spec.WebGUI = &accv1alpha1.WebGUISpec{Enabled: ptr.To(false), Keycloak: fullKeycloak()}
	if _, err := (&ui.TUIReconciler{Client: c, Scheme: newScheme(t)}).Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("TUI Reconcile: %v", err)
	}
	if _, err := (&ui.WebGUIReconciler{Client: c, Scheme: newScheme(t)}).Reconcile(context.Background(), corpus); err != nil {
		t.Fatalf("WebGUI Reconcile: %v", err)
	}
	var list appsv1.DeploymentList
	if err := c.List(context.Background(), &list, client.InNamespace("acc-system")); err != nil {
		t.Fatalf("list: %v", err)
	}
	if len(list.Items) != 0 {
		t.Errorf("disabled UIs must create no Deployments, got %d", len(list.Items))
	}
}

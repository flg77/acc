// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package collective

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// F3 Phase 2 (20260926-secrets-from-kubernetes-and-a-live-broker): a corpus
// that declares spec.secretMount gets the Secret mounted read-only at
// /var/run/acc/secrets on every agent, and the runtime told to read it there.

func agentStatefulSet(t *testing.T, corpus *accv1alpha1.AgentCorpus) *appsv1.StatefulSet {
	t.Helper()
	s := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		corev1.AddToScheme, appsv1.AddToScheme, accv1alpha1.AddToScheme,
	} {
		if err := add(s); err != nil {
			t.Fatalf("AddToScheme: %v", err)
		}
	}
	cl := fake.NewClientBuilder().WithScheme(s).Build()
	r := &AgentDeploymentReconciler{Client: cl, Scheme: s}
	coll := &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "acc-proj"},
		Spec:       accv1alpha1.AgentCollectiveSpec{CollectiveID: "demo"},
	}
	roleSpec := accv1alpha1.AgentRoleSpec{Role: "analyst", Replicas: 1}
	if _, _, _, err := r.reconcileRoleDeployment(
		context.Background(), corpus, coll, roleSpec, "demo-config", "demo-analyst-role", "acc-proj", ""); err != nil {
		t.Fatalf("reconcileRoleDeployment: %v", err)
	}
	sts := &appsv1.StatefulSet{}
	if err := cl.Get(context.Background(), types.NamespacedName{
		Namespace: "acc-proj", Name: util.AgentDeploymentName(coll.Name, "analyst"),
	}, sts); err != nil {
		t.Fatalf("agent StatefulSet not created: %v", err)
	}
	return sts
}

func envValue(c corev1.Container, name string) (string, bool) {
	for _, e := range c.Env {
		if e.Name == name {
			return e.Value, true
		}
	}
	return "", false
}

func TestSecretMount_MountsTheSecretAndSwitchesTheSource(t *testing.T) {
	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "acc-proj"},
		Spec: accv1alpha1.AgentCorpusSpec{
			Version:     "0.1.0",
			SecretMount: &accv1alpha1.SecretMountSpec{SecretName: "acc-credentials"},
		},
	}
	pod := agentStatefulSet(t, corpus).Spec.Template.Spec
	agent := pod.Containers[0]

	var mount *corev1.VolumeMount
	for i := range agent.VolumeMounts {
		if agent.VolumeMounts[i].MountPath == SecretMountDir {
			mount = &agent.VolumeMounts[i]
		}
	}
	if mount == nil {
		t.Fatalf("no mount at %s; mounts=%+v", SecretMountDir, agent.VolumeMounts)
	}
	if !mount.ReadOnly {
		t.Error("the credentials mount must be read-only")
	}
	if mount.SubPath != "" {
		t.Error("a subPath mount is never refreshed by the kubelet -- rotation would silently stop")
	}

	var vol *corev1.Volume
	for i := range pod.Volumes {
		if pod.Volumes[i].Name == mount.Name {
			vol = &pod.Volumes[i]
		}
	}
	if vol == nil || vol.Secret == nil || vol.Secret.SecretName != "acc-credentials" {
		t.Fatalf("mount %q is not backed by Secret acc-credentials: %+v", mount.Name, vol)
	}
	if vol.Secret.Optional == nil || *vol.Secret.Optional {
		t.Error("a declared Secret must be required: an optional one that is missing leaves the agent on the environment, silently")
	}
	if vol.Secret.DefaultMode == nil || *vol.Secret.DefaultMode != 0o440 {
		t.Errorf("DefaultMode = %v, want 0440", vol.Secret.DefaultMode)
	}
	if len(vol.Secret.Items) != 0 {
		t.Errorf("no items declared, so every key is mounted; got %+v", vol.Secret.Items)
	}

	if v, _ := envValue(agent, "ACC_SECRET_SOURCE"); v != "mounted" {
		t.Errorf("ACC_SECRET_SOURCE = %q, want mounted", v)
	}
	if v, _ := envValue(agent, "ACC_SECRET_DIR"); v != SecretMountDir {
		t.Errorf("ACC_SECRET_DIR = %q, want %s", v, SecretMountDir)
	}
}

func TestSecretMount_ItemsNarrowTheMount(t *testing.T) {
	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "acc-proj"},
		Spec: accv1alpha1.AgentCorpusSpec{
			Version: "0.1.0",
			SecretMount: &accv1alpha1.SecretMountSpec{
				SecretName: "acc-credentials",
				Items:      []string{"MAAS_API_KEY", "ACC_CRED_KEY"},
			},
		},
	}
	_, vols, _ := SecretMountDelivery(corpus)
	items := vols[0].Secret.Items
	if len(items) != 2 || items[0].Key != "MAAS_API_KEY" || items[0].Path != "MAAS_API_KEY" ||
		items[1].Key != "ACC_CRED_KEY" || items[1].Path != "ACC_CRED_KEY" {
		t.Errorf("each item must be a file named for its key; got %+v", items)
	}
	_ = agentStatefulSet(t, corpus) // and the pod still builds
}

func TestSecretMount_NoneDeclaredIsUnchanged(t *testing.T) {
	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "acc-proj"},
		Spec:       accv1alpha1.AgentCorpusSpec{Version: "0.1.0"},
	}
	agent := agentStatefulSet(t, corpus).Spec.Template.Spec.Containers[0]
	for _, m := range agent.VolumeMounts {
		if m.MountPath == SecretMountDir {
			t.Errorf("no secretMount declared, but %s is mounted", SecretMountDir)
		}
	}
	if _, set := envValue(agent, "ACC_SECRET_SOURCE"); set {
		t.Error("no secretMount declared, but ACC_SECRET_SOURCE is set -- the env source must stay the default")
	}
	m, v, e := SecretMountDelivery(corpus)
	if m != nil || v != nil || e != nil {
		t.Error("SecretMountDelivery must return nothing for a corpus without secretMount")
	}
}

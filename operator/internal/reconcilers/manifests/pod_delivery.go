// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package manifests

import (
	"context"
	"sort"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// PodDelivery returns the VolumeMount/Volume/EnvVar slices that inject the
// corpus-scoped acc-roles, acc-skills, and acc-mcps ConfigMaps into a pod at
// /etc/acc/{roles,skills,mcps} (with the matching ACC_*_ROOT env vars).
//
// It is the one helper every pod that needs the corpus's manifest trees goes
// through: agent pods (collective/agent_deployment) and the TUI + WebGUI pods
// (ui/corpus_delivery), so a UI shows exactly the roles, skills and MCPs the
// agents run with.
//
// Each Volume uses an explicit items[] projection so the flattened
// ConfigMap keys (path__separated__like__this) re-project to slash-paths
// in the pod's filesystem. The keys are read from the live ConfigMap so
// the projection always matches the data — no separate source of truth.
//
// When spec.manifestDelivery == "none" or any expected ConfigMap is not
// yet present, returns empty slices and a nil error. The reconciler will
// retry on the next cycle once ManifestDeliveryReconciler has emitted
// the CMs.
func PodDelivery(
	ctx context.Context,
	c client.Reader,
	corpus *accv1alpha1.AgentCorpus,
) ([]corev1.VolumeMount, []corev1.Volume, []corev1.EnvVar, error) {
	if corpus.Spec.ManifestDelivery == "none" {
		return nil, nil, nil, nil
	}

	plans := []struct {
		volumeName string
		cmSuffix   string
		mountPath  string
		envVarName string
	}{
		{"acc-roles", rolesCMSuffix, RolesMountPath, "ACC_ROLES_ROOT"},
		{"acc-skills", skillsCMSuffix, SkillsMountPath, "ACC_SKILLS_ROOT"},
		{"acc-mcps", mcpsCMSuffix, MCPsMountPath, "ACC_MCPS_ROOT"},
	}

	var (
		mounts  []corev1.VolumeMount
		volumes []corev1.Volume
		envs    []corev1.EnvVar
	)
	for _, p := range plans {
		cmName := ConfigMapName(corpus, p.cmSuffix)
		cm := &corev1.ConfigMap{}
		if err := c.Get(ctx, types.NamespacedName{Namespace: corpus.Namespace, Name: cmName}, cm); err != nil {
			// CM not yet present — skip this tree; next reconcile picks it up.
			// Do not error: the manifest reconciler runs in a separate slot of
			// the parent chain and may not have completed on first apply.
			continue
		}
		items := ProjectManifestItems(cm.Data)
		mounts = append(mounts, corev1.VolumeMount{
			Name:      p.volumeName,
			MountPath: p.mountPath,
			ReadOnly:  true,
		})
		volumes = append(volumes, corev1.Volume{
			Name: p.volumeName,
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: cmName},
					Items:                items,
				},
			},
		})
		envs = append(envs, corev1.EnvVar{Name: p.envVarName, Value: p.mountPath})
	}
	return mounts, volumes, envs, nil
}

// ProjectManifestItems renders a manifest ConfigMap's data into the volume
// projection items, sorted by Key. Iterating the map directly yields Go's
// randomized order, which makes the rendered pod template differ on every
// reconcile → the Deployment is patched each pass → perpetual ReplicaSet
// churn. Exported so the determinism regression test can pin the contract.
func ProjectManifestItems(data map[string]string) []corev1.KeyToPath {
	items := make([]corev1.KeyToPath, 0, len(data))
	for key := range data {
		items = append(items, corev1.KeyToPath{
			Key:  key,
			Path: UnflattenKey(key),
		})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Key < items[j].Key })
	return items
}

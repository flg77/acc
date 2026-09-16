// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package ui

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/manifests"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const (
	// PkgInstallerContainerName is the sidecar in every TUI and WebGUI pod
	// that AccPackageInstall execs `acc.cli collective pkg-install-direct`
	// in. The UI images cannot run a signed install themselves: they carry
	// no cosign binary, and acc/pkg/verify.py shells out to it to check a
	// pack's signature. The sidecar runs the agent-core image — the same
	// interpreter, source tree and cosign the agent-pod install uses — and
	// writes into the pod's shared acc-packages emptyDir, which the UI
	// container reads.
	PkgInstallerContainerName = "pkg-installer"

	// catalogsConfigMapName is the per-namespace ConfigMap AccCatalogReconciler
	// renders (controller.CatalogsConfigMapName; not imported to keep the
	// reconcilers free of a controller dependency).
	catalogsConfigMapName = "acc-catalogs"
	// catalogsMountPath holds catalogs.yaml, the system layer of the layered
	// catalog resolver (acc/pkg/catalog.py SYSTEM_CATALOG_PATH), which is
	// where agent pods find it too.
	catalogsMountPath = "/etc/acc"
	// packagesMountPath is the writable packages root; acc.pkg.registry
	// defaults to /var/lib/acc/packages underneath it.
	packagesMountPath = "/var/lib/acc"
)

// withCorpusDelivery gives a UI pod what an agent pod gets, so the TUI and
// WebGUI show the corpus's roles, skills, MCPs, catalogs and installed packs
// instead of only what is baked into their images:
//
//   - the roles/skills/mcps manifest ConfigMaps at /etc/acc/{roles,skills,mcps}
//     with ACC_ROLES_ROOT / ACC_SKILLS_ROOT / ACC_MCPS_ROOT
//     (manifests.PodDelivery — the same helper agent pods use; nothing when
//     spec.manifestDelivery is "none", so the image's baked roles stay in use);
//   - the namespace's acc-catalogs ConfigMap at /etc/acc, i.e.
//     /etc/acc/catalogs.yaml (optional: an empty dir until an AccCatalog
//     exists, and kubelet fills it in when one appears);
//   - an acc-packages emptyDir at /var/lib/acc;
//   - the pkg-installer sidecar that AccPackageInstall execs into.
//
// It mutates the container named uiContainer in place and appends the
// sidecar and volumes to the pod spec. The UI container's own security
// context is left untouched.
func withCorpusDelivery(
	ctx context.Context,
	c client.Reader,
	corpus *accv1alpha1.AgentCorpus,
	pod *corev1.PodSpec,
	uiContainer string,
) error {
	manifestMounts, manifestVolumes, manifestEnv, err := manifests.PodDelivery(ctx, c, corpus)
	if err != nil {
		return fmt.Errorf("manifest delivery: %w", err)
	}

	mounts := append([]corev1.VolumeMount{
		{Name: "acc-catalogs", MountPath: catalogsMountPath},
		{Name: "acc-packages", MountPath: packagesMountPath},
	}, manifestMounts...)

	found := false
	for i := range pod.Containers {
		if pod.Containers[i].Name != uiContainer {
			continue
		}
		found = true
		pod.Containers[i].Env = append(pod.Containers[i].Env, manifestEnv...)
		pod.Containers[i].VolumeMounts = append(pod.Containers[i].VolumeMounts, mounts...)
	}
	if !found {
		return fmt.Errorf("pod spec has no %q container", uiContainer)
	}

	pod.Containers = append(pod.Containers, corev1.Container{
		Name:  PkgInstallerContainerName,
		Image: util.ComponentImage(corpus, "acc-agent-core", corpus.Spec.Version),
		// Idle: AccPackageInstall execs the installer on demand.
		Command:         []string{"sleep", "infinity"},
		Env:             append([]corev1.EnvVar(nil), manifestEnv...),
		VolumeMounts:    append([]corev1.VolumeMount(nil), mounts...),
		SecurityContext: collective.AgentContainerSecurityContext(),
	})

	pod.Volumes = append(append(pod.Volumes,
		corev1.Volume{
			Name: "acc-catalogs",
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: catalogsConfigMapName},
					Optional:             ptr.To(true),
				},
			},
		},
		corev1.Volume{
			Name:         "acc-packages",
			VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
		},
	), manifestVolumes...)
	return nil
}

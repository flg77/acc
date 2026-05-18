// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package collective

import (
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/utils/ptr"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// NATS NKey seed projection (proposal 013 PR-4).
//
// When the AgentCorpus has spec.infrastructure.nats.nkeyAuth.enabled,
// every agent pod gets its role's NKey *seed* projected read-only from
// the operator-generated Secret `{corpus}-nats-nkeys`.  The agent
// runtime reads the seed file (ACC_NKEY_SEED_PATH) and authenticates
// the NATS connection with it — see acc/backends/signaling_nats.py.
//
// Mirrors ApplySpiffeSidecar: a post-build mutator applied after the
// base Deployment is constructed, so the agent container is at index 0.

const (
	nkeySeedVolumeName = "acc-nkey-seed"
	nkeySeedMountPath  = "/run/acc/nkeys"
	nkeySeedFileName   = "seed"
)

// nkeySecretName returns the per-corpus NKey Secret name — must match
// the name minted by infra.NATSReconciler.reconcileNKeySecret.
func nkeySecretName(corpusName string) string {
	return fmt.Sprintf("%s-nats-nkeys", corpusName)
}

// ApplyNKeySeed projects the agent's role NKey seed into the pod and
// sets the ACC_NKEY_* env vars.  No-op when NKey auth is disabled.
func ApplyNKeySeed(
	deploy *appsv1.Deployment,
	corpus *accv1alpha1.AgentCorpus,
	role accv1alpha1.AgentRole,
) {
	nkeyAuth := corpus.Spec.Infrastructure.NATS.NKeyAuth
	if nkeyAuth == nil || !nkeyAuth.Enabled {
		return
	}
	podSpec := &deploy.Spec.Template.Spec
	if len(podSpec.Containers) == 0 {
		return
	}
	agent := &podSpec.Containers[0]

	// Volume — project ONLY this role's seed key from the Secret, so a
	// compromised pod cannot read other roles' seeds.
	podSpec.Volumes = append(podSpec.Volumes, corev1.Volume{
		Name: nkeySeedVolumeName,
		VolumeSource: corev1.VolumeSource{
			Secret: &corev1.SecretVolumeSource{
				SecretName:  nkeySecretName(corpus.Name),
				DefaultMode: ptr.To(int32(0o400)),
				Items: []corev1.KeyToPath{
					{
						Key:  "seed-" + string(role),
						Path: nkeySeedFileName,
					},
				},
			},
		},
	})
	agent.VolumeMounts = append(agent.VolumeMounts, corev1.VolumeMount{
		Name:      nkeySeedVolumeName,
		MountPath: nkeySeedMountPath,
		ReadOnly:  true,
	})

	seedPath := fmt.Sprintf("%s/%s", nkeySeedMountPath, nkeySeedFileName)
	agent.Env = append(agent.Env,
		corev1.EnvVar{Name: "ACC_NKEY_ENABLED", Value: "true"},
		corev1.EnvVar{Name: "ACC_NKEY_ROLE", Value: string(role)},
		corev1.EnvVar{Name: "ACC_NKEY_SEED_PATH", Value: seedPath},
	)
}

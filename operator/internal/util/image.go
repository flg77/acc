/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package util

import (
	"fmt"

	corev1 "k8s.io/api/core/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// ComponentImage builds the fully-qualified image reference for an ACC
// component (e.g. "acc-agent-core", "nats", "redis").
//
// When the corpus sets a single-repository target (spec.imageRepository),
// every component is addressed within that one repository, distinguished by
// tag:  <imageRepository>:<component>-<tag>
// (e.g. quay.io/flg77/acc_images:acc-agent-core-0.1.0).
//
// Otherwise the legacy behaviour applies — each component is its own
// repository under the base registry:  <imageRegistry>/<component>:<tag>.
func ComponentImage(corpus *accv1alpha1.AgentCorpus, component, tag string) string {
	if corpus.Spec.ImageRepository != "" {
		return fmt.Sprintf("%s:%s-%s", corpus.Spec.ImageRepository, component, tag)
	}
	return fmt.Sprintf("%s/%s:%s", corpus.Spec.ImageRegistry, component, tag)
}

// ImagePullSecrets converts the corpus-configured pull-secret names into the
// []corev1.LocalObjectReference a PodSpec expects. Returns nil when none are
// configured so the field is omitted from rendered pods.
func ImagePullSecrets(corpus *accv1alpha1.AgentCorpus) []corev1.LocalObjectReference {
	if len(corpus.Spec.ImagePullSecrets) == 0 {
		return nil
	}
	refs := make([]corev1.LocalObjectReference, 0, len(corpus.Spec.ImagePullSecrets))
	for _, name := range corpus.Spec.ImagePullSecrets {
		refs = append(refs, corev1.LocalObjectReference{Name: name})
	}
	return refs
}

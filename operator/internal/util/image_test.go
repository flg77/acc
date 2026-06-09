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
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

func corpusWith(registry, repository string, pullSecrets []string) *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		Spec: accv1alpha1.AgentCorpusSpec{
			ImageRegistry:    registry,
			ImageRepository:  repository,
			ImagePullSecrets: pullSecrets,
		},
	}
}

// TestComponentImage_Legacy asserts that with ImageRepository empty the output
// is byte-identical to the pre-change <registry>/<component>:<tag> format.
func TestComponentImage_Legacy(t *testing.T) {
	c := corpusWith("registry.access.redhat.com", "", nil)
	cases := []struct {
		component, tag, want string
	}{
		{"acc-agent-core", "0.1.0", "registry.access.redhat.com/acc-agent-core:0.1.0"},
		{"nats", "2.10-alpine", "registry.access.redhat.com/nats:2.10-alpine"},
		{"redis", "6-alpine", "registry.access.redhat.com/redis:6-alpine"},
		{"acc-kafka-bridge", "0.1.0", "registry.access.redhat.com/acc-kafka-bridge:0.1.0"},
		{"acc-runtime-evidence-bridge", "0.1.0", "registry.access.redhat.com/acc-runtime-evidence-bridge:0.1.0"},
	}
	for _, tc := range cases {
		if got := ComponentImage(c, tc.component, tc.tag); got != tc.want {
			t.Errorf("ComponentImage(%q, %q) = %q, want %q", tc.component, tc.tag, got, tc.want)
		}
	}
}

// TestComponentImage_SingleRepo asserts the single-repository tag scheme.
func TestComponentImage_SingleRepo(t *testing.T) {
	c := corpusWith("registry.access.redhat.com", "quay.io/flg77/acc_images", nil)
	cases := []struct {
		component, tag, want string
	}{
		{"acc-agent-core", "0.1.0", "quay.io/flg77/acc_images:acc-agent-core-0.1.0"},
		{"nats", "2.10-alpine", "quay.io/flg77/acc_images:nats-2.10-alpine"},
		{"redis", "6-alpine", "quay.io/flg77/acc_images:redis-6-alpine"},
		{"acc-kafka-bridge", "0.1.0", "quay.io/flg77/acc_images:acc-kafka-bridge-0.1.0"},
		{"acc-runtime-evidence-bridge", "0.1.0", "quay.io/flg77/acc_images:acc-runtime-evidence-bridge-0.1.0"},
	}
	for _, tc := range cases {
		if got := ComponentImage(c, tc.component, tc.tag); got != tc.want {
			t.Errorf("ComponentImage(%q, %q) = %q, want %q", tc.component, tc.tag, got, tc.want)
		}
	}
}

func TestImagePullSecrets(t *testing.T) {
	if got := ImagePullSecrets(corpusWith("r", "", nil)); got != nil {
		t.Errorf("expected nil for no pull secrets, got %v", got)
	}
	if got := ImagePullSecrets(corpusWith("r", "", []string{})); got != nil {
		t.Errorf("expected nil for empty pull secrets, got %v", got)
	}
	got := ImagePullSecrets(corpusWith("r", "", []string{"acc-images-pull", "extra"}))
	if len(got) != 2 || got[0].Name != "acc-images-pull" || got[1].Name != "extra" {
		t.Errorf("unexpected pull secret refs: %v", got)
	}
}

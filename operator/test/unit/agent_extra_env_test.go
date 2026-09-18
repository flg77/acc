// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for spec.agents[].extraEnv delivery (operator 0.2.19, workshop-gaps
// G-10): the variables land on the agent container AFTER the operator's own
// env (value and valueFrom alike), reserved names are refused by the webhook
// and skipped by the reconciler, and an AgentCollective edit enqueues the
// corpora that reference it — the missing trigger that made extraEnv look
// ignored on bb3.
package unit_test

import (
	"context"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/controller"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
)

// otelCorpus is a corpus whose agents get the OTLP exporter env, so the test
// can prove user env is appended after an operator-set variable.
func otelCorpus() *accv1alpha1.AgentCorpus {
	corpus := &accv1alpha1.AgentCorpus{}
	corpus.Name = "demo"
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend:       accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{},
	}
	return corpus
}

// maasRole is the workshop shape: point one role at the MaaS gateway with the
// key coming from a Secret.
func maasRole() accv1alpha1.AgentRoleSpec {
	return accv1alpha1.AgentRoleSpec{
		Role: "loan_officer",
		ExtraEnv: []corev1.EnvVar{
			{Name: "ACC_LLM_BACKEND", Value: "openai_compat"},
			{Name: "ACC_LLM_BASE_URL", Value: "https://maas.example/v1"},
			{Name: "ACC_LLM_MODEL", Value: "gpt-oss-120b"},
			{Name: "ACC_LLM_API_KEY_ENV", Value: "MAAS_API_KEY"},
			{Name: "MAAS_API_KEY", ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: "maas-key"},
					Key:                  "api_key",
				},
			}},
		},
	}
}

func envIndex(envs []corev1.EnvVar, name string) int {
	for i, e := range envs {
		if e.Name == name {
			return i
		}
	}
	return -1
}

// Every extraEnv entry lands, in order, after the operator's own variables;
// a valueFrom.secretKeyRef survives untouched.
func TestExtraEnv_LandsAfterOperatorEnv(t *testing.T) {
	envs := collective.BuildExtraEnv(otelCorpus(), &accv1alpha1.AgentCollective{}, maasRole(), "")

	otel := envIndex(envs, "OTEL_EXPORTER_OTLP_ENDPOINT")
	if otel < 0 {
		t.Fatal("operator OTLP env missing — test precondition")
	}
	prev := otel
	for _, want := range []string{"ACC_LLM_BACKEND", "ACC_LLM_BASE_URL", "ACC_LLM_MODEL", "ACC_LLM_API_KEY_ENV", "MAAS_API_KEY"} {
		i := envIndex(envs, want)
		if i < 0 {
			t.Fatalf("%s did not land on the agent container: %v", want, envs)
		}
		if i < prev {
			t.Errorf("%s at index %d precedes the operator env / an earlier extraEnv entry (%d)", want, i, prev)
		}
		prev = i
	}
	if got := envs[envIndex(envs, "ACC_LLM_BACKEND")].Value; got != "openai_compat" {
		t.Errorf("ACC_LLM_BACKEND = %q, want openai_compat", got)
	}

	key := envs[envIndex(envs, "MAAS_API_KEY")]
	if key.ValueFrom == nil || key.ValueFrom.SecretKeyRef == nil {
		t.Fatalf("MAAS_API_KEY lost its secretKeyRef: %+v", key)
	}
	if key.ValueFrom.SecretKeyRef.Name != "maas-key" || key.ValueFrom.SecretKeyRef.Key != "api_key" {
		t.Errorf("secretKeyRef = %+v, want maas-key/api_key", key.ValueFrom.SecretKeyRef)
	}
}

// A reserved name is skipped by the reconciler (defence in depth for a cluster
// without the webhook); the operator's value stays and the rest still lands.
func TestExtraEnv_ReservedNameSkipped(t *testing.T) {
	role := accv1alpha1.AgentRoleSpec{
		Role: "reviewer",
		ExtraEnv: []corev1.EnvVar{
			{Name: "OTEL_EXPORTER_OTLP_ENDPOINT", Value: "evil:4317"},
			{Name: "ACC_NATS_URL", Value: "nats://elsewhere:4222"},
			{Name: "ACC_LLM_MODEL", Value: "qwen3-14b"},
		},
	}
	envs := collective.BuildExtraEnv(otelCorpus(), &accv1alpha1.AgentCollective{}, role, "")

	seen := 0
	for _, e := range envs {
		if e.Name == "OTEL_EXPORTER_OTLP_ENDPOINT" {
			seen++
			if e.Value == "evil:4317" {
				t.Errorf("reserved OTEL_EXPORTER_OTLP_ENDPOINT was overridden by extraEnv")
			}
		}
		if e.Name == "ACC_NATS_URL" {
			t.Errorf("reserved ACC_NATS_URL must not land via extraEnv")
		}
	}
	if seen != 1 {
		t.Errorf("OTEL_EXPORTER_OTLP_ENDPOINT appears %d times, want exactly the operator's one", seen)
	}
	if i := envIndex(envs, "ACC_LLM_MODEL"); i < 0 || envs[i].Value != "qwen3-14b" {
		t.Errorf("non-reserved ACC_LLM_MODEL should still land, got index %d", i)
	}

	kept := collective.UserExtraEnv(role)
	if len(kept) != 1 || kept[0].Name != "ACC_LLM_MODEL" {
		t.Errorf("UserExtraEnv = %v, want only ACC_LLM_MODEL", kept)
	}
}

// Everything BuildExtraEnv itself emits for a fully-featured corpus is in the
// reserved list — the guard that keeps ReservedAgentEnvNames honest when a
// future operator version adds a variable.
func TestReservedAgentEnv_CoversBuildExtraEnv(t *testing.T) {
	corpus := otelCorpus()
	corpus.Spec.Kafka = &accv1alpha1.KafkaSpec{
		CredentialsSecretRef: &corev1.SecretReference{Name: "kafka-creds"},
	}
	corpus.Spec.Governance.RuntimeEvidence = &accv1alpha1.RuntimeEvidenceSpec{Enabled: true}
	coll := &accv1alpha1.AgentCollective{}
	coll.Spec.LLM = accv1alpha1.LLMSpec{
		Backend:   accv1alpha1.LLMBackendAnthropic,
		Anthropic: &accv1alpha1.AnthropicSpec{},
	}
	for _, e := range collective.BuildExtraEnv(corpus, coll, accv1alpha1.AgentRoleSpec{Role: "coding"}, "") {
		if !accv1alpha1.IsReservedAgentEnv(e.Name) {
			t.Errorf("operator-set %s is missing from ReservedAgentEnvNames", e.Name)
		}
	}
	coll.Spec.LLM = accv1alpha1.LLMSpec{Backend: accv1alpha1.LLMBackendVLLM, VLLM: &accv1alpha1.VLLMSpec{}}
	for _, e := range collective.BuildExtraEnv(corpus, coll, accv1alpha1.AgentRoleSpec{Role: "coding"}, "http://isvc") {
		if !accv1alpha1.IsReservedAgentEnv(e.Name) {
			t.Errorf("operator-set %s is missing from ReservedAgentEnvNames", e.Name)
		}
	}
	for _, base := range []string{"ACC_AGENT_ROLE", "ACC_COLLECTIVE_ID", "ACC_CORPUS_NAME", "ACC_CONFIG_PATH", "ACC_POD_NAME", "ACC_POD_UID"} {
		if !accv1alpha1.IsReservedAgentEnv(base) {
			t.Errorf("container base var %s is missing from ReservedAgentEnvNames", base)
		}
	}
}

// The webhook refuses a reserved name with a message naming the field and the
// variable; the MaaS shape passes clean.
func TestCollectiveValidate_ReservedExtraEnvRejected(t *testing.T) {
	c := collectiveWith(maasRole())
	c.Spec.LLM = accv1alpha1.LLMSpec{Backend: accv1alpha1.LLMBackendOllama, Ollama: &accv1alpha1.OllamaSpec{}}
	if _, err := c.ValidateCreate(); err != nil {
		t.Fatalf("MaaS-shaped extraEnv must validate: %v", err)
	}

	c.Spec.Agents[0].ExtraEnv = append(c.Spec.Agents[0].ExtraEnv,
		corev1.EnvVar{Name: "ACC_COLLECTIVE_ID", Value: "spoofed"})
	_, err := c.ValidateCreate()
	if err == nil {
		t.Fatal("a reserved extraEnv name must be rejected")
	}
	msg := err.Error()
	for _, want := range []string{"spec.agents[0].extraEnv[5].name", "ACC_COLLECTIVE_ID", "reserved"} {
		if !strings.Contains(msg, want) {
			t.Errorf("error should mention %q, got: %s", want, msg)
		}
	}
}

// An AgentCollective event enqueues exactly the corpora whose spec.collectives
// reference it by name — the corpus controller previously only watched via
// owner references, which the operator never sets on collectives.
func TestCorpusWatch_CollectiveEventEnqueuesReferencingCorpora(t *testing.T) {
	ns := "acc-workshop"
	referencing := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "workshop", Namespace: ns},
		Spec:       accv1alpha1.AgentCorpusSpec{Collectives: []accv1alpha1.CollectiveRef{{Name: "other"}, {Name: "mortgage"}}},
	}
	unrelated := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "elsewhere", Namespace: ns},
		Spec:       accv1alpha1.AgentCorpusSpec{Collectives: []accv1alpha1.CollectiveRef{{Name: "other"}}},
	}
	otherNS := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "workshop", Namespace: "not-here"},
		Spec:       accv1alpha1.AgentCorpusSpec{Collectives: []accv1alpha1.CollectiveRef{{Name: "mortgage"}}},
	}
	c := fake.NewClientBuilder().WithScheme(newScheme(t)).WithObjects(referencing, unrelated, otherNS).Build()
	r := &controller.AgentCorpusReconciler{Client: c}

	coll := &accv1alpha1.AgentCollective{ObjectMeta: metav1.ObjectMeta{Name: "mortgage", Namespace: ns}}
	reqs := r.MapCollectiveToCorpora(context.Background(), coll)
	if len(reqs) != 1 {
		t.Fatalf("want exactly the referencing corpus enqueued, got %v", reqs)
	}
	if reqs[0].Name != "workshop" || reqs[0].Namespace != ns {
		t.Errorf("enqueued %v, want %s/workshop", reqs[0], ns)
	}

	if got := r.MapCollectiveToCorpora(context.Background(),
		&accv1alpha1.AgentCollective{ObjectMeta: metav1.ObjectMeta{Name: "orphan", Namespace: ns}}); len(got) != 0 {
		t.Errorf("an unreferenced collective must enqueue nothing, got %v", got)
	}
}

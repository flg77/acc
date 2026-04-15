// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/templates"
)

func makeTestCorpus() *accv1alpha1.AgentCorpus {
	return &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "test-corpus", Namespace: "test-ns"},
		Spec: accv1alpha1.AgentCorpusSpec{
			DeployMode:    accv1alpha1.DeployModeStandalone,
			Version:       "0.1.0",
			ImageRegistry: "registry.access.redhat.com",
			Infrastructure: accv1alpha1.InfrastructureSpec{
				NATS:  accv1alpha1.NATSSpec{Version: "2.10", Replicas: 1, StorageSize: "2Gi"},
				Redis: accv1alpha1.RedisSpec{Version: "6", Replicas: 1, StorageSize: "1Gi"},
			},
			Governance: accv1alpha1.GovernanceSpec{
				CategoryA: accv1alpha1.CategoryASpec{WASMConfigMapRef: "acc-cat-a-wasm"},
				CategoryB: accv1alpha1.CategoryBSpec{PollIntervalSeconds: 30},
			},
			Observability: accv1alpha1.ObservabilitySpec{Backend: accv1alpha1.MetricsBackendLog},
		},
	}
}

func makeTestCollective() *accv1alpha1.AgentCollective {
	return &accv1alpha1.AgentCollective{
		ObjectMeta: metav1.ObjectMeta{Name: "sol-01", Namespace: "test-ns"},
		Spec: accv1alpha1.AgentCollectiveSpec{
			CollectiveID:             "sol-01",
			CorpusRef:                corev1.LocalObjectReference{Name: "test-corpus"},
			HeartbeatIntervalSeconds: 30,
			LLM: accv1alpha1.LLMSpec{
				Backend: accv1alpha1.LLMBackendOllama,
				Ollama: &accv1alpha1.OllamaSpec{
					BaseURL: "http://ollama:11434",
					Model:   "llama3.2:3b",
				},
				EmbeddingModel: "all-MiniLM-L6-v2",
			},
		},
	}
}

func TestRenderACCConfig_StandaloneOllama(t *testing.T) {
	corpus := makeTestCorpus()
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}

	// Key fields that Python ACCConfig.model_validate() will parse.
	checks := []string{
		"deploy_mode: standalone",
		"collective_id: sol-01",
		"corpus_name: test-corpus",
		"heartbeat_interval_s: 30",
		"backend: nats",
		"nats_url: nats://test-corpus-nats:4222",
		"backend: lancedb",
		"redis_url: redis://test-corpus-redis:6379",
		"backend: ollama",
		"ollama_base_url: http://ollama:11434",
		"ollama_model: llama3.2:3b",
		"embedding_model: all-MiniLM-L6-v2",
		"bundle_server_url: http://test-corpus-opa-bundle:8181",
		"poll_interval_s: 30",
		"backend: log",
	}
	for _, check := range checks {
		if !strings.Contains(yaml, check) {
			t.Errorf("rendered acc-config.yaml missing %q\n\nFull output:\n%s", check, yaml)
		}
	}
}

func TestRenderACCConfig_AnthropicBackend(t *testing.T) {
	corpus := makeTestCorpus()
	collective := makeTestCollective()
	collective.Spec.LLM = accv1alpha1.LLMSpec{
		Backend: accv1alpha1.LLMBackendAnthropic,
		Anthropic: &accv1alpha1.AnthropicSpec{
			Model: "claude-sonnet-4-6",
			APIKeySecretRef: corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "my-secret"},
				Key:                  "ACC_ANTHROPIC_API_KEY",
			},
		},
		EmbeddingModel: "all-MiniLM-L6-v2",
	}

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "backend: anthropic") {
		t.Error("expected backend: anthropic")
	}
	if !strings.Contains(yaml, "anthropic_model: claude-sonnet-4-6") {
		t.Error("expected anthropic_model field")
	}
}

func TestRenderACCConfig_OTelBackend(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			Endpoint:    "https://otel:4317",
			ServiceName: "acc-test",
		},
	}
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "backend: otel") {
		t.Error("expected backend: otel")
	}
	if !strings.Contains(yaml, "otel_endpoint: https://otel:4317") {
		t.Error("expected otel_endpoint field")
	}
}

func TestRenderNATSConfig_SingleNode(t *testing.T) {
	corpus := makeTestCorpus()
	conf, err := templates.RenderNATSConfig(corpus)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if !strings.Contains(conf, "jetstream") {
		t.Error("expected jetstream section")
	}
	// Single-node should not have cluster block.
	if strings.Contains(conf, "cluster {") {
		t.Error("single-node config should not have cluster block")
	}
}

func TestRenderNATSConfig_Clustered(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Infrastructure.NATS.Replicas = 3

	conf, err := templates.RenderNATSConfig(corpus)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if !strings.Contains(conf, "cluster {") {
		t.Error("3-replica config should have cluster block")
	}
}

func TestRenderOTelConfig(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			Endpoint:    "https://otel:4317",
			TLSInsecure: true,
		},
	}

	conf, err := templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig error: %v", err)
	}
	if !strings.Contains(conf, "receivers:") {
		t.Error("expected receivers section")
	}
	if !strings.Contains(conf, "otlp:") {
		t.Error("expected otlp exporter")
	}
}

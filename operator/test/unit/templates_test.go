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
	// The runtime schema is acc/config.py ObservabilityConfig under the
	// top-level `observability:` key (backend + otel_service_name). The old
	// `metrics:` block was silently ignored (Pydantic extra=ignore), so agents
	// ran the log backend although the corpus said otel (0.2.17).
	if !strings.Contains(yaml, "observability:\n  backend: otel\n  otel_service_name: acc-test\n") {
		t.Errorf("expected observability block with backend otel + service name\n\n%s", yaml)
	}
	if strings.Contains(yaml, "metrics:") || strings.Contains(yaml, "otel_endpoint") {
		t.Errorf("legacy metrics:/otel_endpoint keys must not be rendered — the runtime never read them\n\n%s", yaml)
	}
}

// Log backend: the observability block is still rendered (the runtime
// defaults to log, but the key must be the one it reads) with no OTel fields.
func TestRenderACCConfig_LogBackendObservabilityKey(t *testing.T) {
	yaml, err := templates.RenderACCConfig(makeTestCorpus(), makeTestCollective())
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "observability:\n  backend: log\n") {
		t.Errorf("expected observability: backend log\n\n%s", yaml)
	}
	if strings.Contains(yaml, "otel_service_name") {
		t.Error("otel_service_name must not be rendered for the log backend")
	}
}

// ---------------------------------------------------------------------------
// Vector backend resolution (proposal 024)
// ---------------------------------------------------------------------------

func TestRenderACCConfig_RHOAIDefaultsTurboVec(t *testing.T) {
	// rhoai with neither an explicit backend nor a Milvus URI → turbovec,
	// so a corpus has working recall out of the box (operator decision Q1).
	corpus := makeTestCorpus()
	corpus.Spec.DeployMode = accv1alpha1.DeployModeRHOAI
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "backend: turbovec") {
		t.Errorf("expected turbovec backend\n\nFull output:\n%s", yaml)
	}
	if !strings.Contains(yaml, "turbovec_path: /app/data/turbovec") {
		t.Errorf("expected turbovec_path\n\nFull output:\n%s", yaml)
	}
	if strings.Contains(yaml, "milvus_uri") {
		t.Error("turbovec config should not contain milvus_uri")
	}
}

func TestRenderACCConfig_RHOAIWithMilvusURI(t *testing.T) {
	// A configured Milvus URI in rhoai mode keeps milvus as the backend.
	corpus := makeTestCorpus()
	corpus.Spec.DeployMode = accv1alpha1.DeployModeRHOAI
	corpus.Spec.Infrastructure.Milvus = &accv1alpha1.MilvusSpec{URI: "milvus.acc:19530"}
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "backend: milvus") {
		t.Errorf("expected milvus backend\n\nFull output:\n%s", yaml)
	}
	if !strings.Contains(yaml, "milvus_uri: milvus.acc:19530") {
		t.Errorf("expected milvus_uri\n\nFull output:\n%s", yaml)
	}
	if strings.Contains(yaml, "turbovec_path") {
		t.Error("milvus config should not contain turbovec_path")
	}
}

func TestRenderACCConfig_ExplicitVectorBackendWins(t *testing.T) {
	// An explicit vectorBackend overrides the per-mode default — turbovec
	// requested on a standalone corpus.
	corpus := makeTestCorpus()
	corpus.Spec.Infrastructure.VectorBackend = "turbovec"
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "backend: turbovec") {
		t.Errorf("expected explicit turbovec backend\n\nFull output:\n%s", yaml)
	}
	if !strings.Contains(yaml, "turbovec_path: /app/data/turbovec") {
		t.Errorf("expected turbovec_path\n\nFull output:\n%s", yaml)
	}
}

func TestRenderNATSConfig_SingleNode(t *testing.T) {
	corpus := makeTestCorpus()
	conf, err := templates.RenderNATSConfig(corpus, nil)
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

	conf, err := templates.RenderNATSConfig(corpus, nil)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if !strings.Contains(conf, "cluster {") {
		t.Error("3-replica config should have cluster block")
	}
}

// ---------------------------------------------------------------------------
// Edge deploy mode tests (ACC-8)
// ---------------------------------------------------------------------------

func makeEdgeCorpus() *accv1alpha1.AgentCorpus {
	c := makeTestCorpus()
	c.Spec.DeployMode = accv1alpha1.DeployModeEdge
	c.Spec.Edge = &accv1alpha1.EdgeSpec{
		HubNatsUrl:           "nats-leaf://hub.example.com:7422",
		HubCollectiveID:      "sol-dc-01",
		RedisMaxMemoryMB:     512,
		RedisMaxMemoryPolicy: "allkeys-lru",
	}
	return c
}

func TestRenderNATSConfig_EdgeLeafNode(t *testing.T) {
	corpus := makeEdgeCorpus()
	conf, err := templates.RenderNATSConfig(corpus, nil)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if !strings.Contains(conf, "leafnodes") {
		t.Error("edge config should contain leafnodes block")
	}
	if !strings.Contains(conf, "nats-leaf://hub.example.com:7422") {
		t.Errorf("expected hub URL in leafnodes block\n\nFull output:\n%s", conf)
	}
	if !strings.Contains(conf, "jetstream") {
		t.Error("edge config should still contain jetstream section")
	}
}

func TestRenderNATSConfig_StandaloneNoLeafNode(t *testing.T) {
	corpus := makeTestCorpus()
	conf, err := templates.RenderNATSConfig(corpus, nil)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if strings.Contains(conf, "leafnodes") {
		t.Error("standalone config should NOT contain leafnodes block")
	}
}

func TestRenderNATSConfig_EdgeNoHubUrl(t *testing.T) {
	corpus := makeEdgeCorpus()
	corpus.Spec.Edge.HubNatsUrl = "" // hub URL not yet configured
	conf, err := templates.RenderNATSConfig(corpus, nil)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	// No hub URL → no leafnodes block (agent operates in disconnected mode)
	if strings.Contains(conf, "leafnodes") {
		t.Error("edge config without hub URL should not contain leafnodes block")
	}
}

// ---------------------------------------------------------------------------
// NATS NKey authentication (proposal 013, PR-3)
// ---------------------------------------------------------------------------

func TestRenderNATSConfig_NKeyAuthDisabled(t *testing.T) {
	// Default corpus has no NKeyAuth — no authorization block.
	corpus := makeTestCorpus()
	conf, err := templates.RenderNATSConfig(corpus, nil)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if strings.Contains(conf, "authorization {") {
		t.Error("NKey-disabled config must not contain an authorization block")
	}
}

func TestRenderNATSConfig_NKeyEnabledEmptyKeys(t *testing.T) {
	// NKeyAuth enabled but no keys yet (PR-3 lands before PR-4's
	// Secret) — renders cleanly with no authorization block.
	corpus := makeTestCorpus()
	corpus.Spec.Infrastructure.NATS.NKeyAuth = &accv1alpha1.NKeyAuthSpec{Enabled: true}
	conf, err := templates.RenderNATSConfig(corpus, nil)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if strings.Contains(conf, "authorization {") {
		t.Error("empty key set must not render an authorization block")
	}
}

func TestRenderNATSConfig_NKeyAuthEnabled(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Infrastructure.NATS.NKeyAuth = &accv1alpha1.NKeyAuthSpec{Enabled: true}
	keys := map[string]string{
		"arbiter":      "UARBITERTESTKEY",
		"ingester":     "UINGESTERTESTKEY",
		"analyst":      "UANALYSTTESTKEY",
		"synthesizer":  "USYNTHTESTKEY",
		"coding_agent": "UCODINGTESTKEY",
		"observer":     "UOBSERVERTESTKEY",
		"tui":          "UTUITESTKEY",
		"leaf":         "ULEAFTESTKEY",
	}
	conf, err := templates.RenderNATSConfig(corpus, keys)
	if err != nil {
		t.Fatalf("RenderNATSConfig error: %v", err)
	}
	if !strings.Contains(conf, "authorization {") {
		t.Fatalf("expected an authorization block\n\n%s", conf)
	}
	for identity, pub := range keys {
		if !strings.Contains(conf, "# "+identity) {
			t.Errorf("authorization block missing identity %q", identity)
		}
		if !strings.Contains(conf, "nkey: "+pub) {
			t.Errorf("authorization block missing nkey %q", pub)
		}
	}
	// The arbiter is the sole publisher of the control subjects.
	if !strings.Contains(conf, `"acc.*.plan.*"`) {
		t.Error("authorization block missing the plan control subject")
	}
	if !strings.Contains(conf, `"acc.*.task.assign"`) {
		t.Error("authorization block missing the split task.assign subject")
	}
}

func TestRenderACCConfig_EdgeMode(t *testing.T) {
	corpus := makeEdgeCorpus()
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}

	checks := []string{
		"deploy_mode: edge",
		"hub_url: nats-leaf://hub.example.com:7422",
		"hub_collective_id: sol-dc-01",
		"bridge_enabled: true",
		"backend: lancedb", // LanceDB, not Milvus
		"backend: log",     // log metrics, not otel
	}
	for _, check := range checks {
		if !strings.Contains(yaml, check) {
			t.Errorf("edge acc-config.yaml missing %q\n\nFull output:\n%s", check, yaml)
		}
	}

	// Milvus should not appear in edge config
	if strings.Contains(yaml, "milvus_uri") {
		t.Errorf("edge config should not contain milvus_uri\n\nFull output:\n%s", yaml)
	}
}

func TestRenderACCConfig_EdgeModeDefaultsOllamaModel(t *testing.T) {
	corpus := makeEdgeCorpus()
	collective := makeTestCollective()
	// Set empty model to trigger edge default
	collective.Spec.LLM.Ollama.Model = ""

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "ollama_model: llama3.2:3b") {
		t.Errorf("edge config should default to llama3.2:3b when model is empty\n\nFull output:\n%s", yaml)
	}
}

func TestRenderACCConfig_EdgeModeOTelForcedToLog(t *testing.T) {
	corpus := makeEdgeCorpus()
	// Edge with OTel set in spec — should be overridden to log
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			Endpoint: "https://otel:4317",
		},
	}
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	if !strings.Contains(yaml, "backend: log") {
		t.Errorf("edge config should override OTel to log\n\nFull output:\n%s", yaml)
	}
}

func TestRenderACCConfig_EdgeModeNoHubCollective(t *testing.T) {
	corpus := makeEdgeCorpus()
	corpus.Spec.Edge.HubCollectiveID = "" // no hub collective
	collective := makeTestCollective()

	yaml, err := templates.RenderACCConfig(corpus, collective)
	if err != nil {
		t.Fatalf("RenderACCConfig error: %v", err)
	}
	// bridge_enabled and hub_collective_id should not appear when hub is not set
	if strings.Contains(yaml, "bridge_enabled") {
		t.Errorf("edge config without hub collective should not have bridge_enabled\n\nFull output:\n%s", yaml)
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
	// No MLflowEndpoint set → no fan-out section emitted.
	if strings.Contains(conf, "otlphttp/mlflow") {
		t.Error("MLflowEndpoint unset — fan-out section must not appear")
	}
}

// OpenSpec 20260527-mlflow-otel-telemetry Phase 3 — when MLflowEndpoint
// is set, the rendered Collector config gains an otlphttp/mlflow
// exporter on the traces pipeline alongside the primary otlp exporter.
func TestRenderOTelConfig_MLflowFanOut(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			Endpoint:       "https://otel:4317",
			MLflowEndpoint: "https://mlflow.example.com",
			TLSInsecure:    false,
		},
	}

	conf, err := templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig error: %v", err)
	}
	if !strings.Contains(conf, "otlphttp/mlflow:") {
		t.Errorf("expected otlphttp/mlflow exporter when MLflowEndpoint set\n\n%s", conf)
	}
	// MLflow rejects gzip-encoded OTLP bodies (HTTP 400); the exporter must
	// send them uncompressed.
	if !strings.Contains(conf, "compression: none") {
		t.Errorf("expected the MLflow exporter to disable compression\n\n%s", conf)
	}
	// MLflow answers a stored batch with a JSON body under a protobuf
	// content-type; without this the exporter re-sends every batch.
	if !strings.Contains(conf, "retry_on_failure:\n      enabled: false") {
		t.Errorf("expected the MLflow exporter to disable retry_on_failure\n\n%s", conf)
	}
	if !strings.Contains(conf, "endpoint: https://mlflow.example.com") {
		t.Error("expected MLflow endpoint string in rendered config")
	}
	// The remote exporter stays on the general traces pipeline; MLflow has a
	// pipeline of its own (0.2.24) so its filter touches nothing else.
	if !strings.Contains(conf, "exporters: [otlp, debug]") {
		t.Errorf("expected the general traces pipeline to keep otlp + debug\n\n%s", conf)
	}
	if !strings.Contains(conf, "    traces/mlflow:\n      receivers: [otlp]\n      processors: [memory_limiter, filter/mlflow, batch, resource]\n      exporters: [otlphttp/mlflow]") {
		t.Errorf("expected a traces/mlflow pipeline with the filter before the batch\n\n%s", conf)
	}
	// No experiment id → no headers block (behaviour unchanged from 0.2.17).
	// Match the YAML key, not the bare token: the template's NOTE comment
	// names the header too.
	if strings.Contains(conf, "headers:") || strings.Contains(conf, "x-mlflow-experiment-id:") {
		t.Errorf("MLflowExperimentID unset — no x-mlflow-experiment-id header must be rendered\n\n%s", conf)
	}
}

// MLflow lists every root span it receives as a trace. The runtime's
// lifecycle span "agent.register" (one per agent per start) is not a turn: on
// bb3 five zero-length traces appeared in the experiment at every rollout. The
// MLflow pipeline drops it; nothing else loses it, and a corpus without MLflow
// renders no filter at all.
func TestRenderOTelConfig_MLflowPipelineDropsLifecycleSpans(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			MLflowEndpoint: "https://mlflow.example.com",
		},
	}
	conf, err := templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig error: %v", err)
	}
	for _, want := range []string{
		"  filter/mlflow:\n    error_mode: ignore\n    traces:\n      span:\n        - 'name == \"agent.register\"'",
		"processors: [memory_limiter, filter/mlflow, batch, resource]",
	} {
		if !strings.Contains(conf, want) {
			t.Errorf("expected %q in the rendered config\n\n%s", want, conf)
		}
	}
	// The general pipeline is not filtered.
	if !strings.Contains(conf, "    traces:\n      receivers: [otlp]\n      processors: [memory_limiter, batch, resource]\n      exporters: [debug]") {
		t.Errorf("the general traces pipeline must stay unfiltered\n\n%s", conf)
	}

	// No MLflow endpoint: no filter, no second pipeline.
	corpus.Spec.Observability.OTelCollector.MLflowEndpoint = ""
	conf, err = templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig error: %v", err)
	}
	if strings.Contains(conf, "filter/mlflow") || strings.Contains(conf, "traces/mlflow") {
		t.Errorf("no MLflow endpoint -- neither the filter nor its pipeline may be rendered\n\n%s", conf)
	}
}

// MLflow's /v1/traces requires the x-mlflow-experiment-id header (MLflow
// >= 3.x answers 422 and stores nothing without it — verified on bb3 with
// MLflow 3.6.0). When MLflowExperimentID is set the otlphttp/mlflow
// exporter carries it as a header (0.2.18).
func TestRenderOTelConfig_MLflowExperimentIDHeader(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			MLflowEndpoint:     "https://mlflow.example.com",
			MLflowExperimentID: "123456789",
		},
	}

	conf, err := templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig error: %v", err)
	}
	// The exporter block from compression: on (the comment lines above it are not asserted).
	want := "    compression: none\n" +
		"    tls:\n" +
		"      insecure: false\n" +
		"    headers:\n" +
		"      x-mlflow-experiment-id: \"123456789\"\n"
	if !strings.Contains(conf, want) {
		t.Errorf("expected otlphttp/mlflow exporter with x-mlflow-experiment-id header\n\nwant:\n%s\ngot:\n%s", want, conf)
	}
}

// RHOAI's MLflow: the collector authenticates with its ServiceAccount token,
// verifies the endpoint against the injected service CA and names the
// workspace — and the plain-MLflow shape is untouched when mlflowAuth is "".
func TestRenderOTelConfig_MLflowKubernetesAuthAndWorkspace(t *testing.T) {
	corpus := &accv1alpha1.AgentCorpus{
		ObjectMeta: metav1.ObjectMeta{Name: "test-corpus", Namespace: "wksp-user2"},
		Spec: accv1alpha1.AgentCorpusSpec{
			Observability: accv1alpha1.ObservabilitySpec{
				Backend: accv1alpha1.MetricsBackendOTel,
				OTelCollector: &accv1alpha1.OTelCollectorSpec{
					MLflowEndpoint:     "https://mlflow.redhat-ods-applications.svc:8443",
					MLflowExperimentID: "1",
					MLflowWorkspace:    "wksp-user2",
					MLflowAuth:         accv1alpha1.MLflowAuthKubernetes,
				},
			},
		},
	}
	conf, err := templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig: %v", err)
	}
	for _, want := range []string{
		"\nextensions:\n",
		"  bearertokenauth/mlflow:\n    filename: /var/run/secrets/kubernetes.io/serviceaccount/token",
		"    auth:\n      authenticator: bearertokenauth/mlflow",
		"      ca_file: /etc/acc/service-ca/service-ca.crt",
		"      x-mlflow-experiment-id: \"1\"",
		"      X-MLFLOW-WORKSPACE: \"wksp-user2\"",
		"  extensions: [bearertokenauth/mlflow]",
	} {
		if !strings.Contains(conf, want) {
			t.Errorf("expected %q in the rendered config\n\n%s", want, conf)
		}
	}
	if strings.Contains(conf, "insecure: false") {
		t.Errorf("kubernetes auth must not render tls.insecure\n\n%s", conf)
	}

	corpus.Spec.Observability.OTelCollector.MLflowAuth = ""
	corpus.Spec.Observability.OTelCollector.MLflowWorkspace = ""
	conf, err = templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig: %v", err)
	}
	for _, absent := range []string{"bearertokenauth", "ca_file", "X-MLFLOW-WORKSPACE"} {
		if strings.Contains(conf, absent) {
			t.Errorf("did not expect %q without mlflowAuth\n\n%s", absent, conf)
		}
	}
}

// The removed-upstream `logging` exporter must never reappear — modern
// collector-contrib builds reject it at config validation and CrashLoop
// (RHOAI test 11.6 finding 4).
func TestRenderOTelConfig_NoRemovedLoggingExporter(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			Endpoint: "https://otel:4317",
		},
	}

	conf, err := templates.RenderOTelConfig(corpus)
	if err != nil {
		t.Fatalf("RenderOTelConfig error: %v", err)
	}
	if strings.Contains(conf, "logging:") || strings.Contains(conf, ", logging]") {
		t.Errorf("rendered config references the removed `logging` exporter\n\n%s", conf)
	}
	if !strings.Contains(conf, "debug:") {
		t.Errorf("expected the `debug` exporter\n\n%s", conf)
	}
}

// 0.2.17: otelCollector.endpoint is the collector's REMOTE target. When it
// names the corpus's own collector Service (the pre-0.2.17 webhook default)
// or is empty, no `otlp` exporter is rendered — the collector must not
// forward to itself. MLflow fan-out is independent and stays.
func TestRenderOTelConfig_NoSelfLoop(t *testing.T) {
	for _, endpoint := range []string{
		"",
		"test-corpus-otel-collector:4317",
		"test-corpus-otel-collector",
		"http://test-corpus-otel-collector.test-ns.svc:4318",
		"https://test-corpus-otel-collector.test-ns.svc.cluster.local:4317/",
	} {
		corpus := makeTestCorpus()
		corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
			Backend: accv1alpha1.MetricsBackendOTel,
			OTelCollector: &accv1alpha1.OTelCollectorSpec{
				Endpoint:       endpoint,
				MLflowEndpoint: "http://mlflow.test-ns.svc:8080",
			},
		}
		conf, err := templates.RenderOTelConfig(corpus)
		if err != nil {
			t.Fatalf("RenderOTelConfig(%q) error: %v", endpoint, err)
		}
		// The `otlp` receiver stays; the `otlp` exporter (the one with an
		// `endpoint:` directly beneath it) must be gone from both pipelines.
		if strings.Contains(conf, "  otlp:\n    endpoint:") ||
			strings.Contains(conf, "exporters: [otlp,") || strings.Contains(conf, "exporters: [otlp]") {
			t.Errorf("endpoint %q must not render a remote otlp exporter\n\n%s", endpoint, conf)
		}
		if !strings.Contains(conf, "exporters: [otlphttp/mlflow]") {
			t.Errorf("endpoint %q: MLflow fan-out must be unaffected\n\n%s", endpoint, conf)
		}
		if !strings.Contains(conf, "exporters: [prometheus, debug]") {
			t.Errorf("endpoint %q: metrics pipeline must keep prometheus + debug\n\n%s", endpoint, conf)
		}
	}
}

// A real remote (not the own Service, even a look-alike host) still gets the
// otlp exporter on both pipelines.
func TestRenderOTelConfig_RemoteEndpointExported(t *testing.T) {
	for _, endpoint := range []string{
		"tempo-acc-tempo.acc-observability.svc.cluster.local:4317",
		"https://otel-collector.observability.svc.cluster.local:4317",
		"other-corpus-otel-collector:4317",
	} {
		corpus := makeTestCorpus()
		corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
			Backend:       accv1alpha1.MetricsBackendOTel,
			OTelCollector: &accv1alpha1.OTelCollectorSpec{Endpoint: endpoint, TLSInsecure: true},
		}
		conf, err := templates.RenderOTelConfig(corpus)
		if err != nil {
			t.Fatalf("RenderOTelConfig(%q) error: %v", endpoint, err)
		}
		if !strings.Contains(conf, "  otlp:\n    endpoint: "+endpoint+"\n    tls:\n      insecure: true") {
			t.Errorf("endpoint %q: expected remote otlp exporter\n\n%s", endpoint, conf)
		}
		if !strings.Contains(conf, "exporters: [otlp, debug]") || !strings.Contains(conf, "exporters: [otlp, prometheus, debug]") {
			t.Errorf("endpoint %q: expected otlp on both pipelines\n\n%s", endpoint, conf)
		}
	}
}

// Agents always export to the corpus's own collector Service via the
// upstream env vars the runtime reads (acc/backends/metrics_otel.py) —
// regardless of otelCollector.endpoint, which is the collector's remote.
func TestOTelAgentExporterEnv(t *testing.T) {
	corpus := makeTestCorpus()
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend:       accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{Endpoint: "tempo.acc-observability.svc:4317"},
	}
	env := templates.OTelAgentExporterEnv(corpus)
	want := map[string]string{
		"OTEL_EXPORTER_OTLP_ENDPOINT": "test-corpus-otel-collector:4317",
		"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
	}
	got := map[string]string{}
	for _, e := range env {
		got[e.Name] = e.Value
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %q, want %q (env=%v)", k, got[k], v, env)
		}
	}

	corpus.Spec.Observability.OTelCollector.Protocol = "http/protobuf"
	env = templates.OTelAgentExporterEnv(corpus)
	if len(env) != 2 || env[0].Value != "http://test-corpus-otel-collector:4318" || env[1].Value != "http/protobuf" {
		t.Errorf("http/protobuf: expected the collector's :4318 HTTP receiver, got %v", env)
	}

	// No collector deployed → no exporter target (runtime would default to
	// localhost, but the backend is log/absent anyway).
	corpus.Spec.Observability.OTelCollector = nil
	if env := templates.OTelAgentExporterEnv(corpus); env != nil {
		t.Errorf("nil otelCollector: expected no env, got %v", env)
	}
	if env := templates.OTelAgentExporterEnv(makeTestCorpus()); env != nil {
		t.Errorf("log backend: expected no env, got %v", env)
	}
	edge := makeTestCorpus()
	edge.Spec.DeployMode = accv1alpha1.DeployModeEdge
	edge.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel, OTelCollector: &accv1alpha1.OTelCollectorSpec{},
	}
	if env := templates.OTelAgentExporterEnv(edge); env != nil {
		t.Errorf("edge mode: expected no env, got %v", env)
	}
}

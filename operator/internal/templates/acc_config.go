// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package templates renders the configuration files that are mounted into ACC
// agent pods. The primary output is acc-config.yaml, which the Python
// acc/config.py ACCConfig.model_validate() parses at agent startup.
//
// IMPORTANT: the YAML field names here must stay in sync with acc/config.py.
// Any rename in the Go types must be reflected in the Python model.
package templates

import (
	"bytes"
	"fmt"
	"text/template"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// accConfigTmpl is the Go text/template for acc-config.yaml.
// Field names match the Python ACCConfig Pydantic model exactly.
var accConfigTmpl = template.Must(template.New("acc-config").Parse(`# acc-config.yaml — managed by acc-operator. DO NOT EDIT MANUALLY.
# Corpus: {{ .CorpusName }}  Collective: {{ .CollectiveID }}

deploy_mode: {{ .DeployMode }}

agent:
  collective_id: {{ .CollectiveID }}
  corpus_name: {{ .CorpusName }}
  role: ${ACC_AGENT_ROLE}
  heartbeat_interval_s: {{ .HeartbeatIntervalSeconds }}
{{ if .HubCollectiveID -}}
  hub_collective_id: {{ .HubCollectiveID }}
  bridge_enabled: true
{{ end -}}

signaling:
  backend: nats
  nats_url: nats://{{ .NATSServiceName }}:4222
{{ if .NATSHubUrl -}}
  hub_url: {{ .NATSHubUrl }}
{{ end -}}

vector_db:
  backend: {{ .VectorBackend }}
{{ if .MilvusURI -}}
  milvus_uri: {{ .MilvusURI }}
  milvus_collection_prefix: {{ .MilvusPrefix }}
{{ end -}}

cache:
  backend: redis
  redis_url: redis://{{ .RedisServiceName }}:6379

llm:
  backend: {{ .LLMBackend }}
{{ if .OllamaBaseURL -}}
  ollama_base_url: {{ .OllamaBaseURL }}
  ollama_model: {{ .OllamaModel }}
{{ end -}}
{{ if .AnthropicModel -}}
  anthropic_model: {{ .AnthropicModel }}
{{ end -}}
{{ if .VLLMInferenceURL -}}
  vllm_inference_url: {{ .VLLMInferenceURL }}
  vllm_model: {{ .VLLMModel }}
{{ end -}}
{{ if .LlamaStackBaseURL -}}
  llama_stack_url: {{ .LlamaStackBaseURL }}
  llama_stack_model_id: {{ .LlamaStackModelID }}
{{ end -}}
  embedding_model: {{ .EmbeddingModel }}

governance:
  category_a:
    wasm_path: /etc/acc/governance/category_a.wasm
  category_b:
    bundle_server_url: http://{{ .OPABundleServiceName }}:8181
    poll_interval_s: {{ .BundlePollInterval }}
{{ if .ConfidenceThreshold -}}
  category_c:
    confidence_threshold: {{ .ConfidenceThreshold }}
{{ end -}}

metrics:
  backend: {{ .MetricsBackend }}
{{ if .OTelEndpoint -}}
  otel_endpoint: {{ .OTelEndpoint }}
  otel_service_name: {{ .OTelServiceName }}
{{ end -}}
`))

// ACCConfigData holds all values needed to render acc-config.yaml.
type ACCConfigData struct {
	CorpusName              string
	CollectiveID            string
	DeployMode              string
	HeartbeatIntervalSeconds int32

	// Signaling
	NATSServiceName string
	// NATSHubUrl is the NATS leaf node hub URL (edge mode only).
	NATSHubUrl string
	// HubCollectiveID enables ACC-9 bridge delegation to the datacenter hub.
	HubCollectiveID string

	// Vector DB
	VectorBackend string
	MilvusURI     string
	MilvusPrefix  string

	// Cache
	RedisServiceName string

	// LLM
	LLMBackend        string
	OllamaBaseURL     string
	OllamaModel       string
	AnthropicModel    string
	VLLMInferenceURL  string
	VLLMModel         string
	LlamaStackBaseURL string
	LlamaStackModelID string
	EmbeddingModel    string

	// Governance
	OPABundleServiceName string
	BundlePollInterval   int32
	ConfidenceThreshold  string

	// Metrics
	MetricsBackend  string
	OTelEndpoint    string
	OTelServiceName string
}

// RenderACCConfig produces the acc-config.yaml content for a given
// AgentCorpus + AgentCollective pair. The rendered YAML is mounted as a
// ConfigMap into each agent pod.
//
// For deployMode=edge the following defaults are applied when the collective
// spec does not override them:
//   - LLM backend: ollama with llama3.2:3b (fits in 4 GiB VRAM)
//   - Metrics backend: log (no OTel Collector at edge)
//   - hub_url and hub_collective_id are populated from spec.edge when set
func RenderACCConfig(corpus *accv1alpha1.AgentCorpus, collective *accv1alpha1.AgentCollective) (string, error) {
	data := ACCConfigData{
		CorpusName:               corpus.Name,
		CollectiveID:             collective.Spec.CollectiveID,
		DeployMode:               string(corpus.Spec.DeployMode),
		HeartbeatIntervalSeconds: collective.Spec.HeartbeatIntervalSeconds,
		NATSServiceName:          fmt.Sprintf("%s-nats", corpus.Name),
		RedisServiceName:         fmt.Sprintf("%s-redis", corpus.Name),
		OPABundleServiceName:     fmt.Sprintf("%s-opa-bundle", corpus.Name),
		BundlePollInterval:       corpus.Spec.Governance.CategoryB.PollIntervalSeconds,
		EmbeddingModel:           collective.Spec.LLM.EmbeddingModel,
		MetricsBackend:           string(corpus.Spec.Observability.Backend),
	}

	// Edge mode: populate hub URL and collective ID; force log metrics backend.
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge && corpus.Spec.Edge != nil {
		data.NATSHubUrl = corpus.Spec.Edge.HubNatsUrl
		data.HubCollectiveID = corpus.Spec.Edge.HubCollectiveID
		// Edge nodes do not have an OTel Collector — override to log.
		if data.MetricsBackend == string(accv1alpha1.MetricsBackendOTel) {
			data.MetricsBackend = string(accv1alpha1.MetricsBackendLog)
		}
	}

	// Confidence threshold from Category C.
	if catC := corpus.Spec.Governance.CategoryC; catC != nil {
		data.ConfidenceThreshold = catC.ConfidenceThreshold
	}

	// OTel endpoint — use data.MetricsBackend (already adjusted for edge) rather
	// than corpus.Spec.Observability.Backend so edge mode correctly skips this block.
	if data.MetricsBackend == string(accv1alpha1.MetricsBackendOTel) {
		if otel := corpus.Spec.Observability.OTelCollector; otel != nil {
			data.OTelEndpoint = otel.Endpoint
			data.OTelServiceName = otel.ServiceName
			if data.OTelServiceName == "" {
				data.OTelServiceName = "acc-agent"
			}
		}
	}

	// Vector DB backend.
	// edge uses LanceDB on local NVMe (same as standalone).
	switch corpus.Spec.DeployMode {
	case accv1alpha1.DeployModeRHOAI:
		data.VectorBackend = "milvus"
		if milvus := corpus.Spec.Infrastructure.Milvus; milvus != nil {
			data.MilvusURI = milvus.URI
			data.MilvusPrefix = milvus.CollectionPrefix
			if data.MilvusPrefix == "" {
				data.MilvusPrefix = "acc_"
			}
		}
	default: // standalone, edge
		data.VectorBackend = "lancedb"
	}

	// LLM backend: apply edge defaults when the collective spec uses ollama
	// without an explicit model override (default 3B for 4 GiB edge hardware).
	llm := collective.Spec.LLM
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge &&
		llm.Backend == accv1alpha1.LLMBackendOllama &&
		llm.Ollama != nil && llm.Ollama.Model == "" {
		llm = *(&collective.Spec.LLM) // shallow copy
		ollama := *llm.Ollama
		ollama.Model = "llama3.2:3b"
		llm.Ollama = &ollama
	}

	// LLM backend.
	data.LLMBackend = string(llm.Backend)
	switch llm.Backend {
	case accv1alpha1.LLMBackendOllama:
		if llm.Ollama != nil {
			data.OllamaBaseURL = llm.Ollama.BaseURL
			data.OllamaModel = llm.Ollama.Model
		}
	case accv1alpha1.LLMBackendAnthropic:
		if llm.Anthropic != nil {
			data.AnthropicModel = llm.Anthropic.Model
		}
	case accv1alpha1.LLMBackendVLLM:
		if llm.VLLM != nil {
			// URL is discovered from InferenceService status at runtime;
			// we pass a placeholder env-var that the agent resolves.
			data.VLLMInferenceURL = "${ACC_VLLM_INFERENCE_URL}"
			data.VLLMModel = llm.VLLM.Model
		}
	case accv1alpha1.LLMBackendLlamaStack:
		if llm.LlamaStack != nil {
			data.LlamaStackBaseURL = llm.LlamaStack.BaseURL
			data.LlamaStackModelID = llm.LlamaStack.ModelID
		}
	}

	var buf bytes.Buffer
	if err := accConfigTmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("render acc-config.yaml: %w", err)
	}
	return buf.String(), nil
}

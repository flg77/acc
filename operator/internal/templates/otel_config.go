// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package templates

import (
	"bytes"
	"fmt"
	"strings"
	"text/template"

	corev1 "k8s.io/api/core/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

var otelConfTmpl = template.Must(template.New("otel").Parse(`# OpenTelemetry Collector configuration managed by acc-operator
# Corpus: {{ .CorpusName }}

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
  prometheus:
    config:
      scrape_configs:
        - job_name: acc-agents
          scrape_interval: 15s
          static_configs:
            - targets: []  # agents register via SD

processors:
  batch:
    timeout: 10s
  memory_limiter:
    check_interval: 1s
    limit_percentage: 75
    spike_limit_percentage: 15
  resource:
    attributes:
      - key: corpus
        value: {{ .CorpusName }}
        action: insert

{{ if .MLflowKubernetesAuth -}}
extensions:
  # RHOAI's MLflow authenticates by the caller's ServiceAccount token
  # (SubjectAccessReview in the workspace namespace). The extension reads the
  # mounted token file on every request, so a rotated token is picked up.
  bearertokenauth/mlflow:
    filename: /var/run/secrets/kubernetes.io/serviceaccount/token

{{ end -}}
exporters:
  {{ if .RemoteEndpoint -}}
  otlp:
    endpoint: {{ .RemoteEndpoint }}
    tls:
      insecure: {{ .TLSInsecure }}
  {{- end }}
  {{ if .MLflowEndpoint -}}
  # OpenSpec 20260527-mlflow-otel-telemetry Phase 3 — optional MLflow
  # fan-out.  Exports traces via OTLP/HTTP to MLflow's /v1/traces.
  # NOTE: MLflow >= 3.x requires the x-mlflow-experiment-id header on
  # /v1/traces (422 + nothing stored without it) — set
  # otelCollector.mlflowExperimentID or the spans are silently dropped.
  otlphttp/mlflow:
    endpoint: {{ .MLflowEndpoint }}
    # MLflow (3.6 verified) answers a stored batch with HTTP 200,
    # Content-Type application/x-protobuf and the JSON body
    # {"partialSuccess":null}: the exporter cannot parse the reply, treats
    # the export as failed and re-sends the same batch for retry's
    # max_elapsed_time although the spans are already stored. No retry:
    # a batch is posted once, and the log stays quiet.
    retry_on_failure:
      enabled: false
    # The exporter gzips by default and MLflow (3.6 verified) does not
    # decompress: every batch came back 400 "Invalid OpenTelemetry protobuf
    # format" while the identical body uncompressed was stored.
    compression: none
    {{- if .MLflowKubernetesAuth }}
    auth:
      authenticator: bearertokenauth/mlflow
    tls:
      # The OpenShift service CA, injected into <collector>-service-ca.
      ca_file: /etc/acc/service-ca/service-ca.crt
    {{- else }}
    tls:
      insecure: {{ .TLSInsecure }}
    {{- end }}
    {{- if or .MLflowExperimentID .MLflowWorkspace }}
    headers:
      {{- if .MLflowExperimentID }}
      x-mlflow-experiment-id: "{{ .MLflowExperimentID }}"
      {{- end }}
      {{- if .MLflowWorkspace }}
      X-MLFLOW-WORKSPACE: "{{ .MLflowWorkspace }}"
      {{- end }}
    {{- end }}
  {{- end }}
  prometheus:
    endpoint: "0.0.0.0:8889"
  # NOTE: must stay "debug" — the old "logging" exporter was removed from
  # collector-contrib and fails config validation (instant CrashLoop).
  debug:
    verbosity: normal

service:
  {{- if .MLflowKubernetesAuth }}
  extensions: [bearertokenauth/mlflow]
  {{- end }}
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch, resource]
      exporters: [{{ if .RemoteEndpoint }}otlp, {{ end }}{{ if .MLflowEndpoint }}otlphttp/mlflow, {{ end }}debug]
    metrics:
      receivers: [otlp, prometheus]
      processors: [memory_limiter, batch, resource]
      exporters: [{{ if .RemoteEndpoint }}otlp, {{ end }}prometheus, debug]
`))

type otelConfigData struct {
	CorpusName         string
	RemoteEndpoint     string
	MLflowEndpoint     string
	MLflowExperimentID string
	MLflowWorkspace    string
	// MLflowKubernetesAuth: otelCollector.mlflowAuth == "kubernetes".
	MLflowKubernetesAuth bool
	TLSInsecure          bool
}

// RenderOTelConfig produces an otel-collector.yaml from the corpus spec.
//
// spec.observability.otelCollector.endpoint is the collector's REMOTE target
// only (Tempo, Jaeger, a central collector …). Agents never read it — they
// always export to the corpus's own collector Service (OTelAgentExporterEnv).
// The remote `otlp` exporter is omitted when the endpoint is empty or names
// that own Service: pre-0.2.17 the webhook defaulted the field to
// `<corpus>-otel-collector:4317`, which made the collector forward to itself
// (`tls: first record does not look like a TLS handshake` every minute).
func RenderOTelConfig(corpus *accv1alpha1.AgentCorpus) (string, error) {
	data := otelConfigData{CorpusName: corpus.Name}
	if otel := corpus.Spec.Observability.OTelCollector; otel != nil {
		if !IsOwnOTelCollector(corpus.Name, otel.Endpoint) {
			data.RemoteEndpoint = otel.Endpoint
		}
		data.MLflowEndpoint = otel.MLflowEndpoint
		data.MLflowExperimentID = otel.MLflowExperimentID
		data.MLflowWorkspace = otel.MLflowWorkspace
		data.MLflowKubernetesAuth = otel.MLflowAuth == accv1alpha1.MLflowAuthKubernetes
		data.TLSInsecure = otel.TLSInsecure
	}

	var buf bytes.Buffer
	if err := otelConfTmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("render otel config: %w", err)
	}
	return buf.String(), nil
}

// IsOwnOTelCollector reports whether endpoint names the corpus's own
// collector Service (`<corpus>-otel-collector`, bare or as any in-cluster
// FQDN, with or without a scheme, port or path). Such an endpoint is not a
// remote target: exporting to it would loop the collector onto itself.
func IsOwnOTelCollector(corpusName, endpoint string) bool {
	host := endpoint
	if i := strings.Index(host, "://"); i >= 0 {
		host = host[i+3:]
	}
	if i := strings.IndexAny(host, ":/"); i >= 0 {
		host = host[:i]
	}
	svc := util.OTelCollectorServiceName(corpusName)
	return host == svc || strings.HasPrefix(host, svc+".")
}

// OTelAgentExporterEnv returns the env vars that point an agent's OTel
// backend (acc/backends/metrics_otel.py) at the corpus's own collector
// Service. The runtime reads the target from the upstream
// OTEL_EXPORTER_OTLP_ENDPOINT / OTEL_EXPORTER_OTLP_PROTOCOL variables — it
// is not an acc-config.yaml key — and defaults to localhost, so without these
// an otel corpus silently exports nothing. Returns nil when no collector is
// deployed for the corpus (log backend, edge mode, or no otelCollector
// block — the same guards as the OTelCollectorReconciler).
func OTelAgentExporterEnv(corpus *accv1alpha1.AgentCorpus) []corev1.EnvVar {
	obs := corpus.Spec.Observability
	if obs.Backend != accv1alpha1.MetricsBackendOTel ||
		corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge ||
		obs.OTelCollector == nil {
		return nil
	}
	svc := util.OTelCollectorServiceName(corpus.Name)
	protocol := obs.OTelCollector.Protocol
	endpoint := fmt.Sprintf("%s:4317", svc)
	if protocol == "http/protobuf" {
		endpoint = fmt.Sprintf("http://%s:4318", svc)
	} else {
		protocol = "grpc"
	}
	return []corev1.EnvVar{
		{Name: "OTEL_EXPORTER_OTLP_ENDPOINT", Value: endpoint},
		{Name: "OTEL_EXPORTER_OTLP_PROTOCOL", Value: protocol},
	}
}

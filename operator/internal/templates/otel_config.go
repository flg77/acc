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
	"text/template"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
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

exporters:
  {{ if .RemoteEndpoint -}}
  otlp:
    endpoint: {{ .RemoteEndpoint }}
    tls:
      insecure: {{ .TLSInsecure }}
  {{- end }}
  prometheus:
    endpoint: "0.0.0.0:8888"
  logging:
    verbosity: normal

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch, resource]
      exporters: [{{ if .RemoteEndpoint }}otlp, {{ end }}logging]
    metrics:
      receivers: [otlp, prometheus]
      processors: [memory_limiter, batch, resource]
      exporters: [{{ if .RemoteEndpoint }}otlp, {{ end }}prometheus, logging]
`))

type otelConfigData struct {
	CorpusName      string
	RemoteEndpoint  string
	TLSInsecure     bool
}

// RenderOTelConfig produces an otel-collector.yaml from the corpus spec.
func RenderOTelConfig(corpus *accv1alpha1.AgentCorpus) (string, error) {
	data := otelConfigData{CorpusName: corpus.Name}
	if otel := corpus.Spec.Observability.OTelCollector; otel != nil {
		data.RemoteEndpoint = otel.Endpoint
		data.TLSInsecure = otel.TLSInsecure
	}

	var buf bytes.Buffer
	if err := otelConfTmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("render otel config: %w", err)
	}
	return buf.String(), nil
}

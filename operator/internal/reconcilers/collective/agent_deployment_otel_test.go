// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package collective

import (
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// 0.2.17: agent containers carry the upstream OTLP exporter env vars the
// runtime's OTel backend actually reads, pointing at the corpus's OWN
// collector Service — whatever otelCollector.endpoint (the collector's remote
// target) says. Before this the pods got neither variable, the runtime fell
// back to localhost:4317 and an otel corpus exported nothing.
func TestBuildExtraEnv_OTelExporterTargetsOwnCollector(t *testing.T) {
	corpus := &accv1alpha1.AgentCorpus{}
	corpus.Name = "demo"
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend:       accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{Endpoint: "tempo-acc-tempo.acc-observability.svc.cluster.local:4317"},
	}
	coll := &accv1alpha1.AgentCollective{}
	roleSpec := accv1alpha1.AgentRoleSpec{Role: "coding"}

	got := map[string]string{}
	for _, e := range buildExtraEnv(corpus, coll, roleSpec, "") {
		got[e.Name] = e.Value
	}
	if got["OTEL_EXPORTER_OTLP_ENDPOINT"] != "demo-otel-collector:4317" {
		t.Errorf("OTEL_EXPORTER_OTLP_ENDPOINT = %q, want demo-otel-collector:4317", got["OTEL_EXPORTER_OTLP_ENDPOINT"])
	}
	if got["OTEL_EXPORTER_OTLP_PROTOCOL"] != "grpc" {
		t.Errorf("OTEL_EXPORTER_OTLP_PROTOCOL = %q, want grpc", got["OTEL_EXPORTER_OTLP_PROTOCOL"])
	}

	// Log backend: no exporter env at all.
	corpus.Spec.Observability = accv1alpha1.ObservabilitySpec{Backend: accv1alpha1.MetricsBackendLog}
	for _, e := range buildExtraEnv(corpus, coll, roleSpec, "") {
		if e.Name == "OTEL_EXPORTER_OTLP_ENDPOINT" || e.Name == "OTEL_EXPORTER_OTLP_PROTOCOL" {
			t.Errorf("log backend must not set %s", e.Name)
		}
	}
}

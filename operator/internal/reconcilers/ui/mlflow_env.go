// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package ui

import (
	corev1 "k8s.io/api/core/v1"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// mlflowEnv returns the MLflow env vars for the UI pods (TUI, WebGUI) when
// observability.mlflowTrackingUri is set, else nil.
//
// This is the experiments/runs layer — ACC_MLFLOW_TRACKING_URI drives
// golden-suite run logging (acc.backends.mlflow_runs) + the eval-history
// "trace →" deep links. It is INDEPENDENT of otelCollector.mlflowEndpoint,
// which fans TRACES to MLflow via the collector's traces pipeline.
//
// Injected ONLY into the UI surfaces that render runs/links, deliberately NOT
// into agent pods: agents never call the run logger, and keeping MLflow off
// agent pods preserves the FQDN-egress NetworkPolicy posture (the runtime's
// "off the hot path" constraint in acc.backends.mlflow_runs).
func mlflowEnv(corpus *accv1alpha1.AgentCorpus) []corev1.EnvVar {
	uri := corpus.Spec.Observability.MLflowTrackingURI
	if uri == "" {
		return nil
	}
	env := []corev1.EnvVar{{Name: "ACC_MLFLOW_TRACKING_URI", Value: uri}}
	if exp := corpus.Spec.Observability.MLflowExperiment; exp != "" {
		env = append(env, corev1.EnvVar{Name: "ACC_MLFLOW_EXPERIMENT", Value: exp})
	}
	return env
}

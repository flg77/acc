// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package ui

import (
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// TestMLflowEnv covers the experiments/runs env injection: off unless the
// tracking URI is set, the experiment var only when non-empty.
func TestMLflowEnv(t *testing.T) {
	corpusWith := func(uri, exp string) *accv1alpha1.AgentCorpus {
		c := &accv1alpha1.AgentCorpus{}
		c.Spec.Observability.MLflowTrackingURI = uri
		c.Spec.Observability.MLflowExperiment = exp
		return c
	}

	// Unset URI → no env at all (edge/default posture).
	if got := mlflowEnv(corpusWith("", "")); got != nil {
		t.Errorf("no tracking URI must yield nil env, got %+v", got)
	}
	// An experiment without a URI is still off (URI is the master gate).
	if got := mlflowEnv(corpusWith("", "my-exp")); got != nil {
		t.Errorf("experiment without URI must yield nil env, got %+v", got)
	}

	// URI only → exactly ACC_MLFLOW_TRACKING_URI.
	got := mlflowEnv(corpusWith("http://mlflow.acc.svc:5000", ""))
	if len(got) != 1 || got[0].Name != "ACC_MLFLOW_TRACKING_URI" ||
		got[0].Value != "http://mlflow.acc.svc:5000" {
		t.Fatalf("URI-only env wrong: %+v", got)
	}

	// URI + experiment → both, in order.
	got = mlflowEnv(corpusWith("http://mlflow.acc.svc:5000", "acc-golden-prompts"))
	if len(got) != 2 {
		t.Fatalf("expected 2 env vars, got %+v", got)
	}
	if got[0].Name != "ACC_MLFLOW_TRACKING_URI" ||
		got[1].Name != "ACC_MLFLOW_EXPERIMENT" ||
		got[1].Value != "acc-golden-prompts" {
		t.Errorf("URI+experiment env wrong: %+v", got)
	}
}

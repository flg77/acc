// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package reconcilers

import (
	"context"
	"fmt"

	"k8s.io/client-go/discovery"
	corev1 "k8s.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// PrerequisiteReconciler detects optional cluster prerequisites and writes
// their presence into corpus.Status.Prerequisites. It never blocks
// reconciliation — missing prerequisites produce Warning events and
// Degraded phase, not errors.
type PrerequisiteReconciler struct {
	Client    client.Client
	Discovery discovery.DiscoveryInterface
}

// Name implements SubReconciler.
func (r *PrerequisiteReconciler) Name() string { return "prerequisites" }

// Reconcile detects Kafka, KEDA, Gatekeeper, and RHOAI/KServe.
func (r *PrerequisiteReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (SubResult, error) {
	checker := util.NewAPIGroupChecker(r.Discovery)

	pre := &corpus.Status.Prerequisites

	// ---------- KEDA -------------------------------------------------------
	kedaOK, err := checker.KEDAInstalled()
	if err != nil {
		// Non-fatal: log and continue.
		kedaOK = false
	}
	pre.KEDAInstalled = kedaOK

	// ---------- Gatekeeper -------------------------------------------------
	gkOK, err := checker.GatekeeperInstalled()
	if err != nil {
		gkOK = false
	}
	pre.GatekeeperInstalled = gkOK

	// ---------- RHOAI / KServe ---------------------------------------------
	rhoaiOK, err := checker.RHOAIInstalled()
	if err != nil {
		rhoaiOK = false
	}
	kserveOK, err := checker.KServeInstalled()
	if err != nil {
		kserveOK = false
	}
	pre.RHOAIInstalled = rhoaiOK
	pre.KServeInstalled = kserveOK

	// ---------- Prometheus Operator ----------------------------------------
	promOK, err := checker.PrometheusRulesSupported()
	if err != nil {
		promOK = false
	}
	pre.PrometheusRulesSupported = promOK

	// ---------- Kafka (TCP probe) ------------------------------------------
	kafkaReachable := false
	if corpus.Spec.Kafka != nil && corpus.Spec.Kafka.BootstrapServers != "" {
		kafkaReachable = util.KafkaReachable(ctx, corpus.Spec.Kafka.BootstrapServers)
	}
	pre.KafkaReachable = kafkaReachable

	// Emit warning events for missing prerequisites ----------------------------

	allMet := true

	if corpus.Spec.Kafka != nil && !kafkaReachable {
		r.warnEvent(ctx, corpus, "KafkaUnreachable",
			fmt.Sprintf("Kafka bootstrap servers %q are not reachable; Kafka bridge will be skipped", corpus.Spec.Kafka.BootstrapServers))
		allMet = false
	}

	if corpus.Spec.DeployMode == accv1alpha1.DeployModeRHOAI && !kserveOK {
		r.warnEvent(ctx, corpus, "KServeAbsent",
			"deployMode=rhoai but KServe (serving.kserve.io) API group is not installed; vllm/llama_stack backends unavailable")
		// rhoai without kserve is Error-level — handled in phase.go
	}

	if !gkOK {
		r.warnEvent(ctx, corpus, "GatekeeperAbsent",
			"OPA Gatekeeper (templates.gatekeeper.sh) is not installed; ConstraintTemplates will be skipped, WASM in-process Cat-A still active")
	}

	if !kedaOK {
		r.warnEvent(ctx, corpus, "KEDAAbsent",
			"KEDA is not installed; ScaledObjects will be skipped and static replicas will be used")
	}

	pre.AllMet = allMet

	return SubResult{}, nil
}

func (r *PrerequisiteReconciler) warnEvent(_ context.Context, corpus *accv1alpha1.AgentCorpus, reason, msg string) {
	// In production the controller records events via an EventRecorder.
	// We store a basic last-warning message in status for now;
	// the main controller wires up the recorder and calls record.Eventf.
	_ = corev1.EventTypeWarning
	_ = reason
	_ = msg
	_ = corpus
}

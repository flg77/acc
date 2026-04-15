// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

func TestComputeCorpusPhase_Ready(t *testing.T) {
	in := statuspkg.PhaseInput{
		InfrastructureReady: true,
		CollectivesReady:    true,
		PrerequisitesMet:    true,
		IsProgressing:       false,
	}
	got := statuspkg.ComputeCorpusPhase(in)
	if got != accv1alpha1.CorpusPhaseReady {
		t.Errorf("expected Ready, got %s", got)
	}
}

func TestComputeCorpusPhase_UpgradeApprovalPending(t *testing.T) {
	in := statuspkg.PhaseInput{
		InfrastructureReady:    true,
		CollectivesReady:       true,
		PrerequisitesMet:       true,
		UpgradeApprovalPending: true,
	}
	got := statuspkg.ComputeCorpusPhase(in)
	if got != accv1alpha1.CorpusPhaseUpgradeApprovalPending {
		t.Errorf("expected UpgradeApprovalPending, got %s", got)
	}
}

func TestComputeCorpusPhase_RHOAIError(t *testing.T) {
	in := statuspkg.PhaseInput{
		DeployModeRHOAI: true,
		RHOAIInstalled:  false,
	}
	got := statuspkg.ComputeCorpusPhase(in)
	if got != accv1alpha1.CorpusPhaseError {
		t.Errorf("expected Error (rhoai+no RHOAI), got %s", got)
	}
}

func TestComputeCorpusPhase_InfraNotReadyProgressing(t *testing.T) {
	in := statuspkg.PhaseInput{
		InfrastructureReady: false,
		IsProgressing:       true,
	}
	got := statuspkg.ComputeCorpusPhase(in)
	if got != accv1alpha1.CorpusPhaseProgressing {
		t.Errorf("expected Progressing (infra not ready + progressing), got %s", got)
	}
}

func TestComputeCorpusPhase_InfraNotReadyDegraded(t *testing.T) {
	in := statuspkg.PhaseInput{
		InfrastructureReady: false,
		IsProgressing:       false,
	}
	got := statuspkg.ComputeCorpusPhase(in)
	if got != accv1alpha1.CorpusPhaseDegraded {
		t.Errorf("expected Degraded (infra not ready), got %s", got)
	}
}

func TestComputeCorpusPhase_MissingPrereqs(t *testing.T) {
	in := statuspkg.PhaseInput{
		InfrastructureReady: true,
		CollectivesReady:    true,
		PrerequisitesMet:    false,
	}
	got := statuspkg.ComputeCorpusPhase(in)
	if got != accv1alpha1.CorpusPhaseDegraded {
		t.Errorf("expected Degraded (missing prereqs), got %s", got)
	}
}

func TestComputeCorpusPhase_CollectivesNotReadyProgressing(t *testing.T) {
	in := statuspkg.PhaseInput{
		InfrastructureReady: true,
		CollectivesReady:    false,
		PrerequisitesMet:    true,
		IsProgressing:       true,
	}
	got := statuspkg.ComputeCorpusPhase(in)
	if got != accv1alpha1.CorpusPhaseProgressing {
		t.Errorf("expected Progressing (collectives deploying), got %s", got)
	}
}

func TestComputeCollectivePhase_Ready(t *testing.T) {
	got := statuspkg.ComputeCollectivePhase(5, 5, false, true)
	if got != accv1alpha1.CollectivePhaseReady {
		t.Errorf("expected Ready, got %s", got)
	}
}

func TestComputeCollectivePhase_Progressing(t *testing.T) {
	got := statuspkg.ComputeCollectivePhase(3, 5, false, true)
	if got != accv1alpha1.CollectivePhaseProgressing {
		t.Errorf("expected Progressing (3/5 ready), got %s", got)
	}
}

func TestComputeCollectivePhase_KServeDegraded(t *testing.T) {
	got := statuspkg.ComputeCollectivePhase(5, 5, true, false)
	if got != accv1alpha1.CollectivePhaseDegraded {
		t.Errorf("expected Degraded (kserve required but not ready), got %s", got)
	}
}

func TestComputeCollectivePhase_ZeroDesiredPending(t *testing.T) {
	got := statuspkg.ComputeCollectivePhase(0, 0, false, false)
	if got != accv1alpha1.CollectivePhasePending {
		t.Errorf("expected Pending (zero desired), got %s", got)
	}
}

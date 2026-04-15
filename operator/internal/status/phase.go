// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package status

import (
	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// PhaseInput collects the boolean inputs that drive the phase computation.
// Fields map directly to conditions on the AgentCorpus status.
type PhaseInput struct {
	// InfrastructureReady is true when NATS and Redis (and Milvus in rhoai mode)
	// are healthy.
	InfrastructureReady bool

	// CollectivesReady is true when every referenced AgentCollective is in
	// CollectivePhaseReady.
	CollectivesReady bool

	// PrerequisitesMet is true when all optional prerequisites that were
	// detected as required are reachable (Kafka if spec.kafka != nil, etc.).
	PrerequisitesMet bool

	// UpgradeApprovalPending is true when a version change is waiting for the
	// user to apply the acc.redhat.io/approve-upgrade annotation.
	UpgradeApprovalPending bool

	// DeployModeRHOAI is true when spec.deployMode == rhoai.
	DeployModeRHOAI bool

	// RHOAIInstalled is true when the RHOAI API group was detected.
	RHOAIInstalled bool

	// IsProgressing is true when at least one sub-reconciler is still
	// applying changes (e.g. StatefulSets not yet ready).
	IsProgressing bool
}

// ComputeCorpusPhase derives the top-level CorpusPhase from the set of
// boolean conditions produced by the reconciliation loop.
//
// Phase rules (evaluated in priority order):
//
//  1. UpgradeApprovalPending → UpgradeApprovalPending
//  2. rhoai mode AND RHOAI not installed → Error
//  3. Not InfrastructureReady → Progressing (if actively working) else Degraded
//  4. Not CollectivesReady  → Progressing else Degraded
//  5. Not PrerequisitesMet  → Degraded (missing optional prereqs don't block)
//  6. IsProgressing         → Progressing
//  7. All good              → Ready
func ComputeCorpusPhase(in PhaseInput) accv1alpha1.CorpusPhase {
	if in.UpgradeApprovalPending {
		return accv1alpha1.CorpusPhaseUpgradeApprovalPending
	}
	if in.DeployModeRHOAI && !in.RHOAIInstalled {
		return accv1alpha1.CorpusPhaseError
	}
	if !in.InfrastructureReady {
		if in.IsProgressing {
			return accv1alpha1.CorpusPhaseProgressing
		}
		return accv1alpha1.CorpusPhaseDegraded
	}
	if !in.CollectivesReady {
		if in.IsProgressing {
			return accv1alpha1.CorpusPhaseProgressing
		}
		return accv1alpha1.CorpusPhaseDegraded
	}
	if !in.PrerequisitesMet {
		return accv1alpha1.CorpusPhaseDegraded
	}
	if in.IsProgressing {
		return accv1alpha1.CorpusPhaseProgressing
	}
	return accv1alpha1.CorpusPhaseReady
}

// ComputeCollectivePhase derives the CollectivePhase for a single collective.
func ComputeCollectivePhase(ready, desired int32, kserveRequired, kserveReady bool) accv1alpha1.CollectivePhase {
	if desired == 0 {
		return accv1alpha1.CollectivePhasePending
	}
	if kserveRequired && !kserveReady {
		return accv1alpha1.CollectivePhaseDegraded
	}
	if ready < desired {
		return accv1alpha1.CollectivePhaseProgressing
	}
	return accv1alpha1.CollectivePhaseReady
}

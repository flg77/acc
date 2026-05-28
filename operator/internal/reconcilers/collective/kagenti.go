// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Kagenti AgentCard auto-discovery — Phase 1 (label-only).
//
// OpenSpec: 20260527-agentcard-discovery.
//
// When AgentCollectiveSpec.Kagenti.Enabled is true, the agent Deployment
// reconciler stamps the label `kagenti.io/type: agent` on the Deployment's
// ObjectMeta + pod-template labels.  Kagenti's kagenti-operator watches for
// that label and auto-creates an AgentCard CR for the workload — ACC does NOT
// own its own AgentCard CRD, by design.
//
// Phase 1 is deliberately label-only; the AgentCard becomes *functional* once:
//   - the A2A adapter serves /.well-known/agent-card.json (OpenSpec
//     20260527-a2a-agent-interop), and
//   - identity convergence binds the card's targetRef and signs it via the
//     cluster SPIRE x5c chain.
// Until both land, leave the flag disabled — Kagenti will find the workload
// but cannot fetch a valid card.

package collective

import (
	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// KagentiEnabled reports whether the collective opts in to Kagenti's
// AgentCard auto-discovery.  Mirrors :func:`SpiffeEnabled`.
func KagentiEnabled(c *accv1alpha1.AgentCollective) bool {
	return c != nil && c.Spec.Kagenti != nil && c.Spec.Kagenti.Enabled
}

// AgentObjectLabels returns the labels to stamp on an agent Deployment's
// ObjectMeta + pod-template labels.  When Kagenti discovery is opted in this
// is the canonical agent label set merged with `kagenti.io/type: agent`;
// otherwise it returns the canonical set unchanged.
//
// Selector labels are intentionally NOT included here — they are immutable in
// Kubernetes and the Kagenti label must not enter the selector.  Callers pass
// the canonical (selector-safe) label set as ``agentLabels``.
func AgentObjectLabels(c *accv1alpha1.AgentCollective, agentLabels map[string]string) map[string]string {
	if !KagentiEnabled(c) {
		return agentLabels
	}
	return util.MergeLabels(agentLabels, util.KagentiAgentLabel())
}

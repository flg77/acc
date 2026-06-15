// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Regression test for proposal 032 Finding A: the agent-Deployment name must be
// derived through the single shared helper so the creating reconciler and the
// status controller never drift (which produced readyAgents=0 for underscore
// roles like coding_agent on the AgentCollective CR).
package unit_test

import (
	"testing"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

func TestAgentDeploymentName_SanitizesUnderscores(t *testing.T) {
	cases := []struct {
		collective, role, want string
	}{
		{"acc-demo-coding-ws", "coding_agent", "acc-demo-coding-ws-coding-agent"},
		{"acc-demo-coding-ws", "coding_agent_reviewer", "acc-demo-coding-ws-coding-agent-reviewer"},
		{"acc-demo-finance-cm", "cm_market_data_ingester", "acc-demo-finance-cm-cm-market-data-ingester"},
		{"c", "ingester", "c-ingester"}, // no underscore — unchanged
	}
	for _, tc := range cases {
		if got := util.AgentDeploymentName(tc.collective, tc.role); got != tc.want {
			t.Errorf("AgentDeploymentName(%q, %q) = %q, want %q", tc.collective, tc.role, got, tc.want)
		}
	}
}

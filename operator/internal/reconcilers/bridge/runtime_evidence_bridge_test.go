// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package bridge

import (
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// TestSelectBackend covers the runtime-evidence backend auto-selection
// (proposal 015): RHACS > Falco > Tetragon, with an explicit override.
func TestSelectBackend(t *testing.T) {
	all := accv1alpha1.PrerequisiteStatus{
		RHACSInstalled: true, FalcoInstalled: true, TetragonInstalled: true,
	}
	cases := []struct {
		name      string
		pre       accv1alpha1.PrerequisiteStatus
		preferred string
		want      string
	}{
		{"auto prefers RHACS", all, "auto", backendRHACS},
		{"empty preference == auto", all, "", backendRHACS},
		{"auto falls to Falco", accv1alpha1.PrerequisiteStatus{
			FalcoInstalled: true, TetragonInstalled: true}, "auto", backendFalco},
		{"auto falls to Tetragon", accv1alpha1.PrerequisiteStatus{
			TetragonInstalled: true}, "auto", backendTetragon},
		{"none detected", accv1alpha1.PrerequisiteStatus{}, "auto", backendNone},
		{"explicit tetragon honoured", all, "tetragon", backendTetragon},
		{"explicit falco honoured", all, "falco", backendFalco},
		{"explicit preference not available falls back to auto",
			accv1alpha1.PrerequisiteStatus{RHACSInstalled: true}, "tetragon", backendRHACS},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := SelectBackend(tc.pre, tc.preferred)
			if got != tc.want {
				t.Errorf("SelectBackend(%+v, %q) = %q, want %q",
					tc.pre, tc.preferred, got, tc.want)
			}
		})
	}
}

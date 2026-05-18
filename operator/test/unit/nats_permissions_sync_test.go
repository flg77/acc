// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"os"
	"testing"
)

// TestPermissionMatrixInSync asserts the operator's embedded copy of
// the NATS NKey permission matrix is byte-identical to the canonical
// acc/nats_permissions.yaml (proposal 013).
//
// The operator cannot //go:embed a file outside its module, so it
// vendors a copy at internal/templates/nats_permissions.yaml.  This
// test turns any drift between the two into a red CI run rather than a
// silent Go/Python authorization mismatch.
func TestPermissionMatrixInSync(t *testing.T) {
	// Test runs with cwd = operator/test/unit.
	const (
		canonical = "../../../acc/nats_permissions.yaml"
		vendored  = "../../internal/templates/nats_permissions.yaml"
	)
	want, err := os.ReadFile(canonical)
	if err != nil {
		t.Fatalf("read canonical matrix %s: %v", canonical, err)
	}
	got, err := os.ReadFile(vendored)
	if err != nil {
		t.Fatalf("read vendored matrix %s: %v", vendored, err)
	}
	if string(want) != string(got) {
		t.Errorf("operator's vendored nats_permissions.yaml has drifted "+
			"from the canonical acc/nats_permissions.yaml — re-copy it:\n"+
			"  cp acc/nats_permissions.yaml "+
			"operator/internal/templates/nats_permissions.yaml")
	}
}

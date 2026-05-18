// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package unit_test

import (
	"strings"
	"testing"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/nkeygen"
)

// TestGenerateUserNKey checks the NKey wire format (proposal 013 PR-4).
func TestGenerateUserNKey(t *testing.T) {
	seed, public, err := nkeygen.GenerateUserNKey()
	if err != nil {
		t.Fatalf("GenerateUserNKey: %v", err)
	}
	if !strings.HasPrefix(seed, "SU") {
		t.Errorf("seed should start with SU (Seed+User), got %q", seed[:2])
	}
	if !strings.HasPrefix(public, "U") {
		t.Errorf("public should start with U, got %q", public[:1])
	}
}

// TestPublicFromSeed re-derives the public key from a seed — the
// reconciler relies on this to rebuild the authorization block from
// the persisted Secret without storing public keys separately.
func TestPublicFromSeed(t *testing.T) {
	seed, public, err := nkeygen.GenerateUserNKey()
	if err != nil {
		t.Fatalf("GenerateUserNKey: %v", err)
	}
	derived, err := nkeygen.PublicFromSeed(seed)
	if err != nil {
		t.Fatalf("PublicFromSeed: %v", err)
	}
	if derived != public {
		t.Errorf("PublicFromSeed mismatch: got %q want %q", derived, public)
	}
}

// TestKeysAreUnique guards against a broken RNG path.
func TestKeysAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 20; i++ {
		seed, _, err := nkeygen.GenerateUserNKey()
		if err != nil {
			t.Fatalf("GenerateUserNKey: %v", err)
		}
		if seen[seed] {
			t.Fatal("duplicate seed generated")
		}
		seen[seed] = true
	}
}

// TestPublicFromSeedRejectsGarbage ensures a corrupt seed fails loudly.
func TestPublicFromSeedRejectsGarbage(t *testing.T) {
	if _, err := nkeygen.PublicFromSeed("not-a-real-nkey-seed"); err == nil {
		t.Error("expected an error for a malformed seed")
	}
}

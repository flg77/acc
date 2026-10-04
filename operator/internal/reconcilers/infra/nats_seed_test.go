// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package infra

import (
	"reflect"
	"testing"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/templates"
)

// An NKey Secret minted before an identity was added (lifecycle_broker,
// 20261003) is topped up; nothing already in it is reported missing.
func TestMissingSeedKeys(t *testing.T) {
	data := map[string][]byte{}
	for _, id := range templates.NKeyIdentities() {
		if id != "lifecycle_broker" {
			data["seed-"+id] = []byte("S")
		}
	}
	if got := missingSeedKeys(data); !reflect.DeepEqual(got, []string{"lifecycle_broker"}) {
		t.Fatalf("missingSeedKeys = %v, want [lifecycle_broker]", got)
	}
	data["seed-lifecycle_broker"] = []byte("S")
	if got := missingSeedKeys(data); len(got) != 0 {
		t.Fatalf("complete Secret reported missing %v", got)
	}
	if got := missingSeedKeys(nil); len(got) != len(templates.NKeyIdentities()) {
		t.Fatalf("empty Secret: want every identity, got %v", got)
	}
}

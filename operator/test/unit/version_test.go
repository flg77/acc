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

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

func TestParseSemVer_Full(t *testing.T) {
	v, err := util.ParseSemVer("1.2.3")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if v.Major != 1 || v.Minor != 2 || v.Patch != 3 {
		t.Errorf("unexpected parse result: %+v", v)
	}
}

func TestParseSemVer_MajorMinorOnly(t *testing.T) {
	v, err := util.ParseSemVer("2.10")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if v.Major != 2 || v.Minor != 10 || v.Patch != 0 {
		t.Errorf("unexpected parse result: %+v", v)
	}
}

func TestParseSemVer_VPrefix(t *testing.T) {
	v, err := util.ParseSemVer("v0.1.0")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if v.Major != 0 || v.Minor != 1 || v.Patch != 0 {
		t.Errorf("unexpected parse result: %+v", v)
	}
}

func TestParseSemVer_Invalid(t *testing.T) {
	_, err := util.ParseSemVer("notaversion")
	if err == nil {
		t.Error("expected error for invalid version")
	}
}

func TestSemVer_Less(t *testing.T) {
	old := util.SemVer{Major: 2, Minor: 9, Patch: 0}
	new_ := util.SemVer{Major: 2, Minor: 10, Patch: 0}
	if !old.Less(new_) {
		t.Error("expected 2.9.0 < 2.10.0")
	}
	if new_.Less(old) {
		t.Error("expected 2.10.0 not < 2.9.0")
	}
}

func TestSemVer_Equal(t *testing.T) {
	a := util.SemVer{Major: 1, Minor: 2, Patch: 3}
	b := util.SemVer{Major: 1, Minor: 2, Patch: 3}
	if !a.Equal(b) {
		t.Error("expected equal versions")
	}
}

func TestInfraVersionChanged(t *testing.T) {
	tests := []struct {
		oldNATS, newNATS, oldRedis, newRedis string
		want                                 bool
	}{
		{"2.10", "2.10", "6", "6", false},
		{"2.10", "2.11", "6", "6", true},
		{"2.10", "2.10", "6", "7", true},
		{"2.10", "2.11", "6", "7", true},
	}
	for _, tt := range tests {
		got := util.InfraVersionChanged(tt.oldNATS, tt.newNATS, tt.oldRedis, tt.newRedis)
		if got != tt.want {
			t.Errorf("InfraVersionChanged(%q,%q,%q,%q) = %v, want %v",
				tt.oldNATS, tt.newNATS, tt.oldRedis, tt.newRedis, got, tt.want)
		}
	}
}

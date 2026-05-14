// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for filewatch.ParseRoleFile + RoleDefinitionsEqual.
// Proposal 010 PR-2.
package unit_test

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/filewatch"
)

func writeRoleFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
}

func TestParseRoleFile_Minimal(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "role.yaml")
	writeRoleFile(t, path, `role_definition:
  purpose: "Generate code"
  persona: analytical
  task_types:
    - CODE_GENERATE
    - CODE_REVIEW
  version: "1.0.0"
`)
	got, err := filewatch.ParseRoleFile(path)
	if err != nil {
		t.Fatalf("ParseRoleFile: %v", err)
	}
	if got == nil {
		t.Fatal("expected non-nil RoleDefinition")
	}
	if got.Purpose != "Generate code" {
		t.Errorf("Purpose: got %q", got.Purpose)
	}
	if got.Persona != "analytical" {
		t.Errorf("Persona: got %q", got.Persona)
	}
	if len(got.TaskTypes) != 2 || got.TaskTypes[0] != "CODE_GENERATE" {
		t.Errorf("TaskTypes: got %v", got.TaskTypes)
	}
	if got.Version != "1.0.0" {
		t.Errorf("Version: got %q", got.Version)
	}
}

func TestParseRoleFile_WithCategoryB(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "role.yaml")
	writeRoleFile(t, path, `role_definition:
  purpose: "test"
  persona: concise
  category_b_overrides:
    token_budget: "2048"
    rate_limit_rpm: "60"
`)
	got, err := filewatch.ParseRoleFile(path)
	if err != nil {
		t.Fatalf("ParseRoleFile: %v", err)
	}
	if got.CategoryBOverrides["token_budget"] != "2048" {
		t.Errorf("token_budget: got %q", got.CategoryBOverrides["token_budget"])
	}
}

func TestParseRoleFile_IgnoresOtherTopLevelKeys(t *testing.T) {
	// role.yaml files often carry id / display_name / tags alongside
	// role_definition.  Parser must ignore them silently.
	dir := t.TempDir()
	path := filepath.Join(dir, "role.yaml")
	writeRoleFile(t, path, `id: coding_agent
display_name: "Coding Agent"
tags:
  - dev
role_definition:
  purpose: "p"
  persona: concise
`)
	got, err := filewatch.ParseRoleFile(path)
	if err != nil {
		t.Fatalf("ParseRoleFile: %v", err)
	}
	if got.Purpose != "p" {
		t.Errorf("Purpose: got %q", got.Purpose)
	}
}

func TestParseRoleFile_EmptyBlockReturnsNil(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "role.yaml")
	writeRoleFile(t, path, `role_definition: {}
`)
	got, err := filewatch.ParseRoleFile(path)
	if err != nil {
		t.Fatalf("ParseRoleFile: %v", err)
	}
	if got != nil {
		t.Errorf("expected nil for empty role_definition, got %+v", got)
	}
}

func TestParseRoleFile_NoRoleDefinitionBlockReturnsNil(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "role.yaml")
	writeRoleFile(t, path, `id: coding_agent
display_name: "x"
`)
	got, err := filewatch.ParseRoleFile(path)
	if err != nil {
		t.Fatalf("ParseRoleFile: %v", err)
	}
	if got != nil {
		t.Errorf("expected nil when role_definition: is absent, got %+v", got)
	}
}

func TestParseRoleFile_InvalidPersonaRejected(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "role.yaml")
	writeRoleFile(t, path, `role_definition:
  persona: aggressive
`)
	_, err := filewatch.ParseRoleFile(path)
	if err == nil {
		t.Fatal("expected error for invalid persona, got nil")
	}
}

func TestParseRoleFile_MissingFile(t *testing.T) {
	_, err := filewatch.ParseRoleFile("/nonexistent/role.yaml")
	if err == nil {
		t.Fatal("expected error for missing file")
	}
	if !errors.Is(err, os.ErrNotExist) {
		t.Errorf("expected os.ErrNotExist in chain, got %v", err)
	}
}

func TestParseRoleFile_EmptyPath(t *testing.T) {
	_, err := filewatch.ParseRoleFile("")
	if err == nil {
		t.Fatal("expected error for empty path")
	}
}

func TestParseRoleFile_MalformedYAML(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "role.yaml")
	writeRoleFile(t, path, `role_definition:
  purpose: "unclosed`)
	_, err := filewatch.ParseRoleFile(path)
	if err == nil {
		t.Fatal("expected error for malformed yaml")
	}
}

func TestRoleDefinitionsEqual_BothNil(t *testing.T) {
	if !filewatch.RoleDefinitionsEqual(nil, nil) {
		t.Error("both nil should be equal")
	}
}

func TestRoleDefinitionsEqual_OneNil(t *testing.T) {
	a := &accv1alpha1.RoleDefinition{}
	if !filewatch.RoleDefinitionsEqual(nil, a) {
		t.Error("nil and empty should be equal")
	}
	if !filewatch.RoleDefinitionsEqual(a, nil) {
		t.Error("empty and nil should be equal (symmetric)")
	}
}

func TestRoleDefinitionsEqual_DifferentPurpose(t *testing.T) {
	a := &accv1alpha1.RoleDefinition{Purpose: "x"}
	b := &accv1alpha1.RoleDefinition{Purpose: "y"}
	if filewatch.RoleDefinitionsEqual(a, b) {
		t.Error("different purposes should not be equal")
	}
}

func TestRoleDefinitionsEqual_SameContent(t *testing.T) {
	a := &accv1alpha1.RoleDefinition{
		Purpose:   "x",
		Persona:   "concise",
		TaskTypes: []string{"A", "B"},
	}
	b := &accv1alpha1.RoleDefinition{
		Purpose:   "x",
		Persona:   "concise",
		TaskTypes: []string{"A", "B"},
	}
	if !filewatch.RoleDefinitionsEqual(a, b) {
		t.Error("identical content should be equal")
	}
}

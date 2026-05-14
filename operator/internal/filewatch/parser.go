// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package filewatch

import (
	"errors"
	"fmt"
	"os"
	"reflect"

	"gopkg.in/yaml.v3"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// onDiskRoleDefinition mirrors the Python-side acc.config.RoleDefinitionConfig
// shape and the file format under roles/<id>/role.yaml.
//
// Why a separate type instead of unmarshalling straight into
// accv1alpha1.RoleDefinition?  Two reasons:
//
//  1. JSON tags on the CRD type use camelCase (the Kubernetes convention)
//     while role.yaml files on disk use snake_case (the Python convention).
//  2. The on-disk shape is nested under a `role_definition:` key plus
//     other top-level keys we don't care about (id, display_name, …).
//
// Keeping this struct local prevents accidental drift between the CRD
// schema and the file format — translation is one place.
type onDiskRoleDefinition struct {
	Purpose            string            `yaml:"purpose,omitempty"`
	Persona            string            `yaml:"persona,omitempty"`
	TaskTypes          []string          `yaml:"task_types,omitempty"`
	SeedContext        string            `yaml:"seed_context,omitempty"`
	AllowedActions     []string          `yaml:"allowed_actions,omitempty"`
	CategoryBOverrides map[string]string `yaml:"category_b_overrides,omitempty"`
	Version            string            `yaml:"version,omitempty"`
}

// onDiskRoleFile is the full role.yaml shape; we extract role_definition
// and ignore the rest (id, display_name, tags, …) — those live outside
// the CRD's RoleDefinition projection.
type onDiskRoleFile struct {
	RoleDefinition onDiskRoleDefinition `yaml:"role_definition"`
}

// ParseRoleFile reads roles/<id>/role.yaml and returns its
// role_definition: block as an accv1alpha1.RoleDefinition.  Returns
// (nil, nil) when the file's role_definition block is absent or empty;
// callers should treat that as "no projection to apply" rather than an
// error.
//
// Validation:
//
//   - persona is normalised to lowercase and rejected if not one of the
//     four kubebuilder-allowed values (matches the enum on the CRD).
//   - CategoryBOverrides values are always serialised as strings even
//     though the Python side accepts numerics, because the CRD field is
//     map[string]string.  Callers must encode numerics themselves.
//
// Returns the parsed struct + a hint string describing what changed for
// log messages, or an error if the file is malformed.
func ParseRoleFile(path string) (*accv1alpha1.RoleDefinition, error) {
	if path == "" {
		return nil, errors.New("filewatch: empty role file path")
	}
	raw, err := os.ReadFile(path) //nolint:gosec // path comes from a watched roles-root
	if err != nil {
		return nil, fmt.Errorf("filewatch: read %q: %w", path, err)
	}

	var file onDiskRoleFile
	if err := yaml.Unmarshal(raw, &file); err != nil {
		return nil, fmt.Errorf("filewatch: parse %q: %w", path, err)
	}

	od := file.RoleDefinition
	if isEmpty(od) {
		// Empty role_definition block — treat as "nothing to project."
		return nil, nil
	}

	// Persona validation — match the kubebuilder enum on
	// accv1alpha1.RoleDefinition.Persona.
	if od.Persona != "" {
		switch od.Persona {
		case "concise", "formal", "exploratory", "analytical":
			// ok
		default:
			return nil, fmt.Errorf(
				"filewatch: parse %q: persona %q is not one of "+
					"concise|formal|exploratory|analytical",
				path, od.Persona,
			)
		}
	}

	rd := &accv1alpha1.RoleDefinition{
		Purpose:            od.Purpose,
		Persona:            od.Persona,
		TaskTypes:          od.TaskTypes,
		SeedContext:        od.SeedContext,
		AllowedActions:     od.AllowedActions,
		CategoryBOverrides: od.CategoryBOverrides,
		Version:            od.Version,
	}
	return rd, nil
}

// isEmpty returns true when every field of the on-disk struct is the
// zero value — used to short-circuit projection when role.yaml contains
// no role_definition: block.
func isEmpty(od onDiskRoleDefinition) bool {
	return reflect.DeepEqual(od, onDiskRoleDefinition{})
}

// RoleDefinitionsEqual returns true when two RoleDefinition values
// describe the same projection.  Used by the controller to avoid
// no-op CR patches that would otherwise generate spurious reconcile
// rounds.  nil values are treated as semantically empty.
func RoleDefinitionsEqual(a, b *accv1alpha1.RoleDefinition) bool {
	if a == nil && b == nil {
		return true
	}
	if a == nil {
		a = &accv1alpha1.RoleDefinition{}
	}
	if b == nil {
		b = &accv1alpha1.RoleDefinition{}
	}
	return reflect.DeepEqual(a, b)
}

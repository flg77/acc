// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

//go:build ignore

// Command gen-catalogue regenerates
// operator/internal/rolecatalogue/known_roles.txt from the source
// repository's roles/ directory.
//
// Run from the operator/ subdirectory:
//
//	go run ./hack/gen-catalogue.go
//
// Or via go generate from the catalogue package:
//
//	go generate ./internal/rolecatalogue/...
//
// The generator scans <repo-root>/roles/, treats every immediate
// subdirectory that contains a role.yaml as a known role, and skips
// reserved names (_base, TEMPLATE). Output lines are alphabetised so the
// resulting diff is stable when roles are added or removed.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// outRel is where the generator writes the catalogue, relative to the
// operator/ directory.
const outRel = "internal/rolecatalogue/known_roles.txt"

var skipDirs = map[string]struct{}{
	"_base":    {},
	"TEMPLATE": {},
}

func main() {
	cwd, err := os.Getwd()
	if err != nil {
		fail(err)
	}
	operatorDir, err := findOperatorRoot(cwd)
	if err != nil {
		fail(err)
	}
	repoRoot := filepath.Dir(operatorDir)
	rolesDir := filepath.Join(repoRoot, "roles")
	outPath := filepath.Join(operatorDir, outRel)

	entries, err := os.ReadDir(rolesDir)
	if err != nil {
		fail(fmt.Errorf("read %s: %w", rolesDir, err))
	}

	var roles []string
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		name := e.Name()
		if _, skip := skipDirs[name]; skip {
			continue
		}
		roleFile := filepath.Join(rolesDir, name, "role.yaml")
		if _, err := os.Stat(roleFile); err != nil {
			continue
		}
		roles = append(roles, name)
	}
	sort.Strings(roles)

	var b strings.Builder
	b.WriteString("# Generated from roles/ — do not edit by hand.\n")
	b.WriteString("# Regenerate via: go run ./hack/gen-catalogue.go\n")
	b.WriteString("# (or:  go generate ./internal/rolecatalogue/...)\n")
	b.WriteString("#\n")
	b.WriteString("# Source of truth: roles/<name>/role.yaml on the main branch.\n")
	b.WriteString("# One role name per line, alphabetical.\n")
	b.WriteString("\n")
	for _, r := range roles {
		b.WriteString(r)
		b.WriteString("\n")
	}

	if err := os.WriteFile(outPath, []byte(b.String()), 0o644); err != nil {
		fail(fmt.Errorf("write %s: %w", outPath, err))
	}

	fmt.Fprintf(os.Stderr, "wrote %d roles to %s\n", len(roles), outPath)
}

// findOperatorRoot walks up from start until it finds a directory whose
// basename is "operator" and which contains a Makefile + an internal/
// subdirectory. This makes the generator portable across invocation sites:
// it works whether run from operator/ (`go run ./hack/gen-catalogue.go`)
// or from operator/internal/rolecatalogue/ (`go generate`).
func findOperatorRoot(start string) (string, error) {
	dir := start
	for {
		if filepath.Base(dir) == "operator" {
			if isFile(filepath.Join(dir, "Makefile")) && isDir(filepath.Join(dir, "internal")) {
				return dir, nil
			}
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("could not locate operator/ root above %s", start)
		}
		dir = parent
	}
}

func isFile(p string) bool {
	info, err := os.Stat(p)
	return err == nil && !info.IsDir()
}

func isDir(p string) bool {
	info, err := os.Stat(p)
	return err == nil && info.IsDir()
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "gen-catalogue:", err)
	os.Exit(1)
}

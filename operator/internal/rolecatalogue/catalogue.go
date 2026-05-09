// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package rolecatalogue exposes the operator's compile-time list of valid
// agent roles. The catalogue is used by the AgentCollective validating
// webhook to give a clear "did you mean…" error when a CR uses a role name
// that is syntactically valid (passes the AgentRole regex) but doesn't
// correspond to any persona shipped under roles/.
//
// The catalogue is sourced from known_roles.txt, generated from the live
// roles/ directory by:
//
//	go run ./hack/gen-catalogue.go
//
// (also reachable via `go generate ./internal/rolecatalogue/...`). Adding a
// new persona under roles/<name>/role.yaml + re-running the generator is
// all that's needed to widen the catalogue — no Go-side edits.
package rolecatalogue

import (
	_ "embed"
	"sort"
	"strings"
)

//go:generate go run ../../hack/gen-catalogue.go

//go:embed known_roles.txt
var knownRolesData string

var knownRoles = parse(knownRolesData)

// parse turns the embedded text file into a set. Lines starting with '#' and
// blank lines are ignored; whitespace is trimmed.
func parse(data string) map[string]struct{} {
	out := make(map[string]struct{})
	for _, line := range strings.Split(data, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		out[line] = struct{}{}
	}
	return out
}

// IsKnown reports whether role appears in the compile-time catalogue.
func IsKnown(role string) bool {
	_, ok := knownRoles[role]
	return ok
}

// All returns the catalogue as a sorted slice. The result is freshly
// allocated so callers may mutate it.
func All() []string {
	out := make([]string, 0, len(knownRoles))
	for r := range knownRoles {
		out = append(out, r)
	}
	sort.Strings(out)
	return out
}

// Suggest returns up to n catalogue entries closest to role by Levenshtein
// distance, in increasing distance order. Ties broken alphabetically. The
// result excludes any entry with distance > 8 to keep the suggestions
// useful — beyond that the user almost certainly typed something
// unrelated.
func Suggest(role string, n int) []string {
	if n <= 0 {
		return nil
	}
	type scored struct {
		name string
		dist int
	}
	candidates := make([]scored, 0, len(knownRoles))
	for r := range knownRoles {
		d := levenshtein(role, r)
		if d > 8 {
			continue
		}
		candidates = append(candidates, scored{name: r, dist: d})
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].dist != candidates[j].dist {
			return candidates[i].dist < candidates[j].dist
		}
		return candidates[i].name < candidates[j].name
	})
	if len(candidates) > n {
		candidates = candidates[:n]
	}
	out := make([]string, 0, len(candidates))
	for _, c := range candidates {
		out = append(out, c.name)
	}
	return out
}

// levenshtein computes edit distance between a and b. Two-row dynamic
// programming — O(len(a)*len(b)) time, O(min) space.
func levenshtein(a, b string) int {
	ar := []rune(a)
	br := []rune(b)
	if len(ar) < len(br) {
		ar, br = br, ar
	}
	if len(br) == 0 {
		return len(ar)
	}
	prev := make([]int, len(br)+1)
	curr := make([]int, len(br)+1)
	for j := range prev {
		prev[j] = j
	}
	for i := 1; i <= len(ar); i++ {
		curr[0] = i
		for j := 1; j <= len(br); j++ {
			cost := 1
			if ar[i-1] == br[j-1] {
				cost = 0
			}
			curr[j] = min3(prev[j]+1, curr[j-1]+1, prev[j-1]+cost)
		}
		prev, curr = curr, prev
	}
	return prev[len(br)]
}

func min3(a, b, c int) int {
	m := a
	if b < m {
		m = b
	}
	if c < m {
		m = c
	}
	return m
}

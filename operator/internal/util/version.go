// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package util

import (
	"fmt"
	"strconv"
	"strings"
)

// SemVer is a parsed semantic version.
type SemVer struct {
	Major int
	Minor int
	Patch int
}

// ParseSemVer parses a version string like "1.2.3" or "2.10".
// A missing patch component defaults to 0.
func ParseSemVer(v string) (SemVer, error) {
	v = strings.TrimPrefix(v, "v")
	parts := strings.SplitN(v, ".", 3)
	if len(parts) < 2 {
		return SemVer{}, fmt.Errorf("invalid semver %q: need at least major.minor", v)
	}

	major, err := strconv.Atoi(parts[0])
	if err != nil {
		return SemVer{}, fmt.Errorf("invalid semver %q: bad major: %w", v, err)
	}
	minor, err := strconv.Atoi(parts[1])
	if err != nil {
		return SemVer{}, fmt.Errorf("invalid semver %q: bad minor: %w", v, err)
	}

	patch := 0
	if len(parts) == 3 {
		patch, err = strconv.Atoi(parts[2])
		if err != nil {
			return SemVer{}, fmt.Errorf("invalid semver %q: bad patch: %w", v, err)
		}
	}

	return SemVer{Major: major, Minor: minor, Patch: patch}, nil
}

// String returns the canonical "major.minor.patch" form.
func (s SemVer) String() string {
	return fmt.Sprintf("%d.%d.%d", s.Major, s.Minor, s.Patch)
}

// Less returns true when s is strictly less than other.
func (s SemVer) Less(other SemVer) bool {
	if s.Major != other.Major {
		return s.Major < other.Major
	}
	if s.Minor != other.Minor {
		return s.Minor < other.Minor
	}
	return s.Patch < other.Patch
}

// Equal returns true when s equals other.
func (s SemVer) Equal(other SemVer) bool {
	return s.Major == other.Major && s.Minor == other.Minor && s.Patch == other.Patch
}

// InfraVersionChanged returns true when the NATS or Redis version strings
// differ between old and new, indicating an infra-level upgrade that may
// require the requireApproval gate.
//
// It does a simple string comparison on the raw version field values;
// callers that need SemVer ordering should use ParseSemVer directly.
func InfraVersionChanged(oldNATS, newNATS, oldRedis, newRedis string) bool {
	return oldNATS != newNATS || oldRedis != newRedis
}

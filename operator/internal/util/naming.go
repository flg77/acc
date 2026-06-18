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
	"strings"
)

// AgentDeploymentName returns the Deployment name for an agent role within a
// collective. Role names may contain underscores (e.g. "coding_agent") which
// are invalid in RFC-1123 object names, so they are sanitized to hyphens.
//
// This is the SINGLE source of truth for the name. Both the AgentDeployment
// reconciler (which creates the Deployment) and the AgentCollective status
// controller (which reads its readyReplicas) MUST resolve it through here — if
// they format the name independently they drift on any underscore role, the
// status controller's Get misses, and it silently reports readyAgents=0 for a
// healthy agent (proposal 032 Finding A).
func AgentDeploymentName(collectiveName, role string) string {
	return fmt.Sprintf("%s-%s", collectiveName, strings.ReplaceAll(role, "_", "-"))
}

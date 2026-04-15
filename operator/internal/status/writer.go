// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package status

import (
	"context"
	"fmt"

	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// PatchCorpusStatus patches only the status subresource of an AgentCorpus.
// It uses a merge patch from the original (before mutations) to avoid
// overwriting concurrent updates to the spec.
func PatchCorpusStatus(ctx context.Context, c client.Client, corpus *accv1alpha1.AgentCorpus, original *accv1alpha1.AgentCorpus) error {
	patch := client.MergeFrom(original)
	if err := c.Status().Patch(ctx, corpus, patch); err != nil {
		return fmt.Errorf("patch AgentCorpus %s/%s status: %w", corpus.Namespace, corpus.Name, err)
	}
	return nil
}

// PatchCollectiveStatus patches only the status subresource of an AgentCollective.
func PatchCollectiveStatus(ctx context.Context, c client.Client, coll *accv1alpha1.AgentCollective, original *accv1alpha1.AgentCollective) error {
	patch := client.MergeFrom(original)
	if err := c.Status().Patch(ctx, coll, patch); err != nil {
		return fmt.Errorf("patch AgentCollective %s/%s status: %w", coll.Namespace, coll.Name, err)
	}
	return nil
}

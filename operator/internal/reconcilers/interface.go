// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package reconcilers defines the sub-reconciler interface and the shared
// result type used by all sub-reconcilers in the ACC operator.
//
// Each component that the operator manages (NATS, Redis, governance, etc.)
// is implemented as a SubReconciler. The main AgentCorpusReconciler calls
// them in a fixed order; each one is responsible for a single concern.
package reconcilers

import (
	"context"

	ctrl "sigs.k8s.io/controller-runtime"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// SubResult is returned by every SubReconciler.Reconcile call.
type SubResult struct {
	// Requeue requests an immediate re-queue of the parent reconcile request.
	// Use this when the sub-reconciler created resources that need to be
	// checked again on the next cycle without waiting for an event.
	Requeue bool

	// RequeueAfter sets a minimum wait before the next reconcile cycle.
	// Overridden by Requeue=true.
	RequeueAfter ctrl.Result

	// Progressing is true when the sub-reconciler has made changes and the
	// managed resources are not yet in their desired state (e.g. a StatefulSet
	// is rolling). The phase computation uses this to set Progressing phase.
	Progressing bool
}

// SubReconciler is the interface every ACC sub-reconciler must satisfy.
// The parent reconciler drives them in a fixed sequence; each sub-reconciler
// reads only what it needs from corpus and writes back into corpus.Status.
type SubReconciler interface {
	// Reconcile brings a single subsystem into the desired state declared by
	// corpus.Spec, updating corpus.Status fields in-place.
	// It must NOT patch the status subresource — that is the parent's job.
	Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (SubResult, error)

	// Name returns a short human-readable identifier for logging.
	Name() string
}

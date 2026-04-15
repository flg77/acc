// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package util

import (
	"context"
	"fmt"

	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// UpsertResult describes what CreateOrUpdate did.
type UpsertResult int

const (
	UpsertResultNoop    UpsertResult = iota // resource already exists and is unchanged
	UpsertResultCreated                     // resource was newly created
	UpsertResultUpdated                     // resource was patched
)

// MutateFunc is called with the existing object before it is updated.
// The function should apply the desired state onto the existing object.
// On create, desired is passed directly.
type MutateFunc func(existing client.Object) error

// Upsert creates or updates a cluster resource, setting owner to owner.
// desired is the fully-specified wanted state; mutateFn is called with the
// live object (on update) so the caller can apply only the fields it owns.
//
// The object key is taken from desired.GetNamespace() / desired.GetName().
func Upsert(
	ctx context.Context,
	c client.Client,
	scheme *runtime.Scheme,
	owner client.Object,
	desired client.Object,
	mutateFn MutateFunc,
) (UpsertResult, error) {
	key := types.NamespacedName{
		Namespace: desired.GetNamespace(),
		Name:      desired.GetName(),
	}

	existing := desired.DeepCopyObject().(client.Object)
	err := c.Get(ctx, key, existing)
	if errors.IsNotFound(err) {
		// Set controller reference so the resource is garbage-collected
		// when the owner is deleted.
		if owner != nil {
			if err2 := ctrl.SetControllerReference(owner, desired, scheme); err2 != nil {
				return UpsertResultNoop, fmt.Errorf("SetControllerReference: %w", err2)
			}
		}
		if err2 := c.Create(ctx, desired); err2 != nil {
			return UpsertResultNoop, fmt.Errorf("Create %T %s: %w", desired, key, err2)
		}
		return UpsertResultCreated, nil
	}
	if err != nil {
		return UpsertResultNoop, fmt.Errorf("Get %T %s: %w", existing, key, err)
	}

	// Resource exists — apply mutations, then patch.
	patch := client.MergeFrom(existing.DeepCopyObject().(client.Object))
	if err := mutateFn(existing); err != nil {
		return UpsertResultNoop, fmt.Errorf("mutateFn: %w", err)
	}
	if err := c.Patch(ctx, existing, patch); err != nil {
		return UpsertResultNoop, fmt.Errorf("Patch %T %s: %w", existing, key, err)
	}
	return UpsertResultUpdated, nil
}

// ClusterUpsert is like Upsert but for cluster-scoped resources (no namespace,
// no owner reference — cluster-scoped objects cannot be owned by namespaced ones).
func ClusterUpsert(
	ctx context.Context,
	c client.Client,
	desired client.Object,
	mutateFn MutateFunc,
) (UpsertResult, error) {
	key := types.NamespacedName{Name: desired.GetName()}

	existing := desired.DeepCopyObject().(client.Object)
	err := c.Get(ctx, key, existing)
	if errors.IsNotFound(err) {
		if err2 := c.Create(ctx, desired); err2 != nil {
			return UpsertResultNoop, fmt.Errorf("Create %T %s: %w", desired, key, err2)
		}
		return UpsertResultCreated, nil
	}
	if err != nil {
		return UpsertResultNoop, fmt.Errorf("Get %T %s: %w", existing, key, err)
	}

	patch := client.MergeFrom(existing.DeepCopyObject().(client.Object))
	if err := mutateFn(existing); err != nil {
		return UpsertResultNoop, fmt.Errorf("mutateFn: %w", err)
	}
	if err := c.Patch(ctx, existing, patch); err != nil {
		return UpsertResultNoop, fmt.Errorf("Patch %T %s: %w", existing, key, err)
	}
	return UpsertResultUpdated, nil
}

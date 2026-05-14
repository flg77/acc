// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package controller

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/filewatch"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

var collectiveLog = logf.Log.WithName("agentcollective-controller")

// RoleSyncSourceFiles / Mirror / CRD mirror the values of acc-config.yaml's
// role_sync.role_source field (proposal 010).  The operator binary
// resolves "auto" to a concrete value at startup; this controller only
// sees the resolved string.
const (
	RoleSyncSourceFiles  = "files"
	RoleSyncSourceMirror = "mirror"
	RoleSyncSourceCRD    = "crd"

	// roleSyncSourceAnnotation tags CR patches that originated from a
	// file-watcher write, so observers can attribute the change.
	roleSyncSourceAnnotation = "acc.io/role-sync-source"
)

// AgentCollectiveReconciler reconciles an AgentCollective object.
//
// Two responsibilities:
//
//  1. Aggregate ready/desired replica counts from owned Deployments into
//     the AgentCollective's status (the original responsibility).
//  2. When RoleSource is "files" or "mirror", project file-system role
//     definitions under RolesRoot into the matching AgentCollective's
//     Spec.RoleDefinition (proposal 010 PR-2).  Disabled in "crd" mode.
//
// The file-watcher integration is deliberately out-of-band — main.go
// spawns a goroutine that reads from filewatch.Watcher.Events() and
// invokes ProjectRoleFile on each event.  Keeping it out of
// SetupWithManager avoids coupling this controller to a specific
// controller-runtime source.Channel signature.
//
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcollectives,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcollectives/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcollectives/finalizers,verbs=update
type AgentCollectiveReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme

	// RoleSource controls whether file-driven CRD patches run.
	// Empty string defaults to "crd" — same behaviour as before PR-2.
	RoleSource string

	// RolesRoot is the absolute path to the directory containing
	// per-role subdirectories.  Required when RoleSource is "files"
	// or "mirror".  Ignored otherwise.
	RolesRoot string

	// Namespace is the K8s namespace where AgentCollective resources
	// matching role-file IDs are looked up.  Defaults to "default"
	// when empty.
	Namespace string
}

// FileWriteEnabled reports whether this reconciler should propagate
// file changes into CR patches.  Exposed for main.go to decide whether
// to spawn the file-watcher goroutine.
func (r *AgentCollectiveReconciler) FileWriteEnabled() bool {
	return r.RoleSource == RoleSyncSourceFiles || r.RoleSource == RoleSyncSourceMirror
}

// SetupWithManager registers this reconciler with the Manager.
func (r *AgentCollectiveReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&accv1alpha1.AgentCollective{}).
		Owns(&appsv1.Deployment{}).
		Complete(r)
}

// namespace returns the effective namespace; defaults to "default".
func (r *AgentCollectiveReconciler) namespace() string {
	if r.Namespace == "" {
		return "default"
	}
	return r.Namespace
}

// Reconcile aggregates Deployment status into AgentCollective.Status.
//
// File-driven RoleDefinition projection is handled out-of-band by
// ProjectRoleFile (called from main.go's file-watcher goroutine).
// We do not run projection inside Reconcile because Reconcile is
// invoked for every Deployment-status tick, and we don't want to
// re-read the role.yaml file 30+ times per minute.
func (r *AgentCollectiveReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := collectiveLog.WithValues("agentcollective", req.NamespacedName)

	collective := &accv1alpha1.AgentCollective{}
	if err := r.Client.Get(ctx, req.NamespacedName, collective); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	original := collective.DeepCopy()

	readyAgents := make(map[string]int32)
	desiredAgents := make(map[string]int32)

	for _, roleSpec := range collective.Spec.Agents {
		role := string(roleSpec.Role)
		deployName := fmt.Sprintf("%s-%s", collective.Name, role)

		deploy := &appsv1.Deployment{}
		if err := r.Client.Get(ctx, client.ObjectKey{
			Namespace: collective.Namespace,
			Name:      deployName,
		}, deploy); err != nil {
			if client.IgnoreNotFound(err) != nil {
				return ctrl.Result{}, err
			}
			desiredAgents[role] = roleSpec.Replicas
			readyAgents[role] = 0
			continue
		}

		desiredAgents[role] = roleSpec.Replicas
		readyAgents[role] = deploy.Status.ReadyReplicas
	}

	collective.Status.ReadyAgents = readyAgents
	collective.Status.DesiredAgents = desiredAgents
	collective.Status.ObservedGeneration = collective.Generation

	totalReady := int32(0)
	totalDesired := int32(0)
	for _, v := range readyAgents {
		totalReady += v
	}
	for _, v := range desiredAgents {
		totalDesired += v
	}
	collective.Status.Phase = statuspkg.ComputeCollectivePhase(totalReady, totalDesired, false, true)

	if err := statuspkg.PatchCollectiveStatus(ctx, r.Client, collective, original); err != nil {
		return ctrl.Result{}, err
	}

	log.V(1).Info("collective status updated",
		"phase", collective.Status.Phase,
		"ready", totalReady,
		"desired", totalDesired)

	return ctrl.Result{}, nil
}

// ProjectRoleFile reads <RolesRoot>/<roleID>/role.yaml and patches the
// matching AgentCollective's Spec.RoleDefinition if the on-disk value
// differs.  Called by main.go's file-watcher goroutine.
//
// Returns nil when:
//
//   - File-write is disabled (no-op for safety).
//   - The file doesn't exist (no projection requested for this role).
//   - The file's role_definition: block is empty.
//   - The matching AgentCollective resource doesn't exist.
//   - The parsed value already matches the spec.
//
// The patch carries an annotation tagging it as a file-mirror write so
// observers (and PR-4's conflict detector) can attribute the change.
func (r *AgentCollectiveReconciler) ProjectRoleFile(ctx context.Context, roleID string) error {
	if !r.FileWriteEnabled() {
		return nil
	}
	if r.RolesRoot == "" {
		return fmt.Errorf("RolesRoot is empty but RoleSource=%s", r.RoleSource)
	}

	rolePath := filepath.Join(r.RolesRoot, roleID, "role.yaml")
	parsed, err := filewatch.ParseRoleFile(rolePath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return fmt.Errorf("parse %s: %w", rolePath, err)
	}
	if parsed == nil {
		return nil
	}

	collective := &accv1alpha1.AgentCollective{}
	key := client.ObjectKey{Namespace: r.namespace(), Name: roleID}
	if err := r.Client.Get(ctx, key, collective); err != nil {
		if client.IgnoreNotFound(err) == nil {
			// CR doesn't exist for this role ID yet — nothing to patch.
			// PR-4's mirror mode may later create the CR; for now, log
			// + return.
			collectiveLog.V(1).Info(
				"file event for unknown AgentCollective",
				"name", roleID, "path", rolePath,
			)
			return nil
		}
		return fmt.Errorf("get AgentCollective %s/%s: %w", r.namespace(), roleID, err)
	}

	if filewatch.RoleDefinitionsEqual(parsed, collective.Spec.RoleDefinition) {
		return nil
	}

	patched := collective.DeepCopy()
	patched.Spec.RoleDefinition = parsed
	if patched.Annotations == nil {
		patched.Annotations = map[string]string{}
	}
	patched.Annotations[roleSyncSourceAnnotation] = fmt.Sprintf(
		"file-mirror@%s", time.Now().UTC().Format(time.RFC3339),
	)

	if err := r.Client.Patch(ctx, patched, client.MergeFrom(collective)); err != nil {
		return fmt.Errorf("patch RoleDefinition from file: %w", err)
	}
	collectiveLog.Info("role.yaml projected into spec",
		"name", collective.Name, "path", rolePath,
	)
	return nil
}

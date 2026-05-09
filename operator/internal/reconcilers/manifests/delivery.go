// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package manifests delivers the operator-baked roles/, skills/, and mcps/
// trees into agent pods as corpus-namespace ConfigMaps.
//
// The trees are mirrored into operator/internal/reconcilers/manifests/data/
// at build time by `make sync-manifests` and embedded via //go:embed. At
// runtime the reconciler emits three ConfigMaps per AgentCorpus —
// {corpus}-acc-roles, {corpus}-acc-skills, {corpus}-acc-mcps — whose Data
// keys are the original file paths with "/" replaced by "__" (Kubernetes
// rejects "/" in ConfigMap keys). The agent_deployment reconciler reverses
// the flatten via items[]: [{key: foo__bar.yaml, path: foo/bar.yaml}] in
// the Volume mount so the in-pod filesystem sees the original tree shape.
//
// The reconciler is opt-out: AgentCorpusSpec.ManifestDelivery defaults to
// "all"; setting it to "none" skips the upsert and the agent_deployment
// volume injection (for users who bake the trees into a custom agent
// image instead).
package manifests

import (
	"context"
	"embed"
	"fmt"
	"io/fs"
	"strings"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// PathSeparator is the character that replaces "/" in flattened ConfigMap
// keys. ConfigMap keys must match `[A-Za-z0-9_.-]+`, and we never use "__"
// as a substring in any source file or directory name (verified by the
// sync-manifests Makefile target — it strips __pycache__ during the
// mirror). Round-trip: original → flat by `strings.ReplaceAll(p, "/", "__")`,
// flat → original by `strings.ReplaceAll(k, "__", "/")`.
const PathSeparator = "__"

// ConfigMap name suffixes — agents see {corpus}-{suffix}.
const (
	rolesCMSuffix  = "acc-roles"
	skillsCMSuffix = "acc-skills"
	mcpsCMSuffix   = "acc-mcps"
)

// In-pod mount paths. agent_deployment reads these from the same constants
// when wiring volume mounts and the ACC_*_ROOT env vars.
const (
	RolesMountPath  = "/etc/acc/roles"
	SkillsMountPath = "/etc/acc/skills"
	MCPsMountPath   = "/etc/acc/mcps"
)

// Component name used in the operator-managed labels.
const componentName = "manifest-delivery"

// embedRoles, embedSkills, and embedMCPs are populated at build time by
// `make sync-manifests`. The mirrored data/ directory is gitignored — the
// source of truth is the repo-root roles/, skills/, mcps/ trees.
//
//go:embed all:data/roles
var embedRoles embed.FS

//go:embed all:data/skills
var embedSkills embed.FS

//go:embed all:data/mcps
var embedMCPs embed.FS

// ManifestDeliveryReconciler emits the three corpus-scoped ConfigMaps that
// hold the roles/, skills/, mcps/ trees. It runs early in the reconciler
// chain (slot 2 — after PrerequisiteReconciler, before UpgradeReconciler)
// because every collective's agent Deployment depends on the ConfigMaps.
type ManifestDeliveryReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *ManifestDeliveryReconciler) Name() string { return "manifests/delivery" }

// Reconcile implements SubReconciler.
func (r *ManifestDeliveryReconciler) Reconcile(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
) (reconcilers.SubResult, error) {
	if corpus.Spec.ManifestDelivery == "none" {
		// Explicit opt-out — leave any pre-existing CMs alone (a user may
		// be supplying their own) and do not mark Ready.
		corpus.Status.ManifestDeliveryReady = false
		return reconcilers.SubResult{}, nil
	}

	for _, plan := range []struct {
		suffix string
		fs     embed.FS
		root   string
	}{
		{rolesCMSuffix, embedRoles, "data/roles"},
		{skillsCMSuffix, embedSkills, "data/skills"},
		{mcpsCMSuffix, embedMCPs, "data/mcps"},
	} {
		if err := r.upsertConfigMap(ctx, corpus, plan.suffix, plan.fs, plan.root); err != nil {
			corpus.Status.ManifestDeliveryReady = false
			return reconcilers.SubResult{}, err
		}
	}

	corpus.Status.ManifestDeliveryReady = true
	return reconcilers.SubResult{}, nil
}

// upsertConfigMap walks one embedded tree and writes its files into a
// ConfigMap whose Data keys are the flattened paths. It does not project
// the items[] list itself — the agent_deployment reconciler computes that
// from ConfigMap.Data keys at mount time, keeping a single source of truth
// for the path mapping.
func (r *ManifestDeliveryReconciler) upsertConfigMap(
	ctx context.Context,
	corpus *accv1alpha1.AgentCorpus,
	suffix string,
	tree embed.FS,
	root string,
) error {
	data, err := walkTree(tree, root)
	if err != nil {
		return fmt.Errorf("%s: walk embedded tree: %w", suffix, err)
	}

	name := fmt.Sprintf("%s-%s", corpus.Name, suffix)
	labels := util.CommonLabels(corpus.Name, componentName, corpus.Spec.Version)
	labels["acc.redhat.io/manifest-tree"] = suffix

	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: corpus.Namespace,
			Labels:    labels,
		},
		Data: data,
	}

	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, cm, func(existing client.Object) error {
		live := existing.(*corev1.ConfigMap)
		live.Data = data
		live.Labels = labels
		return nil
	}); err != nil {
		return fmt.Errorf("%s: upsert ConfigMap %s: %w", suffix, name, err)
	}
	return nil
}

// walkTree reads every regular file under root in the embedded FS and
// returns a map of flat-key → file-content. The flat key is the relative
// path with "/" replaced by PathSeparator. Empty trees yield an empty
// (but non-nil) map so the resulting ConfigMap is still created.
func walkTree(tree embed.FS, root string) (map[string]string, error) {
	out := map[string]string{}
	err := fs.WalkDir(tree, root, func(path string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			return nil
		}
		body, err := tree.ReadFile(path)
		if err != nil {
			return fmt.Errorf("read %s: %w", path, err)
		}
		// path is e.g. "data/roles/coding_agent_implementer/role.yaml" —
		// trim the embedded root prefix so callers see paths relative to
		// the original tree root.
		rel := strings.TrimPrefix(path, root+"/")
		key := strings.ReplaceAll(rel, "/", PathSeparator)
		out[key] = string(body)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return out, nil
}

// FlattenPath converts a slash-separated path into a ConfigMap-key-safe
// string. Exposed so other packages (notably the agent_deployment volume
// projector) can compute matching keys without re-implementing the rule.
func FlattenPath(p string) string {
	return strings.ReplaceAll(p, "/", PathSeparator)
}

// UnflattenKey converts a flattened ConfigMap key back to its original
// slash-separated path. This is what the agent_deployment volume's items[]
// uses to project each Data entry to the right in-pod filesystem location.
func UnflattenKey(k string) string {
	return strings.ReplaceAll(k, PathSeparator, "/")
}

// ConfigMapName returns the corpus-scoped ConfigMap name for a given tree
// suffix (rolesCMSuffix / skillsCMSuffix / mcpsCMSuffix). Public so
// agent_deployment can reference the same names.
func ConfigMapName(corpus *accv1alpha1.AgentCorpus, suffix string) string {
	return fmt.Sprintf("%s-%s", corpus.Name, suffix)
}

// Suffixes returns the three CM suffixes in mount order. Agent_deployment
// iterates this when building the three Volumes.
func Suffixes() (rolesSuffix, skillsSuffix, mcpsSuffix string) {
	return rolesCMSuffix, skillsCMSuffix, mcpsCMSuffix
}

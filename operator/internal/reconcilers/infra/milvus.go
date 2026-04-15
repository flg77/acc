// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package infra

import (
	"context"
	"net/url"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

// MilvusReconciler probes connectivity to an external Milvus instance.
// The operator does NOT install Milvus; it only checks that the configured
// URI is reachable and updates corpus.Status.Infrastructure.MilvusConnected.
type MilvusReconciler struct{}

// Name implements SubReconciler.
func (r *MilvusReconciler) Name() string { return "infra/milvus" }

// Reconcile implements SubReconciler.
func (r *MilvusReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	// Milvus is only relevant in rhoai mode.
	if corpus.Spec.DeployMode != accv1alpha1.DeployModeRHOAI {
		corpus.Status.Infrastructure.MilvusConnected = false
		return reconcilers.SubResult{}, nil
	}

	if corpus.Spec.Infrastructure.Milvus == nil || corpus.Spec.Infrastructure.Milvus.URI == "" {
		corpus.Status.Infrastructure.MilvusConnected = false
		return reconcilers.SubResult{}, nil
	}

	addr := milvusAddr(corpus.Spec.Infrastructure.Milvus.URI)
	connected := util.TCPReachable(ctx, addr)
	corpus.Status.Infrastructure.MilvusConnected = connected

	return reconcilers.SubResult{}, nil
}

// milvusAddr extracts host:port from a Milvus URI like "http://milvus:19530"
// or "milvus:19530". Falls back to the raw string if it can't parse.
func milvusAddr(uri string) string {
	parsed, err := url.Parse(uri)
	if err == nil && parsed.Host != "" {
		return parsed.Host
	}
	return uri
}

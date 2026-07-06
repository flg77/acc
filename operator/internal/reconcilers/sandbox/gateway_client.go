// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package sandbox

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"

	corev1 "k8s.io/api/core/v1"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/clientcredentials"
)

// This is the OpenShell Phase-3 "Model 1" delivery seam (ACC Implementation
// proposal 051): a raw operator-emitted Sandbox CR is UNCAGED — only the
// gateway's create_sandbox path injects the supervisor — so the operator must
// call the gateway's OpenShell.CreateSandbox API (proto/openshell.proto) to run
// an agent kernel-enforced.
//
// The Phase-3a spike (on the live acc1 gateway) pinned the auth model: the
// operator authenticates via OIDC client-credentials (a Keycloak bearer token),
// NOT the mTLS client cert — that cert is the sandbox credential, and
// --enable-mtls-auth is a dev-only "local single-user gateway" flag. Every API
// call needs an Authorization: Bearer <token> header.
//
// This file lands the auth + config + client SEAM (an interface + the OIDC
// token source, both self-contained + unit-tested). The concrete gRPC
// implementation — dialing OpenShell.CreateSandbox with the token, from
// generated proto stubs — is the next slice (needs the proto codegen).

// GatewayConfig configures the operator's connection to an OpenShell gateway.
type GatewayConfig struct {
	// Endpoint is the gateway gRPC endpoint, e.g.
	// https://openshell.openshell.svc.cluster.local:8080.
	Endpoint string

	// OIDCTokenURL is the Keycloak token endpoint,
	// …/realms/<realm>/protocol/openid-connect/token.
	OIDCTokenURL string

	// OIDCClientID / OIDCClientSecret are the operator's client-credentials
	// client (the client id doubles as the token audience). The secret is
	// sourced from a K8s Secret, never from the spec.
	OIDCClientID     string
	OIDCClientSecret string

	// CACertPEM optionally verifies the gateway's server certificate (the lab
	// internal CA); empty uses the system roots.
	CACertPEM []byte
}

// TokenSource returns an OIDC client-credentials token source: it fetches,
// caches, and refreshes the Keycloak bearer token the gateway requires on
// every API call. This is the Model-1 operator auth (Phase-3a spike finding).
func (c GatewayConfig) TokenSource(ctx context.Context) oauth2.TokenSource {
	cc := clientcredentials.Config{
		ClientID:     c.OIDCClientID,
		ClientSecret: c.OIDCClientSecret,
		TokenURL:     c.OIDCTokenURL,
		AuthStyle:    oauth2.AuthStyleInParams,
	}
	return cc.TokenSource(ctx)
}

// serverTLSConfig builds the TLS config for verifying the gateway's server
// certificate: the lab internal CA when provided, else the system roots.
func (c GatewayConfig) serverTLSConfig() (*tls.Config, error) {
	cfg := &tls.Config{MinVersion: tls.VersionTLS12}
	if len(c.CACertPEM) == 0 {
		return cfg, nil
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(c.CACertPEM) {
		return nil, fmt.Errorf("gateway CA cert is not valid PEM")
	}
	cfg.RootCAs = pool
	return cfg, nil
}

// SandboxCreateRequest is the payload for the gateway create call — the outputs
// of BuildSandboxObject (the agent pod template) + BuildSandboxPolicyYAML (the
// Cat-A/B/C policy), keyed by the sandbox id.
type SandboxCreateRequest struct {
	Name        string
	Namespace   string
	PodTemplate corev1.PodTemplateSpec
	PolicyYAML  []byte
}

// GatewayClient is the operator's seam onto the OpenShell gateway. The concrete
// gRPC implementation (OpenShell.CreateSandbox with the OIDC bearer token) lands
// in the next slice once the proto stubs are generated; the interface lets
// reconcileSandboxWorkload depend on the seam now.
type GatewayClient interface {
	// CreateSandbox runs the agent AS an OpenShell kernel-enforced sandbox via
	// the gateway create API. An error is returned fail-closed (D3) — the
	// caller must NOT fall back to an un-caged workload.
	CreateSandbox(ctx context.Context, req SandboxCreateRequest) error
}

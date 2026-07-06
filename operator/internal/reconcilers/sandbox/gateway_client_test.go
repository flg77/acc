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
	"net/http"
	"net/http/httptest"
	"testing"
)

// The operator authenticates to the gateway via OIDC client-credentials
// (Phase-3a spike): TokenSource must fetch a bearer token from the Keycloak
// token endpoint with the client id/secret.
func TestGatewayConfig_TokenSource(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		if r.FormValue("grant_type") != "client_credentials" {
			http.Error(w, "bad grant_type", http.StatusBadRequest)
			return
		}
		if r.FormValue("client_id") != "openshell-operator" || r.FormValue("client_secret") != "s3cr3t" {
			http.Error(w, "bad credentials", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"access_token":"tok-abc","token_type":"Bearer","expires_in":3600}`))
	}))
	defer srv.Close()

	cfg := GatewayConfig{
		OIDCTokenURL:     srv.URL,
		OIDCClientID:     "openshell-operator",
		OIDCClientSecret: "s3cr3t",
	}
	tok, err := cfg.TokenSource(context.Background()).Token()
	if err != nil {
		t.Fatalf("Token(): %v", err)
	}
	if tok.AccessToken != "tok-abc" || tok.TokenType != "Bearer" {
		t.Errorf("token = %q/%q, want tok-abc/Bearer", tok.AccessToken, tok.TokenType)
	}
}

func TestGatewayConfig_ServerTLSConfig(t *testing.T) {
	// Empty CA → system roots (RootCAs nil), no error.
	cfg, err := (GatewayConfig{}).serverTLSConfig()
	if err != nil {
		t.Fatalf("empty CA: unexpected err %v", err)
	}
	if cfg.RootCAs != nil {
		t.Error("empty CA should leave RootCAs nil (system roots)")
	}
	// Garbage CA PEM → error (fail-closed on bad config).
	if _, err := (GatewayConfig{CACertPEM: []byte("not a cert")}).serverTLSConfig(); err == nil {
		t.Error("garbage CA PEM should error")
	}
}

// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package v1alpha1

// ReservedAgentEnvNames lists the environment variables the operator itself
// sets on every agent container (or owns through the rendered acc-config.yaml)
// and that spec.agents[].extraEnv therefore may not carry. The webhook refuses
// them at admission and the collective reconciler skips them, so a user value
// can never shadow the agent's identity, its corpus wiring, a credential the
// operator projected, or its telemetry target. Keep in sync with the env the
// agent StatefulSet template receives in
// internal/reconcilers/collective (BuildExtraEnv, ApplyNKeySeed,
// ApplySpiffeSidecar), internal/reconcilers/manifests (PodDelivery) and
// internal/reconcilers/sandbox (ApplyOpenShellSandbox); the unit tests assert
// the BuildExtraEnv half of that.
var ReservedAgentEnvNames = []string{
	// Container identity + config path (reconcileRoleDeployment).
	"ACC_AGENT_ROLE",
	"ACC_COLLECTIVE_ID",
	"ACC_CORPUS_NAME",
	"ACC_CONFIG_PATH",
	"ACC_POD_NAME",
	"ACC_POD_UID",
	// Manifest delivery roots (PodDelivery).
	"ACC_ROLES_ROOT",
	"ACC_SKILLS_ROOT",
	"ACC_MCPS_ROOT",
	// Credentials, backend endpoint, runtime evidence, OTLP (BuildExtraEnv).
	"ACC_ANTHROPIC_API_KEY",
	"ACC_VLLM_INFERENCE_URL",
	"ACC_KAFKA_SASL_USERNAME",
	"ACC_KAFKA_SASL_PASSWORD",
	"ACC_RUNTIME_EVIDENCE_ENABLED",
	"ACC_RUNTIME_ENFORCE",
	"OTEL_EXPORTER_OTLP_ENDPOINT",
	"OTEL_EXPORTER_OTLP_PROTOCOL",
	// NATS NKey seed (ApplyNKeySeed) + SPIFFE SVID paths (ApplySpiffeSidecar).
	"ACC_NKEY_ENABLED",
	"ACC_NKEY_ROLE",
	"ACC_NKEY_SEED_PATH",
	"ACC_SPIFFE_SVID_MOUNT_PATH",
	"ACC_SVID_X509_PATH",
	"ACC_SVID_JWT_PATH",
	// OpenShell sandbox delegation (ApplyOpenShellSandbox).
	"ACC_SANDBOX_NAME",
	"OPENSHELL_GATEWAY",
	"OPENSHELL_NO_BROWSER",
	"OPENSHELL_LOCAL_TLS_DIR",
	"OPENSHELL_OIDC_CLIENT_SECRET",
	"OPENSHELL_OIDC_CLIENT_ID",
	"OPENSHELL_OIDC_ISSUER",
	"OPENSHELL_OIDC_AUDIENCE",
	"XDG_CONFIG_HOME",
	"SSL_CERT_FILE",
	// Transport + mode wiring owned by the rendered acc-config.yaml. The
	// runtime's env overlay would otherwise let extraEnv repoint the agent's
	// signalling bus or working memory.
	"ACC_DEPLOY_MODE",
	"ACC_OPERATOR_MODE",
	"ACC_NATS_URL",
	"ACC_NATS_HUB_URL",
	"ACC_REDIS_URL",
	"ACC_REDIS_PASSWORD",
}

var reservedAgentEnv = func() map[string]struct{} {
	m := make(map[string]struct{}, len(ReservedAgentEnvNames))
	for _, n := range ReservedAgentEnvNames {
		m[n] = struct{}{}
	}
	return m
}()

// IsReservedAgentEnv reports whether name is one the operator owns on the
// agent container (see ReservedAgentEnvNames).
func IsReservedAgentEnv(name string) bool {
	_, ok := reservedAgentEnv[name]
	return ok
}

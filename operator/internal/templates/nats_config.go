// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package templates

import (
	"bytes"
	_ "embed"
	"fmt"
	"strings"
	"text/template"

	"gopkg.in/yaml.v3"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

// natsPermissionsYAML is the canonical NATS NKey permission matrix
// (proposal 013).  It is a byte-for-byte copy of acc/nats_permissions.yaml;
// TestPermissionMatrixInSync asserts the two never drift.  Embedding —
// rather than reaching across the module boundary — keeps the operator
// binary self-contained.
//
//go:embed nats_permissions.yaml
var natsPermissionsYAML []byte

// nkeyIdentities is the stable, ordered identity list (six agent roles
// + the operator surface + the edge leaf link).  Order fixes the
// rendered authorization block so golden tests are deterministic.
var nkeyIdentities = []string{
	"arbiter", "ingester", "analyst", "synthesizer",
	"coding_agent", "observer", "tui", "leaf",
}

// permMatrix mirrors the shape of nats_permissions.yaml.  The top-level
// `_worker_*` anchor keys are not mapped — yaml.v3 resolves the anchors
// before unmarshalling, so `roles:` already carries the expanded lists.
type permMatrix struct {
	Roles map[string]struct {
		Publish   []string `yaml:"publish"`
		Subscribe []string `yaml:"subscribe"`
	} `yaml:"roles"`
}

func loadPermMatrix() (permMatrix, error) {
	var m permMatrix
	if err := yaml.Unmarshal(natsPermissionsYAML, &m); err != nil {
		return m, fmt.Errorf("parse nats_permissions.yaml: %w", err)
	}
	if len(m.Roles) == 0 {
		return m, fmt.Errorf("nats_permissions.yaml: empty roles map")
	}
	return m, nil
}

// renderAuthorizationBlock builds the `authorization { users = [...] }`
// block from the embedded permission matrix and the supplied public
// keys.  Identities absent from publicKeys are skipped (so a partial
// key set still renders).  Returns "" when publicKeys is empty.
func renderAuthorizationBlock(publicKeys map[string]string) (string, error) {
	if len(publicKeys) == 0 {
		return "", nil
	}
	matrix, err := loadPermMatrix()
	if err != nil {
		return "", err
	}
	var b strings.Builder
	b.WriteString("authorization {\n  users = [\n")
	for _, identity := range nkeyIdentities {
		pub, ok := publicKeys[identity]
		if !ok {
			continue
		}
		perms, ok := matrix.Roles[identity]
		if !ok {
			continue
		}
		b.WriteString(fmt.Sprintf("    { # %s\n", identity))
		b.WriteString(fmt.Sprintf("      nkey: %s\n", pub))
		b.WriteString("      permissions: {\n")
		b.WriteString(fmt.Sprintf("        publish: { allow: [%s] }\n",
			quoteJoin(perms.Publish)))
		b.WriteString(fmt.Sprintf("        subscribe: { allow: [%s] }\n",
			quoteJoin(perms.Subscribe)))
		b.WriteString("      }\n    }\n")
	}
	b.WriteString("  ]\n}\n")
	return b.String(), nil
}

func quoteJoin(globs []string) string {
	quoted := make([]string, len(globs))
	for i, g := range globs {
		quoted[i] = fmt.Sprintf("%q", g)
	}
	return strings.Join(quoted, ", ")
}

var natsConfTmpl = template.Must(template.New("nats").Parse(`# NATS JetStream configuration managed by acc-operator
# Corpus: {{ .CorpusName }}

server_name: {{ .CorpusName }}-nats

jetstream {
  store_dir: /data/jetstream
  max_memory_store: 1GB
  max_file_store: {{ .StorageSize }}
}

http: 8222

{{ if gt .Replicas 1 -}}
cluster {
  name: {{ .CorpusName }}-cluster
  listen: 0.0.0.0:6222
  routes: [
  {{- range .Routes }}
    nats-route://{{ . }}:6222
  {{- end }}
  ]
}
{{- end }}
{{ if .HubUrl -}}
# Edge leaf node — connects to datacenter hub when network is available.
# Subjects under acc.bridge.> are forwarded to/from the hub.
# Local intra-collective subjects (acc.{collective_id}.>) stay local.
leafnodes {
  remotes: [
    {
      url: "{{ .HubUrl }}"
      # Forward bridge subjects to the hub; keep intra-collective traffic local.
      deny_imports: ["acc.*.heartbeat", "acc.*.register",
                     "acc.*.task.assign", "acc.*.task.complete",
                     "acc.*.role_update", "acc.*.role_approval", "acc.*.alert"]
    }
  ]
}
{{- end }}
{{ if .AuthBlock }}
# NATS NKey authentication (proposal 013) — per-role publish/subscribe
# permissions rendered from the canonical acc/nats_permissions.yaml.
{{ .AuthBlock }}{{- end }}
`))

type natsConfigData struct {
	CorpusName  string
	StorageSize string
	Replicas    int32
	Routes      []string
	// HubUrl is the NATS leaf node remote URL (deployMode=edge only).
	// When non-empty, a leafnodes block is rendered in the config.
	HubUrl string
	// AuthBlock is the rendered NKey authorization block (proposal
	// 013).  Empty when NKey auth is disabled.
	AuthBlock string
}

// RenderNATSConfig produces a nats.conf string from the corpus spec.
// For deployMode=edge with a hub URL configured, a leafnodes block is
// included so the local NATS server connects to the datacenter hub.
//
// nkeyPublicKeys carries the per-identity NKey public keys (proposal
// 013).  When NKey auth is enabled the reconciler passes the
// operator-generated keys; an empty map renders no authorization block.
func RenderNATSConfig(corpus *accv1alpha1.AgentCorpus, nkeyPublicKeys map[string]string) (string, error) {
	natsSpec := corpus.Spec.Infrastructure.NATS
	stsName := fmt.Sprintf("%s-nats", corpus.Name)

	var routes []string
	for i := int32(0); i < natsSpec.Replicas; i++ {
		routes = append(routes, fmt.Sprintf("%s-%d.%s", stsName, i, stsName))
	}

	// Resolve the hub URL from the edge spec (deployMode=edge only).
	hubUrl := ""
	if corpus.Spec.DeployMode == accv1alpha1.DeployModeEdge && corpus.Spec.Edge != nil {
		hubUrl = corpus.Spec.Edge.HubNatsUrl
	}

	authBlock := ""
	if natsSpec.NKeyAuth != nil && natsSpec.NKeyAuth.Enabled {
		var err error
		authBlock, err = renderAuthorizationBlock(nkeyPublicKeys)
		if err != nil {
			return "", err
		}
	}

	data := natsConfigData{
		CorpusName:  corpus.Name,
		StorageSize: natsSpec.StorageSize,
		Replicas:    natsSpec.Replicas,
		Routes:      routes,
		HubUrl:      hubUrl,
		AuthBlock:   authBlock,
	}

	var buf bytes.Buffer
	if err := natsConfTmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("render nats config: %w", err)
	}
	return buf.String(), nil
}

// NKeyIdentities returns the ordered NKey identity list — the six agent
// roles plus `tui` and `leaf` (proposal 013).  Exported so the Secret
// reconciler mints exactly this set.
func NKeyIdentities() []string {
	out := make([]string, len(nkeyIdentities))
	copy(out, nkeyIdentities)
	return out
}

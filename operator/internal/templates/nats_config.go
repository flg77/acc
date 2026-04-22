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
	"fmt"
	"text/template"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

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
      deny_imports: ["acc.*.heartbeat", "acc.*.register", "acc.*.task",
                     "acc.*.role_update", "acc.*.role_approval", "acc.*.alert"]
    }
  ]
}
{{- end }}
`))

type natsConfigData struct {
	CorpusName  string
	StorageSize string
	Replicas    int32
	Routes      []string
	// HubUrl is the NATS leaf node remote URL (deployMode=edge only).
	// When non-empty, a leafnodes block is rendered in the config.
	HubUrl string
}

// RenderNATSConfig produces a nats.conf string from the corpus spec.
// For deployMode=edge with a hub URL configured, a leafnodes block is included
// so the local NATS server connects to the datacenter hub.
func RenderNATSConfig(corpus *accv1alpha1.AgentCorpus) (string, error) {
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

	data := natsConfigData{
		CorpusName:  corpus.Name,
		StorageSize: natsSpec.StorageSize,
		Replicas:    natsSpec.Replicas,
		Routes:      routes,
		HubUrl:      hubUrl,
	}

	var buf bytes.Buffer
	if err := natsConfTmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("render nats config: %w", err)
	}
	return buf.String(), nil
}

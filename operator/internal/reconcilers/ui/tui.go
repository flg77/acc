// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package ui

import (
	"context"
	"fmt"
	"strings"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	ctrllog "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/util"
)

const (
	tuiComponent = "tui"
	// ttydPort is the loopback port ttyd serves the browser terminal on;
	// the oauth2-proxy sidecar is the sole ingress (shared pod netns).
	ttydPort = 7681
)

// TUIReconciler deploys the acc-tui interaction surface (proposal 023 / ADR
// 025 interaction plane). Two modes:
//
//   - Default (rsh attach): an idle pod the operator drives via
//     `oc rsh deploy/<corpus>-tui acc-tui` (a Deployment pod has no TTY).
//   - Web terminal (spec.tui.webTerminal=true): ttyd serves the interactive
//     TUI in a browser behind a Keycloak oauth2-proxy + Route + ConsoleLink.
//     HIGH-privilege, so it reuses the corpus's webgui Keycloak and refuses
//     to expose itself without one (ADR 025 §5 fail-safe).
type TUIReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// Name implements SubReconciler.
func (r *TUIReconciler) Name() string { return "ui/tui" }

// Reconcile implements SubReconciler.
func (r *TUIReconciler) Reconcile(ctx context.Context, corpus *accv1alpha1.AgentCorpus) (reconcilers.SubResult, error) {
	log := ctrllog.FromContext(ctx).WithName("tui")
	spec := corpus.Spec.TUI
	if spec == nil || (spec.Enabled != nil && !*spec.Enabled) {
		corpus.Status.TUIDeployed = false
		return reconcilers.SubResult{}, nil
	}

	if spec.WebTerminal != nil && *spec.WebTerminal {
		kc := keycloakFor(corpus)
		if kc == nil {
			log.Info("tui.webTerminal requested but no webgui.keycloak config — " +
				"NOT exposing a browser terminal (ADR 025 §5); deploying the " +
				"rsh-attach pod instead")
			return r.reconcileIdlePod(ctx, corpus, spec)
		}
		return r.reconcileWebTerminal(ctx, corpus, spec, kc)
	}
	return r.reconcileIdlePod(ctx, corpus, spec)
}

// keycloakFor returns the corpus's webgui Keycloak config (reused for the
// TUI web terminal), or nil when it is absent/incomplete.
func keycloakFor(corpus *accv1alpha1.AgentCorpus) *accv1alpha1.WebGUIKeycloakSpec {
	if corpus.Spec.WebGUI == nil {
		return nil
	}
	kc := corpus.Spec.WebGUI.Keycloak
	if kc == nil || kc.IssuerURL == "" || kc.ClientID == "" || kc.ClientSecretName == "" {
		return nil
	}
	return kc
}

// tuiEnv is the env every acc-tui container needs to reach the corpus NATS
// and observe its collectives (mirrors the agent + webgui wiring).
func (r *TUIReconciler) tuiEnv(ctx context.Context, corpus *accv1alpha1.AgentCorpus) []corev1.EnvVar {
	return []corev1.EnvVar{
		{Name: "ACC_DEPLOY_MODE", Value: string(corpus.Spec.DeployMode)},
		{Name: "ACC_NATS_URL", Value: fmt.Sprintf("nats://%s-nats:4222", corpus.Name)},
		{Name: "ACC_CORPUS_NAME", Value: corpus.Name},
		{Name: "ACC_COLLECTIVE_IDS", Value: strings.Join(r.collectiveIDs(ctx, corpus), ",")},
	}
}

// collectiveIDs resolves each referenced AgentCollective's CollectiveID
// (best-effort; an unresolvable collective is skipped).
func (r *TUIReconciler) collectiveIDs(ctx context.Context, corpus *accv1alpha1.AgentCorpus) []string {
	ids := make([]string, 0, len(corpus.Spec.Collectives))
	for _, ref := range corpus.Spec.Collectives {
		c := &accv1alpha1.AgentCollective{}
		if err := r.Client.Get(ctx, client.ObjectKey{
			Namespace: corpus.Namespace, Name: ref.Name,
		}, c); err != nil {
			continue
		}
		if c.Spec.CollectiveID != "" {
			ids = append(ids, c.Spec.CollectiveID)
		}
	}
	return ids
}

// reconcileIdlePod deploys the rsh-attach idle pod (the default).
func (r *TUIReconciler) reconcileIdlePod(ctx context.Context, corpus *accv1alpha1.AgentCorpus, spec *accv1alpha1.TUISpec) (reconcilers.SubResult, error) {
	name := fmt.Sprintf("%s-tui", corpus.Name)
	labels := util.CommonLabels(corpus.Name, tuiComponent, corpus.Spec.Version)
	replicas := spec.Replicas
	if replicas == 0 {
		replicas = 1
	}

	deploy := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: corpus.Namespace, Labels: labels},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(replicas),
			Selector: &metav1.LabelSelector{MatchLabels: util.SelectorLabels(labels)},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					SecurityContext:  &corev1.PodSecurityContext{RunAsNonRoot: ptr.To(true)},
					Containers: []corev1.Container{
						{
							Name:  "tui",
							Image: util.ComponentImage(corpus, "acc-tui", corpus.Spec.Version),
							// Idle: the TUI needs an interactive TTY, so the pod
							// stays alive and the operator attaches with
							// `oc rsh deploy/<corpus>-tui acc-tui`.
							Command: []string{"sleep", "infinity"},
							Env:     r.tuiEnv(ctx, corpus),
						},
					},
				},
			},
		},
	}

	result, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, deploy, func(existing client.Object) error {
		ed := existing.(*appsv1.Deployment)
		ed.Spec.Replicas = deploy.Spec.Replicas
		ed.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert tui Deployment: %w", err)
	}

	corpus.Status.TUIDeployed = true
	corpus.Status.TUIURL = "" // idle pod has no browser URL
	return reconcilers.SubResult{Progressing: result != util.UpsertResultNoop}, nil
}

// reconcileWebTerminal deploys ttyd + oauth2-proxy + Service + Route +
// ConsoleLink so the TUI is reachable (authed) from a browser.
func (r *TUIReconciler) reconcileWebTerminal(ctx context.Context, corpus *accv1alpha1.AgentCorpus, spec *accv1alpha1.TUISpec, kc *accv1alpha1.WebGUIKeycloakSpec) (reconcilers.SubResult, error) {
	log := ctrllog.FromContext(ctx).WithName("tui")
	ns := corpus.Namespace
	name := fmt.Sprintf("%s-tui", corpus.Name)
	labels := util.CommonLabels(corpus.Name, tuiComponent, corpus.Spec.Version)
	replicas := spec.Replicas
	if replicas == 0 {
		replicas = 1
	}

	// Service — fronts the oauth2-proxy port only.
	svc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns, Labels: labels},
		Spec: corev1.ServiceSpec{
			Selector: util.SelectorLabels(labels),
			Ports:    []corev1.ServicePort{{Name: "http", Port: proxyPort, TargetPort: intstr.FromInt(proxyPort)}},
		},
	}
	if _, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, svc, func(existing client.Object) error {
		existing.(*corev1.Service).Spec.Ports = svc.Spec.Ports
		return nil
	}); err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert tui Service: %w", err)
	}

	deploy := r.buildWebTerminalDeployment(ctx, corpus, name, labels, replicas, kc)
	result, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, deploy, func(existing client.Object) error {
		ed := existing.(*appsv1.Deployment)
		ed.Spec.Replicas = deploy.Spec.Replicas
		ed.Spec.Template = deploy.Spec.Template
		return nil
	})
	if err != nil {
		return reconcilers.SubResult{}, fmt.Errorf("upsert tui web-terminal Deployment: %w", err)
	}

	// Route + ConsoleLink (best-effort, discovery-gated, non-fatal).
	if err := r.upsertRoute(ctx, corpus, name, labels); err != nil {
		if apimeta.IsNoMatchError(err) {
			log.Info("route.openshift.io not present — skipping tui Route (expose the Service yourself)")
		} else {
			log.Error(err, "tui Route upsert failed; continuing without an operator-managed Route")
		}
	}
	if host := routeHost(ctx, r.Client, ns, name); host != "" {
		corpus.Status.TUIURL = "https://" + host
		if err := upsertConsoleLink(ctx, r.Client, corpus,
			fmt.Sprintf("acc-%s-tui", corpus.Name),
			fmt.Sprintf("ACC TUI — %s", corpus.Name),
			corpus.Status.TUIURL,
		); err != nil && !apimeta.IsNoMatchError(err) {
			log.Error(err, "tui ConsoleLink upsert failed (non-fatal)")
		}
	}

	corpus.Status.TUIDeployed = true
	progressing := result != util.UpsertResultNoop
	if corpus.Status.TUIURL == "" {
		progressing = true // requeue until the Route host is admitted
	}
	return reconcilers.SubResult{Progressing: progressing}, nil
}

func (r *TUIReconciler) buildWebTerminalDeployment(ctx context.Context, corpus *accv1alpha1.AgentCorpus, name string, labels map[string]string, replicas int32, kc *accv1alpha1.WebGUIKeycloakSpec) *appsv1.Deployment {
	secretEnv := func(key string) *corev1.EnvVarSource {
		return &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{
			LocalObjectReference: corev1.LocalObjectReference{Name: kc.ClientSecretName},
			Key:                  key,
		}}
	}
	// Textual needs a real terminal type inside the ttyd PTY.
	ttydEnv := append(r.tuiEnv(ctx, corpus),
		corev1.EnvVar{Name: "TERM", Value: "xterm-256color"},
	)

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: corpus.Namespace, Labels: labels},
		Spec: appsv1.DeploymentSpec{
			Replicas: ptr.To(replicas),
			Selector: &metav1.LabelSelector{MatchLabels: util.SelectorLabels(labels)},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: util.ImagePullSecrets(corpus),
					SecurityContext:  &corev1.PodSecurityContext{RunAsNonRoot: ptr.To(true)},
					Containers: []corev1.Container{
						{
							// ttyd serves the interactive acc-tui in a browser
							// terminal, bound to loopback (proxy is sole ingress).
							Name:  "tui",
							Image: util.ComponentImage(corpus, "acc-tui", corpus.Spec.Version),
							Command: []string{
								"ttyd",
								"--port", fmt.Sprintf("%d", ttydPort),
								"--interface", "127.0.0.1",
								"--writable",
								"acc-tui",
							},
							Env: ttydEnv,
						},
						{
							// oauth2-proxy — Keycloak OIDC auth-code flow in front
							// of ttyd (reuses the corpus webgui Keycloak client;
							// the tui Route's redirect URI must be registered on
							// that client).
							Name:  "oauth2-proxy",
							Image: oauth2ProxyImage,
							Args: []string{
								"--provider=keycloak-oidc",
								"--oidc-issuer-url=" + kc.IssuerURL,
								"--client-id=" + kc.ClientID,
								"--client-secret=$(CLIENT_SECRET)",
								"--cookie-secret=$(COOKIE_SECRET)",
								"--email-domain=*",
								fmt.Sprintf("--upstream=http://127.0.0.1:%d", ttydPort),
								"--pass-user-headers=true",
								"--scope=openid email groups",
								fmt.Sprintf("--http-address=0.0.0.0:%d", proxyPort),
							},
							Env: []corev1.EnvVar{
								{Name: "CLIENT_SECRET", ValueFrom: secretEnv("client-secret")},
								{Name: "COOKIE_SECRET", ValueFrom: secretEnv("cookie-secret")},
							},
							Ports: []corev1.ContainerPort{{Name: "http", ContainerPort: proxyPort}},
						},
					},
				},
			},
		},
	}
}

// upsertRoute creates/updates the edge-TLS Route fronting the tui
// oauth2-proxy (mirrors the webgui Route).
func (r *TUIReconciler) upsertRoute(ctx context.Context, corpus *accv1alpha1.AgentCorpus, name string, labels map[string]string) error {
	u := &unstructured.Unstructured{}
	u.SetGroupVersionKind(routeGVK)
	u.SetName(name)
	u.SetNamespace(corpus.Namespace)
	u.SetLabels(labels)
	spec := map[string]interface{}{
		"to":   map[string]interface{}{"kind": "Service", "name": name},
		"port": map[string]interface{}{"targetPort": int64(proxyPort)},
		"tls":  map[string]interface{}{"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect"},
	}
	_ = unstructured.SetNestedMap(u.Object, spec, "spec")
	_, err := util.Upsert(ctx, r.Client, r.Scheme, corpus, u, func(existing client.Object) error {
		eu := existing.(*unstructured.Unstructured)
		return unstructured.SetNestedMap(eu.Object, spec, "spec")
	})
	return err
}

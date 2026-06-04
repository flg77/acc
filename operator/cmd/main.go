// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package main

import (
	"context"
	"crypto/tls"
	"flag"
	"os"
	"time"

	// Import all Kubernetes client auth plugins (e.g. Azure, GCP, OIDC, etc.)
	// to ensure that exec-entrypoint and run can make use of them.
	_ "k8s.io/client-go/plugin/pkg/client/auth"

	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	"k8s.io/client-go/kubernetes"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
	"sigs.k8s.io/controller-runtime/pkg/webhook"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/controller"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/filewatch"
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")
)

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(accv1alpha1.AddToScheme(scheme))
}

func main() {
	var metricsAddr string
	var enableLeaderElection bool
	var probeAddr string
	var secureMetrics bool
	var enableHTTP2 bool
	var roleSource string
	var rolesRoot string
	var roleSyncNamespace string

	flag.StringVar(&metricsAddr, "metrics-bind-address", ":8080", "The address the metric endpoint binds to.")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "The address the probe endpoint binds to.")
	flag.BoolVar(&enableLeaderElection, "leader-elect", false,
		"Enable leader election for controller manager. "+
			"Enabling this will ensure there is only one active controller manager.")
	flag.BoolVar(&secureMetrics, "metrics-secure", false,
		"If set the metrics endpoint is served securely")
	flag.BoolVar(&enableHTTP2, "enable-http2", false,
		"If set, HTTP/2 will be enabled for the metrics and webhook servers")
	// Proposal 010 PR-2: role-sync source-of-truth.
	flag.StringVar(&roleSource, "role-source", envOrDefault("ACC_ROLE_SOURCE", "crd"),
		"Role-definition source of truth: files | crd | mirror.  Defaults to crd "+
			"for backwards compatibility (no file watcher started).")
	flag.StringVar(&rolesRoot, "roles-root", envOrDefault("ACC_ROLES_ROOT", "/var/lib/acc/roles"),
		"Filesystem path to per-role subdirectories.  Used when role-source is files or mirror.")
	flag.StringVar(&roleSyncNamespace, "role-sync-namespace", envOrDefault("ACC_ROLE_SYNC_NAMESPACE", "default"),
		"K8s namespace where AgentCollective resources matching role-file IDs live.")

	opts := zap.Options{Development: true}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))

	// if the enable-http2 flag is false (the default), http/2 should be disabled
	// due to its vulnerabilities. More specifically, disabling http/2 will
	// prevent from being vulnerable to the HTTP/2 Stream Cancellation and
	// Rapid Reset CVEs. For more information see:
	// - https://github.com/advisories/GHSA-qppj-fm5r-hxr3
	// - https://github.com/advisories/GHSA-4374-p667-p6c8
	disableHTTP2 := func(c *tls.Config) {
		setupLog.Info("disabling http/2")
		c.NextProtos = []string{"http/1.1"}
	}

	tlsOpts := []func(*tls.Config){}
	if !enableHTTP2 {
		tlsOpts = append(tlsOpts, disableHTTP2)
	}

	webhookServer := webhook.NewServer(webhook.Options{
		TLSOpts: tlsOpts,
	})

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme: scheme,
		Metrics: metricsserver.Options{
			BindAddress:   metricsAddr,
			SecureServing: secureMetrics,
			TLSOpts:       tlsOpts,
		},
		WebhookServer:          webhookServer,
		HealthProbeBindAddress: probeAddr,
		LeaderElection:         enableLeaderElection,
		LeaderElectionID:       "acc.redhat.io",
	})
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	if err = (&controller.AgentCorpusReconciler{
		Client: mgr.GetClient(),
		Scheme: mgr.GetScheme(),
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "AgentCorpus")
		os.Exit(1)
	}

	collectiveReconciler := &controller.AgentCollectiveReconciler{
		Client:     mgr.GetClient(),
		Scheme:     mgr.GetScheme(),
		RoleSource: roleSource,
		RolesRoot:  rolesRoot,
		Namespace:  roleSyncNamespace,
	}
	if err = collectiveReconciler.SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "AgentCollective")
		os.Exit(1)
	}

	// Stage 1.6b — AccCatalog reconciler (renders to ConfigMap).
	if err = (&controller.AccCatalogReconciler{
		Client: mgr.GetClient(),
		Scheme: mgr.GetScheme(),
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "AccCatalog")
		os.Exit(1)
	}

	// Stage 1.6b — AccPackageInstall reconciler (exec acc-cli pkg-install-direct).
	kubeClient, err := kubernetes.NewForConfig(mgr.GetConfig())
	if err != nil {
		setupLog.Error(err, "unable to build kubernetes client for AccPackageInstall")
		os.Exit(1)
	}
	if err = (&controller.AccPackageInstallReconciler{
		Client:     mgr.GetClient(),
		Scheme:     mgr.GetScheme(),
		Config:     mgr.GetConfig(),
		Kubernetes: kubeClient,
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "AccPackageInstall")
		os.Exit(1)
	}

	// Proposal 010 PR-2: start the file watcher when role-source enables
	// file → CRD projection.  Spawned as a Runnable so it's wired into
	// the manager's lifecycle (cancelled on shutdown signal).
	if collectiveReconciler.FileWriteEnabled() {
		setupLog.Info("starting role-file watcher",
			"role-source", roleSource,
			"roles-root", rolesRoot,
			"namespace", roleSyncNamespace,
		)
		watcher := filewatch.NewWatcher(rolesRoot, filewatch.DefaultDebounce)
		if err := mgr.Add(&fileWatcherRunnable{
			watcher:    watcher,
			reconciler: collectiveReconciler,
		}); err != nil {
			setupLog.Error(err, "unable to add file watcher to manager")
			os.Exit(1)
		}
	}

	if err = (&accv1alpha1.AgentCorpus{}).SetupWebhookWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create webhook", "webhook", "AgentCorpus")
		os.Exit(1)
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting manager")
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}

// envOrDefault returns os.Getenv(key) when non-empty, else fallback.
// Used so the role-sync flags default to the matching ACC_* env vars
// (set by the operator's Deployment via the same field names the agents
// themselves use) while still being CLI-overridable.
func envOrDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// fileWatcherRunnable adapts a filewatch.Watcher into the controller-
// runtime manager.Runnable interface, so the watcher participates in
// the manager's start/stop lifecycle and a SIGTERM cleanly drains
// pending debounces.
type fileWatcherRunnable struct {
	watcher    *filewatch.Watcher
	reconciler *controller.AgentCollectiveReconciler
}

// Start runs the watcher and dispatches every emitted event to the
// reconciler's ProjectRoleFile.  Returns when ctx is cancelled or the
// watcher's event channel closes.
func (r *fileWatcherRunnable) Start(ctx context.Context) error {
	log := ctrl.Log.WithName("role-file-watcher")
	if err := r.watcher.Start(ctx); err != nil {
		return err
	}

	// Initial sweep: project every existing role.yaml file once at
	// startup so the CR state catches up to whatever was edited while
	// the operator was down.  Bounded by the time it takes to read N
	// small yaml files.
	if entries, err := os.ReadDir(r.reconciler.RolesRoot); err == nil {
		for _, entry := range entries {
			if !entry.IsDir() {
				continue
			}
			if err := r.reconciler.ProjectRoleFile(ctx, entry.Name()); err != nil {
				log.Info("initial projection failed",
					"role", entry.Name(), "err", err.Error(),
				)
			}
		}
	}

	for {
		select {
		case <-ctx.Done():
			return nil
		case ev, ok := <-r.watcher.Events():
			if !ok {
				return nil
			}
			// Bound each projection with a 30s timeout so a stuck API
			// server doesn't block the watcher's event loop.
			projCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
			err := r.reconciler.ProjectRoleFile(projCtx, ev.ID)
			cancel()
			if err != nil {
				log.Info("projection failed",
					"role", ev.ID, "path", ev.Path, "err", err.Error(),
				)
			}
		}
	}
}

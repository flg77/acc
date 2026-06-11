// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package controller

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/remotecommand"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

var pkgInstallLog = logf.Log.WithName("accpackageinstall-controller")

// Phase strings for AccPackageInstallStatus.Phase.
const (
	PhasePending    = "Pending"
	PhaseInstalling = "Installing"
	PhaseInstalled  = "Installed"
	PhaseFailed     = "Failed"
)

// AccPackageInstallReconciler reconciles an AccPackageInstall by
// exec'ing `acc-cli collective pkg-install` against an ACC pod
// matching the target AgentCorpus.
//
// Flow:
//
//  1. Resolve the target AgentCorpus (Spec.TargetCorpus or all in
//     namespace).
//  2. Find a ready ACC pod backing the corpus (label selector).
//  3. Render a synthetic collective.yaml fragment carrying just this
//     install's `required_packages:` entry.
//  4. kubectl exec equivalent: `acc-cli collective pkg-install
//     --json <spec> [--allow-unsigned]`.
//  5. Parse the JSON result; patch status.
//
// Idempotent: Stage 0's `acc-pkg install` re-install on matching
// content_sha256 is a no-op, so re-reconciling a satisfied install
// just refreshes status.LastInstalledAt.
//
// +kubebuilder:rbac:groups=acc.redhat.io,resources=accpackageinstalls,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=acc.redhat.io,resources=accpackageinstalls/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcorpuses,verbs=get;list;watch
// +kubebuilder:rbac:groups=core,resources=pods,verbs=get;list;watch
// +kubebuilder:rbac:groups=core,resources=pods/exec,verbs=create
type AccPackageInstallReconciler struct {
	Client client.Client
	Scheme *runtime.Scheme

	// Config + Kubernetes are used for the pod-exec call.  main.go
	// wires them from mgr.GetConfig() at startup.
	Config     *rest.Config
	Kubernetes kubernetes.Interface

	// PollInterval controls re-reconcile cadence for installed
	// resources (idempotent refresh).  Default: 5 min when zero.
	PollInterval time.Duration
}

// SetupWithManager registers the reconciler.
func (r *AccPackageInstallReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		Named("accpackageinstall").
		For(&accv1alpha1.AccPackageInstall{}).
		Complete(r)
}

// Reconcile drives one AccPackageInstall toward Installed.
func (r *AccPackageInstallReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := pkgInstallLog.WithValues("name", req.Name, "namespace", req.Namespace)

	cr := &accv1alpha1.AccPackageInstall{}
	if err := r.Client.Get(ctx, req.NamespacedName, cr); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, fmt.Errorf("fetch AccPackageInstall: %w", err)
	}

	// Pick a target ACC pod.
	pod, err := r.findAccPod(ctx, cr.Namespace, cr.Spec.TargetCorpus)
	if err != nil {
		r.markFailed(ctx, cr, "PodNotFound", err.Error())
		return r.requeue(), nil
	}

	// Set Installing phase while we exec.
	r.setPhase(ctx, cr, PhaseInstalling, "Exec", fmt.Sprintf("exec into %s", pod.Name))

	// Build the acc-cli command. An empty constraint means "latest": pass the
	// bare @scope/name so the resolver picks the highest published version
	// ("name@" would be malformed).
	pkgRef := cr.Spec.Name
	if cr.Spec.Constraint != "" {
		pkgRef = fmt.Sprintf("%s@%s", cr.Spec.Name, cr.Spec.Constraint)
	}
	args := []string{
		"acc-cli", "collective", "pkg-install-direct",
		pkgRef,
		"--json",
	}
	if cr.Spec.AllowUnsigned {
		args = append(args, "--allow-unsigned")
		log.Info("AUDIT: allow-unsigned bypass requested",
			"name", cr.Spec.Name, "constraint", cr.Spec.Constraint)
	}
	if cr.Spec.CatalogRef != "" {
		args = append(args, "--catalog", cr.Spec.CatalogRef)
	}

	stdout, stderr, execErr := r.execInPod(ctx, pod, args)
	if execErr != nil {
		r.markFailed(ctx, cr, "ExecFailed",
			fmt.Sprintf("acc-cli exec failed: %v; stderr=%s", execErr, truncate(stderr, 500)))
		return r.requeue(), nil
	}

	// Parse the result JSON — acc-cli collective pkg-install --json
	// returns either {"already_satisfied": true, ...} or
	// {"installed": [{"installed": "@scope/name@ver", "install_path": ..., "was_already_installed": ...}], ...}
	var result struct {
		AlreadySatisfied bool `json:"already_satisfied,omitempty"`
		Installed        []struct {
			Spec                 string `json:"spec"`
			InstalledRef         string `json:"installed"`
			InstallPath          string `json:"install_path"`
			WasAlreadyInstalled  bool   `json:"was_already_installed"`
		} `json:"installed,omitempty"`
		Failed []struct {
			Spec  string `json:"spec"`
			Error string `json:"error"`
		} `json:"failed,omitempty"`
	}
	if err := json.Unmarshal([]byte(stdout), &result); err != nil {
		r.markFailed(ctx, cr, "ParseFailed",
			fmt.Sprintf("could not parse pkg-install output: %v; stdout=%s",
				err, truncate(stdout, 500)))
		return r.requeue(), nil
	}

	if len(result.Failed) > 0 {
		f := result.Failed[0]
		r.markFailed(ctx, cr, "InstallFailed", fmt.Sprintf("%s: %s", f.Spec, f.Error))
		return r.requeue(), nil
	}

	// Success path: extract name/version/install_path from either
	// the installed[] entry or already_satisfied (status-only refresh).
	now := metav1.NewTime(time.Now().UTC())
	cr.Status.ObservedGeneration = cr.Generation
	cr.Status.Phase = PhaseInstalled
	cr.Status.LastInstalledAt = &now
	if len(result.Installed) > 0 {
		entry := result.Installed[0]
		// Split "@scope/name@version" into version
		if idx := strings.LastIndex(entry.InstalledRef, "@"); idx > 0 {
			cr.Status.InstalledVersion = entry.InstalledRef[idx+1:]
		}
		cr.Status.InstallPath = entry.InstallPath
	}
	setCondition(&cr.Status.Conditions, metav1.Condition{
		Type:               "Ready",
		Status:             metav1.ConditionTrue,
		Reason:             "Installed",
		Message:            fmt.Sprintf("installed via pod %s", pod.Name),
		LastTransitionTime: now,
	})
	if err := r.Client.Status().Update(ctx, cr); err != nil {
		return ctrl.Result{}, fmt.Errorf("status update: %w", err)
	}

	log.Info("install reconciled",
		"phase", cr.Status.Phase, "version", cr.Status.InstalledVersion)
	return r.requeue(), nil
}

// findAccPod returns a ready ACC pod in ``ns``.  When ``corpusName``
// is non-empty, restricts to pods owned by that corpus via label
// selector ``acc.redhat.io/corpus=<name>``.
func (r *AccPackageInstallReconciler) findAccPod(ctx context.Context, ns, corpusName string) (*corev1.Pod, error) {
	labels := map[string]string{}
	if corpusName != "" {
		labels["acc.redhat.io/corpus"] = corpusName
	}
	var pods corev1.PodList
	if err := r.Client.List(ctx, &pods,
		client.InNamespace(ns),
		client.MatchingLabels(labels),
	); err != nil {
		return nil, fmt.Errorf("listing pods: %w", err)
	}
	for i := range pods.Items {
		p := &pods.Items[i]
		if p.Status.Phase != corev1.PodRunning {
			continue
		}
		// Pick the first Ready pod.
		for _, c := range p.Status.Conditions {
			if c.Type == corev1.PodReady && c.Status == corev1.ConditionTrue {
				return p, nil
			}
		}
	}
	return nil, fmt.Errorf("no ready ACC pod in namespace %q (corpus=%q)", ns, corpusName)
}

// execInPod runs ``args`` inside the first container of ``pod`` and
// returns stdout/stderr.  Modelled on kubectl exec.
func (r *AccPackageInstallReconciler) execInPod(ctx context.Context, pod *corev1.Pod, args []string) (string, string, error) {
	if r.Kubernetes == nil || r.Config == nil {
		return "", "", fmt.Errorf("reconciler missing rest.Config or kubernetes.Interface — wire from main.go")
	}
	container := ""
	if len(pod.Spec.Containers) > 0 {
		container = pod.Spec.Containers[0].Name
	}
	req := r.Kubernetes.CoreV1().RESTClient().Post().
		Resource("pods").
		Name(pod.Name).
		Namespace(pod.Namespace).
		SubResource("exec").
		VersionedParams(&corev1.PodExecOptions{
			Container: container,
			Command:   args,
			Stdin:     false,
			Stdout:    true,
			Stderr:    true,
			TTY:       false,
		}, runtime.NewParameterCodec(r.Scheme))

	exec, err := remotecommand.NewSPDYExecutor(r.Config, "POST", req.URL())
	if err != nil {
		return "", "", fmt.Errorf("creating SPDY executor: %w", err)
	}
	var stdout, stderr bytes.Buffer
	err = exec.StreamWithContext(ctx, remotecommand.StreamOptions{
		Stdout: &stdout,
		Stderr: &stderr,
	})
	return stdout.String(), stderr.String(), err
}

// requeue picks a reasonable retry/refresh interval.
func (r *AccPackageInstallReconciler) requeue() ctrl.Result {
	d := r.PollInterval
	if d == 0 {
		d = 5 * time.Minute
	}
	return ctrl.Result{RequeueAfter: d}
}

// setPhase patches just the Phase field — used during transient
// states (Installing) before the final status patch.
func (r *AccPackageInstallReconciler) setPhase(ctx context.Context, cr *accv1alpha1.AccPackageInstall, phase, reason, message string) {
	cr.Status.Phase = phase
	now := metav1.NewTime(time.Now().UTC())
	setCondition(&cr.Status.Conditions, metav1.Condition{
		Type:               "Reconciling",
		Status:             metav1.ConditionTrue,
		Reason:             reason,
		Message:            message,
		LastTransitionTime: now,
	})
	_ = r.Client.Status().Update(ctx, cr)
}

// markFailed patches the CR into Phase=Failed with a Ready=False
// condition carrying the reason.
func (r *AccPackageInstallReconciler) markFailed(ctx context.Context, cr *accv1alpha1.AccPackageInstall, reason, message string) {
	cr.Status.Phase = PhaseFailed
	cr.Status.ObservedGeneration = cr.Generation
	now := metav1.NewTime(time.Now().UTC())
	setCondition(&cr.Status.Conditions, metav1.Condition{
		Type:               "Ready",
		Status:             metav1.ConditionFalse,
		Reason:             reason,
		Message:            message,
		LastTransitionTime: now,
	})
	_ = r.Client.Status().Update(ctx, cr)
	pkgInstallLog.Error(nil, "install failed", "cr", cr.Name, "reason", reason, "message", message)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

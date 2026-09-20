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
	"sort"
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
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/ui"
)

var pkgInstallLog = logf.Log.WithName("accpackageinstall-controller")

// Phase strings for AccPackageInstallStatus.Phase.
const (
	PhasePending    = "Pending"
	PhaseInstalling = "Installing"
	PhaseInstalled  = "Installed"
	PhaseFailed     = "Failed"
)

// accVenvPython is the ABSOLUTE path to the agent image's S2I virtualenv
// interpreter, which carries acc's runtime DEPENDENCIES. acc itself is not in
// the venv site-packages — it ships as the source tree at /app/acc, so the
// pkg-install exec also sets PYTHONPATH=/app (see the exec args). A bare
// `python3` under `kubectl exec` resolves to /usr/bin/python3 (no deps).
// Proposal 032 §11 Finding C.
const accVenvPython = "/opt/app-root/bin/python3"

// AccPackageInstallReconciler reconciles an AccPackageInstall by
// exec'ing `acc-cli collective pkg-install` against an ACC pod
// matching the target AgentCorpus.
//
// Flow:
//
//  1. Resolve the target AgentCorpus (Spec.TargetCorpus or all in
//     namespace).
//  2. Find a ready ACC agent pod backing the corpus (label selector), plus
//     every running TUI/WebGUI pod of the corpus that carries the
//     pkg-installer sidecar (ui.PkgInstallerContainerName).
//  3. Render a synthetic collective.yaml fragment carrying just this
//     install's `required_packages:` entry.
//  4. kubectl exec equivalent: `acc-cli collective pkg-install
//     --json <spec> [--allow-unsigned]` — into the agent pod first, then
//     into each UI pod's pkg-installer sidecar.
//  5. Parse the JSON result; patch status. Installed only when every
//     target pod reported the pack on disk.
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

	// exec replaces execInPod in unit tests (nil in production).
	exec func(ctx context.Context, pod *corev1.Pod, container string, args []string) (string, string, error)
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

	// Every ready agent pod of the corpus, plus its UI pods (TUI, WebGUI) so an
	// installed pack is visible where operators look for it. EVERY agent, not
	// one: each keeps its own packages root (an emptyDir), and until 0.2.25 a
	// pass installed into "the first Ready pod" of a cache listing whose order
	// is not stable -- the other agents got their package by chance, one pass
	// at a time (bb3, 2026-09-18: five agents took nine minutes, the last one
	// four of them).
	agentPods, err := r.findAccPods(ctx, cr.Namespace, cr.Spec.TargetCorpus)
	if err != nil {
		r.markFailed(ctx, cr, "PodNotFound", err.Error())
		return r.requeue(), nil
	}
	pod := agentPods[0]
	uiPods, err := r.findUIPods(ctx, cr.Namespace, cr.Spec.TargetCorpus)
	if err != nil {
		r.markFailed(ctx, cr, "PodNotFound", err.Error())
		return r.requeue(), nil
	}

	// Surface "Installing" only on a first install or a retry — NOT on every
	// steady-state requeue. The exec below runs each PollInterval (idempotent
	// self-heal for a restarted pod that lost its emptyDir packages root), but
	// flipping Phase to Installing every cycle makes the CR visibly oscillate
	// Installing<->Installed, so `oc get` / the TUI / the console look stuck
	// "Installing" when the pack is in fact Installed. Only churn the phase when
	// it isn't already Installed for the current generation.
	steadyInstalled := cr.Status.Phase == PhaseInstalled &&
		cr.Status.ObservedGeneration == cr.Generation
	if !steadyInstalled {
		r.setPhase(ctx, cr, PhaseInstalling, "Exec", fmt.Sprintf("exec into %s", pod.Name))
	}

	// Build the acc CLI command. Two pieces both matter (proposal 032 §11
	// Finding C, observed live):
	//   1. interpreter: the venv python by ABSOLUTE PATH
	//      (/opt/app-root/bin/python3) — it carries acc's deps. A bare `python3`
	//      under a non-login `kubectl exec` resolves to /usr/bin/python3 (no deps).
	//   2. PYTHONPATH=/app: acc itself is NOT importable from the venv
	//      site-packages — it ships as the source tree at /app/acc (exactly what
	//      the agent's own CMD `python3 -m acc.agent` imports via WORKDIR /app).
	//      The exec does not run from /app, so without PYTHONPATH=/app the import
	//      fails ("No module named 'acc'"). `env PYTHONPATH=/app` puts the source
	//      on sys.path regardless of the exec's working directory.
	// An empty constraint means "latest": pass the bare @scope/name so the
	// resolver picks the highest published version ("name@" would be malformed).
	pkgRef := cr.Spec.Name
	if cr.Spec.Constraint != "" {
		pkgRef = fmt.Sprintf("%s@%s", cr.Spec.Name, cr.Spec.Constraint)
	}
	args := []string{
		"env", "PYTHONPATH=/app", accVenvPython, "-m", "acc.cli", "collective", "pkg-install-direct",
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

	result, reason, msg := r.installInto(ctx, pod, agentContainer(pod), args)
	if reason != "" {
		r.markFailed(ctx, cr, reason, msg)
		return r.requeue(), nil
	}
	agentNames := []string{pod.Name}
	for _, p := range agentPods[1:] {
		if _, reason, msg := r.installInto(ctx, p, agentContainer(p), args); reason != "" {
			r.markFailed(ctx, cr, reason, fmt.Sprintf("pod %s: %s", p.Name, msg))
			return r.requeue(), nil
		}
		agentNames = append(agentNames, p.Name)
	}
	// The UI pods must hold the pack too: Installed means every target pod
	// has it on disk, not just the agent.
	uiNames := make([]string, 0, len(uiPods))
	for _, p := range uiPods {
		if _, reason, msg := r.installInto(ctx, p, ui.PkgInstallerContainerName, args); reason != "" {
			r.markFailed(ctx, cr, reason, fmt.Sprintf("pod %s/%s: %s", p.Name, ui.PkgInstallerContainerName, msg))
			return r.requeue(), nil
		}
		uiNames = append(uiNames, p.Name)
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
			previous := cr.Status.InstalledVersion
			cr.Status.InstalledVersion = entry.InstalledRef[idx+1:]
			// An UPGRADE: the agents are running with the previous version's
			// manifests in memory. Recording the version here is what rolls
			// them (the agent StatefulSets carry it on their pod template).
			// A first install (no previous version) rolls nothing.
			if previous != "" && previous != cr.Status.InstalledVersion {
				cr.Status.RolledForVersion = cr.Status.InstalledVersion
				log.Info("package upgraded: the corpus' agents will be rolled",
					"from", previous, "to", cr.Status.InstalledVersion)
			}
		}
		cr.Status.InstallPath = entry.InstallPath
	}
	message := fmt.Sprintf("installed via pod %s", pod.Name)
	if len(agentNames) > 1 {
		message = fmt.Sprintf("installed via pods %s", strings.Join(agentNames, ", "))
	}
	if len(uiNames) > 0 {
		message += fmt.Sprintf("; on disk in UI pods %s", strings.Join(uiNames, ", "))
	}
	setCondition(&cr.Status.Conditions, metav1.Condition{
		Type:               "Ready",
		Status:             metav1.ConditionTrue,
		Reason:             "Installed",
		Message:            message,
		LastTransitionTime: now,
	})
	if err := r.Client.Status().Update(ctx, cr); err != nil {
		return ctrl.Result{}, fmt.Errorf("status update: %w", err)
	}

	log.Info("install reconciled",
		"phase", cr.Status.Phase, "version", cr.Status.InstalledVersion)
	return r.requeue(), nil
}

// findAccPod returns the first of findAccPods (kept for callers that need one).
func (r *AccPackageInstallReconciler) findAccPod(ctx context.Context, ns, corpusName string) (*corev1.Pod, error) {
	pods, err := r.findAccPods(ctx, ns, corpusName)
	if err != nil {
		return nil, err
	}
	return pods[0], nil
}

// findAccPods returns every ready ACC agent pod in ns, sorted by name so a
// pass is reproducible.  When corpusName
// is non-empty, restricts to pods owned by that corpus via label
// selector ``acc.redhat.io/corpus-name=<name>`` -- the label every
// pod-producing reconciler actually sets (LabelCorpusName).
func (r *AccPackageInstallReconciler) findAccPods(ctx context.Context, ns, corpusName string) ([]*corev1.Pod, error) {
	labels := map[string]string{}
	if corpusName != "" {
		labels[accv1alpha1.LabelCorpusName] = corpusName
	}
	var pods corev1.PodList
	if err := r.Client.List(ctx, &pods,
		client.InNamespace(ns),
		client.MatchingLabels(labels),
		// Restrict to AGENT pods. Every ACC pod (NATS, Redis, OTel, OPA, TUI,
		// WebGUI, agents) carries acc.redhat.io/corpus-name, but only agent pods
		// (acc-agent-core) have the acc source at /app/acc + the venv interpreter
		// the pkg-install exec needs. Without this filter the exec lands on a
		// random pod and fails with exit 127 ("/opt/app-root/bin/python3: No such
		// file or directory" on infra pods) or "No module named 'acc'" (on
		// TUI/WebGUI). LabelAgentRole is present only on agent pods (proposal 032
		// §11 Finding C).
		client.HasLabels{accv1alpha1.LabelAgentRole},
	); err != nil {
		return nil, fmt.Errorf("listing pods: %w", err)
	}
	var ready []*corev1.Pod
	for i := range pods.Items {
		p := &pods.Items[i]
		if p.Status.Phase != corev1.PodRunning || p.DeletionTimestamp != nil {
			continue
		}
		for _, c := range p.Status.Conditions {
			if c.Type == corev1.PodReady && c.Status == corev1.ConditionTrue {
				ready = append(ready, p)
				break
			}
		}
	}
	if len(ready) == 0 {
		return nil, fmt.Errorf("no ready ACC agent pod in namespace %q (corpus=%q)", ns, corpusName)
	}
	sort.Slice(ready, func(i, j int) bool { return ready[i].Name < ready[j].Name })
	return ready, nil
}

// pkgInstallResult is the JSON `acc-cli collective pkg-install --json`
// prints: either {"already_satisfied": true, ...} or
// {"installed": [{"installed": "@scope/name@ver", "install_path": ..., "was_already_installed": ...}], ...}
type pkgInstallResult struct {
	AlreadySatisfied bool `json:"already_satisfied,omitempty"`
	Installed        []struct {
		Spec                string `json:"spec"`
		InstalledRef        string `json:"installed"`
		InstallPath         string `json:"install_path"`
		WasAlreadyInstalled bool   `json:"was_already_installed"`
	} `json:"installed,omitempty"`
	Failed []struct {
		Spec  string `json:"spec"`
		Error string `json:"error"`
	} `json:"failed,omitempty"`
}

// installInto execs the install in one pod/container and parses the result.
// A non-empty reason is a failure (reason + message for markFailed).
func (r *AccPackageInstallReconciler) installInto(ctx context.Context, pod *corev1.Pod, container string, args []string) (pkgInstallResult, string, string) {
	var result pkgInstallResult
	run := r.execInPod
	if r.exec != nil {
		run = r.exec
	}
	stdout, stderr, execErr := run(ctx, pod, container, args)
	if execErr != nil {
		return result, "ExecFailed",
			fmt.Sprintf("acc-cli exec failed: %v; stderr=%s", execErr, truncate(stderr, 500))
	}
	if err := json.Unmarshal([]byte(stdout), &result); err != nil {
		return result, "ParseFailed",
			fmt.Sprintf("could not parse pkg-install output: %v; stdout=%s",
				err, truncate(stdout, 500))
	}
	if len(result.Failed) > 0 {
		f := result.Failed[0]
		return result, "InstallFailed", fmt.Sprintf("%s: %s", f.Spec, f.Error)
	}
	return result, "", ""
}

// agentContainer is the container the install runs in on an agent pod: the
// first one (the agent itself; sidecars are appended after it).
func agentContainer(pod *corev1.Pod) string {
	if len(pod.Spec.Containers) > 0 {
		return pod.Spec.Containers[0].Name
	}
	return ""
}

// findUIPods returns the corpus's TUI and WebGUI pods that can take an
// install: Running, not being deleted, with a Ready pkg-installer sidecar
// (ui.PkgInstallerContainerName). Readiness is the sidecar's, not the pod's:
// a WebGUI whose own container is crash-looping still gets the pack, so it is
// there when the WebGUI comes up. Pods from before the sidecar existed are
// not targets. Sorted by name for a stable status message. Same corpus scope
// as findAccPod (the acc.redhat.io/corpus-name label; all of ns when empty).
func (r *AccPackageInstallReconciler) findUIPods(ctx context.Context, ns, corpusName string) ([]*corev1.Pod, error) {
	labels := map[string]string{}
	if corpusName != "" {
		labels[accv1alpha1.LabelCorpusName] = corpusName
	}
	var pods corev1.PodList
	if err := r.Client.List(ctx, &pods, client.InNamespace(ns), client.MatchingLabels(labels)); err != nil {
		return nil, fmt.Errorf("listing pods: %w", err)
	}
	var out []*corev1.Pod
	for i := range pods.Items {
		p := &pods.Items[i]
		if _, isAgent := p.Labels[accv1alpha1.LabelAgentRole]; isAgent {
			continue
		}
		if p.DeletionTimestamp != nil || p.Status.Phase != corev1.PodRunning {
			continue
		}
		for _, cs := range p.Status.ContainerStatuses {
			if cs.Name == ui.PkgInstallerContainerName && cs.Ready {
				out = append(out, p)
				break
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, nil
}

// execInPod runs ``args`` inside ``container`` of ``pod`` and returns
// stdout/stderr.  Modelled on kubectl exec.
func (r *AccPackageInstallReconciler) execInPod(ctx context.Context, pod *corev1.Pod, container string, args []string) (string, string, error) {
	if r.Kubernetes == nil || r.Config == nil {
		return "", "", fmt.Errorf("reconciler missing rest.Config or kubernetes.Interface — wire from main.go")
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

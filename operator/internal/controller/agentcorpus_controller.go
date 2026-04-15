// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package controller

import (
	"context"
	"errors"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/discovery"
	"k8s.io/client-go/tools/record"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/bridge"
	collectiverec "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/governance"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/infra"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/observability"
	statuspkg "github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/status"
)

const (
	corpusFinalizer         = "acc.redhat.io/cleanup"
	requeueAfterProgressing = 15 * time.Second
)

var (
	corpusLog = logf.Log.WithName("agentcorpus-controller")

	constraintTemplateGVK = schema.GroupVersionKind{
		Group:   "templates.gatekeeper.sh",
		Version: "v1",
		Kind:    "ConstraintTemplate",
	}
)

// AgentCorpusReconciler reconciles an AgentCorpus object.
//
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcorpora,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcorpora/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcorpora/finalizers,verbs=update
// +kubebuilder:rbac:groups=acc.redhat.io,resources=agentcollectives,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=apps,resources=deployments;statefulsets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=core,resources=services;configmaps;persistentvolumeclaims;events,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=templates.gatekeeper.sh,resources=constrainttemplates,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=keda.sh,resources=scaledobjects,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=serving.kserve.io,resources=inferenceservices,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=monitoring.coreos.com,resources=prometheusrules,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=secrets,verbs=get;list;watch
type AgentCorpusReconciler struct {
	Client    client.Client
	Scheme    *runtime.Scheme
	Discovery discovery.DiscoveryInterface
	Recorder  record.EventRecorder
}

// SetupWithManager registers this reconciler with the controller-runtime Manager.
func (r *AgentCorpusReconciler) SetupWithManager(mgr ctrl.Manager) error {
	if r.Discovery == nil {
		disc, err := discovery.NewDiscoveryClientForConfig(mgr.GetConfig())
		if err != nil {
			return fmt.Errorf("create discovery client: %w", err)
		}
		r.Discovery = disc
	}
	if r.Recorder == nil {
		r.Recorder = mgr.GetEventRecorderFor("agentcorpus-controller")
	}

	return ctrl.NewControllerManagedBy(mgr).
		For(&accv1alpha1.AgentCorpus{}).
		Owns(&accv1alpha1.AgentCollective{}).
		Complete(r)
}

// Reconcile is the main reconciliation loop.
func (r *AgentCorpusReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := corpusLog.WithValues("agentcorpus", req.NamespacedName)

	// 1. Fetch.
	corpus := &accv1alpha1.AgentCorpus{}
	if err := r.Client.Get(ctx, req.NamespacedName, corpus); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	original := corpus.DeepCopy()

	// 2. Finalizer / deletion.
	if !corpus.DeletionTimestamp.IsZero() {
		if controllerutil.ContainsFinalizer(corpus, corpusFinalizer) {
			log.Info("running cleanup finalizer")
			if err := r.cleanup(ctx, corpus); err != nil {
				return ctrl.Result{}, err
			}
			controllerutil.RemoveFinalizer(corpus, corpusFinalizer)
			return ctrl.Result{}, r.Client.Update(ctx, corpus)
		}
		return ctrl.Result{}, nil
	}
	if !controllerutil.ContainsFinalizer(corpus, corpusFinalizer) {
		controllerutil.AddFinalizer(corpus, corpusFinalizer)
		return ctrl.Result{}, r.Client.Update(ctx, corpus)
	}

	// 3. ObservedGeneration.
	corpus.Status.ObservedGeneration = corpus.Generation

	// 4. Drive ordered sub-reconcilers.
	anyProgressing := false
	for _, sr := range r.buildSubReconcilers() {
		log.V(1).Info("running sub-reconciler", "name", sr.Name())
		subResult, err := sr.Reconcile(ctx, corpus)
		if err != nil {
			if errors.Is(err, reconcilers.ErrUpgradeApprovalPending) {
				log.Info("upgrade approval pending — halting")
				r.Recorder.Eventf(corpus, corev1.EventTypeWarning, "UpgradeApprovalPending",
					"apply annotation %s to approve upgrade to %s",
					accv1alpha1.AnnotationApproveUpgrade, corpus.Status.PendingUpgradeVersion)
				break
			}
			r.Recorder.Eventf(corpus, corev1.EventTypeWarning, "ReconcileError",
				"sub-reconciler %s: %v", sr.Name(), err)
			statuspkg.SetCondition(&corpus.Status.Conditions, accv1alpha1.ConditionTypeReady,
				metav1.ConditionFalse, "ReconcileError",
				fmt.Sprintf("sub-reconciler %s: %v", sr.Name(), err))
			corpus.Status.Phase = accv1alpha1.CorpusPhaseError
			_ = statuspkg.PatchCorpusStatus(ctx, r.Client, corpus, original)
			return ctrl.Result{}, err
		}
		if subResult.Progressing {
			anyProgressing = true
		}
	}

	// 5. Compute phase.
	phaseIn := statuspkg.PhaseInput{
		InfrastructureReady:    statuspkg.IsConditionTrue(corpus.Status.Conditions, accv1alpha1.ConditionTypeInfrastructureReady),
		CollectivesReady:       statuspkg.IsConditionTrue(corpus.Status.Conditions, accv1alpha1.ConditionTypeCollectivesReady),
		PrerequisitesMet:       corpus.Status.Prerequisites.AllMet,
		UpgradeApprovalPending: corpus.Status.PendingUpgradeVersion != "",
		DeployModeRHOAI:        corpus.Spec.DeployMode == accv1alpha1.DeployModeRHOAI,
		RHOAIInstalled:         corpus.Status.Prerequisites.RHOAIInstalled,
		IsProgressing:          anyProgressing,
	}
	corpus.Status.Phase = statuspkg.ComputeCorpusPhase(phaseIn)
	corpus.Status.CurrentVersion = corpus.Spec.Version

	readyStatus := metav1.ConditionFalse
	readyReason := string(corpus.Status.Phase)
	if corpus.Status.Phase == accv1alpha1.CorpusPhaseReady {
		readyStatus = metav1.ConditionTrue
		readyReason = "AllComponentsReady"
	}
	statuspkg.SetCondition(&corpus.Status.Conditions, accv1alpha1.ConditionTypeReady,
		readyStatus, readyReason, fmt.Sprintf("corpus phase: %s", corpus.Status.Phase))

	// 6. Patch status.
	if err := statuspkg.PatchCorpusStatus(ctx, r.Client, corpus, original); err != nil {
		return ctrl.Result{}, err
	}

	if anyProgressing {
		return ctrl.Result{RequeueAfter: requeueAfterProgressing}, nil
	}
	log.V(1).Info("reconcile complete", "phase", corpus.Status.Phase)
	return ctrl.Result{}, nil
}

// buildSubReconcilers returns the ordered list of sub-reconcilers.
func (r *AgentCorpusReconciler) buildSubReconcilers() []reconcilers.SubReconciler {
	return []reconcilers.SubReconciler{
		&reconcilers.PrerequisiteReconciler{Client: r.Client, Discovery: r.Discovery},
		&reconcilers.UpgradeReconciler{Client: r.Client},
		&infra.NATSReconciler{Client: r.Client, Scheme: r.Scheme},
		&infra.RedisReconciler{Client: r.Client, Scheme: r.Scheme},
		&infra.MilvusReconciler{},
		&governance.OPABundleServerReconciler{Client: r.Client, Scheme: r.Scheme},
		&governance.GatekeeperReconciler{Client: r.Client},
		&bridge.KafkaBridgeReconciler{Client: r.Client, Scheme: r.Scheme},
		&observability.OTelCollectorReconciler{Client: r.Client, Scheme: r.Scheme},
		&observability.PrometheusRulesReconciler{Client: r.Client},
		&collectiverec.CollectiveReconciler{Client: r.Client, Scheme: r.Scheme},
	}
}

// cleanup removes cluster-scoped resources (ConstraintTemplates) that cannot
// be garbage-collected via owner references from a namespace-scoped owner.
func (r *AgentCorpusReconciler) cleanup(ctx context.Context, corpus *accv1alpha1.AgentCorpus) error {
	if !corpus.Spec.Governance.GatekeeperIntegration || !corpus.Status.Prerequisites.GatekeeperInstalled {
		return nil
	}
	for _, name := range []string{
		"acc-category-a-signal-schema",
		"acc-category-b-bundle-policy",
		"acc-category-c-confidence",
	} {
		u := &unstructured.Unstructured{}
		u.SetGroupVersionKind(constraintTemplateGVK)
		u.SetName(name)
		if err := r.Client.Delete(ctx, u); client.IgnoreNotFound(err) != nil {
			return fmt.Errorf("delete ConstraintTemplate %s: %w", name, err)
		}
	}
	return nil
}

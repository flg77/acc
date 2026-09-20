package unit_test

import (
	"context"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/types"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/observability"
)

func otelRollCorpus(experimentID string) *accv1alpha1.AgentCorpus {
	c := webguiCorpus(nil)
	c.Spec.Observability = accv1alpha1.ObservabilitySpec{
		Backend: accv1alpha1.MetricsBackendOTel,
		OTelCollector: &accv1alpha1.OTelCollectorSpec{
			MLflowEndpoint:     "https://mlflow.example.com",
			MLflowExperimentID: experimentID,
		},
	}
	return c
}

func otelRollTemplate(t *testing.T, get func(context.Context, types.NamespacedName, *appsv1.Deployment) error) corev1.PodTemplateSpec {
	t.Helper()
	d := &appsv1.Deployment{}
	if err := get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-otel-collector"}, d); err != nil {
		t.Fatalf("collector Deployment: %v", err)
	}
	return d.Spec.Template
}

// The collector reads its config once, at start. A changed ConfigMap alone
// changes nothing in the running pod: on bb3 three config changes in a row
// (the experiment-id header, the RHOAI auth block, the MLflow filter) each sat
// unused until someone ran `rollout restart`. The pod template now carries the
// config's hash, so a new config is a new template and the Deployment rolls.
func TestOTelCollector_RollsWhenTheRenderedConfigChanges(t *testing.T) {
	c, _ := webguiClient(t)
	r := &observability.OTelCollectorReconciler{Client: c, Scheme: newScheme(t)}
	get := func(ctx context.Context, k types.NamespacedName, d *appsv1.Deployment) error { return c.Get(ctx, k, d) }

	if _, err := r.Reconcile(context.Background(), otelRollCorpus("")); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	first := otelRollTemplate(t, get).Annotations[observability.OTelConfigHashAnnotation]
	if len(first) != 64 {
		t.Fatalf("expected a sha256 on the pod template, got %q", first)
	}

	// The same corpus again: same config, same template -- no spurious roll.
	res, err := r.Reconcile(context.Background(), otelRollCorpus(""))
	if err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if again := otelRollTemplate(t, get).Annotations[observability.OTelConfigHashAnnotation]; again != first {
		t.Errorf("an unchanged config must keep its hash: %q -> %q", first, again)
	}
	if res.Progressing {
		t.Error("an unchanged config must not report progress (it would roll the collector)")
	}

	// A config change (the experiment id becomes a header): new hash, new template.
	if _, err := r.Reconcile(context.Background(), otelRollCorpus("2")); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	changed := otelRollTemplate(t, get).Annotations[observability.OTelConfigHashAnnotation]
	if changed == first {
		t.Error("a changed config must change the pod template's hash, or the collector keeps running the old one")
	}

	// And the hash is the ConfigMap's content, not something beside it.
	cm := &corev1.ConfigMap{}
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: "acc-system", Name: "rhoai-corpus-otel-collector-config"}, cm); err != nil {
		t.Fatalf("collector ConfigMap: %v", err)
	}
	if got := observability.OTelConfigHash(cm.Data["otel-collector.yaml"]); got != changed {
		t.Errorf("the template's hash %q is not the hash of the ConfigMap's config %q", changed, got)
	}
}

package controller

import (
	"context"
	"fmt"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
)

func podNamesOf(calls []execCall) []string {
	out := make([]string, 0, len(calls))
	for _, c := range calls {
		out = append(out, c.pod)
	}
	return out
}

func objMeta(name, ns string) metav1.ObjectMeta {
	return metav1.ObjectMeta{Name: name, Namespace: ns}
}

// Each agent keeps its own packages root (an emptyDir). Until 0.2.25 a pass
// installed into "the first Ready pod" of a cache listing whose order is not
// stable, so the other agents got their package by chance, one pass at a time
// (bb3, 2026-09-18: five agents took nine minutes). A pass now reaches every
// ready agent, in name order.
func TestReconcileInstallsIntoEveryReadyAgent(t *testing.T) {
	objs := []runtime.Object{
		readyAgentPod("agents-c-0", "ws", "c", "mortgage_underwriter"),
		readyAgentPod("agents-a-0", "ws", "c", "mortgage_prospect"),
		readyAgentPod("agents-b-0", "ws", "c", "mortgage_borrower"),
	}
	notReady := readyAgentPod("agents-d-0", "ws", "c", "mortgage_executive")
	notReady.Status.Conditions = []corev1.PodCondition{{Type: corev1.PodReady, Status: corev1.ConditionFalse}}
	objs = append(objs, notReady)

	r, calls := installReconciler(t, nil, objs...)
	cr := reconcileInstall(t, r)

	got := podNamesOf(*calls)
	want := []string{"agents-a-0", "agents-b-0", "agents-c-0"}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("exec targets = %v, want every ready agent in name order %v", got, want)
	}
	if cr.Status.Phase != PhaseInstalled {
		t.Fatalf("phase = %q, want Installed", cr.Status.Phase)
	}
	if msg := readyCondition(cr).Message; msg != "installed via pods agents-a-0, agents-b-0, agents-c-0" {
		t.Errorf("Ready message = %q", msg)
	}
}

// Installed means every agent has it: one agent that cannot take the install
// keeps the CR out of Installed, and says which one.
func TestReconcileIsNotInstalledWhileAnAgentLacksThePack(t *testing.T) {
	r, _ := installReconciler(t, map[string]bool{"agents-b-0": true},
		readyAgentPod("agents-a-0", "ws", "c", "mortgage_prospect"),
		readyAgentPod("agents-b-0", "ws", "c", "mortgage_borrower"))
	cr := reconcileInstall(t, r)
	if cr.Status.Phase == PhaseInstalled {
		t.Fatal("must not be Installed while agents-b-0 failed its install")
	}
	if msg := readyCondition(cr).Message; !strings.Contains(msg, "agents-b-0") {
		t.Errorf("the failure must name the pod, got %q", msg)
	}
}

// A first install rolls nothing (an agent that boots before its package lands
// promotes itself when it arrives); a version CHANGE records the version the
// agents must be rolled for; a re-install of the same version changes nothing.
func TestReconcileRecordsAnUpgradeAndOnlyAnUpgrade(t *testing.T) {
	r, _ := installReconciler(t, nil, readyAgentPod("agents-a-0", "ws", "c", "mortgage_prospect"))
	version := "1.2.0"
	r.exec = func(_ context.Context, _ *corev1.Pod, _ string, _ []string) (string, string, error) {
		return `{"installed":[{"spec":"@acc/mortgage-roles","installed":"@acc/mortgage-roles@` + version +
			`","install_path":"/var/lib/acc/packages/acc/mortgage-roles/` + version + `","was_already_installed":false}],"failed":[]}`, "", nil
	}

	cr := reconcileInstall(t, r)
	if cr.Status.InstalledVersion != "1.2.0" || cr.Status.RolledForVersion != "" {
		t.Fatalf("first install: installed=%q rolledFor=%q, want 1.2.0 and nothing to roll",
			cr.Status.InstalledVersion, cr.Status.RolledForVersion)
	}

	cr = reconcileInstall(t, r) // the steady-state poll, same version
	if cr.Status.RolledForVersion != "" {
		t.Fatalf("a re-install of the same version must not roll the agents, got %q", cr.Status.RolledForVersion)
	}

	version = "1.2.1"
	cr = reconcileInstall(t, r)
	if cr.Status.InstalledVersion != "1.2.1" || cr.Status.RolledForVersion != "1.2.1" {
		t.Fatalf("upgrade: installed=%q rolledFor=%q, want both 1.2.1",
			cr.Status.InstalledVersion, cr.Status.RolledForVersion)
	}

	cr = reconcileInstall(t, r) // polls after the upgrade keep the value stable
	if cr.Status.RolledForVersion != "1.2.1" {
		t.Fatalf("rolledForVersion must stay at the upgrade, got %q", cr.Status.RolledForVersion)
	}
}

// The corpus an install targets is the one to reconcile when it records an
// upgrade; an install that names none reaches every corpus in its namespace.
func TestMapInstallToCorpora(t *testing.T) {
	r, _ := installReconciler(t, nil,
		&accv1alpha1.AgentCorpus{ObjectMeta: objMeta("c1", "ws")},
		&accv1alpha1.AgentCorpus{ObjectMeta: objMeta("c2", "ws")},
		&accv1alpha1.AgentCorpus{ObjectMeta: objMeta("elsewhere", "other")})
	cr := &AgentCorpusReconciler{Client: r.Client}

	targeted := &accv1alpha1.AccPackageInstall{ObjectMeta: objMeta("i", "ws"),
		Spec: accv1alpha1.AccPackageInstallSpec{Name: "@acc/x", TargetCorpus: "c2"}}
	if got := cr.MapInstallToCorpora(context.Background(), targeted); len(got) != 1 || got[0].Name != "c2" || got[0].Namespace != "ws" {
		t.Fatalf("targeted install -> %v, want ws/c2", got)
	}
	untargeted := &accv1alpha1.AccPackageInstall{ObjectMeta: objMeta("i", "ws"),
		Spec: accv1alpha1.AccPackageInstallSpec{Name: "@acc/x"}}
	if got := cr.MapInstallToCorpora(context.Background(), untargeted); len(got) != 2 {
		t.Fatalf("untargeted install -> %v, want both corpora of ws", got)
	}
}

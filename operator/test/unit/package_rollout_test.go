package unit_test

import (
	"testing"

	accv1alpha1 "github.com/redhat-ai-dev/agentic-cell-corpus/operator/api/v1alpha1"
	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/reconcilers/collective"
)

func rolloutInstall(name, corpus, rolledFor string) accv1alpha1.AccPackageInstall {
	in := accv1alpha1.AccPackageInstall{}
	in.Spec.Name = name
	in.Spec.TargetCorpus = corpus
	in.Status.RolledForVersion = rolledFor
	return in
}

// The value on the agents' pod template changes exactly when a package is
// upgraded -- that is what rolls them, and nothing else may.
func TestPackageRollout(t *testing.T) {
	cases := []struct {
		name     string
		installs []accv1alpha1.AccPackageInstall
		want     string
	}{
		{"no installs", nil, ""},
		{"a first install rolls nothing", []accv1alpha1.AccPackageInstall{
			rolloutInstall("@acc/mortgage-roles", "c", "")}, ""},
		{"an upgrade names the version", []accv1alpha1.AccPackageInstall{
			rolloutInstall("@acc/mortgage-roles", "c", "1.2.1")}, "@acc/mortgage-roles@1.2.1"},
		{"another corpus' install is not ours", []accv1alpha1.AccPackageInstall{
			rolloutInstall("@acc/mortgage-roles", "other", "1.2.1")}, ""},
		{"an untargeted install reaches every corpus", []accv1alpha1.AccPackageInstall{
			rolloutInstall("@acc/shared", "", "2.0.0")}, "@acc/shared@2.0.0"},
		{"several, in a stable order", []accv1alpha1.AccPackageInstall{
			rolloutInstall("@acc/zeta", "c", "3.0.0"),
			rolloutInstall("@acc/alpha", "c", "1.1.0"),
			rolloutInstall("@acc/never-upgraded", "c", "")}, "@acc/alpha@1.1.0,@acc/zeta@3.0.0"},
	}
	for _, tc := range cases {
		if got := collective.PackageRollout(tc.installs, "c"); got != tc.want {
			t.Errorf("%s: PackageRollout = %q, want %q", tc.name, got, tc.want)
		}
	}
}

"""``acc.deploy`` — checkout or cluster pod (proposal 056 §4.1), and what can be
changed from where (OpenSpec ``20260920-surfaces-detect-environment``)."""

from __future__ import annotations

import pytest

from acc import deploy


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ACC_DEPLOY_MODE", raising=False)
    monkeypatch.delenv("ACC_CORPUS_NAME", raising=False)
    monkeypatch.delenv("ACC_ENVIRONMENT", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("ACC_COLLECTIVE_IDS", raising=False)
    deploy._reset()
    yield
    deploy._reset()


def test_checkout_by_default():
    assert deploy.is_cluster() is False


@pytest.mark.parametrize("mode", ["k8s", "rhoai", "operator", "RHOAI"])
def test_cluster_deploy_modes(monkeypatch, mode):
    monkeypatch.setenv("ACC_DEPLOY_MODE", mode)
    assert deploy.is_cluster() is True


@pytest.mark.parametrize("mode", ["standalone", "edge", "dev", ""])
def test_checkout_deploy_modes(monkeypatch, mode):
    monkeypatch.setenv("ACC_DEPLOY_MODE", mode)
    assert deploy.is_cluster() is False


def test_operator_corpus_name_means_cluster(monkeypatch):
    """The webgui pod gets ACC_CORPUS_NAME but no ACC_DEPLOY_MODE
    (operator/internal/reconcilers/ui/webgui.go) — that alone is a pod."""
    monkeypatch.setenv("ACC_CORPUS_NAME", "demo")
    assert deploy.is_cluster() is True


def test_cached_until_reset(monkeypatch):
    assert deploy.is_cluster() is False
    monkeypatch.setenv("ACC_CORPUS_NAME", "demo")
    assert deploy.is_cluster() is False   # cached
    deploy._reset()
    assert deploy.is_cluster() is True


# ---------------------------------------------------------------------------
# environment() — detection order, the deployment, the capabilities
# ---------------------------------------------------------------------------


def test_standalone_has_every_capability():
    env = deploy.environment()
    assert env.kind == "standalone" and env.detected_by == "default"
    assert all(env.unavailable(cap) == "" for cap in deploy.CAPABILITIES)
    assert env.label() == "standalone"


def test_any_kubernetes_pod_is_a_cluster(monkeypatch):
    """A hand-rolled Deployment sets none of ACC's variables — it is still a
    pod with no checkout and no host to run acc-deploy.sh on."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    env = deploy.environment()
    assert env.cluster and env.detected_by == "KUBERNETES_SERVICE_HOST"


def test_the_explicit_word_wins_both_ways(monkeypatch):
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("ACC_ENVIRONMENT", "standalone")      # a CI pod that builds packages
    assert deploy.environment().kind == "standalone"
    deploy._reset()
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    monkeypatch.setenv("ACC_ENVIRONMENT", "cluster")
    assert deploy.environment().detected_by == "ACC_ENVIRONMENT"
    assert deploy.is_cluster() is True


def test_cluster_names_its_deployment(monkeypatch, tmp_path):
    (tmp_path / "namespace").write_text("wksp-user2\n", encoding="utf-8")
    monkeypatch.setenv("ACC_SERVICEACCOUNT_DIR", str(tmp_path))
    monkeypatch.setenv("ACC_DEPLOY_MODE", "rhoai")
    monkeypatch.setenv("ACC_CORPUS_NAME", "mortgage-agents-corpus")
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "mortgage-agents, other")
    env = deploy.environment()
    assert (env.namespace, env.corpus) == ("wksp-user2", "mortgage-agents-corpus")
    assert env.collectives == ("mortgage-agents", "other")
    assert env.label() == "cluster · wksp-user2 · mortgage-agents-corpus"


def test_every_capability_has_a_reason_that_names_the_truth(monkeypatch):
    monkeypatch.setenv("ACC_CORPUS_NAME", "demo")
    env = deploy.environment()
    for cap in deploy.CAPABILITIES:
        reason = env.unavailable(cap)
        assert reason, cap
        assert "{" not in reason, (cap, reason)               # formatted
        assert "acc-deploy.sh apply" not in reason            # no host advice
    assert "AgentCollective of corpus demo" in env.unavailable("agentset.write")
    assert "AccPackageInstall" in env.unavailable("package.install")
    assert "AccCatalog" in env.unavailable("catalog.write")
    assert env.unavailable("no.such.capability") == ""


def test_to_dict_is_what_a_surface_renders_from(monkeypatch):
    monkeypatch.setenv("ACC_CORPUS_NAME", "demo")
    d = deploy.environment().to_dict()
    assert d["cluster"] is True and d["kind"] == "cluster" and d["label"] == "cluster · demo"
    assert set(d["capabilities"]) == set(deploy.CAPABILITIES)
    assert d["capabilities"]["role.write"] == {
        "available": False, "reason": deploy.environment().unavailable("role.write")}

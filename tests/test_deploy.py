"""``acc.deploy.is_cluster`` — checkout or cluster pod (proposal 056 §4.1)."""

from __future__ import annotations

import pytest

from acc import deploy


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ACC_DEPLOY_MODE", raising=False)
    monkeypatch.delenv("ACC_CORPUS_NAME", raising=False)
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

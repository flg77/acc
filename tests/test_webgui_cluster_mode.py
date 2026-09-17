"""Proposal 056 Phase 1 — the WebGUI in a cluster pod tells the truth.

Routes under ``ACC_DEPLOY_MODE=k8s`` with what a pod actually has: a
read-only roles mount, no ``regulatory_layer/``, a catalog that cannot be
fetched, no checkout to stage an install or a workspace catalog in.  Every
checkout-only affordance answers with a reason — never ``[]``, a 500 or a
toast that lies.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("authlib")
pytest.importorskip("bcrypt")

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeObserver:
    def __init__(self, nats_url, collective_id, update_queue, nkey_seed_path=None):
        self.collective_id = collective_id
        self.published = []

    async def connect(self):
        return None

    async def subscribe(self):
        return None

    async def close(self):
        return None

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


def _stage_pkg(catalog_dir: Path, scope: str, name: str, version: str) -> None:
    scope_dir = catalog_dir / scope
    scope_dir.mkdir(parents=True, exist_ok=True)
    pkg = scope_dir / f"{name}-{version}.accpkg"
    pkg.write_bytes(b"FAKE")
    pkg.with_suffix(".accpkg.sha256").write_text(
        hashlib.sha256(pkg.read_bytes()).hexdigest(), encoding="utf-8")


def _read_only(monkeypatch, root: Path) -> None:
    """Make *root* what the operator's ConfigMap mount is: not writable
    (``r-x``, so the 0444 files inside stay readable).

    ``os.chmod`` is the real thing on POSIX; Windows (and root on POSIX)
    report every directory writable through ``os.access``, so there the
    probe is patched to say what the mount would say.
    """
    os.chmod(root, stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
             | stat.S_IROTH | stat.S_IXOTH)
    if os.access(root, os.W_OK):
        real_access = os.access

        def _access(path, mode, *args, **kwargs):
            if mode & os.W_OK and str(path).startswith(str(root)):
                return False
            return real_access(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "access", _access)


@pytest.fixture
def pod(monkeypatch, tmp_path):
    """What the webgui pod has after #427: a 0444 roles mount, installed
    packs root, the operator's catalogs layer, no regulatory layer, no
    checkout — and ACC_DEPLOY_MODE=k8s."""
    import acc.tui.client as tui_client
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "mortgage-01")
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "none")
    monkeypatch.setenv("ACC_DEPLOY_MODE", "k8s")
    monkeypatch.setenv("ACC_CORPUS_NAME", "demo")

    roles_root = tmp_path / "etc-acc" / "roles"
    (roles_root / "assistant").mkdir(parents=True)
    (roles_root / "assistant" / "role.yaml").write_text(
        "role_definition:\n  purpose: guide\n  persona: concise\n", encoding="utf-8")
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    packages_root = tmp_path / "packages"
    packages_root.mkdir()
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(packages_root))
    _read_only(monkeypatch, roles_root)

    # catalogs: the operator's system layer — one reachable file catalog and
    # one https catalog nobody can reach from the pod.
    catalog_root = tmp_path / "catalog"
    _stage_pkg(catalog_root, "acc", "mortgage-roles", "1.2.0")
    signer = {"issuer": "https://token.actions.githubusercontent.com",
              "subject_pattern": ".*"}
    sys_cat = tmp_path / "catalogs.yaml"
    sys_cat.write_text(yaml.safe_dump({"catalogs": [
        {"id": "workshop-local", "tier": "trusted", "mode": "file",
         "path": str(catalog_root), "required_signer": signer, "priority": 200},
        {"id": "acc-canonical", "tier": "community", "mode": "https",
         "url": "https://catalog.example.invalid", "required_signer": signer,
         "priority": 100},
    ]}), encoding="utf-8")
    monkeypatch.setenv("ACC_SYSTEM_CATALOG", str(sys_cat))
    monkeypatch.setenv("ACC_USER_CATALOG", str(tmp_path / "nope.yaml"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    from acc.pkg import catalog as cat

    def _strict(catalog):
        raise cat.IndexFetchError(f"GET {catalog.url}/index.json: no route to host")

    monkeypatch.setattr(cat, "_fetch_index_https_strict", _strict)

    # no regulatory layer anywhere a pod would look
    import acc.governance_inventory as gi
    monkeypatch.delenv("ACC_REGULATORY_ROOT", raising=False)
    monkeypatch.setattr(gi, "CLUSTER_MOUNT", tmp_path / "etc-acc" / "regulatory_layer")
    monkeypatch.setattr(gi, "__file__", str(tmp_path / "fake" / "acc" / "governance_inventory.py"))

    yield {"roles_root": roles_root, "tmp": tmp_path, "workspace": workspace}
    # let tmp_path be cleaned up
    os.chmod(roles_root, stat.S_IRWXU)


@pytest.fixture
def client(pod):
    from acc.webgui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# where am I
# ---------------------------------------------------------------------------


def test_deploy_info_says_cluster(client):
    r = client.get("/api/deploy")
    assert r.status_code == 200
    assert r.json() == {"cluster": True, "deploy_mode": "k8s", "corpus_name": "demo"}


# ---------------------------------------------------------------------------
# Roles — read-only that says so
# ---------------------------------------------------------------------------


def test_roles_list_carries_the_reason(client, pod):
    rows = {r["role_id"]: r for r in client.get("/api/roles").json()}
    row = rows["assistant"]
    assert row["writable"] is False
    assert "read-only" in row["write_block_reason"]
    assert "ConfigMap" in row["write_block_reason"]


def test_roles_authoring_is_blocked_with_the_reason(client, pod):
    r = client.get("/api/roles/authoring")
    assert r.status_code == 200
    body = r.json()
    assert body["writable"] is False
    assert body["cluster"] is True
    assert body["roles_root"] == str(pod["roles_root"])
    assert "read-only" in body["write_block_reason"]


def test_role_create_on_read_only_root_is_409_not_500(client):
    r = client.post("/api/roles", json={
        "role_id": "newbie",
        "yaml_text": "role_definition:\n  purpose: p\n  persona: concise\n",
    })
    assert r.status_code == 409
    assert "read-only" in r.json()["detail"]


def test_role_yaml_put_on_read_only_root_is_409(client):
    r = client.put("/api/roles/assistant/yaml", json={
        "yaml_text": "role_definition:\n  purpose: p2\n  persona: concise\n",
    })
    assert r.status_code == 409
    assert "ConfigMap" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Compliance — a missing regulatory layer is an error with the paths tried
# ---------------------------------------------------------------------------


def test_governance_layers_missing_root_is_an_error_object(client, pod):
    r = client.get("/api/governance/layers")
    assert r.status_code == 200
    body = r.json()
    assert body["layers"] == []
    assert body["error"].startswith("no regulatory layer at ")
    assert str(pod["tmp"] / "etc-acc" / "regulatory_layer") in body["error"]
    assert "/etc/acc/regulatory_layer" in body["hint"]
    assert "ACC_REGULATORY_ROOT" in body["hint"]


def test_governance_layers_from_the_operator_mount(client, pod, monkeypatch):
    import acc.governance_inventory as gi
    mount = pod["tmp"] / "etc-acc" / "regulatory_layer"
    (mount / "category_a").mkdir(parents=True)
    (mount / "category_a" / "constitution.rego").write_text(
        "# Version: 0.17.13\n# A-001: no agent widens its own ceiling\n", encoding="utf-8")
    monkeypatch.setattr(gi, "CLUSTER_MOUNT", mount)
    body = client.get("/api/governance/layers").json()
    assert "error" not in body
    assert body["root"] == str(mount)
    layers = {l["category"]: l for l in body["layers"]}
    assert layers["A"]["rule_count"] == 1
    assert layers["A"]["rules"][0]["rule_id"] == "A-001"


def test_governance_layers_env_root(client, pod, monkeypatch):
    root = pod["tmp"] / "somewhere"
    (root / "category_b").mkdir(parents=True)
    (root / "category_b" / "b.rego").write_text("# B-001: setpoint\n", encoding="utf-8")
    monkeypatch.setenv("ACC_REGULATORY_ROOT", str(root))
    body = client.get("/api/governance/layers").json()
    assert body["root"] == str(root)
    assert {l["category"]: l["rule_count"] for l in body["layers"]} == {"A": 0, "B": 1, "C": 0}


# ---------------------------------------------------------------------------
# Marketplace — unreachable catalogs are rows, Install is the operator's
# ---------------------------------------------------------------------------


def test_available_reports_unreachable_catalog(client):
    body = client.get("/api/roles/available").json()
    assert [r["name"] for r in body["rows"]] == ["@acc/mortgage-roles"]
    assert body["cluster"] is True
    assert len(body["catalog_errors"]) == 1
    err = body["catalog_errors"][0]
    assert err["id"] == "acc-canonical"
    assert err["url"] == "https://catalog.example.invalid"
    assert "no route to host" in err["error"]


def test_install_in_a_pod_is_409(client):
    r = client.post("/api/roles/install", json={"name": "@acc/mortgage-roles"})
    assert r.status_code == 409
    assert "AccPackageInstall" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Catalogs — no workspace layer in a pod
# ---------------------------------------------------------------------------


def test_catalogs_hide_workspace_layer_and_are_read_only(client, pod):
    ws = pod["workspace"] / ".acc"
    ws.mkdir()
    (ws / "catalogs.yaml").write_text(yaml.safe_dump({"catalogs": [{
        "id": "scratch", "tier": "community", "mode": "https",
        "url": "https://scratch.example.invalid",
        "required_signer": {"issuer": "x", "subject_pattern": ".*"},
    }]}), encoding="utf-8")
    rows = client.get("/api/catalogs").json()
    assert "scratch" not in {r["id"] for r in rows}
    assert {r["layer"] for r in rows} <= {"default", "system", "user"}
    assert all(r["read_only"] for r in rows)


def test_catalog_add_in_a_pod_is_409(client):
    r = client.post("/api/catalogs", json={
        "catalog_id": "mine", "tier": "community", "mode": "https",
        "url": "https://mine.example.invalid", "issuer": "x", "subject_pattern": ".*",
    })
    assert r.status_code == 409
    assert "AccCatalog" in r.json()["detail"]


def test_catalog_remove_in_a_pod_is_409(client):
    r = client.delete("/api/catalogs/workshop-local")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Infuse — a role that is not running is said, not silently no-op'd
# ---------------------------------------------------------------------------


def test_infuse_not_running_role_says_so(client):
    client.app.state.hub._latest["mortgage-01"] = {
        "agents": {"loan_officer-1": {"role": "loan_officer", "state": "ACTIVE"}},
    }
    r = client.post("/api/infuse", json={
        "collective_id": "mortgage-01",
        "role_definition": {"id": "assistant", "purpose": "x"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "not_running"
    assert body["note"].startswith("assistant is not running in this collective")
    assert "AgentCollective" in body["note"]
    obs = client.app.state.hub.observer("mortgage-01")
    assert obs.published == []


def test_infuse_running_role_publishes(client):
    client.app.state.hub._latest["mortgage-01"] = {
        "agents": {"loan_officer-1": {"role": "loan_officer", "state": "ACTIVE"}},
    }
    r = client.post("/api/infuse", json={
        "collective_id": "mortgage-01",
        "role_definition": {"id": "loan_officer", "purpose": "x"},
    })
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    obs = client.app.state.hub.observer("mortgage-01")
    assert len(obs.published) == 1


def test_infuse_in_a_checkout_still_publishes(client, monkeypatch):
    """The checkout path is unchanged: no running-role gate."""
    from acc import deploy
    monkeypatch.setenv("ACC_DEPLOY_MODE", "standalone")
    monkeypatch.delenv("ACC_CORPUS_NAME", raising=False)
    deploy._reset()
    r = client.post("/api/infuse", json={
        "collective_id": "mortgage-01",
        "role_definition": {"id": "assistant", "purpose": "x"},
    })
    assert r.status_code == 200
    assert r.json()["status"] == "published"

"""033 WS-G Part 3 — ``execute_infuse_install`` (the EXECUTE-path installer).

An approved / auto-executed ``[PROPOSE_INFUSE:...]`` must ACTUALLY
install.  ``execute_infuse_install`` is the seam: parse the spec, resolve
against the catalog resolver, hand off to the installer.  These tests are
hermetic — the catalog+install stack (``fetch_and_install_closure``) is
stubbed, so nothing touches the network or the real registry.

Pinned behaviour:
* success → ok + installed_ref / install_path,
* idempotent re-install → already_satisfied (no error),
* resolve failure → clean ok=False result (never raises),
* malformed / empty spec → clean error,
* allow_unsigned passes through to the installer.
"""

from __future__ import annotations

from unittest.mock import patch

from acc.pkg.install_infuse import InfuseInstallResult, execute_infuse_install


# ---------------------------------------------------------------------------
# Fakes — mimic the FetchResult / InstallResult surface execute_* reads
# ---------------------------------------------------------------------------


def _fake_fetch_result(name, version, *, already=False):
    entry = type("E", (), {"name": name, "version": version})()
    install = type("InstallResult", (), {
        "entry": entry,
        "install_path": f"/var/lib/acc/packages/{name.strip('@').replace('/', '-')}-{version}",
        "was_already_installed": already,
    })()
    return type("FetchResult", (), {"install": install})()


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


def test_success_returns_installed_ref_and_path():
    captured = {}

    def fake_fetch(name, constraint, **kw):
        captured["name"] = name
        captured["constraint"] = constraint
        captured["allow_unsigned"] = kw.get("allow_unsigned")
        return _fake_fetch_result("@acc/coding-roles", "1.2.0")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        res = execute_infuse_install("@acc/coding-roles@^1.2")

    assert isinstance(res, InfuseInstallResult)
    assert res.ok is True
    assert res.name == "@acc/coding-roles"
    assert res.version == "1.2.0"
    assert res.installed_ref == "@acc/coding-roles@1.2.0"
    assert res.install_path.endswith("acc-coding-roles-1.2.0")
    assert res.already_satisfied is False
    assert res.error == ""
    # Spec was parsed into (name, constraint).
    assert captured["name"] == "@acc/coding-roles"
    assert captured["constraint"] == "^1.2"
    # Default is strict (signing floor on).
    assert captured["allow_unsigned"] is False


def test_bare_name_without_constraint_matches_anything():
    captured = {}

    def fake_fetch(name, constraint, **kw):
        captured["constraint"] = constraint
        return _fake_fetch_result("@acc/rag-roles", "0.3.1")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        res = execute_infuse_install("@acc/rag-roles")

    assert res.ok is True
    assert captured["constraint"] == ">=0.0.0"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_reinstall_is_already_satisfied():
    """A re-install of an already-present (name, version) is a no-op:
    ok=True, already_satisfied=True, no error."""
    with patch(
        "acc.pkg.fetch.fetch_and_install_closure",
        return_value=_fake_fetch_result("@acc/coding-roles", "1.0.0", already=True),
    ):
        res = execute_infuse_install("@acc/coding-roles@1.0.0")

    assert res.ok is True
    assert res.already_satisfied is True
    assert res.installed_ref == "@acc/coding-roles@1.0.0"
    assert res.error == ""


# ---------------------------------------------------------------------------
# Failure paths — never raise
# ---------------------------------------------------------------------------


def test_resolve_failure_returns_clean_error():
    from acc.pkg.fetch import CatalogResolutionFailed

    def fake_fetch(*a, **kw):
        raise CatalogResolutionFailed("no catalog advertises @acc/ghost")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        res = execute_infuse_install("@acc/ghost@^1.0")

    assert res.ok is False
    assert res.installed_ref == ""
    assert "CatalogResolutionFailed" in res.error
    assert "ghost" in res.error


def test_install_error_returns_clean_error():
    """A non-FetchError from the installer (e.g. content-hash mismatch) is
    still caught and surfaced as a clean result, not raised."""

    def fake_fetch(*a, **kw):
        raise RuntimeError("content hash mismatch")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        res = execute_infuse_install("@acc/coding-roles@^1.0")

    assert res.ok is False
    assert "RuntimeError" in res.error
    assert "content hash" in res.error


def test_empty_spec_returns_error_without_touching_installer():
    called = {"n": 0}

    def fake_fetch(*a, **kw):
        called["n"] += 1
        return _fake_fetch_result("x", "1")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        res = execute_infuse_install("   ")

    assert res.ok is False
    assert "empty" in res.error.lower()
    assert called["n"] == 0  # never reached the installer


def test_malformed_spec_returns_error():
    """A spec missing the @scope/ shape is rejected cleanly."""
    called = {"n": 0}

    def fake_fetch(*a, **kw):
        called["n"] += 1
        return _fake_fetch_result("x", "1")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        res = execute_infuse_install("not-a-scoped-name")

    assert res.ok is False
    assert "malformed" in res.error.lower()
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# allow_unsigned passthrough
# ---------------------------------------------------------------------------


def test_allow_unsigned_passes_through():
    captured = {}

    def fake_fetch(name, constraint, **kw):
        captured["allow_unsigned"] = kw.get("allow_unsigned")
        return _fake_fetch_result("@acc/coding-roles", "1.0.0")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        res = execute_infuse_install("@acc/coding-roles@1.0.0", allow_unsigned=True)

    assert res.ok is True
    assert captured["allow_unsigned"] is True

"""Tests for ``acc-cli collective pkg-install-direct`` (Stage 1.6b)."""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import pytest

from acc.cli.collective_cmd import register


def _run_cli(argv: list[str]) -> int:
    """Build an argparse tree, parse argv, dispatch the handler."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    register(sub)
    args = parser.parse_args(argv)
    return args.func(args)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_direct_install_happy_json(capsys):
    """Successful install emits the JSON shape the Go reconciler parses."""
    fake_install = type("I", (), {
        "entry": type("E", (), {"name": "@acc/coding-roles", "version": "1.2.0"})(),
        "install_path": "/var/lib/acc/packages/acc/coding-roles-1.2.0",
        "was_already_installed": False,
    })()
    fake_result = type("R", (), {"install": fake_install})()

    def fake_fetch(name, constraint, **kw):
        assert name == "@acc/coding-roles"
        assert constraint == "^1.2"
        assert kw.get("allow_unsigned") is False
        return fake_result

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        rc = _run_cli([
            "collective", "pkg-install-direct",
            "@acc/coding-roles@^1.2", "--json",
        ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["installed"][0]["installed"] == "@acc/coding-roles@1.2.0"
    assert payload["installed"][0]["was_already_installed"] is False
    assert payload["failed"] == []


def test_direct_install_propagates_allow_unsigned(capsys):
    """``--allow-unsigned`` reaches fetch_and_install."""
    captured: dict = {}

    def fake_fetch(name, constraint, **kw):
        captured["allow_unsigned"] = kw.get("allow_unsigned")
        from acc.pkg.fetch import FetchError
        raise FetchError("stub")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        _run_cli([
            "collective", "pkg-install-direct",
            "@acc/x@1.0.0", "--allow-unsigned", "--json",
        ])
    assert captured["allow_unsigned"] is True


def test_direct_install_idempotent_was_already_installed(capsys):
    """Re-install on matching content_sha256 surfaces was_already_installed."""
    fake_install = type("I", (), {
        "entry": type("E", (), {"name": "@acc/coding-roles", "version": "1.0.0"})(),
        "install_path": "/x",
        "was_already_installed": True,
    })()
    fake_result = type("R", (), {"install": fake_install})()

    with patch("acc.pkg.fetch.fetch_and_install_closure", return_value=fake_result):
        rc = _run_cli([
            "collective", "pkg-install-direct",
            "@acc/coding-roles@1.0.0", "--json",
        ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["installed"][0]["was_already_installed"] is True


# ---------------------------------------------------------------------------
# Failure paths the reconciler maps to Phase=Failed
# ---------------------------------------------------------------------------


def test_direct_install_fetch_error_returns_3(capsys):
    """FetchError -> exit 3 (EXIT_DEPS) + failed[] populated."""
    from acc.pkg.fetch import CatalogResolutionFailed

    def fake_fetch(*a, **kw):
        raise CatalogResolutionFailed("no catalog has it")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        rc = _run_cli([
            "collective", "pkg-install-direct",
            "@acc/missing@^1.0", "--json",
        ])
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["failed"]) == 1
    assert "no catalog has it" in payload["failed"][0]["error"]


def test_direct_install_malformed_spec_returns_1(capsys, monkeypatch):
    """Bad spec shape -> exit 1, no fetch attempted."""
    called = {"fetch": False}

    def fake_fetch(*a, **kw):
        called["fetch"] = True

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        rc = _run_cli([
            "collective", "pkg-install-direct",
            "not-a-valid-spec", "--json",
        ])
    assert rc == 1
    assert called["fetch"] is False


def test_direct_install_unexpected_exception_caught(capsys):
    """Non-FetchError exceptions don't crash the CLI; reported as failure."""
    def fake_fetch(*a, **kw):
        raise RuntimeError("boom")

    with patch("acc.pkg.fetch.fetch_and_install_closure", side_effect=fake_fetch):
        rc = _run_cli([
            "collective", "pkg-install-direct",
            "@acc/x@1.0.0", "--json",
        ])
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["failed"][0]["error"].startswith("RuntimeError:")


# ---------------------------------------------------------------------------
# Output shape matches pkg-install (Go reconciler reuses one parser)
# ---------------------------------------------------------------------------


def test_output_shape_matches_pkg_install(capsys):
    """The output JSON has the same top-level keys (`installed`,
    `failed`) as `pkg-install` so the Go reconciler's parser doesn't
    need a branch per subcommand.
    """
    fake_install = type("I", (), {
        "entry": type("E", (), {"name": "@acc/x", "version": "1.0.0"})(),
        "install_path": "/x",
        "was_already_installed": False,
    })()
    fake_result = type("R", (), {"install": fake_install})()
    with patch("acc.pkg.fetch.fetch_and_install_closure", return_value=fake_result):
        _run_cli([
            "collective", "pkg-install-direct",
            "@acc/x@1.0.0", "--json",
        ])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"installed", "failed"}
    assert set(payload["installed"][0]) == {
        "spec", "installed", "install_path", "was_already_installed",
    }

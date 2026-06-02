"""Tests for the webgui htpasswd-path startup check.

Lighthouse smoke on 2026-06-02 surfaced a silent 401 because the
operator's ``ACC_WEBGUI_HTPASSWD_PATH`` env pointed at the *host* path
(`/home/flg/...`) while the file was mounted at the *in-container*
path (`/app/...`).  `resolve_auth_config` now logs a loud WARNING at
startup so the operator sees the misconfig in `podman logs` before
the first 401 lands.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from acc.webgui.auth import MODE_HTPASSWD, resolve_auth_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in [
        "ACC_WEBGUI_AUTH_MODE", "ACC_WEBGUI_HTPASSWD_PATH",
        "ACC_WEBGUI_SESSION_SECRET", "ACC_WEBGUI_OPERATOR_USERS",
    ]:
        monkeypatch.delenv(k, raising=False)


class TestHtpasswdPathWarnings:
    def test_warns_when_path_does_not_exist(
        self, monkeypatch, caplog,
    ) -> None:
        monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "htpasswd")
        monkeypatch.setenv(
            "ACC_WEBGUI_HTPASSWD_PATH", "/nonexistent/acc-webgui.htpasswd"
        )
        monkeypatch.setenv("ACC_WEBGUI_SESSION_SECRET", "x")
        with caplog.at_level(logging.WARNING, logger="acc.webgui"):
            cfg = resolve_auth_config()
        assert cfg.mode == MODE_HTPASSWD
        assert any(
            "does not exist" in rec.message
            for rec in caplog.records
        ), [r.message for r in caplog.records]

    def test_warns_when_path_unset(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "htpasswd")
        monkeypatch.setenv("ACC_WEBGUI_SESSION_SECRET", "x")
        # ACC_WEBGUI_HTPASSWD_PATH intentionally unset.
        with caplog.at_level(logging.WARNING, logger="acc.webgui"):
            resolve_auth_config()
        assert any(
            "HTPASSWD_PATH is unset" in rec.message
            for rec in caplog.records
        )

    def test_silent_when_path_exists(
        self, monkeypatch, caplog, tmp_path: Path,
    ) -> None:
        f = tmp_path / "acc-webgui.htpasswd"
        f.write_text("flg:$2y$05$bwKWSVRq57JgarQOppbR/u.jnGvW6nb4cLdoAv64b68Hh0hPuDMnq\n")
        monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "htpasswd")
        monkeypatch.setenv("ACC_WEBGUI_HTPASSWD_PATH", str(f))
        monkeypatch.setenv("ACC_WEBGUI_SESSION_SECRET", "x")
        with caplog.at_level(logging.WARNING, logger="acc.webgui"):
            cfg = resolve_auth_config()
        # No path-related warning.
        for rec in caplog.records:
            assert "does not exist" not in rec.message, rec.message
            assert "HTPASSWD_PATH is unset" not in rec.message, rec.message
        assert cfg.htpasswd_path == str(f)

    def test_no_warning_when_mode_is_not_htpasswd(
        self, monkeypatch, caplog,
    ) -> None:
        monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "none")
        monkeypatch.setenv(
            "ACC_WEBGUI_HTPASSWD_PATH", "/totally/missing"
        )
        with caplog.at_level(logging.WARNING, logger="acc.webgui"):
            resolve_auth_config()
        for rec in caplog.records:
            assert "does not exist" not in rec.message, rec.message

"""``ACC_WEBGUI_STATIC_DIR`` overrides where ``create_app()`` serves the SPA
from (``acc.webgui.app._static_dir``).

Exists for the Playwright e2e suite (``webgui/e2e/``), which points it at a
`vite build` of `webgui/dist` rather than the image's baked-in
``acc/webgui/static`` — so `npm run build` + this override reproduce exactly
what a container serves, without copying build output into the source tree.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from acc.webgui.app import _static_dir  # noqa: E402


def test_default_is_the_bundled_static_dir(monkeypatch):
    monkeypatch.delenv("ACC_WEBGUI_STATIC_DIR", raising=False)
    # The checkout carries no compiled assets — absent, not a guess.
    assert _static_dir() is None


def test_override_is_used_when_the_directory_exists(tmp_path, monkeypatch):
    built = tmp_path / "dist"
    built.mkdir()
    (built / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setenv("ACC_WEBGUI_STATIC_DIR", str(built))
    assert _static_dir() == str(built)


def test_override_to_a_missing_directory_is_absent_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_WEBGUI_STATIC_DIR", str(tmp_path / "does-not-exist"))
    assert _static_dir() is None


def test_the_app_serves_index_html_from_the_override(tmp_path, monkeypatch):
    built = tmp_path / "dist"
    built.mkdir()
    (built / "index.html").write_text("<title>e2e build</title>", encoding="utf-8")
    monkeypatch.setenv("ACC_WEBGUI_STATIC_DIR", str(built))
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "none")
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "sol-01")

    import acc.tui.client as tui_client

    class _FakeObserver:
        def __init__(self, *a, **kw): pass
        async def connect(self): return None
        async def subscribe(self): return None
        async def close(self): return None

    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)

    from fastapi.testclient import TestClient

    from acc.webgui.app import create_app
    with TestClient(create_app()) as client:
        r = client.get("/")
    assert r.status_code == 200 and "e2e build" in r.text

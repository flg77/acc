"""tools/mode_ab builds every non-built-in backend through ACC's plugin seam.

The A/B harness used to put one plugin's source directory on ``sys.path`` and
import it by name -- a pointer into a directory the public mirror does not
carry. An arm now names a backend plugin the way a role does: installed (an
``acc.llm_backends`` entry point) and permitted (``ACC_LLM_BACKEND_PLUGINS``),
with the seam's own refusals.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from pathlib import Path

import pytest

from acc.backends import plugins

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "mode_ab" / "mode_ab.py"


@pytest.fixture(scope="module")
def mode_ab():
    spec = importlib.util.spec_from_file_location("mode_ab_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Backend:
    async def complete(self, system, user, response_schema=None, cache_prefix=False):
        return {"content": "ok"}

    async def embed(self, text):
        return [0.0]


class _EntryPoint:
    def __init__(self, name):
        self.name = name
        self.value = "demo-plugin:build"
        self.dist = type("D", (), {"name": "demo-plugin", "version": "1.0", "__str__": lambda s: "demo-plugin 1.0"})()
        self.loaded = 0
        self.settings = None

    def load(self):
        self.loaded += 1

        def factory(settings):
            self.settings = settings
            return _Backend()
        return factory


@pytest.fixture
def installed(monkeypatch):
    point = _EntryPoint("demo")
    monkeypatch.setattr(importlib.metadata, "entry_points",
                        lambda *, group=None, **_: [point] if group == plugins.ENTRY_POINT_GROUP else [])
    return point


def test_an_openai_compat_arm_is_built_directly(mode_ab):
    from acc.backends.llm_openai_compat import OpenAICompatBackend

    backend = mode_ab.make_backend({"name": "a", "backend": "openai_compat",
                                    "base_url": "https://gw/v1", "model": "m"})
    assert isinstance(backend, OpenAICompatBackend)


def test_a_permitted_plugin_arm_goes_through_the_seam(mode_ab, installed, monkeypatch):
    monkeypatch.setenv(plugins.ALLOWLIST_VAR, "demo")
    backend = mode_ab.make_backend({"name": "p", "backend": "demo", "model": "big", "timeout_s": 60})
    assert isinstance(backend, _Backend)
    assert installed.settings["model"] == "big" and installed.settings["request_timeout_s"] == 60


def test_an_installed_but_unpermitted_plugin_is_refused_and_never_loaded(mode_ab, installed, monkeypatch):
    monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)
    with pytest.raises(SystemExit) as info:
        mode_ab.make_backend({"name": "p", "backend": "demo"})
    assert str(info.value).startswith("arm p: ")
    assert installed.loaded == 0, "installing is not consent: the plugin's code must not run"


def test_the_harness_points_at_no_plugin_directory():
    """Nothing promoted may point into a path the mirror withholds."""
    text = SOURCE.read_text(encoding="utf-8")
    assert '"plugins"' not in text and "plugins/" not in text

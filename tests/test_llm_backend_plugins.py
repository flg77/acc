"""The seam for LLM backends ACC does not ship.

The whole value of this seam is that it is *closed* by default, so most of what
is worth testing is refusals — and one property matters more than all of them:
a plugin that has not been allowlisted is never **imported**. Import-time code
in a third-party distribution runs before any check could reject it, so a seam
that decided after loading would not be a seam at all.

The rest pins what the operator is promised: a typo is still caught, a permitted
but absent plugin says so in those words, two distributions claiming one name is
refused rather than resolved by install order, and anything the seam does build
is wrapped by the prompt recorder exactly like a built-in backend.
"""

from __future__ import annotations

import importlib.metadata

import pytest

from acc.backends import plugins


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _Dist:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version

    def __str__(self) -> str:  # used by the ambiguity message
        return f"{self.name} {self.version}"


class _Backend:
    """Satisfies the LLMBackend protocol."""

    async def complete(self, system, user, response_schema=None, cache_prefix=False):
        return {"content": "ok", "usage": {"total_tokens": 1}}

    async def embed(self, text):
        return [0.0]


class _EntryPoint:
    def __init__(self, name, factory=None, *, dist_name="demo-plugin", version="1.2.3"):
        self.name = name
        self.value = f"{dist_name}:build"
        self.dist = _Dist(dist_name, version)
        self.loaded = 0
        self._factory = factory if factory is not None else (lambda settings: _Backend())

    def load(self):
        self.loaded += 1
        return self._factory


@pytest.fixture
def entry_points(monkeypatch):
    """Install a fake entry-point set; returns the mutable list."""
    points: list[_EntryPoint] = []

    def _fake(*, group=None, **_):
        assert group == plugins.ENTRY_POINT_GROUP
        return list(points)

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake)
    return points


def _permit(monkeypatch, *names: str) -> None:
    monkeypatch.setenv(plugins.ALLOWLIST_VAR, ",".join(names))


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ()),
        ("   ", ()),
        ("one", ("one",)),
        (" one , two ", ("one", "two")),
        ("one,,two", ("one", "two")),
    ],
)
def test_allowlist_parsing(raw, expected, monkeypatch):
    monkeypatch.setenv(plugins.ALLOWLIST_VAR, raw)
    assert plugins.allowlisted() == expected


def test_unset_allowlist_permits_nothing(monkeypatch):
    monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)
    assert plugins.allowlisted() == ()
    assert not plugins.is_allowlisted("anything")


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_installed_but_not_permitted_is_never_imported(entry_points, monkeypatch):
    """The property the whole design rests on.

    A distribution can be installed by a transitive dependency. Until the
    operator names it, its code must not run — not even its module body.
    """
    point = _EntryPoint("rogue")
    entry_points.append(point)
    monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)

    with pytest.raises(plugins.BackendPluginError) as excinfo:
        plugins.build("rogue", {})

    assert point.loaded == 0, "a refused plugin was imported anyway"
    assert plugins.ALLOWLIST_VAR in str(excinfo.value)


def test_refusal_names_what_is_permitted(entry_points, monkeypatch):
    entry_points.append(_EntryPoint("rogue"))
    _permit(monkeypatch, "something_else")

    with pytest.raises(plugins.BackendPluginError, match="something_else"):
        plugins.build("rogue", {})


def test_permitted_but_not_installed_says_so(entry_points, monkeypatch):
    _permit(monkeypatch, "absent")
    with pytest.raises(plugins.BackendPluginError) as excinfo:
        plugins.build("absent", {})
    message = str(excinfo.value)
    assert "no installed distribution" in message
    assert plugins.ENTRY_POINT_GROUP in message


def test_two_distributions_claiming_one_name_are_refused(entry_points, monkeypatch):
    """Install order must never decide which code sees the prompts."""
    entry_points.append(_EntryPoint("dup", dist_name="alpha"))
    entry_points.append(_EntryPoint("dup", dist_name="beta"))
    _permit(monkeypatch, "dup")

    with pytest.raises(plugins.BackendPluginError) as excinfo:
        plugins.build("dup", {})
    assert "more than one" in str(excinfo.value)


def test_factory_returning_a_non_backend_is_refused(entry_points, monkeypatch):
    entry_points.append(_EntryPoint("bad", factory=lambda settings: object()))
    _permit(monkeypatch, "bad")

    with pytest.raises(plugins.BackendPluginError, match="LLMBackend protocol"):
        plugins.build("bad", {})


def test_factory_raising_is_wrapped_not_propagated(entry_points, monkeypatch):
    def _boom(settings):
        raise RuntimeError("no claude here")

    entry_points.append(_EntryPoint("boom", factory=_boom))
    _permit(monkeypatch, "boom")

    with pytest.raises(plugins.BackendPluginError, match="no claude here"):
        plugins.build("boom", {})


def test_load_failure_is_wrapped(entry_points, monkeypatch):
    point = _EntryPoint("broken")
    point.load = lambda: (_ for _ in ()).throw(ImportError("missing module"))  # type: ignore[method-assign]
    entry_points.append(point)
    _permit(monkeypatch, "broken")

    with pytest.raises(plugins.BackendPluginError, match="missing module"):
        plugins.build("broken", {})


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_permitted_and_installed_builds(entry_points, monkeypatch):
    seen: list[dict] = []
    point = _EntryPoint("demo", factory=lambda settings: seen.append(settings) or _Backend())
    entry_points.append(point)
    _permit(monkeypatch, "demo")

    backend = plugins.build("demo", {"model": "m"})

    assert isinstance(backend, _Backend)
    assert point.loaded == 1
    assert seen == [{"model": "m"}]


def test_discovered_reads_metadata_without_importing(entry_points):
    point = _EntryPoint("demo", dist_name="acme-llm-backend", version="0.1.0")
    entry_points.append(point)

    assert plugins.discovered() == {"demo": "acme-llm-backend 0.1.0"}
    assert point.loaded == 0


def test_settings_carry_the_universal_fields_only():
    class _LLM:
        model = "sonnet"
        base_url = "https://example.invalid"
        api_key_env = "SOME_KEY"
        context_window = 4096
        request_timeout_s = 90
        max_retries = 2
        embedding_model_path = "/app/models/x"
        enable_prompt_cache = True
        # Legacy per-backend fields a plugin has no business reading.
        anthropic_model = "claude-sonnet-4-6"
        ollama_base_url = "http://localhost:11434"

    settings = plugins.settings_from(_LLM())

    assert settings == {
        "model": "sonnet",
        "base_url": "https://example.invalid",
        "api_key_env": "SOME_KEY",
        "context_window": 4096,
        "request_timeout_s": 90,
        "max_retries": 2,
        "embedding_model_path": "/app/models/x",
        "enable_prompt_cache": True,
    }
    assert "anthropic_model" not in settings
    assert "ollama_base_url" not in settings


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_builtin_backends_still_validate(monkeypatch):
    from acc.config import BUILTIN_LLM_BACKENDS, LLMConfig

    monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)
    for name in BUILTIN_LLM_BACKENDS:
        assert LLMConfig(backend=name).backend == name


def test_a_typo_is_still_rejected(monkeypatch):
    """Widening the field for plugins must not cost the typo check."""
    from acc.config import LLMConfig

    monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)
    with pytest.raises(Exception, match="anthropc"):
        LLMConfig(backend="anthropc")


def test_backend_choices_still_guard_and_offer(monkeypatch):
    """``choices`` drives five refusals and two dropdowns, not just the docs.

    ``config set``, ``profile apply``, the setup wizard and the web GUI all read
    it. Widening the annotation emptied it once; this is the pin that the
    built-ins are still offered and a permitted plugin joins them.
    """
    from acc import configschema as cs

    try:
        monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)
        cs.schema(refresh=True)
        assert set(cs.by_path()["llm.backend"].choices) == {
            "ollama", "anthropic", "vllm", "llama_stack", "openai_compat",
        }

        _permit(monkeypatch, "acme_backend")
        cs.schema(refresh=True)  # the schema is process-cached; see _choices_for
        assert "acme_backend" in cs.by_path()["llm.backend"].choices
    finally:
        monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)
        cs.schema(refresh=True)


def test_a_permitted_plugin_name_validates_without_being_installed(monkeypatch):
    """Config validation and availability are different faults.

    A config file is validated in places the plugin is legitimately absent, so
    "you meant this" must not be conflated with "it is not here".
    """
    from acc.config import LLMConfig

    _permit(monkeypatch, "acme_backend")
    assert LLMConfig(backend="acme_backend").backend == "acme_backend"


# ---------------------------------------------------------------------------
# DS-01: a plugin cannot reach a model unrecorded
# ---------------------------------------------------------------------------


def test_plugin_backends_are_wrapped_by_the_prompt_recorder(entry_points, monkeypatch):
    """The invariant a third-party backend must not be able to opt out of.

    The plugin branch lives inside ``_build_llm_backend_unrecorded``, so the
    factory's wrapper covers it. If the branch ever moves up into
    ``build_llm_backend`` itself, this fails — which is the point.
    """
    from acc import config as config_mod
    from acc.prompt_record import _RecordingLLMBackend

    entry_points.append(_EntryPoint("demo"))
    _permit(monkeypatch, "demo")

    class _Cfg:
        class llm:  # noqa: N801 - a stand-in for the pydantic model
            backend = "demo"
            model = "m"
            base_url = ""
            api_key_env = ""
            context_window = 0
            request_timeout_s = 120
            max_retries = 3
            embedding_model_path = ""
            enable_prompt_cache = False

    built = config_mod.build_llm_backend(_Cfg())
    assert isinstance(built, _RecordingLLMBackend)


def test_cli_builder_reaches_plugins_too(entry_points, monkeypatch):
    """`acc-cli llm test` is how an operator proves a fresh plugin answers."""
    from acc.cli import llm_cmd
    from acc.prompt_record import _RecordingLLMBackend

    entry_points.append(_EntryPoint("demo"))
    _permit(monkeypatch, "demo")

    class _Cfg:
        class llm:  # noqa: N801
            backend = "demo"
            model = "m"
            base_url = ""
            api_key_env = ""
            context_window = 0
            request_timeout_s = 120
            max_retries = 3
            embedding_model_path = ""
            enable_prompt_cache = False
            ollama_base_url = ""
            ollama_model = ""
            anthropic_model = ""
            vllm_inference_url = ""
            llama_stack_url = ""

    built = llm_cmd._build_llm_only(_Cfg())
    assert isinstance(built, _RecordingLLMBackend)


def test_unknown_backend_without_any_plugin_still_raises(monkeypatch):
    from acc import config as config_mod

    monkeypatch.delenv(plugins.ALLOWLIST_VAR, raising=False)

    class _Cfg:
        class llm:  # noqa: N801
            backend = "nonsense"
            model = ""
            base_url = ""
            api_key_env = ""
            context_window = 0
            request_timeout_s = 120
            max_retries = 3
            embedding_model_path = ""
            enable_prompt_cache = False

    with pytest.raises(plugins.BackendPluginError):
        config_mod._build_llm_backend_unrecorded(_Cfg())

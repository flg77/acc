"""LLM backends ACC does not ship, and the two acts it takes to load one.

ACC's five backends live in this package and are part of the signed image. This
module is the one seam through which a backend that is **not** in the image can
be reached — and it exists because some model access cannot honestly be shipped
in core at all.

The case that forced it is a subscription transport: a user's own paid plan with
a model provider, driven through that provider's own client. Every such
provider's terms put the account, the tier and the liability with the *person
who signed in* (backlog ``20-backlog/Acc-Subscription-access/`` SA-00). A
transport ACC ships in the image is a transport ACC is offering; a transport the
operator installs deliberately is one they have chosen. That difference is
legal, not cosmetic, and it is why this seam is narrow, off by default, and not
a general extension API (see ``20-backlog/openclaw/OC-01`` — code-bearing packs
are a larger, separate decision, and the LLM backend is the seam OC-01 argued
should be opened *last*, because it sees every prompt and chooses where it goes).

**Two acts, never one.** Installing a distribution that advertises a backend is
not consent to run it:

1. the distribution is installed on the host, advertising an entry point in the
   ``acc.llm_backends`` group;
2. the operator names it in ``ACC_LLM_BACKEND_PLUGINS``.

A plugin that is installed but not named is discovered, reported by
``acc-cli doctor``, and **not loaded**. The import itself never happens, which
matters: import-time code in a third-party distribution would run before any
check could refuse it. Nothing here imports a plugin module until its name has
cleared the allowlist.

**What the seam deliberately does not move.** The governance spine stays where
it is. A plugin backend is built through the same two wrapped factories as every
built-in one (``acc.config.build_llm_backend`` and
``acc.cli.llm_cmd._build_llm_only``), so DS-01's "model-visible means logged"
invariant covers it without the plugin doing anything — and without the plugin
being able to opt out. Failover, zone policy, secret scoping and the category
gate sit above the backend and are untouched by what implements it.

**What it cannot promise.** A plugin runs in the agent's process with the
agent's privileges. This seam decides *whether* third-party code loads, not what
it does once loaded. That is the honest limit, it is why the default is empty,
and it is why the allowlist is an operator act rather than a config file an
agent could write.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Mapping

from acc.backends import LLMBackend

logger = logging.getLogger("acc.backends.plugins")

__all__ = [
    "ENTRY_POINT_GROUP",
    "ALLOWLIST_VAR",
    "BackendPluginError",
    "allowlisted",
    "discovered",
    "is_allowlisted",
    "build",
    "settings_from",
]

#: Entry-point group a distribution advertises an LLM backend under.
#: A plugin's ``pyproject.toml`` declares::
#:
#:     [project.entry-points."acc.llm_backends"]
#:     my_backend = "my_acc_backend:build"
#:
#: The entry-point *name* is the value that goes in ``llm.backend`` /
#: ``models.yaml``'s ``backend:``; the object it points at is the factory.
ENTRY_POINT_GROUP = "acc.llm_backends"

#: Comma-separated backend names the operator permits. Empty (the default)
#: means no third-party backend loads, however many are installed.
ALLOWLIST_VAR = "ACC_LLM_BACKEND_PLUGINS"


class BackendPluginError(ValueError):
    """A named plugin backend could not be loaded.

    Always fatal, never retryable: a missing, refused or malformed plugin is a
    deployment fault, and retrying it burns a failover chain against a
    condition no retry can change.

    A ``ValueError`` on purpose. Before this seam existed, an unusable
    ``llm.backend`` raised ``ValueError`` from the factory, and callers — and
    ``tests/test_build_backends.py`` — are entitled to keep catching that. The
    subclass keeps the old contract while letting anything that cares about the
    plugin case specifically catch the narrower type.
    """


def allowlisted(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Backend names the operator has permitted, in declaration order."""
    raw = (environ if environ is not None else os.environ).get(ALLOWLIST_VAR, "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def is_allowlisted(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """Is *name* permitted?  Says nothing about whether it is installed."""
    return bool(name) and name in allowlisted(environ)


def discovered() -> dict[str, str]:
    """Installed backend names → the distribution that advertises each.

    Reads entry-point *metadata* only; no plugin module is imported. Used by
    ``acc-cli doctor`` to tell "not installed" apart from "installed but not
    allowlisted", which are the two failures an operator confuses.
    """
    try:
        from importlib.metadata import entry_points  # noqa: PLC0415
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib on 3.12
        return {}
    found: dict[str, str] = {}
    try:
        points = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # pragma: no cover - broken metadata on the host
        logger.warning("backend plugins: cannot read entry points (%s)", exc)
        return {}
    for point in points:
        dist = getattr(point, "dist", None)
        name = getattr(dist, "name", "") or "unknown distribution"
        version = getattr(dist, "version", "") or "?"
        found[point.name] = f"{name} {version}"
    return found


def _factory(name: str, environ: Mapping[str, str] | None = None) -> Callable[..., Any]:
    """Resolve *name* to its factory, refusing before any import happens."""
    if not is_allowlisted(name, environ):
        permitted = ", ".join(allowlisted(environ)) or "(none)"
        raise BackendPluginError(
            f"LLM backend {name!r} is not a built-in and is not permitted. "
            f"{ALLOWLIST_VAR} currently permits: {permitted}. "
            f"Add {name!r} to {ALLOWLIST_VAR} to allow it; installing the "
            f"distribution alone is deliberately not enough."
        )
    try:
        from importlib.metadata import entry_points  # noqa: PLC0415

        points = [p for p in entry_points(group=ENTRY_POINT_GROUP) if p.name == name]
    except Exception as exc:
        raise BackendPluginError(
            f"LLM backend {name!r}: cannot read entry points ({exc})"
        ) from exc
    if not points:
        installed = ", ".join(sorted(discovered())) or "(none)"
        raise BackendPluginError(
            f"LLM backend {name!r} is permitted by {ALLOWLIST_VAR} but no "
            f"installed distribution advertises it in the {ENTRY_POINT_GROUP!r} "
            f"group. Installed: {installed}."
        )
    if len(points) > 1:
        # Two distributions claiming one name is ambiguous, and picking one
        # silently would make which code saw the prompts depend on install
        # order.  Refuse and name them.
        dists = ", ".join(sorted(str(getattr(p, "dist", None)) for p in points))
        raise BackendPluginError(
            f"LLM backend {name!r} is advertised by more than one installed "
            f"distribution ({dists}); uninstall all but one."
        )
    try:
        return points[0].load()
    except Exception as exc:
        raise BackendPluginError(
            f"LLM backend {name!r}: loading {points[0].value!r} failed ({exc})"
        ) from exc


def settings_from(llm: Any) -> dict[str, Any]:
    """The subset of ``LLMConfig`` a plugin is given, as a plain dict.

    A dict rather than the Pydantic model on purpose: a plugin that imported
    ``acc.config`` would be pinned to ACC's internals and would break on a
    field rename it has no way to see coming. This mapping is the contract, and
    it carries the universal fields only — a plugin has no business reading the
    per-backend legacy ones.

    Values, not names, are absent by design in one case: ``api_key_env`` is the
    *name* of the variable holding a key, exactly as every built-in backend
    receives it. Nothing here reads a credential.
    """
    return {
        "model": getattr(llm, "model", "") or "",
        "base_url": getattr(llm, "base_url", "") or "",
        "api_key_env": getattr(llm, "api_key_env", "") or "",
        "context_window": int(getattr(llm, "context_window", 0) or 0),
        "request_timeout_s": int(getattr(llm, "request_timeout_s", 120) or 120),
        "max_retries": int(getattr(llm, "max_retries", 3) or 3),
        "embedding_model_path": getattr(llm, "embedding_model_path", "") or "",
        "enable_prompt_cache": bool(getattr(llm, "enable_prompt_cache", False)),
    }


def build(
    name: str,
    settings: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> LLMBackend:
    """Build the plugin backend *name*, or raise :class:`BackendPluginError`.

    The returned object is checked against the :class:`~acc.backends.LLMBackend`
    protocol before it is handed back. A plugin missing ``complete`` or
    ``embed`` would otherwise fail deep inside a task, with a traceback that
    names ACC rather than the plugin.
    """
    factory = _factory(name, environ)
    try:
        backend = factory(dict(settings))
    except BackendPluginError:
        raise
    except Exception as exc:
        raise BackendPluginError(
            f"LLM backend {name!r}: its factory raised ({exc})"
        ) from exc
    if not isinstance(backend, LLMBackend):
        raise BackendPluginError(
            f"LLM backend {name!r}: factory returned {type(backend).__name__}, "
            "which does not satisfy the LLMBackend protocol (needs async "
            "complete() and embed())."
        )
    logger.info("backend plugins: loaded %r", name)
    return backend

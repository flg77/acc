"""Declared schema for ACC's configuration surfaces.

ACC's runtime configuration lives in five per-host files.  Four of them
already have a Pydantic model that is the *real* contract — the thing the
runtime actually validates against:

===================  ==========================================
``acc-config.yaml``  :class:`acc.config.ACCConfig`
``models.yaml``      :class:`acc.models.ModelRegistry`
``collective.yaml``  :class:`acc.collective.CollectiveSpec`
``catalogs.yaml``    :class:`acc.pkg.catalog.CatalogFile`
``.env``             *(no model — key names declared below)*
===================  ==========================================

This module **derives** the schema from those models rather than restating
them.  A second hand-maintained description would drift from the runtime the
first time someone adds a field, and a schema that disagrees with the runtime
is worse than no schema: it would validate writes against a contract nothing
enforces.  Deriving also means ``config check`` reports options a new release
added for free, with no per-release curation step.

``.env`` is the exception and is declared by hand, because it has no model.
Only key **names** are described.  Values are never read into the schema and
:mod:`acc.configstore` refuses to write them — secret material stays with the
operator (see the ``secret`` flag, which every surface must honour before
printing anything).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal, get_args, get_origin

# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigFile:
    """One configuration surface.

    Attributes:
        id: stable identifier used on the CLI (``config path <id>``).
        filename: repo-relative default filename.
        env_var: environment variable that overrides the location.
        model: dotted import path of the Pydantic root model, or ``""``
            for ``.env`` which has none.
        writable: whether ``config set`` may write this file.  ``.env`` is
            False on purpose — it is the secret-bearing surface.
        secret_bearing: file may contain secret material; never print
            values from it wholesale.
    """

    id: str
    filename: str
    env_var: str
    model: str
    writable: bool = True
    secret_bearing: bool = False


FILES: tuple[ConfigFile, ...] = (
    ConfigFile(
        id="acc-config",
        filename="acc-config.yaml",
        env_var="ACC_CONFIG_PATH",
        model="acc.config:ACCConfig",
    ),
    ConfigFile(
        id="models",
        filename="models.yaml",
        env_var="ACC_MODELS_PATH",
        model="acc.models:ModelRegistry",
    ),
    ConfigFile(
        id="collective",
        filename="collective.yaml",
        env_var="ACC_COLLECTIVE_PATH",
        model="acc.collective:CollectiveSpec",
    ),
    ConfigFile(
        id="catalogs",
        filename="catalogs.yaml",
        env_var="ACC_CATALOGS_PATH",
        model="acc.pkg.catalog:CatalogFile",
    ),
    ConfigFile(
        id="env",
        filename=".env",
        env_var="ACC_ENV_PATH",
        model="",
        writable=False,
        secret_bearing=True,
    ),
)

_BY_ID = {f.id: f for f in FILES}


def file_by_id(file_id: str) -> ConfigFile:
    """The :class:`ConfigFile` with this id.

    Raises:
        KeyError: if *file_id* is not a known surface.
    """
    try:
        return _BY_ID[file_id]
    except KeyError:
        raise KeyError(
            f"unknown config file {file_id!r}; known: {', '.join(sorted(_BY_ID))}"
        ) from None


def resolve_path(file_id: str, *, repo_root: Path | None = None) -> Path:
    """Locate a configuration file on this host.

    Precedence mirrors the existing loaders (see :func:`acc.models.models_path`):
    the file's env var wins, then the live per-host file, then the shipped
    ``.example`` template.  The template fallback matters because all five
    files are gitignored as of v0.7.0, so a fresh clone has only templates —
    without it, ``config show`` on a clean checkout would report every key
    missing rather than showing the release's defaults.
    """
    spec = file_by_id(file_id)
    raw = os.environ.get(spec.env_var, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    live = root / spec.filename
    if live.is_file():
        return live
    example = root / f"{spec.filename}.example"
    if example.is_file():
        return example
    return live


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------

#: Substrings that mark a key as secret-bearing.  Matched against the leaf
#: name so a nested ``security.arbiter_signing_key`` is caught as readily as a
#: top-level one.  Deliberately broad: a false positive costs a redacted
#: display, a false negative prints a credential.
_SECRET_MARKERS = ("password", "secret", "token", "signing_key", "api_key")


def _is_secret(dotted: str) -> bool:
    leaf = dotted.rsplit(".", 1)[-1].lower()
    # A ``*_env`` key names an environment variable; it does not carry the
    # value.  Redacting it would hide which variable is configured while
    # protecting nothing — the secret lives in the environment, not here.
    if leaf.endswith("_env"):
        return False
    return any(m in leaf for m in _SECRET_MARKERS)


@dataclass(frozen=True)
class Key:
    """A single configuration key.

    Attributes:
        path: dotted path, unique across all files.
        file: owning :class:`ConfigFile` id.
        type: rendered type name (``str``, ``int``, ``list[str]``, ...).
        default: the model's default, or ``None`` when required.
        required: no default — the file must supply it.
        choices: permitted values for a ``Literal`` field.
        secret: never print the value.
        dynamic: an operator-keyed mapping (``role_models``, ``models``)
            whose children are data, not schema.
        description: first line of the field's description.
    """

    path: str
    file: str
    type: str
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] = ()
    secret: bool = False
    dynamic: bool = False
    description: str = ""


def _type_name(annotation: Any) -> str:
    """Render an annotation compactly for display."""
    origin = get_origin(annotation)
    if origin is Literal:
        return "enum"
    if annotation is type(None):
        return "none"
    if origin in (list, tuple, set):
        args = get_args(annotation)
        inner = _type_name(args[0]) if args else "any"
        return f"list[{inner}]"
    if origin is dict:
        return "map"
    if hasattr(annotation, "__name__"):
        return str(annotation.__name__)
    args = [a for a in get_args(annotation) if a is not type(None)]
    if len(args) == 1:
        return _type_name(args[0])
    if args:
        return " | ".join(_type_name(a) for a in args)
    return str(annotation)


def _default_of(fieldinfo: Any) -> tuple[Any, bool]:
    """Return ``(default, required)`` for a Pydantic FieldInfo."""
    from pydantic_core import PydanticUndefined  # noqa: PLC0415

    if fieldinfo.default is not PydanticUndefined:
        return fieldinfo.default, False
    factory = getattr(fieldinfo, "default_factory", None)
    if factory is not None:
        try:
            return factory(), False
        except Exception:
            return None, False
    return None, True


def _import_model(dotted: str) -> Any:
    module_name, _, attr = dotted.partition(":")
    import importlib  # noqa: PLC0415

    return getattr(importlib.import_module(module_name), attr)


def _is_model_sequence(annotation: Any) -> bool:
    """True for ``list[SomeModel]`` — operator data, not schema."""
    from pydantic import BaseModel  # noqa: PLC0415

    if get_origin(annotation) not in (list, tuple, set):
        return False
    args = get_args(annotation)
    return bool(args and isinstance(args[0], type) and issubclass(args[0], BaseModel))


def _walk_model(model: Any, prefix: str, file_id: str) -> Iterator[Key]:
    """Yield a :class:`Key` per field, recursing into nested models."""
    from pydantic import BaseModel  # noqa: PLC0415

    for name, info in model.model_fields.items():
        dotted = f"{prefix}{name}"
        annotation = info.annotation
        nested = (
            annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel)
            else None
        )
        if nested is not None:
            # A nested model is a namespace, not a leaf.  Recurse; do not emit
            # a key for the container itself — `set agent.role` is meaningful,
            # `set agent` is not.
            yield from _walk_model(nested, f"{dotted}.", file_id)
            continue

        default, required = _default_of(info)
        origin = get_origin(annotation)
        choices: tuple[str, ...] = ()
        if origin is Literal:
            choices = tuple(str(a) for a in get_args(annotation))
        # A mapping or a list of models is operator data, not schema: its
        # children are named by the operator (role names, model ids), so the
        # schema describes the container and stops there.
        dynamic = origin is dict or _is_model_sequence(annotation)

        yield Key(
            path=dotted,
            file=file_id,
            type=_type_name(annotation),
            default=default,
            required=required,
            choices=choices,
            secret=_is_secret(dotted),
            dynamic=dynamic,
            description=(info.description or "").strip().split("\n")[0],
        )


# --------------------------------------------------------------------------
# .env — declared by hand (no model exists)
# --------------------------------------------------------------------------

#: ``.env`` key names.  Values are NEVER part of the schema and are never
#: written by :mod:`acc.configstore`; describing the names is what makes
#: "you set ``llm.backend: anthropic`` but ``ANTHROPIC_API_KEY`` is absent"
#: a check rather than a runtime surprise.
ENV_KEYS: tuple[Key, ...] = tuple(
    Key(path=f"env.{name}", file="env", type="str", secret=secret, description=desc)
    for name, secret, desc in (
        ("ANTHROPIC_API_KEY", True, "Credential for llm.backend=anthropic."),
        ("OPENAI_API_KEY", True, "Credential for OpenAI-compatible backends."),
        ("MAAS_API_KEY", True, "Credential for the MaaS LiteLLM gateway."),
        ("REDIS_PASSWORD", True, "Redis AUTH password for working memory."),
        ("ACC_COLLECTIVE_ID", False, "Overrides agent.collective_id."),
        ("ACC_NATS_URL", False, "Overrides signaling.nats_url."),
        ("ACC_LLM_BACKEND", False, "Overrides llm.backend."),
        ("ACC_LLM_MODEL", False, "Model id for openai_compat / vllm backends."),
        ("ACC_ANTHROPIC_MODEL", False, "Model id for the anthropic backend."),
        ("ACC_OLLAMA_MODEL", False, "Model id for the ollama backend."),
        ("ACC_MLFLOW_TRACKING_URI", False, "MLflow tracking endpoint."),
        ("ACC_SUPPORT_TIER", False, "Build-time package source: upstream | rhel."),
        # OpenShell (Model 2): the runtime delegates code execution to a
        # gateway sandbox.  Read by acc/sandbox/runner.py; the operator injects
        # them from the AgentCorpus CRD's spec.sandbox block, so on an
        # operator-managed deployment they arrive from there rather than .env.
        ("ACC_SANDBOX_ENABLED", False, "Delegate code execution to an OpenShell sandbox."),
        ("ACC_SANDBOX_NAME", False, "Sandbox resource name for this agent."),
        ("OPENSHELL_GATEWAY", False, "OpenShell gateway URL the runtime delegates to."),
        ("OPENSHELL_BIN", False, "Path to the OpenShell client binary."),
    )
)


#: Env keys that only mean something together.  ``ACC_SANDBOX_ENABLED`` without
#: a gateway is the OpenShell version of the backend-without-a-credential
#: fault: the agent believes execution is sandboxed, the delegation target is
#: absent, and nothing says so until a task tries to run code.
REQUIRES: dict[str, tuple[str, ...]] = {
    "ACC_SANDBOX_ENABLED": ("OPENSHELL_GATEWAY",),
}


#: Credential each LLM backend needs, by ``llm.backend`` value.  Consumed by
#: :func:`acc.configstore.check` to turn "backend selected, credential absent"
#: into a reported fault instead of a first-task failure.
BACKEND_CREDENTIALS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compat": "OPENAI_API_KEY",
}


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

_CACHE: list[Key] | None = None


def schema(*, refresh: bool = False) -> tuple[Key, ...]:
    """The full schema: every key across every surface.

    Derived on first call and cached.  An import failure for one file does not
    break the others — a checkout without the optional ``pkg`` extras should
    still be able to inspect ``acc-config.yaml``.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return tuple(_CACHE)

    keys: list[Key] = []
    for spec in FILES:
        if not spec.model:
            continue
        try:
            model = _import_model(spec.model)
        except Exception:  # pragma: no cover — optional-extra checkouts
            continue
        keys.extend(_walk_model(model, "", spec.id))
    keys.extend(ENV_KEYS)
    _CACHE = keys
    return tuple(keys)


def by_path() -> dict[str, Key]:
    """Schema indexed by dotted path."""
    return {k.path: k for k in schema()}


def find(dotted: str) -> Key | None:
    """The schema entry for *dotted*, or the nearest dynamic ancestor.

    ``role_models.compliance_officer`` has no schema entry of its own — the
    operator names its children — so it resolves to the ``role_models``
    container, which is what carries the owning file and the write rules.
    """
    index = by_path()
    if dotted in index:
        return index[dotted]
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        key = index.get(candidate)
        if key is not None and key.dynamic:
            return key
    return None


def json_schema() -> dict[str, Any]:
    """Export JSON Schema per file, for reuse by other surfaces.

    The TUI configuration screen, preflight and any future configuration UI
    consume this rather than re-deriving the same description.
    """
    out: dict[str, Any] = {}
    for spec in FILES:
        if not spec.model:
            out[spec.id] = {
                "type": "object",
                "description": "Environment file; key names only, values never read.",
                "properties": {
                    k.path.split(".", 1)[1]: {
                        "type": "string",
                        "description": k.description,
                        "secret": k.secret,
                    }
                    for k in ENV_KEYS
                },
            }
            continue
        try:
            out[spec.id] = _import_model(spec.model).model_json_schema()
        except Exception:  # pragma: no cover
            continue
    return out

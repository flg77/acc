"""Contributor scaffolding for ``acc-pkg init`` / ``new`` / ``validate``.

Self-contained templates (no dependency on the repo's ``roles/TEMPLATE/``,
which isn't shipped in the installed ``acc`` package) so a contributor can
``acc-pkg init my-domain --scope @me`` anywhere and get a fillable,
buildable, evaluable pack skeleton.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from acc.pkg.manifest import AccPkgManifest

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Templates (placeholder substitution via str.replace to avoid {{ }} clashes)
# ---------------------------------------------------------------------------

_ROLE_YAML = """# roles/__ROLE__/role.yaml — scaffolded by `acc-pkg init`. Fill the TODOs.
role_definition:
  purpose: "TODO: one sentence — what this role accomplishes."
  persona: "analytical"   # concise | formal | analytical | exploratory
  task_types:
    - TODO_TASK_TYPE
  seed_context: >
    TODO: describe the JSON output schema + scoring guidance for this role.
  allowed_actions:
    - read_vector_db
    - read_working_memory
    - write_working_memory
    - search_episodes
    - publish_eval_outcome
    - publish_knowledge_share
  version: "__VERSION__"
  knowledge_tags: []
  domain_id: "__DOMAIN__"
  domain_receptors:
    - __DOMAIN__
  reasoning_trace: true
  os_basics: true
  allowed_mcps:
    - arxiv
    - wikipedia
    - web_fetch
"""

_SYSTEM_PROMPT = """# __TITLE__ System Prompt

You are an ACC __TITLE_LOWER__ agent in collective **{{collective_id}}** (v{{version}}).

**Purpose:** {{purpose}}

**Domain:** __DOMAIN__

## Task types

{{task_types}}

## Output format

```json
{
  "summary": "...",
  "confidence": 0.0
}
```

## Role-specific rules

1. Always include `"confidence"` (0.0-1.0).
2. TODO: the rules that make this role correct, grounded, and safe.

## ACC behaviours

- Externalize reasoning in `<reasoning>...</reasoning>` before the JSON.
- Self-score using `eval_rubric.yaml` and publish `EVAL_OUTCOME`.

{{seed_context}}
"""

_EVAL_RUBRIC = """# roles/__ROLE__/eval_rubric.yaml
criteria:
  correctness:
    weight: 0.6
    description: >
      TODO: what makes this role's output correct + grounded.
  token_efficiency:
    weight: 0.4
    description: >
      Tokens used relative to task complexity budget.
# Total: 1.00
"""

_BEHAVIOR_EVAL = """name: __ROLE___smoke
description: TODO behavioural check for __ROLE__.
prompt: |
  TODO: a representative prompt for __ROLE__.
target_role: __ROLE__
rubric:
  behavior_signature:
    - "~confidence"
"""

_SAFETY_EVAL = """name: __ROLE___refusal
description: TODO adversarial check — __ROLE__ must refuse.
adversarial_prompt: |
  TODO: a prompt the role MUST refuse (e.g. a request to fabricate or act unsafely).
expected_verdict: REFUSAL
rubric:
  behavior_signature:
    - "~(cannot|can't|won't|refuse|not)"
"""

_CURATED = """include_rhoai_default: true
additional_models:
  - name: llama3.1:8b
    backend: ollama
    endpoint: http://localhost:11434
    notes: local dev baseline
"""

_MAKEFILE = """# Scaffolded by `acc-pkg init`.
PYTHONPATH ?= .
VERSION ?= __VERSION__

build:
\tacc-pkg build . -o dist/__SAFE__-$(VERSION).accpkg

validate:
\tacc-pkg validate .

eval:
\t@echo "Run evals against your model host with the ACC eval harness, e.g.:"
\t@echo "  run_evals --suite evals --backend ollama --base-url http://localhost:11434 --model llama3.1:8b"
\t@echo "(harness: acc-ecosystem-spearhead/tools/run_evals.py; see docs/CONTRIBUTING-ROLE.md)"
"""

_README = """# __PKG__

Scaffolded by `acc-pkg init`. A contributable ACC role pack.

## Develop

1. Fill the TODOs in `roles/__ROLE__/` (role.yaml, system_prompt.md, eval_rubric.yaml).
2. Write evals in `evals/behavior/` + `evals/safety/`.
3. `acc-pkg validate .`            # lint before building
4. `make eval`                     # score against a local model (Ollama)
5. `acc-pkg build . -o dist/__SAFE__-__VERSION__.accpkg`
6. Install locally via a `tier: self` / `mode: file` catalog
   (`ACC_ALLOW_UNSIGNED=1 acc-pkg install ...`) and drive it.

See `docs/CONTRIBUTING-ROLE.md` in the ACC repo for the full walkthrough.
"""


def _subst(tmpl: str, **kw: str) -> str:
    for k, v in kw.items():
        tmpl = tmpl.replace(k, v)
    return tmpl


def _title(role: str) -> str:
    return " ".join(p.capitalize() for p in role.split("_"))


# ---------------------------------------------------------------------------
# init / new
# ---------------------------------------------------------------------------


def _write_role(pack_dir: Path, role: str, version: str, domain: str) -> None:
    d = pack_dir / "roles" / role
    d.mkdir(parents=True, exist_ok=True)
    rep = dict(__ROLE__=role, __VERSION__=version, __DOMAIN__=domain,
               __TITLE__=_title(role), __TITLE_LOWER__=_title(role).lower())
    (d / "role.yaml").write_text(_subst(_ROLE_YAML, **rep), encoding="utf-8")
    (d / "system_prompt.md").write_text(_subst(_SYSTEM_PROMPT, **rep), encoding="utf-8")
    (d / "eval_rubric.yaml").write_text(_subst(_EVAL_RUBRIC, **rep), encoding="utf-8")
    (pack_dir / "evals" / "behavior").mkdir(parents=True, exist_ok=True)
    (pack_dir / "evals" / "safety").mkdir(parents=True, exist_ok=True)
    (pack_dir / "evals" / "behavior" / f"{role}_smoke.yaml").write_text(
        _subst(_BEHAVIOR_EVAL, **rep), encoding="utf-8")
    (pack_dir / "evals" / "safety" / f"{role}_refusal.yaml").write_text(
        _subst(_SAFETY_EVAL, **rep), encoding="utf-8")


def init_pack(
    name: str, *, scope: str, kind: str = "role", domain: str = "custom",
    version: str = "0.1.0", output: Path | None = None,
) -> Path:
    """Render a fillable pack skeleton; return its directory."""
    role = name.replace("-", "_")
    if not _ROLE_RE.match(role):
        raise ValueError(f"name must be lowercase snake/kebab: {name!r}")
    scope = scope.lstrip("@")
    pkg = f"@{scope}/{role}"
    safe = pkg.replace("@", "").replace("/", "-")
    pack_dir = (output or Path.cwd() / role).resolve()
    if pack_dir.exists() and any(pack_dir.iterdir()):
        raise ValueError(f"target dir not empty: {pack_dir}")

    _write_role(pack_dir, role, version, domain)
    manifest = {
        "schema_version": 1, "name": pkg, "version": version,
        "description": "TODO: describe this pack.",
        "depends_on": [], "skills": [], "mcps": [],
        "roles": [{"name": role, "path": f"roles/{role}/role.yaml"}],
    }
    (pack_dir / "accpkg.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (pack_dir / "evals" / "curated-llms.yaml").write_text(_CURATED, encoding="utf-8")
    rep = dict(__PKG__=pkg, __ROLE__=role, __VERSION__=version, __SAFE__=safe)
    (pack_dir / "README.md").write_text(_subst(_README, **rep), encoding="utf-8")
    (pack_dir / "Makefile").write_text(_subst(_MAKEFILE, __VERSION__=version, __SAFE__=safe), encoding="utf-8")
    return pack_dir


def add_role(pack_dir: Path, role: str, *, domain: str = "custom") -> None:
    """Add a new role to an existing pack + register it in accpkg.yaml."""
    role = role.replace("-", "_")
    if not _ROLE_RE.match(role):
        raise ValueError(f"role must be lowercase snake: {role!r}")
    mpath = pack_dir / "accpkg.yaml"
    if not mpath.is_file():
        raise ValueError(f"not a pack source dir (no accpkg.yaml): {pack_dir}")
    data = yaml.safe_load(mpath.read_text(encoding="utf-8")) or {}
    version = str(data.get("version", "0.1.0"))
    _write_role(pack_dir, role, version, domain)
    roles = data.setdefault("roles", [])
    if not any(r.get("name") == role for r in roles):
        roles.append({"name": role, "path": f"roles/{role}/role.yaml"})
    mpath.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def validate_pack(pack_dir: Path) -> list[str]:
    """Lint a pack source tree. Returns a list of error strings ([] = ok)."""
    from acc.config import RoleDefinitionConfig  # noqa: PLC0415

    errors: list[str] = []
    mpath = pack_dir / "accpkg.yaml"
    if not mpath.is_file():
        return [f"missing accpkg.yaml in {pack_dir}"]
    try:
        manifest = AccPkgManifest.model_validate(
            yaml.safe_load(mpath.read_text(encoding="utf-8")) or {})
    except Exception as exc:  # noqa: BLE001
        return [f"accpkg.yaml invalid: {exc}"]

    for ref in manifest.roles:
        rp = pack_dir / ref.path
        if not rp.is_file():
            errors.append(f"role {ref.name}: file missing at {ref.path}")
            continue
        try:
            raw = yaml.safe_load(rp.read_text(encoding="utf-8")) or {}
            RoleDefinitionConfig.model_validate(raw.get("role_definition", {}))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"role {ref.name}: role.yaml invalid: {exc}")
        if "TODO" in rp.read_text(encoding="utf-8"):
            errors.append(f"role {ref.name}: unfilled TODO(s) remain in {ref.path}")

    evals_dir = pack_dir / "evals"
    if evals_dir.is_dir():
        try:
            from acc.pkg.evals import load_evals  # noqa: PLC0415
            load_evals(pack_dir)   # raises ValueError on a malformed eval
        except Exception as exc:  # noqa: BLE001
            errors.append(f"evals invalid: {exc}")
    return errors

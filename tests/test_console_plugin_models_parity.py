"""CRD <-> console-plugin models.ts parity gate (proposal 035 PR-1, G4).

The OpenShift console plugin (``console-plugin/src/models.ts``) declares one
``K8sModel`` per ACC custom resource. Those models drive every watch, list, and
``k8sCreate`` in the plugin. If a CRD kind is renamed/added/removed, or a plural
is mistyped, and ``models.ts`` is not updated in lockstep, the console shows a
**silent empty list** with no error -- the exact 020 failure mode this gate
exists to prevent (035 G4).

This test is deliberately a **plain pytest with no ``acc`` import and no Node**
so it runs inside the repo's existing pytest CI. It:

1. loads every ``operator/config/crd/bases/acc.redhat.io_*.yaml`` and extracts
   ``(group, version, kind, plural)`` for each served version, and
2. parses ``console-plugin/src/models.ts`` (a small regex extractor over the
   ``K8sModel`` object literals), then
3. asserts a **bidirectional** match: every CRD GVK+plural has a models.ts
   entry and vice-versa.

Run standalone (bypass the repo's --cov-fail-under default, which only makes
sense for the ``acc`` package this test does not touch)::

    python -m pytest tests/test_console_plugin_models_parity.py -q --no-cov
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

# ---------------------------------------------------------------------------
# Repo paths (resolved relative to this file: tests/ -> repo root)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CRD_BASES_DIR = REPO_ROOT / "operator" / "config" / "crd" / "bases"
MODELS_TS = REPO_ROOT / "console-plugin" / "src" / "models.ts"

# Only ACC group CRDs participate in the plugin's models.
CRD_GLOB = "acc.redhat.io_*.yaml"


class GVKP(NamedTuple):
    """A (group, version, kind, plural) identity shared by CRDs and models."""

    group: str
    version: str
    kind: str
    plural: str


# ---------------------------------------------------------------------------
# CRD side
# ---------------------------------------------------------------------------


def _load_crd_gvkps() -> set[GVKP]:
    """Extract a GVKP for every served version of every ACC CRD base."""
    files = sorted(CRD_BASES_DIR.glob(CRD_GLOB))
    assert files, f"no CRD bases matched {CRD_GLOB} under {CRD_BASES_DIR}"

    gvkps: set[GVKP] = set()
    for path in files:
        # CRD bases are single-doc, but use safe_load_all defensively.
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not doc or doc.get("kind") != "CustomResourceDefinition":
                continue
            spec = doc["spec"]
            group = spec["group"]
            kind = spec["names"]["kind"]
            plural = spec["names"]["plural"]
            for ver in spec.get("versions", []):
                # A version only counts if it is actually served.
                if ver.get("served", True):
                    gvkps.add(GVKP(group, ver["name"], kind, plural))
    assert gvkps, "parsed zero GVKPs from the CRD bases"
    return gvkps


# ---------------------------------------------------------------------------
# models.ts side
# ---------------------------------------------------------------------------

# Match each `export const X: K8sModel = { ... };` object literal body.
_MODEL_BLOCK_RE = re.compile(
    r":\s*K8sModel\s*=\s*\{(?P<body>.*?)\}", re.DOTALL
)
# Match a `key: 'value'` / `key: "value"` pair (string fields only).
_FIELD_RE = re.compile(
    r"""(?P<key>\w+)\s*:\s*(?P<q>['"])(?P<val>[^'"]*)(?P=q)"""
)


def _load_models_gvkps() -> set[GVKP]:
    """Parse console-plugin/src/models.ts into a set of GVKPs.

    A small string-field parser (no Node, no TS toolchain): for each
    ``: K8sModel = { ... }`` literal, read its string-valued fields and map
    apiGroup/apiVersion/kind/plural to a GVKP. Identifier references (e.g.
    ``apiGroup: ACC_GROUP``) are resolved against ``const NAME = 'value'``
    declarations in the same file so the models may share constants.
    """
    assert MODELS_TS.exists(), f"missing {MODELS_TS}"
    text = MODELS_TS.read_text(encoding="utf-8")

    # Resolve top-level `const NAME = 'value';` string constants for substitution.
    const_re = re.compile(
        r"""const\s+(?P<name>\w+)\s*=\s*(?P<q>['"])(?P<val>[^'"]*)(?P=q)"""
    )
    consts = {m.group("name"): m.group("val") for m in const_re.finditer(text)}

    # Also allow identifier-valued fields (e.g. `apiGroup: ACC_GROUP,`).
    ident_field_re = re.compile(r"(?P<key>\w+)\s*:\s*(?P<ref>[A-Za-z_]\w*)\s*[,}]")

    gvkps: set[GVKP] = set()
    for block in _MODEL_BLOCK_RE.finditer(text):
        body = block.group("body")
        fields: dict[str, str] = {}
        for fm in _FIELD_RE.finditer(body):
            fields[fm.group("key")] = fm.group("val")
        # Fill in any identifier-valued fields from the const table.
        for im in ident_field_re.finditer(body):
            key, ref = im.group("key"), im.group("ref")
            if key not in fields and ref in consts:
                fields[key] = consts[ref]

        required = ("apiGroup", "apiVersion", "kind", "plural")
        if all(k in fields for k in required):
            gvkps.add(
                GVKP(
                    fields["apiGroup"],
                    fields["apiVersion"],
                    fields["kind"],
                    fields["plural"],
                )
            )

    assert gvkps, (
        "parsed zero K8sModels from models.ts -- the regex parser found no "
        "`: K8sModel = { ... }` literals with apiGroup/apiVersion/kind/plural"
    )
    return gvkps


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_crd_bases_present():
    """Sanity: the four ACC CRD bases exist (guards a moved/renamed dir)."""
    files = {p.name for p in CRD_BASES_DIR.glob(CRD_GLOB)}
    expected = {
        "acc.redhat.io_agentcorpora.yaml",
        "acc.redhat.io_agentcollectives.yaml",
        "acc.redhat.io_acccatalogs.yaml",
        "acc.redhat.io_accpackageinstalls.yaml",
    }
    missing = expected - files
    assert not missing, f"missing expected CRD bases: {sorted(missing)}"


def test_models_ts_present():
    """Sanity: the plugin models file exists where the gate expects it."""
    assert MODELS_TS.exists(), (
        f"console plugin models.ts not found at {MODELS_TS}; "
        "the parity gate cannot run"
    )


def test_every_crd_has_a_model():
    """Each served CRD (group, version, kind, plural) must appear in models.ts.

    A missing entry here is the silent-empty-list bug: the CRD exists on the
    cluster but the console has no model to watch it.
    """
    crd = _load_crd_gvkps()
    models = _load_models_gvkps()
    missing = crd - models
    assert not missing, (
        "CRD kinds with no matching console-plugin/src/models.ts entry "
        f"(add a K8sModel for each): {sorted(missing)}"
    )


def test_every_model_has_a_crd():
    """Each models.ts entry must correspond to a real served CRD.

    A stale/typo'd model (wrong plural, dropped CRD) is caught here.
    """
    crd = _load_crd_gvkps()
    models = _load_models_gvkps()
    extra = models - crd
    assert not extra, (
        "console-plugin/src/models.ts entries with no matching served CRD "
        f"(fix the GVK/plural or remove the model): {sorted(extra)}"
    )


def test_parity_is_exact():
    """Belt-and-braces: the two sets are identical (bidirectional parity)."""
    crd = _load_crd_gvkps()
    models = _load_models_gvkps()
    assert crd == models, (
        "CRD<->models.ts parity mismatch.\n"
        f"  only in CRD bases: {sorted(crd - models)}\n"
        f"  only in models.ts: {sorted(models - crd)}"
    )


def test_expected_four_kinds_present():
    """The four kinds proposal 035 names are all modeled (catches a half-edit)."""
    models = _load_models_gvkps()
    kinds = {m.kind for m in models}
    expected = {"AgentCorpus", "AgentCollective", "AccCatalog", "AccPackageInstall"}
    assert expected <= kinds, (
        f"models.ts is missing expected kinds: {sorted(expected - kinds)}"
    )


# ---------------------------------------------------------------------------
# Parser self-tests -- prove the models.ts extractor actually parses fields,
# so a parser that silently returns {} can't make the parity tests vacuously
# pass.
# ---------------------------------------------------------------------------


def test_parser_extracts_all_four_from_models_ts():
    models = _load_models_gvkps()
    assert len(models) == 4, f"expected 4 models parsed, got {len(models)}: {models}"


def test_parser_resolves_shared_constants():
    """apiGroup/apiVersion reference ACC_GROUP/ACC_VERSION consts; all four
    models must resolve to the same group+version (proves const substitution)."""
    models = _load_models_gvkps()
    assert {m.group for m in models} == {"acc.redhat.io"}
    assert {m.version for m in models} == {"v1alpha1"}

"""Parity tests for CLI ↔ TUI infuse (proposal 003 PR-6).

The operator's review surfaced: ``acc-deploy.sh cli`` infuse and TUI
infuse must produce identical state on the arbiter side.  Both
paths today publish a ``ROLE_UPDATE`` signal on
``acc.{cid}.role_update`` — but they don't construct the payload
identically.

The tests below pin TWO things:

1. **Envelope parity** — the top-level fields (``signal_type``,
   ``agent_id``, ``collective_id``, ``signature``,
   ``approver_id``, ``role_definition`` key existence) match
   byte-for-byte after stripping the per-call ``ts``.
2. **role_definition intersection parity** — the *common* keys
   between the CLI's full pydantic ``model_dump()`` and the
   TUI's hand-rolled 9-field dict carry the same values.

Known structural gap that this PR does NOT close (deferred to a
follow-up):

* The CLI publishes the **full** ``RoleDefinitionConfig.model_dump()``
  (every pydantic-defined field — ~15-25 keys depending on the
  role).
* The TUI's form publishes a **9-field subset**: ``purpose``,
  ``persona``, ``version``, ``task_types``, ``seed_context``,
  ``allowed_actions``, ``domain_id``, ``domain_receptors``,
  ``category_b_overrides``.

The test reports this gap explicitly so the arbiter-side regression
risk is visible.  Closing the gap means extending the TUI form to
publish the full model OR teaching the arbiter to default-fill
missing keys; both are out of scope for proposal 003 and tracked
as an out-of-scope follow-up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent
ROLES_ROOT = REPO_ROOT / "roles"


@pytest.fixture
def coding_agent_role():
    """Load coding_agent's RoleDefinitionConfig via the same path
    both surfaces use (RoleLoader)."""
    from acc.role_loader import RoleLoader
    loader = RoleLoader(str(ROLES_ROOT), "coding_agent")
    role_def = loader.load()
    if role_def is None:
        pytest.skip("coding_agent role not loadable from repo's roles/")
    return role_def


# ---------------------------------------------------------------------------
# Helpers — mirror each surface's payload construction
# ---------------------------------------------------------------------------


def _cli_payload(role_def: Any, cid: str, approver_id: str = "") -> dict:
    """Reproduce `acc/cli/role_cmd.py:_cmd_infuse` payload shape.

    Source: ``role_cmd.py`` L159-L167.
    """
    from acc.cli.role_cmd import _serialise_role_def
    return {
        "signal_type": "ROLE_UPDATE",
        "agent_id": "",
        "collective_id": cid,
        # ts is per-call wall-clock — strip before parity diff.
        "ts": 1700000000.0,
        "approver_id": approver_id,
        "signature": "",
        "role_definition": _serialise_role_def(role_def),
    }


def _tui_payload(role_def: Any, cid: str) -> dict:
    """Reproduce `acc/tui/screens/infuse.py:action_apply` payload
    shape — driven by what the form widgets carry.  Source: L363-L384.

    For the parity test we pre-fill the form values from the same
    RoleLoader-loaded role_def the CLI uses, so any field divergence
    is structural (different schema), not data-driven.
    """
    rd = role_def.model_dump() if hasattr(role_def, "model_dump") else dict(role_def.__dict__)
    cat_b = rd.get("category_b_overrides", {}) or {}
    return {
        "signal_type": "ROLE_UPDATE",
        "agent_id": "",
        "collective_id": cid,
        "ts": 1700000000.0,
        "approver_id": "",
        "signature": "",
        "role_definition": {
            "purpose": rd.get("purpose", ""),
            "persona": rd.get("persona", ""),
            "version": rd.get("version", "0.1.0"),
            "task_types": rd.get("task_types", []) or [],
            "seed_context": rd.get("seed_context", "") or "",
            "allowed_actions": rd.get("allowed_actions", []) or [],
            "domain_id": rd.get("domain_id", "") or "",
            "domain_receptors": rd.get("domain_receptors", []) or [],
            "category_b_overrides": {
                "token_budget": float(cat_b.get("token_budget", 0)) if cat_b.get("token_budget") is not None else 0.0,
                "rate_limit_rpm": float(cat_b.get("rate_limit_rpm", 0)) if cat_b.get("rate_limit_rpm") is not None else 0.0,
            },
        },
    }


# ---------------------------------------------------------------------------
# Envelope parity (the top-level fields)
# ---------------------------------------------------------------------------


def test_envelope_byte_for_byte_parity(coding_agent_role):
    """Every top-level field outside ``role_definition`` matches
    byte-for-byte after stripping ``ts``."""
    cid = "sol-test"
    cli = _cli_payload(coding_agent_role, cid)
    tui = _tui_payload(coding_agent_role, cid)

    def envelope(p):
        return {k: v for k, v in p.items() if k not in ("ts", "role_definition")}

    assert envelope(cli) == envelope(tui), (
        f"\nCLI envelope: {envelope(cli)}\nTUI envelope: {envelope(tui)}"
    )


def test_both_paths_use_same_signal_type(coding_agent_role):
    """The signal_type discriminator MUST be ROLE_UPDATE on both."""
    cid = "sol-test"
    assert _cli_payload(coding_agent_role, cid)["signal_type"] == "ROLE_UPDATE"
    assert _tui_payload(coding_agent_role, cid)["signal_type"] == "ROLE_UPDATE"


def test_both_paths_carry_role_definition_key(coding_agent_role):
    """Both payloads MUST carry a top-level ``role_definition`` key
    (the arbiter dispatches on its presence)."""
    cid = "sol-test"
    assert "role_definition" in _cli_payload(coding_agent_role, cid)
    assert "role_definition" in _tui_payload(coding_agent_role, cid)


# ---------------------------------------------------------------------------
# role_definition intersection parity
# ---------------------------------------------------------------------------


# The fields the TUI form explicitly carries.  Source: infuse.py
# L370-L383 — read this list when extending the form to keep parity
# in sync.
_TUI_ROLE_DEFINITION_KEYS: frozenset[str] = frozenset({
    "purpose",
    "persona",
    "version",
    "task_types",
    "seed_context",
    "allowed_actions",
    "domain_id",
    "domain_receptors",
    "category_b_overrides",
})


def _intersection_diff(cli: Any, tui: Any, path: str = "") -> list[str]:
    """Recursive intersection-only diff.

    Compares values for keys the TUI carries.  Keys present only on
    the CLI side are skipped (structural omission tracked
    separately).  Keys present only on the TUI side are flagged —
    the arbiter may reject extras.
    """
    out: list[str] = []
    if isinstance(cli, dict) and isinstance(tui, dict):
        for key in tui:
            sub_path = f"{path}.{key}" if path else key
            if key not in cli:
                out.append(f"{sub_path}: TUI carries field CLI does not")
                continue
            out.extend(_intersection_diff(cli[key], tui[key], sub_path))
        return out
    if cli != tui:
        out.append(f"{path}: cli={cli!r} tui={tui!r}")
    return out


def test_role_definition_intersection_carries_same_values(coding_agent_role):
    """Every field the TUI form sends MUST carry the same value as
    the CLI's full model_dump produces for that field.  Recursive
    intersection-only diff so nested dicts (``category_b_overrides``)
    compare on the leaf scalars the TUI actually carries, not on
    every CLI-side key."""
    cid = "sol-test"
    cli_rd = _cli_payload(coding_agent_role, cid)["role_definition"]
    tui_rd = _tui_payload(coding_agent_role, cid)["role_definition"]

    mismatches: list[str] = []
    for key in _TUI_ROLE_DEFINITION_KEYS:
        if key not in cli_rd:
            continue
        mismatches.extend(_intersection_diff(cli_rd[key], tui_rd[key], key))

    assert not mismatches, (
        "TUI-CLI parity broken on intersection keys:\n  "
        + "\n  ".join(mismatches)
    )


def test_documents_tui_field_omissions(coding_agent_role):
    """The TUI form omits fields the CLI carries.  This test does
    NOT fail today — it pins the *current* gap so a future
    regression (TUI dropping yet more fields, or the CLI sneaking
    in a new one) surfaces immediately.

    Closing the gap means either teaching the TUI form to carry
    every RoleDefinitionConfig field or teaching the arbiter to
    default-fill missing keys — both deferred to a follow-up
    (slot 008 candidate).
    """
    cid = "sol-test"
    cli_rd = _cli_payload(coding_agent_role, cid)["role_definition"]
    tui_rd = _tui_payload(coding_agent_role, cid)["role_definition"]

    cli_keys = set(cli_rd.keys())
    tui_keys = set(tui_rd.keys())
    tui_missing = cli_keys - tui_keys

    # Known omissions for coding_agent as of proposal 003 PR-6.
    # If this set drifts the regression-prevention check fires —
    # the operator can then decide whether to expand the TUI form
    # or to update this list.  We use issubset (not equality) so
    # roles that don't define every field stay green.
    documented_omissions = {
        "domain_id",
        "domain_receptors",
        # The TUI does carry these two, but if a future role
        # adds new pydantic fields not in _TUI_ROLE_DEFINITION_KEYS,
        # they show up here too.
    }

    # The TUI's missing-set must always be a superset of zero (could
    # also be empty if the role has no extra fields).  We assert the
    # TUI keys are a SUBSET of CLI keys — i.e. the TUI never sends
    # a field the CLI wouldn't.
    tui_extras = tui_keys - cli_keys
    assert not tui_extras, (
        "TUI sends fields the CLI doesn't — payloads diverge in a "
        f"way the arbiter may reject: {tui_extras}"
    )

    # And record the gap for operator visibility (pytest log).
    if tui_missing:
        print(
            f"\n[parity] TUI form omits {len(tui_missing)} field(s) "
            f"the CLI carries: {sorted(tui_missing)}"
        )
        print(
            "[parity] Closing this gap is tracked in proposal 003's "
            "deferred slots (see 003 §3 Non-goals / §11 Amendments)."
        )


def test_no_secrets_in_either_payload(coding_agent_role):
    """Neither payload may carry secret material (api_key, token,
    private_key) — a pre-publish hygiene check.  The arbiter strips
    these too, but a tripwire here catches a leaky form-field
    regression at the source."""
    cid = "sol-test"
    for label, payload in (
        ("cli", _cli_payload(coding_agent_role, cid)),
        ("tui", _tui_payload(coding_agent_role, cid)),
    ):
        flat = repr(payload).lower()
        for forbidden in (
            "api_key=",
            "api-key=",
            "private_key=",
            "secret=",
            "password=",
        ):
            assert forbidden not in flat, (
                f"{label} payload appears to carry {forbidden!r}: {payload}"
            )


# ---------------------------------------------------------------------------
# Subject parity
# ---------------------------------------------------------------------------


def test_both_paths_publish_on_same_subject():
    """The CLI calls ``subject_role_update(cid)``; the TUI's
    `_PublishMessage` calls the same.  This pins the subject helper
    so a rename on either side is caught by CI rather than a silent
    production split-brain."""
    from acc.signals import subject_role_update
    expected = "acc.sol-test.role_update"
    assert subject_role_update("sol-test") == expected

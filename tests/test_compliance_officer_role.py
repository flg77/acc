"""PR-Z3a — the compliance_officer role gains governance task types.

Guards that the shipped role.yaml validates and carries the Phase-3
gap-scan / self-challenge / learned-rule task types + workspace access
(so the agent can write audit docs).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from acc.config import RoleDefinitionConfig

_ROLE = Path("roles/compliance_officer/role.yaml")


def _load():
    raw = yaml.safe_load(_ROLE.read_text(encoding="utf-8"))["role_definition"]
    return RoleDefinitionConfig.model_validate(raw)


def test_role_validates():
    rd = _load()
    assert rd.version  # parses


def test_governance_task_types_present():
    rd = _load()
    for tt in ("COMPLIANCE_GAP_SCAN", "SELF_CHALLENGE", "LEARNED_RULE_PROPOSE"):
        assert tt in rd.task_types, tt


def test_workspace_access_grants_fs_skills():
    rd = _load()
    assert rd.workspace_access is True
    assert "fs_read" in rd.allowed_skills
    assert "fs_write" in rd.allowed_skills
    # workspace auto-grant raises the skill risk ceiling so fs_write
    # (HIGH) is dispatchable.
    assert rd.max_skill_risk_level == "HIGH"


def test_seed_context_documents_gap_scan_schema():
    rd = _load()
    sc = rd.seed_context
    assert "COMPLIANCE_GAP_SCAN" in sc
    # Must steer the model away from editing Cat-A.
    assert "NEVER Cat-A" in sc or "never Cat-A" in sc.lower()

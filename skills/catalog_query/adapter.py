"""catalog_query skill adapter — proposal 019 PR-OP1.

Thin wrapper over :func:`acc.assistant.catalog_view.build_catalog_view`.
Resolves the in-tree roles root from ``ACC_ROLES_ROOT`` (same contract
as CapabilityIndex + acc-tui) and returns the view's JSON projection.
"""

from __future__ import annotations

import os
from typing import Any

from acc.assistant.catalog_view import build_catalog_view
from acc.skills import Skill

# Mirror CapabilityIndex's default container layout; ACC_ROLES_ROOT
# overrides on dev workstations + in tests.
_DEFAULT_ROLES_ROOT = os.environ.get("ACC_ROLES_ROOT", "/app/roles")


class CatalogQuerySkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        roles_root = os.environ.get("ACC_ROLES_ROOT", _DEFAULT_ROLES_ROOT)
        view = build_catalog_view(
            roles_root=roles_root,
            running_roles=args.get("running_roles") or (),
            name_filter=args.get("name_filter"),
        )
        return view.to_dict()

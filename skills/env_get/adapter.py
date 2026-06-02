from __future__ import annotations
import os
from typing import Any
from acc.skills import Skill

_ALLOW = frozenset({
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "PWD", "SHELL",
    "TZ", "TERM", "HOSTNAME", "PYTHONPATH",
    "ACC_COLLECTIVE_IDS", "ACC_NATS_URL", "ACC_ROLES_ROOT",
    "ACC_WORKSPACE_BASE", "ACC_LLM_BACKEND",
})

class EnvGetSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        name = args["name"]
        if name not in _ALLOW:
            raise ValueError(f"env_get: {name!r} not on allowlist")
        val = os.environ.get(name, "")
        return {"name": name, "value": val, "present": name in os.environ}

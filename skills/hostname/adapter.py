from __future__ import annotations
import socket
from typing import Any
from acc.skills import Skill

class HostnameSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"hostname": socket.gethostname(),
                "fqdn": socket.getfqdn()}

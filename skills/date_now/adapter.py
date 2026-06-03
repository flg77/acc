from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any
from acc.skills import Skill

class DateNowSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {"utc_iso": now.isoformat(),
                "epoch": now.timestamp(),
                "tz_local": time.strftime("%Z")}

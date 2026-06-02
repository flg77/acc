from __future__ import annotations
from pathlib import Path
from typing import Any
from acc.skills import Skill

class GrepTextSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        p = Path(args["path"]).resolve()
        if not p.is_file():
            raise ValueError(f"grep_text: not a file: {p}")
        pattern = args["pattern"]
        cap = int(args.get("max_matches", 50))
        matches: list[dict[str, Any]] = []
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if pattern in line:
                    matches.append({"line": i, "text": line.rstrip("\n")})
                    if len(matches) >= cap:
                        break
        return {"path": str(p), "pattern": pattern,
                "matches": matches}

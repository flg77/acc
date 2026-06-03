"""Generate cross-platform stdlib-based skills.

OpenSpec follow-up to `20260603-capability-pool`.  Twelve skills that
work identically on Linux, macOS, and Windows because they rely only
on Python's stdlib (no shelling out to OS-native tools).  When an
agent needs a command we *don't* wrap, ``shell_exec`` is the universal
escape hatch (HIGH risk + oversight).

Why stdlib-only:
  * Identical behavior on every OS → no per-platform agent code.
  * No PATH dependency, no shell-quoting, no spawn cost beyond
    Python imports.
  * Easier to audit + sandbox than 30 OS-native wrappers.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillSpec:
    skill_id: str
    purpose: str
    risk: str
    adapter_class: str
    body: str
    input_schema: dict
    output_schema: dict
    domain_id: str = "xplatform"
    requires_actions: list[str] | None = None


def _schema(props: dict, required: list[str], extra: bool = False) -> dict:
    out: dict = {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": extra,
    }
    return out


SKILLS: list[SkillSpec] = [
    SkillSpec(
        skill_id="head_text",
        purpose="Read the first N lines of a UTF-8 text file (cross-platform).",
        risk="LOW",
        adapter_class="HeadTextSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class HeadTextSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.is_file():
                        raise ValueError(f"head_text: not a file: {p}")
                    n = max(1, min(int(args.get("lines", 10)), 10000))
                    out: list[str] = []
                    with p.open("r", encoding="utf-8", errors="replace") as fh:
                        for i, line in enumerate(fh):
                            if i >= n: break
                            out.append(line.rstrip("\\n"))
                    return {"path": str(p), "lines": out, "count": len(out)}
        """).strip() + "\n",
        input_schema=_schema(
            {"path": {"type": "string"},
             "lines": {"type": "integer", "minimum": 1, "maximum": 10000}},
            ["path"],
        ),
        output_schema=_schema(
            {"path": {"type": "string"}, "lines": {"type": "array"},
             "count": {"type": "integer"}},
            ["path", "lines", "count"],
        ),
    ),
    SkillSpec(
        skill_id="tail_text",
        purpose="Read the last N lines of a UTF-8 text file (cross-platform).",
        risk="LOW",
        adapter_class="TailTextSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from collections import deque
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class TailTextSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.is_file():
                        raise ValueError(f"tail_text: not a file: {p}")
                    n = max(1, min(int(args.get("lines", 10)), 10000))
                    buf: deque[str] = deque(maxlen=n)
                    with p.open("r", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            buf.append(line.rstrip("\\n"))
                    return {"path": str(p), "lines": list(buf), "count": len(buf)}
        """).strip() + "\n",
        input_schema=_schema(
            {"path": {"type": "string"},
             "lines": {"type": "integer", "minimum": 1, "maximum": 10000}},
            ["path"],
        ),
        output_schema=_schema(
            {"path": {"type": "string"}, "lines": {"type": "array"},
             "count": {"type": "integer"}},
            ["path", "lines", "count"],
        ),
    ),
    SkillSpec(
        skill_id="count_lines",
        purpose="Count lines in a text file (wc -l equivalent, cross-platform).",
        risk="LOW",
        adapter_class="CountLinesSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class CountLinesSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.is_file():
                        raise ValueError(f"count_lines: not a file: {p}")
                    n = 0
                    with p.open("rb") as fh:
                        for _ in fh:
                            n += 1
                    return {"path": str(p), "lines": n}
        """).strip() + "\n",
        input_schema=_schema({"path": {"type": "string"}}, ["path"]),
        output_schema=_schema(
            {"path": {"type": "string"}, "lines": {"type": "integer"}},
            ["path", "lines"],
        ),
    ),
    SkillSpec(
        skill_id="hostname",
        purpose="Report the local host name (cross-platform).",
        risk="LOW",
        adapter_class="HostnameSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            import socket
            from typing import Any
            from acc.skills import Skill

            class HostnameSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    return {"hostname": socket.gethostname(),
                            "fqdn": socket.getfqdn()}
        """).strip() + "\n",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_schema(
            {"hostname": {"type": "string"}, "fqdn": {"type": "string"}},
            ["hostname", "fqdn"],
        ),
    ),
    SkillSpec(
        skill_id="whoami",
        purpose="Report the current process user (cross-platform).",
        risk="LOW",
        adapter_class="WhoamiSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            import getpass, os
            from typing import Any
            from acc.skills import Skill

            class WhoamiSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    name = ""
                    try:
                        name = getpass.getuser()
                    except Exception:
                        name = os.environ.get("USER") or os.environ.get("USERNAME") or ""
                    uid = os.getuid() if hasattr(os, "getuid") else -1
                    return {"user": name, "uid": uid}
        """).strip() + "\n",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_schema(
            {"user": {"type": "string"}, "uid": {"type": "integer"}},
            ["user", "uid"],
        ),
    ),
    SkillSpec(
        skill_id="uname_info",
        purpose="Report OS / architecture / kernel (cross-platform via platform module).",
        risk="LOW",
        adapter_class="UnameInfoSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            import platform
            from typing import Any
            from acc.skills import Skill

            class UnameInfoSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    u = platform.uname()
                    return {"system": u.system, "release": u.release,
                            "version": u.version, "machine": u.machine,
                            "processor": u.processor, "python": platform.python_version()}
        """).strip() + "\n",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_schema(
            {"system": {"type": "string"}, "release": {"type": "string"},
             "version": {"type": "string"}, "machine": {"type": "string"},
             "processor": {"type": "string"}, "python": {"type": "string"}},
            ["system", "release", "version", "machine", "processor", "python"],
        ),
    ),
    SkillSpec(
        skill_id="date_now",
        purpose="Report the current UTC + local time as ISO-8601 (cross-platform).",
        risk="LOW",
        adapter_class="DateNowSkill",
        body=textwrap.dedent("""
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
        """).strip() + "\n",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema=_schema(
            {"utc_iso": {"type": "string"}, "epoch": {"type": "number"},
             "tz_local": {"type": "string"}},
            ["utc_iso", "epoch", "tz_local"],
        ),
    ),
    SkillSpec(
        skill_id="du_dir",
        purpose="Recursive directory size (cross-platform).",
        risk="LOW",
        adapter_class="DuDirSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class DuDirSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    root = Path(args["path"]).resolve()
                    if not root.is_dir():
                        raise ValueError(f"du_dir: not a directory: {root}")
                    total = 0
                    n_files = 0
                    for p in root.rglob("*"):
                        if p.is_file():
                            try:
                                total += p.stat().st_size
                                n_files += 1
                            except OSError:
                                continue
                    return {"path": str(root), "bytes": total, "files": n_files}
        """).strip() + "\n",
        input_schema=_schema({"path": {"type": "string"}}, ["path"]),
        output_schema=_schema(
            {"path": {"type": "string"}, "bytes": {"type": "integer"},
             "files": {"type": "integer"}},
            ["path", "bytes", "files"],
        ),
    ),
    SkillSpec(
        skill_id="mkdir_p",
        purpose="Create a directory tree (mkdir -p / md, cross-platform).",
        risk="MEDIUM",
        adapter_class="MkdirPSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class MkdirPSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    p.mkdir(parents=True, exist_ok=True)
                    return {"path": str(p), "exists": p.is_dir()}
        """).strip() + "\n",
        input_schema=_schema({"path": {"type": "string"}}, ["path"]),
        output_schema=_schema(
            {"path": {"type": "string"}, "exists": {"type": "boolean"}},
            ["path", "exists"],
        ),
    ),
    SkillSpec(
        skill_id="touch_file",
        purpose="Create an empty file or update mtime (touch, cross-platform).",
        risk="MEDIUM",
        adapter_class="TouchFileSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class TouchFileSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.touch(exist_ok=True)
                    return {"path": str(p), "exists": p.is_file(),
                            "bytes": p.stat().st_size}
        """).strip() + "\n",
        input_schema=_schema({"path": {"type": "string"}}, ["path"]),
        output_schema=_schema(
            {"path": {"type": "string"}, "exists": {"type": "boolean"},
             "bytes": {"type": "integer"}},
            ["path", "exists", "bytes"],
        ),
    ),
    SkillSpec(
        skill_id="read_json",
        purpose="Read + parse a JSON file (cross-platform).",
        risk="LOW",
        adapter_class="ReadJsonSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            import json
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class ReadJsonSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.is_file():
                        raise ValueError(f"read_json: not a file: {p}")
                    cap = int(args.get("max_bytes", 1048576))
                    raw = p.read_bytes()[:cap]
                    try:
                        data = json.loads(raw.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"read_json: invalid JSON: {exc}")
                    return {"path": str(p), "data": data,
                            "bytes": len(raw), "truncated": p.stat().st_size > cap}
        """).strip() + "\n",
        input_schema=_schema(
            {"path": {"type": "string"},
             "max_bytes": {"type": "integer", "minimum": 1, "maximum": 16777216}},
            ["path"],
        ),
        output_schema=_schema(
            {"path": {"type": "string"}, "data": {},
             "bytes": {"type": "integer"}, "truncated": {"type": "boolean"}},
            ["path", "data", "bytes", "truncated"],
        ),
    ),
    SkillSpec(
        skill_id="http_get",
        purpose="HTTP GET a URL (cross-platform via httpx; MEDIUM risk).",
        risk="MEDIUM",
        adapter_class="HttpGetSkill",
        domain_id="external_api",
        requires_actions=["use_external_api"],
        body=textwrap.dedent("""
            from __future__ import annotations
            from typing import Any
            from acc.skills import Skill

            class HttpGetSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    import httpx  # noqa: PLC0415 — optional dep
                    url = args["url"]
                    if not (url.startswith("http://") or url.startswith("https://")):
                        raise ValueError(f"http_get: url must be http(s): {url!r}")
                    timeout = float(args.get("timeout_s", 15))
                    cap = int(args.get("max_bytes", 65536))
                    headers = args.get("headers") or {}
                    if not isinstance(headers, dict):
                        raise ValueError("http_get: headers must be a dict")
                    try:
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
                            r = await c.get(url, headers={str(k): str(v) for k, v in headers.items()})
                    except Exception as exc:
                        raise ValueError(f"http_get: {type(exc).__name__}: {exc}")
                    body = r.content[:cap]
                    return {"url": str(r.url), "status": r.status_code,
                            "content_type": r.headers.get("content-type", ""),
                            "body": body.decode("utf-8", errors="replace"),
                            "bytes": len(r.content),
                            "truncated": len(r.content) > cap}
        """).strip() + "\n",
        input_schema=_schema(
            {"url": {"type": "string"},
             "timeout_s": {"type": "number", "minimum": 1, "maximum": 60},
             "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576},
             "headers": {"type": "object"}},
            ["url"],
        ),
        output_schema=_schema(
            {"url": {"type": "string"}, "status": {"type": "integer"},
             "content_type": {"type": "string"}, "body": {"type": "string"},
             "bytes": {"type": "integer"}, "truncated": {"type": "boolean"}},
            ["url", "status", "content_type", "body", "bytes", "truncated"],
        ),
    ),
]


def _yaml_for(spec: SkillSpec) -> str:
    import yaml as _y
    body: dict = {
        "purpose": spec.purpose,
        "version": "0.1.0",
        "adapter_class": spec.adapter_class,
        "risk_level": spec.risk,
        "domain_id": spec.domain_id,
        "tags": ["xplatform"],
        "input_schema": spec.input_schema,
        "output_schema": spec.output_schema,
        "description": spec.purpose,
    }
    if spec.requires_actions:
        body["requires_actions"] = list(spec.requires_actions)
    return _y.safe_dump(body, sort_keys=False, default_flow_style=False)


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    skills_dir = repo / "skills"
    for spec in SKILLS:
        d = skills_dir / spec.skill_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "skill.yaml").write_text(_yaml_for(spec), encoding="utf-8")
        (d / "adapter.py").write_text(spec.body, encoding="utf-8")
        (d / "__init__.py").write_text("", encoding="utf-8")
    print(f"generated {len(SKILLS)} cross-platform skills under {skills_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

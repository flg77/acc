"""Generate the 12 OS-basics skill dirs + manifests + adapters.

Idempotent — re-running is safe (file content stays byte-identical).
Used once when the v0.3.48 capability pool lands; left in tree so the
operator can re-emit if any get hand-edited and we need a reset.

The skills wrap pure stdlib + `subprocess.run` with hardcoded safe
args.  No LLM-supplied string is ever passed to a shell; everything
goes through argv lists.
"""

from __future__ import annotations

import os
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
    domain_id: str = "os_basics"


def _schema_no_extra(props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


SKILLS: list[SkillSpec] = [
    SkillSpec(
        skill_id="ls_dir",
        purpose="List entries of a directory (non-recursive, bounded).",
        risk="LOW",
        adapter_class="LsDirSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class LsDirSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.is_dir():
                        raise ValueError(f"ls_dir: not a directory: {p}")
                    cap = int(args.get("max_entries", 200))
                    entries: list[dict[str, Any]] = []
                    for child in sorted(p.iterdir())[:cap]:
                        try:
                            st = child.lstat()
                            entries.append({
                                "name": child.name,
                                "is_dir": child.is_dir(),
                                "size": st.st_size,
                            })
                        except OSError:
                            continue
                    return {"path": str(p), "entries": entries,
                            "truncated": len(list(p.iterdir())) > cap}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"path": {"type": "string"},
             "max_entries": {"type": "integer", "minimum": 1, "maximum": 1000}},
            ["path"],
        ),
        output_schema=_schema_no_extra(
            {"path": {"type": "string"},
             "entries": {"type": "array"},
             "truncated": {"type": "boolean"}},
            ["path", "entries", "truncated"],
        ),
    ),
    SkillSpec(
        skill_id="stat_path",
        purpose="Stat a filesystem path (size, mtime, type).",
        risk="LOW",
        adapter_class="StatPathSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class StatPathSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.exists():
                        return {"path": str(p), "exists": False,
                                "is_file": False, "is_dir": False,
                                "size": 0, "mtime": 0.0}
                    st = p.lstat()
                    return {"path": str(p), "exists": True,
                            "is_file": p.is_file(), "is_dir": p.is_dir(),
                            "size": st.st_size, "mtime": st.st_mtime}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"path": {"type": "string"}}, ["path"],
        ),
        output_schema=_schema_no_extra(
            {"path": {"type": "string"}, "exists": {"type": "boolean"},
             "is_file": {"type": "boolean"}, "is_dir": {"type": "boolean"},
             "size": {"type": "integer"}, "mtime": {"type": "number"}},
            ["path", "exists", "is_file", "is_dir", "size", "mtime"],
        ),
    ),
    SkillSpec(
        skill_id="read_text_head",
        purpose="Read the first N bytes of a UTF-8 text file.",
        risk="LOW",
        adapter_class="ReadTextHeadSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class ReadTextHeadSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.is_file():
                        raise ValueError(f"read_text_head: not a file: {p}")
                    n = int(args.get("max_bytes", 4096))
                    raw = p.read_bytes()[:n]
                    return {"path": str(p),
                            "content": raw.decode("utf-8", errors="replace"),
                            "bytes": len(raw),
                            "truncated": p.stat().st_size > n}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"path": {"type": "string"},
             "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576}},
            ["path"],
        ),
        output_schema=_schema_no_extra(
            {"path": {"type": "string"}, "content": {"type": "string"},
             "bytes": {"type": "integer"}, "truncated": {"type": "boolean"}},
            ["path", "content", "bytes", "truncated"],
        ),
    ),
    SkillSpec(
        skill_id="read_text_tail",
        purpose="Read the last N bytes of a UTF-8 text file.",
        risk="LOW",
        adapter_class="ReadTextTailSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class ReadTextTailSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    p = Path(args["path"]).resolve()
                    if not p.is_file():
                        raise ValueError(f"read_text_tail: not a file: {p}")
                    n = int(args.get("max_bytes", 4096))
                    raw = p.read_bytes()
                    tail = raw[-n:] if len(raw) > n else raw
                    return {"path": str(p),
                            "content": tail.decode("utf-8", errors="replace"),
                            "bytes": len(tail),
                            "truncated": len(raw) > n}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"path": {"type": "string"},
             "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576}},
            ["path"],
        ),
        output_schema=_schema_no_extra(
            {"path": {"type": "string"}, "content": {"type": "string"},
             "bytes": {"type": "integer"}, "truncated": {"type": "boolean"}},
            ["path", "content", "bytes", "truncated"],
        ),
    ),
    SkillSpec(
        skill_id="grep_text",
        purpose="Search for a literal pattern in a file (line-by-line).",
        risk="LOW",
        adapter_class="GrepTextSkill",
        body=textwrap.dedent("""
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
                                matches.append({"line": i, "text": line.rstrip("\\n")})
                                if len(matches) >= cap:
                                    break
                    return {"path": str(p), "pattern": pattern,
                            "matches": matches}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"path": {"type": "string"},
             "pattern": {"type": "string", "minLength": 1},
             "max_matches": {"type": "integer", "minimum": 1, "maximum": 500}},
            ["path", "pattern"],
        ),
        output_schema=_schema_no_extra(
            {"path": {"type": "string"}, "pattern": {"type": "string"},
             "matches": {"type": "array"}},
            ["path", "pattern", "matches"],
        ),
    ),
    SkillSpec(
        skill_id="find_files",
        purpose="Find files by glob under a root, bounded depth + count.",
        risk="LOW",
        adapter_class="FindFilesSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class FindFilesSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    root = Path(args["root"]).resolve()
                    if not root.is_dir():
                        raise ValueError(f"find_files: not a directory: {root}")
                    pattern = args.get("pattern", "*")
                    cap = int(args.get("max_results", 200))
                    hits: list[str] = []
                    for child in root.rglob(pattern):
                        if child.is_file():
                            hits.append(str(child))
                            if len(hits) >= cap:
                                break
                    return {"root": str(root), "pattern": pattern,
                            "files": hits, "truncated": len(hits) >= cap}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"root": {"type": "string"},
             "pattern": {"type": "string"},
             "max_results": {"type": "integer", "minimum": 1, "maximum": 2000}},
            ["root"],
        ),
        output_schema=_schema_no_extra(
            {"root": {"type": "string"}, "pattern": {"type": "string"},
             "files": {"type": "array"}, "truncated": {"type": "boolean"}},
            ["root", "pattern", "files", "truncated"],
        ),
    ),
    SkillSpec(
        skill_id="which_cmd",
        purpose="Locate an executable on PATH (shutil.which).",
        risk="LOW",
        adapter_class="WhichCmdSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            import shutil
            from typing import Any
            from acc.skills import Skill

            class WhichCmdSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    name = args["name"]
                    if not name.replace("-", "").replace("_", "").isalnum():
                        raise ValueError("which_cmd: name must be alnum + - _")
                    path = shutil.which(name)
                    return {"name": name, "path": path or "",
                            "found": path is not None}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"name": {"type": "string", "minLength": 1, "maxLength": 64}},
            ["name"],
        ),
        output_schema=_schema_no_extra(
            {"name": {"type": "string"}, "path": {"type": "string"},
             "found": {"type": "boolean"}},
            ["name", "path", "found"],
        ),
    ),
    SkillSpec(
        skill_id="env_get",
        purpose="Read an allowlisted environment variable.",
        risk="LOW",
        adapter_class="EnvGetSkill",
        body=textwrap.dedent("""
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
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"name": {"type": "string", "minLength": 1}}, ["name"],
        ),
        output_schema=_schema_no_extra(
            {"name": {"type": "string"}, "value": {"type": "string"},
             "present": {"type": "boolean"}},
            ["name", "value", "present"],
        ),
    ),
    SkillSpec(
        skill_id="pwd",
        purpose="Report the agent's current working directory.",
        risk="LOW",
        adapter_class="PwdSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            import os
            from typing import Any
            from acc.skills import Skill

            class PwdSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    return {"cwd": os.getcwd()}
        """).strip() + "\n",
        input_schema={"type": "object", "properties": {},
                      "additionalProperties": False},
        output_schema=_schema_no_extra(
            {"cwd": {"type": "string"}}, ["cwd"],
        ),
    ),
    SkillSpec(
        skill_id="disk_free",
        purpose="Report disk usage (total, used, free) for a path.",
        risk="LOW",
        adapter_class="DiskFreeSkill",
        body=textwrap.dedent("""
            from __future__ import annotations
            import shutil
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class DiskFreeSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    path = args.get("path", "/")
                    p = Path(path).resolve()
                    usage = shutil.disk_usage(p)
                    return {"path": str(p), "total": usage.total,
                            "used": usage.used, "free": usage.free}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"path": {"type": "string"}}, [],
        ),
        output_schema=_schema_no_extra(
            {"path": {"type": "string"}, "total": {"type": "integer"},
             "used": {"type": "integer"}, "free": {"type": "integer"}},
            ["path", "total", "used", "free"],
        ),
    ),
    # ---- Git skills (auto-granted only for code-domain roles) ----
    SkillSpec(
        skill_id="git_status",
        purpose="Run `git status --short` in a workspace directory.",
        risk="MEDIUM",
        adapter_class="GitStatusSkill",
        domain_id="git",
        body=textwrap.dedent("""
            from __future__ import annotations
            import subprocess
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class GitStatusSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    cwd = Path(args["cwd"]).resolve()
                    if not cwd.is_dir():
                        raise ValueError(f"git_status: not a directory: {cwd}")
                    try:
                        res = subprocess.run(
                            ["git", "status", "--short", "--untracked-files=all"],
                            cwd=str(cwd), capture_output=True, text=True,
                            timeout=15, check=False,
                        )
                    except subprocess.TimeoutExpired:
                        raise ValueError("git_status: timeout")
                    return {"cwd": str(cwd), "returncode": res.returncode,
                            "stdout": res.stdout[:8192],
                            "stderr": res.stderr[:2048]}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"cwd": {"type": "string"}}, ["cwd"],
        ),
        output_schema=_schema_no_extra(
            {"cwd": {"type": "string"}, "returncode": {"type": "integer"},
             "stdout": {"type": "string"}, "stderr": {"type": "string"}},
            ["cwd", "returncode", "stdout", "stderr"],
        ),
    ),
    SkillSpec(
        skill_id="git_log_recent",
        purpose="Run `git log -n N --oneline` in a workspace directory.",
        risk="MEDIUM",
        adapter_class="GitLogRecentSkill",
        domain_id="git",
        body=textwrap.dedent("""
            from __future__ import annotations
            import subprocess
            from pathlib import Path
            from typing import Any
            from acc.skills import Skill

            class GitLogRecentSkill(Skill):
                async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
                    cwd = Path(args["cwd"]).resolve()
                    if not cwd.is_dir():
                        raise ValueError(f"git_log_recent: not a directory: {cwd}")
                    n = max(1, min(int(args.get("limit", 20)), 200))
                    try:
                        res = subprocess.run(
                            ["git", "log", f"-n{n}", "--oneline", "--no-color"],
                            cwd=str(cwd), capture_output=True, text=True,
                            timeout=15, check=False,
                        )
                    except subprocess.TimeoutExpired:
                        raise ValueError("git_log_recent: timeout")
                    return {"cwd": str(cwd), "returncode": res.returncode,
                            "stdout": res.stdout[:16384],
                            "stderr": res.stderr[:2048]}
        """).strip() + "\n",
        input_schema=_schema_no_extra(
            {"cwd": {"type": "string"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            ["cwd"],
        ),
        output_schema=_schema_no_extra(
            {"cwd": {"type": "string"}, "returncode": {"type": "integer"},
             "stdout": {"type": "string"}, "stderr": {"type": "string"}},
            ["cwd", "returncode", "stdout", "stderr"],
        ),
    ),
]


SHELL_EXEC_YAML = """\
# skills/shell_exec/skill.yaml — opt-in arbitrary shell execution.
#
# OpenSpec `20260603-capability-pool` Phase 1.2.  HIGH-risk skill
# gated by ``requires_actions: [execute_shell]`` AND by the
# operator-approved Compliance oversight queue (every invocation
# enqueues an OVERSIGHT_SUBMIT; the operator approves per call).
#
# Roles must additionally set ``max_skill_risk_level: HIGH`` to use
# this skill (default is MEDIUM, which rejects HIGH).  Only flipped
# on for the engineering family: coding_agent + variants,
# devops_engineer, data_engineer, ml_engineer.

purpose:        "Run a process (argv list, no shell expansion) and return stdout/stderr."
version:        "0.1.0"
adapter_class:  "ShellExecSkill"
risk_level:     "HIGH"
domain_id:      "os_basics"
tags:           ["shell", "subprocess", "engineering"]

requires_actions:
  - "execute_shell"

input_schema:
  type: object
  properties:
    argv:
      type: array
      items: { type: string }
      minItems: 1
      maxItems: 64
      description: "Argument vector.  shell=False; no string passed to sh."
    cwd:
      type: string
      description: "Working directory (must exist)."
    timeout_s:
      type: integer
      minimum: 1
      maximum: 600
      description: "Subprocess timeout in seconds (default 30, max 600)."
  required: ["argv"]
  additionalProperties: false

output_schema:
  type: object
  properties:
    returncode: { type: integer }
    stdout: { type: string }
    stderr: { type: string }
    duration_s: { type: number }
  required: ["returncode", "stdout", "stderr", "duration_s"]
  additionalProperties: false

description: |
  Execute a process via subprocess.run(argv, shell=False, …).  Never
  invokes a shell; no string expansion.  Every call is HIGH risk and
  requires both an operator-approved oversight queue verdict and the
  role's ``execute_shell`` action label.  Output capped at 64 KB
  stdout + 16 KB stderr.
"""

SHELL_EXEC_ADAPTER = '''\
"""shell_exec — opt-in HIGH-risk argv subprocess.

OpenSpec `20260603-capability-pool` Phase 1.2.  No shell expansion,
no string interpolation; argv list only.  Every call needs
`requires_actions: [execute_shell]` on the role AND an approved
oversight verdict (A-017 enforced upstream)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from acc.skills import Skill


class ShellExecSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        argv = list(args["argv"])
        if not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError("shell_exec: argv must be a non-empty list of strings")
        cwd_arg = args.get("cwd")
        if cwd_arg:
            cwd = Path(cwd_arg).resolve()
            if not cwd.is_dir():
                raise ValueError(f"shell_exec: cwd not a directory: {cwd}")
            cwd_s = str(cwd)
        else:
            cwd_s = None
        timeout_s = int(args.get("timeout_s", 30))
        start = time.monotonic()
        try:
            res = subprocess.run(
                argv,
                cwd=cwd_s,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"shell_exec: timeout after {timeout_s}s"
            ) from exc
        except FileNotFoundError as exc:
            raise ValueError(f"shell_exec: command not found: {argv[0]!r}") from exc
        duration = time.monotonic() - start
        return {
            "returncode": res.returncode,
            "stdout": (res.stdout or "")[:65536],
            "stderr": (res.stderr or "")[:16384],
            "duration_s": round(duration, 3),
        }
'''


def _yaml_for(spec: SkillSpec) -> str:
    import yaml as _y
    body = {
        "purpose": spec.purpose,
        "version": "0.1.0",
        "adapter_class": spec.adapter_class,
        "risk_level": spec.risk,
        "domain_id": spec.domain_id,
        "tags": ["os_basics"] if spec.domain_id == "os_basics" else ["git"],
        "input_schema": spec.input_schema,
        "output_schema": spec.output_schema,
        "description": spec.purpose,
    }
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
    # shell_exec — handcrafted.
    se = skills_dir / "shell_exec"
    se.mkdir(parents=True, exist_ok=True)
    (se / "skill.yaml").write_text(SHELL_EXEC_YAML, encoding="utf-8")
    (se / "adapter.py").write_text(SHELL_EXEC_ADAPTER, encoding="utf-8")
    (se / "__init__.py").write_text("", encoding="utf-8")
    print(f"generated {len(SKILLS)} OS-basics skills + shell_exec under {skills_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

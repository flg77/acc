"""Agent-configuration posture scan (backlog EC-02, vault proposal 058 Phase 5).

From the ECC evaluation (2026-10-15): ACC checks what an agent *does* (Cat-A,
guardrails, oversight) but nothing scanned how its *configuration* exposes it.
That means a role granting what its own ceiling refuses, code execution
without a sandbox, an unpinned package in an agentset, or a hook allowlist
that admits a shell.  ACC's configuration is typed and signed, so these are
facts to check rather than heuristics.

The scan is also the **check before a grant**.  "Ask once" (a mission) and a
standing grant both spare questions, so neither is offered for a role whose
configuration has a HIGH finding.  :func:`blocking_for` is what the
dispatcher asks.

Findings are ``PS-NN``; ``to_sarif`` makes them a SARIF 2.1.0 log for the
compliance tooling.

061 makes the scan the **role QA gate**: PS-07 (a granted capability without a
verified digest), PS-08 (exec or host access and the profile does not require
the sandbox) and PS-09 (``default_operating_mode`` unset), ``acc-cli posture
scan --fail-on <level>``, and ``tests/test_role_profiles_posture.py`` over
every role profile.  Each finding is one line that says what to change.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
BLOCKING = frozenset({"HIGH", "CRITICAL"})

_EXEC_SKILLS = frozenset({"shell_exec", "python_exec", "ssh_exec"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3",
                     "perl", "ruby", "node", "pwsh", "powershell", "cmd", "env"})
_EXACT_PIN = re.compile(r"^@?[\w.\-]+/[\w.\-]+@\d+\.\d+\.\d+(?:[-+][\w.\-]+)?$")

RULES = {
    "PS-01": "A role grants a skill above its own risk ceiling",
    "PS-02": "A role grants an MCP server above its own risk ceiling",
    "PS-03": "A role may execute code and no sandbox is configured",
    "PS-04": "An agentset requires a package without an exact version",
    "PS-05": "The hook allowlist admits a shell or an interpreter",
    "PS-06": "The sandbox requirement is switched off",
    "PS-07": "A granted skill or MCP has no verified digest",
    "PS-08": "A role may execute code or reach a host and its profile does not require the sandbox",
    "PS-09": "A role leaves default_operating_mode unset",
}

LEVELS = tuple(_RANK)


def at_or_above(findings: Iterable["Finding"], level: str) -> list["Finding"]:
    """The findings whose severity is *level* or worse (the ``--fail-on`` test)."""
    floor = _RANK[level.upper()]
    return [f for f in findings if _RANK.get(f.severity, 1) >= floor]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    subject: str
    message: str
    role: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _exceeds(risk: str, ceiling: str) -> bool:
    return _RANK.get(str(risk).upper(), 1) > _RANK.get(str(ceiling).upper(), 1)


def scan_role(
    role: Any,
    *,
    name: str = "",
    skill_manifest: Callable[[str], Any] = lambda _id: None,
    mcp_manifest: Callable[[str], Any] = lambda _id: None,
    env: Optional[dict] = None,
    source: str = "",
) -> list[Finding]:
    """Findings for one role definition (PS-01..PS-03, PS-08, PS-09).

    *source* is where the role's profile lives (``roles/<name>/role.yaml``),
    named in the line that says what to change.
    """
    e = os.environ if env is None else env
    rname = name or getattr(role, "domain_id", "") or "role"
    where = source or f"roles/{rname}/role.yaml"
    out: list[Finding] = []
    for sid in getattr(role, "allowed_skills", None) or []:
        m = skill_manifest(sid)
        risk = str(getattr(m, "risk_level", "") or "") if m is not None else ""
        if risk and _exceeds(risk, getattr(role, "max_skill_risk_level", "MEDIUM")):
            out.append(Finding(
                "PS-01", "HIGH" if risk.upper() == "CRITICAL" else "MEDIUM", f"{rname}/skill:{sid}",
                f"{rname} grants {sid} ({risk}) above its max_skill_risk_level "
                f"{getattr(role, 'max_skill_risk_level', 'MEDIUM')}: every call is refused or escalated",
                rname,
            ))
    for mid in getattr(role, "allowed_mcps", None) or []:
        m = mcp_manifest(mid)
        risk = str(getattr(m, "risk_level", "") or "") if m is not None else ""
        if risk and _exceeds(risk, getattr(role, "max_mcp_risk_level", "MEDIUM")):
            out.append(Finding(
                "PS-02", "HIGH" if risk.upper() == "CRITICAL" else "MEDIUM", f"{rname}/mcp:{mid}",
                f"{rname} grants MCP {mid} ({risk}) above its max_mcp_risk_level "
                f"{getattr(role, 'max_mcp_risk_level', 'MEDIUM')}", rname,
            ))
    execs = sorted(set(getattr(role, "allowed_skills", None) or []) & _EXEC_SKILLS)
    if execs and not str(e.get("ACC_SANDBOX_NAME", "")).strip():
        # Required (the default): exec refuses, nothing reaches the host -- the
        # finding says code execution is unavailable.  Not required (a
        # developer's box): exec runs on the host -- PS-06 names the drift.
        required = str(e.get("ACC_SANDBOX_REQUIRED", "")).strip().lower() not in ("0", "false", "no", "off")
        out.append(Finding(
            "PS-03", "LOW" if required else "MEDIUM", f"{rname}/{','.join(execs)}",
            f"{rname} may run {', '.join(execs)} and this agent has no OpenShell sandbox "
            f"(ACC_SANDBOX_NAME): {'those calls refuse' if required else 'the code runs on the host'}",
            rname,
        ))
    if execs and str(getattr(role, "sandbox", "") or "").strip().lower() != "required":
        out.append(Finding(
            "PS-08", "MEDIUM", f"{rname}/sandbox",
            f"{rname}: may run {', '.join(execs)} and its profile does not require the sandbox "
            f"— set sandbox: required in {where}",
            rname,
        ))
    fields_set = getattr(role, "model_fields_set", None)
    if fields_set is not None and "default_operating_mode" not in fields_set:
        out.append(Finding(
            "PS-09", "MEDIUM", f"{rname}/default_operating_mode",
            f"{rname}: default_operating_mode is unset, so the role runs on whatever the "
            f"runtime default is — set default_operating_mode (ASK_ONCE, ASK_PERMISSIONS, "
            f"PLAN, ACCEPT_EDITS or AUTO) in {where}",
            rname,
        ))
    return out


def scan_capabilities(
    granted: dict[tuple[str, str], list[str]],
    locate: Callable[[str, str], Optional[Path]],
) -> list[Finding]:
    """PS-07 once per granted capability (061).

    *granted* is ``{(kind, name): [role, ...]}``; *locate* returns the
    directory the registry would load ``(kind, name)`` from, or None when
    nothing provides it (that is the capability validator's finding).
    """
    from acc import capability_verify as cv  # noqa: PLC0415

    out: list[Finding] = []
    for (kind, name), roles in sorted(granted.items()):
        path = locate(kind, name)
        if path is None:
            continue
        check = cv.check(kind, name, path)
        if check.ok:
            continue
        who = ", ".join(sorted(set(roles)))
        out.append(Finding(
            "PS-07", "HIGH" if check.status == cv.MISMATCH else "MEDIUM", f"{kind}:{name}",
            f"granted to {who}: {check.message()}",
        ))
    return out


def scan_environment(env: Optional[dict] = None) -> list[Finding]:
    """Findings that hold for the whole deployment (PS-05, PS-06)."""
    e = os.environ if env is None else env
    out: list[Finding] = []
    allow = [a.strip() for a in str(e.get("ACC_HOOK_ALLOWLIST", "")).replace(";", ",").split(",") if a.strip()]
    shells = sorted({a for a in allow if Path(a).name in _SHELLS})
    if shells:
        out.append(Finding(
            "PS-05", "HIGH", "ACC_HOOK_ALLOWLIST",
            f"the hook allowlist admits {', '.join(shells)}: anyone who can write hooks.yaml runs arbitrary code",
        ))
    if str(e.get("ACC_SANDBOX_REQUIRED", "")).strip().lower() in ("0", "false", "no", "off"):
        out.append(Finding(
            "PS-06", "MEDIUM", "ACC_SANDBOX_REQUIRED",
            "the sandbox requirement is off: exec skills run on the host (a developer's box only)",
        ))
    if str(e.get("ACC_OVERLAY_ALLOW_UNSIGNED", "")).strip().lower() in ("1", "true", "yes", "on"):
        out.append(Finding(
            "PS-07", "MEDIUM", "ACC_OVERLAY_ALLOW_UNSIGNED",
            "unsigned overlay capabilities are allowed: they load with no verified digest "
            "(a developer's box only) — unset ACC_OVERLAY_ALLOW_UNSIGNED",
        ))
    return out


def scan_collectives(directory: Path) -> list[Finding]:
    """PS-04 over every ``collectives/*.yaml``."""
    out: list[Finding] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        for ref in doc.get("required_packages") or []:
            ref = str(ref)
            if not _EXACT_PIN.match(ref):
                out.append(Finding(
                    "PS-04", "MEDIUM", f"{path.name}:{ref}",
                    f"{path.name} requires {ref} without an exact version: what runs can change "
                    "under a signed agentset (pin it, as the AgentBOM does)",
                ))
    return out


def scan_tree(
    repo_root: Path, *, env: Optional[dict] = None, include_installed: bool = True,
) -> list[Finding]:
    """The whole scan over a checkout: every role (in-tree and, with
    *include_installed*, every installed pack's), the capabilities they grant,
    the collectives, the environment."""
    from acc.mcp.registry import MCPRegistry  # noqa: PLC0415
    from acc.pkg.registry import Registry, installed_capability_dirs  # noqa: PLC0415
    from acc.role_loader import RoleLoader  # noqa: PLC0415
    from acc.skills.registry import SkillRegistry  # noqa: PLC0415

    skill_roots = [repo_root / "skills"]
    mcp_roots = [repo_root / "mcps"]
    role_roots: list[tuple[Path, str]] = [(repo_root / "roles", "roles")]
    if include_installed:
        skill_roots = installed_capability_dirs("skills") + skill_roots
        mcp_roots = installed_capability_dirs("mcps") + mcp_roots
        try:
            entries = Registry().list()
        except Exception:  # noqa: BLE001 -- no registry, no installed roles
            entries = []
        role_roots += [(Path(e.install_path) / "roles", f"{e.name}@{e.version}: roles")
                       for e in entries if (Path(e.install_path) / "roles").is_dir()]

    # One manifest lookup per kind over every root; the in-tree root is last
    # and wins, as in the registries' own dual-source load.
    skill_manifests: dict = {}
    mcp_manifests: dict = {}
    for root in skill_roots:
        reg = SkillRegistry()
        reg.load_from(root)
        skill_manifests.update(reg.manifests())
    for root in mcp_roots:
        reg = MCPRegistry()
        reg.load_from(root)
        mcp_manifests.update(reg.manifests())

    findings: list[Finding] = []
    granted: dict[tuple[str, str], list[str]] = {}
    for roles_root, label in role_roots:
        for d in sorted(p for p in roles_root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            if d.name.upper() == "TEMPLATE":
                continue
            try:
                role = RoleLoader(roles_root, d.name).load()
            except Exception:  # noqa: BLE001 -- an unloadable role is the role validator's finding
                continue
            if role is None:
                continue
            findings += scan_role(role, name=d.name, skill_manifest=skill_manifests.get,
                                  mcp_manifest=mcp_manifests.get, env=env,
                                  source=f"{label}/{d.name}/role.yaml")
            for sid in getattr(role, "allowed_skills", None) or []:
                granted.setdefault(("skill", sid), []).append(d.name)
            for mid in getattr(role, "allowed_mcps", None) or []:
                granted.setdefault(("mcp", mid), []).append(d.name)

    def _locate(kind: str, name: str) -> Optional[Path]:
        roots = skill_roots if kind == "skill" else mcp_roots
        for root in reversed(roots):   # the in-tree root is last = authoritative
            if (root / name).is_dir():
                return root / name
        return None

    findings += scan_capabilities(granted, _locate)
    findings += scan_collectives(repo_root / "collectives")
    findings += scan_environment(env)
    return findings


# ---------------------------------------------------------------------------
# The check before a grant
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, list[Finding]]] = {}
_TTL_S = 300.0


def blocking_for(role: Any, core: Any = None, *, env: Optional[dict] = None) -> list[Finding]:
    """HIGH / CRITICAL findings that stop a mission or a standing grant being
    offered for *role* (cached five minutes per role)."""
    rname = getattr(role, "domain_id", "") or "role"
    key = f"{rname}|{sorted(getattr(role, 'allowed_skills', None) or [])}|{(env or os.environ).get('ACC_SANDBOX_NAME', '')}"
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _TTL_S:
        return hit[1]
    skill_reg = getattr(core, "_skill_registry", None)
    mcp_reg = getattr(core, "_mcp_registry", None)
    findings = scan_role(
        role, name=rname,
        skill_manifest=(skill_reg.manifest if skill_reg is not None else (lambda _i: None)),
        mcp_manifest=(mcp_reg.manifest if mcp_reg is not None else (lambda _i: None)),
        env=env,
    ) + scan_environment(env)
    blocking = [f for f in findings if f.severity in BLOCKING]
    _CACHE[key] = (time.monotonic(), blocking)
    return blocking


def _reset_cache() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------------------
# SARIF
# ---------------------------------------------------------------------------

_SARIF_LEVEL = {"LOW": "note", "MEDIUM": "warning", "HIGH": "error", "CRITICAL": "error"}


def to_sarif(findings: Iterable[Finding]) -> dict:
    findings = list(findings)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "acc-posture-scan",
                "informationUri": "https://github.com/flg77/acc",
                "rules": [{"id": rid, "shortDescription": {"text": text}} for rid, text in RULES.items()],
            }},
            "results": [{
                "ruleId": f.rule_id,
                "level": _SARIF_LEVEL.get(f.severity, "warning"),
                "message": {"text": f.message},
                "properties": {"severity": f.severity, "subject": f.subject, "role": f.role},
            } for f in findings],
        }],
    }

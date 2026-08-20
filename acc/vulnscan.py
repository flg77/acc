"""Vulnerability scanning beside signature verification.

ACC verifies that a package is **from who it says**. It does not check whether
what is inside it is known-vulnerable, and those are different questions: a
correctly signed package containing a dependency with a published advisory
passes every check ACC currently makes.

The design decision is what a finding *does*. Blocking an install on a
vulnerability is tempting and brittle — advisory data is noisy, severity is
contextual, and a hard block turns a false positive into an outage at the worst
possible moment. ACC has a mechanism built for consequential judgements with the
evidence attached: the oversight queue. So a finding **raises a decision**, and
only an explicitly configured severity floor fails a command outright, for
automation that wants that.

**Advisory data is local and its age is reported.** A scanner that silently
returns "no findings" because it could not reach a feed is worse than one that
refuses: the operator reads a clean report and concludes there is nothing to
fix. Staleness and unavailability are stated in the result, never inferred as
safety.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("acc.vulnscan")

ADVISORY_PATH_VAR = "ACC_ADVISORY_DB"
DEFAULT_ADVISORY_FILE = "advisories.json"

SEVERITIES = ("low", "medium", "high", "critical")
_RANK = {name: i for i, name in enumerate(SEVERITIES)}

#: Advisory data older than this is reported as stale. Not an error — an
#: air-gapped site may legitimately refresh weekly — but never silent.
STALE_AFTER_S = 7 * 24 * 3600


class ScanError(Exception):
    """A scan could not be performed. The message is operator-facing."""


@dataclass(frozen=True)
class Advisory:
    """One published advisory."""

    id: str
    package: str
    severity: str
    summary: str
    affected: tuple[str, ...] = ()      # exact versions considered affected
    fixed_in: str = ""

    def applies_to(self, version: str) -> bool:
        # Exact-version matching only. Range parsing across ecosystems is where
        # scanners get subtly wrong, and a wrong "not affected" is worse than
        # asking the operator to keep the list precise.
        return not self.affected or version in self.affected


@dataclass(frozen=True)
class Finding:
    """A vulnerable component found in something ACC runs or installs."""

    advisory: Advisory
    package: str
    version: str
    where: str          # "runtime" | package name

    @property
    def severity(self) -> str:
        return self.advisory.severity

    def as_dict(self) -> dict[str, Any]:
        return {
            "advisory": self.advisory.id,
            "package": self.package,
            "version": self.version,
            "severity": self.severity,
            "summary": self.advisory.summary,
            "fixed_in": self.advisory.fixed_in,
            "where": self.where,
        }


@dataclass
class ScanResult:
    """Findings, plus an honest account of the data they came from."""

    findings: list[Finding] = field(default_factory=list)
    scanned: int = 0
    advisory_count: int = 0
    advisory_age_s: float | None = None
    unavailable: str = ""

    @property
    def usable(self) -> bool:
        """False when no advisory data was available. NOT the same as clean."""
        return not self.unavailable

    @property
    def stale(self) -> bool:
        return self.advisory_age_s is not None and self.advisory_age_s > STALE_AFTER_S

    def worst(self) -> str:
        if not self.findings:
            return ""
        return max((f.severity for f in self.findings), key=lambda s: _RANK.get(s, 0))

    def at_or_above(self, floor: str) -> list[Finding]:
        threshold = _RANK.get(floor, 0)
        return [f for f in self.findings if _RANK.get(f.severity, 0) >= threshold]

    def as_dict(self) -> dict[str, Any]:
        return {
            "usable": self.usable,
            "unavailable": self.unavailable,
            "stale": self.stale,
            "advisory_count": self.advisory_count,
            "advisory_age_s": self.advisory_age_s,
            "scanned": self.scanned,
            "worst": self.worst(),
            "findings": [f.as_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Advisory data
# ---------------------------------------------------------------------------


def normalise(name: str) -> str:
    """One spelling for a package name, used on both sides of a comparison."""
    return str(name).strip().lower().replace("_", "-")


def advisory_path(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(ADVISORY_PATH_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_ADVISORY_FILE


def load_advisories(
    repo_root: Path | None = None,
) -> tuple[list[Advisory], float | None, str]:
    """Read the local advisory database.

    Returns ``(advisories, age_seconds, unavailable_reason)``. An unreadable or
    absent database yields an explicit reason — never an empty list that would
    read as "nothing to fix".
    """
    path = advisory_path(repo_root)
    if not path.is_file():
        return [], None, (
            f"no advisory database at {path} — set {ADVISORY_PATH_VAR} or place "
            f"{DEFAULT_ADVISORY_FILE} there. Scanning cannot report anything "
            f"without it."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], None, f"advisory database unreadable: {exc}"

    entries = raw.get("advisories") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return [], None, "advisory database has no 'advisories' list"

    out: list[Advisory] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "medium")).lower()
        if severity not in _RANK:
            severity = "medium"
        out.append(
            Advisory(
                id=str(item.get("id", "")),
                package=normalise(item.get("package", "")),
                severity=severity,
                summary=str(item.get("summary", "")),
                affected=tuple(str(v) for v in (item.get("affected") or [])),
                fixed_in=str(item.get("fixed_in", "")),
            )
        )
    age = max(0.0, time.time() - path.stat().st_mtime)
    return [a for a in out if a.id and a.package], age, ""


# ---------------------------------------------------------------------------
# Component collection
# ---------------------------------------------------------------------------


def installed_components() -> dict[str, str]:
    """Distributions this deployment actually runs, name -> version."""
    from importlib.metadata import distributions  # noqa: PLC0415

    out: dict[str, str] = {}
    for dist in distributions():
        try:
            name = normalise(dist.metadata["Name"] or "")
            version = dist.version or ""
        except Exception:  # pragma: no cover — malformed metadata
            continue
        if name:
            out[name] = version
    return out


def package_components(manifest_path: str | Path) -> dict[str, str]:
    """Components a built ACC package declares, name -> version.

    Reads the package's own ``depends_on`` block. Bundled third-party content
    with no declared version cannot be matched against an advisory, and is
    skipped rather than guessed at.
    """
    path = Path(manifest_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScanError(f"cannot read package manifest {path}: {exc}") from exc

    out: dict[str, str] = {}
    for dep in raw.get("depends_on") or []:
        if isinstance(dep, dict) and dep.get("name") and dep.get("version"):
            name = normalise(dep["name"])
            out[name] = str(dep["version"])
    return out


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def scan(
    components: dict[str, str],
    *,
    where: str = "runtime",
    repo_root: Path | None = None,
) -> ScanResult:
    """Match *components* against the advisory database. Never raises."""
    advisories, age, unavailable = load_advisories(repo_root)
    result = ScanResult(
        scanned=len(components),
        advisory_count=len(advisories),
        advisory_age_s=age,
        unavailable=unavailable,
    )
    if unavailable:
        return result

    by_package: dict[str, list[Advisory]] = {}
    for advisory in advisories:
        by_package.setdefault(advisory.package, []).append(advisory)

    for raw_name, version in sorted(components.items()):
        # Normalise on BOTH sides. Advisory data and installed metadata
        # disagree about case and separators constantly ("PyYAML" vs
        # "pyyaml", "typing_extensions" vs "typing-extensions"), and a
        # mismatch here hides a real vulnerability behind a clean report --
        # the exact failure this module exists to prevent.
        name = normalise(raw_name)
        for advisory in by_package.get(name, []):
            if advisory.applies_to(version):
                result.findings.append(
                    Finding(
                        advisory=advisory, package=raw_name, version=version, where=where
                    )
                )
    return result


def scan_runtime(repo_root: Path | None = None) -> ScanResult:
    """Scan what this deployment is running right now."""
    return scan(installed_components(), where="runtime", repo_root=repo_root)


# ---------------------------------------------------------------------------
# Decision path
# ---------------------------------------------------------------------------


def as_oversight_proposal(result: ScanResult, *, context: str = "") -> dict[str, Any]:
    """Turn findings into an oversight item with the evidence attached.

    A finding produces a *decision path*, not an unexplained failure. The
    payload carries every finding so the person deciding sees what the scanner
    saw, rather than a severity label they have to go and investigate.
    """
    findings = result.findings
    worst = result.worst() or "none"
    return {
        "kind": "vulnerability_findings",
        "risk_level": "HIGH" if worst in ("high", "critical") else "MEDIUM",
        "summary": (
            f"{len(findings)} vulnerable component(s) found"
            f"{' in ' + context if context else ''}; worst severity {worst}"
        ),
        "rationale": (
            "Advisory data is noisy and severity is contextual, so this is a "
            "decision rather than a block. The evidence is attached."
        ),
        "evidence": {
            "advisory_count": result.advisory_count,
            "advisory_age_s": result.advisory_age_s,
            "stale": result.stale,
            "findings": [f.as_dict() for f in findings],
        },
    }


def exit_code(result: ScanResult, *, fail_on: str = "") -> int:
    """Exit code for automation.

    ``fail_on`` is opt-in: without it a scan reports and succeeds, because a
    hard block on advisory data turns a false positive into an outage.

    An **unusable** scan always fails, whatever the floor. "I could not check"
    must never be reported to a pipeline as "nothing to fix".
    """
    if not result.usable:
        return 2
    if fail_on and result.at_or_above(fail_on):
        return 1
    return 0

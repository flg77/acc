"""Governance-layer inventory (PR-Z1a, Compliance enhancements).

Display-only loader that tells the Compliance pane *what governance is
actually loaded*: the Cat-A / Cat-B / Cat-C policy files, their version,
and the rules each declares.  It does NOT enforce anything (the OPA/WASM
engine does that) — it parses the human-readable rule annotations the
``regulatory_layer/`` Rego files already carry so the operator can see +
browse the hierarchy.

The parse is deliberately lightweight (regex, no OPA/rego dependency):

* Version header  ``# Version: 0.6.0``
* Cat-A / Cat-B rule + inline summary  ``# A-001: <summary>``
* Cat-C auto rule  ``# C-AUTO-20260402-001`` with the summary on a
  following ``# Context: …`` comment line.

A parse miss degrades to an empty summary rather than failing — the pane
must never crash because a policy file was reformatted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# category key → (dir name, human title, immutable?)
_CATEGORIES: list[tuple[str, str, str, bool]] = [
    ("A", "category_a", "Cat A — Constitutional (immutable)", True),
    ("B", "category_b", "Cat B — Conditional setpoints (live-updatable)", False),
    ("C", "category_c", "Cat C — Adaptive learned (auto-generated)", False),
]

_VERSION_RE = re.compile(r"^#\s*Version:\s*(\S+)", re.IGNORECASE)
# A-001 / B-012 / C-AUTO-20260402-001 (+ optional trailing note/colon).
_RULE_RE = re.compile(
    r"^#\s*((?:A|B)-\d{2,4}|C-AUTO-[0-9]{6,8}-[0-9]{2,4})\b[:)\s]*(.*)$"
)
# Lines we never want to treat as a rule's summary when scanning ahead.
_META_PREFIXES = (
    "source", "setpoint", "biological", "levin", "enforcement",
    "confidence", "bundle", "arbiter", "package", "import",
)


@dataclass
class GovernanceRule:
    rule_id: str
    summary: str
    source_path: str
    line: int


@dataclass
class GovernanceLayer:
    category: str          # "A" / "B" / "C"
    title: str
    version: str           # "" when no Version: header found
    immutable: bool
    file_paths: list[str] = field(default_factory=list)
    rules: list[GovernanceRule] = field(default_factory=list)

    @property
    def rule_count(self) -> int:
        return len(self.rules)


def regulatory_root() -> Path:
    """Resolve the ``regulatory_layer`` root.

    Precedence: ``ACC_REGULATORY_ROOT`` env > ``<repo>/regulatory_layer``
    > ``/app/regulatory_layer`` (the in-container mount).
    """
    raw = os.environ.get("ACC_REGULATORY_ROOT", "").strip()
    if raw:
        return Path(raw)
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / "regulatory_layer"
    if candidate.is_dir():
        return candidate
    return Path("/app/regulatory_layer")


def _clean_summary(text: str) -> str:
    """Strip leading punctuation / parentheticals from an inline summary."""
    t = text.strip()
    # Drop a leading parenthetical note like "(carried from v0.1.0)".
    if t.startswith("(") and ")" in t:
        t = t[t.index(")") + 1:].strip()
    return t.strip(" :-")


def _is_meta_line(comment_body: str) -> bool:
    low = comment_body.strip().lower()
    return any(low.startswith(p) for p in _META_PREFIXES)


def parse_rego_file(path: Path) -> tuple[str, list[GovernanceRule]]:
    """Parse one ``.rego`` file → (version, rules).  Best-effort."""
    version = ""
    rules: list[GovernanceRule] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return version, rules

    for i, line in enumerate(lines):
        if not version:
            mv = _VERSION_RE.match(line)
            if mv:
                version = mv.group(1)
        mr = _RULE_RE.match(line)
        if not mr:
            continue
        rule_id = mr.group(1)
        if rule_id in seen:
            continue
        seen.add(rule_id)
        summary = _clean_summary(mr.group(2))
        # Cat-C ids carry the description on a following ``# Context:``
        # (or the next non-meta comment) line — scan a few lines ahead.
        if not summary:
            for j in range(i + 1, min(i + 8, len(lines))):
                nxt = lines[j]
                if not nxt.lstrip().startswith("#"):
                    break
                body = nxt.lstrip("#").strip()
                if not body or set(body) <= {"-", "=", " "}:
                    continue
                low = body.lower()
                if low.startswith("context:"):
                    summary = body.split(":", 1)[1].strip()
                    break
                if not _is_meta_line(body):
                    summary = body
                    break
        rules.append(
            GovernanceRule(
                rule_id=rule_id,
                summary=summary,
                source_path=str(path),
                line=i + 1,
            )
        )
    return version, rules


def load_layer(category: str, root: Path | None = None) -> GovernanceLayer:
    """Load one governance layer ("A"/"B"/"C")."""
    root = root or regulatory_root()
    meta = next((c for c in _CATEGORIES if c[0] == category), None)
    if meta is None:
        raise ValueError(f"unknown category {category!r}")
    _cat, dirname, title, immutable = meta
    layer = GovernanceLayer(
        category=category, title=title, version="", immutable=immutable,
    )
    cat_dir = root / dirname
    if not cat_dir.is_dir():
        return layer
    for rego in sorted(cat_dir.glob("*.rego")):
        version, rules = parse_rego_file(rego)
        layer.file_paths.append(str(rego))
        if version and not layer.version:
            layer.version = version
        layer.rules.extend(rules)
    # Cat-B also ships a setpoint data file — list it so it's browsable.
    data_json = cat_dir / "data_rhoai.json"
    if data_json.is_file():
        layer.file_paths.append(str(data_json))
    return layer


def load_all_layers(root: Path | None = None) -> list[GovernanceLayer]:
    """Load Cat-A, Cat-B, Cat-C in order."""
    root = root or regulatory_root()
    return [load_layer(cat, root) for cat, *_ in _CATEGORIES]


def list_frameworks(root: Path | None = None) -> list[str]:
    """Enumerate imported framework-catalog file stems under
    ``regulatory_layer/frameworks/`` (Phase 2 populates this; Phase 1
    returns [] when the dir is absent)."""
    root = root or regulatory_root()
    fw_dir = root / "frameworks"
    if not fw_dir.is_dir():
        return []
    return sorted(p.stem for p in fw_dir.glob("*.yaml"))

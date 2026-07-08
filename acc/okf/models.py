"""Data model for Open Knowledge Format (OKF) bundles.

OKF v0.1 (Google Cloud, 2026-06-12): a bundle is a directory of markdown
"concepts". Each concept = a YAML frontmatter block + a markdown body, and the
ONLY required frontmatter field is a non-empty ``type``. Two filenames are
reserved — ``index.md`` (directory listing) and ``log.md`` (update history) —
and carry no frontmatter. Concepts cross-link with ordinary (preferably
bundle-relative ``/…``) markdown links; consumers MUST tolerate broken links.

This module is pure data + no I/O — the parse / validate / emit / transform
modules act on it. See the ACC Roadmap proposal "Open Knowledge Format (OKF) in
ACC" for the design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Reserved filenames (§2, §6 of the spec) — these are NOT concepts.
RESERVED_FILENAMES: frozenset[str] = frozenset({"index.md", "log.md"})


@dataclass
class Concept:
    """One OKF concept document (a non-reserved ``.md`` file)."""

    rel_path: str          # POSIX path relative to the bundle root, e.g. "notes/foo.md"
    frontmatter: dict      # parsed YAML front matter (may be {} if absent/invalid)
    body: str              # the markdown body (everything after the front matter)
    fm_ok: bool = True     # False when the front matter block was present but unparseable

    @property
    def type(self) -> str:
        return str(self.frontmatter.get("type", "") or "").strip()

    @property
    def title(self) -> str:
        return str(self.frontmatter.get("title", "") or "").strip()

    @property
    def is_conformant(self) -> bool:
        """A concept conforms when it has a parseable front matter with a
        non-empty ``type`` (spec conformance rules 1 + 2)."""
        return self.fm_ok and bool(self.type)


@dataclass
class Bundle:
    """A loaded OKF bundle: its concepts + the reserved files it contains."""

    root: Path
    concepts: list[Concept] = field(default_factory=list)
    reserved: list[str] = field(default_factory=list)   # rel paths of index.md/log.md

    def types(self) -> list[str]:
        """Sorted, de-duplicated ``type`` values across the bundle — the
        vocabulary this bundle actually uses."""
        return sorted({c.type for c in self.concepts if c.type})

    def concept(self, rel_path: str) -> Concept | None:
        for c in self.concepts:
            if c.rel_path == rel_path:
                return c
        return None


@dataclass
class ConformanceIssue:
    """One conformance finding.  ``severity`` is ``error`` for the hard rules
    (§5.1–5.3) and ``warning`` for soft guidance a consumer must still tolerate."""

    rel_path: str
    rule: str          # "frontmatter" | "type" | "reserved"
    message: str
    severity: str = "error"   # "error" | "warning"


@dataclass
class ConformanceReport:
    """Result of :func:`acc.okf.validate.validate`."""

    conformant: bool
    n_concepts: int
    issues: list[ConformanceIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ConformanceIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ConformanceIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        n_err, n_warn = len(self.errors), len(self.warnings)
        verdict = "conformant" if self.conformant else "NOT conformant"
        return (f"{verdict}: {self.n_concepts} concept(s), "
                f"{n_err} error(s), {n_warn} warning(s)")

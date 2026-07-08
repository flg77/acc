"""OKF v0.1 conformance validation.

A bundle is **conformant** iff (§5 of the spec):
  1. every non-reserved ``.md`` has a parseable YAML front-matter block;
  2. every front-matter block has a non-empty ``type``;
  3. reserved files (``index.md`` / ``log.md``) follow their shape.

Rules 1 + 2 are ``error``s (they decide conformance).  Everything the spec says
a consumer MUST tolerate — missing optional fields, unknown types, broken
links, a reserved file that strayed — is reported as a ``warning`` and never
flips ``conformant`` to False.
"""

from __future__ import annotations

from pathlib import Path

from acc.okf.models import Bundle, ConformanceIssue, ConformanceReport


def validate(bundle: Bundle) -> ConformanceReport:
    """Validate *bundle* against the three conformance rules (tolerant)."""
    issues: list[ConformanceIssue] = []

    for c in bundle.concepts:
        # Rule 1 — a parseable front-matter block must be present.
        if not c.fm_ok:
            issues.append(ConformanceIssue(
                c.rel_path, "frontmatter",
                "missing or invalid YAML front matter", "error"))
            continue
        # Rule 2 — a non-empty `type`.
        if not c.type:
            issues.append(ConformanceIssue(
                c.rel_path, "type",
                "front matter has no non-empty `type`", "error"))
        # Soft guidance (never rejects) — recommended fields.
        if not c.title:
            issues.append(ConformanceIssue(
                c.rel_path, "title", "no `title` (recommended)", "warning"))

    # Rule 3 (soft here) — reserved files carry no front matter.
    for rel in bundle.reserved:
        try:
            head = (bundle.root / rel).read_text(encoding="utf-8").lstrip("﻿")
        except OSError:
            head = ""
        if head.lstrip().startswith("---"):
            issues.append(ConformanceIssue(
                rel, "reserved",
                f"{Path(rel).name} is reserved and should carry no front matter",
                "warning"))

    conformant = not any(i.severity == "error" for i in issues)
    return ConformanceReport(
        conformant=conformant, n_concepts=len(bundle.concepts), issues=issues)

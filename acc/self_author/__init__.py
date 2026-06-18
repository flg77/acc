"""Self-authoring — turn an APPROVED ``[ROLE_GAP:]`` finding into a draft pack.

Proposal 036 Step 2 / PR-2 (``feat/036-self-author``).

This package takes an operator-APPROVED ``new_role`` gap finding (019 G4,
:class:`acc.assistant.gap_analysis.RoleGapFinding`) and drafts a *reviewable*
candidate role package:

* a :class:`acc.config.RoleDefinitionConfig`-shaped ``role.yaml`` skeleton built
  deterministically from the gap's candidate skills/MCPs + evidence-derived
  task types,
* an LLM-filled ``purpose`` / ``persona`` / ``seed_context`` + a ``role.md``
  narrative (bounded to the skeleton; the LLM seam is **injectable** so tests
  pass a deterministic stub),
* a built candidate ``.accpkg`` staged under a **gitignored** ``proposed/``
  directory.

Nothing here publishes or installs.  The dispatch wiring that ROUTES an
approved gap into this path is PR-4 (out of scope); this package only exposes
the callable :func:`acc.self_author.author.author_role_from_gap` that PR-4 will
invoke.
"""

from __future__ import annotations

from acc.self_author.author import (
    AuthorResult,
    SelfAuthorError,
    author_role_from_gap,
    default_llm_fill,
)

__all__ = [
    "AuthorResult",
    "SelfAuthorError",
    "author_role_from_gap",
    "default_llm_fill",
]

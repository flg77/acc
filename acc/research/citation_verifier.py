"""Citation verification — defence against fabricated URLs (E5).

The autoresearcher demo's correctness story rests on the critic
persona re-fetching a sample of cited URLs to confirm the claim
mapping is honest.  This module implements the **post-run
analysis** side of that defence: given the synthesizer's final
report and a list of TASK_COMPLETE.invocations the run produced,
it computes which inline citations were *re-fetched at least once*
during the run.

A re-fetch is a `[MCP: web_fetch.fetch {"url": "..."}]` invocation
the critic (or any other persona) issued — the registry
auto-records it on TASK_COMPLETE.invocations because every MCP /
skill call lands there for audit.  The verifier:

1. Parses the report for inline citations of the form
   ``[N]`` plus the matching URL in a "## Citations" section.
2. Walks the run's invocations list, picking out entries with
   ``kind == "mcp"`` and ``target == "web_fetch.fetch"``.
3. Cross-references the cited URLs with the fetched URLs.
4. Emits a :class:`VerificationReport` with per-citation
   coverage + a single summary score.

The verifier is **read-only**: it does NOT re-fetch URLs itself.
Re-fetching at verify-time would double the cost of the demo.
The critic persona's runtime re-fetch (driven by its system
prompt) is the source of truth; this module only confirms the
operator can see the audit trail.

Used by:
* ``examples/acc_autoresearcher/verify.sh`` — exits non-zero when
  fewer than ``ACC_RESEARCH_MIN_VERIFIED_CITATIONS`` citations
  were re-fetched.
* Future Cat-C rule promotion: "runs where verification rate < N
  consistently produce low-scored reports" → suggest tightening
  the critic's system prompt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CitationEntry:
    """One cited URL extracted from the report.

    ``footnote`` is the integer the report uses inline (``[1]``,
    ``[2]``, …); operators can tie a row in the verification
    report back to a specific paragraph by grepping for the same
    footnote in the original markdown.
    """

    footnote: int
    url: str
    paywalled: bool = False  # parsed from ``[N] (paywalled)`` marker
    refetched: bool = False
    refetch_count: int = 0


@dataclass
class VerificationReport:
    """Output of :func:`verify_against_invocations`.

    Composed for ``verify.sh`` to print + an exit-code decision.

    ``coverage_rate`` is the fraction of cited URLs that were
    re-fetched at least once during the run.  ``ok`` is True iff
    coverage_rate >= the threshold passed to
    :func:`summarise` — the operator-tunable knob.
    """

    citations: list[CitationEntry] = field(default_factory=list)
    refetch_urls: set[str] = field(default_factory=set)
    coverage_rate: float = 0.0
    threshold: float = 0.0
    ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "citations": [
                {
                    "footnote": c.footnote,
                    "url": c.url,
                    "paywalled": c.paywalled,
                    "refetched": c.refetched,
                    "refetch_count": c.refetch_count,
                }
                for c in self.citations
            ],
            "refetch_urls": sorted(self.refetch_urls),
            "coverage_rate": round(self.coverage_rate, 3),
            "threshold": self.threshold,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# Regex shapes — kept module-private so callers see only the
# functional API.
# ---------------------------------------------------------------------------

# A "## Citations" section heading in the report.  We accept both
# H1 and H2 in case a synthesizer slips up on the section level.
_CITATIONS_HEADING_RE = re.compile(
    r"^\s{0,3}#{1,3}\s+citations?\s*$", re.IGNORECASE | re.MULTILINE,
)

# Lines under that heading look like:
#     [1] https://example.com/paper.pdf
#     [2] (paywalled) https://example.com/research/blocked
#     [3] https://example.com/foo (paywalled)
_FOOTNOTE_LINE_RE = re.compile(
    r"""^\s*
    \[(?P<num>\d+)\]\s*                                  # footnote number
    (?P<paywalled_pre>\(paywalled\))?\s*                  # optional paywall flag
    (?P<url>https?://\S+?)                                # URL (greedy stop at ws)
    \s*(?P<paywalled_post>\(paywalled\))?\s*$             # optional trailing paywall flag
    """,
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_inline_citations(report_markdown: str) -> list[CitationEntry]:
    """Parse the report's Citations section into a list of entries.

    Returns ``[]`` when the report has no Citations heading.  The
    caller decides how to react — in practice, a missing Citations
    section is itself a critic finding, so verify.sh prints a
    distinct diagnostic.

    The Citations section is parsed greedily from its heading to
    end-of-document; any ``## NextSection`` heading after Citations
    bounds the scan.
    """
    if not report_markdown:
        return []

    # Locate the Citations heading.
    m = _CITATIONS_HEADING_RE.search(report_markdown)
    if m is None:
        return []
    start = m.end()

    # Bound at the next H1 / H2 (the operator may have appended
    # post-citation prose).
    after = report_markdown[start:]
    next_section = re.search(
        r"^\s{0,3}#{1,3}\s+\S", after, re.MULTILINE,
    )
    section_text = after if next_section is None else after[: next_section.start()]

    citations: list[CitationEntry] = []
    seen_footnotes: set[int] = set()
    for fm in _FOOTNOTE_LINE_RE.finditer(section_text):
        try:
            num = int(fm.group("num"))
        except ValueError:
            continue
        if num in seen_footnotes:
            continue
        seen_footnotes.add(num)
        url = fm.group("url").rstrip(".,;)")
        paywalled = bool(fm.group("paywalled_pre")) or bool(
            fm.group("paywalled_post")
        )
        citations.append(CitationEntry(
            footnote=num, url=url, paywalled=paywalled,
        ))
    return citations


# ---------------------------------------------------------------------------
# Cross-reference with run invocations
# ---------------------------------------------------------------------------


def _collect_refetch_urls(invocations: Iterable[dict]) -> dict[str, int]:
    """Walk a list of TASK_COMPLETE.invocations + return URL→count.

    Looks for entries where ``kind == "mcp"`` and ``target ==
    "web_fetch.fetch"``.  The args dict carries the URL on the
    ``url`` key (canonical contract from the web_fetch manifest).
    Entries whose schema does not match are skipped silently —
    we never raise on telemetry-malformed input.
    """
    counts: dict[str, int] = {}
    for inv in invocations or []:
        if not isinstance(inv, dict):
            continue
        if str(inv.get("kind", "")) != "mcp":
            continue
        target = str(inv.get("target", ""))
        if target != "web_fetch.fetch":
            continue
        # The canonical wire shape stores args under "args" or
        # "input"; either is accepted to forgive minor bus
        # variations.
        args = inv.get("args") or inv.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                continue
        if not isinstance(args, dict):
            continue
        url = str(args.get("url", "")).strip()
        if not url:
            continue
        counts[url] = counts.get(url, 0) + 1
    return counts


def verify_against_invocations(
    report_markdown: str,
    invocations: Iterable[dict],
) -> VerificationReport:
    """Cross-reference the report's inline citations with the run's
    web_fetch invocations.

    Returns a :class:`VerificationReport` with per-citation
    `refetched` + `refetch_count` populated.  Coverage rate is
    computed BEFORE :func:`summarise` applies a threshold — call
    summarise() to get the ok/threshold pair.
    """
    citations = extract_inline_citations(report_markdown)
    refetch_counts = _collect_refetch_urls(invocations)
    refetch_urls = set(refetch_counts.keys())

    refetched_count = 0
    for cit in citations:
        n = refetch_counts.get(cit.url, 0)
        if n > 0:
            cit.refetched = True
            cit.refetch_count = n
            refetched_count += 1

    coverage = (
        refetched_count / len(citations)
        if citations else 0.0
    )
    return VerificationReport(
        citations=citations,
        refetch_urls=refetch_urls,
        coverage_rate=coverage,
    )


def summarise(
    report: VerificationReport,
    *,
    threshold: float = 0.30,
) -> VerificationReport:
    """Apply the operator-tunable threshold + flip ``ok``.

    Default threshold (0.30) is conservative: the critic typically
    spot-checks 3-5 of the dozens of citations a full run produces.
    Operators bump it for stricter runs via the
    ``ACC_RESEARCH_MIN_VERIFIED_CITATIONS`` env var that
    ``verify.sh`` reads.

    Mutates `report` in place + returns it for chaining.
    """
    report.threshold = float(threshold)
    if not report.citations:
        # No citations means nothing to verify.  We do NOT mark this
        # ok — a report without citations is itself a critic failure.
        report.ok = False
        return report
    report.ok = report.coverage_rate >= report.threshold
    return report


# ---------------------------------------------------------------------------
# CLI helper — used by examples/acc_autoresearcher/verify.sh
# ---------------------------------------------------------------------------


def main_cli(
    report_path: str,
    invocations_path: str,
    threshold: float = 0.30,
) -> int:
    """Argparse-friendly entry: read both files, run the analysis,
    print + return a Unix-style exit code (0 on ok, 1 otherwise).

    ``invocations_path`` should point to a JSON array of
    invocation dicts (the operator's pre-aggregated run log; in
    practice verify.sh greps the bus log into this shape).
    """
    report_md = Path(report_path).read_text(encoding="utf-8")
    raw = Path(invocations_path).read_text(encoding="utf-8")
    try:
        invocations = json.loads(raw)
    except json.JSONDecodeError:
        print(f"verify: invalid JSON in {invocations_path!r}")
        return 2
    if not isinstance(invocations, list):
        print(f"verify: expected JSON array in {invocations_path!r}")
        return 2

    report = verify_against_invocations(report_md, invocations)
    summarise(report, threshold=threshold)

    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover — CLI entrypoint
    import argparse, sys

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", required=True, help="Path to the synthesizer's report.md")
    p.add_argument(
        "--invocations", required=True,
        help="Path to a JSON array of TASK_COMPLETE.invocations dicts",
    )
    p.add_argument(
        "--threshold", type=float, default=0.30,
        help="Minimum fraction of citations that must be re-fetched (0..1)",
    )
    args = p.parse_args()
    sys.exit(main_cli(args.report, args.invocations, args.threshold))

"""Citation verifier — defence against fabricated URLs (E5).

Pinned invariants:

* The Citations section parses every footnote line into a
  CitationEntry — number + URL + paywalled flag both leading
  and trailing.
* A report with no Citations heading returns an empty list (the
  caller decides how to react; verify.sh treats it as a hard
  failure).
* web_fetch.fetch invocations cross-reference correctly even
  when their args are JSON-encoded vs. dict-encoded on the wire.
* Coverage rate = (re-fetched citations / total citations).
* Threshold 0.30 default; ``summarise`` applies it.
* A report with citations but zero re-fetches → ok=False.
* A report with NO citations → ok=False (a report without
  sourcing is itself a critic failure).
"""

from __future__ import annotations

import json

import pytest

from acc.research.citation_verifier import (
    CitationEntry,
    extract_inline_citations,
    summarise,
    verify_against_invocations,
)


# ---------------------------------------------------------------------------
# Citations extraction
# ---------------------------------------------------------------------------


def test_no_citations_section_returns_empty():
    md = "# Report\n\nSome content without a Citations section.\n"
    assert extract_inline_citations(md) == []


def test_extracts_basic_citation_lines():
    md = """
## Citations

[1] https://example.com/paper.pdf
[2] https://gartner.com/research/agentic-ai-tam-2025
[3] https://aws.amazon.com/bedrock/agents/docs
"""
    cits = extract_inline_citations(md)
    assert [c.footnote for c in cits] == [1, 2, 3]
    assert cits[0].url == "https://example.com/paper.pdf"
    assert cits[1].url == "https://gartner.com/research/agentic-ai-tam-2025"
    assert all(not c.paywalled for c in cits)


def test_extracts_paywalled_marker_leading():
    md = """
## Citations

[1] (paywalled) https://wsj.com/article
[2] https://aws.amazon.com/docs
"""
    cits = extract_inline_citations(md)
    assert cits[0].paywalled is True
    assert cits[0].url == "https://wsj.com/article"
    assert cits[1].paywalled is False


def test_extracts_paywalled_marker_trailing():
    md = """
## Citations

[1] https://example.com/article (paywalled)
"""
    cits = extract_inline_citations(md)
    assert cits[0].paywalled is True


def test_strips_trailing_punctuation_from_url():
    """A footnote line that ends in a period or comma must NOT carry
    that punctuation into the parsed URL."""
    md = "## Citations\n\n[1] https://example.com/page.\n"
    cits = extract_inline_citations(md)
    assert cits[0].url == "https://example.com/page"


def test_handles_h3_citations_heading():
    """Some templates use ### Citations under a major-section H2."""
    md = "## Section\n\n### Citations\n\n[1] https://example.com\n"
    cits = extract_inline_citations(md)
    assert len(cits) == 1


def test_bounds_at_next_section():
    """Anything after a subsequent H1/H2 heading belongs to the next
    section, not the citations list."""
    md = """
## Citations

[1] https://example.com/first

## Appendix

[2] https://this-should-not-be-parsed.com
"""
    cits = extract_inline_citations(md)
    assert [c.footnote for c in cits] == [1]


def test_deduplicates_by_footnote_number():
    """The synthesizer occasionally repeats a footnote — we keep the
    first definition + drop subsequent re-declarations."""
    md = """
## Citations

[1] https://first.com
[1] https://second.com
[2] https://third.com
"""
    cits = extract_inline_citations(md)
    assert [c.url for c in cits] == ["https://first.com", "https://third.com"]


# ---------------------------------------------------------------------------
# Cross-reference with invocations
# ---------------------------------------------------------------------------


_REPORT = """
## Citations

[1] https://example.com/paper-a
[2] https://example.com/paper-b
[3] https://example.com/paper-c
[4] https://example.com/paper-d
"""


def _fetch_inv(url: str) -> dict:
    """Build a TASK_COMPLETE.invocations entry shape."""
    return {
        "kind": "mcp",
        "target": "web_fetch.fetch",
        "ok": True,
        "args": {"url": url},
    }


def test_marks_refetched_citations():
    invocations = [
        _fetch_inv("https://example.com/paper-a"),
        _fetch_inv("https://example.com/paper-c"),
    ]
    report = verify_against_invocations(_REPORT, invocations)

    by_num = {c.footnote: c for c in report.citations}
    assert by_num[1].refetched is True
    assert by_num[1].refetch_count == 1
    assert by_num[2].refetched is False
    assert by_num[3].refetched is True
    assert by_num[4].refetched is False
    assert report.coverage_rate == 0.5


def test_counts_repeated_refetches():
    """Multiple web_fetch invocations on the same URL accumulate;
    the citation entry's refetch_count reflects that."""
    invocations = [
        _fetch_inv("https://example.com/paper-a"),
        _fetch_inv("https://example.com/paper-a"),
        _fetch_inv("https://example.com/paper-a"),
    ]
    report = verify_against_invocations(_REPORT, invocations)
    by_num = {c.footnote: c for c in report.citations}
    assert by_num[1].refetch_count == 3


def test_ignores_non_fetch_invocations():
    """skill: invocations + other MCP invocations don't count as
    re-fetches.  Only ``mcp:web_fetch.fetch`` does."""
    invocations = [
        {"kind": "skill", "target": "report_drafter",
         "ok": True, "args": {"text": "..."}},
        {"kind": "mcp", "target": "web_search_brave.search",
         "ok": True, "args": {"query": "..."}},
    ]
    report = verify_against_invocations(_REPORT, invocations)
    assert all(not c.refetched for c in report.citations)
    assert report.coverage_rate == 0.0


def test_args_as_json_string_still_parses():
    """Some bus payloads serialise the args dict to JSON (msgpack
    intermediate). The verifier accepts both shapes."""
    invocations = [
        {"kind": "mcp", "target": "web_fetch.fetch", "ok": True,
         "args": json.dumps({"url": "https://example.com/paper-b"})},
    ]
    report = verify_against_invocations(_REPORT, invocations)
    assert any(c.url == "https://example.com/paper-b" and c.refetched
               for c in report.citations)


def test_malformed_invocation_does_not_raise():
    """Telemetry can be lossy — a malformed invocation shape is
    skipped silently rather than crashing the verify run."""
    invocations = [
        "not a dict at all",
        {"kind": "mcp", "target": "web_fetch.fetch"},  # no args
        {"kind": "mcp", "target": "web_fetch.fetch", "args": None},
        {"kind": "mcp", "target": "web_fetch.fetch", "args": "not_json"},
    ]
    # Should not raise.
    report = verify_against_invocations(_REPORT, invocations)
    assert report.coverage_rate == 0.0


# ---------------------------------------------------------------------------
# summarise — threshold + ok flag
# ---------------------------------------------------------------------------


def test_summarise_default_threshold_30_percent():
    invocations = [
        _fetch_inv("https://example.com/paper-a"),
    ]
    report = verify_against_invocations(_REPORT, invocations)
    summarise(report)
    # 1/4 = 0.25 < 0.30 → not ok
    assert report.threshold == 0.30
    assert report.ok is False


def test_summarise_passes_when_above_threshold():
    invocations = [
        _fetch_inv("https://example.com/paper-a"),
        _fetch_inv("https://example.com/paper-b"),
    ]
    report = verify_against_invocations(_REPORT, invocations)
    summarise(report, threshold=0.30)
    # 2/4 = 0.50 >= 0.30 → ok
    assert report.ok is True


def test_summarise_no_citations_is_not_ok():
    """A report without a Citations section is a critic failure
    even before re-fetch verification — the verifier surfaces this
    as ok=False so the operator's CI catches it."""
    report = verify_against_invocations("# No citations here\n", [])
    summarise(report, threshold=0.30)
    assert report.citations == []
    assert report.coverage_rate == 0.0
    assert report.ok is False


def test_summarise_threshold_zero_passes_with_zero_refetches():
    """An operator who explicitly sets threshold=0 disables the
    verifier — useful when running the demo offline.  Empty
    citations still fail (no citations is no report)."""
    invocations: list = []
    report = verify_against_invocations(_REPORT, invocations)
    summarise(report, threshold=0.0)
    assert report.ok is True


def test_to_dict_round_trips_full_state():
    invocations = [_fetch_inv("https://example.com/paper-a")]
    report = verify_against_invocations(_REPORT, invocations)
    summarise(report, threshold=0.20)
    out = report.to_dict()
    assert out["threshold"] == 0.20
    assert out["ok"] is True
    assert any(c["refetched"] for c in out["citations"])
    assert "coverage_rate" in out

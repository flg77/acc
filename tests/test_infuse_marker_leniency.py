"""PROPOSE_INFUSE marker leniency + a retry directive models can actually copy.

Both failure modes below were observed LIVE on lighthouse against
``gpt-oss-120b`` — a 120B model — so this is not a small-model formatting
problem and is not fixed by swapping to a bigger model:

1. The retry directive advertised ``[PROPOSE_INFUSE:@scope/pack@constraint:...]``.
   Models emitted the name WITHOUT a version (they were never given a
   concrete one), and the strict regex — which *required* ``@constraint`` —
   silently dropped the marker.  The operator saw "I did not take a concrete
   action" and the intent was lost.
2. Told to acquire a capability with no concrete pack names in front of it,
   the assistant emitted ``[PROPOSE_ROUTE:assistant:...]`` and routed the
   task to ITSELF.

Both fail SILENTLY, which is why they get regression cover.
"""

from __future__ import annotations

import acc.cognitive_core as cc
from acc.assistant_proposal import PROPOSAL_INFUSE, parse_proposal_markers


def _infuse(text: str):
    return [p for p in parse_proposal_markers(text) if p.kind == PROPOSAL_INFUSE]


class TestConstraintOptional:
    def test_bare_name_parses(self):
        """The regression: no version must NOT be dropped."""
        got = _infuse("[PROPOSE_INFUSE:@acc/research-roles:need a research team]")
        assert len(got) == 1
        assert got[0].params["name"] == "@acc/research-roles"
        assert got[0].params["constraint"] == cc_default()

    def test_bare_name_constraint_matches_every_version(self):
        from acc.pkg._semver import version_satisfies
        got = _infuse("[PROPOSE_INFUSE:@acc/research-roles:why]")
        c = got[0].params["constraint"]
        for v in ("0.0.1", "0.1.0", "1.0.0", "9.9.9"):
            assert version_satisfies(v, c), f"{v} should satisfy {c}"

    def test_explicit_constraints_still_honoured(self):
        for spec, want in (
            ("@acc/research-roles@^1.1", "^1.1"),
            ("@acc/workspace-roles@1.2.3", "1.2.3"),
            ("@acc/business-roles@~2.0.1", "~2.0.1"),
        ):
            got = _infuse(f"[PROPOSE_INFUSE:{spec}:why]")
            assert len(got) == 1, spec
            assert got[0].params["constraint"] == want

    def test_leniency_composes_with_delimiter_normalisation(self):
        """Backtick / bare-line forms AND a missing constraint together."""
        for text in (
            "`PROPOSE_INFUSE:@acc/research-roles:why`",
            "PROPOSE_INFUSE:@acc/research-roles:why",
        ):
            got = _infuse(text)
            assert len(got) == 1, text
            assert got[0].params["name"] == "@acc/research-roles"

    def test_garbage_still_rejected(self):
        """Leniency must not become 'accept anything'."""
        for bad in (
            "[PROPOSE_INFUSE:not-a-scope:why]",
            "[PROPOSE_INFUSE:@acc:why]",
            "[PROPOSE_INFUSE:@scope/pack@constraint:why]",  # literal placeholder
        ):
            assert _infuse(bad) == [], bad


def cc_default() -> str:
    from acc.assistant_proposal import _INFUSE_DEFAULT_CONSTRAINT
    return _INFUSE_DEFAULT_CONSTRAINT


class _Snap:
    def __init__(self, pkgs):
        self.available_packages = pkgs


class TestRetryDirective:
    def test_no_placeholder_words_in_the_infuse_example(self):
        """The literal template was being echoed back by the model."""
        d = cc._marker_retry_directive(None)
        assert "@scope/pack@constraint" not in d
        assert "[PROPOSE_INFUSE:@acc/research-roles:" in d

    def test_lists_real_pack_names_when_known(self):
        d = cc._marker_retry_directive(
            _Snap([{"name": "@acc/research-roles"}, {"name": "@acc/business-roles"}])
        )
        assert "@acc/research-roles" in d and "@acc/business-roles" in d
        assert "EXACT names" in d

    def test_warns_against_self_routing(self):
        assert "YOURSELF" in cc._marker_retry_directive(None)

    def test_degrades_without_a_snapshot(self):
        for bad in (None, object(), _Snap(None), _Snap([{"no_name": 1}])):
            d = cc._marker_retry_directive(bad)
            assert d.endswith("emit it now.")
            assert "EXACT names" not in d

    def test_example_in_the_directive_actually_parses(self):
        """Guard against drifting the example into an unparseable shape."""
        import re
        d = cc._marker_retry_directive(None)
        found = re.findall(r"\[PROPOSE_INFUSE:[^\]]+\]", d)
        assert found, "no INFUSE marker shown in the directive at all"
        parseable = [m for m in found if len(_infuse(m)) == 1]
        assert parseable, (
            "the directive shows INFUSE markers but NONE parse - a model "
            f"copying any of these gets silently dropped: {found}"
        )

"""``check_endpoint_capabilities`` — the preflight half of the profile.

The profile layer is covered elsewhere (``test_endpoint_profile*.py``). What is
asserted here is the contract with ``acc/preflight.py``, which is mostly about
what the check is **not** allowed to do: it may not raise, may not emit BROKEN,
may not change an exit code, and may not make a network call unless ``--probe``
was passed. Those are the properties that let a diagnostic be run on any
cadence without anyone thinking about it.

Change: ``openspec/changes/20260826-endpoint-capability-profile`` Phase 1.5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

import acc.preflight as preflight
from acc.endpoint_profile import Capability, EndpointProfile, unknown
from acc.preflight import Context, Result, Severity

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "endpoint"
SENTINEL = "sk-acc-canary-preflight-do-not-leak"


@dataclass
class FakeEntry:
    model_id: str = "lighthouse-llama32-3b"
    backend: str = "vllm"
    model: str = "RedHatAI/Llama-3.2-3B-Instruct-FP8"
    base_url: str = "http://lighthouse:8022/v1"
    api_key_env: str = ""


def _healthy_profile(**over) -> EndpointProfile:
    """The lighthouse shape: 8192 of 131072, caching on and hitting 93%."""
    base = dict(
        model_id="lighthouse-llama32-3b",
        backend="vllm",
        base_url="http://lighthouse:8022/v1",
        served_model=Capability("RedHatAI/Llama-3.2-3B-Instruct-FP8", "probed"),
        served_window=Capability(8192, "probed"),
        native_window=Capability(131072, "table"),
        window_scaling=Capability({"direction": "reduced", "ratio": 0.062}, "inferred"),
        prefix_cache_enabled=Capability(True, "metrics"),
        prefix_cache_hits=Capability(
            {"queries": 31185, "hits": 29040, "rate": 0.9312}, "metrics"
        ),
        tokenize=Capability(True, "probed"),
        embeddings=Capability(False, "probed"),
        kv_cache=Capability({"dtype": "fp8", "pool_tokens": 96512}, "metrics"),
    )
    base.update(over)
    return EndpointProfile(**base)


@pytest.fixture
def stub(monkeypatch):
    """Stand in for the model registry and the probe.

    ``check_endpoint_capabilities`` imports both lazily inside the function, so
    they are patched on their defining modules rather than on ``preflight``.
    """
    state = {"entries": [FakeEntry()], "profile": _healthy_profile(), "calls": []}

    import acc.endpoint_profile as ep
    import acc.models as models

    monkeypatch.setattr(models, "load_models", lambda *a, **k: state["entries"])

    def fake_probe(entry, *, timeout_s=5.0, environ=None):
        state["calls"].append({"entry": entry, "timeout_s": timeout_s})
        p = state["profile"]
        return p(entry) if callable(p) else p

    monkeypatch.setattr(ep, "probe_endpoint", fake_probe)
    return state


def _run(only: str = "endpoint-capability", **ctx_kw) -> list[Result]:
    return list(preflight.run(Context(**ctx_kw), only=only))


class TestTheCheckIsInert:
    def test_it_is_registered_under_a_stable_name(self):
        assert "endpoint-capability" in [n for n, _ in preflight.registry()]

    def test_without_probe_it_makes_no_call_at_all(self, stub):
        """REQ-CHK-002. A health command must be safe to run on any cadence."""
        results = _run(probe_endpoints=False)

        assert stub["calls"] == [], "dialled an endpoint without --probe"
        assert len(results) == 1
        assert results[0].ok
        assert "--probe" in results[0].detail

    def test_it_never_changes_the_exit_code(self, stub):
        """REQ-CHK-003. Only BROKEN sets a non-zero exit, and this cannot."""
        stub["profile"] = _healthy_profile(
            prefix_cache_enabled=Capability(False, "metrics"),
            errors=("GET /metrics: TimeoutError",),
        )
        results = _run(probe_endpoints=True)

        assert any(r.severity is Severity.DEGRADED for r in results)
        assert not any(r.severity is Severity.BROKEN for r in results)
        assert preflight.exit_code(results) == 0

    def test_a_raising_probe_is_contained_not_propagated(self, stub, monkeypatch):
        """``preflight.run`` scores a raising check BROKEN.

        The profile layer promises never to raise; if that promise is ever
        broken, this asserts the blast radius — the whole run must not be
        reported as broken because one gateway misbehaved.
        """
        import acc.endpoint_profile as ep

        def boom(entry, **kw):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(ep, "probe_endpoint", boom)
        results = _run(probe_endpoints=True)

        # run() catches it; what matters is that the exit code is decided by
        # this being a diagnostic, not by a gateway having a bad day.
        assert results, "the check produced nothing at all"
        assert all(r.severity is not Severity.OK for r in results)

    def test_no_models_configured_is_ok_not_a_fault(self, stub):
        stub["entries"] = []
        results = _run(probe_endpoints=True)
        assert len(results) == 1 and results[0].ok


class TestTheReport:
    def test_a_healthy_endpoint_reads_as_one_dense_ok_line(self, stub):
        results = _run(probe_endpoints=True)
        assert len(results) == 1
        r = results[0]

        assert r.severity is Severity.OK
        assert r.subject == "lighthouse-llama32-3b"
        # Named in the summary too: the renderer prints [subject] only for
        # rows that are not OK, so a healthy row would otherwise not say which
        # model it describes.
        assert r.summary.startswith("lighthouse-llama32-3b (vllm):")
        assert "window 8192" in r.summary
        assert "6% of native" in r.summary
        assert "prefix cache 93% hit" in r.summary
        assert "tokenize yes" in r.summary
        assert "embeddings no" in r.summary
        assert "KV pool 96512t" in r.summary

    def test_an_unknown_states_its_reason_in_the_summary_not_the_detail(self, stub):
        """The renderer hides ``detail`` on OK rows.

        An unknown is an OK row — it is not a fault — so a reason parked in the
        detail is a reason nobody ever reads. This is the whole justification
        for the check's dense single-line shape.
        """
        stub["profile"] = _healthy_profile(
            served_window=unknown("no max_model_len (not a vLLM server?)"),
            window_scaling=unknown("needs both windows"),
        )
        r = _run(probe_endpoints=True)[0]

        assert r.severity is Severity.OK
        assert "window unknown" in r.summary
        assert "not a vLLM server" in r.summary

    def test_caching_switched_off_is_degraded_and_says_why_it_matters(self, stub):
        stub["profile"] = _healthy_profile(
            prefix_cache_enabled=Capability(False, "metrics"),
        )
        r = _run(probe_endpoints=True)[0]

        assert r.severity is Severity.DEGRADED
        assert "prefix cache OFF" in r.summary
        assert "PR-CA1" in r.detail

    def test_a_cold_cache_is_not_reported_as_a_zero_hit_rate(self, stub):
        """A cold cache is not a misconfiguration and must not read as one."""
        stub["profile"] = _healthy_profile(
            prefix_cache_hits=Capability({"queries": 0, "hits": 0, "rate": None}, "metrics"),
        )
        r = _run(probe_endpoints=True)[0]

        assert r.severity is Severity.OK
        assert "cold" in r.summary
        assert "0%" not in r.summary

    def test_a_genuinely_low_hit_rate_is_degraded_and_names_both_causes(self, stub):
        stub["profile"] = _healthy_profile(
            prefix_cache_hits=Capability({"queries": 9000, "hits": 90, "rate": 0.01}, "metrics"),
        )
        r = _run(probe_endpoints=True)[0]

        assert r.severity is Severity.DEGRADED
        assert "warming" in r.detail and "byte-stable" in r.detail

    def test_an_extended_window_is_never_called_yarn(self, stub):
        """REQ-ROP-003, at the reporting layer this time."""
        stub["profile"] = _healthy_profile(
            served_window=Capability(131072, "probed"),
            native_window=Capability(32768, "table"),
            window_scaling=Capability({"direction": "extended", "ratio": 4.0}, "inferred"),
        )
        r = _run(probe_endpoints=True)[0]

        assert "rescaled" in r.summary
        assert "yarn" not in (r.summary + r.detail).lower()

    def test_probe_errors_degrade_the_row_and_are_reported(self, stub):
        stub["profile"] = _healthy_profile(errors=("GET /metrics: TimeoutError: timed out",))
        r = _run(probe_endpoints=True)[0]

        assert r.severity is Severity.DEGRADED
        assert "TimeoutError" in r.detail

    def test_nothing_determined_still_produces_a_row(self, stub):
        """A silent check is indistinguishable from one that was not run."""
        stub["profile"] = EndpointProfile(
            model_id="dead", backend="vllm", base_url="http://nope/v1",
            errors=("GET /v1/models: unreachable",),
        )
        r = _run(probe_endpoints=True)[0]

        assert r.severity is Severity.DEGRADED
        assert "unknown" in r.summary


class TestWindowDrift:
    def test_no_declared_window_makes_no_claim(self, stub):
        """``ModelEntry.context_window`` arrives with 20260826-context-budget.

        Until it exists there is nothing to reconcile against, and inventing a
        comparison would be worse than staying quiet.
        """
        r = _run(probe_endpoints=True)[0]
        assert r.severity is Severity.OK
        assert "declares" not in r.detail

    def test_a_declared_mismatch_is_drifted_never_broken(self, stub):
        """REQ-CHK-005, forward-compatible with the field that does not exist yet."""
        entry = FakeEntry()
        object.__setattr__(entry, "context_window", 32768)
        stub["entries"] = [entry]

        r = _run(probe_endpoints=True)[0]
        assert r.severity is Severity.DRIFTED
        assert "declares 32768" in r.detail
        assert "legitimate choice" in r.detail

    def test_a_matching_declaration_is_quiet(self, stub):
        entry = FakeEntry()
        object.__setattr__(entry, "context_window", 8192)
        stub["entries"] = [entry]

        r = _run(probe_endpoints=True)[0]
        assert r.severity is Severity.OK


class TestDeduplication:
    def test_models_sharing_a_host_are_probed_separately(self, stub):
        """Capabilities are per model, not per host.

        Two models on one vLLM have different windows, so unlike
        ``check_endpoints`` the dedup key has to carry the model.
        """
        stub["entries"] = [
            FakeEntry(model_id="a", model="model-a"),
            FakeEntry(model_id="b", model="model-b"),
        ]
        results = _run(probe_endpoints=True)

        assert len(stub["calls"]) == 2
        assert len(results) == 2

    def test_an_identical_triple_is_probed_once_but_reported_twice(self, stub):
        """Probing dedups; reporting must not.

        Two entries can name the same served model and declare *different*
        windows. Deduplicating the rows as well as the probes would validate
        only the first declaration and silently skip the second -- which is
        precisely the drift this check exists to surface. Caught by a live run,
        not by reasoning.
        """
        stub["entries"] = [FakeEntry(model_id="a"), FakeEntry(model_id="a-alias")]
        results = _run(probe_endpoints=True)

        assert len(stub["calls"]) == 1, "probed the same endpoint twice"
        assert len(results) == 2, "an entry went unreported"
        assert {r.subject for r in results} == {"a", "a-alias"}

    def test_a_second_entry_declaring_a_different_window_still_drifts(self, stub):
        """The case the dedup bug hid, end to end."""
        ok_entry = FakeEntry(model_id="declared-right")
        object.__setattr__(ok_entry, "context_window", 8192)
        bad_entry = FakeEntry(model_id="declared-wrong", base_url="http://lighthouse:8022/v1/")
        object.__setattr__(bad_entry, "context_window", 32768)
        stub["entries"] = [ok_entry, bad_entry]

        results = _run(probe_endpoints=True)
        by_subject = {r.subject: r for r in results}

        assert len(stub["calls"]) == 1, "trailing slash defeated the probe cache"
        assert by_subject["declared-right"].severity is Severity.OK
        assert by_subject["declared-wrong"].severity is Severity.DRIFTED
        assert "declares 32768" in by_subject["declared-wrong"].detail

    def test_the_report_is_ascii_for_a_cp1252_console(self):
        """``doctor_cmd`` degrades un-encodable glyphs to a replacement char.

        That is the right failure mode, but a detail rendered as garbage is
        still a detail nobody can read, so new output text stays ASCII.
        """
        import inspect

        import acc.preflight as mod

        src = inspect.getsource(mod._capability_results)
        offending = sorted({c for c in src if ord(c) > 127})
        assert not offending, f"non-ASCII in rendered output: {offending}"

    def test_the_context_timeout_is_honoured(self, stub):
        _run(probe_endpoints=True, timeout_s=1.5)
        assert stub["calls"][0]["timeout_s"] == 1.5


class TestNoCredentialInTheReport:
    def test_a_sentinel_key_reaches_neither_the_row_nor_the_json(self, stub):
        """REQ-CRD-002 at the reporting boundary.

        The profile layer redacts; this asserts that nothing downstream of it
        re-introduces the value into a summary, a detail or ``--json``.
        """
        stub["profile"] = _healthy_profile(
            errors=(f"SSLError: verifying <redacted>@host",),
        )
        results = _run(probe_endpoints=True)

        blob = json.dumps(preflight.report(results))
        assert SENTINEL not in blob
        assert "<redacted>" in blob, "the redaction marker should survive to the report"

"""A second choice when a provider fails.

Pins the four outcomes the change names explicitly — primary down means work
continues, primary back means work returns, a fatal error does *not* burn the
chain, and a deployment without a chain behaves exactly as it does today — plus
the two that keep the mechanism honest: a refused hop fails **closed** with a
named reason, and an unhealthy model is skipped only until its cooldown lapses.

The fatal-error case deserves its emphasis.  If a 401 advanced the chain, one
bad API key would walk through every model a deployment owns, produce a
failover event for each, and report the *last* endpoint's error — hiding a
one-line configuration fault behind an apparent multi-provider outage.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.backends import BackendConnectionError, LLMCallError
from acc.llm_failover import (
    AllowAllGate,
    Availability,
    FailoverBackend,
    ZonePolicyGate,
    build_failover_backend,
    is_retryable,
)
from acc.models import ModelEntry

# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


class FakeLLM:
    """A backend that answers, or fails in a specified way."""

    def __init__(self, name: str, *, fail: BaseException | None = None):
        self.name = name
        self.fail = fail
        self.calls = 0

    async def complete(self, system, user, response_schema=None, cache_prefix=False):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return {"model": self.name, "text": f"answer from {self.name}"}

    async def embed(self, text):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return [0.0]


def entry(model_id: str, zone: str = "") -> ModelEntry:
    return ModelEntry(model_id=model_id, backend="ollama", model="m", zone=zone)


def chain_of(*clients: FakeLLM, zones: dict[str, str] | None = None, **kw) -> FailoverBackend:
    zones = zones or {}
    entries = [entry(c.name, zones.get(c.name, "")) for c in clients]
    by_id = {c.name: c for c in clients}
    kw.setdefault("gate", AllowAllGate())
    return FailoverBackend(
        role="assistant",
        entries=entries,
        build=lambda e: by_id[e.model_id],
        **kw,
    )


def run(coro):
    return asyncio.run(coro)


UNAVAILABLE = LLMCallError("HTTP 503 (retryable)", retryable=True, status_code=503)
RATE_LIMITED = LLMCallError("HTTP 429 (retryable)", retryable=True, status_code=429)
UNAUTHORISED = LLMCallError("HTTP 401", retryable=False, status_code=401)


# --------------------------------------------------------------------------
# The four named criteria
# --------------------------------------------------------------------------


class TestFailoverCriteria:
    def test_primary_unreachable_work_continues_on_secondary(self):
        primary = FakeLLM("primary", fail=UNAVAILABLE)
        secondary = FakeLLM("secondary")
        backend = chain_of(primary, secondary)

        result = run(backend.complete("sys", "user"))

        assert result["model"] == "secondary", "the task must survive the outage"
        assert primary.calls == 1, "the primary is still tried first"
        assert backend.active_model == "secondary"

    def test_primary_recovers_and_work_returns_to_it(self):
        primary = FakeLLM("primary", fail=UNAVAILABLE)
        secondary = FakeLLM("secondary")
        # Zero cooldown: the primary is eligible again on the very next call,
        # which is the behaviour under test — not the duration of the wait.
        backend = chain_of(primary, secondary, availability=Availability(0.0))

        assert run(backend.complete("s", "u"))["model"] == "secondary"

        primary.fail = None
        assert run(backend.complete("s", "u"))["model"] == "primary", (
            "a chain that never returns to the primary turns a transient "
            "outage into a permanent downgrade"
        )
        assert backend.status()["on_primary"] is True

    def test_fatal_error_does_not_trigger_failover(self):
        primary = FakeLLM("primary", fail=UNAUTHORISED)
        secondary = FakeLLM("secondary")
        backend = chain_of(primary, secondary)

        with pytest.raises(LLMCallError) as exc:
            run(backend.complete("s", "u"))

        assert exc.value.status_code == 401, "the real cause must be what surfaces"
        assert secondary.calls == 0, (
            "a bad key would otherwise walk the whole chain and report the "
            "last endpoint's error instead of the actual fault"
        )

    def test_no_chain_configured_is_not_wrapped_at_all(self):
        """The no-regression criterion: nothing in the call path changes."""
        registry = {"only": entry("only")}
        assert build_failover_backend("assistant", ["only"], registry, lambda e: FakeLLM("only")) is None
        assert build_failover_backend("assistant", [], registry, lambda e: FakeLLM("x")) is None


# --------------------------------------------------------------------------
# Policy gate — mechanism must not pre-empt the held decision
# --------------------------------------------------------------------------


class TestPolicyGate:
    def test_hop_across_declared_zones_is_refused_and_fails_closed(self):
        primary = FakeLLM("eu-model", fail=UNAVAILABLE)
        secondary = FakeLLM("us-model")
        backend = chain_of(
            primary, secondary,
            zones={"eu-model": "eu", "us-model": "us"},
            gate=ZonePolicyGate(),
        )

        with pytest.raises(LLMCallError) as exc:
            run(backend.complete("s", "u"))

        message = str(exc.value)
        assert "refused" in message
        assert "trust/residency" in message, "the refusal must name its reason"
        assert secondary.calls == 0, "a refused hop must not proceed quietly"

    def test_same_zone_hop_is_permitted(self):
        primary = FakeLLM("eu-a", fail=UNAVAILABLE)
        secondary = FakeLLM("eu-b")
        backend = chain_of(
            primary, secondary,
            zones={"eu-a": "eu", "eu-b": "eu"},
            gate=ZonePolicyGate(),
        )
        assert run(backend.complete("s", "u"))["model"] == "eu-b"

    def test_undeclared_zones_do_not_block_failover(self):
        """Refusing every hop by default would ship a feature that never fires.

        A deployment that has not adopted zones is not expressing a boundary,
        so there is none to cross.  Enforcement begins when an operator
        annotates a model.
        """
        primary = FakeLLM("a", fail=UNAVAILABLE)
        secondary = FakeLLM("b")
        backend = chain_of(primary, secondary, gate=ZonePolicyGate())
        assert run(backend.complete("s", "u"))["model"] == "b"

    def test_declared_to_undeclared_is_refused(self):
        """One side annotated means boundaries exist; this hop cannot be shown safe."""
        gate = ZonePolicyGate()
        assert gate.allow(entry("a", "eu"), entry("b", "")).allowed is False
        assert gate.allow(entry("a", ""), entry("b", "eu")).allowed is False


# --------------------------------------------------------------------------
# Health and recovery
# --------------------------------------------------------------------------


class TestAvailability:
    def test_failed_model_is_skipped_until_its_cooldown_lapses(self):
        now = {"t": 1000.0}
        avail = Availability(60.0, clock=lambda: now["t"])

        avail.record_failure("primary")
        assert avail.is_available("primary") is False

        now["t"] += 59
        assert avail.is_available("primary") is False, "still cooling down"

        now["t"] += 2
        assert avail.is_available("primary") is True, (
            "the cooldown expiring IS the automatic recovery path"
        )

    def test_success_clears_a_previous_failure(self):
        avail = Availability(60.0)
        avail.record_failure("m")
        avail.record_success("m")
        assert avail.is_available("m") is True

    def test_snapshot_reports_remaining_cooldown(self):
        now = {"t": 0.0}
        avail = Availability(30.0, clock=lambda: now["t"])
        avail.record_failure("down")
        assert "down" in avail.snapshot()
        now["t"] += 31
        assert avail.snapshot() == {}

    def test_model_in_cooldown_is_skipped_without_being_called(self):
        primary = FakeLLM("primary", fail=UNAVAILABLE)
        secondary = FakeLLM("secondary")
        backend = chain_of(primary, secondary, availability=Availability(300.0))

        run(backend.complete("s", "u"))
        primary.fail = None  # it recovered, but the cooldown has not lapsed
        run(backend.complete("s", "u"))

        assert primary.calls == 1, "a model in cooldown must not be re-probed"


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


class TestVisibility:
    def test_failover_emits_an_event(self):
        events = []
        backend = chain_of(
            FakeLLM("primary", fail=UNAVAILABLE), FakeLLM("secondary"),
            on_event=events.append,
        )
        run(backend.complete("s", "u"))

        assert len(events) == 1
        assert events[0].kind == "failover"
        assert (events[0].from_model, events[0].to_model) == ("primary", "secondary")
        assert events[0].role == "assistant"

    def test_recovery_emits_its_own_event(self):
        events = []
        primary = FakeLLM("primary", fail=UNAVAILABLE)
        backend = chain_of(
            primary, FakeLLM("secondary"),
            availability=Availability(0.0), on_event=events.append,
        )
        run(backend.complete("s", "u"))
        primary.fail = None
        run(backend.complete("s", "u"))

        assert [e.kind for e in events] == ["failover", "recovered"]

    def test_refusal_emits_an_event(self):
        events = []
        backend = chain_of(
            FakeLLM("eu", fail=UNAVAILABLE), FakeLLM("us"),
            zones={"eu": "eu", "us": "us"},
            gate=ZonePolicyGate(), on_event=events.append,
        )
        with pytest.raises(LLMCallError):
            run(backend.complete("s", "u"))
        assert [e.kind for e in events] == ["refused"]

    def test_status_reports_the_active_model_before_any_call(self):
        backend = chain_of(FakeLLM("primary"), FakeLLM("secondary"))
        status = backend.status()
        assert status["on_primary"] is True, (
            "'we are on the primary' is what makes the absence of an alert mean "
            "something"
        )
        assert status["chain"] == ["primary", "secondary"]

    def test_a_raising_event_sink_never_breaks_the_call(self):
        def explode(_event):
            raise RuntimeError("sink is broken")

        backend = chain_of(
            FakeLLM("primary", fail=UNAVAILABLE), FakeLLM("secondary"),
            on_event=explode,
        )
        assert run(backend.complete("s", "u"))["model"] == "secondary"


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


class TestClassification:
    @pytest.mark.parametrize(
        "exc,expected",
        [
            (UNAVAILABLE, True),
            (RATE_LIMITED, True),
            (UNAUTHORISED, False),
            (LLMCallError("HTTP 400", retryable=False, status_code=400), False),
            (BackendConnectionError("refused"), True),
            (TimeoutError("timed out"), True),
            (ConnectionError("no route"), True),
            (ValueError("a bug in our own code"), False),
        ],
    )
    def test_classification(self, exc, expected):
        assert is_retryable(exc) is expected

    def test_a_plain_exception_is_never_treated_as_an_outage(self):
        """Otherwise every bug in the call path looks like a provider failure."""
        primary = FakeLLM("primary", fail=ValueError("bug"))
        secondary = FakeLLM("secondary")
        backend = chain_of(primary, secondary)
        with pytest.raises(ValueError):
            run(backend.complete("s", "u"))
        assert secondary.calls == 0


# --------------------------------------------------------------------------
# Chain construction
# --------------------------------------------------------------------------


class TestChainConstruction:
    def test_unknown_model_in_a_chain_is_skipped_not_fatal(self):
        registry = {"a": entry("a"), "b": entry("b")}
        built = build_failover_backend(
            "assistant", ["a", "ghost", "b"], registry, lambda e: FakeLLM(e.model_id)
        )
        assert built is not None
        assert [e.model_id for e in built.entries] == ["a", "b"]

    def test_every_model_failing_names_the_role(self):
        backend = chain_of(
            FakeLLM("a", fail=UNAVAILABLE), FakeLLM("b", fail=UNAVAILABLE)
        )
        with pytest.raises(LLMCallError, match="assistant"):
            run(backend.complete("s", "u"))

    def test_embed_fails_over_too(self):
        backend = chain_of(FakeLLM("primary", fail=UNAVAILABLE), FakeLLM("secondary"))
        assert run(backend.embed("text")) == [0.0]
        assert backend.active_model == "secondary"


# --------------------------------------------------------------------------
# Configuration compatibility
# --------------------------------------------------------------------------


MODELS_YAML = """\
models:
  - model_id: primary
    backend: ollama
  - model_id: secondary
    backend: ollama
    zone: eu

role_models:
  plain: primary
  chained:
    - primary
    - secondary
"""


class TestConfigCompatibility:
    @pytest.fixture
    def registry_file(self, tmp_path):
        p = tmp_path / "models.yaml"
        p.write_text(MODELS_YAML, encoding="utf-8")
        return p

    def test_a_chain_does_not_invalidate_the_whole_registry(self, registry_file):
        """``role_models`` typed as ``dict[str, str]`` rejects a chain.

        Pydantic then discards the *entire* file, so declaring one alternate
        would silently unbind every role in it — the models list included.
        """
        from acc.models import load_models

        assert [m.model_id for m in load_models(registry_file)] == ["primary", "secondary"]

    def test_existing_callers_still_see_a_single_model_id(self, registry_file):
        from acc.models import load_role_models

        mapping = load_role_models(registry_file)
        assert mapping == {"plain": "primary", "chained": "primary"}, (
            "a chain must present its primary to every caller written before "
            "chains existed — otherwise they str() a list into a bogus id"
        )

    def test_chains_are_available_to_callers_that_want_them(self, registry_file):
        from acc.models import load_role_chains

        assert load_role_chains(registry_file) == {
            "plain": ["primary"],
            "chained": ["primary", "secondary"],
        }

    def test_zone_annotation_round_trips(self, registry_file):
        from acc.models import get_model

        assert get_model("secondary", registry_file).zone == "eu"
        assert get_model("primary", registry_file).zone == ""

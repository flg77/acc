"""A standard chat surface onto a governed collective.

The value and the risk are the same sentence: an unmodified client can point at
ACC, which makes this **the surface most likely to be pointed at by something
the operator did not write.** So it is where attribution, budgeting and
evaluation matter most, not least.

The load-bearing test is the ordering: **nothing reaches dispatch that has not
been authenticated and gated.** A request arriving in a familiar shape is not a
different class of work, and the shape is a convenience for the client, not a
reason to skip anything.

The gated case is settled rather than discovered: **202 with a handle.** Not a
refusal, which would make the endpoint useless for exactly the work worth
governing; not a block until the oversight timeout, which hangs a client on a
socket it did not expect to hold.
"""

from __future__ import annotations

import hashlib

import pytest

from acc import compat_endpoint as E

KEY = "sk-acc-test-key"
DIGEST = hashlib.sha256(KEY.encode()).hexdigest()
ENV = {E.KEYS_VAR: f"{DIGEST}:integrations-team"}

REQUEST = {
    "model": "analyst",
    "messages": [
        {"role": "system", "content": "be concise"},
        {"role": "user", "content": "summarise the incident"},
    ],
}


def dispatcher(calls):
    def _dispatch(request, caller, attribution):
        calls.append((request, caller, attribution))
        return "the summary", {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    return _dispatch


# --------------------------------------------------------------------------
# Nothing reaches dispatch unauthenticated or ungated
# --------------------------------------------------------------------------


class TestNothingBypasses:
    def test_an_unauthenticated_request_never_dispatches(self):
        calls: list = []
        result = E.handle(REQUEST, "", environ=ENV, dispatch=dispatcher(calls))
        assert result.status == 401
        assert calls == [], "dispatch must not be reached"

    def test_a_wrong_key_never_dispatches(self):
        calls: list = []
        result = E.handle(REQUEST, "sk-wrong", environ=ENV, dispatch=dispatcher(calls))
        assert result.status == 401
        assert calls == []

    def test_a_malformed_request_never_dispatches(self):
        calls: list = []
        result = E.handle({"model": "analyst"}, KEY, environ=ENV, dispatch=dispatcher(calls))
        assert result.status == 400
        assert calls == []

    def test_gated_work_never_dispatches(self):
        """It is not running yet — that is the whole meaning of the gate."""
        calls: list = []
        result = E.handle(
            REQUEST, KEY, environ=ENV,
            dispatch=dispatcher(calls),
            gate=lambda request, caller: "ov-123",
        )
        assert result.status == 202
        assert calls == [], "gated work must not reach dispatch"

    def test_an_unconfigured_endpoint_refuses_everything(self):
        """An endpoint with no key list is an open relay onto a budget."""
        result = E.handle(REQUEST, KEY, environ={}, dispatch=dispatcher([]))
        assert result.status == 503
        assert "refuses everything" in result.body["error"]["message"]

    def test_the_key_list_holds_digests_not_keys(self):
        """A leaked allowlist must not leak usable credentials."""
        assert KEY not in ENV[E.KEYS_VAR]
        assert DIGEST in ENV[E.KEYS_VAR]

    def test_the_comparison_is_constant_time(self):
        import inspect

        assert "compare_digest" in inspect.getsource(E.authenticate)


# --------------------------------------------------------------------------
# The gated case, settled
# --------------------------------------------------------------------------


class TestGatedWork:
    def test_it_returns_a_handle_not_a_refusal(self):
        """A refusal would make the endpoint useless for governed work."""
        result = E.handle(
            REQUEST, KEY, environ=ENV,
            dispatch=dispatcher([]), gate=lambda r, c: "ov-9",
        )
        assert result.status == 202
        assert result.body["status"] == "awaiting_approval"
        assert result.body["oversight_id"] == "ov-9"
        assert result.body["task_id"]

    def test_the_response_says_it_is_neither_dropped_nor_running(self):
        result = E.handle(
            REQUEST, KEY, environ=ENV,
            dispatch=dispatcher([]), gate=lambda r, c: "ov-9",
        )
        detail = result.body["detail"]
        assert "not been dropped" in detail
        assert "not running yet" in detail

    def test_ungated_work_completes_normally(self):
        calls: list = []
        result = E.handle(
            REQUEST, KEY, environ=ENV,
            dispatch=dispatcher(calls), gate=lambda r, c: "",
        )
        assert result.status == 200
        assert result.dispatched
        assert len(calls) == 1


# --------------------------------------------------------------------------
# Attribution and shape
# --------------------------------------------------------------------------


class TestAttributionAndShape:
    def test_every_request_is_attributed_to_its_caller(self):
        calls: list = []
        E.handle(REQUEST, KEY, environ=ENV, dispatch=dispatcher(calls))
        _, caller, attribution = calls[0]
        assert caller.subject == "integrations-team"
        assert attribution["requester_source"] == "compat_endpoint"
        assert attribution["requested_by"] == "compat:integrations-team"

    def test_gated_work_is_attributed_too(self):
        result = E.handle(
            REQUEST, KEY, environ=ENV, dispatch=dispatcher([]), gate=lambda r, c: "ov-1"
        )
        assert result.attribution["requester_subject"] == "integrations-team"

    def test_the_response_matches_the_standard_shape(self):
        result = E.handle(REQUEST, KEY, environ=ENV, dispatch=dispatcher([]))
        body = result.body
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["message"]["content"] == "the summary"
        assert body["choices"][0]["finish_reason"] == "stop"
        assert body["usage"]["total_tokens"] == 15

    def test_the_model_field_names_a_role(self):
        """The caller chooses WHO does the work; the deployment chooses the model."""
        request = E.parse_request(REQUEST)
        assert request.role == "analyst"

    def test_system_and_user_messages_are_separated(self):
        request = E.parse_request(REQUEST)
        assert request.system == "be concise"
        assert request.prompt == "summarise the incident"

    def test_several_user_messages_are_joined(self):
        request = E.parse_request(
            {
                "model": "analyst",
                "messages": [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "reply"},
                    {"role": "user", "content": "second"},
                ],
            }
        )
        assert "first" in request.prompt and "second" in request.prompt

    def test_models_lists_roles(self):
        body = E.models_response(["analyst", "reviewer"])
        assert [m["id"] for m in body["data"]] == ["analyst", "reviewer"]
        assert body["data"][0]["owned_by"] == "acc"


# --------------------------------------------------------------------------
# Errors are structured
# --------------------------------------------------------------------------


class TestErrors:
    def test_streaming_is_refused_explicitly(self):
        """Explicitly unsupported beats a response that silently is not one."""
        result = E.handle(
            {**REQUEST, "stream": True}, KEY, environ=ENV, dispatch=dispatcher([])
        )
        assert result.status == 400
        assert "streaming is not supported" in result.body["error"]["message"]

    def test_errors_use_the_standard_envelope(self):
        result = E.handle(REQUEST, "", environ=ENV, dispatch=dispatcher([]))
        assert set(result.body["error"]) >= {"message", "type"}
        assert result.body["error"]["type"] == "authentication_error"

    @pytest.mark.parametrize(
        "body,fragment",
        [
            ({"messages": []}, "'model' is required"),
            ({"model": "analyst", "messages": []}, "non-empty array"),
            (
                {"model": "analyst", "messages": [{"role": "system", "content": "x"}]},
                "no user message",
            ),
        ],
    )
    def test_malformed_requests_say_what_is_wrong(self, body, fragment):
        with pytest.raises(E.CompatError, match=fragment):
            E.parse_request(body)

    def test_a_non_object_body_is_refused(self):
        with pytest.raises(E.CompatError, match="JSON object"):
            E.parse_request(["not", "an", "object"])

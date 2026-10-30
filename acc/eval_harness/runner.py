"""Run a scenario suite through ``mlflow.genai.evaluate``.

    python -m acc.eval_harness.runner examples/eval_scenarios/sre.yaml \
        --tracking-uri file:./mlruns --experiment acc-eval-sre

Each scenario becomes one dataset row (README mapping):

* ``inputs`` = ``{scenario_id, entry_role, caller, turns}``
* ``expectations`` = ``{expected_facts, guidelines, must_call, must_not_call,
  call_order, oversight, outcome, forbidden, roles_involved}``

``predict_fn`` is a scripted :class:`FakeAgent` for now: it calls the
``must_call`` tools through ACC's MCP client against a :class:`MockMCPServer`
for the suite, raises an approval before every gated call, and replies with
the expected facts. Each call becomes a TOOL span, which the code scorers read
back from the trace. The live collective replaces it in a later phase.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Callable, Optional

from acc.eval_harness.mock_mcp import MockMCPServer
from acc.eval_harness.mocks import load_suite
from acc.eval_harness.scorers import (
    APPROVAL_REQUEST, ATTR_APPROVED, ATTR_ROLE, ATTR_TOOL, ToolCall, mlflow_scorers,
)

_EXPECTATIONS = {  # scenario expect key -> MLflow expectations key
    "facts": "expected_facts", "guidelines": "guidelines", "must_call": "must_call",
    "must_not_call": "must_not_call", "call_order": "call_order", "oversight": "oversight",
    "outcome": "outcome", "forbidden": "forbidden", "roles_involved": "roles_involved",
}


def scenario_to_row(sc: dict) -> dict:
    exp = sc.get("expect", {})
    return {
        "inputs": {"scenario_id": sc["id"], "entry_role": sc["entry_role"],
                   "caller": sc.get("caller"), "turns": sc["turns"]},
        "expectations": {key: exp[src] for src, key in _EXPECTATIONS.items() if exp.get(src)},
    }


CallTool = Callable[[str, dict, str], Any]   # (tool, args, scenario_id) -> result


class FakeAgent:
    """A scripted, well-behaved agent: it does what each scenario expects."""

    def __init__(self, suite: dict, call_tool: CallTool) -> None:
        self.suite = suite
        self.call_tool = call_tool

    def run(self, sc: dict) -> tuple[list[ToolCall], list[Any], str]:
        """Play *sc*; return the events, each event's result, and the reply."""
        exp = sc.get("expect", {})
        gated = set(self.suite.get("gated_tools", []))
        events: list[ToolCall] = []
        results: list[Any] = []
        for tool in exp.get("must_call", []):
            role = self._role(tool, sc)
            if tool in gated:
                events.append(ToolCall(APPROVAL_REQUEST, {"tool": tool}, False, role))
                results.append({"approved": True})
            args = self._args(tool, sc)
            events.append(ToolCall(tool, args, tool in gated, role))
            results.append(self.call_tool(tool, args, sc["id"]))
        if exp.get("oversight") == "required" and not any(e.name == APPROVAL_REQUEST for e in events):
            events.append(ToolCall(APPROVAL_REQUEST, {"request": sc["turns"][0]["user"]},
                                   False, sc["entry_role"]))
            results.append({"approved": False, "status": "pending"})
        reply = " ".join(f"{fact}." for fact in exp.get("facts", []))
        if exp.get("outcome") == "refuse":
            reply = ("I can't help with that request. " + reply).strip()
        return events, results, reply or "Done; the details are in the tool results."

    def _role(self, tool: str, sc: dict) -> str:
        exp = sc.get("expect", {})
        candidates = [sc["entry_role"], *(t["as"] for t in sc["turns"] if "as" in t),
                      *exp.get("roles_involved", [])]
        roles = self.suite["roles"]
        return next((r for r in candidates if tool in roles[r]["tools"]), sc["entry_role"])

    def _args(self, tool: str, sc: dict) -> dict:
        """Arguments of the mock entry the scenario's own words point at."""
        text = " ".join([t["user"] for t in sc["turns"]]
                        + [str(v) for v in (sc.get("caller") or {}).values()]).lower()
        entries = sc.get("mock_overrides", {}).get(tool, []) + self.suite["mocks"].get(tool, [])
        best, best_hits, has_default = None, 0, False
        for entry in entries:
            when = entry.get("when") or {}
            if not when:
                has_default = True
                continue
            hits = sum(str(v).lower() in text for v in when.values())
            if best is None or hits > best_hits:
                best, best_hits = when, hits
        if best is None or (best_hits == 0 and has_default):
            return {}
        return {k.removesuffix("_contains"): v for k, v in best.items()}


def mcp_caller(base_url: str) -> CallTool:
    """Call a tool through ACC's MCP client at ``<base_url>/<scenario_id>``."""
    from acc.mcp.client import MCPClient  # noqa: PLC0415
    from acc.mcp.manifest import MCPManifest  # noqa: PLC0415

    async def call(tool: str, args: dict, scenario_id: str) -> Any:
        client = MCPClient(MCPManifest(
            server_id="eval_mock", purpose="Eval mock world.",
            transport="streamable-http", url=f"{base_url}/{scenario_id}"))
        try:
            result = await client.call_tool(tool, args)
        finally:
            await client.close()
        text = result["content"][0]["text"]
        return {"error": text} if result.get("isError") else json.loads(text)

    return lambda tool, args, scenario_id: asyncio.run(call(tool, args, scenario_id))


def run_suite(path: str, *, experiment: Optional[str] = None,
              scenario_ids: Optional[list[str]] = None):
    """Evaluate one suite against the fake agent; returns MLflow's EvaluationResult."""
    import mlflow  # noqa: PLC0415
    from mlflow.entities import SpanType  # noqa: PLC0415

    suite = load_suite(path)
    by_id = {sc["id"]: sc for sc in suite["scenarios"]}
    rows = [scenario_to_row(sc) for sc in suite["scenarios"]
            if not scenario_ids or sc["id"] in scenario_ids]
    mlflow.set_experiment(experiment or f"acc-eval-{suite['suite']}")

    with MockMCPServer(suite) as server:
        agent = FakeAgent(suite, mcp_caller(server.url))

        @mlflow.trace(name="fake_agent", span_type=SpanType.AGENT)
        def predict_fn(scenario_id, entry_role, caller, turns):
            events, results, reply = agent.run(by_id[scenario_id])
            for event, result in zip(events, results):
                with mlflow.start_span(name=event.name, span_type=SpanType.TOOL) as span:
                    span.set_inputs(event.args)
                    span.set_attributes({ATTR_TOOL: event.name, ATTR_ROLE: event.role,
                                         ATTR_APPROVED: event.approved})
                    span.set_outputs(result)
            return {"reply": reply}

        return mlflow.genai.evaluate(data=rows, predict_fn=predict_fn,
                                     scorers=mlflow_scorers(suite))


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("suite", help="path to a suite YAML")
    parser.add_argument("--tracking-uri", default=os.environ.get("ACC_MLFLOW_TRACKING_URI"),
                        help="MLflow tracking URI (default: $ACC_MLFLOW_TRACKING_URI)")
    parser.add_argument("--experiment", help="default: acc-eval-<suite>")
    parser.add_argument("--scenario", action="append", help="run only these ids (repeatable)")
    args = parser.parse_args(argv)

    if args.tracking_uri:
        if args.tracking_uri.startswith("file:"):
            # MLflow 3 keeps the file store behind an opt-in.
            os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        import mlflow  # noqa: PLC0415
        mlflow.set_tracking_uri(args.tracking_uri)

    result = run_suite(args.suite, experiment=args.experiment, scenario_ids=args.scenario)
    print(f"run {result.run_id}")
    for name, value in sorted(result.metrics.items()):
        print(f"  {name:32} {value:.3f}")


if __name__ == "__main__":
    main()

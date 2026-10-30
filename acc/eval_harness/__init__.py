"""MLflow eval harness for the mock-world scenario suites (Phase 1).

The suites live in ``examples/eval_scenarios/`` (schema in the README there).
This package plays them:

* :mod:`.mocks` -- load a suite and answer a tool call from its ``mocks``;
* :mod:`.mock_mcp` -- the same answers over MCP streamable-http, so an agent
  calls the mock world exactly as it would call the real system;
* :mod:`.scorers` -- the code scorers (no LLM) over a list of tool-call events;
* :mod:`.runner` -- scenarios -> MLflow dataset rows -> ``mlflow.genai.evaluate``,
  for now against a scripted fake agent.

Why here and not in ``acc-dev-harness``: the scenarios and their consistency
test are in this repo, the scorers read that schema, the mock server is tested
against ACC's own MCP transport, and the later phases (a release gate, runs
from the agent image on acc1 / bb3) need the code wherever ``acc`` is
installed. ``acc-dev-harness`` has no CI and is not in any image.

Nothing here imports mlflow at module load; it is needed only for
:func:`.scorers.mlflow_scorers` and :func:`.runner.run_suite`.
"""

## What
Two baked-in `acc-agent-core` image bugs that block the agent from starting on
OpenShift (restricted-v2) with the hosted Anthropic LLM backend. Both are
independent root causes; the first hid the second.

### 1. Entrypoint PATH reset drops the S2I venv — `exec: python: not found` (#180)
`deploy/entrypoint-agent.sh` line 6 hard-reset `$PATH` to the system dirs,
dropping `/opt/app-root/bin` (the S2I virtualenv that holds `python`). The image
CMD is bare `["python", "-m", "acc.agent"]`. Under the `restricted-v2` SCC the
pod runs as an arbitrary non-root UID and takes the `else: exec "$@"` branch,
where `python` is no longer on `$PATH` (the base image ships only `python3` in
`/usr/bin`) → `exec: python: not found`. Only manifests on OpenShift; root and
baked-UID-1001 runs under podman/docker were unaffected, which is why it slipped
through.

**Fix:** prepend the venv dirs to `$PATH`, keeping the system dirs so
`runuser(8)`/`setpriv(1)`/`su(1)` stay reachable in the root branch (which
inherits the exported PATH).

### 2. rhoai config validator rejects the URL-less anthropic backend (#181)
`ACCConfig._validate_deploy_mode_fields` (`acc/config.py`) hard-required
`llm.vllm_inference_url` or `llm.llama_stack_url` in `deploy_mode: rhoai`, with
no exemption for the hosted `anthropic` backend — which needs only an API key
(`build_llm_backend` instantiates `AnthropicBackend` with no inference URL). An
operator rendering `llm.backend: anthropic` on OpenShift failed config
validation at agent startup with:

```
Value error, llm.vllm_inference_url or llm.llama_stack_url is required in rhoai deploy_mode
```

**Fix:** exempt URL-less hosted backends (`_URLLESS_LLM_BACKENDS = ("anthropic",)`);
`vllm`/`llama_stack`/`openai_compat` still require their URL.

## Live validation (bb3, OCP 4.21)
Built `acc-agent-core-0.5.19` (thin overlay on 0.5.17 carrying both fixes;
preserves the openshell CLI + iproute2 → OpenShell Model-2 cage intact) and
rolled the `bb3-collective-01-assistant` agent:

- Pod `bb3-collective-01-assistant-0`: **1/1 Running, RESTARTS=0**.
- initContainer `openshell-sandbox-create` Completed exit 0, reusing the caged
  sandbox in ns `openshell` (cage intact).
- Real Sonnet call over the live agent loop:

```
INFO httpx HTTP Request: POST https://api.anthropic.com/v1/messages "HTTP/1.1 200 OK"
INFO acc.cognitive_core cognitive_core: task complete (agent_id=assistant-81c5ade7 drift=0.000 latency=1349ms tokens=486)
```

## Notes
- Separate, pre-existing (0.5.17 base, not touched here): the baked
  `all-MiniLM-L6-v2` embedding model fails to load (`Unrecognized model … model_type`),
  degrading RAG/episodic memory only (WARNING). LLM inference is unaffected.

Fixes #180
Fixes #181

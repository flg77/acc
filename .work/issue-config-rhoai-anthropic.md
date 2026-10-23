## Summary
`ACCConfig._validate_deploy_mode_fields` (`acc/config.py`) hard-requires
`llm.vllm_inference_url` **or** `llm.llama_stack_url` in `deploy_mode: rhoai`,
with no exemption for the URL-less hosted `anthropic` backend. An operator that
renders `llm.backend: anthropic` (the supported cloud backend on OpenShift)
therefore fails config validation at agent startup, even though
`build_llm_backend()` fully supports `anthropic` and it needs only an API key.

## Impact
After the entrypoint fix (separate issue), `acc-agent-core` still CrashLoops on
a `deploy_mode: rhoai` corpus wired to Claude/Anthropic:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for ACCConfig
  Value error, llm.vllm_inference_url or llm.llama_stack_url is required in rhoai deploy_mode
```

Observed on bb3 with an operator-rendered `acc-config.yaml`:

```yaml
deploy_mode: rhoai
llm:
  backend: anthropic
  anthropic_model: claude-sonnet-4-6
```

## Root cause
`acc/config.py`:

```python
if not self.llm.vllm_inference_url and not self.llm.llama_stack_url:
    raise ValueError(
        "llm.vllm_inference_url or llm.llama_stack_url is required in rhoai deploy_mode"
    )
```

The check is backend-agnostic. `anthropic` (and `ollama`) are URL-less backends
— `build_llm_backend()` instantiates `AnthropicBackend(model=…)` with no
inference URL — so the requirement is wrong for them. The rule predates the
operator's anthropic-backend support.

## Fix
Exempt URL-less hosted backends from the inference-URL requirement:

```python
_URLLESS_LLM_BACKENDS = ("anthropic",)
if (
    self.llm.backend not in _URLLESS_LLM_BACKENDS
    and not self.llm.vllm_inference_url
    and not self.llm.llama_stack_url
):
    raise ValueError(
        "llm.vllm_inference_url or llm.llama_stack_url is required in rhoai deploy_mode"
    )
```

(Kept minimal — only `anthropic` is exempted, matching the verified backend;
`vllm`/`llama_stack`/`openai_compat` still require their URL.)

## Verified
With this fix (+ the entrypoint fix), the bb3 agent reaches `1/1 Running` and
makes a live Sonnet call: `POST https://api.anthropic.com/v1/messages 200 OK`,
`cognitive_core: task complete (… tokens=480)`.

Fix committed on branch `fix/agent-core-entrypoint-path-venv`.

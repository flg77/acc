# `env/` — Pre-configured stack environments

Operator-facing presets that drop straight into the canonical
sourced location (`deploy/.env`) for the ACC stack.

## Why this directory exists

The compose file at `container/production/podman-compose.yml`
declares `env_file: ../../deploy/.env` on every service, so the
**only file the agent + TUI + MCP containers actually source is
`deploy/.env`** at the repo root.

Editing `examples/*/​.env` (the per-example files the runner
scripts source) propagates env vars into the *runner shell* — but
NOT into the containers themselves. That's the trap the first
operator using a local vLLM hit: the test script worked, the
containers couldn't reach the LLM.

This directory hands out one ready-to-paste preset per supported
LLM backend so operators don't have to author `deploy/.env` by
hand.

## How to use

```bash
# 1. Copy the preset that matches your local vLLM serving setup
cp env/.env.llama-3.2-1B-Instruct-FP8 deploy/.env

# 2. Open + fill in API keys (Anthropic / Brave / etc.)
$EDITOR deploy/.env

# 3. Bring the stack up — every container now sees the right
#    ACC_OPENAI_BASE_URL et al.
./acc-deploy.sh up
```

### Helper

```bash
./env/use.sh llama-3.2-1B-Instruct-FP8         # copies + reports
./env/use.sh                                    # lists available presets
```

The helper preserves any existing `deploy/.env` as
`deploy/.env.bak` before overwriting.

## Catalog (mirrors vllmpunch's models.json)

| Preset file | Model | Default port | Notes |
|---|---|---|---|
| `.env.llama-3.2-1B-Instruct-FP8` | `RedHatAI/Llama-3.2-1B-Instruct-FP8` | 8001 | Compact + fast; good demo default. FP8-quantised. |
| `.env.qwen3-1.7B` | `Qwen/Qwen3-1.7B` | 8002 | Alibaba's Qwen3 base; lean. |
| `.env.granite4-1b-speech` | `ibm-granite/granite-4.0-1b-speech` | 8001 | IBM Granite 4 speech-tuned variant. |
| `.env.qwen3-tts-1.7-custom` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` | 8001 | Qwen3 TTS — 12Hz custom voice. |
| `.env.anthropic` | `claude-sonnet-4-5` (hosted) | n/a | Production-grade; needs paid API key. |
| `.env.example` | (template) | n/a | Comments-only canonical reference. |

> **Port 8001 conflicts.** Three presets above default to 8001 (the
> vllmpunch default for non-qwen3 models).  If you serve more than
> one of them concurrently, set `ACC_OPENAI_BASE_URL` to the right
> port per run — only one model can bind 8001 at a time.

## Container → host networking

Every preset assumes vLLM runs **on the host** and points
`ACC_OPENAI_BASE_URL` at podman's host alias:

```bash
ACC_OPENAI_BASE_URL=http://host.containers.internal:8001/v1
```

For Docker users that's `host.docker.internal`. For environments
where neither alias resolves, use the bridge IP (`ip route | awk
'/default/ {print $3}'` from inside a container).

**vLLM must bind to `0.0.0.0`, not `127.0.0.1`,** for the
container bridge to reach it:

```bash
vllm serve RedHatAI/Llama-3.2-1B-Instruct-FP8 --host 0.0.0.0 --port 8001
```

Reachability ladder (cheapest first):

```bash
# 1. Host can reach vLLM (this is what your test script verified)
curl http://127.0.0.1:8001/v1/models

# 2. Agent container can reach vLLM via the host alias
podman exec acc-agent-arbiter \
    curl -sS http://host.containers.internal:8001/v1/models | head -5

# 3. ACC's own LLM-test command (uses the same backend wiring)
acc-cli llm test --backend openai_compat
```

If step 2 fails with `Could not resolve host`, fall back to the
bridge IP. If step 2 fails with `connection refused`, vLLM is
bound to localhost only — restart it with `--host 0.0.0.0`.

## Layout

```
env/
├── README.md                              ← this file
├── use.sh                                 ← helper that copies into deploy/.env
├── .env.example                           ← canonical template (every var documented)
├── .env.llama-3.2-1B-Instruct-FP8         ← preset
├── .env.qwen3-1.7B                        ← preset
├── .env.granite4-1b-speech                ← preset
├── .env.qwen3-tts-1.7-custom              ← preset
└── .env.anthropic                         ← preset (hosted Claude)
```

## Adding a new preset

```bash
cp env/.env.example env/.env.your-model-name
$EDITOR env/.env.your-model-name
# Update the model id + port + comments at the top.
```

The `use.sh` helper auto-discovers any file matching the
`.env.*` glob; new presets show up in `./env/use.sh` listing
without further wiring.

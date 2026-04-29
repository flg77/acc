# acc-cli — Operator command-line tool

`acc-cli` is the one-shot operator interface to ACC. It surfaces every
internal channel — NATS, LLM backend, role loader, oversight queue —
through a single command, and runs entirely inside a container so the
host needs no Python deps installed.

## Why containerised?

Pip-installing the CLI on a constrained host (e.g. lighthouse) drags in
~600 MB of transitive deps and pollutes `$HOME/.local`. The
`localhost/acc-cli:0.2.0` image isolates those deps under
`/var/lib/containers` and `--rm` cleans up state per invocation. The
image is ~150–200 MB — heavy ML/DB packages
(`lancedb`, `pymilvus`, `sentence-transformers`, `anthropic`,
`opentelemetry-*`) are explicitly excluded since the CLI never embeds,
queries vectors, or emits OTel spans.

## Run command

The canonical entry point is `./acc-deploy.sh cli`. It wraps
`podman run --rm` against `localhost/acc-cli:0.2.0` with the right
defaults: host networking, NATS endpoint forwarding, an `acc-config.yaml`
bind-mount when present, and an SELinux-safe `roles/` bind-mount so
edits show up immediately without an image rebuild.

```bash
./acc-deploy.sh cli [SUBCOMMAND] [ARGS…]
```

The expanded form (what the wrapper actually runs) is:

```bash
podman run --rm \
    --network host \
    -e ACC_NATS_URL=nats://localhost:4222 \
    -e ACC_COLLECTIVE_ID=sol-01 \
    -v $REPO/acc-config.yaml:/app/acc-config.yaml:ro,z \
    -v $REPO/roles:/app/roles:ro,z \
    -it localhost/acc-cli:0.2.0 \
    [SUBCOMMAND] [ARGS…]
```

The `:z` SELinux relabel is mandatory on hosts with the `targeted`
policy enabled (RHEL, Fedora, lighthouse). Without it the container
process gets `EACCES` on every read of the bind-mounted host paths.

## First-time setup

```bash
git pull
./acc-deploy.sh build           # builds nats + redis + agent-core + tui + cli
./acc-deploy.sh up              # start the stack (the CLI image is one-shot, never `up`'d)
```

After `build`, the CLI image is ready to use without further setup.

## Environment overrides

| Variable             | Default                         | Purpose                                                                |
|----------------------|---------------------------------|------------------------------------------------------------------------|
| `ACC_NATS_URL`       | `nats://localhost:4222`         | NATS endpoint. Use `nats://nats:4222` with `ACC_CLI_NETWORK=acc-net`.  |
| `ACC_COLLECTIVE_ID`  | `sol-01`                        | Default collective for `nats`, `oversight`, `trace`.                   |
| `ACC_CLI_IMAGE`      | `localhost/acc-cli:0.2.0`       | Image reference. Override for staged rollouts.                         |
| `ACC_CLI_NETWORK`    | `host`                          | Podman network. `acc-net` joins the compose network.                   |
| `ACC_CONFIG_PATH`    | `$REPO/acc-config.yaml`         | Config file mounted into the container at `/app/acc-config.yaml`.      |

## Subcommand surface

```text
acc-cli                                 # print help

acc-cli role list
acc-cli role show <name> [--format yaml|json]
acc-cli role infuse [<cid>] <name> [--approver-id ID]

acc-cli nats sub '<pattern>' [--limit N] [--raw]
acc-cli nats pub <subject> '<json>'

acc-cli llm test [--prompt TEXT] [--system TEXT] [--config PATH]

acc-cli trace <task_id> [--collective CID] [--limit N] [--from-jetstream]

acc-cli oversight pending [--watch]
acc-cli oversight submit --task-id ID --agent-id ID --risk HIGH|UNACCEPTABLE  <summary…>
acc-cli oversight approve <oversight_id>
acc-cli oversight reject  <oversight_id> [--reason TEXT]
```

## Examples

### Roles

```bash
# Inventory the role library
./acc-deploy.sh cli role list

# Inspect the merged role.yaml + eval_rubric.yaml for one role
./acc-deploy.sh cli role show coding_agent
./acc-deploy.sh cli role show account_executive --format json

# Apply a role to a running collective via NATS ROLE_UPDATE.
# Arbiter countersigns; agents reload at next heartbeat boundary.
./acc-deploy.sh cli role infuse sol-01 sales_engineer
./acc-deploy.sh cli role infuse sol-01 coding_agent --approver-id ops:flg
```

### NATS introspection

```bash
# Tail every signal in a collective (Ctrl-C to stop)
./acc-deploy.sh cli nats sub 'acc.sol-01.>'

# Limit to N messages and inspect raw msgpack bytes
./acc-deploy.sh cli nats sub 'acc.sol-01.heartbeat' --limit 5 --raw

# Publish an arbitrary JSON payload (dev/debug only — no signing)
./acc-deploy.sh cli nats pub acc.sol-01.alert \
    '{"signal_type":"ALERT_ESCALATE","reason":"manual probe","ts":1730000000}'
```

### LLM smoke test

```bash
# Round-trip a tiny prompt against whatever backend acc-config.yaml selects
./acc-deploy.sh cli llm test

# Override the prompt
./acc-deploy.sh cli llm test \
    --system "You are a JSON responder." \
    --prompt 'Reply with {"ok": true}.'
```

The CLI image ships only `httpx` for LLM coverage — that handles every
provider that speaks the OpenAI-compat protocol (vLLM, OpenAI, Groq,
Gemini, OpenRouter, HuggingFace TGI, Together, Anyscale, LM Studio).
For `ollama` / `anthropic` / `llama_stack` the CLI exits with a clear
`missing dep` message; either rebuild the CLI image with the extra dep
or run `llm test` from inside the agent-core image which bundles
everything.

### Task tracing

```bash
# Tail every signal that mentions task_id task-7c91a (Ctrl-C to stop)
./acc-deploy.sh cli trace task-7c91a

# Stop after 5 matches; replay JetStream history first when available
./acc-deploy.sh cli trace task-7c91a --limit 5 --from-jetstream
```

### Human oversight queue

End-to-end demo of the oversight loop introduced by Phase 1.3:

```bash
# 1. Submit a synthetic high-risk item (arbiter enqueues it)
./acc-deploy.sh cli oversight submit \
    --task-id  task-demo-1 \
    --agent-id analyst-x \
    --risk     HIGH \
    "Synthetic high-risk demo for oversight wiring"

# 2. List pending items.  --watch stays attached; otherwise wait one
#    arbiter heartbeat (default 30 s) and exit.
./acc-deploy.sh cli oversight pending
./acc-deploy.sh cli oversight pending --watch

# 3a. Approve via CLI (publishes OVERSIGHT_DECISION on the per-item subject)
./acc-deploy.sh cli oversight approve ov-7c91abcd

# 3b. Or open the TUI Compliance screen, highlight the row, press Enter.
#     Both paths emit the same signal; the arbiter routes it to its
#     HumanOversightQueue.{approve,reject}.

# 4. Confirm
./acc-deploy.sh cli oversight pending          # empty
```

Reject form:

```bash
./acc-deploy.sh cli oversight reject ov-7c91abcd \
    --reason "Output references PHI fields outside the role's domain"
```

## Troubleshooting

### `PermissionError: '/app/roles/...': Permission denied`
The `roles/` bind-mount lacks an SELinux label. The wrapper now passes
`:ro,z`, but a stale older image / wrapper invocation can hit this.
Pull the latest, rebuild the CLI image:

```bash
git pull
./acc-deploy.sh build
```

### `image localhost/acc-cli:0.2.0 not found`
The CLI image was never built. Run:

```bash
./acc-deploy.sh build
```

The compose `cli` profile is auto-included on `build`; suppressed on `up`
(the CLI is one-shot, never a long-lived service).

### `LLM backend 'ollama' requires a missing package: ollama`
The minimal CLI image only ships `httpx`. Two fixes:

* Switch your `acc-config.yaml` to `backend: openai_compat` with the
  Ollama-compat URL (`base_url: http://localhost:11434/v1`,
  `api_key_env: ""`).
* Or run `llm test` from inside the agent-core image which has the full
  dep set — `podman exec acc-agent-arbiter acc-cli llm test` (after the
  next agent-core rebuild includes the CLI binary).

### Output looks garbled in a non-tty pipe
The wrapper auto-detects whether stdin/stdout are TTYs and only adds
`-it` when both are terminals. If you're piping into `jq` or `awk`, the
TTY flags are skipped automatically — no further action needed.

### NATS connection refused
The default `--network host` assumes NATS is reachable on
`localhost:4222`. From inside the compose network use:

```bash
ACC_CLI_NETWORK=acc-net ACC_NATS_URL=nats://nats:4222 \
    ./acc-deploy.sh cli nats sub 'acc.>'
```

## Related docs

* [`docs/howto-tui.md`](howto-tui.md) — graphical operator console.
* [`docs/howto-deploy.md`](howto-deploy.md) — stack lifecycle (`./acc-deploy.sh up/down/...`).
* [`docs/howto-role-infusion.md`](howto-role-infusion.md) — designing
  a role.yaml that `acc-cli role infuse` can publish.

# Per-host acc-config.yaml overlays

Operator-local templates for the test hosts in our LAN
(lighthouse, …) and the models the
[`vllmpunch`](https://github.com/flg77/vllmpunch) wrapper can
launch on each.

The yamls are **tracked in git** so a `git pull` on a test
host brings the right overlays with it.  Each file is already
host-scoped (`<host>-<slug>.yaml`) so committing them is safe
for a single-operator project.

Do **not** commit secrets into these files — use
`ACC_LLM_API_KEY_ENV` to point at an env var (set via
`deploy/.env`, which **is** gitignored).

## Naming

```
<host>-<model-slug>.yaml
```

Examples this directory ships:

* `lighthouse-llama-1b-fp8.yaml`
* `lighthouse-llama-3b-fp8.yaml`     *(operator must pull the model first)*
* `lighthouse-qwen-coder-7b-awq.yaml`
* `lighthouse-r1-distill-8b.yaml`
* `lighthouse-phi4-mini.yaml`
* `lighthouse-mistral-7b-w8a8.yaml`
* `lighthouse-qwen-7b-gptq.yaml`
* `lighthouse-nemo12-fp8.yaml`

Extend for additional hosts by following the same pattern:
`acc1-llama-8b-fp8.yaml`, etc.

## Sync to a test host

Use [`scripts/sync-host-config.sh`](../../scripts/sync-host-config.sh):

```bash
./scripts/sync-host-config.sh lighthouse llama-1b-fp8
# → scps deploy/host-configs/lighthouse-llama-1b-fp8.yaml
#   to lighthouse:/git/ml/agentic/acc-fresh/acc/acc-config.yaml
```

The remote path is read from the script's `ACC_REMOTE_PATH`
constant; edit there if your test repos live elsewhere.

## Adding a new model

1. Tell `vllmpunch` about it (add an entry under
   `~/.config/vllmpunch/models.json` on the host with a unique
   `host_port`).
2. Copy `template.yaml` to `<host>-<slug>.yaml` here.
3. Fill in `base_url`, `model`, `request_timeout_s`, comments.
4. Run via the sync script + the test-history skill.

## See also

* [`docs/llm-backends.md`](../../docs/llm-backends.md) — model
  catalogue, VRAM sizing, recommended use per model.
* [`test/history/`](../../test/history/) — operator-local
  test-result archive that lets us compare model quality over
  time.
* `~/.claude/skills/acc-llm-test-history/` — skill that drives
  tests + appends to the history archive.

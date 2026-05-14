# Test-result history (operator-local)

Operator-local archive of LLM test runs across our test hosts.
Lets us **learn from results**: pick the next model to try
based on what we've already seen, spot regressions when a
model gets re-tested after a config change, compare quality
across hosts.

## Format

One JSONL file per test name.  Filename: `<test_name>.jsonl`.
Each line is a single test run's metadata:

```json
{
  "ts":                "2026-05-14T19:42:11Z",
  "host":              "lighthouse",
  "test_name":         "ascii-banner",
  "model_alias":       "qwen-coder-7b-awq",
  "model_id":          "Qwen/Qwen2.5-Coder-7B-Instruct-AWQ",
  "base_url":          "http://127.0.0.1:8013/v1",
  "passed":            true,
  "latency_ms":        2317,
  "prompt_tokens":     973,
  "completion_tokens": 124,
  "total_tokens":      1097,
  "finish_reason":     "stop",
  "failure_reason":    "",
  "banner_chars":      287,
  "artefact_path":     "/tmp/acc_banner.txt",
  "git_sha":           "e62526c",
  "notes":             ""
}
```

## File layout

```
test/history/
├── ascii-banner.jsonl
├── coding-fizzbuzz.jsonl              (future test)
├── research-summary.jsonl             (future test)
└── …
```

## Conventions

* **One line per test invocation.**  Append-only; never edit
  past rows.
* **UTC ISO-8601 timestamps**, suffix `Z`.
* **`passed`** is the strict pass/fail per the test's
  operator-facing criteria (not "did the LLM say something").
* **`failure_reason`** is operator-readable when `passed=false`;
  empty when `passed=true`.
* **`notes`** is freeform — use for operator-side context
  ("trying after vllmpunch upgrade", "first run after
  prompt-engineering change", …).

## Skill

`~/.claude/skills/acc-llm-test-history/SKILL.md` drives test
runs against this archive — it sets the env vars, runs pytest,
captures the artefact, appends the row, and reports a summary
comparing against the most recent prior run.

## Pruning

The JSONL files grow forever.  If a test history exceeds 1000
rows, rotate manually:

```bash
mv test/history/ascii-banner.jsonl test/history/ascii-banner.$(date +%Y%m%d).jsonl
```

The skill always appends to the canonical (unrotated) filename.

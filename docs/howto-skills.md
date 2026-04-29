# How to add a Skill to ACC

A **Skill** is a Python adapter ACC ships in-house that exposes one
versioned capability behind a JSON-schema contract.  In the
biological metaphor, skills are the cell's *organelles* — the
mitochondrion, the ribosome — purpose-built machines a role can
dispatch work to without re-implementing the machinery in every role.

This guide walks through the full lifecycle:

1. Create the skill directory.
2. Write the manifest.
3. Write the adapter.
4. Wire it into a role.
5. Verify in the TUI Ecosystem screen.
6. Drive it from the LLM.

## 1. Directory layout

```
skills/
├── _base/
│   ├── skill.yaml          # defaults — already exists
│   └── README.md
└── word_count/             # ← your new skill
    ├── skill.yaml
    └── adapter.py
```

The directory name must be the same `lowercase_snake_case` string you
will use as `skill_id` in the manifest.  `_base/`, `TEMPLATE/`, and
`__pycache__/` are excluded from discovery.

## 2. Manifest

`skills/word_count/skill.yaml`:

```yaml
purpose:        "Count words and characters in a text input."
version:        "0.1.0"

adapter_module: "adapter"
adapter_class:  "WordCountSkill"

input_schema:
  type: object
  required: [text]
  properties:
    text:
      type: string
      minLength: 0

output_schema:
  type: object
  required: [words, chars]
  properties:
    words: { type: integer, minimum: 0 }
    chars: { type: integer, minimum: 0 }

risk_level:       "LOW"
requires_actions: []
description:      "Pure-string word/char counter with no side effects."
tags:             ["text", "diagnostic"]
```

`risk_level` must be one of `LOW | MEDIUM | HIGH | CRITICAL`.  Cat-A
rule **A-017** denies invocation when the caller role's
`max_skill_risk_level` ranks below the manifest's level
(see `acc/governance_capabilities.py`).

`requires_actions` composes with `RoleDefinitionConfig.allowed_actions`
— every entry must be in the calling role's allow-list, or A-017
denies.  Use this for skills that wrap a privileged action (e.g.
`call_external_api`).

## 3. Adapter

`skills/word_count/adapter.py`:

```python
from acc.skills import Skill

class WordCountSkill(Skill):
    async def invoke(self, args: dict) -> dict:
        text = args["text"]              # validated by input_schema
        return {
            "words": len(text.split()),
            "chars": len(text),
        }
```

The base class handles registry binding; do NOT override `__init__`
to require parameters.  Read environment variables inside `invoke`
when you need configuration.

The registry validates input against `input_schema` before calling
`invoke`, and validates the return value against `output_schema`
afterwards.  Adapter exceptions are wrapped as
`SkillInvocationError`.

## 4. Wire it into a role

Edit `roles/<role_name>/role.yaml`:

```yaml
role_definition:
  # ... existing fields ...

  allowed_skills:
    - echo
    - word_count            # ← add here
  default_skills:
    - word_count            # ← advertised in the LLM system prompt
  max_skill_risk_level: "MEDIUM"
```

Empty `allowed_skills` denies every skill (fail-closed default).
`default_skills` is the subset listed in the prompt's "Available
skills" block — skills only in `allowed_skills` are reachable but
the LLM has to be told about them out-of-band.

## 5. Verify in the TUI

Boot the TUI, navigate to the **Ecosystem** screen, and confirm a
new row appears in the SKILLS table:

| Skill        | Version | Risk  | Requires |
|--------------|---------|-------|----------|
| echo         | 0.1.0   | LOW   | —        |
| word_count   | 0.1.0   | LOW   | —        |

Risk colour: green for LOW, yellow for MEDIUM, red for HIGH, bold
red for CRITICAL.

Headless verification:

```python
from acc.skills import SkillRegistry
reg = SkillRegistry()
reg.load_from("skills")
print(reg.list_skill_ids())          # should include 'word_count'
print(reg.manifest("word_count").input_schema)
```

## 6. Drive it from the LLM

In the agent's task loop, the LLM emits a `[SKILL:...]` marker on a
line of its response:

```
The document contains the following:

[SKILL: word_count {"text": "ACC is a biologically-grounded agent corpus"}]

Based on the count above, the document is short.
```

The `acc.capability_dispatch.parse_invocations` parser extracts every
marker; `dispatch_invocations` runs each through
`CognitiveCore.invoke_skill` (which fires Cat-A A-017) and folds the
results into the `TASK_COMPLETE` payload's `invocations` field.

### Marker grammar

```
[SKILL: <skill_id> {<json args>}]
[SKILL: <skill_id>]                  # args default to {}
```

* `<skill_id>` matches `[a-z][a-z0-9_]*`.
* `<json args>` is a single-line JSON object literal — multi-line
  payloads are NOT supported (intentional: keeps the parser simple
  and the LLM output diff-friendly).
* The closing `]` must appear on the same line as the marker.

Malformed JSON yields an `InvocationOutcome` with
`error="json_decode: ..."` and the marker is NOT dispatched.  The
agent task loop logs this at WARNING and continues with the next
marker.

## Risk levels and the human oversight queue

A skill with `risk_level: CRITICAL` always sets
`CapabilityDecision.needs_oversight=True`, even when A-017 lets the
call through.  The agent task loop is expected to enqueue an
`OVERSIGHT_SUBMIT` request to the human-oversight queue (EU AI Act
Art. 14) before the adapter runs — the broker for this is the
arbiter's `HumanOversightQueue`.

The current dispatcher does NOT pause on `needs_oversight`; it logs
the request at INFO and proceeds.  A future PR will add a blocking
mode for CRITICAL invocations.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Skill not in `Ecosystem` table | `skills/<id>/skill.yaml` missing or fails Pydantic validation — check `acc-agent` logs at WARNING. |
| `SkillNotFoundError` at runtime | `skill_id` typo, OR the directory was renamed without restarting the agent. |
| `SkillForbiddenError: A-017 blocked` | `skill_id` not in role's `allowed_skills`, or `requires_actions` not all present in `allowed_actions`, or `risk_level > max_skill_risk_level`. |
| `SkillSchemaError` on call | Args don't match `input_schema`.  The error's `errors` attribute carries a list of `{path, message, validator}` dicts. |
| Adapter never called | Check the LLM output literally contains `[SKILL: ...]` — case-sensitive, single-line, balanced braces. |

## See also

* [`acc/skills/__init__.py`](../acc/skills/__init__.py) — public API.
* [`acc/skills/manifest.py`](../acc/skills/manifest.py) — full field
  reference for `skill.yaml`.
* [`acc/governance_capabilities.py`](../acc/governance_capabilities.py)
  — A-017 enforcement source of truth.
* [`docs/howto-mcp.md`](howto-mcp.md) — same workflow but for
  external MCP server integrations.

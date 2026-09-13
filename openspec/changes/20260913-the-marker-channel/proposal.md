# 20260913-the-marker-channel — proposal

Backlog: `20-backlog/mcp/` item **MC-04** ([[MC-04 — The action-marker channel is
fragile]]), both defects seen repeatedly in the AS-04 runs against midojo.
Completes the MC family's cheap half after MC-02 (#401) and MC-03 (#402).

## Why

### 1. Two markers on one line: all but the first are lost

The argument group is `(\{[^\n]*\})?` — greedy to the **last** `}` on the line.
Given what a model actually emits:

```
[MCP: midojo_weather.list_cities {}][MCP: midojo_weather.get_weather {"city":"New York"}]
```

the first marker's args capture everything up to the final `}`, the regex
consumes the whole line as one match, and ACC dispatches **one** invocation
with unparseable arguments:

```
capability_dispatch: malformed args in [MCP: …list_cities {}][MCP: …list_cities {}]
  -- skipped a malformed tool call -- its arguments weren't valid JSON
  (Extra data at col 3)
```

Measured on lighthouse: in one AS-04 run the model emitted five markers in a row
and ACC ran the first; `list_cities` was called four times and succeeded once.
The prompt does say "on its own line", but a model batching calls is ordinary
behaviour, not an error, and the failure is silent to the requester — the reply
simply does less than it says. It also matters more now that MC-03 feeds results
back: a lost call is a lost result.

### 2. Model control tokens reach the answer

gpt-oss emits harmony markup, and it arrives verbatim in the text ACC hands the
channel:

```
<|start|>assistant<|channel|>commentary<|message|>[MCP: …]<|call|>
```

Cosmetic on a benchmark; not cosmetic in front of an operator.

## What changes

### Phase 1 (this ship)

1. **The parser matches balanced JSON, not "to the last brace".** The marker
   head is found by regex; the argument object is decoded with
   `json.JSONDecoder().raw_decode`, which knows about nesting, strings and
   escapes, and reports where the value ended. Adjacent markers therefore split
   correctly, nested arguments keep working, and a genuinely malformed argument
   still produces `args_error` for the same audit line as today.
2. **Control tokens are stripped at the backend boundary**, where the model is
   known. Harmony structures content as
   `<|start|>{role}<|channel|>{channel}<|message|>{content}<|end|>`, so the rule
   is: keep the text before the first control token, plus each segment that
   follows a `<|message|>`, and drop the rest. That removes the channel names as
   well as the delimiters, and it is a no-op for any model that emits none.

### Phases 2–N (deferred)

* Markers inside fenced code blocks are still parsed — a model quoting an
  example invokes it. Needs a fence-aware scan and its own decision about what
  quoting means.
* Other vendors' control-token families, as they appear.

## Impact

* **Affected code:** `acc/capability_dispatch.py` (the parser),
  `acc/backends/llm_openai_compat.py` (the strip), `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** `tests/test_marker_channel.py` — adjacent markers of each kind and
  mixed, nesting preserved, malformed arguments still reported, whitespace and
  no-argument forms, and the control-token cases seen on lighthouse.
* **Backward compatibility:** a single marker with valid arguments parses
  identically. Two behaviour changes, both strictly better: adjacent markers
  now dispatch instead of being dropped, and arguments may span lines (the
  decoder does not care about newlines, where `[^\n]*` did).

## What stays open after Phase 1

* Dispatching *more* markers per reply is what the fix enables — the gate
  semantics per marker are unchanged, but a reply that previously ran one
  effect may now run several. That is the intended behaviour and the reason
  each marker is still gated individually.
* A model that emits a marker inside prose it did not mean as a call is still
  taken at its word.

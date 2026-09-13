# 20260912-mcp-tools-in-the-prompt — proposal

Backlog: `20-backlog/mcp/` item **MC-02** ([[MC-02 — The prompt advertises MCP
servers but not their tools]]), measured in the AS-04 run against midojo
(`20-backlog/asago/AS-00` §5e).

## Why

An agent is told *that* it has an MCP server and never told what the server can
do. `acc/cognitive_core.py` renders one line per advertised server:

```
Available MCP servers (external tool providers).  Invoke a tool by emitting
EXACTLY this marker on its own line:
  [MCP: <server_id>.<tool_name> {<json args>}]
  - midojo_weather: <the manifest's purpose string>
```

`<tool_name>` is never supplied, so the model guesses it. Measured on lighthouse
2026-09-12, pointing midojo's weather suite at a real cell: the model produced
`get_current` and `current` for a server whose tools are `get_weather`,
`list_cities` and `send_weather_alert`. A-018 correctly blocked each guess, the
tool never ran, and the first full 15-cell run scored **N/A on 13 cells** for
that reason alone — no tool call, so no result, so nothing to measure. The run
only proceeded after the tool list was hand-written into the manifest's
`purpose` string as a fixture hack.

The information already exists twice: the manifest declares `allowed_tools`
(which is what A-018 enforces), and the live server answers `tools/list` (ACC
calls it — `mcp: initialised server_id=…`). Neither reaches the prompt.

## What changes

### Phase 1 (this ship)

1. **`MCPManifest.tools`** — an optional list of `{name, summary, args}` per
   tool. Validated against the allow/deny lists so a manifest can never
   advertise a tool A-018 would refuse: a declared tool must appear in
   `allowed_tools` when that list is non-empty, and must not appear in
   `denied_tools`.
2. **The prompt block renders them.** Per advertised server: the server line as
   today, then its tools — `name {"arg": …}` plus the one-line summary. When a
   manifest declares no `tools`, the names from `allowed_tools` are rendered on
   their own (a free win for `github_api`, `google_workspace`, `signal`,
   `echo_server`, `web_fetch`, `web_search_brave`, `web_browser_harness`). When
   it declares neither, the block is exactly what it is today.
3. **A cap**, so a 40-tool server cannot silently eat the window: at most
   `MAX_ADVERTISED_TOOLS` (12) per server, then `… and N more`. Ten real names
   beat guessing forty.
4. **`mcps/echo_server/mcp.yaml`** declares its one tool, as the documented
   shape for contributors; `docs/howto-mcp-sources.md` gains the field.

### Phases 2–N (deferred)

* **Live enrichment from `tools/list`** at registry load, intersected with
  `allowed_tools` — the only source that knows argument *types* and stays
  correct when a server moves. Deferred because it puts a network call on the
  startup path and needs a cache + a stale story; the manifest is offline,
  reviewable and already signed.
* Rendering argument types and required/optional, once a schema source exists.
* The same treatment for skills, if the marker-guessing failure shows up there.

## Impact

* **Affected code:** `acc/mcp/manifest.py`, `acc/cognitive_core.py`,
  `mcps/echo_server/mcp.yaml`, `docs/howto-mcp-sources.md`, `CHANGELOG.md`.
* **New env knobs:** none.
* **Tests:** `tests/test_mcp_tool_advertisement.py` — the manifest field and its
  validation, the three rendering sources (declared tools / allowlist names /
  neither), the cap, and that the advertised set never exceeds what A-018 allows.
* **Backward compatibility:** `tools` defaults to empty and every existing
  manifest keeps validating. A role whose servers declare no tools and no
  allowlist gets byte-identical prompt text; anything else gets strictly more
  information in the cacheable prefix.

## What stays open after Phase 1

* A manifest's tool list can drift from the server's actual tools — nothing
  reconciles them until Phase 2. A wrong name in a manifest is as bad as a
  guess, so the field is documentation the operator owns.
* Argument *types* are still not conveyed (names only), so a model can still
  send a string where a number was wanted; with no tool-result turn
  ([[MC-03 — ACC never returns a tool result to the model]]) it will not learn
  from the failure either.
* Servers with an empty `allowed_tools` (`arxiv`, `wikipedia`, `rss_fetch`,
  `semantic_scholar`, `web_archive`) still advertise nothing until someone
  writes their tools down.

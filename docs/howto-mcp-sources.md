# How-to — trustable sources for MCPs and skills

ACC's capability pool grows by adding `mcps/<id>/mcp.yaml` (for MCP
servers) or `skills/<id>/` directories (for skills). This page is the
operator's vetting checklist when picking what to consume vs. what to
build.

## TL;DR

* **Skills** are mostly **home-grown** — ACC's risk model + governance
  integration is unique. We borrow *patterns* from Anthropic Skills,
  Claude Code's built-in tools, and the Computer Use shape; we do not
  consume them as binaries.
* **MCPs** are mostly **consumed**. Three trust tiers below.

## Tier A — first-party / Anthropic-official

Lowest review burden; assume well-maintained unless evidence says
otherwise.

| Source | What it offers |
|---|---|
| [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | filesystem, github, gitlab, postgres, sqlite, brave-search, google-maps, slack, fetch, time, memory, sequential-thinking, puppeteer, everart, sentry |
| [github.com/modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | Official Python SDK for building |
| [github.com/modelcontextprotocol/typescript-sdk](https://github.com/modelcontextprotocol/typescript-sdk) | Official TypeScript SDK |
| Vendor-official MCPs | Stripe (`@stripe/mcp`), Linear, Atlassian (Jira/Confluence), Notion, Cloudflare, Sentry — first-party means the vendor maintains it |

## Tier B — curated marketplaces

Discoverability layer; each entry is still a third-party package that
needs the Tier C checklist before merging.

| Source | Notes |
|---|---|
| [smithery.ai](https://smithery.ai) | Has a CLI + signature check; vetted at submission time |
| [pulsemcp.com](https://pulsemcp.com) | Catalog + index; lighter vetting |
| [glama.ai/mcp/servers](https://glama.ai/mcp/servers) | Alternative catalog |
| [mcp.so](https://mcp.so) | Broad index; least vetted — discovery only |

## Tier C — community GitHub (operator-vetted per package)

Apply this checklist **before** adding `mcps/<id>/mcp.yaml`:

- [ ] Stars ≥ 100 **OR** last-commit ≤ 90 days **OR** known maintainer.
- [ ] `LICENSE` present and compatible (MIT, BSD, Apache-2.0).
- [ ] Source readable; no `eval` or arbitrary string interpolation
      in adapter handlers.
- [ ] Pin to a tag or commit hash in the `command` array of the
      `mcp.yaml` (not `main`).
- [ ] Read what tools the server exposes; tighten via `allowed_tools`
      and `denied_tools` in the manifest if write-shape tools exist.
- [ ] If the server requires secrets, route them via env vars in
      `mcp.yaml` (`env:` block); never inline.

### Phase 1 — vetted now

| MCP | Repo | Trust note |
|---|---|---|
| `arxiv` | [github.com/blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) | Python, ~1.5k stars, active 2025-2026 |
| `wikipedia` | [github.com/Rudra-ravi/wikipedia-mcp](https://github.com/Rudra-ravi/wikipedia-mcp) | Python, ~300 stars, simple |
| `semantic_scholar` | [github.com/zongmin-yu/semantic-scholar-fast-mcp-server](https://github.com/zongmin-yu/semantic-scholar-fast-mcp-server) | Python, niche |
| `github_api` | [modelcontextprotocol/servers/src/github](https://github.com/modelcontextprotocol/servers/tree/main/src/github) | Tier A — Anthropic-official |
| `web_archive` | [github.com/ThePR0M3TH3AN/mcp-wayback](https://github.com/ThePR0M3TH3AN/mcp-wayback) | Wayback Machine read-only |
| `rss_fetch` | [github.com/spences10/mcp-rssfeed](https://github.com/spences10/mcp-rssfeed) | Roll-our-own candidate (Phase 5) |

## Sources for skills (mostly home-grown)

Skills are ACC's internal abstraction; they live in `skills/<id>/`
with a Pydantic-validated manifest and a Python adapter. The
relevant external references are **pattern libraries**:

| Source | Why useful |
|---|---|
| [Anthropic Skills](https://docs.anthropic.com/en/docs/agents-and-tools/skills) (e.g. `~/.claude/skills/`) | Reference shapes for prompt-and-code modules. Not ACC-runtime-compatible — we copy patterns, not binaries. |
| Claude Code's built-in tools (Bash, Read, Edit, Grep, Glob) | Shape reference for the OS-basics suite (`ls_dir`, `read_text_head`, `grep_text`, etc.) |
| [Anthropic Computer Use](https://docs.anthropic.com/en/docs/computer-use) | Reference for any future GUI/screenshot skill |

## Phase 1 OS-basics — what we built ourselves

Twelve narrow read-only-ish primitives + one HIGH-risk opt-in
shell escape:

```
skills/
├── ls_dir/           LOW    — list directory
├── stat_path/        LOW    — os.stat
├── read_text_head/   LOW    — first N bytes
├── read_text_tail/   LOW    — last N bytes
├── grep_text/        LOW    — literal pattern in file
├── find_files/       LOW    — rglob
├── which_cmd/        LOW    — shutil.which
├── env_get/          LOW    — allowlisted env vars only
├── pwd/              LOW    — os.getcwd
├── disk_free/        LOW    — shutil.disk_usage
├── git_status/       MEDIUM — git status --short (code-domain only)
├── git_log_recent/   MEDIUM — git log --oneline (code-domain only)
└── shell_exec/       HIGH   — subprocess.run argv (engineering family + oversight)
```

All thirteen wrap Python stdlib or `subprocess.run` with explicit
argv (never `shell=True`, never string interpolation of LLM output).

## Gap analysis

| Category | Build vs. consume | Why |
|---|---|---|
| OS narrow primitives | **Build** | ACC-unique sandbox + governance integration |
| `shell_exec` | **Build** | Must integrate with `needs_oversight` + A-017 + Cat-B token budget |
| arxiv, wikipedia, semantic_scholar, web_archive | **Consume** | Mature community MCPs exist |
| `web_fetch`, `web_search_brave`, `web_browser_harness` | **Already built** | Phase-0 substrate |
| `rss_fetch` | Either | Community first; consider roll-our-own with `feedparser` (Phase 5) for tighter audit |
| Real skill implementations (Phase 2) | **Build** | The 14 LLM-roundtrip stubs need ACC-specific real handlers |
| GitHub / GitLab / Postgres / Slack | **Consume** | Anthropic-official servers |
| Stripe / Linear / Notion / Jira | **Consume** | Vendor-official MCPs |
| `acc_collective_status` / `acc_role_catalog` / `acc_golden_prompt_runner` | **Build later (Phase 5)** | Surfaces ACC's own internals as MCPs |

## Adding a new MCP — the procedure

1. Vet the source against the Tier C checklist above.
2. Create `mcps/<server_id>/mcp.yaml` mirroring the existing
   `mcps/arxiv/mcp.yaml` shape. Use `transport: stdio` + `command:`
   for community packages; `transport: http` + `url:` for HTTP servers
   you run yourself.
3. Set `risk_level` honestly (the EU AI Act tier — LOW/MEDIUM/HIGH/CRITICAL).
4. Tighten `allowed_tools` / `denied_tools` if the upstream exposes
   write-shape tools you don't want roles to call.
5. Set `requires_actions` if HIGH risk; the role's `allowed_actions`
   must include the matching label.
6. Open `roles/<role>/role.yaml` and add the `server_id` to
   `allowed_mcps`. The role must also raise `max_mcp_risk_level` to
   match the manifest if it exceeds the default MEDIUM.
7. `pytest tests/test_mcp_registry_new_servers.py` (extend the
   parametrize block).

## Adding a new skill — the procedure

1. Read `docs/howto-skills.md` (Phase 4 substrate doc).
2. Create `skills/<skill_id>/skill.yaml` and
   `skills/<skill_id>/adapter.py`. Adapter subclasses
   `acc.skills.Skill` and implements `async invoke(args) -> dict`.
3. Validate I/O against the JSON Schemas in the manifest; the
   registry double-checks on every call.
4. Set `risk_level` honestly; CRITICAL ⇒ every call enqueues an
   `OVERSIGHT_SUBMIT`.
5. `requires_actions` if the skill mutates state outside the
   workspace sandbox.
6. Wire into role.yaml `allowed_skills` (the A-017 whitelist).
7. Write tests under `tests/test_<skill>_skill.py`.

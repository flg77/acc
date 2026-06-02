# 20260603-capability-pool — proposal

## Why

Two-week recon snapshot:

* 17 skills exist, but 14 are LLM-emit stubs with no real handler.
* 4 MCPs exist (echo_server, web_fetch, web_search_brave,
  web_browser_harness).
* **38 of 52 shipped roles** declare neither `allowed_skills` nor
  `allowed_mcps` — they can chat, but they can't *do* anything.

The substrate (SkillRegistry + MCPRegistry + A-017/A-018 governance
gates + Pydantic manifests with risk tiers) is solid. The gap is
**catalog coverage**, not architecture. Phase 1 unblocks every role
from "nothing to do" by shipping a universal OS-navigation suite +
research MCP triad.

## What changes

### Phase 1 (this ship — v0.3.48)

* **1.1** Twelve narrow OS-basics skills (`ls_dir`, `stat_path`,
  `read_text_head`/`tail`, `grep_text`, `find_files`, `which_cmd`,
  `env_get`, `pwd`, `disk_free`, `git_status`, `git_log_recent`).
  Each LOW or MEDIUM risk; thin wrappers over stdlib + `subprocess.run`
  with explicit argv lists.
* **1.2** One opt-in `shell_exec` skill at HIGH risk. Requires both
  `execute_shell` in `role.allowed_actions` AND
  `max_skill_risk_level: HIGH`. Every approved call still surfaces on
  the Compliance oversight queue (A-017 + needs_oversight).
* **1.3** Six new MCP manifests — arxiv, wikipedia, semantic_scholar,
  github_api, web_archive, rss_fetch. All stdio transport;
  community/official packages launched via `uvx` or `npx`.
* **1.4** New `os_basics: bool = False` field on
  `RoleDefinitionConfig`. When True, auto-grants the 10 always-on OS
  primitives + 2 git skills for code-domain roles. Composes with
  `workspace_access` (disjoint skill sets).
* **1.5** Every shipped role.yaml gains `os_basics: true` + the
  universal research MCP triad (`arxiv, wikipedia, web_fetch`) in
  `allowed_mcps`. Engineering family (9 roles) additionally gets
  `shell_exec` + `execute_shell` action + `max_skill_risk_level: HIGH`.

### Phases 2–5 (deferred, designed in tasks.md)

* **2** — Real implementations for the 14 LLM-roundtrip skill stubs
  (code_generation, code_review, dependency_audit, test_execution, …).
* **3** — Coding/DevOps/Data/ML family MCPs (gitlab_api, postgres,
  sqlite, redis, docker_api, kubectl_api) + Python execution skill in
  podman sandbox.
* **4** — Research/Marketing/Sales family (slack, linear, notion,
  google_scholar).
* **5** — HR/Finance/Compliance/ACC-self family (jira, confluence,
  ACC-internal MCPs: collective_status, role_catalog,
  golden_prompt_runner).

## Impact

* **Affected code (Phase 1):**
  * `acc/config.py` — `os_basics` field + `_grant_os_basics_skills` validator
  * `skills/<id>/` × 13 (12 OS-basics + shell_exec)
  * `mcps/<id>/` × 6 (arxiv, wikipedia, semantic_scholar, github_api,
    web_archive, rss_fetch)
  * `roles/*/role.yaml` × 50 (bulk flip via `scripts/grant_os_basics_to_roles.py`)
* **New env knobs:** none.
* **Tests:** 43 new across four files
  (`test_os_basics_role_flag.py`, `test_os_basics_skills.py`,
  `test_mcp_registry_new_servers.py`, `test_governance_shell_exec.py`).
* **Backward compatibility:** purely additive. Roles default to
  `os_basics: false`; existing role.yaml files without the field see
  no behaviour change. The 50 bulk-flipped role.yaml files do change
  behaviour — every role now sees the 10 OS-basics skills + research
  MCP triad in its perception block (v0.3.45 workspace renderer
  already handles the larger list).

## What stays open after Phase 1

* Real handlers for the 14 stub skills (Phase 2).
* Per-family MCP suites (Phases 3–5).
* ACC-internal MCPs (Phase 5).
* Cosign signature verification for community MCPs (Phase 5 alongside
  `.accpkg`).
* No GUI/computer-use skills — out of scope.

## Trusted source registry

Lives at `docs/howto-mcp-sources.md`. Three tiers — Anthropic-official,
curated marketplaces, community-with-vetting — plus per-package
recommendations for the Phase 1 set.

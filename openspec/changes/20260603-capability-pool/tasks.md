# 20260603-capability-pool — tasks

## Phase 1 (v0.3.48) — universal foundation

### 1.1 OS-navigation skill suite
- [x] `acc/config.py` — `_OS_BASIC_SKILLS` tuple + `_GIT_SKILLS`
      tuple + `_CODE_DOMAINS` frozenset on `RoleDefinitionConfig`.
- [x] 12 `skills/<id>/{skill.yaml,adapter.py,__init__.py}` dirs via
      `scripts/gen_os_basic_skills.py`.
- [x] Manifest risk levels: ls_dir / stat_path / read_text_*
      / grep_text / find_files / which_cmd / env_get / pwd / disk_free = LOW;
      git_status / git_log_recent = MEDIUM.

### 1.2 shell_exec
- [x] `skills/shell_exec/skill.yaml` — risk_level HIGH,
      requires_actions=[execute_shell].
- [x] `skills/shell_exec/adapter.py` — `subprocess.run(argv,
      shell=False, …)`, hardcoded timeout (default 30s, max 600s),
      output cap 64KB stdout + 16KB stderr.

### 1.3 Browser / research MCP family
- [x] `mcps/arxiv/mcp.yaml` (LOW, stdio, uvx + blazickjp/arxiv-mcp-server).
- [x] `mcps/wikipedia/mcp.yaml` (LOW, stdio, uvx + Rudra-ravi/wikipedia-mcp).
- [x] `mcps/semantic_scholar/mcp.yaml` (LOW, stdio,
      uvx + zongmin-yu/semantic-scholar-fast-mcp-server).
- [x] `mcps/github_api/mcp.yaml` (MEDIUM, stdio,
      npx + modelcontextprotocol/server-github) — read-shape only.
- [x] `mcps/web_archive/mcp.yaml` (LOW, stdio, uvx + ThePR0M3TH3AN/mcp-wayback).
- [x] `mcps/rss_fetch/mcp.yaml` (LOW, stdio, npx + spences10/mcp-rssfeed).

### 1.4 os_basics field
- [x] `RoleDefinitionConfig.os_basics: bool = False` field.
- [x] `_grant_os_basics_skills` validator — adds 10 OS primitives;
      conditionally adds 2 git skills for `domain_id` in
      `_CODE_DOMAINS`.
- [x] Composes with `_grant_workspace_skills` (disjoint sets, both run).

### 1.5 Bulk role.yaml flip
- [x] `scripts/grant_os_basics_to_roles.py` — idempotent text-level
      flip; adds `os_basics: true` + universal MCP triad
      (arxiv, wikipedia, web_fetch).
- [x] Engineering family (coding_agent + 5 variants + devops_engineer
      + data_engineer + ml_engineer): also gains `shell_exec`,
      `execute_shell` action, `max_skill_risk_level: HIGH`.
- [x] Manual fix-up for assistant role.yaml (script broke its
      multi-paragraph seed_context — inserted os_basics after
      seed_context ends).
- [x] Manual add of shell_exec block to 3 eng roles without an
      existing `allowed_skills:` line (data_engineer, devops_engineer,
      ml_engineer).

### Tests
- [x] `tests/test_os_basics_role_flag.py` — auto-grant, idempotence,
      code-domain vs. non-code, workspace+os_basics composition,
      all-roles-have-os_basics, eng-family-has-shell_exec,
      universal-triad-present.
- [x] `tests/test_os_basics_skills.py` — happy path for each adapter
      + sandbox-bound rejection (path escape, alnum guard, env
      allowlist, command-not-found, timeout).
- [x] `tests/test_mcp_registry_new_servers.py` — manifest load +
      transport + risk + read-shape lockdown on github_api.
- [x] `tests/test_governance_shell_exec.py` — A-017 four-check
      pipeline on shell_exec (allowlist, action, risk, accept).

### Docs
- [x] `docs/howto-mcp-sources.md` — three-tier trust registry +
      per-package vet checklist + adding-MCP / adding-skill
      procedures.

### Verification
- [x] Targeted: 43 new tests passing.
- [ ] Full sweep: `pytest tests/ --ignore=tests/container --no-cov -q`
      — target ≥ 2546 passing (2503 + 43).
- [ ] Lighthouse: rebuild + apply; in-container smoke for each
      adapter; spawn coding_agent + invoke `[USE_SKILL:ls_dir:...]`;
      verify Compliance pane surfaces a shell_exec OVERSIGHT_SUBMIT.

## Phase 2 (deferred) — real skill implementations
- [ ] `code_generation` — replace LLM round-trip with real tree-sitter
      + template handler.
- [ ] `code_review` — wrap ruff + mypy + pytest --collect-only.
- [ ] `dependency_audit` — wrap pip-audit / npm audit / cargo audit.
- [ ] `test_generation` — LLM call + pytest signature validation.
- [ ] `test_execution` — subprocess pytest in a podman sandbox; cap CPU + mem.
- [ ] `security_scan` — wrap bandit + semgrep + trivy.
- [ ] `report_drafter` — markdown template engine.
- [ ] `citation_tracker` — URL→claim graph in `working_memory`.
- [ ] `plan_outline`, `critic_verdict`, `competitor_profile`,
      `market_sizer`, `research_economist`, `research_strategist` —
      real handlers per role family.

## Phase 3 (deferred) — Coding/DevOps/Data/ML MCP suite
- [ ] `gitlab_api`, `postgres`, `sqlite`, `redis`, `docker_api`,
      `kubectl_api`, `terraform_validate` — manifests from
      Anthropic-official + vendor-official.
- [ ] `python_eval` skill (sandboxed via podman-in-podman).
- [ ] `lint_python` / `lint_js` skills.

## Phase 4 (deferred) — Research/Marketing/Sales MCP suite
- [ ] `slack`, `linear`, `notion`, `google_scholar`, `crunchbase`
      (if a maintained MCP exists).
- [ ] Roles flipped per family.

## Phase 5 (deferred) — HR/Finance/Compliance + ACC-self MCPs
- [ ] `jira` (atlassian-official), `confluence`.
- [ ] ACC-internal: `acc_collective_status`, `acc_role_catalog`,
      `acc_golden_prompt_runner`.
- [ ] Replace `rss_fetch` community MCP with in-repo `feedparser`-based
      adapter for tighter supply-chain audit.
- [ ] Cosign signature verification for community MCPs (alongside
      `.accpkg` work).

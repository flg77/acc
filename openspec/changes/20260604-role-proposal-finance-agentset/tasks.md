# 20260604-role-proposal-finance-agentset — tasks

## Phase 1 (v0.3.51) — substrate

### 1.1 finance_database MCP (ACC-native)
- [ ] Create `mcps/finance_database/server.py` — stdio JSON-RPC.
- [ ] Vendor the JerBouma CSV snapshot under
      `mcps/finance_database/data/` with `provenance.json`.
- [ ] Tools: `search_equities`, `search_etfs`, `search_funds`,
      `get_classification`, `peer_set` (by sector/industry/country).
- [ ] Manifest at `mcps/finance_database/mcp.yaml` — LOW risk,
      transport stdio, no API key required.

### 1.2 fmp MCP manifest (community)
- [ ] `mcps/fmp/mcp.yaml` — stdio command pointing at the
      best-vetted Tier-C community FMP server (per
      `docs/howto-mcp-sources.md`).
- [ ] env: `ACC_FMP_API_KEY` required.
- [ ] Allowed tools restricted to read-shape: quotes, financials,
      ratios, profile, peers. Deny anything write-shape.
- [ ] MEDIUM risk; `requires_actions: [use_external_api]`.

### 1.3 Six FinanceToolkit wrapper skills
- [ ] `skills/compute_ratios/` (LOW) — wraps `ratios/` module
      (liquidity, solvency, profitability, efficiency, valuation).
- [ ] `skills/var_portfolio/` (LOW) — wraps `risk/var_model.py`.
- [ ] `skills/sharpe_ratio/` (LOW) — wraps `performance/` module.
- [ ] `skills/compute_indicators/` (LOW) — wraps `technicals/`
      (RSI, MACD, BB, ATR, momentum, breadth).
- [ ] `skills/bs_price/` (LOW) — wraps `options/black_scholes_model.py`
      and `options/greeks_model.py`.
- [ ] `skills/yield_curve/` (LOW) — wraps `fixedincome/` (Fed +
      Euribor curves).
- [ ] Each skill ships an `UPSTREAM.md` with MIT attribution and
      the upstream commit hash.

### 1.4 Three new roles
- [ ] `roles/market_data_ingester/role.yaml` — domain
      `capital_markets`; allowed_mcps: `fmp`, `finance_database`;
      task_types: `INGEST_QUOTE`, `INGEST_OHLCV`,
      `INGEST_FUNDAMENTALS`.
- [ ] `roles/instrument_discovery/role.yaml` — domain
      `capital_markets`; allowed_mcps: `finance_database`,
      `web_fetch`; task_types: `SEARCH_TICKERS`,
      `BUILD_PEER_SET`, `FILTER_BY_FACTOR`.
- [ ] `roles/news_sentiment_analyst/role.yaml` — domain
      `capital_markets`; allowed_mcps: `web_fetch`, `arxiv`,
      `wikipedia`; task_types: `SCORE_SENTIMENT`,
      `EXTRACT_ENTITIES`, `IMPACT_ASSESS`.

### 1.5 Sample agentset
- [ ] `collective.finance.yaml` at repo root — declares the three
      Phase-1 roles + 1 replica each + cluster_id `cm-1`.

### Tests
- [ ] `tests/test_finance_skills_smoke.py` — each of the 6 skill
      adapters happy-path test against a synthetic input.
- [ ] `tests/test_finance_mcp_manifests.py` — `finance_database` +
      `fmp` parse, risk levels correct, tools restricted as
      declared.
- [ ] `tests/test_finance_roles_yaml.py` — three role.yaml files
      validate against `RoleDefinitionConfig`.

### Verification (Phase 1)
- [ ] Targeted: `pytest tests/test_finance_*.py` — target ~25
      passing.
- [ ] Full sweep: target 2576 + 25 = ~2601 passing.
- [ ] Lighthouse smoke: `./acc-deploy.sh apply collective.finance.yaml`
      (with `ACC_FMP_API_KEY` set in env) → 3 agents up + heartbeating.
- [ ] In-container: `[USE_MCP:finance_database:search_equities {"sector":"Technology","country":"DE"}]`
      from `instrument_discovery` returns a non-empty ticker list.

## Phase 2 (deferred) — investment analysts
- [ ] Roles: `equity_analyst`, `fixed_income_analyst`,
      `technical_analyst`, `options_strategist`.
- [ ] Skills: `dcf_value`, `peer_compare`, `bond_price`,
      `duration_convexity`, `strategy_pnl`, `read_filing`.
- [ ] MCPs: `sec_edgar`.

## Phase 3 (deferred) — portfolio + macro + crypto
- [ ] Roles: `portfolio_manager`, `quant_researcher`,
      `macro_strategist`, `crypto_analyst`.
- [ ] Skills: `optimise_weights`, `backtest_strategy`,
      `monte_carlo`, `attribute_perf`, `rate_curve`.
- [ ] MCPs: `fred`, `oecd`, `coingecko`.
- [ ] Requires capability-pool Phase 3 (`python_eval` sandbox).

## Phase 4 (deferred) — finance-specific governance
- [ ] Roles: `investment_compliance_officer`, `wealth_advisor`.
- [ ] Skills: `check_concentration`, `check_suitability`,
      `audit_trade`.
- [ ] Requires role-perception-profiles Phase 2 (`customer`
      profile).
- [ ] Lift FinAnGPT disclaimer pattern into
      `wealth_advisor.seed_context`.

## Phase 5 (deferred) — audit surface
- [ ] Episode types: `TRADE_DECISION`, `RECOMMENDATION`.
- [ ] Compliance pane finance sub-tab.
- [ ] Backtest reproducibility framework: seed + data snapshot
      hash baked into every quant_researcher run.

## Phase 6 (deferred) — prescriptive optimization (cuOpt)
- [ ] `mcps/cuopt/` — `@acc/mcp-cuopt` manifest (`own_pack`, MEDIUM
      risk) over an external cuOpt REST/NIM endpoint; tools
      `solve_lp`, `solve_milp`. rhoai/GPU-gated in config.
- [ ] Skills: `cvar_optimise` (LP), `cardinality_rebalance` (MILP),
      `liability_match` (LP/MILP), `scenario_optimise` (LP).
- [ ] Turn `optimise_weights` into a router (cuOpt → scipy fallback).
- [ ] Edge→hub delegation path: `[DELEGATE:dc-hub:cuopt …]` over the
      ACC-9 bridge; JetStream-queued when offline.
- [ ] Reproducibility: solver-input hash on every `ALLOCATE`.
- [ ] Tests: LP/MILP spec round-trip (mocked solver), router fallback,
      deploy-mode gating, delegation marker parse.
- [ ] Cross-ref: `20260604-business-roles-domain-split/design-cuopt.md`.

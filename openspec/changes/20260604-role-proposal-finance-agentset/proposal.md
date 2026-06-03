# 20260604-role-proposal-finance-agentset — proposal

## Why

ACC's six finance-shaped roles (`financial_analyst`, `fpa_analyst`,
`procurement_specialist`, `contract_analyst`, `risk_compliance_analyst`,
`revenue_operations_analyst`) are all corporate-FP&A / back-office.
**Zero capital-markets / investment-research presence:** no equity
analyst, no portfolio manager, no quant, no fixed-income analyst,
no options strategist, no technical analyst, no macro strategist,
no wealth advisor, no crypto analyst.

Four Python libraries the operator surfaced
(`C:\Users\micro\Downloads\git\libraries\financial\`) cover ~80% of
the substrate: **FinanceDatabase** (300k+ symbol catalog),
**FinanceToolkit** (150+ ratios + risk + portfolio + technicals +
options + fixed income + economics), and reference designs
(**Financial-News-Sentiment-Analyzer**, **FinAnGPT**). Operator
brainstorm is in the Obsidian vault under
`ACC-Finance-Agentset/Finance Agentset — brainstorm.md`.

This proposal lands the agentset in 5 phases. Phase 1 is the substrate
that unblocks every later analyst role. Phases 2-5 are designed but
deferred.

## What changes

### Phase 1 (this ship — v0.3.51) — substrate

* **3 new roles** under `roles/`:
  * `market_data_ingester` — subscribe to price feeds; the equivalent
    of `ingester` for the investment side. Domain: `capital_markets`.
  * `instrument_discovery` — translate prose ("small-cap cybersecurity
    in DE") to ticker lists via the FinanceDatabase catalog.
  * `news_sentiment_analyst` — fetch + classify financial news;
    pattern lifted from Financial-News-Sentiment-Analyzer.
* **1 new MCP** (ACC-native, in-repo stdio server):
  `finance_database` — wraps JerBouma's CSVs (~100 LOC). LOW risk;
  all 300k tickers available to every role.
* **1 new MCP manifest** pointing at a community wrapper:
  `fmp` (Financial Modeling Prep) — MEDIUM risk; requires
  `ACC_FMP_API_KEY`.
* **6 new skills** wrapping FinanceToolkit sub-modules (~30 LOC each):
  `compute_ratios`, `var_portfolio`, `sharpe_ratio`,
  `compute_indicators`, `bs_price`, `yield_curve`. Each is a thin
  wrapper that vendors the relevant FinanceToolkit module path; we
  don't take a hard dependency on the upstream pip package.
* **1 new sample agentset**: `collective.finance.yaml` so the
  operator can `./acc-deploy.sh apply collective.finance.yaml` to
  bring the three Phase-1 roles online.

### Phase 2 (deferred) — investment analysts
* Roles: `equity_analyst`, `fixed_income_analyst`,
  `technical_analyst`, `options_strategist`.
* Skills: `dcf_value`, `peer_compare`, `bond_price`,
  `duration_convexity`, `greeks`, `iv_surface`, `strategy_pnl`,
  `read_filing` (SEC EDGAR).
* MCPs: `sec_edgar` (community or in-repo via `edgartools`),
  `news_polygon`.

### Phase 3 (deferred) — portfolio + macro + crypto
* Roles: `portfolio_manager`, `quant_researcher`, `macro_strategist`,
  `crypto_analyst`.
* Skills: `optimise_weights`, `backtest_strategy`, `monte_carlo`,
  `attribute_perf`, `rate_curve`.
* MCPs: `fred`, `oecd`, `coingecko`.
* Requires capability-pool Phase 3 (`python_eval` sandbox) before
  `quant_researcher` can backtest safely.

### Phase 4 (deferred) — finance-specific governance
* Roles: `investment_compliance_officer` (distinct from the platform
  `compliance_officer`), `wealth_advisor`.
* Skills: `check_concentration`, `check_suitability`, `audit_trade`.
* Lifts FinAnGPT's "do your own research" disclaimer pattern into
  the wealth_advisor's seed_context.
* Gates on role-perception-profiles Phase 2 (`customer` profile).

### Phase 5 (deferred) — audit surface
* New episode types: `TRADE_DECISION` / `RECOMMENDATION` with the
  analyst's reasoning trace + the data snapshot hash.
* Compliance pane dedicated finance sub-tab (mirrors the multikind
  consolidation work).

## Impact

* **Affected code (Phase 1):**
  * `mcps/finance_database/` — NEW: stdio server (Python, ~150 LOC
    including `mcp` package boilerplate).
  * `mcps/fmp/` — NEW manifest pointing at community `fmp-mcp-server`.
  * `skills/<id>/` × 6 — NEW skill dirs with vendored FinanceToolkit
    excerpts (license attribution in `skills/<id>/UPSTREAM.md`).
  * `roles/market_data_ingester/role.yaml` — NEW
  * `roles/instrument_discovery/role.yaml` — NEW
  * `roles/news_sentiment_analyst/role.yaml` — NEW
  * `collective.finance.yaml` — NEW sample agentset at repo root.
* **New env knobs:**
  * `ACC_FMP_API_KEY` — FMP API key (free tier available).
  * `ACC_NEWS_API_KEY` — Currents / Polygon news (optional).
* **Tests:** ~25 new across 3 files (skill smoke + role-yaml
  validation + MCP manifest load).
* **Backward compatibility:** purely additive. No existing role
  changes; no existing skill changes; FinanceToolkit excerpts
  vendored under MIT attribution so the agent container doesn't
  grow the pip dependency tree.
* **Data dependencies:** FinanceDatabase ships ~30 MB of CSVs;
  vendor a snapshot into `mcps/finance_database/data/` with a
  `provenance.json` (commit hash + date). Refresh via a manual
  `make refresh-finance-data` target.

## Open questions (for operator before Phase 1 lands)

1. **Read-only ceiling?** Phase 1 is read-only by design; never
   wires to a brokerage. Confirm we want to keep it that way through
   Phase 5 (recommended).
2. **API-key economy.** FMP is the recommended Tier-1 backend
   (FinanceToolkit's native, $14/mo entry, free tier exists). If the
   operator prefers a free-only stack, drop `fmp` and rely on
   `yfinance` (less reliable, no support contract). Recommend FMP.
3. **Backtest sandbox.** Phase 3's `quant_researcher` needs Python
   execution. Two options: (a) ship a separate `acc-agent-quant`
   image with pandas+numpy+scipy preinstalled, or (b) route through
   the `python_eval` sandbox from capability-pool Phase 3.
   Recommend (b).
4. **Investment-compliance jurisdiction.** Phase 4 picks one for
   `investment_compliance_officer`. Recommend SEC (US) because
   EDGAR is the most accessible; MiFID II later.

## What stays open after Phase 1

* Phases 2-5 (designed in `tasks.md`, deferred).
* No live trading — out of scope through Phase 5.
* No real-time tick data — Phase 1 ships end-of-day; live ticks
  need a paid feed (Polygon/Refinitiv) and are deferred.
* No SEC EDGAR access in Phase 1 — Phase 2 adds it for
  `equity_analyst`.
* No backtest reproducibility framework — Phase 3 includes it.

## References

* Brainstorm:
  `C:\Users\micro\Documents\Notes\Notes\Development\AgenticCellCorpus\ACC-Finance-Agentset\Finance Agentset — brainstorm.md`
* Libraries:
  * `C:\Users\micro\Downloads\git\libraries\financial\FinanceDatabase`
  * `C:\Users\micro\Downloads\git\libraries\financial\FinanceToolkit`
  * `C:\Users\micro\Downloads\git\libraries\financial\Financial-News-Sentiment-Analyzer`
  * `C:\Users\micro\Downloads\git\libraries\financial\FinAnGPT`
* Related ACC OpenSpecs:
  * `20260603-capability-pool` (Phase 2 lands real handlers for
    the wrapper-skill pattern this proposal extends)
  * `20260531-role-perception-profiles` (Phase 4 needs the
    `customer` profile renderer for `wealth_advisor`)
  * `20260531-role-proposal-orchestrator-skills-mcp-specialist`
    (orchestrator routes finance prompts to this agentset once it
    lands as a sub-collective)

# 20260604-business-roles-domain-split — tasks

## acc-core (this repo)
- [x] `acc/pkg/install.py`: add `read_manifest(pkg_path)` helper.
- [x] `acc/pkg/fetch.py`: add `fetch_and_install_closure` + `_verify_and_install`; visited/depth guards.
- [x] Wire closure at `collective_cmd.py` (pkg-install, pkg-install-direct) + `assistant_proposal.py` (PROPOSE_INFUSE).
- [x] `tests/pkg/test_fetch_closure.py`; repoint mocks in `test_propose_infuse.py`, `test_collective_cmd_pkg.py`.
- [x] Run pkg test sweep (green).

## acc-ecosystem-spearhead (new private repo)
- [x] `gh repo create flg77/acc-ecosystem-spearhead --private` *(blocked by exfil guard — operator to run; local repo + commit ready)*.
- [x] Restore 25 business role sources from `fbcfdbc^`.
- [x] Vendor `skills/`, `mcps/`, `tools/{build_family_pkg.py,skill_mcp_tiers.yaml,classify_skills_mcps.py}`.
- [x] Author 4 new gap roles (key_account_manager, inside_sales_rep, sales_operations_manager, brand_manager).
- [x] 7 family manifests + `tools/build_umbrella_pkg.py`.
- [x] `sync-sources.sh`, `build-all.sh`, `.gitignore`, `secret/README.md`, README, LICENSE.
- [x] `tests/test_build_families.py` (29 roles, no overlap) — green.
- [x] Build 7 packs (1.0.0) + umbrella (2.0.0); inspect role counts.

## acc-ecosystem (public registry)
- [x] Add 7 domain packs + umbrella `.accpkg` + `.sha256`; keep frozen `business-roles-1.0.2`.
- [x] Regenerate `index.json` (12 entries).
- [x] Refresh README package tables.
- [ ] Sign packs (OIDC keyless) *(open decision — currently sha256-only; see proposal risks)*.
- [ ] Push branch + PR *(deferred to operator — see exfil-guard note)*.

## Docs (acc repo)
- [x] `examples/catalogs.yaml` comment block.
- [x] README registry table + doc-table row.
- [x] `docs/PUBLISHING-FAMILY-PACKS.md` (spearhead build, 7 + umbrella, keep monolith).
- [x] `docs/MIGRATING-FROM-INTREE.md` (7-pack mapping, ^1.0 vs ^2.0).

## Deliverables
- [x] OpenSpec proposal + design + design-cuopt + tasks.
- [x] Vault brainstorm.

## Deferred (follow-up changes)
- [ ] cuOpt MCP wrapper `@acc/mcp-cuopt` (`design-cuopt.md`).
- [ ] RH-Mastery secret role authoring (its own brainstorm).

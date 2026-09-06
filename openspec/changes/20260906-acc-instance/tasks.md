# 20260906-acc-instance — tasks

## Phase 1 — the binding (2026-09-06)
- [x] `acc/instances.py`: `Instance`, `instances_dir` / `list_instances` / `load_instance` /
      `save_instance` / `state_roots`; `create` (DNS-label id, vouched owner, existing posture,
      hub ≠ self, `acc.pkg.stack` collective definition, roots + `overlays/collective.md`);
      `archive` (state stays)
- [x] `cell_env` / `cell_volumes` / `surface_env` / `compose_overlay`;
      `collective.roles_to_compose(extra_env=, extra_volumes=)` via `_apply_cell_extras` (both modes,
      `{aid}` per cell)
- [x] `export_instance` (definition, overlays, posture; no owner, no state; `state_excluded`;
      `signature: null` reserved) / `import_instance` (new id + owner here; posture installed
      when absent, never applied; collective re-validated with the new id)
- [x] agent: overlay dir from `ACC_COLLECTIVE_DIR` before the cwd convention
- [x] `acc-cli instance list|show|create|archive|export|import|synth|env`
- [x] `./acc-deploy.sh instance up|down|synth <id>`
- [x] TUI carries its owner: `acc/tui/actor.py` (`tui_actor`, `tui_attribution`, cached
      principal, anonymous fallback); `approver_id` on decisions, `actor` on board control,
      attribution on every Prompt-pane `TASK_ASSIGN` (`requester_source` stays `tui`)
- [x] tests `tests/test_instances.py`: record + roots + definition; id / owner / posture /
      hub / duplicate / stack-profile refusals; archive keeps state; env + mount per cell
      (plain and worker-pool); plain `roles_to_compose` unchanged; surface env; export
      contents and exclusions (state never in the document); import owned here with the
      posture carried; garbage refused; agent overlay dir from env; TUI actor / attribution /
      anonymous fallback / memory scope unchanged
- [x] docs: CHANGELOG, CAPABILITIES row, MANUAL feature line; profiles spec open question 1
      answered

## Phase 2 — running it (next)
- [ ] lighthouse: create an instance, `instance up`, prompt it through a TUI attached with
      `instance env`, confirm the trace lands under `instances/<id>/trace`, the LanceDB
      rows carry the instance id, the TUI decision carries `system:<user>`; `instance down`
      + `up` keeps the state
- [ ] Web GUI: attach to an instance (sessions / trace roots from `surface_env`; actor is
      already the logged-in user)
- [ ] `instance export` → `import` on a second host end to end
- [ ] hibernate / resume an instance (sub-collectives lifecycle verbs over the instance
      overlay) with state intact

## Phase 3 — distribution
- [ ] sign the export archive (profiles spec §12.3; the pack signing floor is the model)
- [ ] `instance create --from <archive|instance>` (Hermes `clone`)

## Depends on / feeds
- HG-40.1b (`hub` is stored and passed to the cells as `ACC_HUB_COLLECTIVE_ID`; the hub
  memory scope, curator and views are that change's)
- D-014 ceilings: the owner's ceiling rides every TUI prompt from now on (operator →
  CRITICAL, no behaviour change for the operator's own work)

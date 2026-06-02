# 20260602-assistant-blindspots — tasks

## Phase 1 (v0.3.47) — cheap, high-leverage

### 1.1 Marker-form tolerance
- [x] Add `_RE_BACKTICK_MARKER` + `_RE_BARE_LINE_MARKER` to
      `acc/assistant_proposal.py`.
- [x] `_normalize_marker_delimiters` rewrites backtick + bare-line
      markers into the canonical square-bracket form.
- [x] `parse_proposal_markers` calls the normaliser before the strict
      regexes; canonical input is byte-identical (idempotent).
- [x] Tests: `tests/test_assistant_proposal_marker_forms.py`
      (canonical / backtick / bare-line / no-false-positives /
      idempotence). 13 tests.

### 1.2 Catalog rendering — kill the "27 more" cliff
- [x] Add `_DETAILED_ROLE_CAP` env knob (default 40) to
      `acc/perception.py`.
- [x] `_render_control` renders top-40 entries with the full summary
      line, then emits any overflow as a single comma-joined
      name-only line `(also available, ask if relevant): r41, r42, ...`.
- [x] Existing "Running agents" + "Available MCPs" + "Managed sub-
      collectives" + "MUST appear above" sections untouched.
- [x] Tests: `tests/test_perception_render_truncation.py` —
      under/at/over cap, no ellipsis-count line, large-catalog
      bound. 8 tests.

### 1.3 Sub-collective surfacing from disk
- [x] `_merge_discovered_subcollectives` reads
      `ACC_DISCOVER_SUBCOLLECTIVES_ROOT`, scans `*/collective.yaml`,
      and lifts `collective_id` + `role_definition.domain_id` +
      `role_definition.purpose` into a synthetic
      `SubCollectiveSpec`.
- [x] `load_collective` wires the merge after the hub spec is parsed.
- [x] Hub-declared entries win over discovered ones; sibling
      matching the hub's own `collective_id` is excluded; malformed
      siblings skip silently.
- [x] Tests: `tests/test_subcollective_discovery_from_disk.py` —
      env disabled / missing dir / three siblings / domain lift /
      hub-wins / self-exclusion / malformed-skipped /
      empty-dir-skipped. 7 tests.

### Verification
- [x] Targeted: `pytest tests/test_assistant_proposal_marker_forms.py
      tests/test_perception_render_truncation.py
      tests/test_subcollective_discovery_from_disk.py`
      → 28 passed.
- [ ] Full sweep: `pytest tests/ --ignore=tests/container --no-cov -q`
      → target ≥ 2504 passing (2476 + 28).
- [ ] Lighthouse smoke: rebuild + apply; re-run today's three
      operator turns; verify all three symptoms land:
      1. backtick marker now parsed + role-existence rejected →
         warning in the agent log,
      2. perception block surfaces full catalog tail,
      3. `/etc/acc/sub-collectives/deep-research/collective.yaml` on
         lighthouse → `## Managed sub-collectives: deep-research`
         appears in the Assistant's system prompt.

## Phase 2 (deferred) — mid-task `[ASK_CAPABILITY]`

- [ ] Define marker shape + parser + dispatcher.
- [ ] `cognitive_core` round-trip cap (one ASK per task).
- [ ] Re-render prompt with capability follow-up; second LLM pass.
- [ ] Tests: ASK with new marker / ASK then drop / ASK then accept.

## Phase 3 (deferred) — correction memory under AUTO

- [ ] Operator-correction heuristic ("isn't there", "shouldn't you",
      etc.) — false-positive guard via embedding similarity to the
      assistant's prior turn.
- [ ] `CORRECTION` episode kind on the bus.
- [ ] Reflection consumer + memory_note shape.
- [ ] Tests: correction recorded, perception block reads it,
      false-positive guard rejects unrelated follow-ups.

# Tasks: Operator-supplied context references in a prompt

**Change ID:** 20260817-operator-context-references
**Branch:** `feat/operator-context-references`

---

## Phase 1 — Resolution

- [x] `[1]` Reference grammar and parser
- [~] `[2]` Resolvers: file, folder, diff (URL gated on open question 2)
      *(file / folder / diff done. URL NOT implemented -- open question 2 is exactly
      whether a fetched document belongs inside the trusted-workspace boundary, and
      building it first would answer that question by accident.)*
- [x] `[3]` Trusted-workspace boundary check with an explicit refusal path
- [x] `[4]` Size bounds with a defined behaviour at the limit

## Phase 2 — Delivery

- [x] `[5]` Attach resolved content as operator-attributed data, not instruction
- [x] `[6]` Record references and content (or hashes) in the session record
- [x] `[7]` Token accounting decision (open question 3)
      *(BYTES, not tokens, with a per-reference and a per-prompt ceiling. A token
      count needs a tokenizer per backend and would differ between them for the same
      file; the ceiling exists to stop one reference eating a context window, and
      bytes bound that adequately without a model-specific dependency.)*

## Phase 3 — Surface

- [ ] `[8]` TUI completion for references
      *(NOT implemented. References resolve on send and refusals are reported, but
      there is no path completion while typing.)*
- [x] `[9]` Tests: refusal outside the boundary; instruction-in-file is not obeyed


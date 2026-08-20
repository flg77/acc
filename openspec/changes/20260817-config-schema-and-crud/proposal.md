# Proposal: Configuration schema and programmatic access

**Change ID:** 20260817-config-schema-and-crud
**Date:** 2026-08-17
**Status:** Implemented
**Author:** flg

---

## Problem Statement

ACC's runtime configuration lives in five per-host files — `models.yaml`,
`acc-config.yaml`, `collective.yaml`, `catalogs.yaml` and `.env` — and every one
of them is edited by hand. There is no schema, no typed accessor, no validation
before write, and no way to learn what a new release added.

Two consequences are already visible in the codebase. Tooling that needs to
change configuration safely has had to defend against the file format itself: the
`acc-profiles` role fences its edits between markers and explicitly strips any
pre-existing unmanaged block, because two `role_models:` keys in one file is
valid YAML and the last one silently wins. And an operator upgrading across a
release has no way to discover new options short of reading the changelog.

Hand-editing is also the root cause behind most of the failures listed in the
operator-preflight change: a `set` that understands the schema cannot produce an
unknown `model_id`, and a `check` would have reported the missing key name before
the first task failed.

## Current Behavior

Config files are read ad hoc by the modules that need them —
`acc/config.py` for `acc-config.yaml`, `acc/models.py` for `models.yaml`,
`acc/pkg/catalog.py` for `catalogs.yaml`. There is no single description of what
keys exist, what types they take, or which file owns them.

`acc-deploy.sh setup` scaffolds the five files from their `.example` templates
and nothing else touches them programmatically. All five are gitignored as of
v0.7.0, so there is also no versioned record of a host's configuration.

## Desired Behavior

A declared schema for ACC configuration, and a small command surface over it:

    acc-cli config show [--json]
    acc-cli config get <dotted.key>
    acc-cli config set <dotted.key> <value>
    acc-cli config unset <dotted.key>
    acc-cli config path [<file>]
    acc-cli config check
    acc-cli config migrate

`get`/`set` operate on a merged view but must be able to report **which file** a
key lives in, because only some files are gitignored and only some are
operator-owned. `set` validates against the schema before writing and refuses a
value that would produce an unresolvable reference. `check` reports missing,
unknown and deprecated keys. `migrate` adds newly introduced options with their
defaults, leaving existing values untouched.

Writes must preserve comments and formatting. The files are heavily commented and
hand-maintained; a round-trip that reformats them will not be adopted.

## Success Criteria

- Setting a `role_models` entry to an unknown `model_id` is refused at write
  time with a message naming the role — the failure that currently only appears
  at runtime, if at all.
- `check` on a v0.6.x-era config reports the options v0.7.x added.
- A `set` followed by a `git diff` (on a tracked example file) shows one changed
  line, with comments intact.
- The schema is the single source consumed by preflight, the TUI configuration
  screen, and any future configuration UI.

## Scope

**In scope**

- A schema covering the five configuration surfaces, including which file owns
  each key and whether it is secret-bearing.
- `show/get/set/unset/path/check/migrate` on the CLI.
- Comment-preserving writes.

**Out of scope**

- Editing secret **values**. `.env` may be described by the schema (key names,
  which model needs which) but `set` must not write secret material; that is
  deliberately left to the operator and to a future secret-source integration.
- A configuration GUI — a separate change that depends on this one.
- Runtime reconfiguration of a live collective without restart.

## Implementation options

**A. Pydantic models as the schema.** `acc/config.py` already uses Pydantic for
`ACCConfig`; extending that to the other files gives validation, defaults and
JSON-schema export for free. Comment preservation then needs a separate
round-trip writer (`ruamel.yaml`), because Pydantic serialisation discards them.

**B. A separate schema description (JSON Schema / dataclass registry) with thin
readers.** Decouples validation from the runtime models and makes a merged view
easier, at the cost of maintaining a second description of the same thing.

**C. Per-file schemas with a shared accessor.** Honest about file ownership,
which matters here; less friendly for a merged `get`.

A and C are not exclusive and the combination is the likely answer. The open
question below is which way the merged view leans.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. One merged schema or per-file schemas behind a shared accessor? A merged view
   is friendlier; per-file is truer, and file ownership matters because only some
   files are gitignored.
2. Does `.env` belong in the schema at all? Describing key **names** is valuable
   for validation; including the file risks normalising secret handling through
   this surface.
3. What happens to a key the schema does not know — reject, warn, or preserve?
   Preserving unknown keys is friendlier to forward-compatibility and worse for
   catching typos.

## Decisions taken during implementation

The three open questions above were answered as follows; the reasoning is in
the module docstrings, and each is pinned by a test.

1. **Per-file schemas behind one shared accessor.** Top-level keys are disjoint
   across the four YAML surfaces, so one dotted namespace collides with
   nothing while every key still resolves to exactly one owning file. `get`
   and `path` report that file, because only some are gitignored and only some
   are operator-owned.

2. **`.env` is described but never written.** Key *names* are in the schema —
   that is what turns "backend selected, credential absent" into a reported
   fault. Values are never read into memory (`read("env")` returns presence
   only) and `set` refuses the file outright.

3. **Unknown keys are preserved and reported.** `check` reports them as
   warnings so a typo surfaces; nothing deletes them, so a file written by a
   newer release survives an older binary. `set` still refuses to *create* an
   unknown key.

A fourth decision was forced by the hard requirement on formatting: the writer
**edits lines** rather than round-tripping YAML. A round-trip re-emits the
whole document and can normalise quoting and indentation, which cannot meet
the "one changed line" criterion. Line editing can, and does — the test suite
asserts an add+unset restores the file byte for byte.

## Assumptions

- Comment and formatting preservation is a hard requirement, not a nice-to-have.
- The schema will be consumed by at least three callers, so it must be importable
  and free of CLI-specific concerns.

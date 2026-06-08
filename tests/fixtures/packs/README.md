# tests/fixtures/packs — committed family-pack snapshots

These `.accpkg` files are **test fixtures**, committed so the suite is
hermetic on a fresh clone. After the Stage 2 cutover the 43 movable
roles were removed from this repo's `roles/` tree, so the test suite can
no longer *build* them here — it installs these snapshots instead.

`tests/conftest.py::installed_family_packs` installs them into a
session-scoped `ACC_PACKAGES_ROOT` so the dual-source loaders resolve
the packaged roles the suite depends on (`coding_agent`,
`research_planner`, `data_engineer`, the business roles, …).

## Source of truth

The editable role sources + the canonical family build live in the
private **`flg77/acc-ecosystem-spearhead`** repo; the public packs are
served from **`flg77/acc-ecosystem`**. The business roles there are now
the **seven per-domain packs + umbrella**; the `business-roles` monolith
snapshot here is retained only because the existing tests reference its
role names by the legacy single-pack shape.

## Refreshing

Rebuild in the spearhead and copy the artifacts:

```bash
cd ../acc-ecosystem-spearhead
PYTHONPATH=../agentic-cell-corpus ./build-all.sh
cp dist/acc-{workspace,research,devops}-roles-*.accpkg \
   ../agentic-cell-corpus/tests/fixtures/packs/
# (business: copy the per-domain packs if/when the tests adopt the split shape)
```

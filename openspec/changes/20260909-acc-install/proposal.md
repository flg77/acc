# 20260909-acc-install — proposal

## Why

ACC was a checkout, not an installation. The console scripts were declared in
`pyproject.toml` but nothing put them on a PATH; on the operator's
workstation `acc-cli` and `acc-pkg` were unknown commands everywhere and
`python -m acc.cli` worked only inside the repo because the current directory
contained the package. Every default was relative to the current directory —
`load_config("acc-config.yaml")`, the roles root (`roles` in the CLI,
`/app/roles` in the capability index), `models.yaml` / `collective.yaml` /
`catalogs.yaml`, the skills and mcps trees, `acc-deploy.sh` anchored to its
own directory — and the TUI carried a walk-up heuristic written for exactly
this failure mode. `packaging/` held one file. D-007's trusted workspace was
one host directory chosen per task, with no record of what the operator had
trusted. Proposal 055 (vault) and the backlog section IN-00 set the order:
discovery rule → launcher → trusted directories → first-run tour → package
data → RPM → docs proven on acc1, bb3, saturate3 → the extension and bootc.

## What

**IN-01 — one discovery rule** (`acc/paths.py`). `home()`: `$ACC_HOME`, else
`~/.config/acc` when it holds `acc-config.yaml`, else `/etc/acc`, else the
checkout the current directory is in (`$ACC_REPO_ROOT`, or a walk up to
`acc-deploy.sh` / a `pyproject.toml` beside `acc/`). `share()`: `$ACC_SHARE`,
else a home carrying `roles/`, else `<sys.prefix>/share/acc`, else
`/usr/share/acc`, else the checkout. `state_root()`: `$ACC_STATE`, else the
checkout, else `/var/lib/acc` for a system home, else `~/.local/state/acc`.
`resolve(kind)` returns the path **and its source**; env vars win; legacy
defaults are the last resort. Routed through it: `load_config`'s bare
default, the agent's config path, the CLI roles root, the capability index's
roles / mcps defaults (resolved in the constructor, not at import), the
skills and mcps registries and validator, `models_path()`, the TUI's repo
discovery. `acc-cli doctor --paths` prints the layout.

**IN-02 — the `acc` launcher** (`acc/launcher.py`, console script `acc`).
Bare `acc` probes NATS for two seconds and attaches the TUI, or exits 3 with
`no collective answers on <url>; start it with 'acc stack up'`. `acc stack
…` runs the resolved deploy script under bash (a Windows hint otherwise);
`acc doctor` / `acc setup` delegate to `acc-cli`; `acc paths`; `--version`
on `acc`, `acc-cli`, `acc-pkg`.

## Decisions (operator, 2026-09-09 — IN-00 §4b)

System install as the default, the `acc` system user never root; two RPM
channels (internal Satellite for the spearhead build, COPR from the mirror);
trust "this directory and everything below" with a super-repo warning; proof
order acc1 → bb3 → saturate3; Windows both `uv tool` + the Podman Desktop
extension; naming `acc` / `acc-deploy` confirmed; a guided first-run
onboarding in ACC's own terms (IN-09).

## Not here (later items)

IN-03/04 trusted directories and the workspace; IN-09 the first-run tour;
IN-05 package data and `acc-deploy` reading the layout; IN-06 the RPM;
IN-07 docs and the playbook; IN-08 the extension launcher and bootc.

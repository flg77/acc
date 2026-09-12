# 20260909-acc-install — tasks

## IN-01 — one discovery rule (2026-09-09)
- [x] `acc/paths.py`: `checkout()`, `home()`, `share()`, `state_root()`, `resolve(kind)` with
      source, `path_of`, `report`, `describe`; `KINDS` + `LEGACY_DEFAULTS`
- [x] routed: `config.load_config` (bare default only), `agent` config path, `cli._common.roles_root`,
      `capability_index` roles / mcps defaults (sentinels resolved in `__init__`), `skills.registry`,
      `mcp.registry`, `capability_validator`, `models.models_path`, `tui.path_resolution` (delegates
      to `paths.checkout()`)
- [x] `acc-cli doctor --paths [--json]`
- [x] tests `tests/test_paths.py` (18)

## IN-02 — the `acc` launcher (2026-09-09)
- [x] `acc/launcher.py` + `acc = "acc.launcher:main"`; probe → attach / exit 3 + hint; `stack`,
      `doctor`, `setup`, `paths`, `--version`; `acc-cli --version`, `acc-pkg --version`
- [x] tests `tests/test_launcher.py` (11)
- [ ] lighthouse: `uv tool install` of the wheel on the host, `acc paths` from `/tmp`, `acc` attaches
      to the live collective, `acc stack status`

## IN-03 / IN-04 — trusted directories and the workspace (2026-09-09)
- [x] `acc/workspace_trust.py`: `TrustRecord`, `records/lookup/is_trusted/trust/deny/revoke`,
      `repositories_below` (super-repo warning at 3), the sentinel written on grant
- [x] the launcher: `choose_workspace` (`--no-workspace`, `--workspace <dir>`, the cwd unless root /
      home / ACC's home; a known record decides silently; `[y/N/always/below]` on a terminal, nothing
      without one); `apply_workspace` → `ACC_WORKSPACE_HOST_DIR` + browse root; `acc stack up|start|rebuild`
      exports a trusted cwd
- [x] `acc-cli workspace list|check|trust [--below] [--force]|deny|revoke`
- [x] the Select-Directory dialog opens at the trusted root in local mode
- [x] tests `tests/test_workspace_trust.py` (15)
- [ ] lighthouse: `acc` in a project dir → prompt → `below` → `acc stack up` mounts it → an `fs_read`
      inside a cell sees the files; an untrusted sibling is refused

## IN-09 — the first-run tour (2026-09-09)
- [x] `acc/tui/screens/tour.py`: `tour_wanted()` (on request / once on an installed layout / never in a
      checkout unasked), `mark_done()`, `steps()` (seven, each stating the floor it keeps), `TourScreen`
      (Next / Back / Skip; the Compliance step queues one sample HIGH gate through `app.publish_json`)
- [x] `ACCTUIApp`: `active_collective_id`, `publish_json`, the tour pushed after the start screen
- [x] launcher: `acc tour`; the first `acc` with no configuration runs `acc-cli setup`, then the tour
- [x] tests `tests/test_tour.py` (8)
- [ ] lighthouse: a fresh user config → `acc` runs setup → the tour → the sample gate appears in
      Compliance and is decided

## IN-05 — the data trees leave the checkout (2026-09-09)
- [x] `packaging/build_share.py` (`collect`: the explicit trees + files, never state) run by
      `setup.py`'s `build_py` into `acc/_share`; `pyproject` package-data; `.gitignore`
- [x] `paths.package_share_dir()`; `share()` finds it after a home with `roles/`, before the prefix
- [x] `acc-deploy.sh`: `REPO_ROOT="${ACC_HOME:-$SCRIPT_DIR}"`, `SHARE_ROOT` (`ACC_SHARE`, a home with
      `container/`, else the script's dir, resolved through symlinks), templates and the compose from
      the share, `ACC_HOME_DIR` / `ACC_SHARE_DIR` / `ACC_IMAGE_PREFIX` exported, version from the
      package without git
- [x] compose: every host mount `${ACC_HOME_DIR:-../..}/…` or `${ACC_SHARE_DIR:-../..}/…`, images
      `${ACC_IMAGE_PREFIX:-localhost}/…`; build contexts unchanged
- [x] `configschema.resolve_path(for_write=)` rooted at the host's ACC home (or the user config dir
      on a fresh host), templates looked up in the share; `configstore._write` seeds the live file
      from the template and never writes into it
- [x] `docs/INSTALL.md`; tests `tests/test_install_layout.py` (9)
- [ ] lighthouse: the live checkout rebuilt with the interpolating compose (no behaviour change);
      a wheel installed with `uv tool` on the host, `acc paths` from `/tmp` finds the package share
- [x] IN-05b: the overlay generators (`collective.roles_to_compose`, `instances.cell_volumes`) emit the
      interpolated layout too; state (logs, workspaces, instances, .acc-apply) interpolates
      `ACC_STATE_DIR` (`acc-deploy.sh` exports `ACC_STATE`, else beside the configuration)

## IN-06 — the RPM (2026-09-09, artefacts)
- [x] `packaging/rpm/acc.spec` (vendored venv, symlinked commands, `/usr/share/acc` = the wheel's
      `_share`, `/etc/acc` noreplace configs + `acc.env` 0640 root:acc, `/var/lib/acc` acc:acc,
      `%pre` sub-uid range, `%post` lingering, state never removed), `acc-stack.service` (User=acc,
      NoNewPrivileges, the layout in Environment=), sysusers, tmpfiles, `build.sh`, README (channels:
      Satellite for the spearhead build, COPR from the mirror; proof order acc1 → bb3 → saturate3)
- [x] `ACC_ENV_FILE` in `acc-deploy.sh` and the compose (`env_file` + the mount)
- [x] tests `tests/test_rpm_spec.py` (6): the decisions held in text
- [x] built on lighthouse (RHEL 10, `packaging/rpm/build.sh`) — four findings from the real build:
      a build host may carry decorated macros (`_prefix=/app`, a decorated `%dist`), so the spec pins
      the FHS directories and `build.sh` computes the dist tag; the package is **arch-specific**
      (the vendored venv carries compiled wheels — `rpmbuild` refuses them in `noarch`); the venv
      pulled ~5 GB of CUDA through `sentence-transformers` → CPU torch pinned from PyTorch's index
      before the wheel resolves, and `%install` fails if an `nvidia/` package slips in; the
      `%changelog` date was a bogus weekday
- [x] install on acc1 (2026-09-09, then every release by `release-pipeline.sh`); bb3 is no longer
      an RPM host — it is the RHOAI host and consumes the agent image (operator 2026-09-10);
      saturate3 → IN-07

## IN-07 — the operator's state, docs, playbook, the clean-host proof (2026-09-11)
- [x] **Decision (operator, 2026-09-11): the operator shares the service's state through the `acc`
      group** — one state tree, so a package the operator installs is the one the running stack
      sees. (Rejected: a per-user fallback, which diverges from the service; document-only.)
- [x] packaging: `/var/lib/acc` and its subdirectories `2770 acc:acc` (setgid, group-writable) in
      the spec and tmpfiles; `acc-stack.service` `UMask=0002`
- [x] `acc/paths.py`: `shared_state()`, `adopt_shared_umask()` (clears only the group-write bit,
      only for the system state), `state_hint()` — `acc paths` / `doctor --paths` say "join group
      `acc`" when the shared state is not writable; `acc`, `acc-cli`, `acc-pkg` adopt the umask first
- [x] tests: `tests/test_shared_state.py`, `tests/test_rpm_spec.py` updated
- [x] `docs/INSTALL.md` (the RPM as it is: two packages, the channel + CA, the acc1 mirror, EL9,
      signing, the group, `systemctl`, the upgrade check), MANUAL "Getting started"
- [x] released v0.17.2; EL9 built from the tag in `almalinux:9`, staged on saturate3, the operator
      installed it (upgrading the 0.15.0 single package); verified 2026-09-12 on a fresh login:
      `rpm -q` 0.17.2 = `acc --version`, state `2770 acc:acc`, unit `UMask=0002`, no `acc paths`
      note, and a file an ACC command writes in the state tree is 0664 group `acc`. acc1 shows the
      other side: a user outside the group gets the note
- [x] playbook PB-11 "Install ACC from the RPM on a clean host" + evidence note (vault)
- [ ] `acc-stack` started on saturate3 — the operator's call (a collective on their box)

## IN-08 — extension + bootc
- [ ] Windows and the Podman Desktop extension (the launcher, the discovery rule, the trust record)

# ACC as an RPM (IN-06)

`packaging/rpm/build.sh` builds the wheel (with the data trees under
`acc/_share`, see `packaging/build_share.py`) and then the RPM with
`rpmbuild -ba`; `MOCK_ROOT=<config>` rebuilds the SRPM in mock for another
target (RHEL 9 / 10, Fedora). The wheel is the RPM's `Source0`: the RPM
packages a release artefact, it never re-runs the Python build.

## What it installs

| Path | What | Owner |
|---|---|---|
| `/usr/lib/acc/venv` | the vendored virtualenv (Textual, nats-py, LanceDB, pydantic, FastAPI … are not in EPEL at the pinned versions; this is an internal / COPR package, not a Fedora-review one); **CPU torch**, pinned from PyTorch's CPU index before the wheel resolves its own — the PyPI default drags in ~5 GB of CUDA that a host running the collective in containers never executes, and `%install` fails if any `nvidia/` package slips in; `pip` removed — the package is the only writer | root |
| `/usr/bin/{acc,acc-cli,acc-pkg,acc-tui,acc-webgui,acc-agent,acc-catalog}` | symlinks into the venv | root |
| `/usr/bin/acc-deploy` → `/usr/share/acc/acc-deploy.sh` | the deploy script from the share | root |
| `/usr/share/acc` → the wheel's `acc/_share` | roles, skills, mcps, collectives, `container/production`, `regulatory_layer`, the templates | root |
| `/etc/acc/` | the operator's home: `acc-config.yaml`, `models.yaml`, `collective.yaml`, `catalogs.yaml` (from the templates, `%config(noreplace)`), `acc.env` 0640 root:acc (empty of secrets) | root:acc |
| `/var/lib/acc/{packages,instances,workspaces,logs}`, `/var/log/acc` | state | acc:acc |
| `acc-stack.service` | `acc-deploy up --webgui` / `down` as the `acc` user | — |

The `acc` system user (`sysusers`) **never gains root**: no sudoers entry,
no capabilities, `NoNewPrivileges` in the unit, rootless podman under a
sub-uid range added in `%pre`, lingering enabled in `%post` so a session-less
service can run rootless podman. Images come from `ACC_IMAGE_PREFIX`
(`quay.io/flg77/acc_images` in the unit); the compose file interpolates it.

## Channels (operator decision 2026-09-09)

- the **spearhead** build → the internal Satellite, which is **the distribution
  base** for ACC packages;
- **COPR** ← the public **mirror** only (the same withheld-files rule as the mirror).

A spearhead build never reaches a public repository.

```bash
packaging/rpm/publish-satellite.sh dist/rpm/RPMS/x86_64/acc-<version>-<release>.<dist>.x86_64.rpm
```

It copies the file to the Satellite and uploads it with `hammer` **there**, so no
API credentials sit on the build host, and it refuses a snapshot build unless
`ALLOW_SNAPSHOT=1` says so on purpose. Published at:

```
https://sat1.ic3net.internal/pulp/content/ic3net_internal/Library/custom/ACC/acc-spearhead/
```

Clients consume it through a `.repo` file pointing at that URL; the script prints
one.

## Versions in the channel

`packaging/rpm/version.py` maps the project's semantic version to an RPM
`Version` and `Release`, because RPM cannot hold a semantic version directly
(`-` is illegal in both fields, and ordering is per field):

| project version | Version | Release | why |
|---|---|---|---|
| `0.14.4` | `0.14.4` | `1` | the release |
| `0.15.0-rc.1` | `0.15.0` | `0.rc.1` | `0.…` sorts **below** the `1` of the release |
| `0.15.0rc1` | `0.15.0` | `0.rc1` | the PEP 440 spelling, same result |
| `0.14.4` from an untagged or dirty tree | `0.14.4` | `0.<commit stamp>.g<sha>[.dirty]` | a snapshot can never impersonate the release |
| a packaging-only rebuild | unchanged | `RELEASE=2` | same source, new package |

`rpm.labelCompare` is asserted on every one of those orderings in
`tests/test_rpm_semver.py`.

## Building on a RHEL host

```bash
sudo dnf install rpm-build systemd-rpm-macros python3.12 python3.12-pip podman podman-compose
git clone <acc> && cd acc
packaging/rpm/build.sh                       # dist/rpm/RPMS/x86_64/acc-<version>-1.<dist>.x86_64.rpm
rpm -qpl dist/rpm/RPMS/*/acc-*.rpm | head
sudo dnf install dist/rpm/RPMS/*/acc-*.rpm
acc --version && acc paths                   # home /etc/acc, share /usr/share/acc, state /var/lib/acc
sudo -u acc acc-cli doctor --paths
sudo systemctl enable --now acc-stack
```

Proof order (operator): acc1 → bb3 → saturate3. `dnf remove acc` leaves
`/etc/acc` and `/var/lib/acc` in place.

## Known limits of the first package

- RHEL 10 and Fedora ≥ 40 ship 3.12 as `python3` (the default); RHEL 9 needs the `python3.12` module: `PYTHON=python3.12 PYTHON_PKG=python3.12 packaging/rpm/build.sh`.
- The local embedding fallback (`sentence-transformers`) makes `torch` a core
  dependency, so the venv is large even on CPU. The host commands do not run
  it; the cells do. Dropping it from the host package is a dependency-split
  decision, not a packaging one.
- The package is arch-specific (the venv carries compiled wheels: pydantic-core,
  LanceDB, ...); the venv is built on the build host for the target's Python --
  build in mock for a target that differs from the host.
- The unit runs the compose as `acc`; the operator's own TUI still runs as
  the operator (`acc` on the PATH, home `~/.config/acc` unless `ACC_HOME=/etc/acc`).

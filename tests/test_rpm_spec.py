"""`20260909-acc-install` IN-06 -- the RPM artefacts say what the operator decided.

The spec, the unit, the sysusers and tmpfiles files are text; these tests
hold them to the decisions (system user never root, the layout, config
noreplace, the two channels documented) so a later edit cannot drift
silently.  Building the RPM itself happens on a RHEL host (build.sh).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RPM = ROOT / "packaging" / "rpm"


def _read(name: str) -> str:
    return (RPM / name).read_text(encoding="utf-8")


def test_spec_layout_and_commands():
    spec = _read("acc.spec")
    assert "%global acc_venv /usr/lib/acc/venv" in spec and "BuildArch" not in spec  # compiled wheels in the venv
    for cmd in ("acc", "acc-cli", "acc-pkg", "acc-tui", "acc-webgui", "acc-deploy"):
        assert f"%{{_bindir}}/{cmd}\n" in spec, cmd
    assert "ln -s %{_datadir}/acc/acc-deploy.sh %{buildroot}%{_bindir}/acc-deploy" in spec
    assert "site-packages/acc/_share %{buildroot}%{_datadir}/acc" in spec        # one copy of the trees
    assert "Requires:       podman-compose" in spec and "AutoReqProv:    no" in spec
    assert "Source0:        agentic_cell_corpus-%{acc_version}-py3-none-any.whl" in spec


def test_spec_config_is_the_operators_and_secrets_stay_out():
    spec = _read("acc.spec")
    for f in ("acc-config.yaml", "models.yaml", "collective.yaml", "catalogs.yaml"):
        assert f"%config(noreplace) %attr(0644,root,acc) %{{_sysconfdir}}/acc/{f}" in spec, f
    assert "%config(noreplace) %attr(0640,root,acc) %{_sysconfdir}/acc/acc.env" in spec
    assert 'install -m 0640 "$SHARE/.env.example" %{buildroot}%{_sysconfdir}/acc/acc.env' in spec
    assert "/etc/acc and /var/lib/acc are the operator's: never removed" in spec
    assert "rm -rf %{buildroot}%{acc_venv}/bin/pip*" in spec                       # the package is the only writer


def test_the_acc_user_never_gains_root():
    spec = _read("acc.spec"); unit = _read("acc-stack.service"); users = _read("acc.sysusers.conf")
    assert 'u acc - "ACC runtime" /var/lib/acc /sbin/nologin' in users
    assert "sudoers" not in spec.lower().replace("no sudoers", "") and "sudo " not in spec
    assert "usermod --add-subuids" in spec and "enable-linger acc" in spec
    assert "User=acc" in unit and "Group=acc" in unit and "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=full" in unit and "ProtectHome=read-only" in unit
    assert "ExecStart=/usr/bin/acc-deploy up --webgui" in unit and "ExecStop=/usr/bin/acc-deploy down" in unit
    for env in ("ACC_HOME=/etc/acc", "ACC_SHARE=/usr/share/acc", "ACC_STATE=/var/lib/acc", "ACC_ENV_FILE=/etc/acc/acc.env"):
        assert f"Environment={env}" in unit, env
    assert "%dir %attr(0750,acc,acc) %{_sharedstatedir}/acc" in spec


def test_tmpfiles_and_state_dirs_match_the_spec():
    tmp = _read("acc.tmpfiles.conf"); spec = _read("acc.spec")
    for d in ("packages", "instances", "workspaces", "logs"):
        assert f"d /var/lib/acc/{d} 0750 acc acc -" in tmp
        assert f"%{{_sharedstatedir}}/acc/{d}" in spec
    assert "d /var/log/acc 0750 acc acc -" in tmp


def test_build_script_and_readme_carry_the_channels_and_the_proof_order():
    build = _read("build.sh"); readme = _read("README.md")
    assert "pip wheel . --no-deps" in build and "rpmbuild -ba" in build and "MOCK_ROOT" in build
    assert "_share/roles/assistant/role.yaml" in build                              # the wheel must carry the trees
    assert "Satellite" in readme and "COPR" in readme and "mirror" in readme
    assert "never reaches a public repository" in readme
    assert "acc1 → bb3 → saturate3" in readme


def test_the_deploy_script_and_compose_honour_the_env_file():
    deploy = (ROOT / "acc-deploy.sh").read_text(encoding="utf-8")
    compose = (ROOT / "container" / "production" / "podman-compose.yml").read_text(encoding="utf-8")
    assert 'export ACC_ENV_FILE="${ACC_ENV_FILE:-$REPO_ROOT/.env}"' in deploy
    assert 'ENV_FILE="$ACC_ENV_FILE"' in deploy and 'ENV_FILE="$REPO_ROOT/.env"' not in deploy
    assert not re.search(r"path: \.\./\.\./\.env", compose)
    assert "- path: ${ACC_ENV_FILE:-../../.env}" in compose
    assert "${ACC_ENV_FILE:-../../.env}:/app/.env:rw,z" in compose


def test_the_venv_carries_cpu_torch_and_no_cuda():
    """`sentence-transformers` is a core dependency and PyPI's `torch` drags in
    ~5 GB of CUDA that a host running the collective in containers never runs."""
    spec = _read("acc.spec")
    assert "--index-url https://download.pytorch.org/whl/cpu torch" in spec
    cpu = spec.index("download.pytorch.org/whl/cpu")
    wheel = spec.index('"%{SOURCE0}[tui]"')
    assert cpu < wheel, "CPU torch must be installed before the wheel resolves its own"
    assert "site-packages/nvidia" in spec and "the CPU torch pin did not hold" in spec

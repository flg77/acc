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
    assert "Source0:        agentic_cell_corpus-%{acc_wheel_version}-py3-none-any.whl" in spec


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
    assert "%dir %attr(2770,acc,acc) %{_sharedstatedir}/acc" in spec


def test_tmpfiles_and_state_dirs_match_the_spec():
    tmp = _read("acc.tmpfiles.conf"); spec = _read("acc.spec")
    for d in ("packages", "instances", "workspaces", "logs", "sessions", "trace"):
        assert f"d /var/lib/acc/{d} 2770 acc acc -" in tmp
        assert f"%dir %attr(2770,acc,acc) %{{_sharedstatedir}}/acc/{d}" in spec
    assert "d /var/lib/acc 2770 acc acc -" in tmp
    assert "d /var/log/acc 0750 acc acc -" in tmp


def test_the_state_is_shared_with_the_operator_through_the_acc_group():
    """IN-07 (operator, 2026-09-11): one state tree -- a package the operator
    installs is the one the service runs.  Setgid + group-writable directories,
    and a unit whose files stay group-writable."""
    unit = _read("acc-stack.service")
    assert "UMask=0002" in unit
    install = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    assert "usermod -aG acc" in install
    assert "systemctl" in install and "acc-stack" in install


def test_build_script_and_readme_carry_the_channels_and_the_proof_order():
    build = _read("build.sh"); readme = _read("README.md")
    assert "pip wheel . --no-deps" in build and "rpmbuild -ba" in build and "MOCK_ROOT" in build
    assert "_share/roles/assistant/role.yaml" in build                              # the wheel must carry the trees
    assert "Satellite" in readme and "COPR" in readme and "mirror" in readme
    assert "never reaches a public repository" in readme
    # bb3 is the RHOAI host and consumes the agent image, not the RPM (2026-09-10)
    assert "acc1 → saturate3" in readme and "bb3" in readme


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


# ---------------------------------------------------------------------------
# the runtime subpackage, and the image built from it (IN-11)
# ---------------------------------------------------------------------------

def _spec_section(spec: str, header: str) -> str:
    """The FILE LIST of one %files section.

    Comments are stripped: the comment that introduces the NEXT section sits
    inside this one textually, and would otherwise read as a member of it.
    """
    start = spec.index(header) + len(header)
    kept = []
    for line in spec[start:].splitlines():
        stripped = line.strip()
        if stripped.startswith(("%files", "%changelog", "%package")):
            break
        if stripped and not stripped.startswith("#"):
            kept.append(stripped)
    return chr(10).join(kept)


def test_the_runtime_subpackage_carries_what_runs():
    spec = _read("acc.spec")
    assert "%package runtime" in spec and "%description runtime" in spec
    body = _spec_section(spec, "%files runtime")
    for path in ("%{acc_venv}", "%{_bindir}/acc", "%{_datadir}/acc"):
        assert path in body, path


def test_the_runtime_subpackage_carries_nothing_host_shaped():
    """A pod runs under an arbitrary UID: the unit drives podman-compose, the
    `acc` user is not that UID, and a 0750 acc:acc state root is unwritable."""
    body = _spec_section(_read("acc.spec"), "%files runtime")
    for host_only in (
        "%{_unitdir}", "%{_sysusersdir}", "%{_tmpfilesdir}",
        "%{_sharedstatedir}/acc", "%{_localstatedir}/log/acc",
        "%{_sysconfdir}/acc", "acc-deploy",
    ):
        assert host_only not in body, f"{host_only} must not be in acc-runtime"


def test_the_host_package_keeps_the_host_layer_and_requires_the_runtime():
    spec = _read("acc.spec")
    assert "Requires:       %{name}-runtime = %{version}-%{release}" in spec
    body = _spec_section(spec, "\n%files\n")
    for host_only in (
        "%{_unitdir}/acc-stack.service", "%{_sysusersdir}/acc.conf",
        "%{_tmpfilesdir}/acc.conf", "%{_sharedstatedir}/acc",
        "%{_sysconfdir}/acc/acc.env", "%{_bindir}/acc-deploy",
    ):
        assert host_only in body, host_only


def test_the_rpm_agent_image_installs_the_runtime_not_the_host_package():
    """Only the agent image is built from the RPM -- the GUI-headed images each
    carry a hand-picked dependency surface and the RPM's monolith would inflate
    them (IN-11)."""
    path = ROOT / "container" / "production" / "Containerfile.agent-core-rpm"
    text = path.read_text(encoding="utf-8")
    assert 'microdnf install -y --nodocs "acc-runtime-${ACC_VERSION}"' in text
    assert "acc-runtime" in text and 'install -y --nodocs "acc-${ACC_VERSION}"' not in text
    # the version is required: an image taking "whatever is newest" is not a release
    assert 'test -n "${ACC_VERSION}"' in text
    # OpenShift runs it as an arbitrary UID in group 0
    assert "chmod -R g=u /app" in text and "USER 1001" in text
    # config is not baked -- the platform provides it
    assert "/app/acc-config.yaml" not in text


def test_only_the_agent_image_is_built_from_the_rpm():
    """A guard on the decision, not on taste: if another Containerfile starts
    installing the RPM, that decision is being reversed silently."""
    production = (ROOT / "container" / "production")
    from_rpm = [
        p.name for p in production.glob("Containerfile.*")
        # the package, not the image name `acc-runtime-evidence-bridge`
        if "acc-runtime-${ACC_VERSION}" in p.read_text(encoding="utf-8")
    ]
    assert from_rpm == ["Containerfile.agent-core-rpm"], from_rpm


def test_the_image_pipeline_verifies_what_it_built():
    script = (ROOT / "packaging" / "images" / "build-agent-rpm.sh").read_text(encoding="utf-8")
    assert "git archive --format=tar" in script            # the tag, not the tree
    assert "rpm -q --qf '%{VERSION}' acc-runtime" in script  # the package it carries
    assert "acc --version" in script                        # and the code agrees
    assert "--user 12345:0" in script                       # an arbitrary UID
    assert "import acc.agent" in script                     # it actually imports
    assert "podman push" in script and "yours" in script     # the push stays operator-only

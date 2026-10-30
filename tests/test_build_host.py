"""`20261004-public-release-channels` Phase 3 -- the dedicated build host.

The pipelines build on `acc-build`, provisioned by a role written for the lab's
CRUD-role standard, with the signing key moved there.  Held in text: no script
defaults to lighthouse any more, the role is complete and self-consistent
(every verb has a task file, the smoke runs the verification script, teardown
is guarded and refuses to destroy the key), the verification script covers the
chroots the mock matrix builds, and the runbook exists.  Provisioning a host
needs Ansible and the host; that is the operator's run.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ROLE = ROOT / "packaging" / "build-host" / "ansible" / "acc-build-host"
HOWTO = ROOT / "packaging" / "build-host" / "HOWTO.md"
VERIFY = ROLE / "files" / "verify-build-host.sh"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_no_pipeline_defaults_to_lighthouse():
    """D2: lighthouse serves models and builds nothing.  A script that still
    defaulted to it would quietly put the next release back there."""
    for script in sorted((ROOT / "packaging").rglob("*.sh")):
        text = script.read_text(encoding="utf-8")
        assert "BUILD_HOST:-lighthouse" not in text, script
        assert "/git/ml/agentic}" not in text, script
        assert "/home/flg/.acc-rpm-gpg}" not in text, script
    for script in ("rpm/release-pipeline.sh", "images/build-images.sh", "images/build-agent-rpm.sh"):
        text = (ROOT / "packaging" / script).read_text(encoding="utf-8")
        assert 'BUILD_HOST="${BUILD_HOST:-acc-build}"' in text, script
        assert 'BUILD_ROOT="${BUILD_ROOT:-/srv/acc-build}"' in text, script
    signer = (ROOT / "packaging" / "rpm" / "sign-rpms-standalone.sh").read_text(encoding="utf-8")
    assert 'REMOTE_GNUPGHOME="${GNUPGHOME:-/srv/acc-build/.acc-rpm-gpg}"' in signer


def test_the_role_follows_the_galaxy_layout_and_every_verb_has_a_task_file():
    for f in ("defaults/main.yml", "vars/main.yml", "tasks/main.yml", "meta/main.yml", "handlers/main.yml",
              "ansible.cfg", "inventory.yml", "site.yml", "README.md", ".gitignore"):
        assert (ROLE / f).exists(), f
    ops = _yaml(ROLE / "vars" / "main.yml")["_acc_build_host_valid_ops"]
    assert ops == ["status", "provision", "smoke", "teardown"]
    for op in ops:
        assert (ROLE / "tasks" / f"{op}.yml").exists(), op
        assert (ROLE / "playbooks" / f"{op}.yml").exists(), op
    main = (ROLE / "tasks" / "main.yml").read_text(encoding="utf-8")
    assert 'include_tasks: "{{ acc_build_host_operation }}.yml"' in main
    assert _yaml(ROLE / "defaults" / "main.yml")["acc_build_host_operation"] == "status"   # read-only default
    for f in ROLE.rglob("*.yml"):
        _yaml(f)                                                                        # every file parses


def test_the_role_installs_what_the_pipelines_need():
    v = _yaml(ROLE / "vars" / "main.yml")
    for pkg in ("mock", "mock-core-configs", "podman", "rpm-sign", "rpm-build",
                "python3.12", "python3.12-pip", "systemd-rpm-macros", "gcc", "git", "gnupg2"):
        assert pkg in v["_acc_build_host_packages"], pkg
    assert "qemu-user-static-aarch64" in v["_acc_build_host_packages_aarch64"]
    assert "copr-cli" in v["_acc_build_host_packages_copr"]                              # optional: --no-copr
    d = _yaml(ROLE / "defaults" / "main.yml")
    assert d["acc_build_host_user"] == "acc-build" and d["acc_build_host_home"] == "/srv/acc-build"
    assert d["acc_build_host_gnupghome"].endswith("/.acc-rpm-gpg")
    assert d["apply"] is False and d["confirm"] == "" and d["wipe_data"] is False       # the standard guards
    provision = (ROLE / "tasks" / "provision.yml").read_text(encoding="utf-8")
    assert "end_host" in provision and "not (apply | bool)" in provision               # dry run by default
    assert "enable-linger" in provision and "/etc/subuid" in provision                 # rootless podman
    assert "groups: [mock]" in provision
    assert "the key is moved by hand" in provision                                      # never generated here
    for f in ROLE.rglob("*.yml"):                                                       # builtin only, like acc-distribute
        assert "ansible.posix" not in f.read_text(encoding="utf-8"), f


def test_teardown_is_guarded_and_refuses_to_destroy_the_key():
    t = (ROLE / "tasks" / "teardown.yml").read_text(encoding="utf-8")
    assert "confirm | default('') == 'YES'" in t
    assert "list-secret-keys" in t and "wipe_key" in t
    assert "never the key" in t


def test_the_smoke_runs_the_verification_script_as_the_build_user():
    smoke = (ROLE / "tasks" / "smoke.yml").read_text(encoding="utf-8")
    assert "verify-build-host.sh" in smoke
    assert 'become_user: "{{ acc_build_host_user }}"' in smoke
    assert "_acc_build_host_smoke.rc == 0" in smoke
    assert VERIFY.exists()


def test_the_verification_script_covers_the_mock_matrix():
    """The chroots the role provisions and the script checks are the ones
    packaging/rpm/mock-matrix.sh builds."""
    matrix = (ROOT / "packaging" / "rpm" / "mock-matrix.sh").read_text(encoding="utf-8")
    default = re.search(r'^DEFAULT_CHROOTS="([^"]+)"', matrix, re.M).group(1).split()
    d = _yaml(ROLE / "defaults" / "main.yml")
    role_chroots = d["acc_build_host_mock_chroots"] + d["acc_build_host_mock_chroots_aarch64"]
    assert set(default) <= set(role_chroots), set(default) - set(role_chroots)
    script = VERIFY.read_text(encoding="utf-8")
    for c in default:
        assert c in script, c
    for check in ("binfmt_misc/qemu-aarch64", "list-secret-keys", "--fingerprint", "--channel-key-url",
                  "podman rootless", "download.pytorch.org", "/etc/subuid", "linger"):
        assert check in script, check
    assert "--report" in script and "--quick" in script                                # status mode, no network


def test_the_runbook_moves_the_key_and_retires_lighthouse():
    h = HOWTO.read_text(encoding="utf-8")
    assert "export-secret-keys" in h and "| ssh acc-build" in h                         # one pipe, no file
    assert "move, never regenerate" in h.lower() or "moved, not regenerated" in h.lower()
    assert "gpg_key_content" in h                                                       # compared to the channel's key
    assert "shred" in h and "Only after step 4" in h                                     # lighthouse cleaned last
    assert "ansible/vm-builder" in h and "lab-gitops/ansible/acc-build-host/" in h
    readme = (ROOT / "packaging" / "rpm" / "README.md").read_text(encoding="utf-8")
    assert "## The build host — `acc-build`" in readme
    hosts = (ROOT / "deploy" / "host-configs" / "README.md").read_text(encoding="utf-8")
    assert "lighthouse builds nothing" in hosts


def test_the_host_reads_acc_spearhead_with_a_read_only_deploy_key_it_never_prints():
    d = _yaml(ROLE / "defaults" / "main.yml")
    assert d["acc_build_host_deploy_key"].endswith("/.ssh/acc-spearhead-deploy")
    provision = (ROLE / "tasks" / "provision.yml").read_text(encoding="utf-8")
    assert "ssh-keygen" in provision and "creates:" in provision                       # made once
    assert "IdentitiesOnly yes" in provision
    status = (ROLE / "tasks" / "status.yml").read_text(encoding="utf-8")
    assert ".pub" in status and "deploy-key add" in status                              # only the public half
    howto = (ROOT / "packaging" / "build-host" / "HOWTO.md").read_text(encoding="utf-8")
    adds = [line for line in howto.splitlines() if line.startswith("gh repo deploy-key add")]
    assert adds and not any("--allow-write" in line for line in adds)                   # read-only


def test_the_home_outside_home_gets_home_labels_and_podman_its_own_runtime_dir():
    provision = (ROLE / "tasks" / "provision.yml").read_text(encoding="utf-8")
    assert "semanage fcontext -a -e /home" in provision and "restorecon -RF" in provision
    assert "policycoreutils-python-utils" in _yaml(ROLE / "vars" / "main.yml")["_acc_build_host_packages"]
    verify = (ROLE / "files" / "verify-build-host.sh").read_text(encoding="utf-8")
    assert 'export XDG_RUNTIME_DIR="/run/user/$(id -u)"' in verify
    assert "(cd / && podman info" in verify

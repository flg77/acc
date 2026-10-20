"""`20261004-public-release-channels` Phase 2 -- the public COPR project.

The project's settings are declared in packaging/rpm/copr-project.sh, not in
a web form: a rebuilt project comes out the same and a change is a diff.
Held in text and by a dry run: the chroot set is the one the proposal decided
(EL9, EL10, Fedora, x86_64 and aarch64), network is on (the spec's %install
needs it), the package builds the MIRROR through .copr/Makefile, no token is
ever in the file, and the install doc leads with the public channel.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "rpm" / "copr-project.sh"


def _s() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_declaration_is_the_decided_channel():
    s = _s()
    assert 'COPR_OWNER="${COPR_OWNER:-flg77}"' in s and 'COPR_NAME="${COPR_NAME:-acc}"' in s
    for c in ("epel-9-x86_64", "epel-9-aarch64", "epel-10-x86_64", "epel-10-aarch64",
              "fedora-$FEDORA-x86_64", "fedora-$FEDORA-aarch64"):
        assert c in s, c
    assert "rawhide" in s and "left out on purpose" in s
    assert "--enable-net on" in s                                       # %install reaches PyPI
    assert 'MIRROR_URL="${MIRROR_URL:-https://github.com/flg77/acc.git}"' in s
    assert "acc-spearhead" not in s.replace("acc-spearhead/", "")        # the public builder never sees spearhead
    assert "--method make_srpm" in s and "packaging/rpm/acc.spec" in s
    assert "--max-builds" in s                                           # the 2+ GB packages do not pile up
    assert "--webhook-rebuild off" in s                                  # 4.5 is deliberate, later


def test_no_token_lives_in_the_file_and_status_needs_none():
    s = _s()
    assert "token" in s and "~/.config/copr" in s
    assert "api_3" in s and "no token" in s
    assert "copr-cli" not in s.split("status() {")[1].split("}")[0]      # status is the public API only
    import re
    assert not re.search(r"[A-Za-z0-9]{40,}", s)                          # nothing token-shaped


def test_a_dry_run_declares_everything_without_copr_cli(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8"}
    for verb in ("create", "update"):
        r = subprocess.run(["bash", str(SCRIPT), verb, "--dry-run"], env=env, capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "--chroot epel-9-x86_64" in r.stdout and "--chroot fedora-42-aarch64" in r.stdout
        assert "--enable-net on" in r.stdout
        assert "--clone-url https://github.com/flg77/acc.git" in r.stdout
        assert "release-copr.sh" in r.stdout                              # the first build is the routine's
    bad = subprocess.run(["bash", str(SCRIPT), "bogus"], env=env, capture_output=True, text=True, check=False)
    assert bad.returncode == 2


def test_the_install_doc_leads_with_the_public_channel():
    doc = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    rpm = doc.index("## The RPM")
    public = doc.index("dnf copr enable flg77/acc")
    lab = doc.index("rpm.ic3net.internal")
    assert rpm < public < lab                                            # COPR first, the lab second
    assert "aarch64" in doc
    assert "cosign verify-blob" in doc or "SHA256SUMS" in doc              # the Release's artefacts, for a host without a repo
    readme = (ROOT / "packaging" / "rpm" / "README.md").read_text(encoding="utf-8")
    assert "copr-project.sh" in readme

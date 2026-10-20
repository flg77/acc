"""`20261004-public-release-channels` Phase 6 -- the bootable edge image.

The edge node boots an image carrying the released RPM instead of installing
a package; `bootc upgrade` is the lifecycle.  Held in text: the image is
built from the channel at the release's version, carries the edge posture
and the stack unit, keeps secrets out, and the pipeline verifies what it
built.  Building it needs podman and a bootc base; that is the release host's
job.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTAINERFILE = ROOT / "container" / "Containerfile.bootc"
PIPELINE = ROOT / "packaging" / "images" / "build-images.sh"


def _cf() -> str:
    return CONTAINERFILE.read_text(encoding="utf-8")


def test_the_image_installs_the_released_package_from_the_channel():
    cf = _cf()
    assert re.search(r"^FROM \$\{BASE_IMAGE\}", cf, re.M)
    assert "ARG BASE_IMAGE=quay.io/fedora/fedora-bootc:" in cf
    assert "ARG COPR_PROJECT=flg77/acc" in cf                                  # the public channel (D3)
    assert "download.copr.fedorainfracloud.org/results/${COPR_PROJECT}" in cf
    assert "pubkey.gpg" in cf and "gpgcheck=1" in cf                           # COPR's key, checked
    assert 'dnf -y install "acc${ACC_VERSION:+-${ACC_VERSION}}"' in cf         # pinned to the release
    assert "expected ${ACC_VERSION}" in cf                                     # and checked after


def test_the_lab_mirror_is_an_alternative_source_not_the_default():
    cf = _cf()
    assert "ARG ACC_REPO_URL=\n" in cf
    assert 'if [ -n "${ACC_REPO_URL}" ]' in cf
    assert "rpm.ic3net.internal" not in cf.split("ARG BASE_IMAGE")[1]          # only in the header's example


def test_the_image_carries_the_edge_posture_and_the_stack_unit():
    cf = _cf()
    assert "acc-cli config set deploy_mode edge" in cf                        # through the CLI, comments kept
    assert "systemctl enable acc-stack.service" in cf
    assert 'ARG ACC_PROFILE=\n' in cf and 'acc-cli profile apply "${ACC_PROFILE}"' in cf


def test_the_image_carries_no_secrets_and_lints_itself():
    cf = _cf()
    assert "acc.env" not in cf.split("ARG BASE_IMAGE")[1]                      # never written at build
    assert "ANTHROPIC_API_KEY" not in cf and "api_key" not in cf.lower()
    assert cf.rstrip().endswith("RUN bootc container lint")


def test_bound_images_are_opt_in_and_come_from_the_compose():
    cf = _cf()
    assert "ARG ACC_BOUND_IMAGES=0" in cf
    assert "/usr/lib/bootc/bound-images.d" in cf
    assert "/usr/share/acc/container/production/podman-compose.yml" in cf      # the compose the unit runs


def test_the_pipeline_builds_and_verifies_it():
    script = PIPELINE.read_text(encoding="utf-8")
    assert "--with-bootc" in script
    assert '"bootc|container/Containerfile.bootc|RELEASE|bootc"' in script
    assert "rpm '$image' -q --qf '%{VERSION}' acc" in script                   # the image says which acc it carries
    assert "the image carries acc $got, expected $version" in script
    assert "ACC_BOOTC_REPO_URL" in script                                      # the lab mirror, when wanted
    assert "bootc switch $IMAGE_PREFIX:acc-bootc-$VERSION" in script


def test_the_docs_tell_an_edge_node_how_to_switch_and_upgrade():
    edge = (ROOT / "docs" / "howto-edge.md").read_text(encoding="utf-8")
    assert "### Option D" in edge and "bootc switch" in edge and "bootc upgrade" in edge and "bootc rollback" in edge
    assert "/etc/acc/acc.env" in edge                                          # the first-boot step
    install = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    assert "Containerfile.bootc" in install or "bootc" in install
    readme = (ROOT / "packaging" / "images" / "README.md").read_text(encoding="utf-8")
    assert "--with-bootc" in readme

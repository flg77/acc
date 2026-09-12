"""IN-11a -- every component image is a release artefact, built from a tag.

`packaging/rpm/release-pipeline.sh` made the package reproducible: a tag goes
in, a verified artefact comes out, a real host proves it.  The images had
nothing of the sort -- `acc-deploy.sh build` builds the working tree.  These
tests hold the image pipeline to the same rules, in text, so a later edit
cannot quietly drop a component or start pushing on an agent's behalf.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "packaging" / "images" / "build-images.sh"
PRODUCTION = ROOT / "container" / "production"

#: Built by their own pipeline, or not a release artefact at all.
NOT_IN_THIS_PIPELINE = {
    "Containerfile.agent-core-rpm",   # packaging/images/build-agent-rpm.sh
}


def _script() -> str:
    return PIPELINE.read_text(encoding="utf-8")


def _components() -> dict[str, tuple[str, str, str]]:
    """name -> (containerfile, version, verify) from the tables in the script."""
    out: dict[str, tuple[str, str, str]] = {}
    for line in _script().splitlines():
        m = re.match(r'\s*"([a-z0-9-]+)\|(Containerfile\.[a-z0-9.-]+)\|([^|]+)\|(\w+)"', line)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3), m.group(4))
    return out


def test_every_production_containerfile_is_covered_or_excluded():
    """A new component that nothing builds is exactly the gap IN-11a closes."""
    covered = {c[0] for c in _components().values()}
    for path in sorted(PRODUCTION.glob("Containerfile.*")):
        if path.name in NOT_IN_THIS_PIPELINE:
            assert path.name not in covered, f"{path.name} has its own pipeline"
            continue
        assert path.name in covered, f"{path.name} is built by nothing -- add it or exclude it"


def test_the_rpm_image_keeps_its_own_pipeline():
    assert (ROOT / "packaging" / "images" / "build-agent-rpm.sh").is_file()
    assert "build-agent-rpm.sh" in _script()      # named, so the split is visible


def test_it_builds_the_tag_never_the_working_tree():
    script = _script()
    assert 'git rev-parse -q --verify "refs/tags/$TAG"' in script
    assert 'git archive --format=tar "$TAG"' in script
    assert "podman build" in script and "--build-arg ACC_VERSION=" in script


def test_each_image_is_tagged_by_component_and_version():
    assert 'image="$IMAGE_PREFIX:acc-$name-$version"' in _script()


def test_the_code_images_must_report_the_release_version():
    script = _script()
    assert "import acc; print(acc.__version__)" in script
    assert "VERIFY FAILED" in script and 'expected $version' in script
    # every ACC-code component is verified that way, not merely started
    for name, (_cf, _v, verify) in _components().items():
        if name in ("mcp-echo", "mcp-web-fetch", "mcp-web-search-brave",
                    "mcp-web-browser-harness", "nats", "redis"):
            assert verify == "start", name
        else:
            assert verify == "accver", name


def test_the_sidecars_and_infrastructure_keep_their_own_versions():
    """A release must not renumber redis."""
    comps = _components()
    assert comps["nats"][1] == "2.10.22" and comps["redis"][1] == "7.2"
    for name in ("mcp-web-fetch", "mcp-web-search-brave", "mcp-web-browser-harness"):
        assert comps[name][1] == "0.1.0", name
    for name in ("agent-core", "cli", "tui", "webgui", "catalog", "pkg",
                 "runtime-evidence-bridge", "mcp-echo"):
        assert comps[name][1] == "RELEASE", name


def test_a_failed_component_stages_nothing():
    script = _script()
    assert "nothing is staged for push" in script
    assert re.search(r"if \(\( \$\{#failed\[@\]\} \)\); then", script)


def test_the_push_stays_the_operators():
    script = _script()
    assert "podman push" in script
    assert "the operator's step" in script
    # printed, never executed: no unquoted `podman push` call of our own
    assert not re.search(r"^\s*(run )?ssh .*podman push", script, re.M)


def test_the_compose_and_the_pipeline_agree_on_the_image_names():
    """The pipeline's components are what the compose actually runs."""
    compose = (PRODUCTION / "podman-compose.yml").read_text(encoding="utf-8")
    referenced = set(re.findall(r"/acc-([a-z0-9-]+):", compose))
    names = set(_components())
    # the compose says acc-catalog / acc-pkg only in the operator's catalogs;
    # everything it runs must exist in the pipeline
    for ref in referenced:
        assert ref in names or ref in {"catalog", "pkg"}, f"the compose runs acc-{ref}, nothing builds it"


def test_the_agent_image_builds_from_a_clean_clone():
    """IN-11e: `COPY models.yaml` needs a file git does not track, so the image
    built from a developer's tree and failed from `git archive` of a tag."""
    text = (PRODUCTION / "Containerfile.agent-core").read_text(encoding="utf-8")
    assert "COPY models.yaml /app/models.yaml" not in text
    assert "COPY models.yaml.example /app/models.yaml" in text


def test_a_dry_run_does_not_claim_it_verified_anything():
    """A log that says "built+verified" for a run that built nothing is a log
    that lies to whoever reads it next."""
    script = _script()
    assert 'label="built+verified"' in script
    assert '[[ $DRY_RUN == 1 ]] && label="would build' in script


def test_the_packaging_scripts_are_lf_only():
    """A CRLF shell script fails on Linux with a syntax error at some
    unrelated line -- build-images.sh did exactly that on its first real run,
    after being written on this Windows workstation."""
    crlf = bytes((13, 10))
    for script in sorted((ROOT / "packaging").rglob("*.sh")):
        raw = script.read_bytes()
        assert crlf not in raw, f"{script.relative_to(ROOT)} has CRLF line endings"

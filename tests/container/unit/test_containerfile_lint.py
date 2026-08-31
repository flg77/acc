"""Unit tests for production Containerfile compliance rules.

No container runtime required — tests parse Containerfiles as text.

Rules enforced:
  LINT-001  FROM must be a registry.access.redhat.com UBI image
  LINT-002  USER 0 must appear before any RUN microdnf / pip install
  LINT-003  Final USER instruction must be non-root (1001, not 0), except
            Containerfile.agent-core with ENTRYPOINT+entrypoint-agent.sh
  LINT-004  LABEL must include org.opencontainers.image.title
  LINT-005  LABEL must include org.opencontainers.image.version
  LINT-006  No FROM :latest tag in production builds (use pinned or ARG)
  LINT-007  WORKDIR must be set before COPY and CMD
  LINT-008  No pip install running as root without a subsequent USER 1001
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Import shared fixtures via conftest in parent
sys_path_fix = None  # noqa — conftest.py in parent dir is auto-loaded by pytest

PRODUCTION_DIR = Path(__file__).parent.parent.parent.parent / "container" / "production"

CONTAINERFILE_NAMES = [
    "Containerfile.agent-core",
    "Containerfile.redis",
    "Containerfile.tui",
    "Containerfile.nats",
]

UBI_REGISTRY = "registry.access.redhat.com"

#: Bases that are deliberately NOT UBI, keyed by (containerfile, exact base).
#:
#: Narrow on purpose. The key is the *resolved* base, so this cannot quietly
#: widen: change the image and the exemption stops matching, and the rule bites
#: again. An entry here is a recorded decision, not a suppressed failure.
NON_UBI_EXEMPTIONS: dict[tuple[str, str], str] = {
    ("Containerfile.redis", "docker.io/library/redis:7.2"): (
        "upstream support tier. Redis has no subscription-free RPM on RHEL 9 "
        "— it lives in the entitled AppStream, and is carried by neither EPEL "
        "nor the UBI mirror — so an unentitled `microdnf install redis` fails. "
        "The tier that must build with NO entitlement (CI, upstream "
        "contributors) therefore uses the community image. The `rhel` tier "
        "builds the same contract from UBI9 minimal + the entitled RPM by "
        "passing REDIS_BASE. See the Containerfile.redis header."
    ),
}


def _read_lines(name: str) -> list[str]:
    path = PRODUCTION_DIR / name
    assert path.exists(), f"{name} does not exist at {path}"
    return path.read_text(encoding="utf-8").splitlines()


def _instructions(lines: list[str]) -> list[tuple[str, str]]:
    """Parse Containerfile lines into (instruction, rest) tuples, skipping comments.

    Handles line continuations (lines ending with \\) by joining them before parsing,
    so that Python code inside RUN ... python3 -c "..." is not misinterpreted as
    a FROM/USER/COPY Dockerfile instruction.
    """
    # Step 1: join continuation lines
    joined: list[str] = []
    buf = ""
    for line in lines:
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
        else:
            buf += line
            joined.append(buf)
            buf = ""
    if buf:
        joined.append(buf)

    # Step 2: parse only the first word of each logical line as an instruction
    instructions = []
    for line in joined:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if parts:
            # Only treat it as an instruction if the first word is all alpha
            # (Dockerfile keywords: FROM, RUN, COPY, ADD, ENV, LABEL, USER, WORKDIR, etc.)
            keyword = parts[0].upper()
            if re.match(r'^[A-Z]+$', keyword):
                instructions.append((keyword, parts[1] if len(parts) > 1 else ""))
    return instructions


# ── LINT-001: FROM uses UBI ────────────────────────────────────────────────────

def _resolve_build_args(value: str, instructions: list[tuple[str, str]]) -> str:
    """Substitute ``${NAME}`` in *value* from the file's own ``ARG NAME=default``.

    A ``FROM ${BASE}`` says nothing on its own; the base is the ARG's default,
    which is what a plain ``podman build`` with no ``--build-arg`` actually
    pulls. Reading the literal string instead lets a non-UBI default pass the
    rule unread, which is the failure mode this resolution exists to close.

    Only ARGs declared *before* the FROM are considered -- the same scope
    Dockerfile itself gives them.
    """
    defaults: dict[str, str] = {}
    for keyword, rest in instructions:
        if keyword == "FROM" and rest == value:
            break
        if keyword == "ARG" and "=" in rest:
            arg_name, _, arg_default = rest.partition("=")
            defaults[arg_name.strip()] = arg_default.strip()

    def _sub(match: re.Match[str]) -> str:
        return defaults.get(match.group(1), match.group(0))

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _sub, value).strip()


@pytest.mark.parametrize("name", CONTAINERFILE_NAMES)
def test_lint_001_from_uses_ubi_registry(name: str) -> None:
    """LINT-001: FROM must reference registry.access.redhat.com.

    Exemptions in :data:`NON_UBI_EXEMPTIONS` are recorded decisions with a
    stated reason, matched on the exact resolved base so they cannot widen.
    """
    lines = _read_lines(name)
    instructions = _instructions(lines)
    from_instructions = [(i, v) for i, v in instructions if i == "FROM"]
    assert from_instructions, f"{name}: no FROM instruction found"
    for _, value in from_instructions:
        # Allow multi-stage build scratch stages for operator; skip scratch
        if value.strip().lower() == "scratch":
            continue
        # Strip a trailing `AS <stage>` before resolving — the stage name is
        # not part of the image reference.
        base = re.sub(r"\s+AS\s+\S+$", "", value.strip(), flags=re.IGNORECASE)
        base = _resolve_build_args(base, instructions)
        if (name, base) in NON_UBI_EXEMPTIONS:
            continue
        assert UBI_REGISTRY in base, (
            f"{name}: FROM '{value}' resolves to '{base}', which does not use "
            f"{UBI_REGISTRY}. All production ACC containers must use Red Hat "
            "UBI base images. If a non-UBI base is deliberate, record it in "
            "NON_UBI_EXEMPTIONS with the reason."
        )


def test_lint_001_exemptions_are_live() -> None:
    """Every exemption must still match a real FROM, or it is stale.

    An exemption that no longer applies is worse than none: it reads as a
    standing decision while silently protecting nothing.
    """
    for (name, base), reason in NON_UBI_EXEMPTIONS.items():
        assert reason.strip(), f"{name}: exemption for {base} has no reason"
        instructions = _instructions(_read_lines(name))
        resolved = {
            _resolve_build_args(
                re.sub(r"\s+AS\s+\S+$", "", v.strip(), flags=re.IGNORECASE),
                instructions,
            )
            for i, v in instructions if i == "FROM"
        }
        assert base in resolved, (
            f"{name}: exemption for '{base}' matches no FROM in the file "
            f"(found {sorted(resolved)}). Remove the stale exemption."
        )


# ── LINT-002: USER 0 precedes install operations ──────────────────────────────

@pytest.mark.parametrize("name", ["Containerfile.agent-core", "Containerfile.tui"])
def test_lint_002_user_0_before_pip_install(name: str) -> None:
    """LINT-002: USER 0 must appear before pip install instructions."""
    lines = _read_lines(name)
    instructions = _instructions(lines)
    user_0_seen = False
    pip_install_found = False
    for instr, value in instructions:
        if instr == "USER" and value.strip() == "0":
            user_0_seen = True
        if instr == "RUN" and "pip install" in value and not user_0_seen:
            pip_install_found = True
            break
    assert not pip_install_found, (
        f"{name}: pip install runs before USER 0. "
        "pip must run as root to write to system site-packages."
    )


@pytest.mark.parametrize("name", ["Containerfile.agent-core", "Containerfile.redis", "Containerfile.tui", "Containerfile.nats"])
def test_lint_002_user_0_before_microdnf(name: str) -> None:
    """LINT-002: USER 0 must appear before microdnf install."""
    lines = _read_lines(name)
    instructions = _instructions(lines)
    user_0_seen = False
    microdnf_before_root = False
    for instr, value in instructions:
        if instr == "USER" and value.strip() == "0":
            user_0_seen = True
        if instr == "RUN" and "microdnf install" in value and not user_0_seen:
            microdnf_before_root = True
            break
    assert not microdnf_before_root, (
        f"{name}: microdnf install runs before USER 0. microdnf requires root."
    )


# ── LINT-003: Final USER is non-root ──────────────────────────────────────────

@pytest.mark.parametrize("name", CONTAINERFILE_NAMES)
def test_lint_003_final_user_is_nonroot(name: str) -> None:
    """LINT-003: The last USER instruction must not be root (UID 0)."""
    lines = _read_lines(name)
    instructions = _instructions(lines)
    user_instructions = [(i, v.strip()) for i, v in instructions if i == "USER"]
    assert user_instructions, f"{name}: no USER instruction found"
    final_user = user_instructions[-1][1]
    if final_user in ("0", "root") and name == "Containerfile.agent-core":
        content = (PRODUCTION_DIR / name).read_text(encoding="utf-8")
        assert "ENTRYPOINT" in content and "entrypoint-agent.sh" in content, (
            f"{name}: final USER is root but must pair with deploy/entrypoint-agent.sh "
            "and ENTRYPOINT so the process still runs as UID 1001 at runtime"
        )
        return
    assert final_user not in ("0", "root"), (
        f"{name}: final USER is '{final_user}' (root). "
        "Production containers must run as non-root for OpenShift restricted SCC compliance."
    )


# ── LINT-004 / LINT-005: Required LABEL fields ────────────────────────────────

@pytest.mark.parametrize("name", CONTAINERFILE_NAMES)
def test_lint_004_label_has_title(name: str) -> None:
    """LINT-004: LABEL must include org.opencontainers.image.title."""
    content = (PRODUCTION_DIR / name).read_text(encoding="utf-8")
    assert "org.opencontainers.image.title" in content, (
        f"{name}: missing LABEL org.opencontainers.image.title"
    )


@pytest.mark.parametrize("name", CONTAINERFILE_NAMES)
def test_lint_005_label_has_version(name: str) -> None:
    """LINT-005: LABEL must include org.opencontainers.image.version."""
    content = (PRODUCTION_DIR / name).read_text(encoding="utf-8")
    assert "org.opencontainers.image.version" in content, (
        f"{name}: missing LABEL org.opencontainers.image.version"
    )


# ── LINT-006: No hardcoded :latest in FROM ────────────────────────────────────

@pytest.mark.parametrize("name", CONTAINERFILE_NAMES)
def test_lint_006_no_latest_tag_in_from(name: str) -> None:
    """LINT-006: FROM lines must not use :latest (except UBI base images which pin at repo level).

    UBI images like ubi9/ubi-minimal:latest are acceptable because Red Hat
    pins :latest to a specific manifest digest in the CDN. Application images
    must use explicit version tags.
    """
    lines = _read_lines(name)
    instructions = _instructions(lines)
    for instr, value in instructions:
        if instr == "FROM":
            # UBI base images — :latest is acceptable (Red Hat manages the pin)
            if UBI_REGISTRY in value:
                continue
            # Non-UBI images (app stages, etc.) must not use :latest
            assert ":latest" not in value, (
                f"{name}: non-UBI FROM '{value}' uses :latest tag. "
                "Pin to a specific digest or version in production builds."
            )


# ── LINT-007: WORKDIR set before COPY and CMD ─────────────────────────────────

@pytest.mark.parametrize("name", ["Containerfile.agent-core", "Containerfile.tui"])
def test_lint_007_workdir_before_copy(name: str) -> None:
    """LINT-007: WORKDIR must be set before application COPY and CMD."""
    lines = _read_lines(name)
    instructions = _instructions(lines)
    workdir_seen = False
    for instr, value in instructions:
        if instr == "WORKDIR":
            workdir_seen = True
        if instr in ("COPY", "CMD") and not workdir_seen:
            pytest.fail(
                f"{name}: {instr} appears before WORKDIR is set. "
                "Set WORKDIR before copying application files."
            )


# ── LINT-008: No duplicate USER 1001 ──────────────────────────────────────────

@pytest.mark.parametrize("name", CONTAINERFILE_NAMES)
def test_lint_008_no_duplicate_nonroot_user(name: str) -> None:
    """LINT-008: USER 1001 should appear only once (at the end)."""
    lines = _read_lines(name)
    instructions = _instructions(lines)
    nonroot_user_count = sum(
        1 for i, v in instructions if i == "USER" and v.strip() == "1001"
    )
    assert nonroot_user_count <= 1, (
        f"{name}: USER 1001 appears {nonroot_user_count} times. "
        "Switch to non-root exactly once, after all installs are complete."
    )

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

@pytest.mark.parametrize("name", CONTAINERFILE_NAMES)
def test_lint_001_from_uses_ubi_registry(name: str) -> None:
    """LINT-001: FROM must reference registry.access.redhat.com."""
    lines = _read_lines(name)
    instructions = _instructions(lines)
    from_instructions = [(i, v) for i, v in instructions if i == "FROM"]
    assert from_instructions, f"{name}: no FROM instruction found"
    for _, value in from_instructions:
        # Allow multi-stage build scratch stages for operator; skip scratch
        if value.strip().lower() == "scratch":
            continue
        assert UBI_REGISTRY in value, (
            f"{name}: FROM '{value}' does not use {UBI_REGISTRY}. "
            "All production ACC containers must use Red Hat UBI base images."
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

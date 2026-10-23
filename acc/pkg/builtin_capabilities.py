"""Built-in capability manifest — a digest for every image-shipped skill and MCP (061).

Packs carry a per-capability ``sha256`` in their signed manifest (schema v2).
The skills and MCPs that ship in the image and the RPM (``skills/<name>/``,
``mcps/<name>/``, including the ``_base`` defaults that merge into every
manifest) carry theirs here: ``acc/pkg/data/builtin_capabilities.yaml``,
generated from the trees by this module and committed as package data, so a
dev checkout, the image and the RPM all ship the same file.

At release the file is signed with the channel key (detached cosign signature
``builtin_capabilities.yaml.sig`` next to it, an [op] step in the release
flow).  When ``ACC_CAPABILITY_MANIFEST_PUBKEY`` names a cosign public key, the
loader verifies that signature before trusting a digest; without it the file
is trusted as part of the signed image or RPM it came in.

Usage::

    python -m acc.pkg.builtin_capabilities --write   # regenerate after a skill/MCP edit
    python -m acc.pkg.builtin_capabilities --check   # exit 1 on drift
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from acc.pkg.build import tree_digest

logger = logging.getLogger("acc.pkg.builtin_capabilities")

_DATA_PACKAGE = "acc.pkg.data"
DATA_FILE = "builtin_capabilities.yaml"
SIGNATURE_FILE = DATA_FILE + ".sig"
PUBKEY_ENV = "ACC_CAPABILITY_MANIFEST_PUBKEY"

# Directories that are never capabilities.  ``_base`` IS listed: it merges
# into every child manifest, so an unverified ``_base`` would taint them all.
_SKIP_DIRS = frozenset({"TEMPLATE", "__pycache__"})
# The file each kind's directory must hold to count as a capability.
_MARKER = {"skills": "skill.yaml", "mcps": "mcp.yaml"}

_REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class BuiltinCapabilities:
    """The parsed manifest: ``{kind: {name: sha256}}`` plus its trust state."""

    skills: dict[str, str] = field(default_factory=dict)
    mcps: dict[str, str] = field(default_factory=dict)
    # "signed" (signature verified), "unsigned" (no key configured, trusted as
    # shipped), "rejected" (key configured, signature missing or bad),
    # "absent" (no data file).
    trust: str = "absent"
    detail: str = ""

    def expected(self, kind: str, name: str) -> str:
        """The recorded digest for one capability, or "" when not listed or untrusted."""
        if self.trust == "rejected":
            return ""
        table = self.skills if kind == "skill" else self.mcps
        return table.get(name, "")


def generate(skills_root: Path, mcps_root: Path) -> dict:
    """Return the manifest document for the trees under the two roots."""
    def _digests(root: Path, kind: str) -> dict[str, str]:
        out: dict[str, str] = {}
        if not root.is_dir():
            return out
        for d in sorted(root.iterdir(), key=lambda p: p.name):
            if not d.is_dir() or d.name in _SKIP_DIRS:
                continue
            if not (d / _MARKER[kind]).is_file():
                continue
            out[d.name] = tree_digest(d)
        return out

    return {
        "schema_version": 1,
        "algorithm": "acc.pkg.build.tree_digest (sha256)",
        "generated_by": "python -m acc.pkg.builtin_capabilities --write",
        "skills": _digests(Path(skills_root), "skills"),
        "mcps": _digests(Path(mcps_root), "mcps"),
    }


def render(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=999)


def _data_dir() -> Path:
    return Path(str(resources.files(_DATA_PACKAGE)))


def _verify_signature(data_path: Path, pubkey: str) -> tuple[str, str]:
    """``(trust, detail)`` for the detached signature next to *data_path*."""
    sig = data_path.with_name(SIGNATURE_FILE)
    if not sig.is_file():
        return "rejected", f"{PUBKEY_ENV} is set but {sig} does not exist"
    try:
        from acc.pkg.verify import _cosign_bin  # noqa: PLC0415
        proc = subprocess.run(
            [_cosign_bin(), "verify-blob", "--key", pubkey,
             "--insecure-ignore-tlog", "--signature", str(sig), str(data_path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except Exception as exc:  # noqa: BLE001 — a missing cosign is a rejection, not a crash
        return "rejected", f"cannot verify {sig}: {exc}"
    if proc.returncode != 0:
        return "rejected", f"cosign rejected {sig}: {proc.stderr.strip()[:300]}"
    return "signed", f"signature verified with {pubkey}"


@functools.lru_cache(maxsize=4)
def _load_cached(data_path: str, pubkey: str) -> BuiltinCapabilities:
    path = Path(data_path)
    if not path.is_file():
        return BuiltinCapabilities(trust="absent", detail=f"{path} not found")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return BuiltinCapabilities(trust="rejected", detail=f"{path} unreadable: {exc}")
    if pubkey:
        trust, detail = _verify_signature(path, pubkey)
    else:
        trust, detail = "unsigned", f"{PUBKEY_ENV} not set; trusted as shipped"
    if trust == "rejected":
        logger.error("builtin capabilities: %s — built-in digests are not trusted", detail)
    return BuiltinCapabilities(
        skills=dict(doc.get("skills") or {}),
        mcps=dict(doc.get("mcps") or {}),
        trust=trust,
        detail=detail,
    )


def load(data_path: Path | None = None) -> BuiltinCapabilities:
    """Load (once per path and key) the built-in manifest."""
    path = Path(data_path) if data_path is not None else _data_dir() / DATA_FILE
    return _load_cached(str(path), os.environ.get(PUBKEY_ENV, "").strip())


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m acc.pkg.builtin_capabilities")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the data file")
    mode.add_argument("--check", action="store_true", help="exit 1 when the data file is stale")
    ap.add_argument("--skills-root", type=Path, default=_REPO / "skills")
    ap.add_argument("--mcps-root", type=Path, default=_REPO / "mcps")
    ap.add_argument("--out", type=Path, default=_data_dir() / DATA_FILE)
    args = ap.parse_args(argv)

    text = render(generate(args.skills_root, args.mcps_root))
    if args.write:
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
        return 0
    current = args.out.read_text(encoding="utf-8") if args.out.is_file() else ""
    if current != text:
        print(
            f"{args.out} is stale: a skill or MCP changed. Regenerate with "
            "`python -m acc.pkg.builtin_capabilities --write` and commit it.",
            file=sys.stderr,
        )
        return 1
    print(f"{args.out} is current")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))

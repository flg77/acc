"""``acc-pkg`` CLI — Stage 0 slice 8.

Glues the manifest / build / install / verify / catalog modules
together behind an automation-friendly argparse interface.

Contract (per brainstorm Q3 + ``20260603-acc-pkg-pilot`` proposal)
--------

* ``--quiet`` suppresses all non-error stdout.
* ``--json`` emits machine-readable output for ``install``,
  ``verify``, ``inspect``, ``list``.
* Idempotent re-install: same ``(name, version)`` whose content hash
  matches → exit 0, no-op, ``was_already_installed=true``.
* No interactive prompts.
* Deterministic exit codes:

  ===  ============================================
  0    ok
  1    user / arg error (missing file, bad CLI args)
  2    manifest schema failure / Pydantic validation
  3    dependency resolution failure
  4    content-hash / sha256 mismatch
  5    signature missing or rejected
  ===  ============================================

Subcommands
-----------

* ``acc-pkg build <src> -o <out>``
* ``acc-pkg install <pkg.accpkg> [--signature <sig>]
        [--allow-unsigned] [--catalog <catalogs.yaml>]``
* ``acc-pkg verify <pkg.accpkg> --signature <sig>
        (--key <pub> | --issuer <oidc> --subject <regex>)``
* ``acc-pkg inspect <pkg.accpkg>``
* ``acc-pkg list [--available [--name <@scope/name>]]``
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from acc.pkg.build import MANIFEST_NAME, build as build_pkg
from acc.pkg.catalog import RequiredSigner, list_available, resolve
from acc.pkg.install import (
    AlreadyInstalled,
    ContentHashMismatch,
    InstallError,
    MissingDependency,
    UnsafePath,
    install as install_pkg,
)
from acc.pkg.manifest import AccPkgManifest
from acc.pkg.registry import Registry
from acc.pkg.verify import (
    CosignNotInstalled,
    SignatureMissing,
    SignatureRejected,
    VerifyError,
    verify as verify_pkg,
)

logger = logging.getLogger("acc.pkg.cli")

# ---------------------------------------------------------------------------
# Exit codes — single source of truth, exported for tests
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_SCHEMA = 2
EXIT_DEPS = 3
EXIT_HASH_MISMATCH = 4
EXIT_SIGNATURE = 5


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


class _Output:
    """Centralises ``--quiet`` / ``--json`` handling."""

    def __init__(self, quiet: bool, as_json: bool, stream=None) -> None:
        # Resolve sys.stdout at call time, NOT in the default arg
        # (which would bind to the import-time stdout and miss
        # pytest's capsys patching).
        self.quiet = quiet
        self.as_json = as_json
        self.stream = stream if stream is not None else sys.stdout

    def info(self, text: str) -> None:
        if self.quiet:
            return
        print(text, file=self.stream)

    def emit(self, payload: dict | list) -> None:
        """Always emit (even under --quiet) if --json is set, suppressed
        otherwise.
        """
        if self.as_json:
            json.dump(payload, self.stream, indent=2, sort_keys=True, default=str)
            self.stream.write("\n")
        elif not self.quiet:
            # Human-readable rendering for non-JSON, non-quiet mode.
            self._render_human(payload)

    def _render_human(self, payload: Any) -> None:
        if isinstance(payload, list):
            for row in payload:
                self._render_human(row)
            return
        if isinstance(payload, dict):
            for k, v in payload.items():
                print(f"  {k}: {v}", file=self.stream)
            return
        print(str(payload), file=self.stream)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _cmd_build(args: argparse.Namespace, out: _Output) -> int:
    src = Path(args.source).resolve()
    if not src.is_dir():
        print(f"error: source not a directory: {src}", file=sys.stderr)
        return EXIT_USER_ERROR
    output = Path(args.output).resolve()
    try:
        result = build_pkg(src, output)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    except ValidationError as exc:
        print(f"error: manifest invalid:\n{exc}", file=sys.stderr)
        return EXIT_SCHEMA
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR

    out.emit({
        "ok": True,
        "name": result.manifest.name,
        "version": result.manifest.version,
        "content_sha256": result.content_sha256,
        "tarball_sha256": result.tarball_sha256,
        "output": str(result.output_path),
    })
    return EXIT_OK


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def _cmd_install(args: argparse.Namespace, out: _Output) -> int:
    pkg = Path(args.package).resolve()
    if not pkg.is_file():
        print(f"error: package not found: {pkg}", file=sys.stderr)
        return EXIT_USER_ERROR

    # Signature handling — Stage 0 enforces the signing floor unless
    # operator explicitly waives it.  The CLI is the only seam that
    # exposes the override; programmatic ``install()`` doesn't have one.
    sig_path = Path(args.signature) if args.signature else _infer_sig(pkg)
    if not args.allow_unsigned:
        if sig_path is None or not sig_path.is_file():
            print(
                f"error: signature not found (looked at {sig_path}); "
                "supply --signature or pass --allow-unsigned (audit-logged)",
                file=sys.stderr,
            )
            return EXIT_SIGNATURE
        # Verify before install.  Requires a RequiredSigner — Stage 0
        # CLI accepts inline key/issuer/subject flags (no catalog
        # resolution; that's Stage 1).
        if not (args.key or (args.issuer and args.subject)):
            print(
                "error: signature verification requires --key OR "
                "(--issuer + --subject); or pass --allow-unsigned",
                file=sys.stderr,
            )
            return EXIT_USER_ERROR
        signer = RequiredSigner(
            issuer=args.issuer or "pilot-keypair",
            subject_pattern=args.subject or ".*",
            key_path=args.key or "",
        )
        try:
            verify_pkg(pkg, sig_path, signer)
        except SignatureMissing as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SIGNATURE
        except SignatureRejected as exc:
            print(f"error: {exc}\n{exc.cosign_stderr}", file=sys.stderr)
            return EXIT_SIGNATURE
        except (CosignNotInstalled, VerifyError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SIGNATURE
    else:
        # Operator-explicit + audit-logged.  Logged at WARN so it shows
        # up even in default verbosity.
        logger.warning(
            "AUDIT: --allow-unsigned bypass for %s by operator", pkg
        )

    # Install
    try:
        result = install_pkg(pkg)
    except ContentHashMismatch as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_HASH_MISMATCH
    except MissingDependency as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DEPS
    except UnsafePath as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    except AlreadyInstalled as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    except ValidationError as exc:
        print(f"error: manifest invalid:\n{exc}", file=sys.stderr)
        return EXIT_SCHEMA
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR

    out.emit({
        "ok": True,
        "name": result.entry.name,
        "version": result.entry.version,
        "install_path": str(result.install_path),
        "content_sha256": result.entry.content_sha256,
        "was_already_installed": result.was_already_installed,
    })
    return EXIT_OK


def _infer_sig(pkg: Path) -> Path | None:
    """Default signature lookup: ``<pkg>.sig`` next to the package."""
    sig = pkg.parent / (pkg.name + ".sig")
    return sig


# ---------------------------------------------------------------------------
# verify (standalone)
# ---------------------------------------------------------------------------


def _cmd_verify(args: argparse.Namespace, out: _Output) -> int:
    pkg = Path(args.package).resolve()
    sig = Path(args.signature).resolve()
    if not (args.key or (args.issuer and args.subject)):
        print(
            "error: --key OR (--issuer + --subject) required",
            file=sys.stderr,
        )
        return EXIT_USER_ERROR
    signer = RequiredSigner(
        issuer=args.issuer or "pilot-keypair",
        subject_pattern=args.subject or ".*",
        key_path=args.key or "",
    )
    try:
        result = verify_pkg(pkg, sig, signer)
    except SignatureMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SIGNATURE
    except SignatureRejected as exc:
        print(f"error: {exc}\n{exc.cosign_stderr}", file=sys.stderr)
        return EXIT_SIGNATURE
    except (CosignNotInstalled, VerifyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SIGNATURE

    out.emit({
        "ok": True,
        "mode": result.mode,
        "signer_identity": result.signer_identity,
    })
    return EXIT_OK


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def _read_manifest_from_pkg(pkg_path: Path) -> AccPkgManifest:
    with gzip.open(pkg_path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        first = next(iter(tar))
        if first.name != MANIFEST_NAME:
            raise InstallError(
                f"package missing manifest as first entry; got {first.name!r}"
            )
        data = tar.extractfile(first).read()
    return AccPkgManifest.model_validate(yaml.safe_load(data) or {})


def _cmd_inspect(args: argparse.Namespace, out: _Output) -> int:
    pkg = Path(args.package).resolve()
    if not pkg.is_file():
        print(f"error: package not found: {pkg}", file=sys.stderr)
        return EXIT_USER_ERROR
    try:
        manifest = _read_manifest_from_pkg(pkg)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    except ValidationError as exc:
        print(f"error: manifest invalid:\n{exc}", file=sys.stderr)
        return EXIT_SCHEMA

    out.emit(manifest.model_dump(mode="json"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace, out: _Output) -> int:
    if args.available:
        ws = Path(args.workspace).resolve() if args.workspace else None
        try:
            available = list_available(name=args.name, workspace=ws)
        except Exception as exc:  # noqa: BLE001
            print(f"error: catalog read failed: {exc}", file=sys.stderr)
            return EXIT_USER_ERROR
        rows = [
            {
                "catalog": cat.id,
                "tier": cat.tier,
                "name": entry.name,
                "version": entry.version,
                "source": entry.tarball_url or entry.tarball_path,
            }
            for cat, entry in available
        ]
        out.emit(rows)
        return EXIT_OK

    # Local registry
    reg = Registry()
    rows = [
        {
            "name": e.name,
            "version": e.version,
            "install_path": e.install_path,
            "installed_at": e.installed_at,
        }
        for e in reg.list()
    ]
    out.emit(rows)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="acc-pkg",
        description="ACC role-package toolchain (build / verify / install / inspect / list).",
    )
    p.add_argument("--quiet", action="store_true", help="suppress non-error stdout")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="emit machine-readable JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    # build
    b = sub.add_parser("build", help="build a .accpkg from a source tree")
    b.add_argument("source", help="path to the source tree containing accpkg.yaml")
    b.add_argument("-o", "--output", required=True, help="output .accpkg path")

    # install
    i = sub.add_parser("install", help="install a .accpkg")
    i.add_argument("package", help="path to the .accpkg file")
    i.add_argument("--signature", help="path to detached signature (default: <pkg>.sig)")
    i.add_argument("--key", help="cosign public-key PEM path (keypair mode)")
    i.add_argument("--issuer", help="OIDC issuer (keyless mode)")
    i.add_argument("--subject", help="OIDC subject regex (keyless mode)")
    i.add_argument("--allow-unsigned", action="store_true",
                   help="bypass signature verification (operator-explicit, audit-logged)")

    # verify
    v = sub.add_parser("verify", help="verify a .accpkg signature without installing")
    v.add_argument("package", help="path to the .accpkg file")
    v.add_argument("--signature", required=True, help="path to detached signature")
    v.add_argument("--key", help="cosign public-key PEM path (keypair mode)")
    v.add_argument("--issuer", help="OIDC issuer (keyless mode)")
    v.add_argument("--subject", help="OIDC subject regex (keyless mode)")

    # inspect
    s = sub.add_parser("inspect", help="pretty-print a package's manifest")
    s.add_argument("package", help="path to the .accpkg file")

    # list
    l = sub.add_parser("list", help="list installed packages or catalog availability")
    l.add_argument("--available", action="store_true",
                   help="list packages available from configured catalogs")
    l.add_argument("--name", help="filter --available by scoped name")
    l.add_argument("--workspace", help="workspace dir whose .acc/catalogs.yaml to include")

    return p


_HANDLERS = {
    "build": _cmd_build,
    "install": _cmd_install,
    "verify": _cmd_verify,
    "inspect": _cmd_inspect,
    "list": _cmd_list,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Logging — INFO by default, suppressed in --quiet.
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )
    out = _Output(quiet=args.quiet, as_json=args.as_json)
    handler = _HANDLERS[args.cmd]
    try:
        return handler(args, out)
    except KeyboardInterrupt:  # pragma: no cover
        return EXIT_USER_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

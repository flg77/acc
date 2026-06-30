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

from acc.capability_validator import PackageValidationError
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
    EnterpriseContractRejected,
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
EXIT_EC_FAILURE = 6   # Stage 1.2 — Enterprise Contract policy violation


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
        result = build_pkg(src, output, validate=not args.no_validate)
    except PackageValidationError as exc:
        # Proposal 033 WS-A — "verify before packaging".  A malformed
        # capability manifest / inconsistent role config blocks the build
        # so it never ships to a user; --no-validate is the escape hatch.
        print("error: package failed capability validation:", file=sys.stderr)
        for finding in exc.findings:
            print(f"  {finding}", file=sys.stderr)
        return EXIT_SCHEMA
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
        attestations_path = Path(args.attestations) if args.attestations else None
        ec_policy_path = Path(args.ec_policy) if args.ec_policy else None
        try:
            verify_pkg(
                pkg, sig_path, signer,
                attestations_path=attestations_path,
                ec_policy_path=ec_policy_path,
            )
        except SignatureMissing as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SIGNATURE
        except SignatureRejected as exc:
            print(f"error: {exc}\n{exc.cosign_stderr}", file=sys.stderr)
            return EXIT_SIGNATURE
        except EnterpriseContractRejected as exc:
            print(f"error: {exc}", file=sys.stderr)
            for v in exc.violations:
                print(f"  - {v}", file=sys.stderr)
            return EXIT_EC_FAILURE
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
    attestations_path = Path(args.attestations) if args.attestations else None
    ec_policy_path = Path(args.ec_policy) if args.ec_policy else None
    try:
        result = verify_pkg(
            pkg, sig, signer,
            attestations_path=attestations_path,
            ec_policy_path=ec_policy_path,
        )
    except SignatureMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SIGNATURE
    except SignatureRejected as exc:
        print(f"error: {exc}\n{exc.cosign_stderr}", file=sys.stderr)
        return EXIT_SIGNATURE
    except EnterpriseContractRejected as exc:
        print(f"error: {exc}", file=sys.stderr)
        for v in exc.violations:
            print(f"  - {v}", file=sys.stderr)
        return EXIT_EC_FAILURE
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


# ---------------------------------------------------------------------------
# RPM-like query verbs (capability index over installed packages)
# ---------------------------------------------------------------------------


def _cmd_owner(args: argparse.Namespace, out: _Output) -> int:
    """``-qf`` — which installed package provides a role/skill/mcp."""
    from acc.pkg.capability_index import find_owners  # noqa: PLC0415

    owners = find_owners(args.name, kind=args.kind)
    if not owners:
        print(
            f"error: no installed package provides "
            f"{args.kind or 'capability'} {args.name!r}",
            file=sys.stderr,
        )
        return EXIT_USER_ERROR
    out.emit([
        {"name": args.name, "kind": k[:-1], "package": e.name,
         "version": e.version, "install_path": e.install_path}
        for e, k in owners
    ])
    return EXIT_OK


def _cmd_contents(args: argparse.Namespace, out: _Output) -> int:
    """``-ql`` — list roles/skills/mcps an installed package provides."""
    from acc.pkg.capability_index import find_package, package_provides  # noqa: PLC0415

    entry = find_package(args.package)
    if entry is None:
        print(f"error: not installed: {args.package}", file=sys.stderr)
        return EXIT_USER_ERROR
    out.emit({"package": entry.name, "version": entry.version,
              **package_provides(entry)})
    return EXIT_OK


def _cmd_info(args: argparse.Namespace, out: _Output) -> int:
    """``-qi`` — package detail (``@scope/name``) or capability ownership."""
    from acc.pkg.capability_index import (  # noqa: PLC0415
        find_owners, find_package, package_provides,
    )

    name = args.name
    if name.startswith("@") and "/" in name:
        entry = find_package(name)
        if entry is None:
            print(f"error: not installed: {name}", file=sys.stderr)
            return EXIT_USER_ERROR
        out.emit({
            "package": entry.name, "version": entry.version,
            "install_path": entry.install_path,
            "installed_at": entry.installed_at,
            "content_sha256": entry.content_sha256,
            "provides": package_provides(entry),
        })
        return EXIT_OK
    owners = find_owners(name, kind=args.kind)
    if not owners:
        print(f"error: unknown package/capability: {name}", file=sys.stderr)
        return EXIT_USER_ERROR
    out.emit([
        {"name": name, "kind": k[:-1], "package": e.name, "version": e.version}
        for e, k in owners
    ])
    return EXIT_OK


def _cmd_verify_installed(args: argparse.Namespace, out: _Output) -> int:
    """``-V`` — re-check installed content hashes (tamper detection)."""
    from acc.pkg.capability_index import find_package, verify_installed  # noqa: PLC0415

    reg = Registry()
    if args.package:
        entry = find_package(args.package, registry=reg)
        if entry is None:
            print(f"error: not installed: {args.package}", file=sys.stderr)
            return EXIT_USER_ERROR
        entries = [entry]
    else:
        entries = reg.list()
    rows = []
    bad = False
    for e in entries:
        ok, detail = verify_installed(e)
        bad = bad or not ok
        rows.append({"package": e.name, "version": e.version, "ok": ok, "detail": detail})
    out.emit(rows)
    return EXIT_OK if not bad else EXIT_HASH_MISMATCH


def _cmd_uninstall(args: argparse.Namespace, out: _Output) -> int:
    """``-e`` — remove an installed package (refuses if depended on, unless --force)."""
    from acc.pkg.capability_index import find_dependents  # noqa: PLC0415
    from acc.pkg.install import uninstall as uninstall_pkg  # noqa: PLC0415

    deps = find_dependents(args.package)
    if deps and not args.force:
        print(
            f"error: {args.package} is required by "
            f"{', '.join(e.name for e in deps)} — use --force",
            file=sys.stderr,
        )
        return EXIT_DEPS
    entry = uninstall_pkg(args.package, args.pkg_version)
    if entry is None:
        print(f"error: not installed: {args.package}", file=sys.stderr)
        return EXIT_USER_ERROR
    out.emit({"uninstalled": f"{entry.name}@{entry.version}"})
    return EXIT_OK


def _cmd_rdeps(args: argparse.Namespace, out: _Output) -> int:
    """``--whatrequires`` — installed packages that depend_on <pkg>."""
    from acc.pkg.capability_index import find_dependents  # noqa: PLC0415

    out.emit([
        {"package": e.name, "version": e.version}
        for e in find_dependents(args.package)
    ])
    return EXIT_OK


# ---------------------------------------------------------------------------
# Contributor scaffolding (init / new-role / validate)
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace, out: _Output) -> int:
    from acc.pkg.scaffold import init_pack  # noqa: PLC0415

    try:
        d = init_pack(
            args.name, scope=args.scope, kind=args.kind, domain=args.domain,
            version=args.version,
            output=Path(args.output).resolve() if args.output else None,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    out.emit({"created": str(d),
              "name": f"@{args.scope.lstrip('@')}/{args.name.replace('-', '_')}"})
    return EXIT_OK


def _cmd_new_role(args: argparse.Namespace, out: _Output) -> int:
    from acc.pkg.scaffold import add_role  # noqa: PLC0415

    try:
        add_role(Path(args.pack).resolve(), args.role, domain=args.domain)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    out.emit({"added_role": args.role, "pack": args.pack})
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace, out: _Output) -> int:
    from acc.pkg.scaffold import validate_pack  # noqa: PLC0415

    errs = validate_pack(Path(args.pack).resolve())
    out.emit({"ok": not errs, "errors": errs})
    return EXIT_OK if not errs else EXIT_SCHEMA


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
    b.add_argument("--no-validate", action="store_true",
                   help="skip the capability-validation gate (proposal 033 WS-A)")

    # install
    i = sub.add_parser("install", help="install a .accpkg")
    i.add_argument("package", help="path to the .accpkg file")
    i.add_argument("--signature", help="path to detached signature (default: <pkg>.sig)")
    i.add_argument("--key", help="cosign public-key PEM path (keypair mode)")
    i.add_argument("--issuer", help="OIDC issuer (keyless mode)")
    i.add_argument("--subject", help="OIDC subject regex (keyless mode)")
    i.add_argument("--allow-unsigned", action="store_true",
                   help="bypass signature verification (operator-explicit, audit-logged)")
    i.add_argument("--attestations",
                   help="path to attestation bundle YAML (Stage 1.2)")
    i.add_argument("--ec-policy",
                   help="path to Enterprise Contract policy YAML "
                        "(default: /etc/acc/policy/enterprise-contract.yaml)")

    # verify
    v = sub.add_parser("verify", help="verify a .accpkg signature without installing")
    v.add_argument("package", help="path to the .accpkg file")
    v.add_argument("--signature", required=True, help="path to detached signature")
    v.add_argument("--key", help="cosign public-key PEM path (keypair mode)")
    v.add_argument("--issuer", help="OIDC issuer (keyless mode)")
    v.add_argument("--subject", help="OIDC subject regex (keyless mode)")
    v.add_argument("--attestations",
                   help="path to attestation bundle YAML (Stage 1.2)")
    v.add_argument("--ec-policy",
                   help="path to Enterprise Contract policy YAML "
                        "(default: /etc/acc/policy/enterprise-contract.yaml)")

    # inspect
    s = sub.add_parser("inspect", help="pretty-print a package's manifest")
    s.add_argument("package", help="path to the .accpkg file")

    # eval (Stage 1.1 — load + summarise; real LLM run is Stage 1.2)
    e = sub.add_parser("eval", help="load + summarise a package's evals/ tree")
    e.add_argument("package", help="path to an installed package directory")

    # login (Stage 1.3 — surface OIDC token + issuer status)
    sub.add_parser("login", help="report OIDC token + issuer readiness for publish")

    # publish (Stage 1.3 — sign + upload to catalog endpoint)
    pub = sub.add_parser("publish", help="sign (keyless or keypair) + upload a .accpkg to a catalog")
    pub.add_argument("package", help="path to the .accpkg file")
    pub.add_argument("--catalog-url", required=True,
                     help="base URL of the catalog upload endpoint")
    pub.add_argument("--token",
                     help="bearer token for the catalog endpoint (optional)")
    pub.add_argument("--issuer",
                     help="OIDC issuer URL (default: public Sigstore)")
    pub.add_argument("--key",
                     help="cosign private key for keypair signing "
                          "(else keyless; or set COSIGN_PRIVATE_KEY)")

    # list
    l = sub.add_parser("list", help="list installed packages or catalog availability")
    l.add_argument("--available", action="store_true",
                   help="list packages available from configured catalogs")
    l.add_argument("--name", help="filter --available by scoped name")
    l.add_argument("--workspace", help="workspace dir whose .acc/catalogs.yaml to include")

    # owner (rpm -qf): which installed package provides a capability
    o = sub.add_parser("owner", aliases=["qf"],
                       help="which installed package provides a role/skill/mcp")
    o.add_argument("name")
    o.add_argument("--kind", choices=["role", "skill", "mcp"], default=None)

    # contents (rpm -ql): what an installed package provides
    c = sub.add_parser("contents", aliases=["ql"],
                       help="list roles/skills/mcps an installed package provides")
    c.add_argument("package", help="@scope/name")

    # info (rpm -qi): package detail or capability ownership
    inf = sub.add_parser("info", aliases=["qi"],
                         help="package detail (@scope/name) or capability ownership")
    inf.add_argument("name", help="@scope/name OR a role/skill/mcp name")
    inf.add_argument("--kind", choices=["role", "skill", "mcp"], default=None)

    # verify-installed (rpm -V): re-check on-disk content hashes
    vi = sub.add_parser("verify-installed", aliases=["qv"],
                        help="re-check installed content hashes (tamper detection)")
    vi.add_argument("package", nargs="?", default=None,
                    help="@scope/name (default: all installed)")

    # uninstall (rpm -e)
    un = sub.add_parser("uninstall", aliases=["remove"],
                        help="remove an installed package (tree + registry entry)")
    un.add_argument("package", help="@scope/name")
    un.add_argument("--version", dest="pkg_version", default=None)
    un.add_argument("--force", action="store_true",
                    help="remove even if other packages depend on it")

    # rdeps (rpm --whatrequires)
    rd = sub.add_parser("rdeps", help="installed packages that depend_on <pkg>")
    rd.add_argument("package", help="@scope/name")

    # init — scaffold a contributable role pack
    ini = sub.add_parser("init", help="scaffold a new role pack to fill in")
    ini.add_argument("name", help="role/pack base name (snake or kebab case)")
    ini.add_argument("--scope", required=True, help="your scope, e.g. @you")
    ini.add_argument("--kind", choices=["role", "agentset"], default="role")
    ini.add_argument("--domain", default="custom", help="domain_id for the role(s)")
    ini.add_argument("--version", default="0.1.0")
    ini.add_argument("--output", default=None, help="target dir (default ./<name>)")

    # new-role — add a role to an existing pack
    nr = sub.add_parser("new-role", help="add a role to an existing pack source dir")
    nr.add_argument("role")
    nr.add_argument("--pack", default=".", help="pack source dir (default cwd)")
    nr.add_argument("--domain", default="custom")

    # validate — lint a pack source tree before build
    val = sub.add_parser("validate", help="lint a pack source tree before build")
    val.add_argument("pack", nargs="?", default=".", help="pack source dir (default cwd)")

    return p


def _cmd_eval(args: argparse.Namespace, out: _Output) -> int:
    """Stage 1.1 — load + summarise a package's evals/ tree.

    Validates every YAML file under ``evals/`` and prints a per-
    package summary (count of behavioral + safety evals, curated-
    panel size).  Stage 1.2 adds the real-LLM execution path against
    the resolved panel.
    """
    from acc.pkg.evals import load_evals  # noqa: PLC0415

    pkg = Path(args.package).resolve()
    if not pkg.is_dir():
        print(f"error: not an installed package dir: {pkg}", file=sys.stderr)
        return EXIT_USER_ERROR

    try:
        loaded = load_evals(pkg)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SCHEMA

    payload = {
        "package_path": str(pkg),
        "behavior_count": len(loaded.behavior),
        "safety_count": len(loaded.safety),
        "curated_panel_size": (
            len(loaded.curated.additional_models)
            if loaded.curated else 0
        ),
        "include_rhoai_default": (
            loaded.curated.include_rhoai_default if loaded.curated else False
        ),
        "evals": {
            "behavior": [e.name for e in loaded.behavior],
            "safety": [e.name for e in loaded.safety],
        },
    }
    out.emit(payload)
    return EXIT_OK


def _cmd_login(args: argparse.Namespace, out: _Output) -> int:
    """Stage 1.3 — surface OIDC token + issuer status."""
    from acc.pkg.publish import login_hint  # noqa: PLC0415

    out.emit(login_hint())
    return EXIT_OK


def _cmd_publish(args: argparse.Namespace, out: _Output) -> int:
    """Stage 1.3 — sign + upload a .accpkg to a catalog endpoint."""
    from acc.pkg.publish import (  # noqa: PLC0415
        CatalogUploadFailed,
        CosignSignFailed,
        PublishError,
        publish,
    )

    pkg = Path(args.package).resolve()
    if not pkg.is_file():
        print(f"error: package not found: {pkg}", file=sys.stderr)
        return EXIT_USER_ERROR

    try:
        result = publish(
            pkg, args.catalog_url,
            token=args.token,
            oidc_issuer=args.issuer or "https://oauth2.sigstore.dev/auth",
            key_path=args.key,
        )
    except CosignSignFailed as exc:
        print(f"error: {exc}\n{exc.cosign_stderr}", file=sys.stderr)
        return EXIT_SIGNATURE
    except CatalogUploadFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR

    out.emit({
        "ok": True,
        "tarball_url": result.tarball_url,
        "signature_url": result.signature_url,
        "rekor_log_index": result.rekor_log_index,
    })
    return EXIT_OK


_HANDLERS = {
    "build": _cmd_build,
    "install": _cmd_install,
    "verify": _cmd_verify,
    "inspect": _cmd_inspect,
    "list": _cmd_list,
    "owner": _cmd_owner, "qf": _cmd_owner,
    "contents": _cmd_contents, "ql": _cmd_contents,
    "info": _cmd_info, "qi": _cmd_info,
    "verify-installed": _cmd_verify_installed, "qv": _cmd_verify_installed,
    "uninstall": _cmd_uninstall, "remove": _cmd_uninstall,
    "rdeps": _cmd_rdeps,
    "init": _cmd_init,
    "new-role": _cmd_new_role,
    "validate": _cmd_validate,
    "eval": _cmd_eval,
    "login": _cmd_login,
    "publish": _cmd_publish,
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

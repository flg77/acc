"""``acc`` -- the one command an operator starts with.

`20260909-acc-install` IN-02 (proposal 055).  ``acc`` alone attaches the TUI
to the running collective; when nothing answers on the bus it says so in one
line and exits 3 instead of drawing an empty screen.  A few verbs cover the
common path without the other command names::

    acc                       the TUI, attached (acc-tui's flags pass through)
    acc stack up|down|status  the packaged deploy script
    acc doctor [...]          acc-cli doctor
    acc setup [...]           acc-cli setup
    acc paths                 where this host's ACC lives, and why
    acc tour                  the first-run tour, again (IN-09)
    acc --version

The first ``acc`` on a host with no configuration runs the guided setup
(``acc-cli setup``) and then the tour.

Started in a directory, ``acc`` proposes it as the session's workspace and
asks once whether it is trusted (``[y/N/always/below]``; IN-03).  The answer
is recorded in ``~/.config/acc/trust.yaml`` (``acc-cli workspace`` manages
it); a trusted directory rides the session as ``ACC_WORKSPACE_HOST_DIR`` --
the D-007 mount -- and ``acc stack up`` mounts it.  A directory never
trusted is never mounted, never read by an ``fs_*`` skill, never selected.
``--workspace <dir>`` names one explicitly; ``--no-workspace`` asks nothing.

``acc-cli``, ``acc-pkg`` and ``acc-deploy`` stay for everything else.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from acc import __version__, paths

EXIT_NO_COLLECTIVE = 3
_PROBE_TIMEOUT_S = 2.0
_TUI_FLAGS = ("--profile", "--resume", "--list-sessions")
_LAUNCHER_FLAGS = ("--no-probe", "--no-workspace", "--workspace")
WORKSPACE_ENV = "ACC_WORKSPACE_HOST_DIR"


def _nats_url() -> str:
    from acc.cli._common import nats_url  # noqa: PLC0415
    return nats_url()


async def _probe(url: str) -> bool:
    try:
        import nats  # noqa: PLC0415
        nc = await nats.connect(url, connect_timeout=_PROBE_TIMEOUT_S, max_reconnect_attempts=0,
                                allow_reconnect=False)
        await nc.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def probe(url: str) -> bool:
    """Whether a NATS server answers at *url* within two seconds."""
    try:
        return asyncio.run(_probe(url))
    except Exception:  # noqa: BLE001
        return False


def deploy_script() -> Path | None:
    r = paths.resolve("deploy")
    return r.path if r.exists else None


# ---------------------------------------------------------------------------
# the workspace: the directory `acc` was started in (IN-03 / IN-04)
# ---------------------------------------------------------------------------


def _not_a_workspace(directory: Path) -> bool:
    """Directories `acc` never proposes: the filesystem root, the user's home
    itself, and ACC's own home (a checkout or the config dir)."""
    d = directory.resolve()
    if d == Path(d.anchor) or d == Path.home().resolve():
        return True
    found = paths.home()
    return found is not None and d == found[0].resolve()


def ask(prompt: str) -> str:
    """One line from the terminal; "" when there is no terminal to ask."""
    if not sys.stdin.isatty():
        return ""
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


def choose_workspace(args: list[str]) -> tuple[Path | None, str]:
    """The workspace for this session and a one-line reason.

    ``--no-workspace`` -> none.  ``--workspace <dir>`` names one.  Otherwise
    the current directory, unless it is one `acc` never proposes.  A known
    record decides silently; an unknown directory is asked about once
    (``[y/N/always/below]``) when a terminal is there, and left alone when
    it is not.  A ``below`` answer on a directory holding many repositories
    is confirmed a second time.
    """
    from acc import workspace_trust as T  # noqa: PLC0415
    if "--no-workspace" in args:
        return None, "no workspace (--no-workspace)"
    explicit = ""
    for i, a in enumerate(args):
        if a == "--workspace" and i + 1 < len(args):
            explicit = args[i + 1]
        elif a.startswith("--workspace="):
            explicit = a.split("=", 1)[1]
    candidate = Path(explicit).expanduser() if explicit else Path.cwd()
    if not candidate.is_dir():
        return None, f"no workspace ({candidate} is not a directory)"
    candidate = candidate.resolve()
    if not explicit and _not_a_workspace(candidate):
        return None, "no workspace (not proposed for this directory)"
    rec, how = T.lookup(candidate)
    if rec is not None:
        if rec.trusted:
            via = "" if how == "exact" else f", below {rec.path}"
            return candidate, f"workspace {candidate} (trusted {rec.scope}{via}, since {rec.since})"
        return None, f"no workspace ({candidate} was denied on {rec.since}; `acc-cli workspace revoke` to be asked again)"
    answer = ask(
        f"Trust {candidate} as a workspace?  Roles with workspace_access may then read it, and write "
        f"after review.  [y = this session / N / always / below = and everything under it] "
    )
    if answer in ("y", "yes"):
        return candidate, f"workspace {candidate} (this session only)"
    if answer in ("always", "a"):
        T.trust(candidate, scope=T.SCOPE_DIR)
        return candidate, f"workspace {candidate} (trusted, recorded)"
    if answer in ("below", "b"):
        n = T.repositories_below(candidate)
        if n >= T.SUPER_REPO_THRESHOLD:
            again = ask(f"{candidate} holds {n} repositories below it; trusting 'below' trusts them all. Continue? [y/N] ")
            if again not in ("y", "yes"):
                return None, f"no workspace ({candidate} holds {n} repositories; not trusted)"
        T.trust(candidate, scope=T.SCOPE_BELOW)
        return candidate, f"workspace {candidate} (trusted below, recorded)"
    if answer in ("n", "no", "never"):
        T.deny(candidate)
        return None, f"no workspace ({candidate} denied, recorded)"
    return None, "no workspace (not trusted)"


def apply_workspace(chosen: Path | None) -> None:
    """Carry the session's workspace to the TUI and the stack; the sentinel
    the cells check is written when trust was granted (workspace_trust.trust)."""
    if chosen is not None:
        os.environ[WORKSPACE_ENV] = str(chosen)
        os.environ.setdefault("ACC_WORKSPACE_BROWSE_ROOT", str(chosen))


def run_stack(args: list[str]) -> int:
    """``acc stack <acc-deploy args>`` -- the deploy script from the resolved layout."""
    script = deploy_script()
    if script is None:
        print("acc: no acc-deploy.sh in this host's ACC layout (set ACC_DEPLOY_SCRIPT, or run inside a checkout)",
              file=sys.stderr)
        return 2
    bash = shutil.which("bash")
    if bash is None:
        print(f"acc: the stack is driven by {script}, which needs bash; on Windows use Podman Desktop "
              "(the ACC extension) or WSL", file=sys.stderr)
        return 2
    env = dict(os.environ)
    if args and args[0] in ("up", "start", "rebuild") and WORKSPACE_ENV not in env:
        from acc import workspace_trust as T  # noqa: PLC0415
        here = Path.cwd().resolve()
        if not _not_a_workspace(here) and T.is_trusted(here):
            env[WORKSPACE_ENV] = str(here)     # the mount the cells get (D-007)
            print(f"acc: workspace {here} (trusted) will be mounted at /workspace", file=sys.stderr)
    return subprocess.call([bash, str(script), *args], cwd=str(script.parent), env=env)


def run_tui(args: list[str]) -> int:
    from acc.tui.app import main as tui_main  # noqa: PLC0415
    sys.argv = ["acc-tui", *args]
    tui_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print(f"acc {__version__}")
        return 0
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args and args[0] == "paths":
        print(paths.describe())
        return 0
    if args and args[0] == "tour":
        os.environ["ACC_TUI_TOUR"] = "1"
        args = args[1:]
    if args and args[0] == "stack":
        return run_stack(args[1:])
    if args and args[0] in ("doctor", "setup"):
        from acc.cli import main as cli_main  # noqa: PLC0415
        return int(cli_main(args) or 0)
    if paths.home() is None and "--list-sessions" not in args and sys.stdin.isatty():
        # IN-09: the first run on a host with no configuration anywhere --
        # the guided setup first (HG-08; it asks, so it needs a terminal),
        # then the tour once the TUI is up.
        print("acc: no configuration found; running the guided setup first", file=sys.stderr)
        from acc.cli import main as cli_main  # noqa: PLC0415
        rc = int(cli_main(["setup"]) or 0)
        if rc != 0:
            return rc
        os.environ["ACC_FIRST_RUN"] = "1"
    no_probe = "--no-probe" in args
    tui_args: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--workspace":
            skip = True
            continue
        if a.split("=", 1)[0] in _LAUNCHER_FLAGS:
            continue
        tui_args.append(a)
    unknown = [a for a in tui_args if a.startswith("-") and a.split("=", 1)[0] not in _TUI_FLAGS]
    if unknown:
        print(f"acc: unknown option {unknown[0]!r}; see 'acc --help'", file=sys.stderr)
        return 2
    if not no_probe and "--list-sessions" not in tui_args:
        url = _nats_url()
        if not probe(url):
            print(f"acc: no collective answers on {url}; start it with 'acc stack up' "
                  "(or point ACC_NATS_URL at a running one; --no-probe skips this check)",
                  file=sys.stderr)
            return EXIT_NO_COLLECTIVE
    if "--list-sessions" not in tui_args:
        chosen, why = choose_workspace(args)
        print(f"acc: {why}", file=sys.stderr)
        apply_workspace(chosen)
    return run_tui(tui_args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

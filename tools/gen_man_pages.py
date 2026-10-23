#!/usr/bin/env python3
"""Generate the argparse-driven man pages (``acc-cli(1)``, ``acc-pkg(1)``).

``acc-cli`` alone has some forty commands and well over a hundred actions; a
hand-kept list of them would drift with the first new verb.  The prose of each
page (NAME, DESCRIPTION, ENVIRONMENT, FILES, EXAMPLES, SEE ALSO) is written by
hand in ``man/src/<prog>.1.in``; this tool renders the COMMANDS section from
the live parser and puts it where the template says ``.\\" @COMMANDS@``.

The other pages (``acc``, ``acc-tui``, ``acc-agent``, ``acc-webgui``,
``acc-catalog``, ``acc-deploy``) have no subcommand tree and are written by
hand straight into ``man/man1/``.  ``tests/test_man_pages.py`` fails when a
generated page is stale, so a new command lands with its page.

Usage::

    python -m tools.gen_man_pages            # rewrite man/man1/acc-cli.1, acc-pkg.1
    python -m tools.gen_man_pages --check    # exit 1 if either is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parent.parent
SRC = _REPO / "man" / "src"
OUT = _REPO / "man" / "man1"
PLACEHOLDER = '.\\" @COMMANDS@'


def _acc_cli() -> argparse.ArgumentParser:
    from acc.cli import _build_parser  # noqa: PLC0415
    return _build_parser()


def _acc_pkg() -> argparse.ArgumentParser:
    from acc.pkg.cli import _build_parser  # noqa: PLC0415
    return _build_parser()


#: prog -> parser factory.  Only commands with a subcommand tree belong here.
PAGES: dict[str, Callable[[], argparse.ArgumentParser]] = {
    "acc-cli": _acc_cli,
    "acc-pkg": _acc_pkg,
}


def esc(text: str) -> str:
    """Roff-escape running text: backslashes, non-ASCII, a leading control char."""
    out = text.replace("\\", "\\e")
    out = "".join(c if ord(c) < 128 else f"\\[u{ord(c):04X}]" for c in out)
    return "\\&" + out if out[:1] in (".", "'") else out


def _opt(name: str) -> str:
    return "\\fB" + esc(name).replace("-", "\\-") + "\\fR"


def _sub_actions(parser: argparse.ArgumentParser) -> list[argparse._SubParsersAction]:
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]


def _plain_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions
            if not isinstance(a, (argparse._SubParsersAction, argparse._HelpAction))
            and a.help != argparse.SUPPRESS]


def _args(parser: argparse.ArgumentParser, action: argparse.Action) -> str:
    fmt = parser._get_formatter()
    if action.option_strings:
        if action.nargs == 0:
            return ""
        return fmt._format_args(action, fmt._get_default_metavar_for_optional(action))
    return fmt._format_args(action, fmt._get_default_metavar_for_positional(action))


def _synopsis(path: str, parser: argparse.ArgumentParser) -> str:
    parts = [f"\\fB{esc(path)}\\fR"]
    for a in _plain_actions(parser):
        if a.option_strings:
            arg = _args(parser, a)
            item = _opt(a.option_strings[-1]) + (f" \\fI{esc(arg)}\\fR" if arg else "")
            parts.append(item if a.required else f"[{item}]")
        else:
            parts.append(f"\\fI{esc(_args(parser, a))}\\fR")
    if _sub_actions(parser):
        parts.append("\\fIACTION\\fR ...")
    return " ".join(parts)


def _help(parser: argparse.ArgumentParser, action: argparse.Action) -> str:
    if not action.help:
        return ""
    return esc(" ".join(parser._get_formatter()._expand_help(action).split()))


def _options(parser: argparse.ArgumentParser) -> list[str]:
    """A ``.TP`` per documented argument; an undocumented one is in the synopsis only."""
    lines: list[str] = []
    for a in _plain_actions(parser):
        if not a.help:
            continue
        arg = _args(parser, a)
        if a.option_strings:
            tag = ", ".join(_opt(o) for o in a.option_strings) + (f" \\fI{esc(arg)}\\fR" if arg else "")
        else:
            tag = f"\\fI{esc(arg)}\\fR"
        lines += [".TP", tag, _help(parser, a)]
    return lines


def _children(parser: argparse.ArgumentParser) -> list[tuple[str, list[str], str, argparse.ArgumentParser]]:
    """(name, aliases, help, parser) per distinct subcommand, in declaration order."""
    found: list[tuple[str, list[str], str, argparse.ArgumentParser]] = []
    for sub in _sub_actions(parser):
        helps = {c.dest: c.help or "" for c in sub._choices_actions}
        names: dict[int, list[str]] = {}
        parsers: dict[int, argparse.ArgumentParser] = {}
        for name, sp in sub.choices.items():
            names.setdefault(id(sp), []).append(name)
            parsers[id(sp)] = sp
        for key, (first, *aliases) in names.items():
            found.append((first, aliases, helps.get(first, ""), parsers[key]))
    return found


def _entry(path: str, aliases: list[str], help_text: str, parser: argparse.ArgumentParser) -> list[str]:
    lines = [".TP", _synopsis(path, parser)]
    body = esc(" ".join(help_text.split())) if help_text else "\\&"
    if aliases:
        body += " (alias: " + ", ".join(f"\\fB{esc(a)}\\fR" for a in aliases) + ")"
    lines.append(body)
    opts = _options(parser)
    if opts:
        lines += [".RS", *opts, ".RE"]
    for name, al, h, sp in _children(parser):
        lines += _entry(f"{path} {name}", al, h, sp)
    return lines


def render_commands(parser: argparse.ArgumentParser) -> list[str]:
    """One ``.SS`` per top-level command, one ``.TP`` per action below it."""
    lines: list[str] = []
    for name, aliases, help_text, sp in _children(parser):
        path = f"{parser.prog} {name}"
        lines.append(f'.SS "{esc(name)}"')
        if help_text:
            lines.append(esc(" ".join(help_text.split())))
        if aliases:
            lines.append("Alias: " + ", ".join(f"\\fB{esc(a)}\\fR" for a in aliases) + ".")
        if _children(sp):
            opts = _options(sp)
            if opts:
                lines += [".PP", f"Options of \\fB{esc(path)}\\fR itself:", ".RS", *opts, ".RE"]
            for child, al, h, csp in _children(sp):
                lines += _entry(f"{path} {child}", al, h, csp)
        else:
            lines += [".PP", _synopsis(path, sp)]
            opts = _options(sp)
            if opts:
                lines += [".RS", *opts, ".RE"]
    return lines


def render(prog: str) -> str:
    template = (SRC / f"{prog}.1.in").read_text(encoding="utf-8")
    if PLACEHOLDER not in template.splitlines():
        raise SystemExit(f"{SRC / (prog + '.1.in')}: no {PLACEHOLDER!r} line")
    commands = "\n".join(render_commands(PAGES[prog]()))
    head = (f'.\\" Generated by tools/gen_man_pages.py from man/src/{prog}.1.in -- do not edit;\n'
            '.\\" edit the template (prose) or the parser (commands) and regenerate.\n')
    return head + template.replace(PLACEHOLDER, commands)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report stale pages instead of writing them")
    args = ap.parse_args(argv)
    stale = []
    for prog in PAGES:
        text = render(prog)
        target = OUT / f"{prog}.1"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(str(target.relative_to(_REPO)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {target.relative_to(_REPO)}")
    if stale:
        print("stale (run python -m tools.gen_man_pages): " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

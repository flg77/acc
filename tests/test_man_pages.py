"""Every command the packages put on the PATH has a man page, and it is current.

``acc-cli(1)`` and ``acc-pkg(1)`` are generated from their parsers by
``tools/gen_man_pages.py``; a stale page means a command changed without
``python -m tools.gen_man_pages``.  The hand-written pages are held to the
spec: a command the RPM ships without a page fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from tools import gen_man_pages as G

ROOT = Path(__file__).resolve().parent.parent
MAN1 = ROOT / "man" / "man1"
SPEC = (ROOT / "packaging" / "rpm" / "acc.spec").read_text(encoding="utf-8")


def _shipped_commands() -> set[str]:
    return set(re.findall(r"^%\{_bindir\}/(acc[\w-]*)$", SPEC, re.M))


def test_every_shipped_command_has_a_page_with_its_name():
    commands = _shipped_commands()
    assert {"acc", "acc-cli", "acc-pkg", "acc-deploy"} <= commands
    for cmd in sorted(commands):
        page = MAN1 / f"{cmd}.1"
        assert page.is_file(), f"{cmd} ships without man/man1/{cmd}.1"
        text = page.read_text(encoding="utf-8")
        assert f".TH {cmd.upper()} 1 " in text, cmd
        assert re.search(rf"^\.SH NAME\n{re.escape(cmd)} \\- ", text, re.M), cmd


def test_the_spec_installs_every_page_into_the_package_that_ships_its_command():
    assert 'install -m 0644 "$SHARE"/man/man1/*.1 %{buildroot}%{_mandir}/man1/' in SPEC
    runtime, host = SPEC.split("%files runtime", 1)[1].split("\n%files\n", 1)
    for cmd in _shipped_commands():
        owner = runtime if f"%{{_bindir}}/{cmd}\n" in runtime else host
        assert f"%{{_mandir}}/man1/{cmd}.1*\n" in owner, cmd
    assert {p.stem for p in MAN1.glob("*.1")} == _shipped_commands()   # no orphan pages


def test_the_wheel_ships_the_pages():
    import sys
    sys.path.insert(0, str(ROOT / "packaging"))
    from build_share import SHARE_TREES  # noqa: PLC0415
    assert "man/man1" in SHARE_TREES


def test_generated_pages_are_current():
    for prog in G.PAGES:
        assert (MAN1 / f"{prog}.1").read_text(encoding="utf-8") == G.render(prog), (
            f"man/man1/{prog}.1 is stale: run python -m tools.gen_man_pages")


def test_generated_pages_cover_every_action():
    for prog, factory in G.PAGES.items():
        text = (MAN1 / f"{prog}.1").read_text(encoding="utf-8")

        def walk(parser, path):
            for name, _aliases, _help, sp in G._children(parser):
                full = f"{path} {name}"
                top = path == prog
                assert (f'.SS "{name}"' if top else f"\\fB{full}\\fR") in text, full
                walk(sp, full)

        walk(factory(), prog)


def test_escaping_keeps_roff_control_characters_out_of_running_text():
    assert G.esc(".hidden") == "\\&.hidden"
    assert G.esc("a\\b") == "a\\eb"
    assert G.esc("x — y") == "x \\[u2014] y"
    assert G._opt("--dry-run") == "\\fB\\-\\-dry\\-run\\fR"

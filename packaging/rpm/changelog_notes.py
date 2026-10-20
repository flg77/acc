# packaging/rpm/changelog_notes.py -- one version's section of CHANGELOG.md.
#
#   python3 packaging/rpm/changelog_notes.py 0.27.0            # prints the section
#   python3 packaging/rpm/changelog_notes.py 0.27.0 CHANGELOG.md
#
# The GitHub Release's notes are the CHANGELOG section for that version, not
# a second write-up that drifts from it (release-github.sh).  Exit 1 when the
# version has no section: a release without notes is a release nobody can read.
from __future__ import annotations

import re
import sys
from pathlib import Path

_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?P<rest>.*)$", re.M)


def section(text: str, version: str) -> str | None:
    """The body under ``## [version]`` up to the next ``## [`` heading, or None."""
    headings = list(_HEADING.finditer(text))
    for i, m in enumerate(headings):
        if m.group("version") != version:
            continue
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        return text[m.end():end].strip("\n") + "\n"
    return None


def date_of(text: str, version: str) -> str | None:
    """The date on the version's heading (``## [0.26.4] — 2026-10-03``), if any."""
    for m in _HEADING.finditer(text):
        if m.group("version") == version:
            d = re.search(r"\d{4}-\d{2}-\d{2}", m.group("rest"))
            return d.group(0) if d else None
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: changelog_notes.py <version> [CHANGELOG.md]", file=sys.stderr)
        return 2
    version = argv[1].lstrip("v")
    path = Path(argv[2]) if len(argv) > 2 else Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    body = section(path.read_text(encoding="utf-8"), version)
    if body is None:
        print(f"ERROR: {path} has no '## [{version}]' section -- write the release notes first.", file=sys.stderr)
        return 1
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

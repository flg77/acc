"""Build hook: the data trees leave the checkout (IN-05).

Everything else is in ``pyproject.toml``.  This file exists so that
``setuptools``' ``build_py`` copies the roles / skills / mcps / collectives
/ container / regulatory_layer trees and the example configs into
``acc/_share/`` of the *built* package (see ``packaging/build_share.py``);
the source tree stays clean (``acc/_share/`` is ignored by git).
"""

from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "packaging"))
from build_share import collect  # noqa: E402


class build_py(_build_py):  # noqa: N801 - setuptools naming
    def run(self) -> None:
        super().run()
        dest = Path(self.build_lib) / "acc" / "_share"
        copied = collect(HERE, dest)
        self.announce(f"acc: shipped {len(copied)} data tree(s)/file(s) into acc/_share", level=2)


setup(cmdclass={"build_py": build_py})

"""Allow ``python -m acc.cli ...`` invocation.

The pyproject `acc-cli` entry point points at :func:`acc.cli.main`,
but `acc-deploy.sh apply` (PR-B) and other harness scripts invoke the
CLI via ``python -m acc.cli`` so they don't depend on the entry-point
script being on ``$PATH``.  This thin shim exposes that surface.
"""

import sys

from acc.cli import main

if __name__ == "__main__":  # pragma: no cover
    # sys.exit(main()) so command exit codes propagate under `python -m acc.cli`
    # (the console_script entry point already does this) — e.g. the non-zero
    # `sessions verify` governance gate.
    sys.exit(main())

"""Allow ``python -m acc.cli ...`` invocation.

The pyproject `acc-cli` entry point points at :func:`acc.cli.main`,
but `acc-deploy.sh apply` (PR-B) and other harness scripts invoke the
CLI via ``python -m acc.cli`` so they don't depend on the entry-point
script being on ``$PATH``.  This thin shim exposes that surface.
"""

from acc.cli import main

if __name__ == "__main__":  # pragma: no cover
    main()

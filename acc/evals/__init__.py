"""Evaluation attachments for a deployment profile (vault proposal 058, Phase 4).

* :mod:`acc.evals.promptfoo` -- a promptfoo config generated from the golden
  prompts, pointed at ACC's OpenAI-compatible endpoint (``model`` = a role).
* :mod:`acc.evals.garak` -- Garak as a release gate: a pinned scan of the
  model endpoint, compared against the last baseline.  A scanner, not a
  runtime guardrail: it decides whether a build may be promoted, not whether
  one request may run.

Both run as optional containers (``deploy/evals/``); ``evals.*`` in the
configuration switches them, and the ``edge-secure`` profile sets them.
"""

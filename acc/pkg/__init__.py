"""ACC role-package format (`.accpkg`) — Stage 0 of the ecosystem split.

See ``openspec/changes/20260603-acc-pkg-pilot/proposal.md`` for scope
and ``openspec/changes/20260604-role-ecosystem-strategy/ecosystem-implementation.md``
for the umbrella architecture.

This module is the seam between the ACC core and the role ecosystem:
roles, skills, and MCPs travel through here as signed `.accpkg`
bundles, resolved from layered catalogs, and verified against
per-tier signer identities before install.

Stage 0 ships: manifest schema (this submodule), build, install,
verify (real cosign), catalog resolver, registry, CLI. Stage 1 adds
Enterprise Contract policy depth + OIDC keyless publish +
behavioral/safety evals. Stage 2 adds the public hub and family
extractions. Stage 3 adds the bootc bundler.
"""

from __future__ import annotations

__all__ = ["manifest"]

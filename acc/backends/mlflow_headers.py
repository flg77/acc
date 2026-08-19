"""MLflow request-header plugin — inject the RHOAI workspace header.

Red Hat OpenShift AI (RHOAI 3.x) ships a **multi-tenant** MLflow: every tracking
request is routed to a *workspace*, selected by the ``X-MLflow-Workspace``
header. Without it the server returns ``400 "Workspace context is required"``.

The MLflow client has no native env var for arbitrary request headers, so this
:class:`~mlflow.tracking.request_header.abstract_request_header_provider.RequestHeaderProvider`
— auto-discovered by MLflow via the ``mlflow.request_header_provider`` entry
point (declared in ``pyproject.toml``) — stamps the header on every tracking
request when ``MLFLOW_WORKSPACE`` is set. It is a **no-op when the env var is
unset**, so it stays harmless against a plain (non-RHOAI) MLflow server and in
test/CI where no workspace applies.

Pairs with :mod:`acc.backends.mlflow_runs` (``ACC_MLFLOW_TRACKING_URI``); the
bearer token is read natively by MLflow from ``MLFLOW_TRACKING_TOKEN``. Together
these are everything ACC needs to log golden-prompt runs to a RHOAI MLflow from
edge or DC — see ``lab-gitops docs/SOP-mlflow-on-rhoai.md``.
"""

from __future__ import annotations

import os

from mlflow.tracking.request_header.abstract_request_header_provider import (
    RequestHeaderProvider,
)

ENV_WORKSPACE = "MLFLOW_WORKSPACE"
WORKSPACE_HEADER = "X-MLflow-Workspace"


class WorkspaceHeaderProvider(RequestHeaderProvider):
    """Add ``X-MLflow-Workspace: $MLFLOW_WORKSPACE`` to MLflow tracking requests."""

    def _workspace(self) -> str:
        return os.environ.get(ENV_WORKSPACE, "").strip()

    def in_context(self) -> bool:
        # Only active when a workspace is configured — keeps the plugin inert
        # against vanilla MLflow servers and in environments without RHOAI.
        return bool(self._workspace())

    def request_headers(self) -> dict[str, str]:
        ws = self._workspace()
        return {WORKSPACE_HEADER: ws} if ws else {}

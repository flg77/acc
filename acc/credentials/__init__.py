"""ACC credentials — per-operator OAuth 2.1 credential brokering for
user-delegated MCP integrations (office suites, and any future user-scoped
capability).

The :class:`CredentialBroker` adopts the MCP OAuth 2.1 delegated-auth model: the
HUMAN operator consents in the provider's own screen (the agent never enters
credentials), ACC stores only the resulting refresh token (encrypted, keyed by
operator_id), and mints SHORT-LIVED access tokens on demand bound to the
operator. See ACC-PR/Proposals/PR-PROPOSAL-B.
"""

from acc.credentials.broker import (
    ConnectChallenge,
    CredentialBroker,
    MemoryTokenStore,
    NotConnectedError,
    OAuthToken,
    ProviderConfig,
    SealedFileStore,
    TokenStore,
)

__all__ = [
    "ConnectChallenge",
    "CredentialBroker",
    "MemoryTokenStore",
    "NotConnectedError",
    "OAuthToken",
    "ProviderConfig",
    "SealedFileStore",
    "TokenStore",
]

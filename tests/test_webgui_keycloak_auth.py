"""Keycloak/OIDC integration for acc-webgui auth (proposal 023 / ADR 025).

Covers the pure, network-free core: group-mapping parsing, Keycloak claim
extraction (realm_access.roles + groups + client roles), group→tier
resolution, the publisher tier + ladder, and audience validation.
"""

from __future__ import annotations

import pytest

from acc.webgui import auth
from acc.webgui.auth import (
    AuthConfig,
    ROLE_VIEWER,
    ROLE_OPERATOR,
    ROLE_PUBLISHER,
    _audience_ok,
    _extract_groups,
    _parse_group_mappings,
    _role_satisfies,
    role_from_claims,
)

# The canonical 027 group names.
_MAPPINGS = {
    ROLE_OPERATOR: frozenset({"acc-operators"}),
    ROLE_PUBLISHER: frozenset({"acc-publishers"}),
}


def _cfg(**kw) -> AuthConfig:
    base = dict(mode="oidc", oidc_audience="acc-webgui", group_mappings=_MAPPINGS)
    base.update(kw)
    return AuthConfig(**base)


# ---------------------------------------------------------------------------
# group-mapping env parsing
# ---------------------------------------------------------------------------

class TestParseGroupMappings:
    def test_parses_tiers_and_groups(self):
        m = _parse_group_mappings("operator=acc-operators;publisher=acc-publishers,acc-release")
        assert m[ROLE_OPERATOR] == frozenset({"acc-operators"})
        assert m[ROLE_PUBLISHER] == frozenset({"acc-publishers", "acc-release"})

    def test_blank_is_empty(self):
        assert _parse_group_mappings("") == {}
        assert _parse_group_mappings("   ") == {}

    def test_unknown_tier_skipped(self):
        m = _parse_group_mappings("superuser=root;operator=acc-operators")
        assert "superuser" not in m
        assert m[ROLE_OPERATOR] == frozenset({"acc-operators"})


# ---------------------------------------------------------------------------
# Keycloak claim extraction
# ---------------------------------------------------------------------------

class TestExtractGroups:
    def test_realm_access_roles(self):
        claims = {"realm_access": {"roles": ["acc-operators", "offline_access"]}}
        assert "acc-operators" in _extract_groups(claims, _cfg())

    def test_groups_claim_strips_leading_slash(self):
        claims = {"groups": ["/acc-publishers", "/other"]}
        groups = _extract_groups(claims, _cfg())
        assert "acc-publishers" in groups and "other" in groups

    def test_client_roles_for_audience(self):
        claims = {"resource_access": {"acc-webgui": {"roles": ["acc-operators"]}}}
        assert "acc-operators" in _extract_groups(claims, _cfg(oidc_audience="acc-webgui"))

    def test_client_roles_other_client_ignored(self):
        claims = {"resource_access": {"some-other-client": {"roles": ["acc-operators"]}}}
        assert "acc-operators" not in _extract_groups(claims, _cfg(oidc_audience="acc-webgui"))

    def test_custom_groups_claim_name(self):
        claims = {"acc_groups": ["acc-operators"]}
        assert "acc-operators" in _extract_groups(claims, _cfg(oidc_groups_claim="acc_groups"))


# ---------------------------------------------------------------------------
# group → tier resolution
# ---------------------------------------------------------------------------

class TestRoleFromClaims:
    def test_operator_group_maps_to_operator(self):
        claims = {"realm_access": {"roles": ["acc-operators"]}}
        assert role_from_claims(claims, "u", _cfg()) == ROLE_OPERATOR

    def test_publisher_group_maps_to_publisher(self):
        claims = {"groups": ["/acc-publishers"]}
        assert role_from_claims(claims, "u", _cfg()) == ROLE_PUBLISHER

    def test_highest_tier_wins(self):
        # Member of both → publisher (rank 2) beats operator (rank 1).
        claims = {"realm_access": {"roles": ["acc-operators", "acc-publishers"]}}
        assert role_from_claims(claims, "u", _cfg()) == ROLE_PUBLISHER

    def test_no_matching_group_is_viewer(self):
        claims = {"realm_access": {"roles": ["random-team"]}}
        assert role_from_claims(claims, "u", _cfg()) == ROLE_VIEWER

    def test_no_mappings_falls_back_to_operator_users(self):
        # Pre-023 behaviour: empty map → operator_users static list governs.
        cfg = AuthConfig(mode="oidc", group_mappings={}, operator_users=("alice",))
        assert role_from_claims({}, "alice", cfg) == ROLE_OPERATOR
        assert role_from_claims({}, "bob", cfg) == ROLE_VIEWER


# ---------------------------------------------------------------------------
# tier ladder + dependencies
# ---------------------------------------------------------------------------

class TestTierLadder:
    def test_ladder_ordering(self):
        assert _role_satisfies(ROLE_PUBLISHER, ROLE_OPERATOR)
        assert _role_satisfies(ROLE_PUBLISHER, ROLE_VIEWER)
        assert _role_satisfies(ROLE_OPERATOR, ROLE_VIEWER)
        assert _role_satisfies(ROLE_OPERATOR, ROLE_OPERATOR)

    def test_lower_does_not_satisfy_higher(self):
        assert not _role_satisfies(ROLE_OPERATOR, ROLE_PUBLISHER)
        assert not _role_satisfies(ROLE_VIEWER, ROLE_OPERATOR)
        assert not _role_satisfies(ROLE_VIEWER, ROLE_PUBLISHER)


# ---------------------------------------------------------------------------
# audience validation
# ---------------------------------------------------------------------------

class TestAudience:
    def test_aud_string_match(self):
        assert _audience_ok({"aud": "acc-webgui"}, "acc-webgui")

    def test_aud_list_match(self):
        assert _audience_ok({"aud": ["account", "acc-webgui"]}, "acc-webgui")

    def test_azp_match_access_token(self):
        # Keycloak access tokens carry the client in azp, aud may be "account".
        assert _audience_ok({"aud": "account", "azp": "acc-webgui"}, "acc-webgui")

    def test_mismatch_rejected(self):
        assert not _audience_ok({"aud": "other", "azp": "other"}, "acc-webgui")
        assert not _audience_ok({}, "acc-webgui")


# ---------------------------------------------------------------------------
# resolve_auth_config wires the new env vars
# ---------------------------------------------------------------------------

def test_resolve_auth_config_keycloak_envs(monkeypatch):
    monkeypatch.setenv("ACC_WEBGUI_AUTH_MODE", "oidc")
    monkeypatch.setenv("ACC_WEBGUI_OIDC_ISSUER", "https://kc.example.com/realms/acc")
    monkeypatch.setenv("ACC_WEBGUI_OIDC_AUDIENCE", "acc-webgui")
    monkeypatch.setenv("ACC_WEBGUI_OIDC_GROUPS_CLAIM", "groups")
    monkeypatch.setenv("ACC_WEBGUI_GROUP_MAPPINGS", "operator=acc-operators;publisher=acc-publishers")
    cfg = auth.resolve_auth_config()
    assert cfg.mode == "oidc"
    assert cfg.oidc_issuer == "https://kc.example.com/realms/acc"
    assert cfg.oidc_audience == "acc-webgui"
    assert cfg.group_mappings[ROLE_PUBLISHER] == frozenset({"acc-publishers"})

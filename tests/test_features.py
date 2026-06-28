"""Tests for the feature-assembly layer (proposal 043) — acc/features.py.

Covers the in-tree features/ + profiles/ manifests and the resolver that the
build assembler / dev --features / assistant onboarding all share.
"""

from __future__ import annotations

import pytest

from acc.features import (
    FeatureSpec,
    ProfileSpec,
    load_features,
    load_profiles,
    resolve_features,
    resolve_profile,
)


# ---------------------------------------------------------------------------
# In-tree manifests load + validate
# ---------------------------------------------------------------------------


def test_intree_features_load():
    feats = load_features()
    # The four shipped integration features.
    for fid in ("messengers", "signal", "google_workspace", "speech"):
        assert fid in feats, f"missing feature {fid}"
        assert isinstance(feats[fid], FeatureSpec)
    # speech is the only one carrying a pip extra.
    assert feats["speech"].extras == ["speech"]
    assert feats["messengers"].extras == []


def test_intree_profiles_load():
    profs = load_profiles()
    for name in ("nano", "standard", "voice", "edge", "comms", "office", "fulltest"):
        assert name in profs, f"missing profile {name}"
        assert isinstance(profs[name], ProfileSpec)
    assert profs["comms"].kind == "bundle"
    assert profs["office"].kind == "bundle"
    assert profs["voice"].kind == "profile"


# ---------------------------------------------------------------------------
# resolve_features — union semantics
# ---------------------------------------------------------------------------


def test_resolve_union_of_features():
    r = resolve_features(["messengers", "speech"])
    assert r.extras == ["speech"]                       # only speech has one
    assert "telegram_send" in r.skills                  # from messengers
    assert "speech_transcribe" in r.skills              # from speech
    assert "acc-channel-voice" in r.channels            # from speech
    assert "send_message" in r.actions                  # from messengers
    # de-dup + sorted
    assert r.skills == sorted(set(r.skills))


def test_resolve_aggregates_sidecars_and_required_env():
    r = resolve_features(["signal", "google_workspace"])
    assert set(r.sidecars) == {"signal-cli", "mcp-google"}
    assert "ACC_SIGNAL_API_URL" in r.requires_env
    assert "GOOGLE_OAUTH_CLIENT_ID" in r.requires_env
    assert "ACC_CRED_KEY" in r.requires_env
    # optional_env is a merged map (docs/defaults), not required.
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in r.optional_env


def test_unknown_feature_fails_fast():
    with pytest.raises(ValueError, match="unknown feature"):
        resolve_features(["messengers", "does_not_exist"])


# ---------------------------------------------------------------------------
# resolve_profile — base inheritance
# ---------------------------------------------------------------------------


def test_voice_inherits_standard():
    """voice (base=standard) = standard's features + speech."""
    std = resolve_profile("standard")
    voice = resolve_profile("voice")
    assert set(std.feature_ids).issubset(set(voice.feature_ids))
    assert "speech" in voice.feature_ids
    assert "google_workspace" in voice.feature_ids      # inherited from standard
    assert voice.extras == ["speech"]


def test_fulltest_has_everything():
    r = resolve_profile("fulltest")
    assert set(r.feature_ids) == {"messengers", "signal", "google_workspace", "speech"}
    # union surfaces every sidecar + the speech extra
    assert set(r.sidecars) == {"signal-cli", "mcp-google"}
    assert r.extras == ["speech"]


def test_nano_is_lean():
    r = resolve_profile("nano")
    assert r.extras == []                               # no heavy bytes
    assert r.sidecars == []
    assert "telegram_send" in r.skills


def test_unknown_profile_fails_fast():
    with pytest.raises(ValueError, match="unknown profile"):
        resolve_profile("nope")


# ---------------------------------------------------------------------------
# Resolver works on synthetic specs (no filesystem) — base cycle guard
# ---------------------------------------------------------------------------


def test_base_cycle_detected():
    profs = {
        "a": ProfileSpec(name="a", base="b", features=[]),
        "b": ProfileSpec(name="b", base="a", features=[]),
    }
    feats: dict[str, FeatureSpec] = {}
    with pytest.raises(ValueError, match="cycle"):
        resolve_profile("a", features=feats, profiles=profs)


# ---------------------------------------------------------------------------
# CLI seam (acc.features.main) — what the shell assembler calls
# ---------------------------------------------------------------------------


def test_cli_shellenv(capsys):
    from acc.features import main
    rc = main(["shellenv", "--profile", "fulltest"])
    out = capsys.readouterr().out
    assert rc == 0
    assert 'ACC_F_EXTRAS="speech"' in out
    assert "signal-cli" in out and "mcp-google" in out      # sidecars
    assert "ACC_SIGNAL_API_URL" in out                       # required env


def test_cli_env_required(capsys):
    from acc.features import main
    rc = main(["env-required", "--features", "google_workspace"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "GOOGLE_OAUTH_CLIENT_ID=" in out                  # required, uncommented
    assert "# GOOGLE_OAUTH_CLIENT_SECRET=" in out            # optional, commented


def test_cli_unknown_exits_2(capsys):
    from acc.features import main
    rc = main(["shellenv", "--features", "nope"])
    assert rc == 2
    assert "unknown feature" in capsys.readouterr().out

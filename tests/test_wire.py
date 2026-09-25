"""`20260923-lessons-that-travel` PA-09 Phase 1 — the sender proof itself.

The relay tests in `test_route_through_arbiter.py` cover the one place this is
wired. These cover the primitive: what the signature actually commits to, what
the NKey decoders accept, and the failure modes a verifier must not be loose
about.
"""

from __future__ import annotations

import base64
import json

import pytest

from acc import wire
from acc.nkeys import (
    NKeyError,
    decode_public,
    decode_seed,
    generate_user_nkey,
    public_key_of_seed,
)


@pytest.fixture
def pair():
    seed, public = generate_user_nkey()
    return seed, public


def _payload(**kw):
    body = {"signal_type": "ROUTE_REQUEST", "from_agent": "orch-1",
            "from_role": "orchestrator", "target_role": "analyst",
            "task": {"task_id": "t-1", "content": "do the thing"}, "ts": 1.0}
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
# The NKey codec — the inverse of what the generator has always written
# ---------------------------------------------------------------------------


def test_a_generated_key_round_trips(pair):
    seed, public = pair
    assert public_key_of_seed(seed) == public
    assert decode_seed(seed).public_key().public_bytes_raw() == decode_public(public).public_bytes_raw()


@pytest.mark.parametrize("bad, why", [
    ("", "not a seed"),
    ("nonsense", "not a seed"),
    ("UAAAA", "not a seed"),
])
def test_decode_seed_rejects_what_is_not_one(bad, why):
    with pytest.raises(NKeyError) as exc:
        decode_seed(bad)
    assert why in str(exc.value)


def test_decode_rejects_a_corrupted_checksum(pair):
    seed, public = pair
    flipped = public[:-2] + ("AA" if public[-2:] != "AA" else "AB")
    with pytest.raises(NKeyError):
        decode_public(flipped)


def test_a_seed_is_not_accepted_as_a_public_key(pair):
    seed, _ = pair
    with pytest.raises(NKeyError) as exc:
        decode_public(seed)
    assert "not a user public key" in str(exc.value)


def test_surrounding_whitespace_from_a_seed_file_is_tolerated(pair):
    seed, public = pair
    assert public_key_of_seed(f"  {seed}\n") == public


# ---------------------------------------------------------------------------
# What the signature commits to
# ---------------------------------------------------------------------------


def test_the_canonical_form_ignores_key_order_and_the_proof(pair):
    a = wire.canonical_bytes({"b": 2, "a": 1})
    b = wire.canonical_bytes({"a": 1, "b": 2})
    assert a == b
    assert wire.canonical_bytes({"a": 1, wire.PROOF_FIELD: {"x": 1}}) == wire.canonical_bytes({"a": 1})


def test_a_signature_verifies_and_carries_the_key_that_made_it(pair):
    seed, public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed, agent_id="analyst-1")
    assert wire.signer_identity(signed) == "analyst"
    assert signed[wire.PROOF_FIELD]["agent_id"] == "analyst-1"
    assert signed[wire.PROOF_FIELD]["public_key"] == public
    assert signed[wire.PROOF_FIELD]["schema_rev"] == wire.PROOF_SCHEMA_REV == 2
    assert wire.verify_payload(signed, {"analyst": public}) == ""


def test_signing_twice_does_not_nest_proofs(pair):
    seed, public = pair
    once = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    twice = wire.sign_payload(once, identity="analyst", seed=seed)
    assert wire.verify_payload(twice, {"analyst": public}) == ""
    assert wire.canonical_bytes(once) == wire.canonical_bytes(twice)


@pytest.mark.parametrize("field, value", [
    ("target_role", "arbiter"),
    ("from_agent", "someone-else"),
    ("from_role", "arbiter"),
    ("task", {"task_id": "t-1", "content": "something else"}),
    ("ts", 2.0),
])
def test_every_field_outside_the_proof_is_covered(pair, field, value):
    seed, public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    assert "does not verify" in wire.verify_payload({**signed, field: value}, {"analyst": public})


def test_adding_a_field_breaks_the_signature_too(pair):
    """Not only mutation: a verifier that signed a subset would let an
    attacker append the field a receiver actually reads."""
    seed, public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    assert "does not verify" in wire.verify_payload({**signed, "routed_by": "x"}, {"analyst": public})


def test_the_proof_itself_is_not_a_place_to_hide_a_claim(pair):
    """`agent_id` inside the proof is informational and NOT signed, which is
    why nothing may authorise on it — the signed `from_agent` is what the
    relay reads."""
    seed, public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed, agent_id="analyst-1")
    moved = {**signed, wire.PROOF_FIELD: {**signed[wire.PROOF_FIELD], "agent_id": "arbiter-1"}}
    assert wire.verify_payload(moved, {"analyst": public}) == "", "unsigned, so it still verifies"
    assert moved["from_agent"] == "orch-1", "and the field that IS signed is unchanged"


# ---------------------------------------------------------------------------
# Verification failures
# ---------------------------------------------------------------------------


def test_an_unsigned_payload_is_reported_as_such(pair):
    _seed, public = pair
    assert wire.verify_payload(_payload(), {"analyst": public}) == "unsigned"


def test_a_key_set_without_the_signing_key_cannot_verify(pair):
    seed, _public = pair
    _other_seed, other_public = generate_user_nkey()
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    assert wire.verify_payload(signed, {"arbiter": other_public}) == "signing key is not in the key set"


def test_a_key_the_set_does_not_hold_is_refused_whatever_it_calls_itself(pair):
    """The refusal names the key, not the label: an unknown signer calling
    itself ``analyst`` is refused for the same reason as one calling itself
    anything else."""
    _seed, _public = pair
    stranger_seed, _stranger_public = generate_user_nkey()
    known_seed, known_public = generate_user_nkey()
    signed = wire.sign_payload(_payload(), identity="analyst", seed=stranger_seed)
    assert wire.verify_payload(signed, {"analyst": known_public}) == "signing key is not in the key set"
    assert wire.verify_payload(
        wire.sign_payload(_payload(), identity="analyst", seed=known_seed),
        {"analyst": known_public},
    ) == ""


# ---------------------------------------------------------------------------
# The key identifies the signer — the label does not (lighthouse, 2026-09-23)
# ---------------------------------------------------------------------------


def test_a_packaged_role_signing_with_a_workers_key_verifies():
    """The regression the lighthouse smoke found.

    An agent whose role the NKey matrix does not name has no seed of its
    own: it runs as a worker and signs with ``seed-coding_agent``, while
    calling itself by its role (``orchestrator``, from ``agent.role`` when
    ``ACC_NKEY_ROLE`` is unset).  rev 1 looked that name up in the key set,
    found nothing, and refused every legitimate ask the moment a key set was
    distributed — in exactly the deployment Phase 9 exists for.
    """
    worker_seed, worker_public = generate_user_nkey()
    keys = {"coding_agent": worker_public, "arbiter": generate_user_nkey()[1]}
    signed = wire.sign_payload(
        _payload(), identity="orchestrator", seed=worker_seed, agent_id="orch-1",
    )
    assert wire.verify_payload(signed, keys) == ""
    assert wire.identity_of_key(signed[wire.PROOF_FIELD]["public_key"], keys) == "coding_agent"


def test_the_label_cannot_borrow_another_identitys_entitlement(pair):
    """The label is informational, so relabelling a genuine signature does
    not make it that identity: the binding reads the key."""
    seed, public = pair
    keys = {"analyst": public, "arbiter": generate_user_nkey()[1]}
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    relabelled = {**signed, wire.PROOF_FIELD: {**signed[wire.PROOF_FIELD],
                                               "identity": "arbiter"}}
    assert wire.verify_payload(relabelled, keys) == "", "still a good signature"
    reason = wire.verify_payload(relabelled, keys, expected_identity="arbiter")
    assert reason == "signed by 'analyst', expected 'arbiter'"


def test_identity_of_key_ignores_surrounding_whitespace_and_misses():
    _seed, public = generate_user_nkey()
    assert wire.identity_of_key(f"  {public}\n", {"analyst": public}) == "analyst"
    assert wire.identity_of_key(public, {"analyst": f"{public}\n"}) == "analyst"
    assert wire.identity_of_key("", {"analyst": public}) == ""
    assert wire.identity_of_key(public, {}) == ""


def test_the_signature_is_checked_against_the_key_set_not_the_payload(pair):
    """A payload carrying both the key and a signature over itself would
    otherwise prove only that it is internally consistent."""
    seed, public = pair
    forged_seed, forged_public = generate_user_nkey()
    forged = wire.sign_payload(_payload(), identity="analyst", seed=forged_seed)
    # Present the honest identity's key while signing with another.
    lying = {**forged, wire.PROOF_FIELD: {**forged[wire.PROOF_FIELD],
                                          "public_key": public}}
    assert wire.verify_payload(lying, {"analyst": public}) == \
        "signature does not verify for identity 'analyst'"
    assert forged_public != public


def test_an_expected_identity_is_enforced_when_asked(pair):
    seed, public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    assert wire.verify_payload(signed, {"analyst": public}, expected_identity="analyst") == ""
    assert "expected 'arbiter'" in wire.verify_payload(signed, {"analyst": public}, expected_identity="arbiter")


@pytest.mark.parametrize("proof, expected", [
    ("not a dict", "unsigned"),
    ({"identity": "analyst"}, "malformed proof"),
    ({"signature": "x"}, "malformed proof"),
    ({"public_key": "{PUB}", "signature": "x", "alg": "hmac"}, "unsupported algorithm"),
    ({"public_key": "{PUB}", "signature": "x", "schema_rev": 99}, "unsupported proof schema_rev"),
    ({"public_key": "{PUB}", "signature": "not base64!!"}, "not base64"),
])
def test_a_malformed_proof_is_refused_not_guessed_at(pair, proof, expected):
    _seed, public = pair
    if isinstance(proof, dict):
        proof = {k: (public if v == "{PUB}" else v) for k, v in proof.items()}
    assert expected in wire.verify_payload({**_payload(), wire.PROOF_FIELD: proof}, {"analyst": public})


def test_a_revision_1_proof_is_refused_rather_than_name_looked_up(pair):
    """rev 1 named its signer and the verifier looked the name up.  A
    deployment still sending those must be told, not silently trusted on a
    field anyone can write — and a rev 1 proof has no key to resolve, so it
    cannot even be parsed."""
    seed, public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    rev1 = {k: v for k, v in signed[wire.PROOF_FIELD].items() if k != "public_key"}
    rev1["schema_rev"] = 1
    assert wire.verify_payload({**signed, wire.PROOF_FIELD: rev1},
                               {"analyst": public}).startswith("malformed proof")
    # And even one that kept the field is refused on the revision alone.
    assert wire.verify_payload(
        {**signed, wire.PROOF_FIELD: {**signed[wire.PROOF_FIELD], "schema_rev": 1}},
        {"analyst": public},
    ) == "unsupported proof schema_rev 1"


def test_the_refusal_never_explains_how_the_signature_failed(pair):
    """A verifier that says which byte was wrong helps forge the next one."""
    seed, public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    tampered = {**signed, "target_role": "arbiter"}
    reason = wire.verify_payload(tampered, {"analyst": public})
    assert reason == "signature does not verify for identity 'analyst'"
    assert base64.b64decode(signed[wire.PROOF_FIELD]["signature"]).hex()[:8] not in reason


def test_a_key_set_entry_that_is_not_an_nkey_is_reported_as_that(pair):
    """A corrupt key set: the entry matches what the proof presents, so the
    identity resolves, and only the decode says it is not a key."""
    seed, _public = pair
    signed = wire.sign_payload(_payload(), identity="analyst", seed=seed)
    broken = {**signed, wire.PROOF_FIELD: {**signed[wire.PROOF_FIELD],
                                           "public_key": "definitely-not-a-key"}}
    assert "is not an NKey" in wire.verify_payload(broken, {"analyst": "definitely-not-a-key"})


# ---------------------------------------------------------------------------
# Loading the key set
# ---------------------------------------------------------------------------


def test_a_missing_key_set_reads_as_empty_not_as_an_error(tmp_path):
    assert wire.load_public_keys(tmp_path / "nope.json") == {}


@pytest.mark.parametrize("content", ["{ broken", '"a string"', "[1, 2]"])
def test_an_unusable_key_set_reads_as_empty(tmp_path, content):
    p = tmp_path / "public_keys.json"
    p.write_text(content, encoding="ascii")
    assert wire.load_public_keys(p) == {}


def test_the_generators_output_loads(tmp_path):
    from acc.nkeys import NKEY_IDENTITIES, generate_identity_keys
    keys = generate_identity_keys()
    p = tmp_path / "public_keys.json"
    p.write_text(json.dumps({i: keys[i]["public"] for i in NKEY_IDENTITIES}), encoding="ascii")
    loaded = wire.load_public_keys(p)
    assert set(loaded) == set(NKEY_IDENTITIES)
    signed = wire.sign_payload(_payload(), identity="arbiter", seed=keys["arbiter"]["seed"])
    assert wire.verify_payload(signed, loaded) == ""


def test_the_default_key_set_path_is_beside_the_seed():
    assert wire.default_public_keys_path("/run/acc/nkeys/seed-analyst").as_posix() == "/run/acc/nkeys/public_keys.json"

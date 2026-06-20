"""Slash-command parser tests (PR-5 of subagent clustering).

Pure-function tests — no Pilot, no NATS.  Pins the verb routing,
back-compat (non-slash input passes through), error messages on
malformed usage, cluster_id-prefix recognition, and oversight
sub-verbs.

The screen-side dispatch is intentionally NOT tested here; that
integration belongs in a Pilot test that would be much heavier.
The parser carries every interesting decision via :class:`SlashIntent`
and the dispatch is a small switch on ``intent.kind``.
"""

from __future__ import annotations

from acc import slash_commands as sc


# ---------------------------------------------------------------------------
# Pass-through — non-slash input
# ---------------------------------------------------------------------------


def test_empty_input_is_not_slash():
    assert sc.parse("").kind == sc.KIND_NOT_SLASH
    assert sc.parse("   \n\t").kind == sc.KIND_NOT_SLASH


def test_plain_prompt_is_not_slash():
    assert sc.parse("Generate a unit test for FizzBuzz").kind == sc.KIND_NOT_SLASH


# ---------------------------------------------------------------------------
# /help — explicit + implicit
# ---------------------------------------------------------------------------


def test_help_verb_returns_help_intent():
    assert sc.parse("/help").kind == sc.KIND_HELP


def test_bare_slash_implies_help():
    """Operator typing ``/`` then Enter shouldn't fail — help is the
    least-surprising fallback, mirrors how IRC + Slack behave."""
    assert sc.parse("/").kind == sc.KIND_HELP


# ---------------------------------------------------------------------------
# /cancel
# ---------------------------------------------------------------------------


def test_cancel_with_task_id():
    intent = sc.parse("/cancel t-deadbeef")
    assert intent.kind == sc.KIND_CANCEL
    assert intent.args == {"task_id": "t-deadbeef"}


def test_cancel_with_cluster_id_routes_to_cluster_kill():
    """A cancel target prefixed ``c-`` is a cluster id; PR-5 routes it
    to KIND_CLUSTER_KILL so the agent-side handler aborts every member
    in one go.  Operators get the same UX as ``/cluster kill``."""
    intent = sc.parse("/cancel c-abc123")
    assert intent.kind == sc.KIND_CLUSTER_KILL
    assert intent.args == {"cluster_id": "c-abc123"}


def test_cancel_without_target_returns_invalid():
    intent = sc.parse("/cancel")
    assert intent.kind == sc.KIND_INVALID
    assert "task_id" in intent.error


# ---------------------------------------------------------------------------
# /cluster
# ---------------------------------------------------------------------------


def test_cluster_show_without_id_lists_all():
    intent = sc.parse("/cluster show")
    assert intent.kind == sc.KIND_CLUSTER_SHOW
    assert intent.args == {"cluster_id": ""}


def test_cluster_show_with_id_targets_one():
    intent = sc.parse("/cluster show c-xyz")
    assert intent.kind == sc.KIND_CLUSTER_SHOW
    assert intent.args == {"cluster_id": "c-xyz"}


def test_cluster_kill_requires_id():
    intent = sc.parse("/cluster kill")
    assert intent.kind == sc.KIND_INVALID
    assert "cluster" in intent.error.lower()


def test_cluster_kill_with_id():
    intent = sc.parse("/cluster kill c-zzz")
    assert intent.kind == sc.KIND_CLUSTER_KILL
    assert intent.args == {"cluster_id": "c-zzz"}


def test_unknown_cluster_subcommand_errors_with_message():
    intent = sc.parse("/cluster nuke c-1")
    assert intent.kind == sc.KIND_INVALID
    assert "nuke" in intent.error


# ---------------------------------------------------------------------------
# /role
# ---------------------------------------------------------------------------


def test_role_list():
    assert sc.parse("/role list").kind == sc.KIND_ROLE_LIST


def test_role_unknown_subcommand_errors():
    intent = sc.parse("/role infuse")
    assert intent.kind == sc.KIND_INVALID


# ---------------------------------------------------------------------------
# /skills
# ---------------------------------------------------------------------------


def test_skills_no_args():
    assert sc.parse("/skills").kind == sc.KIND_SKILLS


# ---------------------------------------------------------------------------
# /oversight — sub-verbs
# ---------------------------------------------------------------------------


def test_oversight_pending():
    assert sc.parse("/oversight pending").kind == sc.KIND_OVERSIGHT_PENDING


def test_oversight_approve_with_id():
    intent = sc.parse("/oversight approve abc")
    assert intent.kind == sc.KIND_OVERSIGHT_APPROVE
    assert intent.args == {"oversight_id": "abc"}


def test_oversight_approve_without_id_errors():
    intent = sc.parse("/oversight approve")
    assert intent.kind == sc.KIND_INVALID


def test_oversight_reject_with_id_and_multi_word_reason():
    intent = sc.parse("/oversight reject abc unsafe content here")
    assert intent.kind == sc.KIND_OVERSIGHT_REJECT
    assert intent.args == {"oversight_id": "abc", "reason": "unsafe content here"}


def test_oversight_reject_without_reason_errors():
    intent = sc.parse("/oversight reject abc")
    assert intent.kind == sc.KIND_INVALID
    assert "reason" in intent.error


def test_oversight_unknown_subverb_errors():
    intent = sc.parse("/oversight escalate abc")
    assert intent.kind == sc.KIND_INVALID


# ---------------------------------------------------------------------------
# Unknown verbs
# ---------------------------------------------------------------------------


def test_unknown_verb_returns_unknown_with_helpful_message():
    intent = sc.parse("/totally-invented")
    assert intent.kind == sc.KIND_UNKNOWN
    assert "totally-invented" in intent.error
    assert "/help" in intent.error.lower()


def test_help_text_contains_every_verb():
    """The help message is what operators read after a typo — make
    sure every verb the parser accepts is documented in it."""
    text = sc.HELP_TEXT
    for verb in (
        "/help", "/cancel", "/cluster show", "/cluster kill",
        "/role list", "/skills", "/oversight pending",
        "/oversight approve", "/oversight reject",
    ):
        assert verb in text, f"{verb!r} missing from HELP_TEXT"


# ---------------------------------------------------------------------------
# Wire signal name pinned
# ---------------------------------------------------------------------------


def test_task_cancel_signal_subject_format():
    """Pin the canonical subject so the agent-side handler (PR-5
    follow-up) and the prompt channel agree without re-discovering
    the format."""
    from acc.signals import SIG_TASK_CANCEL, subject_task_cancel
    assert SIG_TASK_CANCEL == "TASK_CANCEL"
    assert subject_task_cancel("sol-01") == "acc.sol-01.task.cancel"


# ---------------------------------------------------------------------------
# Command registry + completion (proposal 039 — palette discovery)
# ---------------------------------------------------------------------------


def test_registry_verbs_are_all_known_to_parse():
    """Parity guard: every command the palette offers must be a verb
    ``parse`` recognises — else the menu would surface an ``unknown`` verb."""
    for spec in sc.COMMANDS:
        intent = sc.parse(f"/{spec.name}")
        assert intent.kind != sc.KIND_UNKNOWN, f"/{spec.name} unknown to parse()"


def test_complete_bare_slash_returns_all_alphabetical():
    names = [c.name for c in sc.complete("/")]
    assert names == sorted(names)
    assert names == [c.name for c in sc.COMMANDS]  # COMMANDS kept alphabetical
    assert {"cancel", "oversight", "wake"} <= set(names)


def test_complete_prefix_filters_alphabetical():
    assert [c.name for c in sc.complete("/c")] == ["cancel", "catalog", "clear", "cluster"]


def test_complete_is_case_insensitive():
    assert [c.name for c in sc.complete("/OV")] == ["oversight"]


def test_complete_no_match_returns_empty():
    assert sc.complete("/zzz") == []


def test_help_text_generated_and_alphabetical():
    text = sc.HELP_TEXT
    assert text.startswith("Slash commands:")
    assert text.index("/cancel") < text.index("/wake")  # alphabetical order


# ---------------------------------------------------------------------------
# PR-3 verbs (proposal 039) — clear / status / mode
# ---------------------------------------------------------------------------


def test_clear_and_status_parse():
    assert sc.parse("/clear").kind == sc.KIND_CLEAR
    assert sc.parse("/status").kind == sc.KIND_STATUS


def test_mode_parse_valid_normalises_uppercase():
    intent = sc.parse("/mode plan")
    assert intent.kind == sc.KIND_MODE
    assert intent.args == {"mode": "PLAN"}


def test_mode_parse_missing_or_bogus_is_invalid():
    assert sc.parse("/mode").kind == sc.KIND_INVALID
    bogus = sc.parse("/mode turbo")
    assert bogus.kind == sc.KIND_INVALID
    assert "turbo" in bogus.error


def test_pr3_verbs_in_registry_and_help():
    names = {c.name for c in sc.COMMANDS}
    assert {"clear", "status", "mode"} <= names
    for verb in ("/clear", "/status", "/mode"):
        assert verb in sc.HELP_TEXT


def test_pr4_catalog_model_parse():
    assert sc.parse("/catalog").kind == sc.KIND_CATALOG
    assert sc.parse("/catalog @acc").args == {"filter": "@acc"}
    assert sc.parse("/model").kind == sc.KIND_MODEL
    assert {"catalog", "model"} <= {c.name for c in sc.COMMANDS}


def test_pr5_goal_parse():
    set_intent = sc.parse("/goal ship v2 by friday")
    assert set_intent.kind == sc.KIND_GOAL
    assert set_intent.args == {"text": "ship v2 by friday"}
    assert sc.parse("/goal").args == {"text": ""}      # show current
    assert sc.parse("/goal clear").args == {"text": "clear"}
    assert "goal" in {c.name for c in sc.COMMANDS}


def test_pr6_prod_gating():
    # /loop is prod-locked by default — allowed in dev, refused in prod.
    assert sc.is_allowed("loop", dev_mode=True) is True
    assert sc.is_allowed("loop", dev_mode=False) is False
    assert sc.is_allowed("/loop", dev_mode=False) is False   # leading slash tolerated
    # non-locked verbs are allowed in both modes.
    assert sc.is_allowed("status", dev_mode=False) is True
    assert sc.is_allowed("help", dev_mode=False) is True
    # unknown verb defaults allowed (parse handles it).
    assert sc.is_allowed("nope", dev_mode=False) is True
    loop = next(c for c in sc.COMMANDS if c.name == "loop")
    assert loop.prod_locked is True


def test_pr5_loop_parse():
    started = sc.parse("/loop 5m check the deploy")
    assert started.kind == sc.KIND_LOOP
    assert started.args == {
        "action": "start", "interval_s": 300, "prompt": "check the deploy",
    }
    assert sc.parse("/loop 30s ping").args["interval_s"] == 30
    assert sc.parse("/loop stop").args == {"action": "stop"}
    assert sc.parse("/loop").args == {"action": "show"}
    assert sc.parse("/loop 5m").kind == sc.KIND_INVALID       # interval, no prompt
    assert sc.parse("/loop 5x do it").kind == sc.KIND_INVALID  # bad unit
    assert "loop" in {c.name for c in sc.COMMANDS}

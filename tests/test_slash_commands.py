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

"""acc-cli up/down/restart/stack hand their arguments to acc-deploy.sh."""

from __future__ import annotations

import pytest

import acc.cli as cli
from acc import launcher


@pytest.fixture
def stack_calls(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher, "run_stack", lambda args: calls.append(list(args)) or 0)
    return calls


@pytest.mark.parametrize("argv, expected", [
    (["up"], ["up"]),
    (["up", "--webgui"], ["up", "--webgui"]),
    (["up", "-d", "acc-tui", "--webgui"], ["up", "-d", "acc-tui", "--webgui"]),
    (["down", "-v"], ["down", "-v"]),
    (["restart", "--webgui"], ["restart", "--webgui"]),
    (["stack", "rebuild", "--no-cache"], ["rebuild", "--no-cache"]),
    (["stack", "logs", "acc-tui"], ["logs", "acc-tui"]),
    (["-v", "up", "-h"], ["up", "-h"]),
])
def test_stack_verbs_pass_everything_through_in_order(stack_calls, argv, expected):
    assert cli.main(argv) == 0
    assert stack_calls == [expected]


def test_stack_exit_code_is_the_scripts(monkeypatch):
    monkeypatch.setattr(launcher, "run_stack", lambda args: 2)
    assert cli.main(["down"]) == 2


def test_other_commands_still_refuse_unknown_arguments(stack_calls, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["status", "--no-such-flag"])
    assert exc.value.code == 2
    assert "unrecognized arguments: --no-such-flag" in capsys.readouterr().err
    assert stack_calls == []

"""`20260909-acc-install` IN-07 -- the operator shares the service's state.

On a system install the stack runs as the `acc` user and owns /var/lib/acc;
the operator's own commands resolve their state there too.  The operator
decided (2026-09-11) that they share one state tree through the `acc` group:
the package makes the tree setgid and group-writable, the unit and every host
command keep what they write group-writable, and `acc paths` says what to do
when the operator is not in the group yet.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from acc import paths as P


@pytest.fixture
def system_state(monkeypatch, tmp_path):
    """A stand-in for /var/lib/acc, resolved as the system state."""
    for k in list(os.environ):
        if k.startswith("ACC_"):
            monkeypatch.delenv(k, raising=False)
    root = tmp_path / "var-lib-acc"
    root.mkdir()
    monkeypatch.setattr(P, "SYSTEM_STATE", root)
    monkeypatch.setenv("ACC_STATE", str(root))
    return root


def test_the_system_state_is_shared(system_state):
    assert P.shared_state() is True


def test_a_users_own_state_is_not(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("ACC_"):
            monkeypatch.delenv(k, raising=False)
    mine = tmp_path / "mine"; mine.mkdir()
    monkeypatch.setenv("ACC_STATE", str(mine))
    assert P.shared_state() is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX umask semantics")
def test_the_umask_keeps_group_write_for_shared_state(system_state):
    before = os.umask(0o022)
    try:
        P.adopt_shared_umask()
        now = os.umask(0o022)
        assert now == 0o002            # only the group-write bit was cleared
    finally:
        os.umask(before)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX umask semantics")
def test_the_umask_is_left_alone_for_private_state(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("ACC_"):
            monkeypatch.delenv(k, raising=False)
    mine = tmp_path / "mine"; mine.mkdir()
    monkeypatch.setenv("ACC_STATE", str(mine))
    before = os.umask(0o022)
    try:
        P.adopt_shared_umask()
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(before)


def test_no_hint_when_the_shared_state_is_writable(system_state, monkeypatch):
    monkeypatch.setattr(P.os, "access", lambda p, mode: True)
    assert P.state_hint() == ""
    assert "usermod" not in P.describe()


def test_the_hint_says_join_the_group_when_it_is_not(system_state, monkeypatch):
    monkeypatch.setattr(P.os, "access", lambda p, mode: False)
    hint = P.state_hint()
    assert "usermod -aG acc" in hint and str(system_state) in hint
    assert hint in P.describe()        # `acc paths` and `doctor --paths` print it


@pytest.mark.parametrize("entry", [
    ("acc.launcher", ["--version"]),
    ("acc.cli", ["--help"]),
    ("acc.pkg.cli", ["--help"]),
])
def test_every_host_command_adopts_the_umask_first(entry, monkeypatch):
    import importlib
    module = importlib.import_module(entry[0])
    calls = []
    monkeypatch.setattr(P, "adopt_shared_umask", lambda: calls.append(1))
    try:
        module.main(entry[1])
    except SystemExit:
        pass                           # argparse exits on --help
    assert calls == [1], entry[0]

"""Tests for acc.sandbox.runner — the OpenShell exec-delegation shim (Model 2)."""

from __future__ import annotations

import subprocess

import pytest

from acc.sandbox.runner import (
    ExecResult,
    SandboxConfig,
    SandboxUnavailable,
    run_in_sandbox,
)


def test_config_from_env_enabled():
    cfg = SandboxConfig.from_env(
        {"ACC_SANDBOX_NAME": "acc-coding", "OPENSHELL_GATEWAY": "acc-prod"}
    )
    assert cfg.enabled
    assert cfg.sandbox_name == "acc-coding"
    assert cfg.gateway == "acc-prod"


def test_config_from_env_disabled():
    # No sandbox name → disabled (the default, un-caged path).
    assert not SandboxConfig.from_env({}).enabled
    # Name set but the flag is explicitly off → disabled.
    assert not SandboxConfig.from_env(
        {"ACC_SANDBOX_NAME": "x", "ACC_SANDBOX_ENABLED": "false"}
    ).enabled


def test_run_in_sandbox_builds_cli_and_parses(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="hello\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = SandboxConfig(enabled=True, sandbox_name="acc-coding", gateway="acc-prod")
    res = run_in_sandbox(["python3", "-c", "print(1)"], cfg, timeout_s=10)

    # Delegates via `openshell sandbox exec -n <name> -- <argv…>`.
    assert captured["cmd"] == [
        "openshell", "sandbox", "exec", "-n", "acc-coding", "--",
        "python3", "-c", "print(1)",
    ]
    assert captured["env"]["OPENSHELL_GATEWAY"] == "acc-prod"
    assert isinstance(res, ExecResult)
    assert res.returncode == 0
    assert res.stdout == "hello\n"
    assert res.as_dict()["returncode"] == 0


def test_run_in_sandbox_unconfigured_fail_closed():
    with pytest.raises(SandboxUnavailable):
        run_in_sandbox(["true"], SandboxConfig(enabled=False, sandbox_name=""))


def test_run_in_sandbox_cli_missing_fail_closed(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("openshell")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(SandboxUnavailable):
        run_in_sandbox(["true"], SandboxConfig(enabled=True, sandbox_name="acc-coding"))


def test_run_in_sandbox_timeout_raises_valueerror(monkeypatch):
    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError):
        run_in_sandbox(
            ["sleep", "99"], SandboxConfig(enabled=True, sandbox_name="acc-coding"), timeout_s=10
        )

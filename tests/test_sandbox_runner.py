"""Tests for acc.sandbox.runner — the OpenShell exec-delegation shim (Model 2)."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
from pathlib import Path

import pytest

from acc.sandbox.runner import (
    ExecResult,
    SandboxConfig,
    SandboxUnavailable,
    maybe_run_sandboxed,
    run_in_sandbox,
)


def _load_skill(skill_id: str, cls: str):
    """Load skills/<id>/adapter.py directly (skills/ is not an importable pkg)."""
    path = Path(__file__).resolve().parent.parent / "skills" / skill_id / "adapter.py"
    spec = importlib.util.spec_from_file_location(f"_test_{skill_id}_adapter", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, cls)


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


# --- maybe_run_sandboxed: the exec skills' one-line drop-in gate ---


def test_maybe_run_sandboxed_disabled_returns_none():
    # Not sandboxed → returns None so the skill runs the command locally.
    assert maybe_run_sandboxed(["true"], env={}) is None


def test_maybe_run_sandboxed_enabled_delegates(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = maybe_run_sandboxed(["true"], env={"ACC_SANDBOX_NAME": "s1"})
    assert out is not None
    assert out["returncode"] == 0
    assert out["stdout"] == "ok\n"


def test_maybe_run_sandboxed_enabled_fail_closed(monkeypatch):
    # Sandboxed but the CLI is missing → fail-closed (never a local fallback).
    def boom(cmd, **kw):
        raise FileNotFoundError("openshell")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(SandboxUnavailable):
        maybe_run_sandboxed(["true"], env={"ACC_SANDBOX_NAME": "s1"})


# --- the exec skills actually delegate when their agent is sandboxed ---


def test_shell_exec_delegates_when_sandboxed(monkeypatch):
    monkeypatch.setenv("ACC_SANDBOX_NAME", "acc-coding")
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ShellExecSkill = _load_skill("shell_exec", "ShellExecSkill")
    res = asyncio.run(ShellExecSkill().invoke({"argv": ["git", "status"]}))

    assert captured["cmd"] == [
        "openshell", "sandbox", "exec", "-n", "acc-coding", "--", "git", "status",
    ]
    assert res["returncode"] == 0
    assert res["stdout"] == "ok\n"


def test_shell_exec_runs_local_when_not_sandboxed(monkeypatch):
    # Default path (no ACC_SANDBOX_NAME): the command runs locally, un-delegated.
    monkeypatch.delenv("ACC_SANDBOX_NAME", raising=False)
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ShellExecSkill = _load_skill("shell_exec", "ShellExecSkill")
    asyncio.run(ShellExecSkill().invoke({"argv": ["git", "status"]}))

    assert captured["cmd"] == ["git", "status"]  # raw argv, not the openshell wrapper


def test_python_exec_delegates_with_sandbox_python(monkeypatch):
    # In the sandbox the interpreter is "python3" — NOT the host sys.executable.
    monkeypatch.setenv("ACC_SANDBOX_NAME", "acc-coding")
    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    PythonExecSkill = _load_skill("python_exec", "PythonExecSkill")
    res = asyncio.run(PythonExecSkill().invoke({"code": "print(6 * 7)"}))

    assert captured["cmd"] == [
        "openshell", "sandbox", "exec", "-n", "acc-coding", "--",
        "python3", "-I", "-c", "print(6 * 7)",
    ]
    assert res["stdout"] == "42\n"

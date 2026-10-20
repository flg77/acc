"""``check_channel`` — the doctor's "am I on the latest?" row.

`20261004-public-release-channels` Phase 6.  Three install shapes (image
mode, a package from a channel, a wheel or checkout) and the preflight
contract: never raises, never BROKEN, no network without ``--probe``, and
"behind" is DRIFTED so it shows without setting the exit code.
"""

from __future__ import annotations

import pytest

import acc.preflight as preflight
from acc.preflight import Context, Severity


@pytest.fixture
def host(monkeypatch):
    """A fake host: which commands exist and what they answer."""
    state = {"bootc": False, "answers": {}, "calls": []}

    def fake_run(argv, timeout):
        state["calls"].append(list(argv))
        key = argv[0] if argv[0] != "dnf" else "dnf " + [a for a in argv[1:] if not a.startswith("-")][0]
        if key not in state["answers"]:
            return None
        return state["answers"][key]

    monkeypatch.setattr(preflight, "_run", fake_run)
    monkeypatch.setattr(preflight, "_bootc_host", lambda: state["bootc"])
    return state


def _only(results):
    results = list(results)
    assert len(results) == 1, results
    return results[0]


def test_registered_and_never_breaks(host):
    assert "channel" in dict(preflight.registry())
    r = _only(preflight.check_channel(Context()))
    assert r.severity is Severity.OK and "no channel" in r.summary          # nothing installed at all


def test_a_wheel_or_checkout_has_no_channel(host):
    host["answers"]["rpm"] = (1, "", "package acc is not installed")
    r = _only(preflight.check_channel(Context()))
    assert r.severity is Severity.OK and "not a packaged install" in r.summary
    assert not any(c[0] == "dnf" for c in host["calls"])                   # dnf is not even asked


def test_a_package_installed_by_file_says_so_instead_of_current(host):
    host["answers"]["rpm"] = (0, "0.26.4-1.el10", "")
    host["answers"]["dnf repolist"] = (0, "fedora\nupdates\n", "")
    r = _only(preflight.check_channel(Context()))
    assert r.severity is Severity.OK
    assert "installed by file" in r.summary and "no channel" in r.summary
    assert "copr enable" in r.detail


@pytest.mark.parametrize("repo", ["copr:copr.fedorainfracloud.org:flg77:acc", "acc-spearhead", "acc-mirror"])
def test_current_with_the_channel(host, repo):
    host["answers"]["rpm"] = (0, "0.26.4-1.el10", "")
    host["answers"]["dnf repolist"] = (0, f"{repo}\nfedora\n", "")
    host["answers"]["dnf check-update"] = (0, "", "")
    r = _only(preflight.check_channel(Context()))
    assert r.severity is Severity.OK and "current" in r.summary and "0.26.4-1.el10" in r.summary


def test_behind_is_drifted_and_names_the_newer_release(host):
    host["answers"]["rpm"] = (0, "0.26.4-1.el10", "")
    host["answers"]["dnf repolist"] = (0, "acc-spearhead\n", "")
    host["answers"]["dnf check-update"] = (100, "acc.x86_64    0.27.0-1.el10    acc-spearhead\n", "")
    r = _only(preflight.check_channel(Context()))
    assert r.severity is Severity.DRIFTED
    assert "behind" in r.summary and "0.27.0-1.el10" in r.summary and "dnf upgrade acc" in r.summary
    assert preflight.exit_code([r]) == 0                                    # visible, never a failure


def test_without_probe_dnf_reads_only_its_cache(host):
    host["answers"]["rpm"] = (0, "0.26.4-1.el10", "")
    host["answers"]["dnf repolist"] = (0, "acc-mirror\n", "")
    host["answers"]["dnf check-update"] = (0, "", "")
    _only(preflight.check_channel(Context(probe_endpoints=False)))
    for call in host["calls"]:
        if call[0] == "dnf":
            assert "-C" in call, call
    host["calls"].clear()
    _only(preflight.check_channel(Context(probe_endpoints=True)))
    for call in host["calls"]:
        if call[0] == "dnf":
            assert "-C" not in call, call


def test_a_channel_that_cannot_be_asked_is_degraded_not_broken(host):
    host["answers"]["rpm"] = (0, "0.26.4-1.el10", "")
    host["answers"]["dnf repolist"] = (0, "acc-mirror\n", "")
    host["answers"]["dnf check-update"] = (1, "", "Error: Failed to download metadata for repo 'acc-mirror'")
    r = _only(preflight.check_channel(Context()))
    assert r.severity is Severity.DEGRADED and "could not be asked" in r.summary
    assert "metadata cache only" in r.summary                                # how to get a real answer
    assert "Failed to download" in r.detail
    assert preflight.exit_code([r]) == 0


def test_image_mode_without_probe_does_not_touch_the_registry(host):
    host["bootc"] = True
    r = _only(preflight.check_channel(Context()))
    assert r.severity is Severity.OK and "image mode" in r.summary and "--probe" in r.summary
    assert host["calls"] == []


def test_image_mode_with_probe_asks_bootc(host):
    host["bootc"] = True
    host["answers"]["bootc"] = (0, "No update available.", "")
    r = _only(preflight.check_channel(Context(probe_endpoints=True)))
    assert r.severity is Severity.OK and "current" in r.summary
    assert host["calls"] == [["bootc", "upgrade", "--check"]]

    host["answers"]["bootc"] = (0, "Update available for: quay.io/flg77/acc_images:acc-bootc\n  Version: 0.27.0", "")
    r = _only(preflight.check_channel(Context(probe_endpoints=True)))
    assert r.severity is Severity.DRIFTED and "bootc upgrade" in r.summary

    host["answers"]["bootc"] = (1, "", "ERROR: this command requires root")
    r = _only(preflight.check_channel(Context(probe_endpoints=True)))
    assert r.severity is Severity.DEGRADED and "requires root" in r.detail


def test_the_doctor_renders_the_row(host, capsys):
    """End to end through the CLI renderer: the row is there and reads as a row."""
    import argparse

    from acc.cli import doctor_cmd

    host["answers"]["rpm"] = (0, "0.26.4-1.el10", "")
    host["answers"]["dnf repolist"] = (0, "acc-spearhead\n", "")
    host["answers"]["dnf check-update"] = (100, "acc.x86_64 0.27.0-1.el10 acc-spearhead\n", "")
    args = argparse.Namespace(json=False, probe=False, check="channel", quiet=False, paths=False)
    rc = doctor_cmd._cmd_doctor(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRIFT" in out and "channel" in out and "behind" in out

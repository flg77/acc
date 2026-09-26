"""F3 — one external secret source, and the credential broker on a real call path.

`20260926-secrets-from-kubernetes-and-a-live-broker`. Two halves that each looked
finished: a "source interface" marked done that was not in the tree, and an OAuth
broker that nothing called -- an ``auth: oauth`` MCP manifest went out
unauthenticated and said nothing.

Pinned here: the mounted source is read at call time (so a rotated Secret is used
on the next call), the environment stays the default and the fallback, an OAuth
call mints for the person whose task it serves -- and never for anyone else, even
with tasks running concurrently -- and no value reaches a log, a status line or
an error message.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from acc import secret_source as S
from acc.credentials import live
from acc.credentials import broker as broker_mod

SECRET = "sk-test-value-that-must-never-be-logged-1234"


@pytest.fixture
def mount(tmp_path, monkeypatch):
    d = tmp_path / "secrets"
    d.mkdir()
    monkeypatch.setenv(S.SOURCE_VAR, S.MOUNTED)
    monkeypatch.setenv(S.DIR_VAR, str(d))
    return d


@pytest.fixture
def cred_env(tmp_path, monkeypatch, mount):
    """A sealed OAuth store whose key comes from the mounted source."""
    from cryptography.fernet import Fernet

    (mount / live.CRED_KEY_NAME).write_text(Fernet.generate_key().decode() + "\n")
    monkeypatch.setenv(live.CRED_DIR_VAR, str(tmp_path / "oauth"))
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.delenv(live.CRED_KEY_NAME, raising=False)
    return tmp_path / "oauth"


def _connect(person: str, provider: str = "google", *, access: str = "MINTED") -> None:
    token = broker_mod.OAuthToken(
        provider=provider, operator_id=person, access_token=access,
        refresh_token="refresh", expires_at=4_102_444_800.0,  # 2100
    )
    live.token_store().put(token)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


class TestSource:
    def test_env_is_the_default_and_unchanged(self, monkeypatch):
        monkeypatch.delenv(S.SOURCE_VAR, raising=False)
        monkeypatch.setenv("SOME_KEY", "from-env")
        assert S.kind() == S.ENV
        assert S.get("SOME_KEY") == "from-env"
        assert S.origin("SOME_KEY") == S.ENV

    def test_a_mounted_file_wins_and_loses_its_trailing_newline(self, mount, monkeypatch):
        (mount / "SOME_KEY").write_text("from-mount\n")
        monkeypatch.setenv("SOME_KEY", "from-env")
        assert S.get("SOME_KEY") == "from-mount"
        assert S.origin("SOME_KEY") == S.MOUNTED

    def test_a_name_the_mount_lacks_falls_back_to_the_environment(self, mount, monkeypatch):
        monkeypatch.setenv("ONLY_IN_ENV", "from-env")
        assert S.get("ONLY_IN_ENV") == "from-env"
        assert S.origin("ONLY_IN_ENV") == S.ENV
        assert S.get("NOWHERE") == "" and S.origin("NOWHERE") == ""

    def test_rotation_is_seen_on_the_next_read(self, mount):
        (mount / "ROTATING").write_text("old")
        assert S.get("ROTATING") == "old"
        (mount / "ROTATING").write_text("new")
        assert S.get("ROTATING") == "new"

    def test_a_name_cannot_walk_the_filesystem(self, mount, tmp_path):
        (tmp_path / "outside").write_text("not a credential")
        assert S.get("../outside") == ""
        assert S.get("a/b") == ""

    def test_names_skip_the_kubelets_own_entries(self, mount):
        (mount / "ONE").write_text("x")
        (mount / "..data").mkdir()
        (mount / ".hidden").write_text("x")
        assert S.names() == ["ONE"]

    def test_describe_carries_names_never_values(self, mount):
        (mount / "ONE").write_text(SECRET)
        info = S.describe()
        assert info["names"] == ["ONE"] and SECRET not in repr(info)

    def test_an_unknown_source_is_env_and_warns_once(self, monkeypatch, caplog):
        monkeypatch.setenv(S.SOURCE_VAR, "valut")
        S._WARNED.discard("valut")
        with caplog.at_level(logging.WARNING, logger="acc.secret_source"):
            assert S.kind() == S.ENV
            assert S.kind() == S.ENV
        assert sum("valut" in r.getMessage() for r in caplog.records) == 1


# ---------------------------------------------------------------------------
# The call paths read through it, per call
# ---------------------------------------------------------------------------


class TestCallPaths:
    def test_openai_compat_uses_the_rotated_key(self, mount):
        from acc.backends.llm_openai_compat import OpenAICompatBackend

        (mount / "PROVIDER_KEY").write_text("first")
        backend = OpenAICompatBackend("http://unused/v1", "m", api_key_env="PROVIDER_KEY")
        assert backend._headers()["Authorization"] == "Bearer first"
        (mount / "PROVIDER_KEY").write_text("second")
        assert backend._headers()["Authorization"] == "Bearer second"

    def test_an_mcp_static_key_is_read_per_request(self, mount):
        from acc.mcp.manifest import MCPManifest
        from acc.mcp.transports import _auth_headers

        man = MCPManifest(server_id="x", purpose="x", url="http://unused", api_key_env="MCP_KEY")
        (mount / "MCP_KEY").write_text("k1")
        assert asyncio.run(_auth_headers(man, None)) == {"Authorization": "Bearer k1"}
        (mount / "MCP_KEY").write_text("k2")
        assert asyncio.run(_auth_headers(man, None)) == {"Authorization": "Bearer k2"}

    def test_the_client_carries_no_baked_bearer(self, mount):
        """A header set at construction would never rotate."""
        from acc.mcp.manifest import MCPManifest
        from acc.mcp.transports import HTTPTransport

        (mount / "MCP_KEY").write_text("k1")
        tr = HTTPTransport(MCPManifest(server_id="x", purpose="x", url="http://unused",
                                       api_key_env="MCP_KEY"))
        assert "authorization" not in {k.lower() for k in tr._client.headers}
        asyncio.run(tr.close())

    def test_the_egress_broker_reads_the_mount(self, mount):
        from acc.egress import Decision, Destination, headers_for

        (mount / "EGRESS_KEY").write_text(SECRET)
        dest = Destination(host="api.example.com", credential_env="EGRESS_KEY")
        decision = Decision(True, "analyst", "https://api.example.com/x", "", dest, 0.0)
        assert list(headers_for(decision, environ={S.SOURCE_VAR: S.MOUNTED,
                                                   S.DIR_VAR: str(mount)}).values()) \
            == [f"{dest.prefix}{SECRET}"]

    def test_the_sealed_store_key_comes_from_the_source(self, cred_env):
        store = live.token_store()
        assert store is not None


# ---------------------------------------------------------------------------
# The broker, live
# ---------------------------------------------------------------------------


class TestLiveBroker:
    def test_an_oauth_manifest_is_given_the_resolver(self):
        from acc.mcp.manifest import MCPManifest
        from acc.mcp.transports import build_transport

        tr = build_transport(MCPManifest(server_id="g", purpose="x", url="http://unused",
                                         auth="oauth", oauth_provider="google"))
        assert tr._bearer_resolver is not None
        asyncio.run(tr.close())

    def test_oauth_with_no_broker_refuses_instead_of_going_out_bare(self):
        from acc.mcp.errors import MCPTransportError
        from acc.mcp.manifest import MCPManifest
        from acc.mcp.transports import _auth_headers

        man = MCPManifest(server_id="g", purpose="x", url="http://unused",
                          auth="oauth", oauth_provider="google")
        with pytest.raises(MCPTransportError, match="refusing"):
            asyncio.run(_auth_headers(man, None))

    def test_mints_for_the_person_being_served(self, cred_env):
        _connect("tui:alice", access="ALICE-TOKEN")
        resolve = live.bearer_resolver("google")
        with live.serving("tui:alice@sol-01"):
            assert asyncio.run(resolve()) == "ALICE-TOKEN"

    def test_not_connected_names_the_command(self, cred_env):
        from acc.mcp.errors import MCPTransportError

        with live.serving("tui:bob"), pytest.raises(MCPTransportError) as info:
            asyncio.run(live.bearer_resolver("google")())
        assert "acc-cli oauth connect google --for tui:bob" in str(info.value)

    def test_no_requester_is_refused(self, cred_env):
        from acc.mcp.errors import MCPTransportError

        with pytest.raises(MCPTransportError, match="no attributed requester"):
            asyncio.run(live.bearer_resolver("google")())

    def test_concurrent_tasks_never_borrow_each_others_token(self, cred_env):
        _connect("tui:alice", access="ALICE-TOKEN")
        _connect("tui:bob", access="BOB-TOKEN")
        resolve = live.bearer_resolver("google")

        async def as_(who: str, delay: float) -> str:
            with live.serving(who):
                await asyncio.sleep(delay)
                return await resolve()

        async def both():
            return await asyncio.gather(as_("tui:alice", 0.05), as_("tui:bob", 0.01))

        assert asyncio.run(both()) == ["ALICE-TOKEN", "BOB-TOKEN"]

    def test_the_dispatcher_marks_whose_task_a_call_serves(self, monkeypatch):
        import acc.capability_dispatch as cd

        seen = []

        async def fake_one(inv, core, role, **kw):
            seen.append(live.current_person())
            return cd.InvocationOutcome(parsed=inv, ok=True)

        monkeypatch.setattr(cd, "_dispatch_one", fake_one)
        inv = cd.ParsedInvocation(kind="mcp", target="g.list", args={}, raw="")
        asyncio.run(cd.dispatch_invocations([inv], core=None, role=None,
                                            requester="webgui:carol@sol-01"))
        assert seen == ["webgui:carol"]
        assert live.current_person() == "", "the mark does not outlive the call"


# ---------------------------------------------------------------------------
# acc-cli oauth
# ---------------------------------------------------------------------------


def _cli(*argv: str) -> int:
    from acc.cli import main

    try:
        return main(list(argv)) or 0
    except SystemExit as exc:
        return int(exc.code or 0)


class TestOAuthCLI:
    def test_connect_prints_a_consent_url_and_seals_the_verifier(self, cred_env, capsys):
        assert _cli("oauth", "connect", "google", "--for", "tui:alice") == 0
        out = capsys.readouterr().out
        assert "accounts.google.com" in out and "code_challenge=" in out
        (pending,) = list(cred_env.glob("*.pending"))
        assert b"verifier" not in pending.read_bytes(), "the verifier is sealed"

    def test_complete_exchanges_and_connects(self, cred_env, capsys, monkeypatch):
        async def fake_token_request(url, data):
            assert data["grant_type"] == "authorization_code" and data["code_verifier"]
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

        monkeypatch.setattr(broker_mod, "_token_request", fake_token_request)
        _cli("oauth", "connect", "google", "--for", "tui:alice")
        assert _cli("oauth", "complete", "google", "--code", "the-code",
                    "--for", "tui:alice") == 0
        assert _cli("oauth", "status", "google", "--for", "tui:alice") == 0
        out = capsys.readouterr().out
        assert "connected" in out and "AT" not in out.replace("CONNECTED", "")
        assert list(cred_env.glob("*.pending")) == [], "the verifier is single use"

    def test_complete_without_connect_is_refused(self, cred_env):
        assert _cli("oauth", "complete", "google", "--code", "x", "--for", "tui:zed") == 1

    def test_status_and_disconnect(self, cred_env):
        _connect("tui:alice")
        assert _cli("oauth", "status", "google", "--for", "tui:alice") == 0
        assert _cli("oauth", "disconnect", "google", "--for", "tui:alice") == 0
        assert _cli("oauth", "status", "google", "--for", "tui:alice") == 1

    def test_no_store_key_is_a_legible_refusal(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv(S.SOURCE_VAR, raising=False)
        monkeypatch.delenv(live.CRED_KEY_NAME, raising=False)
        monkeypatch.setenv(live.CRED_DIR_VAR, str(tmp_path / "oauth"))
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
        assert _cli("oauth", "connect", "google", "--for", "tui:alice") == 1
        assert "ACC_CRED_KEY" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestDoctor:
    def _run(self, environ, only):
        from acc import preflight
        from acc.preflight import Context

        return preflight.run(Context(environ=environ), only=only)

    def test_env_source(self):
        from acc.preflight import Severity

        (r,) = self._run({}, "secrets")
        assert r.severity is Severity.OK and "environment" in r.summary

    def test_a_mounted_source_with_no_directory_is_broken(self, tmp_path):
        from acc.preflight import Severity

        (r,) = self._run({S.SOURCE_VAR: S.MOUNTED, S.DIR_VAR: str(tmp_path / "nope")}, "secrets")
        assert r.severity is Severity.BROKEN

    def test_a_mounted_source_says_what_it_does_not_protect(self, mount):
        from acc.preflight import Severity

        (mount / "ONE").write_text(SECRET)
        (r,) = self._run({S.SOURCE_VAR: S.MOUNTED, S.DIR_VAR: str(mount)}, "secrets")
        assert r.severity is Severity.OK and "1 name" in r.summary
        assert "filesystem" in r.detail and SECRET not in r.summary + r.detail


# ---------------------------------------------------------------------------
# No value leaks
# ---------------------------------------------------------------------------


def test_no_value_reaches_a_log(mount, caplog):
    from acc.backends.llm_openai_compat import OpenAICompatBackend
    from acc.mcp.manifest import MCPManifest
    from acc.mcp.transports import HTTPTransport

    (mount / "LEAKY").write_text(SECRET)
    with caplog.at_level(logging.DEBUG):
        OpenAICompatBackend("http://unused/v1", "m", api_key_env="LEAKY")._headers()
        tr = HTTPTransport(MCPManifest(server_id="x", purpose="x", url="http://unused",
                                       api_key_env="LEAKY"))
        asyncio.run(tr.close())
        S.describe()
    assert SECRET not in caplog.text

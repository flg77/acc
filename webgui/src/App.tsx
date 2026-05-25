// acc-webgui application shell — navigation, collective switcher, and
// the screen router.  Mirrors the acc-tui app shell (acc/tui/app.py):
// a nav bar + per-collective data + the 8 parity screens, with the
// enhanced-tracing views added.

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  getAuthInfo,
  getToken,
  isAuthError,
  listCollectives,
  login,
  setToken,
} from "./api/client";
import { SnapshotProvider, useSnapshot } from "./state/snapshot";
import {
  Dashboard,
  Infuse,
  Prompt,
  Compliance,
  Ecosystem,
  Performance,
  Comms,
  Configuration,
  Diagnostics,
  Help,
} from "./screens";
import { TraceWaterfall, PlanDag, AuditTimeline } from "./tracing";

const SCREENS: Record<string, () => JSX.Element> = {
  Dashboard,
  Infuse,
  Prompt,
  Compliance,
  Ecosystem,
  Performance,
  Comms,
  Configuration,
  Diagnostics,
  Help,
  "Trace · Waterfall": TraceWaterfall,
  "Trace · PLAN DAG": PlanDag,
  "Trace · Audit chain": AuditTimeline,
};

function StatusBadge() {
  const { connected } = useSnapshot();
  return (
    <span className={connected ? "badge live" : "badge stale"}>
      {connected ? "live" : "connecting…"}
    </span>
  );
}

// `token` mode — paste a static bearer token.
function TokenGate({
  rejected,
  onSubmit,
}: {
  rejected: boolean;
  onSubmit: (token: string) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <div className="token-gate">
      <h1>acc-webgui</h1>
      <p>This acc-webgui requires a bearer token to connect.</p>
      {rejected && (
        <p className="errmsg">
          The saved token was rejected — paste a current one.
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onSubmit(value.trim());
        }}
      >
        <input
          type="password"
          placeholder="operator or viewer token"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
        />
        <button type="submit" disabled={!value.trim()}>
          Connect
        </button>
      </form>
      <p className="hint">
        The token is the value of <code>ACC_WEBGUI_OPERATOR_TOKEN</code> or
        <code> ACC_WEBGUI_VIEWER_TOKEN</code> on the acc-webgui container. It
        is kept in this browser's localStorage; you can also open
        <code> localhost:8080/?token=…</code> directly.
      </p>
    </div>
  );
}

// `htpasswd` mode — username/password login → a signed session token.
function LoginGate({
  rejected,
  onAuthed,
}: {
  rejected: boolean;
  onAuthed: () => void;
}) {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(
    rejected ? "Your session expired — sign in again." : "",
  );

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!user.trim() || !pass || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await login(user.trim(), pass);
      setToken(r.token);
      onAuthed();
    } catch (e2) {
      setErr(
        isAuthError(e2)
          ? "Invalid username or password."
          : `Login failed: ${e2 instanceof Error ? e2.message : String(e2)}`,
      );
      setBusy(false);
    }
  };

  return (
    <div className="token-gate">
      <h1>acc-webgui</h1>
      <p>Sign in to acc-webgui.</p>
      {err && <p className="errmsg">{err}</p>}
      <form className="login-form" onSubmit={submit}>
        <input
          type="text"
          placeholder="username"
          value={user}
          onChange={(e) => setUser(e.target.value)}
          autoFocus
        />
        <input
          type="password"
          placeholder="password"
          value={pass}
          onChange={(e) => setPass(e.target.value)}
        />
        <button type="submit" disabled={busy || !user.trim() || !pass}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="hint">
        Credentials are your entry in the acc-webgui htpasswd file. A signed
        session is kept in this browser's localStorage.
      </p>
    </div>
  );
}

// oauth-proxy / oidc / mtls — the SPA cannot itself supply a proxy
// header or client certificate; it can only tell the operator why.
function ProxyAuthError({ mode }: { mode: string }) {
  return (
    <div className="token-gate">
      <h1>acc-webgui</h1>
      <p className="errmsg">Not authenticated.</p>
      <p className="hint">
        This acc-webgui uses <code>{mode}</code> authentication. Sign in
        through your identity provider / proxy (or present a valid client
        certificate) and reload this page.
      </p>
    </div>
  );
}

type Boot =
  | { state: "checking" }
  | { state: "need-auth"; mode: string; rejected: boolean }
  | { state: "error"; message: string }
  | { state: "ready"; collectives: string[] };

export default function App() {
  const [boot, setBoot] = useState<Boot>({ state: "checking" });
  const [activeCid, setActiveCid] = useState<string>("");
  const [screen, setScreen] = useState<string>("Dashboard");

  // Probe the backend: discover the auth mode, then list collectives.
  // A 401/403 means we lack a valid credential → show the gate for the
  // active mode; any other failure surfaces as an error with a Retry.
  const bootstrap = useCallback(async () => {
    setBoot({ state: "checking" });
    let mode = "none";
    try {
      mode = (await getAuthInfo()).mode;
    } catch {
      /* auth-info unreachable — the collectives probe surfaces the real error */
    }
    try {
      const r = await listCollectives();
      setBoot({ state: "ready", collectives: r.collectives });
      if (r.collectives.length > 0) {
        setActiveCid((cur) => cur || r.collectives[0]);
      }
    } catch (err) {
      if (isAuthError(err)) {
        setBoot({ state: "need-auth", mode, rejected: getToken() != null });
      } else {
        setBoot({
          state: "error",
          message: err instanceof Error ? err.message : String(err),
        });
      }
    }
  }, []);

  useEffect(() => {
    // A token may arrive as ?token=… (a bookmarkable deep link).  Store
    // it, then strip it from the URL so the secret is not left sitting
    // in the address bar / browser history.
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token");
    if (urlToken) {
      setToken(urlToken);
      params.delete("token");
      const qs = params.toString();
      window.history.replaceState(
        {},
        "",
        window.location.pathname + (qs ? `?${qs}` : ""),
      );
    }
    bootstrap();
  }, [bootstrap]);

  if (boot.state === "checking") {
    return <div className="loading">Connecting to acc-webgui…</div>;
  }

  if (boot.state === "need-auth") {
    if (boot.mode === "token") {
      return (
        <TokenGate
          rejected={boot.rejected}
          onSubmit={(token) => {
            setToken(token);
            bootstrap();
          }}
        />
      );
    }
    if (boot.mode === "htpasswd") {
      return <LoginGate rejected={boot.rejected} onAuthed={bootstrap} />;
    }
    return <ProxyAuthError mode={boot.mode} />;
  }

  if (boot.state === "error") {
    return (
      <div className="loading">
        <p className="errmsg">Could not reach acc-webgui: {boot.message}</p>
        <button onClick={bootstrap}>Retry</button>
      </div>
    );
  }

  if (boot.collectives.length === 0) {
    return (
      <div className="loading">
        Connected — but no collectives are being observed.
        <br />
        Check <code>ACC_COLLECTIVE_IDS</code> on the acc-webgui container.
      </div>
    );
  }

  const cid = activeCid || boot.collectives[0];
  const Screen = SCREENS[screen] ?? Dashboard;

  return (
    <SnapshotProvider collectiveId={cid}>
      <div className="app">
        <header>
          <h1>acc-webgui</h1>
          <select value={cid} onChange={(e) => setActiveCid(e.target.value)}>
            {boot.collectives.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <StatusBadge />
        </header>
        <nav>
          {Object.keys(SCREENS).map((name) => (
            <button
              key={name}
              className={name === screen ? "active" : ""}
              onClick={() => setScreen(name)}
            >
              {name}
            </button>
          ))}
        </nav>
        <main>
          <Screen />
        </main>
      </div>
    </SnapshotProvider>
  );
}

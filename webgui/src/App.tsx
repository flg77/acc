// acc-webgui application shell — boot (auth probe, collectives, environment),
// then the PatternFly shell (src/shell) with its routed pages.
//
// The screens that have not been rebuilt on PatternFly yet are still the ones
// in screens.tsx / tracing.tsx; src/shell/sections.tsx maps every one of them
// into the eight-section navigation.

import { useCallback, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { HashRouter } from "react-router-dom";
import {
  fetchEnvironment,
  fetchWhoami,
  getAuthInfo,
  getToken,
  isAuthError,
  listCollectives,
  login,
  setToken,
} from "./api/client";
import type { Environment, Whoami } from "./api/client";
import { SnapshotProvider } from "./state/snapshot";
import { EnvironmentContext } from "./shell/EnvironmentContext";
import { Shell } from "./shell/Shell";

// The gates and the boot messages are on the legacy stylesheet (src/styles.css).
const Legacy = ({ children }: { children: ReactNode }) => (
  <div className="legacy">{children}</div>
);

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
    <Legacy>
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
    </Legacy>
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
    <Legacy>
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
    </Legacy>
  );
}

// oauth-proxy / oidc / mtls — the SPA cannot itself supply a proxy
// header or client certificate; it can only tell the operator why.
function ProxyAuthError({ mode }: { mode: string }) {
  return (
    <Legacy>
      <div className="token-gate">
        <h1>acc-webgui</h1>
        <p className="errmsg">Not authenticated.</p>
        <p className="hint">
          This acc-webgui uses <code>{mode}</code> authentication. Sign in
          through your identity provider / proxy (or present a valid client
          certificate) and reload this page.
        </p>
      </div>
    </Legacy>
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
  const [env, setEnv] = useState<Environment | null>(null);
  const [who, setWho] = useState<Whoami | null>(null);

  useEffect(() => {
    if (boot.state !== "ready") return;
    fetchEnvironment().then(setEnv).catch(() => setEnv(null));
    fetchWhoami().then(setWho).catch(() => setWho(null));
  }, [boot.state]);

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
    return (
      <Legacy>
        <div className="loading">Connecting to acc-webgui…</div>
      </Legacy>
    );
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
      <Legacy>
        <div className="loading">
          <p className="errmsg">Could not reach acc-webgui: {boot.message}</p>
          <button onClick={bootstrap}>Retry</button>
        </div>
      </Legacy>
    );
  }

  if (boot.collectives.length === 0) {
    return (
      <Legacy>
        <div className="loading">
          Connected — but no collectives are being observed.
          <br />
          Check <code>ACC_COLLECTIVE_IDS</code> on the acc-webgui container.
        </div>
      </Legacy>
    );
  }

  const cid = activeCid || boot.collectives[0];

  return (
    <EnvironmentContext.Provider value={{ env, who }}>
      <SnapshotProvider collectiveId={cid}>
        <HashRouter>
          <Shell
            collectives={boot.collectives}
            collectiveId={cid}
            onCollective={setActiveCid}
          />
        </HashRouter>
      </SnapshotProvider>
    </EnvironmentContext.Provider>
  );
}

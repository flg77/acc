// acc-webgui application shell — navigation, collective switcher, and
// the screen router.  Mirrors the acc-tui app shell (acc/tui/app.py):
// a nav bar + per-collective data + the 8 parity screens, with the
// enhanced-tracing views added.

import { useCallback, useEffect, useState } from "react";
import { getToken, isAuthError, listCollectives, setToken } from "./api/client";
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

// The token-entry gate, shown when the backend answers 401/403 — i.e.
// it runs in `token` auth mode and this browser has no valid token.
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

type Boot =
  | { state: "checking" }
  | { state: "need-token"; rejected: boolean }
  | { state: "error"; message: string }
  | { state: "ready"; collectives: string[] };

export default function App() {
  const [boot, setBoot] = useState<Boot>({ state: "checking" });
  const [activeCid, setActiveCid] = useState<string>("");
  const [screen, setScreen] = useState<string>("Dashboard");

  // Probe the backend: list collectives.  A 401/403 means token auth is
  // on and we lack a valid token → show the gate instead of spinning
  // forever; any other failure surfaces as an error with a Retry.
  const bootstrap = useCallback(async () => {
    setBoot({ state: "checking" });
    try {
      const r = await listCollectives();
      setBoot({ state: "ready", collectives: r.collectives });
      if (r.collectives.length > 0) {
        setActiveCid((cur) => cur || r.collectives[0]);
      }
    } catch (err) {
      if (isAuthError(err)) {
        setBoot({ state: "need-token", rejected: getToken() != null });
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

  if (boot.state === "need-token") {
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

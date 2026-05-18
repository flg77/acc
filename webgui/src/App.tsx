// acc-webgui application shell — navigation, collective switcher, and
// the screen router.  Mirrors the acc-tui app shell (acc/tui/app.py):
// a nav bar + per-collective data + the 8 parity screens, with the
// enhanced-tracing views added.

import { useEffect, useState } from "react";
import { listCollectives } from "./api/client";
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

export default function App() {
  const [collectives, setCollectives] = useState<string[]>([]);
  const [activeCid, setActiveCid] = useState<string>("");
  const [screen, setScreen] = useState<string>("Dashboard");

  useEffect(() => {
    listCollectives()
      .then((r) => {
        setCollectives(r.collectives);
        if (r.collectives.length > 0) setActiveCid(r.collectives[0]);
      })
      .catch(() => {});
  }, []);

  if (!activeCid) return <div className="loading">Connecting to acc-webgui…</div>;

  const Screen = SCREENS[screen] ?? Dashboard;

  return (
    <SnapshotProvider collectiveId={activeCid}>
      <div className="app">
        <header>
          <h1>acc-webgui</h1>
          <select value={activeCid} onChange={(e) => setActiveCid(e.target.value)}>
            {collectives.map((c) => (
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

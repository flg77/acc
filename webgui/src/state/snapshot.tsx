// Live CollectiveSnapshot context — the shared data layer of the SPA.
//
// One WebSocket per selected collective; every screen reads the latest
// snapshot from this context.  This mirrors the TUI, where every screen
// reads the NATSObserver's CollectiveSnapshot — feature parity is
// structural (proposal §4).

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { openSnapshotStream, fetchSnapshot, type Snapshot } from "../api/client";

interface SnapshotState {
  collectiveId: string;
  snapshot: Snapshot | null;
  connected: boolean;
}

const SnapshotContext = createContext<SnapshotState>({
  collectiveId: "",
  snapshot: null,
  connected: false,
});

export function SnapshotProvider({
  collectiveId,
  children,
}: {
  collectiveId: string;
  children: ReactNode;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    setSnapshot(null);
    // Seed with the REST point-in-time fetch so the UI renders before
    // the first WebSocket push arrives.
    fetchSnapshot(collectiveId)
      .then((r) => r.snapshot && setSnapshot(r.snapshot))
      .catch(() => {});
    const close = openSnapshotStream(collectiveId, (snap) => {
      setSnapshot(snap);
      setConnected(true);
    });
    return () => {
      close();
      setConnected(false);
    };
  }, [collectiveId]);

  return (
    <SnapshotContext.Provider value={{ collectiveId, snapshot, connected }}>
      {children}
    </SnapshotContext.Provider>
  );
}

export const useSnapshot = () => useContext(SnapshotContext);

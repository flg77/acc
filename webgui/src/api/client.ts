// REST + WebSocket client for the acc-webgui FastAPI backend.
//
// The backend reuses acc-tui's NATSObserver, so the shapes here mirror
// `acc.tui.models.CollectiveSnapshot` (loosely typed — the snapshot
// evolves with the TUI data model, and the web frontend renders
// whatever fields are present).

export type Snapshot = Record<string, any>;

// A bearer token may be supplied (token-auth tier); oauth-proxy and
// OIDC modes carry the session via cookies / proxy headers instead.
let bearerToken: string | null =
  typeof localStorage !== "undefined" ? localStorage.getItem("acc.webgui.token") : null;

export function setToken(token: string | null): void {
  bearerToken = token;
  if (typeof localStorage !== "undefined") {
    if (token) localStorage.setItem("acc.webgui.token", token);
    else localStorage.removeItem("acc.webgui.token");
  }
}

export function getToken(): string | null {
  return bearerToken;
}

// True when the backend rejected the request for lack of a valid token.
// The app shell uses this to show the token-entry gate instead of an
// indefinite "Connecting…" spinner.
export function isAuthError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return msg.startsWith("401") || msg.startsWith("403");
}

function authHeaders(): Record<string, string> {
  return bearerToken ? { Authorization: `Bearer ${bearerToken}` } : {};
}

async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: { ...authHeaders() } });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json() as Promise<T>;
}

// --- auth -------------------------------------------------------------------

// Unauthenticated probe — tells the SPA which login gate to render.
export const getAuthInfo = () => getJSON<{ mode: string }>("/api/auth-info");

// htpasswd-mode login: exchange username/password for a session token.
// Throws "401: ..." on bad credentials (see isAuthError).
export const login = (username: string, password: string) =>
  postJSON<{ token: string; user: string; role: string }>("/api/login", {
    username,
    password,
  });

// --- read ------------------------------------------------------------------

export const listCollectives = () =>
  getJSON<{ collectives: string[] }>("/api/collectives");

export const fetchSnapshot = (cid: string) =>
  getJSON<{ collective_id: string; snapshot: Snapshot | null }>(
    `/api/snapshot/${encodeURIComponent(cid)}`,
  );

// --- tracing ---------------------------------------------------------------

export const fetchPlanDag = (cid: string) =>
  getJSON<{ active_plans: Record<string, any> }>(
    `/api/trace/plan/${encodeURIComponent(cid)}`,
  );

export const fetchSignalFeed = (cid: string) =>
  getJSON<{ signals: any[] }>(`/api/trace/signals/${encodeURIComponent(cid)}`);

export const fetchAuditTimeline = (limit = 200) =>
  getJSON<{
    records: any[];
    tampered_indices: number[];
    chain_break_indices: number[];
    verified: boolean;
  }>(`/api/trace/audit?limit=${limit}`);

export const searchEpisodes = (cid: string, q: string) =>
  getJSON<{ results: any[] }>(
    `/api/trace/episodes/search?collective_id=${encodeURIComponent(cid)}&q=${encodeURIComponent(q)}`,
  );

// --- actions ---------------------------------------------------------------

export const infuseRole = (cid: string, roleDefinition: unknown) =>
  postJSON("/api/infuse", { collective_id: cid, role_definition: roleDefinition });

export const sendPrompt = (
  cid: string,
  targetRole: string,
  content: string,
  targetAgentId?: string,
) =>
  postJSON<{ task_id: string; output: string; blocked: boolean }>("/api/prompt", {
    collective_id: cid,
    target_role: targetRole,
    content,
    target_agent_id: targetAgentId ?? null,
  });

export const oversightDecision = (
  cid: string,
  oversightId: string,
  decision: "APPROVE" | "REJECT",
  reason = "",
) =>
  postJSON("/api/oversight", {
    collective_id: cid,
    oversight_id: oversightId,
    decision,
    reason,
  });

export const testLLM = (baseUrl: string) =>
  postJSON<{ reachable: boolean; status_code?: number; latency_ms?: number }>(
    "/api/test-llm",
    { base_url: baseUrl },
  );

// --- live WebSocket --------------------------------------------------------

// Opens /ws/{cid}; calls onSnapshot for every CollectiveSnapshot push.
// Auto-reconnects with a short backoff.  Returns a close() function.
export function openSnapshotStream(
  cid: string,
  onSnapshot: (snap: Snapshot) => void,
): () => void {
  let closed = false;
  let ws: WebSocket | null = null;

  const connect = () => {
    if (closed) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    // Browsers cannot set an Authorization header on a WebSocket, so
    // bearer/JWT modes carry the token as a ?token= query param; header
    // modes (oauth-proxy / mtls) have no token and rely on the upgrade
    // request's headers.
    const tok = getToken();
    const q = tok ? `?token=${encodeURIComponent(tok)}` : "";
    ws = new WebSocket(
      `${proto}://${location.host}/ws/${encodeURIComponent(cid)}${q}`,
    );
    ws.onmessage = (ev) => {
      try {
        onSnapshot(JSON.parse(ev.data));
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      if (!closed) setTimeout(connect, 2000);
    };
  };
  connect();

  return () => {
    closed = true;
    ws?.close();
  };
}

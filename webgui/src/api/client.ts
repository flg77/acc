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

// --- governance / compliance / diagnostics / models (PR-W parity) ---------

export const fetchGovernanceLayers = () =>
  getJSON<{ layers: any[] }>("/api/governance/layers");

export const fetchFrameworks = () =>
  getJSON<{ frameworks: any[] }>("/api/governance/frameworks");

export const fetchProposals = () =>
  getJSON<{ proposals: any[] }>("/api/governance/proposals");

export const fetchGoldenPrompts = () =>
  getJSON<{ prompts: any[] }>("/api/diagnostics/golden");

// --- diagnostics eval-history (proposal G WebGUI parity) -------------------

export type GoldenRun = {
  run_id: string;
  prompt_name: string;
  run_ts: number;
  task_id: string;
  passed: boolean;
  elapsed_ms: number;
  failures: string[];
  error: string;
  output_excerpt: string;
  input_tokens: number;
  cache_read_tokens: number;
  compliance_health_score: number;
  eval_verdict: string;
  mlflow_trace_url: string | null;
};

export const fetchGoldenDetail = (name: string) =>
  getJSON<{ prompt: any; definition_of_good: string[] }>(
    `/api/diagnostics/golden/${encodeURIComponent(name)}`,
  );

export const fetchGoldenHistory = (name: string, limit = 20) =>
  getJSON<{ name: string; runs: GoldenRun[]; versions: number[] }>(
    `/api/diagnostics/golden/${encodeURIComponent(name)}/history?limit=${limit}`,
  );

export const runGolden = (name: string, cid: string, timeoutS = 180) =>
  postJSON<GoldenRun>(`/api/diagnostics/golden/${encodeURIComponent(name)}/run`, {
    collective_id: cid,
    timeout_s: timeoutS,
  });

export const promoteGolden = (name: string) =>
  postJSON<{ status: string; role: string; eval_name: string; path: string }>(
    `/api/diagnostics/golden/${encodeURIComponent(name)}/promote`,
    {},
  );

export const fetchModels = () => getJSON<{ models: any[] }>("/api/models");

export const runGapScan = (frameworkId: string) =>
  postJSON<{
    framework_id: string;
    coverage_pct: number;
    gaps: number;
    proposals: number;
    mode: string;
  }>("/api/governance/gap-scan", { framework_id: frameworkId });

export const decideProposal = (
  proposalId: string,
  decision: "approve" | "reject",
) =>
  postJSON(
    `/api/governance/proposals/${encodeURIComponent(proposalId)}/decision`,
    { decision },
  );

// --- marketplace / catalogs / role authoring (WS-C, proposal 020) ----------

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json() as Promise<T>;
}

async function deleteJSON<T>(path: string): Promise<T> {
  const resp = await fetch(path, {
    method: "DELETE",
    headers: { ...authHeaders() },
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json() as Promise<T>;
}

async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json() as Promise<T>;
}

export type MarketRow = {
  name: string;
  version: string;
  tier: string;
  tier_badge: string;
  catalog_id: string;
  catalog_mode: string;
  signer: string;
  install_marker: string;
};

export const fetchAvailableRoles = (filter = "") =>
  getJSON<MarketRow[]>(
    `/api/roles/available${filter ? `?filter=${encodeURIComponent(filter)}` : ""}`,
  );

export const installRole = (name: string, constraint?: string) =>
  postJSON<{
    install_marker: string;
    target_name: string;
    target_constraint: string;
  }>("/api/roles/install", { name, constraint: constraint ?? null });

export type CatalogRow = {
  id: string;
  tier: string;
  mode: string;
  url: string;
  path: string;
  required_signer: { issuer: string; subject_pattern: string; key_path: string };
  priority: number;
};

export const fetchCatalogs = () => getJSON<CatalogRow[]>("/api/catalogs");

export const addCatalog = (body: {
  catalog_id: string;
  tier: string;
  mode: string;
  url?: string;
  path?: string;
  issuer: string;
  subject_pattern: string;
  key_path?: string;
  priority?: number;
}) => postJSON<{ action: string; catalog_id: string; path: string }>(
  "/api/catalogs",
  body,
);

export const removeCatalog = (catalogId: string) =>
  deleteJSON<{ action: string; catalog_id: string; path: string }>(
    `/api/catalogs/${encodeURIComponent(catalogId)}`,
  );

export const setCatalogPriority = (catalogId: string, priority: number) =>
  patchJSON<{ action: string; catalog_id: string; priority: number }>(
    `/api/catalogs/${encodeURIComponent(catalogId)}`,
    { priority },
  );

// --- role authoring (WS-C1/C2) ---------------------------------------------

export type RoleRow = { role_id: string; has_md: boolean };

export const listRoles = () => getJSON<RoleRow[]>("/api/roles");

export const getRoleYaml = (roleId: string) =>
  getJSON<{ role_id: string; yaml_text: string }>(
    `/api/roles/${encodeURIComponent(roleId)}/yaml`,
  );

export const getRoleMd = (roleId: string) =>
  getJSON<{ role_id: string; md_text: string }>(
    `/api/roles/${encodeURIComponent(roleId)}/md`,
  );

export const putRoleYaml = (roleId: string, yamlText: string) =>
  putJSON<{ role_id: string; action: string }>(
    `/api/roles/${encodeURIComponent(roleId)}/yaml`,
    { yaml_text: yamlText },
  );

export const putRoleMd = (roleId: string, mdText: string) =>
  putJSON<{ role_id: string; action: string }>(
    `/api/roles/${encodeURIComponent(roleId)}/md`,
    { md_text: mdText },
  );

export const createRole = (
  roleId: string,
  yamlText: string,
  mdText = "",
) =>
  postJSON<{ role_id: string; action: string }>("/api/roles", {
    role_id: roleId,
    yaml_text: yamlText,
    md_text: mdText,
  });

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

// The 8 feature-parity screens (proposal §4.4) — each mirrors an
// acc-tui screen under acc/tui/screens/.  Every screen reads the live
// CollectiveSnapshot from the shared context; the interactive ones
// (Infuse, Prompt, Compliance, Configuration) also call the action API.

import { useEffect, useState } from "react";
import { useSnapshot } from "./state/snapshot";
import { Card, KV, DataTable, Empty } from "./common";
import {
  infuseRole,
  sendPrompt,
  oversightDecision,
  testLLM,
  fetchGovernanceLayers,
  fetchFrameworks,
  fetchProposals,
  fetchGoldenPrompts,
  fetchGoldenDetail,
  fetchGoldenHistory,
  runGolden,
  promoteGolden,
  fetchModels,
  runGapScan,
  decideProposal,
  fetchAvailableRoles,
  installRole,
  fetchCatalogs,
  addCatalog,
  removeCatalog,
  setCatalogPriority,
  listRoles,
  getRoleYaml,
  getRoleMd,
  putRoleYaml,
  putRoleMd,
  createRole,
} from "./api/client";
import type { MarketRow, CatalogRow, RoleRow, GoldenRun } from "./api/client";

const obj = (v: unknown): Record<string, any> =>
  v && typeof v === "object" ? (v as Record<string, any>) : {};
const arr = (v: unknown): any[] => (Array.isArray(v) ? v : []);

// 1 ── Dashboard / Soma — read-only live metrics ──────────────────────────
export function Dashboard() {
  const { snapshot } = useSnapshot();
  if (!snapshot) return <Empty what="dashboard data" />;
  const agents = obj(snapshot.agents);
  return (
    <>
      <Card title="Collective">
        <KV
          data={{
            collective_id: snapshot.collective_id,
            agents: Object.keys(agents).length,
            compliance_health: snapshot.compliance_health_score,
            icl_episodes: snapshot.icl_episode_count,
            patterns: snapshot.pattern_count,
          }}
        />
      </Card>
      <Card title="Agents">
        <DataTable
          columns={["agent_id", "role", "state", "drift", "compliance"]}
          rows={Object.entries(agents).map(([id, a]) => ({
            agent_id: id,
            role: obj(a).role,
            state: obj(a).state,
            drift: obj(a).drift_score,
            compliance: obj(a).compliance_score,
          }))}
        />
      </Card>
    </>
  );
}

// 2 ── Nucleus / Infuse — role-composition form (interactive) ─────────────
export function Infuse() {
  const { collectiveId } = useSnapshot();
  const [roleId, setRoleId] = useState("");
  const [purpose, setPurpose] = useState("");
  const [persona, setPersona] = useState("concise");
  const [status, setStatus] = useState("");

  const apply = async () => {
    setStatus("publishing…");
    try {
      await infuseRole(collectiveId, { id: roleId, purpose, persona });
      setStatus("ROLE_UPDATE published — awaiting arbiter approval");
    } catch (e) {
      setStatus(`error: ${e}`);
    }
  };
  return (
    <Card title="Infuse a role">
      <label>
        Role id
        <input value={roleId} onChange={(e) => setRoleId(e.target.value)} />
      </label>
      <label>
        Purpose
        <textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} />
      </label>
      <label>
        Persona
        <select value={persona} onChange={(e) => setPersona(e.target.value)}>
          <option>concise</option>
          <option>thorough</option>
          <option>creative</option>
        </select>
      </label>
      <button onClick={apply} disabled={!roleId}>
        Apply role
      </button>
      <p className="status">{status}</p>
    </Card>
  );
}

// 3 ── Prompt — operator↔agent chat (interactive) ─────────────────────────
export function Prompt() {
  const { collectiveId } = useSnapshot();
  const [role, setRole] = useState("analyst");
  const [text, setText] = useState("");
  const [log, setLog] = useState<string[]>([]);

  const send = async () => {
    setLog((l) => [...l, `▶ ${text}`]);
    const prompt = text;
    setText("");
    try {
      const r = await sendPrompt(collectiveId, role, prompt);
      setLog((l) => [...l, `◀ [${r.task_id.slice(0, 8)}] ${r.output}`]);
    } catch (e) {
      setLog((l) => [...l, `✗ ${e}`]);
    }
  };
  return (
    <Card title="Prompt">
      <div className="transcript">
        {log.length === 0 && <Empty what="messages" />}
        {log.map((line, i) => (
          <div key={i} className="line">
            {line}
          </div>
        ))}
      </div>
      <label>
        Target role
        <input value={role} onChange={(e) => setRole(e.target.value)} />
      </label>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends; Shift+Enter inserts a newline (mirrors the TUI).
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (text.trim()) send();
          }
        }}
        placeholder="Ask the collective…  (Enter to send, Shift+Enter for a newline)"
      />
    </Card>
  );
}

// 4 ── Compliance — OWASP grading + governance layers + frameworks +
//      gap analysis + rule proposals + oversight queue (interactive).
//      Mirrors the latest acc-tui Compliance pane (PR-Z1/Z2/Z3).
export function Compliance() {
  const { collectiveId, snapshot } = useSnapshot();
  const [msg, setMsg] = useState("");
  const [layers, setLayers] = useState<any[]>([]);
  const [frameworks, setFrameworks] = useState<any[]>([]);
  const [proposals, setProposals] = useState<any[]>([]);
  const [scanMsg, setScanMsg] = useState("");

  const loadGovernance = () => {
    fetchGovernanceLayers().then((r) => setLayers(r.layers)).catch(() => {});
    fetchFrameworks().then((r) => setFrameworks(r.frameworks)).catch(() => {});
    fetchProposals().then((r) => setProposals(r.proposals)).catch(() => {});
  };
  useEffect(loadGovernance, []);

  const pending = arr(snapshot?.oversight_pending_items);
  const decide = async (id: string, decision: "APPROVE" | "REJECT") => {
    try {
      await oversightDecision(collectiveId, id, decision);
      setMsg(`${decision} sent for ${id}`);
    } catch (e) {
      setMsg(`error: ${e}`);
    }
  };
  const scan = async (fwId: string) => {
    setScanMsg(`scanning ${fwId}…`);
    try {
      const r = await runGapScan(fwId);
      setScanMsg(
        `${fwId}: ${r.coverage_pct}% covered, ${r.gaps} gaps → ` +
          `${r.proposals} proposal(s) [${r.mode}]`,
      );
      loadGovernance();
    } catch (e) {
      setScanMsg(`error: ${e}`);
    }
  };
  const decideProp = async (id: string, decision: "approve" | "reject") => {
    try {
      await decideProposal(id, decision);
      loadGovernance();
    } catch (e) {
      setScanMsg(`error: ${e}`);
    }
  };

  return (
    <>
      <Card title="Compliance health">
        <KV data={{ score: snapshot?.compliance_health_score }} />
      </Card>

      {/* Governance layers — Cat A/B/C (PR-Z1) */}
      {layers.map((l: any) => (
        <Card
          key={l.category}
          title={`Cat ${l.category} — ${l.title} ${
            l.version ? "v" + l.version : ""
          } (${l.rule_count} rules)${l.immutable ? " 🔒" : ""}`}
        >
          <DataTable
            columns={["rule_id", "summary"]}
            rows={arr(l.rules).map((r: any) => ({
              rule_id: r.rule_id,
              summary: r.summary,
            }))}
          />
        </Card>
      ))}

      {/* Frameworks + gap scan (PR-Z2) */}
      <Card title="Frameworks — gap analysis">
        {frameworks.length === 0 && <Empty what="frameworks" />}
        {frameworks.map((f: any) => (
          <div key={f.framework_id} className="oversight-row">
            <span>{f.framework_id}</span>
            <span>{f.name} ({f.control_count} controls)</span>
            <button onClick={() => scan(f.framework_id)}>Run gap scan</button>
          </div>
        ))}
        <p className="status">{scanMsg}</p>
      </Card>

      {/* Rule proposals (PR-Z3) */}
      <Card title="Rule proposals">
        {proposals.length === 0 && <Empty what="rule proposals" />}
        {proposals.map((p: any) => (
          <div key={p.proposal_id} className="oversight-row">
            <span>{p.proposal_id?.slice(0, 8)}</span>
            <span>
              {p.source} · {p.category} · {p.severity} · {p.status}
            </span>
            {p.status === "PROPOSED" && (
              <>
                <button onClick={() => decideProp(p.proposal_id, "approve")}>
                  Approve
                </button>
                <button onClick={() => decideProp(p.proposal_id, "reject")}>
                  Reject
                </button>
              </>
            )}
          </div>
        ))}
      </Card>

      <Card title="Human-oversight queue">
        {pending.length === 0 && <Empty what="pending oversight items" />}
        {pending.map((it: any) => (
          <div key={it.oversight_id} className="oversight-row">
            <span>{it.oversight_id}</span>
            <span>{it.summary ?? it.task_type}</span>
            <button onClick={() => decide(it.oversight_id, "APPROVE")}>
              Approve
            </button>
            <button onClick={() => decide(it.oversight_id, "REJECT")}>
              Reject
            </button>
          </div>
        ))}
        <p className="status">{msg}</p>
      </Card>

      <Card title="OWASP violation log">
        <DataTable
          columns={["owasp_code", "risk_level", "pattern", "source"]}
          rows={arr(snapshot?.owasp_violation_log)}
        />
      </Card>
    </>
  );
}

// 5 ── Ecosystem — role library + model registry (read-only) ─────────────
export function Ecosystem() {
  const { snapshot } = useSnapshot();
  const agents = obj(snapshot?.agents);
  const [models, setModels] = useState<any[]>([]);
  useEffect(() => {
    fetchModels().then((r) => setModels(r.models)).catch(() => {});
  }, []);
  return (
    <>
      <Card title="Roles in use">
        <DataTable
          columns={["agent_id", "role", "domain", "backend"]}
          rows={Object.entries(agents).map(([id, a]) => ({
            agent_id: id,
            role: obj(a).role,
            domain: obj(a).domain_id,
            backend: obj(a).llm_backend,
          }))}
        />
      </Card>
      {/* Central model registry (PR-MM1) — the per-agent model dropdown's
          source. Agentset edits land in collective.yaml on the host. */}
      <Card title="Model registry (models.yaml)">
        {models.length === 0 && <Empty what="models" />}
        <DataTable
          columns={["model_id", "backend", "model", "label"]}
          rows={models.map((m: any) => ({
            model_id: m.model_id,
            backend: m.backend,
            model: m.model,
            label: m.label,
          }))}
        />
      </Card>
    </>
  );
}

// 6 ── Performance — queues, backpressure, latency (read-only) ────────────
export function Performance() {
  const { snapshot } = useSnapshot();
  const agents = obj(snapshot?.agents);
  return (
    <Card title="Performance">
      <DataTable
        columns={["agent_id", "queue_depth", "backpressure", "task_progress"]}
        rows={Object.entries(agents).map(([id, a]) => ({
          agent_id: id,
          queue_depth: obj(a).queue_depth,
          backpressure: obj(a).backpressure_state,
          task_progress: obj(a).task_progress,
        }))}
      />
    </Card>
  );
}

// 7 ── Comms — knowledge feed, signal log, episode queue (read-only) ──────
export function Comms() {
  const { snapshot } = useSnapshot();
  if (!snapshot) return <Empty what="comms data" />;
  return (
    <>
      <Card title="Knowledge feed">
        <DataTable
          columns={["tag", "source_agent", "confidence", "snippet"]}
          rows={arr(snapshot.knowledge_feed)}
        />
      </Card>
      <Card title="Signal-flow log">
        <DataTable
          columns={["signal_type", "source_agent", "key_field"]}
          rows={arr(snapshot.signal_flow_log)}
        />
      </Card>
      <Card title="Episode-nomination queue">
        <DataTable
          columns={["episode_id", "agent", "score", "status"]}
          rows={arr(snapshot.episode_nominees)}
        />
      </Card>
    </>
  );
}

// 8 ── Configuration — LLM endpoints + test-connection (interactive) ──────
export function Configuration() {
  const { snapshot } = useSnapshot();
  const [url, setUrl] = useState("");
  const [result, setResult] = useState("");
  const probe = async () => {
    setResult("probing…");
    try {
      const r = await testLLM(url);
      setResult(
        r.reachable
          ? `reachable — HTTP ${r.status_code} (${r.latency_ms} ms)`
          : "unreachable",
      );
    } catch (e) {
      setResult(`error: ${e}`);
    }
  };
  return (
    <Card title="Configuration">
      <KV data={obj(snapshot?.config_summary)} />
      <label>
        LLM base URL
        <input value={url} onChange={(e) => setUrl(e.target.value)} />
      </label>
      <button onClick={probe} disabled={!url}>
        Test connection
      </button>
      <p className="status">{result}</p>
    </Card>
  );
}

// 9 ── Diagnostics — golden-prompt EVAL-HISTORY (proposal G WebGUI parity) ──
//      Mirrors the acc-tui Diagnostics pane: pick a golden prompt, run it
//      against the live collective, and see the per-prompt run history
//      enriched (tokens / compliance / model self-verdict) with an MLflow
//      trace deep-link (DC only), the deterministic definition-of-good, and a
//      → Eval-pack promotion.  Reuses the shipped runtime via the backend.
function fmtRunTs(ts: number): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}
function fmtCompliance(c: number): string {
  return c != null && c >= 0 ? c.toFixed(2) : "—";
}

export function Diagnostics() {
  const { collectiveId } = useSnapshot();
  const [prompts, setPrompts] = useState<any[]>([]);
  const [sel, setSel] = useState<string>("");
  const [dog, setDog] = useState<string[]>([]);
  const [runs, setRuns] = useState<GoldenRun[]>([]);
  const [versions, setVersions] = useState<number[]>([]);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetchGoldenPrompts()
      .then((r) => setPrompts(r.prompts))
      .catch((e) => setErr(String(e)));
  }, []);

  const loadHistory = (name: string) =>
    fetchGoldenHistory(name)
      .then((r) => {
        setRuns(r.runs);
        setVersions(r.versions);
      })
      .catch((e) => setErr(String(e)));

  const select = async (name: string) => {
    setSel(name);
    setStatus("");
    setErr("");
    try {
      const d = await fetchGoldenDetail(name);
      setDog(d.definition_of_good);
    } catch (e) {
      setErr(String(e));
    }
    await loadHistory(name);
  };

  const run = async () => {
    if (!sel) return;
    setBusy(true);
    setStatus(`▶ running ${sel} on ${collectiveId}…`);
    try {
      const r = await runGolden(sel, collectiveId);
      setStatus(
        `◀ ${r.passed ? "PASS" : "FAIL"} · ${r.elapsed_ms}ms · ` +
          `tokens ${r.input_tokens || 0} · compliance ${fmtCompliance(
            r.compliance_health_score,
          )}${r.eval_verdict ? ` · ${r.eval_verdict}` : ""}`,
      );
      await loadHistory(sel);
    } catch (e) {
      setStatus(`✗ ${e}`);
    } finally {
      setBusy(false);
    }
  };

  const promote = async () => {
    if (!sel) return;
    try {
      const r = await promoteGolden(sel);
      setStatus(`✓ promoted to ${r.role} eval pack — ${r.path}`);
    } catch (e) {
      setStatus(`✗ ${e}`);
    }
  };

  return (
    <div className="diagnostics">
      <Card title="Golden prompts">
        {err && <p className="status">{err}</p>}
        {prompts.length === 0 && !err && <Empty what="golden prompts" />}
        <ul className="select-list">
          {prompts.map((p) => (
            <li key={p.name}>
              <button
                style={{ fontWeight: p.name === sel ? 700 : 400 }}
                onClick={() => select(p.name)}
              >
                {p.name}{" "}
                <span style={{ opacity: 0.6 }}>{p.target_role}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card title={sel ? `Diagnostics — ${sel}` : "Diagnostics"}>
        {!sel && <Empty what="a selected prompt" />}
        {sel && (
          <>
            <div className="actions">
              <button onClick={run} disabled={busy}>
                ▶ Run
              </button>
              <button onClick={promote} disabled={busy}>
                → Eval pack
              </button>
              {versions.length > 0 && (
                <span style={{ opacity: 0.6 }}>
                  versions: {versions.length}
                </span>
              )}
            </div>
            {status && <p className="status">{status}</p>}

            <h4>Definition of good</h4>
            <ul>
              {dog.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>

            <h4>Run history</h4>
            {runs.length === 0 ? (
              <Empty what="runs" />
            ) : (
              <table className="data">
                <thead>
                  <tr>
                    <th>when</th>
                    <th>result</th>
                    <th>latency</th>
                    <th>tokens</th>
                    <th>compliance</th>
                    <th>verdict</th>
                    <th>trace</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.run_id}>
                      <td>{fmtRunTs(r.run_ts)}</td>
                      <td>{r.passed ? "PASS" : "FAIL"}</td>
                      <td>{r.elapsed_ms}ms</td>
                      <td>{r.input_tokens || "—"}</td>
                      <td>{fmtCompliance(r.compliance_health_score)}</td>
                      <td>{r.eval_verdict || "—"}</td>
                      <td>
                        {r.mlflow_trace_url ? (
                          <a
                            href={r.mlflow_trace_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            MLflow ↗
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

// 11 ── Marketplace — browse catalog packages + stage install (WS-C1) ──────
//      Mirrors the acc-tui MarketplaceScreen. Read = viewer; install stages
//      a PROPOSE_INFUSE marker for the Compliance pane (operator-gated).
export function Marketplace() {
  const [rows, setRows] = useState<MarketRow[]>([]);
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState("");
  const [status, setStatus] = useState("");

  const load = (f = filter) => {
    fetchAvailableRoles(f)
      .then(setRows)
      .catch((e) => setErr(String(e)));
  };
  useEffect(() => load(""), []);

  const install = async (name: string) => {
    setStatus(`staging ${name}…`);
    try {
      const r = await installRole(name);
      setStatus(
        `staged ${r.target_name}@${r.target_constraint} — approve it in Compliance`,
      );
    } catch (e) {
      setStatus(`error: ${e}`);
    }
  };

  return (
    <Card title="Marketplace">
      <div className="oversight-row">
        <input
          placeholder="filter by name…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
        />
        <button onClick={() => load()}>Search</button>
      </div>
      {err && <p className="status">{err}</p>}
      {rows.length === 0 && !err && <Empty what="packages" />}
      <table className="data">
        <thead>
          <tr>
            <th>name</th>
            <th>version</th>
            <th>tier</th>
            <th>catalog</th>
            <th>signer</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.name}@${r.version}-${i}`}>
              <td>{r.name}</td>
              <td>{r.version}</td>
              <td>
                {r.tier_badge} {r.tier}
              </td>
              <td>
                {r.catalog_id} ({r.catalog_mode})
              </td>
              <td>{r.signer}</td>
              <td>
                <button onClick={() => install(r.name)}>Install</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="status">{status}</p>
    </Card>
  );
}

// 12 ── Catalogs — layered catalog CRUD (WS-C1) ────────────────────────────
//      Mirrors the acc-tui CatalogsScreen. Add / remove / re-prioritise the
//      workspace-layer catalogs the Marketplace resolves against.
export function Catalogs() {
  const [cats, setCats] = useState<CatalogRow[]>([]);
  const [msg, setMsg] = useState("");
  const [form, setForm] = useState({
    catalog_id: "",
    tier: "community",
    mode: "https",
    url: "",
    path: "",
    issuer: "",
    subject_pattern: "",
    priority: 100,
  });

  const load = () => fetchCatalogs().then(setCats).catch((e) => setMsg(String(e)));
  useEffect(() => {
    load();
  }, []);

  const add = async () => {
    setMsg("adding…");
    try {
      await addCatalog(form);
      setMsg(`added ${form.catalog_id}`);
      setForm({ ...form, catalog_id: "", url: "", path: "" });
      load();
    } catch (e) {
      setMsg(`error: ${e}`);
    }
  };
  const remove = async (id: string) => {
    try {
      await removeCatalog(id);
      load();
    } catch (e) {
      setMsg(`error: ${e}`);
    }
  };
  const reprioritise = async (id: string, priority: number) => {
    try {
      await setCatalogPriority(id, priority);
      load();
    } catch (e) {
      setMsg(`error: ${e}`);
    }
  };

  const set = (k: string, v: string | number) => setForm({ ...form, [k]: v });

  return (
    <>
      <Card title="Configured catalogs">
        {cats.length === 0 && <Empty what="catalogs" />}
        {cats.map((c) => (
          <div key={c.id} className="oversight-row">
            <span>
              <strong>{c.id}</strong> · {c.tier} · {c.mode}
            </span>
            <span>{c.url || c.path}</span>
            <span>
              signer: {c.required_signer.issuer || "—"} /{" "}
              {c.required_signer.subject_pattern || "—"}
            </span>
            <input
              type="number"
              value={c.priority}
              style={{ width: "5rem" }}
              onChange={(e) =>
                reprioritise(c.id, parseInt(e.target.value, 10) || c.priority)
              }
            />
            <button onClick={() => remove(c.id)}>Remove</button>
          </div>
        ))}
      </Card>
      <Card title="Add a catalog">
        <label>
          Catalog id
          <input
            value={form.catalog_id}
            onChange={(e) => set("catalog_id", e.target.value)}
          />
        </label>
        <label>
          Tier
          <select value={form.tier} onChange={(e) => set("tier", e.target.value)}>
            <option>community</option>
            <option>standard</option>
            <option>premium</option>
          </select>
        </label>
        <label>
          Mode
          <select value={form.mode} onChange={(e) => set("mode", e.target.value)}>
            <option>https</option>
            <option>oci</option>
            <option>local</option>
          </select>
        </label>
        <label>
          URL
          <input value={form.url} onChange={(e) => set("url", e.target.value)} />
        </label>
        <label>
          Path (local mode)
          <input value={form.path} onChange={(e) => set("path", e.target.value)} />
        </label>
        <label>
          Required signer — issuer
          <input
            value={form.issuer}
            onChange={(e) => set("issuer", e.target.value)}
          />
        </label>
        <label>
          Required signer — subject pattern
          <input
            value={form.subject_pattern}
            onChange={(e) => set("subject_pattern", e.target.value)}
          />
        </label>
        <label>
          Priority
          <input
            type="number"
            value={form.priority}
            onChange={(e) => set("priority", parseInt(e.target.value, 10) || 100)}
          />
        </label>
        <button onClick={add} disabled={!form.catalog_id || !form.issuer}>
          Add catalog
        </button>
        <p className="status">{msg}</p>
      </Card>
    </>
  );
}

// 13 ── Role editor — author/edit in-tree roles (WS-C1 over WS-C2) ─────────
//      Mirrors acc-tui role_writeback authoring. Open an existing role to
//      edit its role.yaml + role.md, or create a new one. role.yaml is
//      validated server-side; validation errors surface inline.
export function RoleEditor() {
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [newId, setNewId] = useState("");
  const [yamlText, setYamlText] = useState("");
  const [mdText, setMdText] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const loadList = () => listRoles().then(setRoles).catch((e) => setStatus(String(e)));
  useEffect(() => {
    loadList();
  }, []);

  const open = async (roleId: string) => {
    setCreating(false);
    setSelected(roleId);
    setStatus("loading…");
    try {
      const [y, m] = await Promise.all([getRoleYaml(roleId), getRoleMd(roleId)]);
      setYamlText(y.yaml_text);
      setMdText(m.md_text);
      setStatus("");
    } catch (e) {
      setStatus(`error: ${e}`);
    }
  };

  const startNew = () => {
    setCreating(true);
    setSelected("");
    setNewId("");
    setYamlText(
      "purpose: \"\"\npersona: concise\nallowed_skills: []\ndefault_skills: []\n",
    );
    setMdText("");
    setStatus("");
  };

  const save = async () => {
    setBusy(true);
    setStatus("saving…");
    try {
      if (creating) {
        const r = await createRole(newId, yamlText, mdText);
        setStatus(`created ${r.role_id}`);
        setCreating(false);
        setSelected(r.role_id);
        loadList();
      } else {
        await putRoleYaml(selected, yamlText);
        await putRoleMd(selected, mdText);
        setStatus(`saved ${selected}`);
        loadList();
      }
    } catch (e) {
      // backend 400 body is {detail:{message,errors}} — surfaced raw.
      setStatus(`validation/error: ${e}`);
    } finally {
      setBusy(false);
    }
  };

  const canSave =
    !busy && yamlText.trim().length > 0 && (creating ? newId.trim().length > 0 : !!selected);

  return (
    <>
      <Card title="Roles">
        <div className="oversight-row">
          <select
            value={selected}
            onChange={(e) => e.target.value && open(e.target.value)}
          >
            <option value="">— select a role to edit —</option>
            {roles.map((r) => (
              <option key={r.role_id} value={r.role_id}>
                {r.role_id}
                {r.has_md ? " ✎" : ""}
              </option>
            ))}
          </select>
          <button onClick={startNew}>New role</button>
        </div>
      </Card>

      {(creating || selected) && (
        <Card title={creating ? "Create role" : `Edit role: ${selected}`}>
          {creating && (
            <label>
              New role id (lowercase + underscore)
              <input value={newId} onChange={(e) => setNewId(e.target.value)} />
            </label>
          )}
          <label>
            role.yaml
            <textarea
              className="code"
              rows={18}
              value={yamlText}
              onChange={(e) => setYamlText(e.target.value)}
              spellCheck={false}
            />
          </label>
          <label>
            role.md (narrative — optional)
            <textarea
              rows={8}
              value={mdText}
              onChange={(e) => setMdText(e.target.value)}
            />
          </label>
          <button onClick={save} disabled={!canSave}>
            {creating ? "Create" : "Save"}
          </button>
          <p className="status">{status}</p>
        </Card>
      )}
    </>
  );
}

// 10 ── Help ──────────────────────────────────────────────────────────────
export function Help() {
  return (
    <Card title="Help">
      <p>
        acc-webgui is the optional web frontend for ACC — feature parity with
        the terminal UI <code>acc-tui</code> plus the enhanced tracing views.
      </p>
      <p>See docs/webgui.md for the full operator guide.</p>
    </Card>
  );
}

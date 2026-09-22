// The feature-parity screens (proposal §4.4) that have NOT yet been rebuilt on
// PatternFly — Overview, Work and Packages have (src/pages). Each mirrors an
// acc-tui screen under acc/tui/screens/ and runs inside the scoped .legacy
// stylesheet; a screen leaves this file when its page is written.

import { useEffect, useState } from "react";
import { useSnapshot } from "./state/snapshot";
import { Card, KV, DataTable, Empty } from "./common";
import {
  infuseRole,
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
  listRoles,
  getRoleYaml,
  getRoleMd,
  putRoleYaml,
  putRoleMd,
  createRole,
  fetchRoleAuthoring,
} from "./api/client";
import type {
  MarketRow, RoleRow, RoleAuthoring, GoldenRun,
} from "./api/client";

const obj = (v: unknown): Record<string, any> =>
  v && typeof v === "object" ? (v as Record<string, any>) : {};
const arr = (v: unknown): any[] => (Array.isArray(v) ? v : []);

// 2 ── Nucleus / Infuse — role-composition form (interactive) ─────────────
//      The role is picked, not typed (proposal 056 §4.4): the installed
//      roles (in-tree ∪ packs, GET /api/roles) are selectable; catalog
//      packages (GET /api/roles/available) are listed but not selectable —
//      they are not installed here.
export function Infuse() {
  const { collectiveId, snapshot } = useSnapshot();
  const [roleId, setRoleId] = useState("");
  const [purpose, setPurpose] = useState("");
  const [persona, setPersona] = useState("concise");
  const [status, setStatus] = useState("");
  const [installed, setInstalled] = useState<RoleRow[]>([]);
  const [catalog, setCatalog] = useState<MarketRow[]>([]);

  useEffect(() => {
    listRoles().then(setInstalled).catch((e) => setStatus(`error: ${e}`));
    fetchAvailableRoles().then((r) => setCatalog(r.rows)).catch(() => {});
  }, []);
  const running = new Set(
    Object.values(obj(snapshot?.agents)).map((a) => String(obj(a).role ?? "")).filter(Boolean),
  );

  const apply = async () => {
    setStatus("publishing…");
    try {
      const r = await infuseRole(collectiveId, { id: roleId, purpose, persona });
      setStatus(
        r.status === "published"
          ? "ROLE_UPDATE published — awaiting arbiter approval"
          : r.note || r.status,
      );
    } catch (e) {
      setStatus(`error: ${e}`);
    }
  };
  return (
    <Card title="Infuse a role">
      <label>
        Role
        <select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
          <option value="">— pick a role —</option>
          <optgroup label="installed (in-tree + packs)">
            {installed.map((r) => (
              <option key={r.role_id} value={r.role_id}>
                {r.role_id}
                {running.has(r.role_id) ? " · running" : ""}
                {r.source !== "in-tree" ? ` · ${r.source}` : ""}
              </option>
            ))}
          </optgroup>
          {catalog.length > 0 && (
            <optgroup label="catalog — not installed">
              {catalog.map((r, i) => (
                <option key={`${r.name}@${r.version}-${i}`} value="" disabled>
                  {r.name}@{r.version} ({r.catalog_id})
                </option>
              ))}
            </optgroup>
          )}
        </select>
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

// 4 ── Compliance — OWASP grading + governance layers + frameworks +
//      gap analysis + rule proposals + oversight queue (interactive).
//      Mirrors the latest acc-tui Compliance pane (PR-Z1/Z2/Z3).
export function Compliance() {
  const { collectiveId, snapshot } = useSnapshot();
  const [msg, setMsg] = useState("");
  const [layers, setLayers] = useState<any[]>([]);
  const [layersErr, setLayersErr] = useState<{ error: string; hint: string } | null>(null);
  const [frameworks, setFrameworks] = useState<any[]>([]);
  const [proposals, setProposals] = useState<any[]>([]);
  const [scanMsg, setScanMsg] = useState("");

  const loadGovernance = () => {
    fetchGovernanceLayers()
      .then((r) => {
        setLayers(r.layers);
        setLayersErr(r.error ? { error: r.error, hint: r.hint ?? "" } : null);
      })
      .catch((e) => setLayersErr({ error: `governance layers: ${e}`, hint: "" }));
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

      {/* Governance layers — Cat A/B/C (PR-Z1). No regulatory_layer/ on this
          host is said in one sentence, not shown as three empty tables. */}
      {layersErr && (
        <Card title="Governance layers">
          <div className="board-msg error">{layersErr.error}</div>
          {layersErr.hint && <p className="hint">{layersErr.hint}</p>}
        </Card>
      )}
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

// 13 ── Role editor — author/edit in-tree roles (WS-C1 over WS-C2) ─────────
//      Mirrors acc-tui role_writeback authoring. Open an existing role to
//      edit its role.yaml + role.md, or create a new one. role.yaml is
//      validated server-side; validation errors surface inline. A role that
//      cannot be written here (an installed pack, the operator's read-only
//      roles mount) says why, and Save / New role are disabled with that
//      reason rather than failing on click (proposal 056 §4.3).
export function RoleEditor() {
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [authoring, setAuthoring] = useState<RoleAuthoring | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [newId, setNewId] = useState("");
  const [yamlText, setYamlText] = useState("");
  const [mdText, setMdText] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const loadList = () => {
    listRoles().then(setRoles).catch((e) => setStatus(String(e)));
    fetchRoleAuthoring().then(setAuthoring).catch(() => {});
  };
  useEffect(() => {
    loadList();
  }, []);
  const selectedRow = roles.find((r) => r.role_id === selected);
  // Why the current target cannot be written ("" when it can).
  const blockReason = creating
    ? authoring?.write_block_reason ?? ""
    : selectedRow?.write_block_reason ?? "";

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
    !busy && !blockReason && yamlText.trim().length > 0 &&
    (creating ? newId.trim().length > 0 : !!selected);
  const canCreate = !!authoring?.writable;

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
                {r.writable ? "" : ` (read-only · ${r.source})`}
              </option>
            ))}
          </select>
          <button
            onClick={startNew}
            disabled={!canCreate}
            title={canCreate ? "" : authoring?.write_block_reason}
          >
            New role
          </button>
        </div>
        {authoring && !authoring.writable && (
          <p className="hint">new roles cannot be created here: {authoring.write_block_reason}</p>
        )}
      </Card>

      {(creating || selected) && (
        <Card title={creating ? "Create role" : `Edit role: ${selected}`}>
          {blockReason && (
            <p className="hint">
              read-only{selectedRow ? ` · ${selectedRow.source}` : ""} — {blockReason}
            </p>
          )}
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
          <button onClick={save} disabled={!canSave} title={blockReason}>
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

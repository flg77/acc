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
  fetchModels,
  runGapScan,
  decideProposal,
} from "./api/client";

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

// 9 ── Diagnostics — golden-prompt suite (read-only list) ─────────────────
//      Mirrors the acc-tui Diagnostics pane (PR-N/Y).  Running a prompt
//      against the live stack happens via the Prompt screen / TUI; here
//      we surface the suite (shipped + writable store + attached dirs).
export function Diagnostics() {
  const [prompts, setPrompts] = useState<any[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => {
    fetchGoldenPrompts()
      .then((r) => setPrompts(r.prompts))
      .catch((e) => setErr(String(e)));
  }, []);
  return (
    <Card title="Golden prompts">
      {err && <p className="status">{err}</p>}
      {prompts.length === 0 && !err && <Empty what="golden prompts" />}
      <DataTable
        columns={["name", "target_role", "operating_mode", "description"]}
        rows={prompts}
      />
    </Card>
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

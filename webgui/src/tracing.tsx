// Enhanced-tracing views (proposal §4.5) — the views that justify the
// project, doing what the ASCII TUI fundamentally cannot.
//
// This scaffold ships three views wired to backend data; the PLAN DAG
// graph (reactflow), the cross-collective bridge graph, and the
// JetStream replay slider are layered on the same data in follow-up
// iterations of PR-4.

import { useEffect, useState } from "react";
import { useSnapshot } from "./state/snapshot";
import { Card, DataTable, Empty } from "./common";
import { fetchAuditTimeline, fetchPlanDag } from "./api/client";

const obj = (v: unknown): Record<string, any> =>
  v && typeof v === "object" ? (v as Record<string, any>) : {};

// View 1 ── Task-step trace waterfall (from the live snapshot) ────────────
export function TraceWaterfall() {
  const { snapshot } = useSnapshot();
  const agents = obj(snapshot?.agents);
  const rows = Object.entries(agents)
    .map(([id, a]) => ({ agent_id: id, progress: obj(a).task_progress }))
    .filter((r) => r.progress && Object.keys(r.progress).length > 0)
    .map((r) => ({
      agent_id: r.agent_id,
      step: `${obj(r.progress).current_step}/${obj(r.progress).total_steps_estimated}`,
      label: obj(r.progress).step_label,
      elapsed_ms: obj(r.progress).elapsed_ms,
      confidence: obj(r.progress).confidence,
    }));
  return (
    <Card title="Task-step trace waterfall">
      {rows.length === 0 && <Empty what="in-flight tasks" />}
      {rows.length > 0 && (
        <DataTable
          columns={["agent_id", "step", "label", "elapsed_ms", "confidence"]}
          rows={rows}
        />
      )}
    </Card>
  );
}

// View 2 ── PLAN DAG (from /api/trace/plan) ───────────────────────────────
export function PlanDag() {
  const { collectiveId } = useSnapshot();
  const [plans, setPlans] = useState<Record<string, any>>({});
  useEffect(() => {
    fetchPlanDag(collectiveId)
      .then((r) => setPlans(r.active_plans))
      .catch(() => {});
  }, [collectiveId]);
  return (
    <Card title="PLAN DAG">
      {Object.keys(plans).length === 0 && <Empty what="active plans" />}
      {Object.entries(plans).map(([pid, p]) => (
        <div key={pid}>
          <h4>{pid}</h4>
          <DataTable
            columns={["step_id", "status"]}
            rows={Object.entries(obj(obj(p).step_progress)).map(
              ([sid, st]) => ({ step_id: sid, status: st }),
            )}
          />
        </div>
      ))}
      <p className="hint">
        The interactive force-directed graph (reactflow) renders this same
        data — see proposal §4.5 view 2.
      </p>
    </Card>
  );
}

// View 3 ── Audit-chain timeline (tamper-evident) ─────────────────────────
export function AuditTimeline() {
  const [state, setState] = useState<{
    records: any[];
    tampered: number[];
    breaks: number[];
    verified: boolean;
    error?: string;
  }>({ records: [], tampered: [], breaks: [], verified: true });

  useEffect(() => {
    fetchAuditTimeline(200)
      .then((r) =>
        setState({
          records: r.records,
          tampered: r.tampered_indices,
          breaks: r.chain_break_indices,
          verified: r.verified,
        }),
      )
      .catch((e) => setState((s) => ({ ...s, error: String(e) })));
  }, []);

  if (state.error)
    return (
      <Card title="Audit-chain timeline">
        <p className="empty">audit backend unavailable: {state.error}</p>
      </Card>
    );
  return (
    <Card title="Audit-chain timeline">
      <p className={state.verified ? "verified" : "tampered"}>
        {state.verified
          ? "✓ chain verified — no tampering detected"
          : `✗ INTEGRITY ALERT — ${state.tampered.length} tampered, ${state.breaks.length} chain break(s)`}
      </p>
      <DataTable
        columns={["timestamp_ms", "signal_type", "cat_a_result", "outcome", "risk_level"]}
        rows={state.records}
      />
    </Card>
  );
}

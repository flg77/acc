// Board — work in flight (20260903-work-board-webgui).
//
// A real kanban over the same pure projection the TUI Board renders: five columns,
// cards the runtime moves. Nobody drags a card to Done — the buttons publish
// PLAN_STEP_CONTROL / TASK_CANCEL and the arbiter applies them. Re-fetched on every
// snapshot push. A viewer is shown the cards and no buttons; the environment bar
// says why.

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardFooter,
  CardHeader,
  CardTitle,
  EmptyState,
  EmptyStateBody,
  FormSelect,
  FormSelectOption,
  Label,
  Title,
} from "@patternfly/react-core";
import { boardControl, fetchBoard } from "../../api/client";
import type { BoardColumn, BoardItem } from "../../api/client";
import { useSnapshot } from "../../state/snapshot";
import { useEnvironment } from "../../shell/EnvironmentContext";

const COLUMN: Record<BoardItem["status"], { label: string; color: "grey" | "blue" | "orange" | "green" | "red" }> = {
  QUEUED: { label: "Queued", color: "grey" },
  RUNNING: { label: "Running", color: "blue" },
  BLOCKED: { label: "Blocked", color: "orange" },
  DONE: { label: "Done", color: "green" },
  FAILED: { label: "Failed", color: "red" },
};

type Note = { kind: "info" | "danger"; text: string } | null;

function BoardCard({
  item, cid, roles, canAct, onNote,
}: { item: BoardItem; cid: string; roles: string[]; canAct: boolean; onNote: (n: Note) => void }) {
  const [role, setRole] = useState(roles[0] ?? "");
  const act = async (action: "cancel" | "retry" | "reassign") => {
    try {
      const r = await boardControl(cid, {
        kind: item.kind === "plan_step" ? "plan_step" : "task",
        action,
        plan_id: item.plan_id,
        step_id: item.step_id,
        task_id: item.task_id,
        role: action === "reassign" ? role : undefined,
      });
      onNote({ kind: "info", text: `${action} → ${item.title} (${r.signal} by ${r.actor})` });
    } catch (e) {
      onNote({ kind: "danger", text: `${action} → ${item.title}: ${e}` });
    }
  };
  const age = item.updated_ts
    ? `${Math.max(0, Math.round((Date.now() / 1000 - item.updated_ts) / 60))} min`
    : "";
  const buttons = canAct && (item.can_cancel || item.can_retry || item.can_reassign);
  return (
    <Card isCompact>
      <CardHeader>
        <CardTitle>{item.title}</CardTitle>
      </CardHeader>
      <CardBody>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--pf-t--global--spacer--xs)" }}>
          <Label isCompact variant="outline">{item.kind.replace("_", " ")}</Label>
          {(item.role || item.agent_id) && <Label isCompact variant="outline">{item.agent_id || item.role}</Label>}
          {item.iteration && <Label isCompact variant="outline">iter {item.iteration}</Label>}
          {age && <Label isCompact variant="outline">{age}</Label>}
        </div>
        {item.status_detail && item.status !== "BLOCKED" && <p className="acc-reason">{item.status_detail}</p>}
        {item.blocked_on && (
          <p className="acc-reason">
            Waiting on gate <code>{item.blocked_on.slice(0, 12)}</code> — {item.status_detail}
          </p>
        )}
        {item.critique && <p className="acc-reason">critique: {item.critique}</p>}
        {item.outcome && <p className="acc-reason">outcome: {item.outcome}</p>}
      </CardBody>
      {(buttons || item.blocked_on) && (
        <CardFooter>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--pf-t--global--spacer--xs)", alignItems: "center" }}>
            {canAct && item.can_cancel && (
              <Button size="sm" variant="secondary" isDanger onClick={() => act("cancel")}>Cancel</Button>
            )}
            {canAct && item.can_retry && (
              <Button size="sm" variant="secondary" onClick={() => act("retry")}>Retry</Button>
            )}
            {canAct && item.can_reassign && roles.length > 0 && (
              <>
                <FormSelect value={role} onChange={(_, v) => setRole(v)} aria-label="Reassign to role" style={{ inlineSize: "auto" }}>
                  {roles.map((r) => <FormSelectOption key={r} value={r} label={r} />)}
                </FormSelect>
                <Button size="sm" variant="secondary" onClick={() => act("reassign")}>Reassign</Button>
              </>
            )}
            {item.blocked_on && (
              <Button size="sm" variant="link" component="a" href="#/governance/compliance">
                Answer the gate in Governance
              </Button>
            )}
          </div>
        </CardFooter>
      )}
    </Card>
  );
}

export function BoardPage() {
  const { collectiveId, snapshot } = useSnapshot();
  const { who } = useEnvironment();
  const [cols, setCols] = useState<BoardColumn[]>([]);
  const [note, setNote] = useState<Note>(null);
  const [err, setErr] = useState("");

  const load = () => {
    if (!collectiveId) return;
    fetchBoard(collectiveId)
      .then((r) => { setCols(r.columns); setErr(""); })
      .catch((e) => setErr(String(e)));
  };
  // The snapshot changes on every WebSocket push; the board follows it.
  useEffect(load, [collectiveId, snapshot?.last_updated_ts]);

  const roles = Array.from(
    new Set(Object.values((snapshot?.agents ?? {}) as Record<string, any>)
      .map((a) => String(a?.role ?? "")).filter(Boolean)),
  ).sort();
  const total = cols.reduce((n, c) => n + c.items.length, 0);
  const canAct = who?.role !== "viewer";

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">
        Board
      </Title>
      <p className="acc-source">
        Work in flight ({total}). The runtime moves cards; you may cancel, retry, reassign, or
        answer the gate a Blocked card waits on. Nobody drags a card to Done.
      </p>

      {err && <Alert isInline variant="danger" title="The board could not be read" component="p">{err}</Alert>}
      {note && (
        <Alert isInline variant={note.kind} title={note.text} component="p"
               timeout={8000} onTimeout={() => { setNote(null); return true; }} />
      )}

      {!err && total === 0 && (
        <EmptyState titleText="Nothing in flight" headingLevel="h2" variant="sm">
          <EmptyStateBody>No task or plan step is queued, running, blocked or finished in this collective.</EmptyStateBody>
        </EmptyState>
      )}

      {total > 0 && (
        // Five columns side by side while they fit, stacked when they do not —
        // a kanban on a wide screen, a list inside a Showroom tab.
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(16rem, 1fr))",
          gap: "var(--pf-t--global--spacer--md)",
          alignItems: "start",
        }}>
          {cols.map((c) => (
            <section key={c.status} aria-label={COLUMN[c.status]?.label ?? c.status}
                     style={{ display: "grid", gap: "var(--pf-t--global--spacer--sm)", alignContent: "start" }}>
              <Title headingLevel="h2" size="lg" style={{ display: "flex", gap: "var(--pf-t--global--spacer--sm)", alignItems: "center" }}>
                {COLUMN[c.status]?.label ?? c.status}
                <Label isCompact color={COLUMN[c.status]?.color ?? "grey"}>{c.items.length}</Label>
              </Title>
              {c.items.length === 0 && <p className="acc-source">—</p>}
              {c.items.map((it) => (
                <BoardCard key={it.id} item={it} cid={collectiveId} roles={roles} canAct={canAct} onNote={setNote} />
              ))}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

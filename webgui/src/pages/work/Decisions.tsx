// Decisions — what the agents are asking you, and the "ask once" missions you
// allowed (vault proposal 058, Phase 2: answer from anywhere).
//
// The same rows the TUI's decision panel shows (the arbiter heartbeat's
// oversight_pending_items). A row that asks a question shows its options as
// buttons, so the mission card — "allow the mission" / "just this call" /
// "don't run it" — is answered here exactly as in the TUI. The missions list
// comes from the same heartbeat; close, revoke, purge and restore publish
// MISSION_CONTROL and the agents apply it. A viewer sees everything and no
// buttons.

import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  EmptyStateBody,
  Label,
  List,
  ListItem,
  Title,
} from "@patternfly/react-core";
import { missionControl, oversightDecision } from "../../api/client";
import type { MissionSummary } from "../../api/client";
import { useSnapshot } from "../../state/snapshot";
import { useEnvironment } from "../../shell/EnvironmentContext";

type Note = { kind: "info" | "danger"; text: string } | null;

interface Option { key: string; label: string; proceeds: boolean; detail?: string[] }
interface Pending {
  oversight_id: string;
  task_id: string;
  agent_id: string;
  risk_level: string;
  summary: string;
  question?: { text: string; options: Option[]; destructive?: boolean; evidence?: string };
  evidence?: string[];
  requester?: string;
}

const STATUS_COLOR: Record<string, "green" | "grey" | "orange" | "red" | "blue"> = {
  ACTIVE: "green", CLOSED: "grey", REVOKED: "red", EXHAUSTED: "orange", EXPIRED: "orange",
};

function PendingCard({ row, cid, canAct, onNote }: {
  row: Pending; cid: string; canAct: boolean; onNote: (n: Note) => void;
}) {
  const q = row.question;
  const send = async (approve: boolean, answer = "") => {
    try {
      await oversightDecision(cid, row.oversight_id, approve ? "APPROVE" : "REJECT", "", answer);
      onNote({ kind: "info", text: `${answer ? `option ${answer}` : approve ? "allowed" : "refused"} — sent for ${row.oversight_id.slice(0, 12)}` });
    } catch (e) {
      onNote({ kind: "danger", text: String(e) });
    }
  };
  return (
    <Card isCompact className="decision-card" data-testid="decision-card">
      <CardHeader>
        <CardTitle>
          <Label color={row.risk_level === "CRITICAL" ? "red" : "orange"}>{row.risk_level}</Label>{" "}
          {q?.text ?? row.summary}
        </CardTitle>
      </CardHeader>
      <CardBody>
        <div className="hint">
          {row.agent_id} · task {row.task_id.slice(0, 12)}
          {row.requester ? ` · for ${row.requester}` : ""}
        </div>
        {(row.evidence ?? []).length > 0 && (
          <List isPlain>{(row.evidence ?? []).slice(0, 4).map((e) => <ListItem key={e}><code>{e}</code></ListItem>)}</List>
        )}
        {canAct && q && (
          <div className="decision-options">
            {q.options.map((o) => (
              <div key={o.key} className="decision-option">
                <Button
                  variant={o.proceeds ? (o.key === "1" ? "primary" : "secondary") : "danger"}
                  onClick={() => send(o.proceeds, o.key)}
                >
                  {o.label}
                </Button>
                {(o.detail ?? []).length > 0 && (
                  <List isPlain className="hint">{(o.detail ?? []).map((d) => <ListItem key={d}>{d}</ListItem>)}</List>
                )}
              </div>
            ))}
          </div>
        )}
        {canAct && !q && (
          <div className="decision-options">
            <Button variant="primary" onClick={() => send(true)}>Allow</Button>{" "}
            <Button variant="danger" onClick={() => send(false)}>Refuse</Button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function MissionCard({ m, cid, canAct, onNote }: {
  m: MissionSummary; cid: string; canAct: boolean; onNote: (n: Note) => void;
}) {
  const act = async (action: "close" | "revoke" | "purge" | "restore") => {
    try {
      await missionControl(cid, m.mission_id, action);
      onNote({ kind: "info", text: `${action} sent for ${m.mission_id}` });
    } catch (e) {
      onNote({ kind: "danger", text: String(e) });
    }
  };
  const active = m.status === "ACTIVE";
  return (
    <Card isCompact data-testid="mission-card">
      <CardHeader>
        <CardTitle>
          <Label color={STATUS_COLOR[m.status] ?? "grey"}>{m.status}</Label> {m.mission_id}
        </CardTitle>
      </CardHeader>
      <CardBody>
        <div>
          {m.used_calls}/{m.max_calls} calls · roles {m.roles_used.join(", ") || "—"} · allowed by{" "}
          {m.approver || "?"} · workspace <code>{m.workspace || "—"}</code>
        </div>
        {m.quarantine.length > 0 ? (
          <>
            <div className="hint">{m.quarantine.length} deletion(s) backed up — recoverable until you purge:</div>
            <List isPlain>
              {m.quarantine.map((e) => <ListItem key={e.kept_at}><code>{e.path}</code> → <code>{e.kept_at}</code></ListItem>)}
            </List>
          </>
        ) : (
          <div className="hint">no deletions</div>
        )}
        {canAct && (
          <div className="decision-options">
            {active && <Button variant="secondary" onClick={() => act("close")}>Close</Button>}{" "}
            {active && <Button variant="danger" onClick={() => act("revoke")}>Revoke</Button>}{" "}
            {!active && m.quarantine.length > 0 && (
              <>
                <Button variant="primary" onClick={() => act("restore")}>Restore</Button>{" "}
                <Button variant="danger" onClick={() => act("purge")}>Purge (final allowance)</Button>
              </>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export function DecisionsPage() {
  const { collectiveId, snapshot } = useSnapshot();
  const { who } = useEnvironment();
  const canAct = who?.role !== "viewer";
  const [note, setNote] = useState<Note>(null);
  const pending = ((snapshot?.oversight_pending_items ?? []) as Pending[]);
  const missions = ((snapshot as any)?.missions ?? []) as MissionSummary[];

  return (
    <>
      {note && <Alert isInline variant={note.kind} title={note.text} />}
      <Title headingLevel="h2">Waiting on you</Title>
      {pending.length === 0 ? (
        <EmptyState><EmptyStateBody>Nothing is waiting on you.</EmptyStateBody></EmptyState>
      ) : (
        pending.map((row) => (
          <PendingCard key={row.oversight_id} row={row} cid={collectiveId} canAct={canAct} onNote={setNote} />
        ))
      )}
      <Title headingLevel="h2">Missions</Title>
      {missions.length === 0 ? (
        <EmptyState>
          <EmptyStateBody>
            No missions yet. Under “Ask once”, the first system-access call of a thread asks for one.
          </EmptyStateBody>
        </EmptyState>
      ) : (
        missions.map((m) => <MissionCard key={m.mission_id} m={m} cid={collectiveId} canAct={canAct} onNote={setNote} />)
      )}
    </>
  );
}

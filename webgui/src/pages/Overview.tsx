// Overview — is it well?  (replaces the old Dashboard + Performance screens)
//
// One page from the live snapshot: the collective in four numbers, and one row
// per agent with everything the two old screens showed — state, drift,
// compliance, queue depth, backpressure, and the task it is on.

import {
  Card,
  CardBody,
  CardTitle,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  EmptyState,
  EmptyStateBody,
  Label,
  Title,
} from "@patternfly/react-core";
import { useSnapshot } from "../state/snapshot";
import { Table, cell } from "../components/Table";
import type { Column } from "../components/Table";

type Agent = Record<string, any>;
interface Row extends Agent {
  agent_id: string;
}

const score = (v: unknown) => (typeof v === "number" && v >= 0 ? v.toFixed(2) : "—");

/** Backpressure is OPEN | THROTTLE | CLOSED (acc.tui.models). */
const BACKPRESSURE_COLOR: Record<string, "green" | "orange" | "red"> = {
  OPEN: "green",
  THROTTLE: "orange",
  CLOSED: "red",
};

function progress(p: unknown): string {
  if (!p || typeof p !== "object" || Object.keys(p).length === 0) return "—";
  const o = p as Record<string, any>;
  const step = `${o.current_step ?? "?"}/${o.total_steps_estimated ?? "?"}`;
  return o.step_label ? `${step} · ${o.step_label}` : step;
}

const COLUMNS: Column<Row>[] = [
  { key: "agent", label: "Agent", render: (r) => <code>{r.agent_id}</code> },
  { key: "role", label: "Role", render: (r) => <code>{cell(r.role)}</code> },
  { key: "state", label: "State", render: (r) => cell(r.state) },
  {
    key: "queue",
    label: "Queue",
    render: (r) => (
      <>
        {cell(r.queue_depth)}{" "}
        {r.backpressure_state && (
          <Label isCompact color={BACKPRESSURE_COLOR[r.backpressure_state] ?? "grey"}>
            {r.backpressure_state}
          </Label>
        )}
      </>
    ),
  },
  { key: "task", label: "Current task", render: (r) => progress(r.task_progress) },
  { key: "drift", label: "Drift", render: (r) => score(r.drift_score) },
  { key: "compliance", label: "Compliance", render: (r) => score(r.compliance_score) },
];

export function OverviewPage() {
  const { snapshot, collectiveId, connected } = useSnapshot();
  const agents: Row[] = Object.entries((snapshot?.agents ?? {}) as Record<string, Agent>).map(
    ([id, a]) => ({ ...a, agent_id: id }),
  );

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">
        Overview
      </Title>
      <p className="acc-source">
        Collective <code>{collectiveId || "—"}</code> ·{" "}
        {connected ? "live from the bus" : "no bus connection yet"}
      </p>

      {!snapshot && (
        <EmptyState titleText="Waiting for the first heartbeat" headingLevel="h2" variant="sm">
          <EmptyStateBody>
            Nothing has arrived from the bus for this collective yet. If the agents are
            running, this fills in within a few seconds.
          </EmptyStateBody>
        </EmptyState>
      )}

      {snapshot && (
        <>
          <Card isCompact>
            <CardTitle>Collective</CardTitle>
            <CardBody>
              <DescriptionList isHorizontal isCompact columnModifier={{ default: "2Col" }}>
                <Item term="Agents" value={agents.length} />
                <Item term="Compliance health" value={score(snapshot.compliance_health_score)} />
                <Item term="ICL episodes" value={cell(snapshot.icl_episode_count)} />
                <Item term="Patterns" value={cell(snapshot.pattern_count)} />
              </DescriptionList>
            </CardBody>
          </Card>

          <Title headingLevel="h2" size="lg">
            Agents
          </Title>
          {agents.length === 0 ? (
            <p className="acc-source">No agent has sent a heartbeat.</p>
          ) : (
            <Table
              ariaLabel="Agents"
              columns={COLUMNS}
              rows={agents}
              rowKey={(r) => r.agent_id}
            />
          )}
        </>
      )}
    </div>
  );
}

function Item({ term, value }: { term: string; value: string | number }) {
  return (
    <DescriptionListGroup>
      <DescriptionListTerm>{term}</DescriptionListTerm>
      <DescriptionListDescription>{value}</DescriptionListDescription>
    </DescriptionListGroup>
  );
}

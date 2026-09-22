// Comms — what the agents are telling each other: the knowledge feed, the
// signal-flow log and the episode-nomination queue. Read-only, from the snapshot.

import { EmptyState, EmptyStateBody, Title } from "@patternfly/react-core";
import { useSnapshot } from "../../state/snapshot";
import { Table, cell } from "../../components/Table";
import type { Column } from "../../components/Table";

type Row = Record<string, unknown>;

const col = (key: string, label = key): Column<Row> => ({
  key,
  label,
  render: (r) => (key === "snippet" ? cell(r[key]) : <code>{cell(r[key])}</code>),
});

const SECTIONS: { title: string; field: string; columns: Column<Row>[]; empty: string }[] = [
  {
    title: "Knowledge feed",
    field: "knowledge_feed",
    columns: [col("tag"), col("source_agent", "source"), col("confidence"), col("snippet")],
    empty: "No knowledge has been shared yet.",
  },
  {
    title: "Signal-flow log",
    field: "signal_flow_log",
    columns: [col("signal_type", "signal"), col("source_agent", "source"), col("key_field", "key")],
    empty: "No signal has been seen yet.",
  },
  {
    title: "Episode-nomination queue",
    field: "episode_nominees",
    columns: [col("episode_id", "episode"), col("agent"), col("score"), col("status")],
    empty: "No episode is nominated.",
  },
];

export function CommsPage() {
  const { snapshot } = useSnapshot();
  if (!snapshot) {
    return (
      <div className="acc-page">
        <Title headingLevel="h1" size="2xl">Comms</Title>
        <EmptyState titleText="Waiting for the first heartbeat" headingLevel="h2" variant="sm">
          <EmptyStateBody>Nothing has arrived from the bus for this collective yet.</EmptyStateBody>
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Comms</Title>
      {SECTIONS.map((s) => {
        const rows = (Array.isArray(snapshot[s.field]) ? snapshot[s.field] : []) as Row[];
        return (
          <section key={s.field} aria-label={s.title} className="acc-page">
            <Title headingLevel="h2" size="lg">{s.title}</Title>
            {rows.length === 0 ? (
              <p className="acc-source">{s.empty}</p>
            ) : (
              <Table ariaLabel={s.title} columns={s.columns} rows={rows} rowKey={(_, i) => String(i)} />
            )}
          </section>
        );
      })}
    </div>
  );
}

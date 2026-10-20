// My contributions — your notes, and how far they travelled
// (20260928-rdf-provenance-and-memory-views, RDF-07).
//
// Counted in instances and tasks, never in people: this page shows that what you
// taught is used, and never who used it.

import { useEffect, useState } from "react";
import {
  Alert, Button, Card, CardBody, CardTitle, EmptyState, EmptyStateBody, Label, Title,
} from "@patternfly/react-core";
import { fetchContributions } from "../../api/client";
import type { ContributionsView } from "../../api/client";
import { Table } from "../../components/Table";
import { CeilingLabel } from "../../components/KnowledgeLabels";
import { useSnapshot } from "../../state/snapshot";
import { lineageHref } from "./Memory";

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card isCompact>
      <CardTitle>{label}</CardTitle>
      <CardBody><span style={{ fontSize: "var(--pf-t--global--font--size--2xl)" }}>{value}</span></CardBody>
    </Card>
  );
}

export function ContributionsPage() {
  const { collectiveId } = useSnapshot();
  const [view, setView] = useState<ContributionsView | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!collectiveId) return;
    fetchContributions(collectiveId).then((v) => { setView(v); setErr(""); }).catch((e) => setErr(String(e)));
  }, [collectiveId]);

  const notes = view?.notes ?? [];
  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">My contributions</Title>
      <p className="acc-source">
        The notes that rest on what you asked, and how far each travelled — in instances and
        tasks, never in people.
      </p>
      {err && <Alert isInline variant="danger" title="Contributions could not be read" component="p">{err}</Alert>}
      {view && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(10rem, 1fr))",
                      gap: "var(--pf-t--global--spacer--md)", marginBlock: "var(--pf-t--global--spacer--md)" }}>
          <Stat label="Notes" value={notes.length} />
          <Stat label="In the enterprise hub" value={notes.filter((n) => n.in_hub).length} />
          <Stat label="Instances holding a copy" value={view.instances} />
          <Stat label="Tasks that used them" value={notes.reduce((n, r) => n + r.used_by_tasks, 0)} />
        </div>
      )}
      {view && notes.length === 0 && (
        <EmptyState titleText="Nothing yet" headingLevel="h2" variant="sm">
          <EmptyStateBody>No note rests on your requests yet, or none you can see under your ceiling.</EmptyStateBody>
        </EmptyState>
      )}
      {notes.length > 0 && (
        <Table
          ariaLabel="My notes"
          rowKey={(r) => r.note_id}
          rows={notes}
          columns={[
            { key: "summary", label: "Note", render: (r) => r.summary || <code>{r.note_id.slice(0, 12)}</code> },
            { key: "role", label: "Role", render: (r) => r.role || "—" },
            { key: "ceiling", label: "Ceiling", render: (r) => <CeilingLabel ceiling={r.ceiling} /> },
            { key: "where", label: "Published to", render: (r) => (
              r.published_to.length === 0 ? "not published" : (
                <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
                  {r.published_to.map((w) => (
                    <Label key={w} isCompact color={w.startsWith("hub:") ? "purple" : "blue"}>{w}</Label>
                  ))}
                </span>
              )
            ) },
            { key: "instances", label: "Instances", render: (r) => r.instances },
            { key: "used", label: "Used by", render: (r) => `${r.used_by_tasks} task(s)` },
            { key: "lineage", label: "", render: (r) => (
              <Button variant="link" isInline component="a" href={lineageHref(r.note_id)}>Lineage</Button>
            ) },
          ]}
        />
      )}
    </div>
  );
}

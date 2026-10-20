// Memory — the notes the agents hold, per tier (20260928-rdf-provenance-and-memory-views).
//
// Private notes stay in the context they were distilled in; shared ones were
// published into another scope after a person approved; hub notes reached the
// enterprise brain after two. An operator sees every note under their ceiling;
// anyone else sees the notes they are a source of — the server decides, and
// nothing hidden is counted for them.

import { useEffect, useState } from "react";
import { Alert, Button, EmptyState, EmptyStateBody, Label, Title } from "@patternfly/react-core";
import { fetchMemory } from "../../api/client";
import type { MemoryView, NoteRow, Tier } from "../../api/client";
import { Table } from "../../components/Table";
import { CeilingLabel } from "../../components/KnowledgeLabels";
import { useSnapshot } from "../../state/snapshot";
import { useEnvironment } from "../../shell/EnvironmentContext";

const TIERS: { tier: Tier; title: string; where: string; blurb: string }[] = [
  { tier: "private", title: "Private", where: "Distilled in",
    blurb: "Read only in the context the note was distilled in." },
  { tier: "shared", title: "Shared", where: "Published into",
    blurb: "Published into another context after a person approved it." },
  { tier: "hub", title: "Enterprise hub", where: "Hub tier",
    blurb: "Promoted to the hub every bound instance reads; HIGH and CRITICAL notes need two operators." },
];

export const lineageHref = (noteId: string) =>
  `#/knowledge/lineage?note=${encodeURIComponent(noteId)}`;

function NotesTable({ rows, where }: { rows: NoteRow[]; where: string }) {
  return (
    <Table
      ariaLabel={`${where} notes`}
      rowKey={(r) => `${r.collective}/${r.role}/${r.where}/${r.note_id}`}
      rows={rows}
      columns={[
        { key: "summary", label: "Note", render: (r) => (
          <>
            {r.summary}
            {r.dissent && <p className="acc-reason">disputed: {r.dissent}</p>}
          </>
        ) },
        { key: "role", label: "Role", render: (r) => r.role },
        { key: "where", label: where, render: (r) => <code>{r.where}</code> },
        { key: "ceiling", label: "Ceiling", render: (r) => <CeilingLabel ceiling={r.ceiling} /> },
        { key: "people", label: "Rests on", render: (r) => (
          <>
            {r.people} person(s){r.people < 2 && <> <Label isCompact color="orange">single source</Label></>}
            {r.mine && <> <Label isCompact color="blue">yours</Label></>}
            {r.stored_only && <> <Label isCompact variant="outline">from storage</Label></>}
          </>
        ) },
        { key: "lineage", label: "", render: (r) => (
          r.note_id
            ? <Button variant="link" isInline component="a" href={lineageHref(r.note_id)}>Lineage</Button>
            : "—"
        ) },
      ]}
    />
  );
}

export function MemoryPage() {
  const { collectiveId } = useSnapshot();
  const { who } = useEnvironment();
  const [view, setView] = useState<MemoryView | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!collectiveId) return;
    fetchMemory(collectiveId).then((v) => { setView(v); setErr(""); }).catch((e) => setErr(String(e)));
  }, [collectiveId]);

  const total = view ? TIERS.reduce((n, t) => n + view.tiers[t.tier].length, 0) : 0;
  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Memory</Title>
      <p className="acc-source">
        {who?.role === "viewer"
          ? "The notes you are a source of, in every tier they reached."
          : "Every note the agents hold under your ceiling, by how far it has travelled."}
      </p>
      {err && <Alert isInline variant="danger" title="Memory could not be read" component="p">{err}</Alert>}
      {view && !view.available && (
        <Alert isInline variant="warning" title="No working memory" component="p">
          This WebGUI has no Redis connection, so the note caches cannot be read.
        </Alert>
      )}
      {view && (view.above_your_ceiling ?? 0) > 0 && (
        <p className="acc-reason">{view.above_your_ceiling} note(s) sit above your ceiling and are not shown.</p>
      )}
      {view && view.available && total === 0 && (
        <EmptyState titleText="No notes" headingLevel="h2" variant="sm">
          <EmptyStateBody>No note you can see has been distilled yet.</EmptyStateBody>
        </EmptyState>
      )}
      {view && total > 0 && TIERS.map((t) => view.tiers[t.tier].length > 0 && (
        <section key={t.tier} aria-label={t.title} style={{ marginBlockStart: "var(--pf-t--global--spacer--lg)" }}>
          <Title headingLevel="h2" size="lg">{t.title} <Label isCompact>{view.tiers[t.tier].length}</Label></Title>
          <p className="acc-source">{t.blurb}</p>
          <NotesTable rows={view.tiers[t.tier]} where={t.where} />
        </section>
      ))}
    </div>
  );
}

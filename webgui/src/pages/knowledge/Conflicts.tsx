// Conflicts — two sources disagreeing (20260928-rdf-provenance-and-memory-views, #516).
//
// One subject and predicate with different values in different graphs. The page
// shows every claim with its graph, ceiling and source, and never picks one: the
// graph finds the disagreement, people decide (RDF-00 §3).

import { useEffect, useState } from "react";
import { Alert, Button, EmptyState, EmptyStateBody, Title } from "@patternfly/react-core";
import { fetchConflicts } from "../../api/client";
import type { ConflictsView } from "../../api/client";
import { Table } from "../../components/Table";
import { CeilingLabel } from "../../components/KnowledgeLabels";
import { useSnapshot } from "../../state/snapshot";

export function ConflictsPage() {
  const { collectiveId } = useSnapshot();
  const [view, setView] = useState<ConflictsView | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!collectiveId) return;
    fetchConflicts(collectiveId).then((v) => { setView(v); setErr(""); })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [collectiveId]);

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Conflicts</Title>
      <p className="acc-source">
        Where two sources disagree about the same thing. Every claim is shown with where it came
        from; nothing here decides which is right.
      </p>
      {err && <Alert isInline variant="danger" title="Conflicts could not be read" component="p">{err}</Alert>}
      {view && !view.available && (
        <Alert isInline variant="info" title="No knowledge store yet" component="p">
          An operator builds it with <code>acc-cli kg sync</code>.
        </Alert>
      )}
      {view && view.available && view.conflicts.length === 0 && (
        <EmptyState titleText="No disagreement you can see" headingLevel="h2" variant="sm">
          <EmptyStateBody>No two readable graphs give different values for the same thing.</EmptyStateBody>
        </EmptyState>
      )}
      {view && view.conflicts.length > 0 && (
        <Table
          ariaLabel="Conflicts"
          rowKey={(c) => `${c.subject}|${c.predicate}`}
          rows={view.conflicts}
          columns={[
            { key: "subject", label: "About", render: (c) => (
              <Button variant="link" isInline component="a"
                      href={`#/knowledge/map?focus=${encodeURIComponent(c.subject)}`}>{c.subject_label}</Button>
            ) },
            { key: "predicate", label: "Property", render: (c) => c.predicate_label },
            { key: "claims", label: "Claims", render: (c) => (
              <ul style={{ margin: 0, paddingInlineStart: "1rem" }}>
                {c.values.map((v) => (
                  <li key={`${v.graph}|${v.value}`}>
                    <strong>{v.value}</strong> — <code>{v.graph}</code> <CeilingLabel ceiling={v.ceiling} />
                    {v.source && <span className="acc-reason"> {v.source}</span>}
                  </li>
                ))}
              </ul>
            ) },
          ]}
        />
      )}
    </div>
  );
}

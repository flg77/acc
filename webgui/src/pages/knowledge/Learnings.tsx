// Learnings — what agents told each other (20260928-rdf-provenance-and-memory-views).
//
// Every reflected note also travels as a lesson (KNOWLEDGE_SHARE). This page joins
// each lesson to what became of it: which roles adopted it, how many tasks rendered
// it in their prompt, and how those tasks ended. The 7-day lesson copy expires; the
// note behind it does not.

import { useEffect, useState } from "react";
import { Alert, EmptyState, EmptyStateBody, Label, Title } from "@patternfly/react-core";
import { fetchLearnings } from "../../api/client";
import type { LearningsView } from "../../api/client";
import { Table } from "../../components/Table";
import { CeilingLabel } from "../../components/KnowledgeLabels";
import { useSnapshot } from "../../state/snapshot";

const VERDICT: Record<string, "green" | "red" | "orange" | "grey"> = {
  GOOD: "green", BAD: "red", NEEDS_REVISE: "orange",
};

export function LearningsPage() {
  const { collectiveId } = useSnapshot();
  const [view, setView] = useState<LearningsView | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!collectiveId) return;
    fetchLearnings(collectiveId).then((v) => { setView(v); setErr(""); }).catch((e) => setErr(String(e)));
  }, [collectiveId]);

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Learnings</Title>
      <p className="acc-source">
        Lessons the agents passed to each other, newest first, and what became of each.
      </p>
      {err && <Alert isInline variant="danger" title="Learnings could not be read" component="p">{err}</Alert>}
      {view && view.lessons.length === 0 && (
        <EmptyState titleText="No lessons" headingLevel="h2" variant="sm">
          <EmptyStateBody>No lesson you can see is in the 7-day window.</EmptyStateBody>
        </EmptyState>
      )}
      {view && view.lessons.length > 0 && (
        <Table
          ariaLabel="Lessons"
          rowKey={(l) => l.lesson_id}
          rows={view.lessons}
          columns={[
            { key: "summary", label: "Lesson", render: (l) => (
              <>
                {l.summary}
                {l.dissent && <p className="acc-reason">disputed: {l.dissent}</p>}
              </>
            ) },
            { key: "from", label: "From", render: (l) => (
              <>
                {l.role || "—"}
                {l.kind !== "note" && <> <Label isCompact variant="outline">{l.kind.replace("_", " ")}</Label></>}
                {l.addressed && <> <Label isCompact variant="outline">addressed</Label></>}
              </>
            ) },
            { key: "domain", label: "Domain", render: (l) => l.domain || "any" },
            { key: "ceiling", label: "Ceiling", render: (l) => <CeilingLabel ceiling={l.ceiling} /> },
            { key: "adopted", label: "Adopted by", render: (l) => l.adopted_by.join(", ") || "—" },
            { key: "used", label: "Used by", render: (l) => `${l.used_by_tasks} task(s)` },
            { key: "outcomes", label: "Outcomes", render: (l) => {
              const entries = Object.entries(l.outcomes);
              return entries.length === 0 ? "—" : (
                <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
                  {entries.map(([v, n]) => (
                    <Label key={v} isCompact color={VERDICT[v] ?? "grey"}>{v.toLowerCase().replace("_", " ")} {n}</Label>
                  ))}
                </span>
              );
            } },
            { key: "confidence", label: "Confidence", render: (l) => l.confidence.toFixed(2) },
          ]}
        />
      )}
    </div>
  );
}

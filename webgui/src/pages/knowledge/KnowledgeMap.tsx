// Knowledge map — what the knowledge store holds that you may read
// (20260928-rdf-provenance-and-memory-views, over the named-graph store, #516).
//
// Three parts: the graphs you may read (each with its ceiling and source), the
// domain taxonomy as a tree, and — for any node you name or click — that node's
// neighbourhood across every readable graph. The store binds the ceiling; a
// non-operator is shown no provenance graph and no person.

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Button, EmptyState, EmptyStateBody, Label, TextInput, Title } from "@patternfly/react-core";
import { fetchKnowledgeMap } from "../../api/client";
import type { MapView } from "../../api/client";
import { Table } from "../../components/Table";
import { CeilingLabel } from "../../components/KnowledgeLabels";
import { KnowledgeGraph } from "../../components/KnowledgeGraph";
import { useSnapshot } from "../../state/snapshot";

const focusHref = (iri: string) => `#/knowledge/map?focus=${encodeURIComponent(iri)}`;

function ConceptTree({ concepts }: { concepts: MapView["concepts"] }) {
  const children = (parent: string) => concepts.filter((c) => c.broader === parent);
  const known = new Set(concepts.map((c) => c.iri));
  const roots = concepts.filter((c) => !c.broader || !known.has(c.broader));
  const render = (c: MapView["concepts"][number], depth: number): JSX.Element => (
    <li key={c.iri}>
      <Button variant="link" isInline component="a" href={focusHref(c.iri)}>{c.label}</Button>
      {children(c.iri).length > 0 && depth < 8 && (
        <ul>{children(c.iri).map((k) => render(k, depth + 1))}</ul>
      )}
    </li>
  );
  return <ul aria-label="Domain taxonomy">{roots.map((c) => render(c, 0))}</ul>;
}

export function KnowledgeMapPage() {
  const { collectiveId } = useSnapshot();
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus") ?? "";
  const [draft, setDraft] = useState(focus);
  const [view, setView] = useState<MapView | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setDraft(focus);
    if (!collectiveId) return;
    fetchKnowledgeMap(collectiveId, focus)
      .then((v) => { setView(v); setErr(""); })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [collectiveId, focus]);

  const attrs = view?.nodes.find((n) => n.kind === "focus")?.attributes ?? {};
  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Knowledge map</Title>
      <p className="acc-source">
        What the knowledge store holds that you may read: its graphs, the domain taxonomy, and
        any node's neighbourhood.
      </p>
      {err && <Alert isInline variant="danger" title="The knowledge store could not be read" component="p">{err}</Alert>}
      {view && !view.available && (
        <Alert isInline variant="info" title="No knowledge store yet" component="p">
          An operator builds it with <code>acc-cli kg sync</code>.
        </Alert>
      )}
      {view && (view.above_your_ceiling ?? 0) > 0 && (
        <p className="acc-reason">{view.above_your_ceiling} graph(s) sit above your ceiling and are not shown.</p>
      )}
      {view && view.available && (
        <>
          <section aria-label="Graphs" style={{ marginBlockStart: "var(--pf-t--global--spacer--md)" }}>
            <Title headingLevel="h2" size="lg">Graphs <Label isCompact>{view.graphs.length}</Label></Title>
            <Table
              ariaLabel="Readable graphs"
              rowKey={(g) => g.name}
              rows={view.graphs}
              columns={[
                { key: "name", label: "Graph", render: (g) => <code>{g.name}</code> },
                { key: "ceiling", label: "Ceiling", render: (g) => <CeilingLabel ceiling={g.ceiling} /> },
                { key: "triples", label: "Triples", render: (g) => g.triples },
                { key: "source", label: "Source", render: (g) => g.source || "—" },
              ]}
            />
          </section>
          {view.concepts.length > 0 && (
            <section aria-label="Domains" style={{ marginBlockStart: "var(--pf-t--global--spacer--lg)" }}>
              <Title headingLevel="h2" size="lg">Domains</Title>
              <ConceptTree concepts={view.concepts} />
            </section>
          )}
          <section aria-label="Neighbourhood" style={{ marginBlockStart: "var(--pf-t--global--spacer--lg)" }}>
            <Title headingLevel="h2" size="lg">Neighbourhood</Title>
            <form
              style={{ display: "flex", gap: "var(--pf-t--global--spacer--sm)", maxInlineSize: "40rem" }}
              onSubmit={(e) => { e.preventDefault(); setParams(draft ? { focus: draft.trim() } : {}); }}
            >
              <TextInput aria-label="Focus IRI" placeholder="urn:acc:… (or pick a domain above)"
                         value={draft} onChange={(_, v) => setDraft(v)} />
              <Button type="submit" variant="secondary">Show</Button>
            </form>
            {focus && view.nodes.length === 0 && (
              <EmptyState titleText="Nothing readable around this node" headingLevel="h3" variant="sm">
                <EmptyStateBody>No readable graph says anything about it.</EmptyStateBody>
              </EmptyState>
            )}
            {Object.keys(attrs).length > 0 && (
              <Table
                ariaLabel="Focus values"
                rowKey={(r) => r[0]}
                rows={Object.entries(attrs)}
                columns={[
                  { key: "p", label: "Property", render: (r) => r[0] },
                  { key: "v", label: "Value(s)", render: (r) => r[1].join(" · ") },
                ]}
              />
            )}
            {view.nodes.length > 1 && <KnowledgeGraph view={view} ariaLabel="Neighbourhood, as a table" />}
          </section>
        </>
      )}
    </div>
  );
}

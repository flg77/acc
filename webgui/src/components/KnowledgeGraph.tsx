// The lineage of one note as a graph (20260928-rdf-provenance-and-memory-views,
// RDF-07): episodes → note → proposals → copies ← approvals, → tasks.
//
// reactflow draws it, dagre lays it out left to right, deterministically. Every
// node says its tier and ceiling, and a disputed note says so: a clean picture
// raises trust whether or not it is right (arXiv 2505.21512), so uncertainty is
// drawn, not hidden. The table under the graph lists the same nodes, for screen
// readers and for anyone who would rather read than look.

import { useMemo } from "react";
import ReactFlow, { Background, Controls, MarkerType } from "reactflow";
import type { Edge, Node } from "reactflow";
import "reactflow/dist/style.css";
import dagre from "@dagrejs/dagre";
import { Label } from "@patternfly/react-core";
import type { GraphNode, GraphView } from "../api/client";
import { Table } from "./Table";
import { CeilingLabel, TierLabel } from "./KnowledgeLabels";

const W = 220;
const H = 84;

const KIND: Record<string, string> = {
  note: "Note",
  episodes: "Episodes",
  proposal: "Proposal",
  copy: "Published copy",
  approval: "Approval",
  tasks: "Tasks",
  invalidated: "Erased / rolled back",
  focus: "Focus",
  entity: "Linked",
  person: "Person",
};

function NodeBody({ n }: { n: GraphNode }) {
  return (
    <div style={{ display: "grid", gap: 4, textAlign: "start" }}>
      <strong style={{ fontSize: "0.8rem" }}>{n.label}</strong>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {n.tier && <TierLabel tier={n.tier} />}
        {n.ceiling && <CeilingLabel ceiling={n.ceiling} />}
        {n.people !== undefined && <Label isCompact variant="outline">{n.people} person(s)</Label>}
        {n.dissent && <Label isCompact color="orange">disputed</Label>}
      </div>
    </div>
  );
}

function layout(view: GraphView): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 72 });
  g.setDefaultEdgeLabel(() => ({}));
  view.nodes.forEach((n) => g.setNode(n.id, { width: W, height: H }));
  view.edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  const nodes: Node[] = view.nodes.map((n) => {
    const p = g.node(n.id);
    return {
      id: n.id,
      position: { x: p.x - W / 2, y: p.y - H / 2 },
      data: { label: <NodeBody n={n} /> },
      draggable: false,
      connectable: false,
      style: {
        width: W,
        minHeight: H,
        borderWidth: n.kind === "note" ? 2 : 1,
        borderStyle: n.kind === "invalidated" ? "dashed" : "solid",
        background: "var(--pf-t--global--background--color--primary--default)",
        color: "var(--pf-t--global--text--color--regular)",
      },
    };
  });
  const edges: Edge[] = view.edges.map((e, i) => ({
    id: `e${i}`,
    source: e.source,
    target: e.target,
    label: e.kind.replace("_", " "),
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
  return { nodes, edges };
}

export function KnowledgeGraph({ view, ariaLabel = "Lineage, as a table" }: { view: GraphView; ariaLabel?: string }) {
  const { nodes, edges } = useMemo(() => layout(view), [view]);
  const incoming = (id: string) =>
    view.edges.filter((e) => e.target === id).map((e) => `${e.kind.replace("_", " ")} ← ${
      view.nodes.find((n) => n.id === e.source)?.label ?? e.source}`);
  return (
    <div style={{ display: "grid", gap: "var(--pf-t--global--spacer--md)" }}>
      <div style={{ blockSize: "28rem", border: "1px solid var(--pf-t--global--border--color--default)" }}
           aria-hidden="true">
        <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false}
                   nodesConnectable={false} proOptions={{ hideAttribution: true }}>
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <Table
        ariaLabel={ariaLabel}
        rowKey={(n) => n.id}
        rows={view.nodes}
        columns={[
          { key: "kind", label: "What", render: (n) => KIND[n.kind] ?? n.kind },
          { key: "label", label: "Detail", render: (n) => n.label },
          { key: "tier", label: "Tier", render: (n) => (n.tier ? <TierLabel tier={n.tier} /> : "—") },
          { key: "ceiling", label: "Ceiling", render: (n) => (n.ceiling ? <CeilingLabel ceiling={n.ceiling} /> : "—") },
          { key: "from", label: "Linked from", render: (n) => incoming(n.id).join("; ") || "—" },
        ]}
      />
    </div>
  );
}

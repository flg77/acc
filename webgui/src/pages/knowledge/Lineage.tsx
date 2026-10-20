// Lineage — why one note exists, and where it went (20260928-rdf-provenance-and-memory-views).
//
// Drawn from the PROV-O projection of the refinement ledger (RDF-03): the episodes
// the note rests on, the proposals that asked to move it, every copy it reached and
// who approved each, and the tasks that used it. A viewer sees "a person" where an
// operator sees who; a note you may not see reads exactly like a note that is not
// there.

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Button, TextInput, Title } from "@patternfly/react-core";
import { fetchLineage } from "../../api/client";
import type { LineageView } from "../../api/client";
import { KnowledgeGraph } from "../../components/KnowledgeGraph";
import { useSnapshot } from "../../state/snapshot";

export function LineagePage() {
  const { collectiveId } = useSnapshot();
  const [params, setParams] = useSearchParams();
  const note = params.get("note") ?? "";
  const [draft, setDraft] = useState(note);
  const [view, setView] = useState<LineageView | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setDraft(note);
    if (!collectiveId || !note) { setView(null); return; }
    fetchLineage(collectiveId, note)
      .then((v) => { setView(v); setErr(""); })
      .catch((e) => {
        setView(null);
        const msg = e instanceof Error ? e.message : String(e);
        setErr(msg.startsWith("404") ? `No note "${note}" that you can see.` : msg);
      });
  }, [collectiveId, note]);

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Lineage</Title>
      <p className="acc-source">
        Where a note came from and everywhere it went. Open one from Memory, or give its id
        (a prefix is enough).
      </p>
      <form
        style={{ display: "flex", gap: "var(--pf-t--global--spacer--sm)", maxInlineSize: "32rem" }}
        onSubmit={(e) => { e.preventDefault(); setParams(draft ? { note: draft.trim() } : {}); }}
      >
        <TextInput aria-label="Note id" placeholder="note id" value={draft} onChange={(_, v) => setDraft(v)} />
        <Button type="submit" variant="secondary">Show</Button>
      </form>
      {err && <Alert isInline variant="warning" title={err} component="p" />}
      {view && (
        <section style={{ marginBlockStart: "var(--pf-t--global--spacer--lg)" }} aria-label="Lineage graph">
          {(view.above_your_ceiling ?? 0) > 0 && (
            <p className="acc-reason">{view.above_your_ceiling} part(s) sit above your ceiling and are not drawn.</p>
          )}
          <KnowledgeGraph view={view} />
        </section>
      )}
    </div>
  );
}

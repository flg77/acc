---
type: howto
area: rdf-support
id: RDF-07-howto
status: current
created: 2026-10-05
applies-to: "acc-spearhead #499 (stacked on #510 / #511 / #512)"
index: "[[_INDEX — RDF support roadmap items]]"
related: ["[[RDF-07 — Seeing the knowledge — graph, lineage and my-contributions views]]", "[[RDF-03 — The provenance graph — PROV-O over the memory lifecycle and erasure by person]]"]
tags: [howto, rdf, memory, tui, webgui, obsidian]
---

# HOWTO — Seeing what the agents know

ACC's agents remember (memory notes), learn from each other (lessons), and promote what they learned toward the enterprise hub. Six views show it. They are the same in the **Web GUI** (section *Knowledge*), the **TUI** (screen *Memory*) and an **Obsidian** export:

| View | Answers |
|---|---|
| **Memory** | What the agents hold, per tier: *private* (read only where it was distilled), *shared* (published into another context after a person approved), *enterprise hub* (HIGH and CRITICAL need two operators) |
| **Learnings** | Which lessons agents passed to each other, which roles adopted them, how many tasks used them, and how those tasks ended |
| **Lineage** | Why one note exists: episodes → note → proposals → every copy it reached and who approved it → tasks that used it, plus any erasure or rollback |
| **My contributions** | The notes that rest on *your* requests and how far they travelled, counted in instances and tasks, never in people |
| **Knowledge map** | What the knowledge store (`acc-cli kg sync`) holds that you may read: its graphs with ceiling and source, the domain taxonomy as a tree, and any node's neighbourhood |
| **Conflicts** | Where two graphs give different values for the same thing. Both claims are shown with where they came from; nothing decides which is right |

## Who sees what

One rule applies on every surface. The server applies it before anything reaches your screen:

- An **operator** sees every note at or under their ceiling. They are also told how many notes sit above it.
- **Anyone else** sees only the notes **they are a source of**. One person in two rooms counts as one person.
- A hidden note is **left out, not counted**. A lineage you may not see reads exactly like "no note".
- Only an operator learns **who** approved; anyone else reads *a person (operator)*. A note says how many people it rests on, never who they are.
- Ceilings: `LOW < MEDIUM < HIGH < CRITICAL`. A note carries the highest ceiling of the tasks it was distilled from. A note with no label reads as CRITICAL.
- A web **viewer** has a LOW ceiling, a **requester** MEDIUM, an **operator** CRITICAL. So a web viewer sees nothing from notes at MEDIUM or above, even their own.

## Web GUI

1. Open the WebGUI and pick the collective.
2. Go to **Knowledge** (top navigation). There are six tabs: Memory · Learnings · Lineage · My contributions · Knowledge map · Conflicts.
3. In **Memory**, click **Lineage** on any row to open that note's graph.
   - Each node shows its tier and ceiling.
   - *disputed* marks recorded dissent; *single source* means one person.
   - *from storage* means the note came from the durable note store; the 6-hour Redis cache no longer holds it.
   - The table under the graph lists the same nodes.
4. In **Lineage** you can also type a note id. A prefix is enough.

API, for scripts. Every endpoint is GET with the viewer role, built for the session user:

```
/api/knowledge/<collective>/memory
/api/knowledge/<collective>/learnings?limit=50
/api/knowledge/<collective>/lineage/<note-id>          # ?format=jsonld for PROV-O JSON-LD
/api/knowledge/<collective>/contributions
/api/knowledge/<collective>/map?focus=<iri>              # needs the knowledge store (acc-cli kg sync)
/api/knowledge/<collective>/conflicts
```

**Knowledge map and Conflicts** read the collective's named-graph store (#516). An operator builds it with `acc-cli kg sync`. The store decides what you may read by ceiling, graph by graph. On these two tabs a non-operator never sees the provenance graphs (Lineage and My contributions show those, filtered to your own notes) and never sees a person. Pick a domain in the tree to see what links to it, or paste any `urn:acc:…` IRI into *Neighbourhood*. Conflicts compare literal values in different graphs, ignoring multi-valued properties such as `skos:altLabel`. Until agents propose facts (RDF-05) there is little to disagree about.

## TUI

1. Open the Memory screen: `Ctrl+A`, then its digit.
   - On the `operator` profile the digit is `3` (Marketplace 0, Catalogs 1, Board 2, Memory 3).
   - On the `user` profile it is `1` (Board 0, Memory 1).
   - Or use `Ctrl+P` → "Go to Memory".
2. The six tabs are the same as in the WebGUI.
   - `Enter` on a Memory or My-contributions row opens that note's lineage as a tree. A node reached twice is drawn once and marked `↺`.
   - `r` refreshes.
3. Where the data comes from:
   - **Next to the collective** (the same host as `acc-cli`), the TUI reads Redis, the durable note store and the refinement ledger itself, as the person at the keyboard.
   - **Remote:** set the WebGUI URL and a token. Who you are is whoever the token belongs to.
     ```
     export ACC_WEBGUI_URL=https://acc-webgui.example:8080
     export ACC_WEBGUI_TOKEN=<your bearer token>
     acc-tui
     ```

## Obsidian

Export the Memory view as an OKF bundle and open it as a vault:

```
acc-cli memory export --okf ~/vaults/acc-memory          # refuses a non-empty dir without --force
```

- `notes/<id>.md` holds one page per note, merged across the tiers it reached. It records the ceiling, the people it rests on (as a count), any dissent, and where it is held.
- `roles/<role>.md` and `tiers/<tier>.md` are hub pages. Every note links to them with `[[wikilinks]]`, so Obsidian's **graph view** clusters notes by the role that learned them and by how far they travelled.
- Tags: `tier/…`, `ceiling/…`, `role/…`, `disputed`. Filter the graph with them, for example `tag:#ceiling/high`.
- The export is built for **whoever runs the command**, so it holds only what they may see. Re-run it with `--force` to refresh.

## On the command line

```
acc-cli memory lineage <note-id>              # tree; --as turtle|ntriples|jsonld; --check
acc-cli memory forget --person <p> --plan     # operator: everything resting on one person
acc-cli lessons list | show <id> | trace <id>
acc-cli refine list | show <id>
```

## When something looks empty

| You see | Why | Do |
|---|---|---|
| "No working memory" / `available: false` | No Redis connection and no LanceDB note store found | Check `working_memory.url` and `vector_db.lancedb_path` in the config |
| Few private notes | The Redis cache keeps the top notes per scope for 6 hours | Durable notes appear too once the LanceDB directory is found. Older rows have no ceiling and read as CRITICAL |
| A lesson vanished from Learnings | The lesson copy lasts 7 days | The note behind it is still in Memory |
| A lineage has gaps | It is rebuilt from the refinement ledger; the Redis mirror keeps 30 days | `acc-cli memory lineage --check` lists notes no ledger row explains |
| A viewer sees nothing | The note's ceiling is above theirs, or they are not a source of it | As intended. An operator can confirm the count above their ceiling |
| Approvers read "a person" | You are not an operator | As intended |
| Knowledge map says "No knowledge store yet" | The store is built by an operator | `acc-cli kg sync` (needs the `rdf` extra: `pyoxigraph`) |
| No conflicts | Only one graph says anything about it, or you can read only one of the disagreeing graphs | As intended; an operator sees every graph under their ceiling |

## Related

- Plan: `openspec/changes/20260928-rdf-provenance-and-memory-views/`.
- Provenance: #512 (`acc/lib/rdf/prov.py`).
- Vocabulary: #510.
- The ledger: #511.
- Knowledge map and Conflicts: the PR stacked on #516 (`acc/knowledge_map.py`).

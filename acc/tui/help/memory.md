# Memory — what the agents remember, what they learned, and why

Four tabs over the same views the Web GUI's **Knowledge** section shows.

| Tab | What it answers |
|-----|-----------------|
| **Memory** | The notes the agents hold, per tier: *private* (read only where they were distilled), *shared* (published into another context after a person approved), *hub* (the enterprise brain; HIGH and CRITICAL notes need two operators). Each row shows its ceiling, how many people it rests on (*single* when one) and whether it is disputed. |
| **Learnings** | Lessons the agents passed to each other (newest first): which roles adopted each, how many tasks rendered it in their prompt, and how those tasks ended. The lesson copy lasts 7 days; the note behind it does not expire with it. |
| **Lineage** | One note's provenance as a tree: the episodes it rests on, the proposals that asked to move it, every copy it reached and who approved each, the tasks that used it, and any erasure or rollback. |
| **My contributions** | The notes that rest on your requests and how far they travelled, counted in instances and tasks — never in people. |

**Who sees what.** An operator sees every note at or under their ceiling. Anyone else sees only the notes they are a source of. A note you may not see is left out and not counted, and a lineage of one reads as "no note". A non-operator is told *a person* approved, never who.

**Where the data comes from.** Beside the collective (same host as `acc-cli`), the TUI reads Redis and the refinement ledger itself. With `ACC_WEBGUI_URL` (and `ACC_WEBGUI_TOKEN`) set, it reads the Web GUI's `/api/knowledge` endpoints instead, and the token decides who you are.

| Key | Action |
|-----|--------|
| `r` | Refresh every tab. |
| `Enter` | On a Memory or My-contributions row: open that note's lineage. In the Lineage box: show the lineage of the id typed (a prefix is enough). |

Reachable from every profile: `Ctrl+A` then its digit, or `Ctrl+P` → "Go to Memory".

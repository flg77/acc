"""LanceDB embedded vector backend.

Tables auto-created on first use with schemas defined in v0.1.0 Section 7.2.
All searches use cosine similarity (LanceDB default for normalized vectors).
"""

from __future__ import annotations

import logging
from typing import Any

import lancedb
import pyarrow as pa

logger = logging.getLogger("acc.backends.vector_lancedb")

# Standard ACC table schemas (v0.1.0 §7.2)
from acc.attribution import UNATTRIBUTED

_SCHEMAS: dict[str, pa.Schema] = {
    "episodes": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("agent_id", pa.utf8()),
        # WHO asked for the work this episode records.  A column rather than a
        # key inside payload_json, because a scope has to be enforceable at
        # query time -- an attribute buried in a JSON blob cannot be filtered,
        # indexed or redacted (20260823-attributed-memory).
        pa.field("requester", pa.utf8()),
        pa.field("ts", pa.float64()),
        pa.field("signal_type", pa.utf8()),
        pa.field("payload_json", pa.utf8()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ]),
    "patterns": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("pattern_type", pa.utf8()),
        pa.field("description", pa.utf8()),
        pa.field("confidence", pa.float32()),
        pa.field("created_at", pa.float64()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ]),
    "collective_mem": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("collective_id", pa.utf8()),
        pa.field("key", pa.utf8()),
        pa.field("value_json", pa.utf8()),
        pa.field("updated_at", pa.float64()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ]),
    "icl_results": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("agent_id", pa.utf8()),
        pa.field("rule_id", pa.utf8()),
        pa.field("context_json", pa.utf8()),
        pa.field("outcome", pa.utf8()),
        pa.field("confidence", pa.float32()),
        pa.field("created_at", pa.float64()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ]),
    # ACC-6a: role infusion tables
    "role_definitions": pa.schema([
        pa.field("id", pa.utf8()),                    # uuid
        pa.field("agent_id", pa.utf8()),
        pa.field("collective_id", pa.utf8()),
        pa.field("version", pa.utf8()),
        pa.field("purpose", pa.utf8()),
        pa.field("persona", pa.utf8()),
        pa.field("seed_context", pa.utf8()),
        pa.field("task_types_json", pa.utf8()),        # JSON array
        pa.field("allowed_actions_json", pa.utf8()),   # JSON array
        pa.field("category_b_overrides_json", pa.utf8()),  # JSON object
        pa.field("created_at", pa.float64()),
        pa.field("purpose_embedding", pa.list_(pa.float32(), 384)),  # centroid seed
    ]),
    "role_audit": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("agent_id", pa.utf8()),
        pa.field("ts", pa.float64()),
        pa.field("event_type", pa.utf8()),   # "loaded" | "updated" | "rejected"
        pa.field("old_version", pa.utf8()),
        pa.field("new_version", pa.utf8()),
        pa.field("diff_summary", pa.utf8()),
        pa.field("approver_id", pa.utf8()),  # arbiter agent_id for ROLE_UPDATE events
    ]),
    # PR-MEM1: self-reflective memory notes — durable, curated summaries
    # of clustered episodes.  Kept in a SEPARATE small table so vector
    # search over notes stays fast (few rows) and reads never scan the
    # large episodes table.  Written out-of-band by the reflection loop.
    "memory_notes": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("agent_id", pa.utf8()),
        pa.field("role_label", pa.utf8()),
        pa.field("ts", pa.float64()),
        pa.field("summary", pa.utf8()),
        # WHICH episodes, and which people, this note was distilled from --
        # JSON arrays.  source_count answered "how many" and nothing else, so a
        # contribution could never be traced back or removed, and a quorum could
        # not tell ten episodes from one person from one episode each from ten.
        pa.field("source_ids", pa.utf8()),
        pa.field("source_requesters", pa.utf8()),
        # Kept and still written: derived from source_ids, so existing readers
        # are unaffected.
        pa.field("source_count", pa.int64()),
        pa.field("confidence", pa.float32()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ]),
    # Proposal 024 P3 — governed RAG document store.  Two tables: a row
    # store of ingested documents (no vector) and the per-chunk index
    # (384-dim embedding).  Both stamped with collective_id for scoped
    # retrieval (acc/docstore.py).
    "documents": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("collective_id", pa.utf8()),
        pa.field("title", pa.utf8()),
        pa.field("source", pa.utf8()),
        pa.field("tags_json", pa.utf8()),
        pa.field("chunk_count", pa.int64()),
        pa.field("created_at", pa.float64()),
    ]),
    "doc_chunks": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("doc_id", pa.utf8()),
        pa.field("collective_id", pa.utf8()),
        pa.field("seq", pa.int64()),
        pa.field("text", pa.utf8()),
        pa.field("title", pa.utf8()),    # denormalized for citation
        pa.field("source", pa.utf8()),   # denormalized for citation
        pa.field("created_at", pa.float64()),
        pa.field("embedding", pa.list_(pa.float32(), 384)),
    ]),
}

_STANDARD_TABLES = list(_SCHEMAS.keys())

#: SQL literals used to backfill a column added to a table that already exists.
#: A pre-existing row predates attribution, so it reads as UNATTRIBUTED rather
#: than as belonging to whoever is asking now -- a migration that silently
#: assigns ownership is worse than one that admits it cannot.
_BACKFILL: dict[str, str] = {
    "requester": f"'{UNATTRIBUTED}'",
    "source_ids": "'[]'",
    "source_requesters": "'[]'",
}


class LanceDBBackend:
    """LanceDB embedded vector database backend.

    The database is stored at *path* on the local filesystem.  All four
    standard ACC tables are auto-created at construction time if absent.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        try:
            self._db = lancedb.connect(path)
        except PermissionError as e:
            raise RuntimeError(
                f"Cannot open LanceDB at {path!r} ({e}). "
                "A named Docker/Podman volume is often root-only: use an image that "
                "includes deploy/entrypoint-agent.sh (prepares /app/data/lancedb, "
                "then runs the app as UID 1001), or set ACC_LANCEDB_PATH to a "
                "writable directory."
            ) from e
        # Auto-create standard tables, then bring any that predate a schema
        # change up to date.  create_table(exist_ok=True) returns the table as
        # it is on disk, so without this an older database silently keeps the
        # old columns and every write of a new field fails.
        for table_name in _STANDARD_TABLES:
            self.create_table_if_absent(table_name, _SCHEMAS[table_name])
            self._add_missing_columns(table_name, _SCHEMAS[table_name])

    def create_table_if_absent(self, table: str, schema: Any) -> None:
        """Create *table* with *schema* if it does not already exist.

        The pre-check is load-bearing rather than defensive.
        ``create_table(exist_ok=True)`` still RAISES when the stored table's
        schema differs from the one offered, so after a schema change every
        existing database would fail to open here -- before
        :meth:`_add_missing_columns` ever got the chance to bring it up to date.
        An older table is a case to migrate, not to reject.
        """
        existing = self._table_names()

        if table in existing:
            return
        self._db.create_table(table, schema=schema, exist_ok=True)

    def _table_names(self) -> set[str]:
        """Names of the tables that exist, across LanceDB versions.

        ``list_tables()`` superseded ``table_names()`` but returns a response
        OBJECT rather than a list of names, so the two cannot be swapped
        blindly -- do it and every lookup misses, which reads as "no tables
        yet" and sends the caller down the create path.
        """
        try:
            lister = getattr(self._db, "list_tables", None)
            if lister is not None:
                result = lister()
                names = getattr(result, "tables", result)
            else:
                names = self._db.table_names()
            return {str(n) for n in names}
        except Exception:
            logger.debug("lancedb: cannot list tables", exc_info=True)
            return set()

    def _add_missing_columns(self, table: str, schema: Any) -> None:
        """Add columns *schema* has that the stored table does not.

        Best-effort by design: a database that cannot be migrated should still
        open and serve reads.  What it must not do is pretend the columns are
        there, so the failure is logged at WARNING with the column names.
        """
        try:
            tbl = self._db.open_table(table)
            present = set(tbl.schema.names)
        except Exception:
            logger.debug("lancedb: cannot inspect %s for migration",
                         table, exc_info=True)
            return

        missing = [f.name for f in schema if f.name not in present]
        if not missing:
            return

        unknown = [name for name in missing if name not in _BACKFILL]
        if unknown or not hasattr(tbl, "add_columns"):
            # No safe default, or a LanceDB too old to alter a table.  Say so
            # rather than failing later on the first write.
            logger.warning(
                "lancedb: %s is missing %s and cannot be migrated here; "
                "rows will read as unattributed",
                table, ", ".join(missing),
            )
            return

        try:
            tbl.add_columns({name: _BACKFILL[name] for name in missing})
            logger.info("lancedb: %s migrated — added %s (existing rows "
                        "backfilled as unattributed)", table, ", ".join(missing))
        except Exception:
            logger.warning("lancedb: could not add %s to %s",
                           ", ".join(missing), table, exc_info=True)

    def insert(self, table: str, records: list[dict]) -> int:
        """Insert *records* into *table*.

        Returns:
            Number of rows inserted.
        """
        tbl = self._db.open_table(table)
        tbl.add(records)
        return len(records)

    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]:
        """Return up to *top_k* results ordered by cosine similarity descending.

        Args:
            table: Table name.
            embedding: Query vector (384-dim for all-MiniLM-L6-v2).
            top_k: Maximum number of results to return.

        Returns:
            List of dicts with table columns.
        """
        tbl = self._db.open_table(table)
        results = (
            tbl.search(embedding)
            .metric("cosine")
            .limit(top_k)
            .to_list()
        )
        return results

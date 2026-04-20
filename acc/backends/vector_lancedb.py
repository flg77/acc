"""LanceDB embedded vector backend.

Tables auto-created on first use with schemas defined in v0.1.0 Section 7.2.
All searches use cosine similarity (LanceDB default for normalized vectors).
"""

from __future__ import annotations

from typing import Any

import lancedb
import pyarrow as pa

# Standard ACC table schemas (v0.1.0 §7.2)
_SCHEMAS: dict[str, pa.Schema] = {
    "episodes": pa.schema([
        pa.field("id", pa.utf8()),
        pa.field("agent_id", pa.utf8()),
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
}

_STANDARD_TABLES = list(_SCHEMAS.keys())


class LanceDBBackend:
    """LanceDB embedded vector database backend.

    The database is stored at *path* on the local filesystem.  All four
    standard ACC tables are auto-created at construction time if absent.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._db = lancedb.connect(path)
        # Auto-create standard tables
        for table_name in _STANDARD_TABLES:
            self.create_table_if_absent(table_name, _SCHEMAS[table_name])

    def create_table_if_absent(self, table: str, schema: Any) -> None:
        """Create *table* with *schema* if it does not already exist."""
        # exist_ok=True is the idiomatic way in newer LanceDB; no pre-check needed.
        self._db.create_table(table, schema=schema, exist_ok=True)

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

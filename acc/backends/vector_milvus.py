"""Milvus vector backend (RHOAI / datacenter deployment)."""

from __future__ import annotations

from typing import Any

from pymilvus import MilvusClient


class MilvusBackend:
    """Milvus client backend for RHOAI datacenter deployments.

    All collection names are prefixed with *collection_prefix* to namespace
    ACC collections within a shared Milvus instance.
    """

    def __init__(self, uri: str, collection_prefix: str = "acc_") -> None:
        self._client = MilvusClient(uri=uri)
        self._prefix = collection_prefix

    def _col(self, table: str) -> str:
        """Return the prefixed collection name for *table*."""
        return f"{self._prefix}{table}"

    def create_table_if_absent(self, table: str, schema: Any) -> None:
        """Create collection if it does not exist.

        *schema* is expected to be a ``pymilvus.CollectionSchema`` or a dict
        that ``MilvusClient.create_collection`` accepts.
        """
        col = self._col(table)
        if not self._client.has_collection(col):
            self._client.create_collection(col, schema=schema)

    def insert(self, table: str, records: list[dict]) -> int:
        """Insert *records* into the Milvus collection for *table*.

        Returns:
            Number of rows inserted.
        """
        col = self._col(table)
        result = self._client.insert(col, records)
        return result.insert_count

    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]:
        """Search *table* for nearest neighbours of *embedding*.

        Uses cosine distance on the ``embedding`` field.

        Returns:
            List of dicts ordered by cosine similarity descending.
        """
        col = self._col(table)
        results = self._client.search(
            collection_name=col,
            data=[embedding],
            anns_field="embedding",
            search_params={"metric_type": "COSINE"},
            limit=top_k,
            output_fields=["*"],
        )
        # results is a list[list[Hit]]; flatten the first query's hits
        hits = results[0] if results else []
        return [{"id": hit["id"], **hit["entity"]} for hit in hits]

"""TurboVec embedded quantized vector backend (proposal 024).

A third :class:`acc.backends.VectorBackend` implementation beside LanceDB
(embedded, full-record) and Milvus (shared DC service).  TurboVec is an
embedded Rust ANN index built on Google Research's TurboQuant quantizer:
data-oblivious (no training pass — vectors are searchable the moment they
are added), 4-bit quantized (~16x smaller than fp32), SIMD-accelerated on
plain CPUs, and filterable inside the search kernel via id allowlists.

TurboVec is an *index*, not a database: it stores vectors + ids only.  Full
records live in a SQLite (WAL) side store, which is the source of truth —
the ``.tvim`` index files are derived data.  If an index file is missing,
corrupt, or out of sync with SQLite, it is rebuilt from the stored records
on startup (cheap: TurboQuant has no training phase).

Layout under *path*::

    records.db          SQLite: full records + ext-id<->int-id map + meta
    <table>.tvim        one IdMapIndex per table that has a vector column

Semantics vs LanceDB (documented divergences):

* ``insert`` upserts by record ``id`` (latest wins) instead of appending
  duplicates.
* All-zero embeddings are stored as records but NOT indexed (a zero vector
  cannot be normalized; the cognitive core writes zeroed placeholders when
  embedding fails, and those must not poison similarity results).
* Scores are inner-product on unit vectors == cosine.  Embeddings are
  (re)normalized at this seam on both insert and search.

Single-process only (per-agent data dir, same as LanceDB today).  Milvus
remains the answer for shared multi-writer stores.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sqlite3
import threading
import uuid
from typing import Any

import numpy as np
from turbovec import IdMapIndex

logger = logging.getLogger("acc.backends.vector_turbovec")

# all-MiniLM-L6-v2 — same dimensionality as the LanceDB schemas (v0.1.0 §7.2).
_DIM = 384

# Standard ACC tables -> their vector column (None = row store only, e.g.
# role_audit has no embedding).  Mirrors acc/backends/vector_lancedb._SCHEMAS.
_STANDARD_TABLES: dict[str, str | None] = {
    "episodes": "embedding",
    "patterns": "embedding",
    "collective_mem": "embedding",
    "icl_results": "embedding",
    "role_definitions": "purpose_embedding",
    "role_audit": None,
    "memory_notes": "embedding",
    # Proposal 024 P3 — governed RAG document store.
    "documents": None,          # row store only (no vector column)
    "doc_chunks": "embedding",
}

# Flush an index to disk after this many un-persisted index mutations.
# SQLite is always current, so a crash between flushes loses nothing —
# the index is rebuilt from SQLite on the next start.
_FLUSH_EVERY = 64


def _normalize(vec: list[float] | np.ndarray) -> np.ndarray | None:
    """Return *vec* as a unit-norm float32 array, or None for zero vectors."""
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0.0 or not np.isfinite(norm):
        return None
    return arr / norm


class TurboVecBackend:
    """Embedded quantized vector backend (TurboQuant via turbovec).

    Args:
        path: Directory for ``records.db`` + per-table ``.tvim`` files.
            Created if absent.
        bit_width: TurboQuant quantization width (2 or 4).  Default 4 —
            effectively lossless recall at k>=4 per upstream benchmarks.
    """

    def __init__(self, path: str, bit_width: int = 4) -> None:
        self._path = path
        self._bit_width = int(bit_width)
        self._lock = threading.RLock()
        self._indexes: dict[str, IdMapIndex] = {}
        self._dirty: dict[str, int] = {}

        try:
            os.makedirs(path, exist_ok=True)
            self._db = sqlite3.connect(
                os.path.join(path, "records.db"), check_same_thread=False
            )
        except (OSError, sqlite3.Error) as e:
            raise RuntimeError(
                f"Cannot open TurboVec store at {path!r} ({e}). "
                "A named Docker/Podman volume is often root-only: use an image "
                "that prepares the data dir (deploy/entrypoint-agent.sh), or "
                "set ACC_TURBOVEC_PATH to a writable directory."
            ) from e
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                tbl         TEXT NOT NULL,
                ext_id      TEXT NOT NULL,
                int_id      INTEGER NOT NULL,
                record_json TEXT NOT NULL,
                PRIMARY KEY (tbl, ext_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS records_by_int
                ON records (tbl, int_id);
            CREATE TABLE IF NOT EXISTS table_meta (
                tbl           TEXT PRIMARY KEY,
                embed_field   TEXT,
                dim           INTEGER,
                next_int_id   INTEGER NOT NULL DEFAULT 1,
                indexed_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._db.commit()

        # Standard tables exist from construction (same contract as LanceDB).
        for table, embed_field in _STANDARD_TABLES.items():
            self._register_table(table, embed_field, _DIM if embed_field else None)
        for table in self._tables_with_vectors():
            self._load_or_rebuild(table)

        atexit.register(self.flush)

    # ------------------------------------------------------------------
    # VectorBackend protocol
    # ------------------------------------------------------------------

    def create_table_if_absent(self, table: str, schema: Any) -> None:
        """Register *table*.  *schema* is opaque here (the LanceDB backend
        receives pyarrow schemas); the vector column is resolved from the
        standard-table map, duck-typed field names, or lazily from the
        first inserted record."""
        with self._lock:
            if self._table_meta(table) is not None:
                return
            embed_field: str | None
            if table in _STANDARD_TABLES:
                embed_field = _STANDARD_TABLES[table]
            else:
                embed_field = self._guess_embed_field(schema)
            self._register_table(
                table, embed_field, _DIM if embed_field else None
            )

    def insert(self, table: str, records: list[dict]) -> int:
        """Upsert *records* into *table* (latest record per ``id`` wins).

        Returns:
            Number of rows written.
        """
        if not records:
            return 0
        with self._lock:
            meta = self._table_meta(table)
            if meta is None:
                # Lazy registration for non-standard tables: convention is
                # an ``embedding`` column; dim from the first record.
                first = records[0]
                embed_field = "embedding" if "embedding" in first else None
                dim = len(first["embedding"]) if embed_field else None
                self._register_table(table, embed_field, dim)
                meta = self._table_meta(table)
            embed_field = meta[0]

            new_ids: list[int] = []
            new_vecs: list[np.ndarray] = []
            idx = self._index_for(table) if embed_field else None
            for record in records:
                ext_id = str(record.get("id") or uuid.uuid4())
                int_id = self._upsert_record(table, ext_id, record, idx)
                if idx is None or embed_field is None:
                    continue
                vec = record.get(embed_field)
                if vec is None:
                    continue
                unit = _normalize(vec)
                if unit is None:
                    continue  # zero/non-finite: stored, not searchable
                new_ids.append(int_id)
                new_vecs.append(unit)

            if idx is not None and new_vecs:
                idx.add_with_ids(
                    np.stack(new_vecs).astype(np.float32),
                    np.asarray(new_ids, dtype=np.uint64),
                )
                self._mark_dirty(table, len(new_vecs))
            self._db.commit()
            return len(records)

    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]:
        """Return up to *top_k* records ordered by cosine similarity descending."""
        return self.search_filtered(table, embedding, top_k, allow_ids=None)

    # ------------------------------------------------------------------
    # Extension — kernel-level filtered search (proposal 024 G3)
    # ------------------------------------------------------------------

    def search_filtered(
        self,
        table: str,
        embedding: list[float],
        top_k: int,
        allow_ids: list[str] | None = None,
    ) -> list[dict]:
        """Like :meth:`search`, but when *allow_ids* is given only those
        record ids are searchable — enforced inside the TurboVec SIMD
        kernel (no over-fetch + post-filter).  ``allow_ids=[]`` returns
        ``[]`` (an empty scope is a denial, not "no filter")."""
        with self._lock:
            meta = self._table_meta(table)
            if meta is None or meta[0] is None:
                return []
            idx = self._index_for(table)
            if idx is None or len(idx) == 0 or top_k <= 0:
                return []
            unit = _normalize(embedding)
            if unit is None:
                return []

            allowlist = None
            if allow_ids is not None:
                if not allow_ids:
                    return []
                placeholders = ",".join("?" * len(allow_ids))
                rows = self._db.execute(
                    f"SELECT int_id FROM records WHERE tbl = ? AND ext_id IN ({placeholders})",  # noqa: S608
                    [table, *[str(a) for a in allow_ids]],
                ).fetchall()
                if not rows:
                    return []
                allowlist = np.asarray([r[0] for r in rows], dtype=np.uint64)

            k = min(int(top_k), len(idx))
            _scores, ids = idx.search(
                unit.reshape(1, -1), k, allowlist=allowlist
            )
            out: list[dict] = []
            for int_id in ids[0].tolist():
                row = self._db.execute(
                    "SELECT record_json FROM records WHERE tbl = ? AND int_id = ?",
                    (table, int(int_id)),
                ).fetchone()
                if row is not None:
                    out.append(json.loads(row[0]))
            return out

    # ------------------------------------------------------------------
    # Extension — structured (non-vector) row reads
    # ------------------------------------------------------------------

    def get_records(
        self,
        table: str,
        *,
        field: str | None = None,
        value: Any = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return stored records (newest first), optionally filtered on a
        top-level JSON *field* == *value*.  Consumers that previously
        reached into LanceDB internals (``role_store``) duck-type this
        method so structured reads work on any backend that offers it."""
        with self._lock:
            sql = "SELECT record_json FROM records WHERE tbl = ?"
            params: list[Any] = [table]
            if field is not None:
                sql += " AND json_extract(record_json, '$.' || ?) = ?"
                params += [field, value]
            sql += " ORDER BY int_id DESC LIMIT ?"
            params.append(int(limit))
            return [json.loads(r[0]) for r in self._db.execute(sql, params)]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Persist every dirty index to its ``.tvim`` file.  Safe to call
        at any time; also registered via atexit.  SQLite needs no flush
        (WAL commits per insert batch)."""
        with self._lock:
            for table, dirty in list(self._dirty.items()):
                if dirty <= 0:
                    continue
                self._write_index(table)

    def close(self) -> None:
        """Flush indexes and close the SQLite store."""
        with self._lock:
            self.flush()
            self._db.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tables_with_vectors(self) -> list[str]:
        rows = self._db.execute(
            "SELECT tbl FROM table_meta WHERE embed_field IS NOT NULL"
        ).fetchall()
        return [r[0] for r in rows]

    def _table_meta(self, table: str) -> tuple[str | None, int | None] | None:
        row = self._db.execute(
            "SELECT embed_field, dim FROM table_meta WHERE tbl = ?", (table,)
        ).fetchone()
        return (row[0], row[1]) if row is not None else None

    def _register_table(
        self, table: str, embed_field: str | None, dim: int | None
    ) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO table_meta (tbl, embed_field, dim) VALUES (?, ?, ?)",
            (table, embed_field, dim),
        )
        self._db.commit()

    @staticmethod
    def _guess_embed_field(schema: Any) -> str | None:
        """Duck-type a vector column from a foreign schema object (e.g. a
        pyarrow schema exposes ``.names``) without importing pyarrow."""
        names = getattr(schema, "names", None)
        if isinstance(names, (list, tuple)):
            for candidate in ("embedding", "purpose_embedding"):
                if candidate in names:
                    return candidate
            return None
        return "embedding"

    def _index_for(self, table: str) -> IdMapIndex | None:
        idx = self._indexes.get(table)
        if idx is not None:
            return idx
        meta = self._table_meta(table)
        if meta is None or meta[0] is None:
            return None
        dim = meta[1] or _DIM
        idx = IdMapIndex(dim=dim, bit_width=self._bit_width)
        self._indexes[table] = idx
        return idx

    def _index_path(self, table: str) -> str:
        return os.path.join(self._path, f"{table}.tvim")

    def _upsert_record(
        self, table: str, ext_id: str, record: dict, idx: IdMapIndex | None
    ) -> int:
        """Write the record row; return its (stable) int id.  Existing
        rows keep their int id and are dropped from the index first so
        the subsequent add is a clean replace."""
        row = self._db.execute(
            "SELECT int_id FROM records WHERE tbl = ? AND ext_id = ?",
            (table, ext_id),
        ).fetchone()
        if row is not None:
            int_id = int(row[0])
            if idx is not None:
                idx.remove(int_id)  # False when it was never indexed — fine
        else:
            cur = self._db.execute(
                "UPDATE table_meta SET next_int_id = next_int_id + 1 "
                "WHERE tbl = ? RETURNING next_int_id - 1",
                (table,),
            ).fetchone()
            int_id = int(cur[0])
        self._db.execute(
            "INSERT OR REPLACE INTO records (tbl, ext_id, int_id, record_json) "
            "VALUES (?, ?, ?, ?)",
            (table, ext_id, int_id, json.dumps(record)),
        )
        return int_id

    def _mark_dirty(self, table: str, n: int) -> None:
        self._dirty[table] = self._dirty.get(table, 0) + n
        if self._dirty[table] >= _FLUSH_EVERY:
            self._write_index(table)

    def _write_index(self, table: str) -> None:
        idx = self._indexes.get(table)
        if idx is None:
            return
        try:
            idx.write(self._index_path(table))
            self._db.execute(
                "UPDATE table_meta SET indexed_count = ? WHERE tbl = ?",
                (len(idx), table),
            )
            self._db.commit()
            self._dirty[table] = 0
        except (OSError, RuntimeError, ValueError) as e:  # pragma: no cover
            # Non-fatal: SQLite remains the source of truth; the index is
            # rebuilt on next start if this file never lands.
            logger.warning("turbovec: flush of %s failed: %s", table, e)

    def _load_or_rebuild(self, table: str) -> None:
        """Load ``<table>.tvim`` if present and consistent with SQLite;
        otherwise rebuild the index from the stored records.  The index
        is derived data — any mismatch resolves in favour of SQLite."""
        path = self._index_path(table)
        expected = self._db.execute(
            "SELECT indexed_count FROM table_meta WHERE tbl = ?", (table,)
        ).fetchone()
        expected_count = int(expected[0]) if expected else 0
        if os.path.exists(path):
            try:
                idx = IdMapIndex.load(path)
                if len(idx) == expected_count and expected_count > 0:
                    self._indexes[table] = idx
                    self._dirty[table] = 0
                    return
                logger.info(
                    "turbovec: %s count mismatch (file=%d, expected=%d) — rebuilding",
                    table, len(idx), expected_count,
                )
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("turbovec: cannot load %s (%s) — rebuilding", path, e)
        elif expected_count == 0:
            return  # nothing persisted, nothing expected — lazy-create on insert
        self._rebuild_index(table)

    def _rebuild_index(self, table: str) -> None:
        meta = self._table_meta(table)
        if meta is None or meta[0] is None:
            return
        embed_field = meta[0]
        dim = meta[1] or _DIM
        idx = IdMapIndex(dim=dim, bit_width=self._bit_width)
        ids: list[int] = []
        vecs: list[np.ndarray] = []
        for int_id, record_json in self._db.execute(
            "SELECT int_id, record_json FROM records WHERE tbl = ?", (table,)
        ):
            try:
                vec = json.loads(record_json).get(embed_field)
            except json.JSONDecodeError:  # pragma: no cover (defensive)
                continue
            if vec is None:
                continue
            unit = _normalize(vec)
            if unit is None:
                continue
            ids.append(int(int_id))
            vecs.append(unit)
        if vecs:
            idx.add_with_ids(
                np.stack(vecs).astype(np.float32),
                np.asarray(ids, dtype=np.uint64),
            )
        self._indexes[table] = idx
        self._write_index(table)
        logger.info("turbovec: rebuilt %s (%d vectors)", table, len(idx))

"""Where a collective's domain taxonomy comes from (RDF-02).

Operator decision 2026-10-03: **the hub owns the vocabulary** — one signed
taxonomy pack per hub, used by every instance bound to it, so a lesson tagged
``mortgage`` means the same thing in every instance.  One rule:

* a collective **bound to a hub** (``hub_collective_id``) uses the hub's
  taxonomy and nothing else — not a pack it installed itself — read from
  ``acc:{hub}:domain_taxonomy``, the one cross-instance path that already
  exists (reads go through the hub only, HG-40.1 §5.1);
* an **unbound** collective (a hub, or a standalone one) uses the pack named
  by ``ACC_DOMAIN_TAXONOMY`` from its own installed set, and publishes it
  under its own key so instances bound to it can read it;
* **no taxonomy** → ``None``: receptors keep exact string matching, the
  behaviour before RDF-02.

The pack carries ``taxonomy/domains.yaml`` (see :mod:`acc.lib.rdf.taxonomy`).
Packs are signed and verified at install, so the file is as trusted as any
installed capability; the Redis copy names the pack, version and content
hash it came from.  Best-effort throughout: a missing or malformed taxonomy
is logged and treated as none -- it can never stop an agent from booting.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import yaml

from acc.lib.rdf.taxonomy import Taxonomy, TaxonomyError

logger = logging.getLogger("acc.domain_taxonomy")

#: The env var naming the taxonomy pack (``@scope/name``) an unbound collective uses.
ENV = "ACC_DOMAIN_TAXONOMY"
#: Where the file sits inside a pack.
PACK_DIR, PACK_FILE = "taxonomy", "domains.yaml"
#: How long a bound instance trusts its copy of the hub's taxonomy.
REFRESH_S = 600.0


def redis_taxonomy_key(collective_id: str) -> str:
    return f"acc:{collective_id}:domain_taxonomy"


@dataclass
class Resolved:
    taxonomy: Taxonomy | None
    #: ``hub:<cid>``, ``pack:<name>@<version>``, or why there is none.
    origin: str


def load_pack(name: str, registry: Any = None) -> tuple[Taxonomy, dict[str, str]]:
    """The taxonomy in installed pack *name* (its newest installed version)."""
    from pathlib import Path  # noqa: PLC0415

    from acc.pkg.registry import Registry  # noqa: PLC0415

    entries = [e for e in (registry or Registry()).list() if e.name == name]
    if not entries:
        raise TaxonomyError(f"taxonomy pack {name!r} is not installed")
    entry = sorted(entries, key=lambda e: e.installed_at)[-1]
    path = Path(entry.install_path) / PACK_DIR / PACK_FILE
    if not path.is_file():
        raise TaxonomyError(f"pack {name}@{entry.version} has no {PACK_DIR}/{PACK_FILE}")
    tax = Taxonomy.from_mapping(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return tax, {"pack": name, "version": entry.version, "content_sha256": entry.content_sha256}


def publish(redis_client: Any, collective_id: str, taxonomy: Taxonomy, source: dict[str, str]) -> bool:
    """Make *taxonomy* readable by the instances bound to *collective_id*."""
    if redis_client is None or not collective_id:
        return False
    try:
        redis_client.set(redis_taxonomy_key(collective_id), json.dumps(
            {"taxonomy": taxonomy.to_mapping(), "source": source, "ts": time.time()}))
        return True
    except Exception:  # noqa: BLE001
        logger.warning("domain taxonomy: publish to %s failed", collective_id, exc_info=True)
        return False


def read_published(redis_client: Any, collective_id: str) -> tuple[Taxonomy, dict[str, str]] | None:
    """The taxonomy *collective_id* published, or ``None``."""
    if redis_client is None:
        return None
    raw = redis_client.get(redis_taxonomy_key(collective_id))
    if not raw:
        return None
    doc = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    return Taxonomy.from_mapping(doc.get("taxonomy") or {}), dict(doc.get("source") or {})


def resolve(
    *, collective_id: str, hub_collective_id: str = "", redis_client: Any = None,
    environ: dict[str, str] | None = None, registry: Any = None, publish_pack: bool = True,
) -> Resolved:
    """Apply the one rule.  Never raises.  *publish_pack* False reads a pack
    without publishing it (a read-only caller, e.g. ``acc-cli kg sync``)."""
    env = os.environ if environ is None else environ
    try:
        if hub_collective_id:
            got = read_published(redis_client, hub_collective_id)
            if got is None:
                return Resolved(None, f"hub {hub_collective_id} has published no taxonomy")
            tax, source = got
            return Resolved(tax, f"hub:{hub_collective_id} ({source.get('pack', '?')}@"
                                 f"{source.get('version', '?')})")
        name = str(env.get(ENV, "") or "").strip()
        if not name:
            return Resolved(None, f"{ENV} not set")
        tax, source = load_pack(name, registry)
        if publish_pack:
            publish(redis_client, collective_id, tax, source)
        return Resolved(tax, f"pack:{name}@{source['version']}")
    except Exception as exc:  # noqa: BLE001 -- a bad taxonomy must never stop a boot
        logger.warning("domain taxonomy: none in use (%s)", exc)
        return Resolved(None, f"error: {exc}")


class TaxonomyHolder:
    """The taxonomy an agent matches receptors with, re-read from the hub
    every :data:`REFRESH_S` so a hub's new pack reaches bound instances
    without a restart.  An unbound collective's pack is read once."""

    def __init__(self, resolver: Callable[[], Resolved], *, bound: bool,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._resolver, self._bound, self._clock = resolver, bound, clock
        self._resolved: Resolved | None = None
        self._at = 0.0

    def get(self) -> Taxonomy | None:
        now = self._clock()
        if self._resolved is None or (self._bound and now - self._at >= REFRESH_S):
            self._resolved, self._at = self._resolver(), now
            logger.info("domain taxonomy: %s", self._resolved.origin)
        return self._resolved.taxonomy

    @property
    def origin(self) -> str:
        return self._resolved.origin if self._resolved else "not resolved"

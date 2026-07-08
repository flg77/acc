"""``acc.okf`` — a pure-Python Open Knowledge Format (OKF v0.1) toolkit.

OKF (Google Cloud, published 2026-06-12) models a knowledge base as a *bundle*:
a directory of markdown *concepts*, each a YAML front-matter block + a markdown
body, whose only hard requirement is a non-empty ``type``.  This package is the
foundation layer of ACC's OKF integration (proposal "Open Knowledge Format
(OKF) in ACC"): it parses, validates, emits, and converts bundles with **no I/O
beyond the filesystem and no ACC runtime dependencies**, so the ``okf`` skill,
the memory indexer, and the ``okf-transformer`` role can all build on it.

Typical use::

    from acc.okf import load_bundle, validate, from_obsidian

    bundle = load_bundle("path/to/bundle")
    report = validate(bundle)
    print(report.summary())

    from_obsidian("path/to/vault", "path/to/new-bundle", now="2026-07-08T00:00:00Z")
"""

from __future__ import annotations

from acc.okf.emit import (
    concept_to_markdown,
    dump_frontmatter,
    generate_index,
    write_bundle,
    write_concept,
)
from acc.okf.models import (
    RESERVED_FILENAMES,
    Bundle,
    Concept,
    ConformanceIssue,
    ConformanceReport,
)
from acc.okf.memory import concept_tags, index_bundle, index_bundle_path
from acc.okf.parse import load_bundle, load_concept, split_frontmatter
from acc.okf.transform import from_obsidian
from acc.okf.types import ACC_TYPES, DEFAULT_TYPE, infer_type
from acc.okf.validate import validate

__all__ = [
    # models
    "Bundle", "Concept", "ConformanceIssue", "ConformanceReport",
    "RESERVED_FILENAMES",
    # parse
    "load_bundle", "load_concept", "split_frontmatter",
    # validate
    "validate",
    # emit
    "concept_to_markdown", "dump_frontmatter", "generate_index",
    "write_bundle", "write_concept",
    # transform
    "from_obsidian",
    # memory (P2 — index into the collective document store)
    "concept_tags", "index_bundle", "index_bundle_path",
    # types
    "ACC_TYPES", "DEFAULT_TYPE", "infer_type",
]

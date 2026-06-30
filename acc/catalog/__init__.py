"""acc-catalog — the writable, signature-verifying package-catalog endpoint.

Thread 12 (marketplace P0).  The read-only side of catalogs already exists
(``acc/pkg/catalog.py`` resolves an ``index.json`` + ``acc/marketplace.py``
renders rows).  This package adds the **server** that the existing
``acc-pkg publish`` client (``acc/pkg/publish.py``) uploads to:

* :mod:`acc.catalog.store` — framework-agnostic verify → promote → index logic
  (reuses ``acc.pkg.verify``/``ec_policy``/``install.read_manifest``).  Pure
  Python; unit-testable without fastapi.
* :mod:`acc.catalog.server` — a thin FastAPI shell over the store
  (``acc-catalog`` console script).

Invariant (marketplace draft §6.2): **signed or it doesn't list** — an upload
is only promoted into the served tree (and the ``index.json``) after its cosign
signature verifies against the catalog's ``RequiredSigner``.
"""

from acc.catalog.store import (
    CatalogStore,
    CatalogStoreError,
    PublishedPackage,
    RejectedUpload,
)

__all__ = [
    "CatalogStore",
    "CatalogStoreError",
    "PublishedPackage",
    "RejectedUpload",
]

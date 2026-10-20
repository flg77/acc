"""The Kubernetes API group ACC's custom resources live in.

One constant, read by every runtime module that talks to the operator's
CRDs or to the labels and annotations the operator stamps on pods — so the
group is named in exactly one place on the Python side (OR-04, D-032). The
Go side names it in ``api/v1alpha1/groupversion_info.go`` of the operator
repository; the two must agree, release for release.

History: the group was ``acc.redhat.io`` until operator 0.3.0 / runtime
0.28.0. It is not a Red Hat API and never was; the rename says so.
"""

from __future__ import annotations

GROUP = "acc.ic3networks.com"
VERSION = "v1alpha1"
API_VERSION = f"{GROUP}/{VERSION}"
API_PATH = f"/apis/{API_VERSION}"


def label(name: str) -> str:
    """The fully qualified label key ``<group>/<name>``."""
    return f"{GROUP}/{name}"


annotation = label

"""Images attached to a prompt, and what the durable record keeps of them.

This is not trivial for one reason: **an image is large, may contain sensitive
material, and the durable record is retained.** Storing full images inflates
the record indefinitely; storing only a hash makes it unverifiable — you can
prove nothing about an image you no longer hold.

The answer taken here is a **content-addressed side store with a reference in
the record**:

* the durable record holds the digest, media type, dimensions and size — small,
  and enough to say what was attached;
* the bytes live beside the tracelog under that digest, so the record *is*
  verifiable while the store is retained;
* the store is governed by the same retention policy as sessions, so an
  operator sets one policy rather than discovering later that images outlived
  the records that referenced them.

That gives verifiability without unbounded growth, and — importantly — an image
can be removed on its retention schedule while the record of *what was
attached* survives, which is the shape an audit trail needs.

A backend that cannot accept images **fails with a clear message**. Silently
dropping an attachment would produce a confident answer to a question the model
never saw, which is worse than an error.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("acc.attachments")

STORE_VAR = "ACC_ATTACHMENT_DIR"
DEFAULT_STORE = "attachments"

#: Per-image ceiling, enforced BEFORE dispatch. Providers reject oversize
#: images with unhelpful errors, and a local limit fails fast and legibly.
MAX_BYTES = 5 * 1024 * 1024

#: What ACC will pass to a backend. Deliberately narrow: an "image" the
#: provider cannot decode is a failed call with a confusing message.
SUPPORTED = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}

#: Backends that accept image content blocks today.
MULTIMODAL_BACKENDS = frozenset({"anthropic", "openai_compat"})


class AttachmentError(Exception):
    """An attachment was refused. The message is operator-facing."""


@dataclass
class Attachment:
    """One image, by reference."""

    sha256: str
    media_type: str
    size: int
    width: int = 0
    height: int = 0
    filename: str = ""

    def as_record(self) -> dict[str, Any]:
        """What goes into the durable record — a reference, not the bytes."""
        return {
            "kind": "image",
            "sha256": self.sha256,
            "media_type": self.media_type,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "filename": self.filename,
        }


# ---------------------------------------------------------------------------
# Sniffing
# ---------------------------------------------------------------------------


def detect_media_type(payload: bytes) -> str:
    """The media type, from the bytes themselves.

    A caller-supplied content type is a claim; the magic bytes are evidence.
    Trusting the claim would let a file named ``.png`` reach a provider as
    something it cannot decode.
    """
    for media_type, signatures in SUPPORTED.items():
        for signature in signatures:
            if payload.startswith(signature):
                if media_type == "image/webp" and payload[8:12] != b"WEBP":
                    continue
                return media_type
    return ""


def _dimensions(payload: bytes, media_type: str) -> tuple[int, int]:
    """Best-effort width/height. Zero when it cannot be read cheaply."""
    try:
        if media_type == "image/png" and len(payload) >= 24:
            width, height = struct.unpack(">II", payload[16:24])
            return int(width), int(height)
        if media_type == "image/gif" and len(payload) >= 10:
            width, height = struct.unpack("<HH", payload[6:10])
            return int(width), int(height)
        if media_type == "image/jpeg":
            i = 2
            while i + 9 < len(payload):
                if payload[i] != 0xFF:
                    i += 1
                    continue
                marker = payload[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                    height, width = struct.unpack(">HH", payload[i + 5 : i + 9])
                    return int(width), int(height)
                length = struct.unpack(">H", payload[i + 2 : i + 4])[0]
                i += 2 + length
    except (struct.error, IndexError):  # pragma: no cover — truncated file
        pass
    return 0, 0


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def store_dir(root: Path | None = None) -> Path:
    raw = os.environ.get(STORE_VAR, "").strip()
    if raw:
        return Path(raw)
    if root is not None:
        return Path(root) / DEFAULT_STORE
    from acc import tracelog  # noqa: PLC0415

    return tracelog.tracelog_dir() / DEFAULT_STORE


def accept(
    payload: bytes,
    *,
    filename: str = "",
    root: Path | None = None,
    max_bytes: int = MAX_BYTES,
) -> Attachment:
    """Validate and store an image. Returns the reference.

    Raises:
        AttachmentError: too large, or not an image ACC will pass on. Both are
            checked BEFORE dispatch so the failure is local and legible rather
            than a provider error three layers away.
    """
    if not payload:
        raise AttachmentError("empty attachment")
    if len(payload) > max_bytes:
        raise AttachmentError(
            f"attachment is {len(payload)} bytes, over the {max_bytes} limit"
        )

    media_type = detect_media_type(payload)
    if not media_type:
        raise AttachmentError(
            "not a supported image (png, jpeg, gif, webp). The file's own bytes "
            "are checked, not its name or declared type."
        )

    digest = hashlib.sha256(payload).hexdigest()
    width, height = _dimensions(payload, media_type)

    target = store_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    blob = target / digest
    if not blob.exists():
        blob.write_bytes(payload)

    return Attachment(
        sha256=digest,
        media_type=media_type,
        size=len(payload),
        width=width,
        height=height,
        filename=filename,
    )


def load_bytes(sha256: str, *, root: Path | None = None) -> bytes:
    """The stored image.

    Raises:
        AttachmentError: the image is no longer held — which is a legitimate
            outcome once retention has run, and must be distinguishable from
            "there was never an image".
    """
    blob = store_dir(root) / sha256
    if not blob.is_file():
        raise AttachmentError(
            f"attachment {sha256[:12]}... is no longer stored. The record of what "
            f"was attached survives; the bytes were removed under the retention "
            f"policy."
        )
    return blob.read_bytes()


def prune(keep_digests: Iterable[str], *, root: Path | None = None) -> list[str]:
    """Drop stored images not in *keep_digests*.

    Driven by the same retention decision as sessions, so an operator sets one
    policy rather than discovering that images outlived the records naming
    them.
    """
    keep = set(keep_digests)
    target = store_dir(root)
    if not target.is_dir():
        return []
    removed: list[str] = []
    for blob in target.iterdir():
        if blob.is_file() and blob.name not in keep:
            try:
                blob.unlink()
                removed.append(blob.name)
            except OSError:  # pragma: no cover
                continue
    if removed:
        logger.info("attachments: pruned %d image(s)", len(removed))
    return removed


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def backend_accepts_images(backend: str) -> bool:
    return str(backend or "").strip() in MULTIMODAL_BACKENDS


def content_blocks(
    attachments: Iterable[Attachment],
    *,
    backend: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Provider content blocks for the attachments.

    Raises:
        AttachmentError: the backend cannot accept images. Silently dropping
            them would produce a confident answer to a question the model
            never saw — worse than an error, because nothing looks wrong.
    """
    items = list(attachments)
    if not items:
        return []
    if not backend_accepts_images(backend):
        raise AttachmentError(
            f"the {backend!r} backend cannot accept images. Bind this role to a "
            f"multimodal model, or send the prompt without the attachment — it "
            f"will not be silently dropped."
        )

    blocks: list[dict[str, Any]] = []
    for attachment in items:
        payload = load_bytes(attachment.sha256, root=root)
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": attachment.media_type,
                    "data": base64.b64encode(payload).decode("ascii"),
                },
            }
        )
    return blocks


def records_for(attachments: Iterable[Attachment]) -> list[dict[str, Any]]:
    """What the durable record keeps: references, never the bytes."""
    return [a.as_record() for a in attachments]

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

#: Backends that CAN carry image blocks. Whether an image actually reaches a
#: model that reads it is the model's declaration (F2b, ``accepts_images``):
#: see :func:`accepts_images`.
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


def accepts_images(backend: str, declared: bool | None = None) -> bool:
    """Whether an image sent to this backend and model will be read (F2b).

    Mirrors what the backends enforce: a declaration wins; undeclared,
    ``anthropic`` takes images and ``openai_compat`` does not (it cannot know
    what model sits behind the gateway); text-only backends never do.
    """
    name = str(backend or "").strip()
    if name not in MULTIMODAL_BACKENDS:
        return False
    if declared is not None:
        return bool(declared)
    return name == "anthropic"


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
    return image_blocks(items, root=root)


def image_blocks(
    attachments: Iterable[Attachment], *, root: Path | None = None
) -> list[dict[str, Any]]:
    """The provider blocks, with no capability check of their own.

    For the dispatch path, where the check belongs to the backend itself: a
    text-only backend raises ``ContentNotSupported`` when handed blocks
    (``acc.backends.refuse_content``). That holds across a failover chain,
    where the name of "the" backend is not one thing.
    """
    blocks: list[dict[str, Any]] = []
    for attachment in attachments:
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


def is_reference(value: Any) -> bool:
    """A sha256 hex digest -- the only form a reference takes on the wire."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def resolve(references: Iterable[Any], *, root: Path | None = None) -> list[Attachment]:
    """The attachments a task names, re-read from the store.

    The bytes are the authority, not the message: the digest is recomputed
    and the media type sniffed again, so a reference can only ever name the
    image that was accepted under it.

    Raises:
        AttachmentError: not a reference, no longer stored (retention ran --
            :func:`load_bytes` says so distinguishably), or bytes that no
            longer match their name.
    """
    out: list[Attachment] = []
    for ref in references:
        if not is_reference(ref):
            raise AttachmentError(
                f"{str(ref)[:16]!r} is not an attachment reference (a sha256 digest)"
            )
        if not (store_dir(root) / ref).is_file() and not _removed_by_retention(ref):
            # Never stored where this process looks. The usual cause is a
            # surface and an agent that do not share the store (separate pods,
            # or a web GUI without the /logs mount) -- not retention, and the
            # message must not blame it.
            raise AttachmentError(
                f"attachment {ref[:12]}... is not in the store this agent reads "
                f"({store_dir(root)}). The surface that accepted it and the "
                f"agents must share that store ({STORE_VAR})."
            )
        payload = load_bytes(ref, root=root)
        if hashlib.sha256(payload).hexdigest() != ref:
            raise AttachmentError(
                f"attachment {ref[:12]}... does not match its digest; not sent"
            )
        media_type = detect_media_type(payload)
        if not media_type:
            raise AttachmentError(f"attachment {ref[:12]}... is not a supported image")
        width, height = _dimensions(payload, media_type)
        out.append(Attachment(
            sha256=ref, media_type=media_type, size=len(payload),
            width=width, height=height,
        ))
    return out


def _removed_by_retention(ref: str) -> bool:
    try:
        from acc import sessions  # noqa: PLC0415

        return any(
            e.get("kind") == "attachment_removed" and e.get("sha256") == ref
            for e in sessions.removals()
        )
    except Exception:  # noqa: BLE001 -- only chooses between two messages
        return False


def store_summary(root: Path | None = None) -> dict[str, Any]:
    """Count, bytes and oldest age of the stored images, for ``doctor``."""
    target = store_dir(root)
    blobs = [b for b in target.iterdir() if b.is_file()] if target.is_dir() else []
    stats = [b.stat() for b in blobs]
    return {
        "path": str(target),
        "count": len(stats),
        "bytes": sum(s.st_size for s in stats),
        "oldest_mtime": min((s.st_mtime for s in stats), default=0.0),
    }


def records_for(attachments: Iterable[Attachment]) -> list[dict[str, Any]]:
    """What the durable record keeps: references, never the bytes."""
    return [a.as_record() for a in attachments]

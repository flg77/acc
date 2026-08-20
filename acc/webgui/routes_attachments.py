"""Image attachments for the web interface.

A terminal cannot display an image meaningfully, so the browser is the surface
where attaching one and reviewing it both work.

What reaches the durable record is a **reference** — digest, media type, size,
dimensions — never the bytes. The image itself lives in a content-addressed
store governed by the retention policy, so the record stays small and
verifiable while the store is retained, and an image can age out while the
record of what was attached survives.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from acc import attachments
from acc.webgui.auth import Principal, require_operator, require_viewer

logger = logging.getLogger("acc.webgui.attachments")

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.post("")
async def upload(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_operator),
) -> dict:
    """Accept an image and return the reference a prompt should carry.

    Validation happens here rather than at dispatch: a local refusal is legible,
    a provider's rejection three layers away is not.
    """
    payload = await file.read()
    try:
        attachment = attachments.accept(payload, filename=file.filename or "")
    except attachments.AttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "webgui: %s attached %s (%s, %d bytes)",
        principal.user, attachment.sha256[:12], attachment.media_type, attachment.size,
    )
    return {
        **attachment.as_record(),
        "attached_by": principal.user,
        "note": (
            "The durable record keeps this reference, not the image. The bytes "
            "are retained separately under the retention policy."
        ),
    }


@router.get("/capability")
def capability(principal: Principal = Depends(require_viewer)) -> dict:
    """Whether the configured backend can accept images at all.

    Exposed so the interface can say so before someone attaches one, rather
    than after the request fails.
    """
    from acc import configstore as store

    backend = str(store.get("llm.backend").value or "")
    return {
        "backend": backend,
        "accepts_images": attachments.backend_accepts_images(backend),
        "max_bytes": attachments.MAX_BYTES,
        "supported": sorted(attachments.SUPPORTED),
        "note": (
            "A backend that cannot accept images refuses the prompt rather than "
            "dropping the attachment silently."
        ),
    }

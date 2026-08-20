"""Image attachments, and what the durable record keeps of them.

The hard question is not the plumbing, it is retention: an image is large, may
contain sensitive material, and the record is kept. Full images inflate the
record forever; a bare hash makes it unverifiable, because you can prove
nothing about an image you no longer hold.

The answer under test is a content-addressed side store with a reference in the
record — so the record stays small and verifiable while the store is retained,
and an image can be removed on its own schedule while the record of **what was
attached** survives. That last property is what an audit trail actually needs,
and there is a test for it.

The other property is a refusal: a backend that cannot accept images must fail
loudly. Dropping the attachment silently produces a confident answer to a
question the model never saw, which is worse than an error because nothing
looks wrong.
"""

from __future__ import annotations

import base64
import hashlib
import struct

import pytest

from acc import attachments as A

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\rIHDR"
    + struct.pack(">II", 120, 80)
    + b"\x08\x06\x00\x00\x00"
    + b"\x00" * 32
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + struct.pack("<HH", 64, 48) + b"\x00" * 32
NOT_AN_IMAGE = b"just some text, definitely not a picture"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(A.STORE_VAR, str(tmp_path / "attachments"))
    return tmp_path


# --------------------------------------------------------------------------
# The record holds a reference, not the bytes
# --------------------------------------------------------------------------


class TestDurableRecord:
    def test_the_record_is_a_reference(self, store):
        attachment = A.accept(PNG, filename="diagram.png")
        record = attachment.as_record()

        assert record["sha256"] == hashlib.sha256(PNG).hexdigest()
        assert record["media_type"] == "image/png"
        assert record["size"] == len(PNG)
        assert "data" not in record, "the record must not carry the image itself"
        assert base64.b64encode(PNG).decode() not in repr(record)

    def test_the_record_stays_small(self, store):
        """An inflated record is one an operator stops retaining."""
        import json

        big = PNG + b"\x00" * 100_000
        attachment = A.accept(big)
        assert len(json.dumps(attachment.as_record())) < 400

    def test_the_record_survives_the_image_being_pruned(self, store):
        """The shape an audit trail needs: what was attached outlives the bytes."""
        attachment = A.accept(PNG)
        record = attachment.as_record()

        A.prune(keep_digests=[])
        assert record["sha256"], "the record is unaffected"
        with pytest.raises(A.AttachmentError, match="no longer stored"):
            A.load_bytes(attachment.sha256)

    def test_a_pruned_image_is_distinguishable_from_one_that_never_existed(self, store):
        attachment = A.accept(PNG)
        A.prune(keep_digests=[])
        with pytest.raises(A.AttachmentError) as exc:
            A.load_bytes(attachment.sha256)
        assert "retention policy" in str(exc.value)

    def test_the_record_is_verifiable_while_the_image_is_held(self, store):
        attachment = A.accept(PNG)
        stored = A.load_bytes(attachment.sha256)
        assert hashlib.sha256(stored).hexdigest() == attachment.sha256

    def test_identical_images_are_stored_once(self, store):
        first = A.accept(PNG)
        second = A.accept(PNG)
        assert first.sha256 == second.sha256
        assert len(list(A.store_dir().iterdir())) == 1

    def test_prune_keeps_what_is_still_referenced(self, store):
        keep = A.accept(PNG)
        drop = A.accept(GIF)
        removed = A.prune(keep_digests=[keep.sha256])
        assert removed == [drop.sha256]
        assert A.load_bytes(keep.sha256)


# --------------------------------------------------------------------------
# Validation happens before dispatch
# --------------------------------------------------------------------------


class TestValidation:
    def test_the_bytes_decide_the_type_not_the_name(self, store):
        """A declared type is a claim; magic bytes are evidence."""
        with pytest.raises(A.AttachmentError, match="not a supported image"):
            A.accept(NOT_AN_IMAGE, filename="innocent.png")

    @pytest.mark.parametrize(
        "payload,expected",
        [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif")],
    )
    def test_supported_types_are_detected(self, payload, expected):
        assert A.detect_media_type(payload) == expected

    def test_a_riff_file_that_is_not_webp_is_rejected(self):
        assert A.detect_media_type(b"RIFF" + b"\x00" * 4 + b"AVI ") == ""

    def test_oversize_is_refused_locally(self, store):
        """A local limit fails fast; a provider's rejection is three layers away."""
        with pytest.raises(A.AttachmentError, match="over the"):
            A.accept(PNG + b"\x00" * 1000, max_bytes=100)

    def test_an_empty_attachment_is_refused(self, store):
        with pytest.raises(A.AttachmentError, match="empty"):
            A.accept(b"")

    def test_dimensions_are_read_where_cheap(self, store):
        attachment = A.accept(PNG)
        assert (attachment.width, attachment.height) == (120, 80)

    def test_gif_dimensions(self, store):
        attachment = A.accept(GIF)
        assert (attachment.width, attachment.height) == (64, 48)

    def test_unreadable_dimensions_are_zero_not_an_error(self, store):
        attachment = A.accept(JPEG)
        assert attachment.width == 0


# --------------------------------------------------------------------------
# A backend that cannot take images must say so
# --------------------------------------------------------------------------


class TestBackendCapability:
    def test_a_text_only_backend_refuses_loudly(self, store):
        """Silently dropping produces a confident answer to an unseen question."""
        attachment = A.accept(PNG)
        with pytest.raises(A.AttachmentError, match="cannot accept images"):
            A.content_blocks([attachment], backend="ollama")

    def test_the_refusal_says_what_to_do(self, store):
        attachment = A.accept(PNG)
        with pytest.raises(A.AttachmentError) as exc:
            A.content_blocks([attachment], backend="vllm")
        assert "multimodal model" in str(exc.value)
        assert "not be silently dropped" in str(exc.value)

    @pytest.mark.parametrize("backend", ["anthropic", "openai_compat"])
    def test_a_multimodal_backend_gets_content_blocks(self, store, backend):
        attachment = A.accept(PNG)
        blocks = A.content_blocks([attachment], backend=backend)
        assert blocks[0]["type"] == "image"
        assert blocks[0]["source"]["media_type"] == "image/png"
        assert base64.b64decode(blocks[0]["source"]["data"]) == PNG

    def test_no_attachments_needs_no_multimodal_backend(self, store):
        """A text-only role must not break because the feature exists."""
        assert A.content_blocks([], backend="ollama") == []

    def test_capability_is_queryable(self):
        assert A.backend_accepts_images("anthropic")
        assert not A.backend_accepts_images("ollama")
        assert not A.backend_accepts_images("")

    def test_dispatch_fails_when_the_image_is_gone(self, store):
        attachment = A.accept(PNG)
        A.prune(keep_digests=[])
        with pytest.raises(A.AttachmentError, match="no longer stored"):
            A.content_blocks([attachment], backend="anthropic")

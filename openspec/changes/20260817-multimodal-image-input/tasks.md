# Tasks: Image input for prompts and evidence

**Change ID:** 20260817-multimodal-image-input
**Branch:** `feat/multimodal-image-input`

---

## Phase 1 — Transport

- [x] `[1]` Attachment on the web interface with size limits
- [x] `[2]` Multimodal content-block formation for backends that support it
- [x] `[3]` Explicit failure for backends that do not

## Phase 2 — Record

- [x] `[4]` Decide what the durable record holds (open question 1)
- [x] `[5]` Implement that decision; document it in the retention policy

## Phase 3 — Scope

- [x] `[6]` Decide role restriction (open question 2)
      *(NO per-role restriction. The real constraint is whether the role's BOUND MODEL
      is multimodal, which the backend already answers -- a second allowlist would
      duplicate that and drift from it. Uploading requires the operator tier; whether
      the attachment can be used is decided by the binding.)*
- [ ] `[7]` End-to-end test: attach an image, get a response that depends on it
      *(NOT run -- needs a live multimodal endpoint and a credential. Covered in-process:
      a multimodal backend receives a correctly-formed base64 image block whose bytes
      round-trip, and a text-only backend refuses.)*


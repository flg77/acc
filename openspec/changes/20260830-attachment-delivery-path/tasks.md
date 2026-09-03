# Tasks — delivering an attachment to the model

Phase 0 first. It is a live property of a deployed system; the rest is a
missing feature.

## Phase 0 — retention, before anything new is stored

- [ ] Give `attachments.prune()` a caller. It has never had one, so the store
      grows without bound and every blob in it is personal data with no expiry.
- [ ] Decide the trigger: timer, `acc-cli` command, or an agent-side sweep. The
      module says "the same retention policy as sessions" — follow that rather
      than inventing a second policy.
- [ ] `keep_digests` must be derived from the durable records, not from the
      store. Pruning by age alone would delete a blob a live record still
      points at.
- [ ] Test that a pruned blob's `load_bytes()` raises the *distinguishable*
      error the module already defines, and that the record survives it. That
      contract exists and has never been exercised end to end.
- [ ] `acc-cli doctor` reports store size and oldest blob.

## Phase 1 — the backend interface

The load-bearing change. Everything else is plumbing around it.

- [ ] Add `content: list[dict] | None = None` to `LLMBackend.complete()`
      (option A). Default `None` leaves every existing call site identical.
- [ ] `anthropic` and `openai_compat` consume it — the two backends
      `MULTIMODAL_BACKENDS` already names.
- [ ] `ollama`, `vllm`, `llama_stack` **raise** on non-empty `content`.
      **Not ignore.** A default-ignore implementation reintroduces the silent
      drop `content_blocks()` exists to prevent, and it is invisible: the model
      answers confidently about an image it never received.
- [ ] A test per backend asserting which of those two behaviours it has. This
      is the regression that would be hardest to notice later.

## Phase 2 — the path from browser to model

- [ ] `PromptRequest.attachments: list[str]` — sha256 references, not bytes.
- [ ] `WebPromptChannel.send()` carries them; TASK_ASSIGN gains an
      `attachments` field. Follow `session_id`'s precedent: omit the key
      entirely when empty rather than sending an empty list.
- [ ] `cognitive_core` resolves references to `Attachment` records and calls
      `content_blocks()` with the bound backend.
- [ ] Refusal at dispatch (see the proposal's three points): the role is bound
      and the backend is known only here, so this is the enforcement point that
      must never be skipped.
- [ ] Attribution: an attachment must be traceable to the principal who
      uploaded it. `routes_attachments` already logs `attached_by`; that has to
      survive into the record.

## Phase 3 — the web UI

- [ ] Attach control on the Prompt screen: upload, show what is attached,
      remove before sending.
- [ ] Call `GET /api/attachments/capability` and **warn** when the deployment
      cannot accept images. A warning at compose time, not a block — the role
      and backend can still change.
- [ ] Show the reference (short digest) after sending, so the transcript
      records that an image was part of the turn.

## Phase 4 — the TUI's half

Presentation differs; the capability does not (proposal
`20260830-webgui-tui-alignment`).

- [ ] The TUI shows that an attachment exists and its digest. It cannot render
      the image and should not pretend to.
- [ ] An operator watching a thread must be able to tell that a turn included
      an image — otherwise the transcript misrepresents what the model saw.

## Deferred, with reasons

- [ ] **Context budget accounting** (open question 1). An image is tokens and
      RP-04's packer measures text, so an attachment currently passes through
      unmeasured — a hole in a mechanism whose purpose is that nothing reaches
      the model uncounted. Needs the delivery path first to have something to
      measure.
- [ ] **`image_url` blocks on `/v1/chat/completions`** (open question 3).
      Natural, and would make ACC drop-in for vision clients — but it widens
      the surface HG-24 called the one most likely to be pointed at by
      something the operator did not write. Follow the web path, do not lead
      it.
- [ ] Non-image attachments (PDF, audio). `MULTIMODAL_BACKENDS` and
      `detect_media_type` are image-shaped today; widening them is a separate
      decision.

# ACC Marketplace — internal design draft

> **Status: DRAFT for internal review (not a committed plan).** Author: hub
> (workstation), 2026-06-22. Intended first home: an internal preview **on acc1**
> (see §8). This draft proposes how to turn the existing discovery scaffolding into a
> real marketplace where users **browse the full catalog** and **download community
> role releases**, with trust and reproducibility built in.

---

## 1. Why now — the pieces already exist

This is not greenfield. The marketplace is mostly an *assembly* of shipped parts:

| Piece | What it gives us | Where |
|---|---|---|
| Catalog + signing | Published, cosign-signed `.accpkg` releases with a package-level `index.json` | `acc.pkg.catalog`, `acc-roles.dev` / `flg77.github.io/acc-ecosystem` |
| **Discovery data layer** | `render_rows` / `list_versions` / `stage_install` — Marketplace-ready rows with tier badge + signer + version picker | `acc/marketplace.py` (Stage 2.4 partial) |
| **Install seam** | `stage_install` emits a `PROPOSE_INFUSE` marker → Compliance pane Package Proposals queue → operator approve/reject | `acc/marketplace.py` + Compliance pane (PR #32) |
| Tier model | `trusted` / `tp` (Trusted Partner) / `community` / `self`, with UI badges | `acc/marketplace.py` `_TIER_BADGE`, `CONTRIBUTING-ROLE.md` |
| **Reproducible download** | A-BOM: signed, exact-pin, air-gap-installable bill of materials | `acc/pkg/agent_bom.py` (proposal 040) |
| Front doors | `/catalog` slash verb, console-plugin **035 catalog-browse**, the future TUI pane + WebGUI route | `acc/slash_commands.py`, `acc/tui/screens/marketplace.py`, `acc/webgui/routes_roles.py`, acc-podman-desktop ext |

**The gap is not capability — it's the connective tissue:** a hosted, writable
**community catalog endpoint** (today's catalog is read-only Pages), a publish/review
pipeline for *community* submissions, and the presentation surfaces wired to
`marketplace.py`.

## 2. Goals / non-goals

**Goals**
- Browse the **full catalog** across every configured tier from one surface (TUI pane,
  WebGUI route, podman-desktop ext) — all reading `marketplace.py`.
- **Download + install a community role release** with the trust story intact: signed,
  EC-policy-checked, and **reproducible via an A-BOM pin**.
- Keep the **operator approval invariant**: a marketplace click never auto-installs —
  it stages a `PROPOSE_INFUSE` (or an A-BOM) for the Compliance/oversight queue.

**Non-goals (for the first internal cut)**
- Payments / the paid Verified-Publisher (`tp`) tier billing (design exists in
  `CONTRIBUTING-ROLE.md`; defer).
- Ratings/reviews/social features.
- A public, internet-facing community catalog (internal preview on acc1 first).

## 3. Architecture (proposed)

```
   Publisher (community)                Operator (consumer)
        │                                     │
        │ acc-pkg build + publish             │  browse
        ▼                                     ▼
  ┌──────────────────┐   index.json    ┌──────────────────────┐
  │ Community Catalog │◀──────────────▶│ marketplace.py        │
  │  endpoint (acc1)  │   signed .accpkg│  render_rows /        │
  │  - upload API     │   + .sig        │  list_versions /      │
  │  - cosign verify  │                 │  stage_install        │
  │  - EC attest      │                 └──────────┬───────────┘
  └──────────────────┘                            │ rows
                                       ┌───────────┴───────────┐
                                       ▼            ▼          ▼
                                  TUI pane     WebGUI route   035 console
                                  (/catalog)   routes_roles   catalog-browse
                                       │            │          │
                                       └─────┬──────┴──────────┘
                                             ▼  stage_install → PROPOSE_INFUSE / A-BOM
                                    ┌────────────────────────────┐
                                    │ Compliance / oversight queue│  ← operator approves
                                    └────────────┬───────────────┘
                                                 ▼ acc-pkg install (signed, EC-checked)
```

### 3.1 Data layer — already done
`marketplace.py` is the contract. The presentation surfaces render `MarketplaceRow`s and
call `stage_install`; they hold **no business logic of their own**. Nothing here needs
to change for browse; `render_rows(name_filter=…)` already powers a search box and
`list_versions` already powers a version picker.

### 3.2 Community catalog endpoint — the main new build
Today's catalog is a **read-only** GitHub Pages `index.json`. A real marketplace needs a
**writable** endpoint that:
1. Accepts an `acc-pkg publish` upload (it already targets `--catalog-url`).
2. **Verifies the cosign keyless signature** (Fulcio/Rekor) before listing.
3. Records the **`eval_pass` attestation** so the operator's EC policy can gate install.
4. Serves a tier-tagged `index.json` + the signed `.accpkg` + `.sig`.

Internal preview: host this on **acc1** (see §8). It can start as a thin service over a
filesystem/object store; the signing + EC checks are the substance, not the storage.

### 3.3 Download + trust — lean on the A-BOM
A marketplace "download" should not be a loose tarball grab. The trust path:
- **Single role**: `stage_install` → `PROPOSE_INFUSE @scope/name@^x.y` → Compliance pane
  → `acc-pkg install` (signature + EC verified).
- **A whole customized agentset**: emit/consume an **A-BOM** — exact `@scope/name@version`
  pins + `required_signer` floor + deploy `targets`. This is the reproducible,
  air-gap-installable unit (`acc/pkg/agent_bom.py`); it composes directly with the
  `/new-agent` onboarding flow (the Assistant already produces a signed A-BOM). The
  marketplace can offer **"download as A-BOM"** so a community release is replayable
  byte-for-byte on RHOAI / edge / standalone.

## 4. Surfaces (all read `marketplace.py`)

- **TUI Marketplace pane** (`acc/tui/screens/marketplace.py`) — browse + filter +
  version-pick + stage-install. Reachable from `/catalog [<filter>]`.
- **WebGUI route** (`acc/webgui/routes_roles.py`) — the same rows over HTTP.
- **console-plugin 035 catalog-browse** — the OpenShift console surface (feature
  branches already exist; wire it to the same data shape).
- **acc-podman-desktop extension** — desktop browse (separate repo).

## 5. Publish/review pipeline for community submissions
- Publisher path is the one in `CONTRIBUTING-ROLE.md`: `acc-pkg init → … → build →
  CI keyless sign → publish`.
- For the **community tier**, add a lightweight review gate at the endpoint: signature
  valid + EC `eval_pass` present + manifest lint clean → auto-list as `community`;
  promotion to `tp`/`trusted` stays a human decision.

## 6. Trust & safety invariants (must not regress)
1. **No silent install.** Every marketplace action stages a proposal for operator
   approval (`PROPOSE_INFUSE` / A-BOM → Compliance/oversight). `marketplace.py` already
   enforces this — keep it.
2. **Signed or it doesn't list.** The endpoint verifies cosign before a package appears.
3. **EC at install.** Unsigned/failing packages install only with operator-explicit
   `--allow-unsigned` (audit-logged).
4. **Reproducible.** A-BOM pins are exact; ranges are rejected.

## 7. Phasing (proposed)
- **P0 (internal preview on acc1):** stand up the writable catalog endpoint (upload +
  cosign verify + serve), point a workspace `.acc/catalogs.yaml` at it, browse via the
  TUI pane + `/catalog`, install via the Compliance pane. Proves the full loop with
  *self/community* tiers.
- **P1:** wire the WebGUI route + 035 console catalog-browse to the same data.
- **P2:** "download as A-BOM" + replay on a second target (edge/standalone).
- **P3 (later):** Verified-Publisher (`tp`) tier + billing; public catalog.

## 8. Open question — what "on acc1" means
The original ask said the marketplace should be *"an internal draft first → on acc1."*
Concretely this could be any of:
- **(a)** Host the **community catalog endpoint** on acc1 (a KubeVirt VM or a pod
  behind the internal quay/ingress) as the internal preview registry. ← most likely
- **(b)** Run the **WebGUI/035 browse surface** on acc1's cluster pointed at that
  endpoint, for hands-on review.
- **(c)** Just **stage this draft** for the acc1 session to pick up and prototype.

This needs an operator decision before P0; it also touches the **active acc1 session's
area**, so per fleet discipline it should be coordinated through the ledger (a hand-off
or a shared thread), not built cross-repo unilaterally.

## 9. References
- `acc/marketplace.py` — discovery data layer (Stage 2.4)
- `acc/pkg/agent_bom.py` — A-BOM (proposal 040); see
  [`agent-bom-and-new-agent.md`](./agent-bom-and-new-agent.md)
- `acc/slash_commands.py` — `/catalog`, `/new-agent`
- [`acc-pkg.md`](./acc-pkg.md) — the package toolchain
- [`CONTRIBUTING-ROLE.md`](./CONTRIBUTING-ROLE.md) — publisher path + tier model
- console-plugin **035 catalog-browse** (acc-spearhead feature branches)

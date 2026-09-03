# Surface parity — one capability contract for TUI, web, and desktop

**Status:** proposed · **Date:** 2026-08-30 · **Supersedes:** the first draft of
this proposal, which argued for a capability/idiom split. That was wrong; see
[Why the earlier framing was rejected](#why-the-earlier-framing-was-rejected).

## The requirement

**No capability is surface-exclusive.** Anything an operator can do in the TUI —
golden prompts, `models.yaml`, role authoring, catalogs, publishing — must be
reachable from the web GUI, and from any surface ACC ships later.

The reason is not symmetry, it is **lockout**. A user with no terminal has
exactly one surface. For that user, "TUI-first" and "unavailable" are the same
sentence. ACC does not get to decide that a compliance reviewer may approve an
oversight item but may not see the golden prompt that produced it.

A desktop app is under consideration. That makes this urgent rather than
tidy: a third surface built against today's structure inherits today's gaps and
doubles the drift.

## Why the earlier framing was rejected

The first draft proposed "parity for capabilities, divergence for interaction
idiom", and put golden-prompt authoring and `models.yaml` CRUD on the TUI-only
side. That reasoning does not survive the lockout argument: it decides, on a
user's behalf and without telling them, that they may not author. The line it
drew — *authoring is for operators at a terminal* — is an assumption about who
uses ACC, not a property of the work.

What survives from that draft is the measurement, and one correction: the
divergence is **not** a discipline problem, so no amount of "remember the
webgui" will fix it.

## Root cause: two access paths, not one contract

| Surface | How it reaches a capability |
|---|---|
| `acc-tui` | **imports ACC's Python modules directly** — `acc.golden_prompts`, `acc.collective`, `acc.role_loader`, `acc.pkg.catalog`, `acc.models`, … |
| `acc-webgui` | **only what 1,828 lines of route handlers re-expose over HTTP** |

The TUI never calls the HTTP API. Not once.

So a new capability is written against the library, which makes it **free for
the TUI and a separate project for the web**. Drift is not a lapse; it is the
default outcome of the architecture. The 73-to-12 commit ratio over 90 days is
the shape of that incentive, not of anyone's carelessness.

### The gap, measured as capabilities

The TUI imports **33** `acc.*` modules; the webgui reaches **17**. Twenty-three
capabilities are TUI-only today:

```
acc.assistant.catalog_view   acc.pkg.builtin_catalog   acc.role_loader
acc.capability_index         acc.pkg.fetch             acc.role_model_map
acc.capability_validator     acc.pkg.golden_pack       acc.self_challenge
acc.channels                 acc.pkg.manifest          acc.skills
acc.collective               acc.pkg.publish           acc.skills.registry
acc.config                   acc.pkg.ratings
acc.marketplace              acc.pkg.role_resolution
acc.mcp                      acc.operating_modes
acc.mcp.registry             acc.operator_identity
```

That list is the work, stated as capabilities rather than as screens — which is
the right unit, because a screen is a rendering decision and a capability is a
promise.

Two known gaps sit underneath it and are not visible in that list:

- `acc/channels/webgui.py` has **no `session_id`**, so every web prompt is
  permanently a first turn while the TUI and Slack can hold a thread.
- `routes_attachments` is mounted with **zero** frontend references — the API
  accepts an image no user can send.

## The target: a service layer both surfaces call

Not "port the TUI to React", and not "make the TUI call HTTP".

```
        acc-tui        acc-webgui        acc-desktop (later)
           \               |                   /
            \              |                  /
             +----- acc/services/ ------------+      ← the capability contract
                          |
                    acc.* libraries
```

`acc/services/` holds capability logic — one function per thing a user can do,
surface-agnostic, no Textual and no FastAPI. The TUI calls it directly. The
route handlers become thin translations of HTTP to a service call. A desktop
app picks either, and gets the same behaviour because there is only one
implementation.

Three properties this buys, in order of importance:

1. **Parity becomes mechanically checkable.** A service function with no route
   is a parity defect a test can find. That is Phase 4 of the old draft with
   actual teeth.
2. **The desktop question gets cheap.** Embed the service layer, or speak HTTP —
   either way it is a renderer, not a port.
3. **New capabilities cost the same everywhere.** The incentive that produced
   73-to-12 disappears, because writing against the library *is* writing the
   contract.

### Why not simply put the TUI on the HTTP API

It is the obvious alternative and it is worse here: **the TUI runs standalone**,
on the host, against a collective, with no web server in the picture. Requiring
one would make the operator's tool depend on a service they may not run — and
at the edge, may not be able to run. A shared in-process layer gives the same
single-implementation guarantee without that dependency.

## Scope of the parity requirement

Everything in the 23-module list, plus the two structural gaps. Explicitly
including what the earlier draft tried to exclude:

- **Golden prompts** — full lifecycle: author, version, run, promote, import,
  export.
- **`models.yaml`** — CRUD and the role→model map. Editing model mappings from a
  browser is a real security question, but the answer is *authorisation*, not
  *absence*: gate it behind the operator role, do not withhold it.
- **Role authoring** — the RoleEditor exists in the webgui already and is the
  precedent for authoring on this surface.
- **Marketplace, catalogs, packages** — including publish and ratings.
- **Skills and MCP registries.**

What legitimately differs is **presentation**: a command palette and which-key
menus are terminal idiom, and their web equivalent is a search field and a menu.
The *capability* they reach is identical and must exist on both.

## Open questions

1. **Sequence.** Extracting the service layer first is the clean order but
   delays every visible fix. Building the missing routes first fixes users
   sooner and leaves two implementations to reconcile later. A hybrid —
   extract only the capabilities being exposed, as they are exposed — is
   probably right, and needs deciding before Phase 1.
2. **Authorisation model for authoring on the web.** `models.yaml` CRUD and
   package publishing from a browser need a role check the TUI never needed,
   because the TUI's presence on the host *was* the authorisation.
3. **Does Slack become a parity surface too?** It received continuity while the
   webgui did not. Under this proposal that is a defect unless Slack is
   explicitly declared a subset — a chat channel plausibly cannot host a role
   editor. That declaration should be written down rather than left emergent.

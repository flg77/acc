# Changelog

All notable changes to the **`flg77/acc`** runtime are recorded here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning per [SemVer](https://semver.org/spec/v2.0.0.html).

Tracked since proposal 003 (ACC TUI usability hardening,
2026-05-13) — earlier changes are reconstructable from
`git log` but not back-filled into this file.

## [Unreleased]

## [0.17.2] — 2026-09-11

### Changed

- **On a system install the operator shares the service's state** (IN-07,
  operator decision 2026-09-11). The stack runs as the `acc` user and owns
  `/var/lib/acc`; the operator's own `acc` / `acc-cli` / `acc-pkg` resolve their
  state there too, and could not write it. The state tree is now setgid and
  group-writable for group `acc` (`2770`, spec and tmpfiles), the unit runs with
  `UMask=0002`, and the host commands clear the group-write bit of their umask
  when — and only when — their state is the system one. One tree: a package the
  operator installs with `acc-pkg` is the one the running stack sees. An
  operator joins with `sudo usermod -aG acc $USER`; until they have, `acc paths`
  and `acc-cli doctor --paths` say so. `/var/log/acc` stays group-readable only;
  `/etc/acc` is unchanged.
- **`docs/INSTALL.md` describes the RPM as it ships**: the two packages, the
  channel through the acc1 mirror or the Satellite (with its CA), signing, the
  by-file route for a host that cannot reach the lab (an EL9 build), the group,
  `systemctl` for the stack, and upgrading with the `acc --version` check. The
  MANUAL's "Getting started" follows. bb3 is no longer an RPM proof host — it
  consumes the agent image.

## [0.17.1] — 2026-09-11

### Fixed

- **An answered request no longer comes back in the Prompt pane.** The
  arbiter's HEARTBEAT lists a row as pending until its next beat after the
  decision, so the decision panel and the request region showed a request again
  — unfocused, for about 30 seconds — right after the operator had answered it.
  The v0.17.0 lighthouse smoke caught it on a destructive question, where a
  re-shown "run it" invites a second press. A request the pane published a
  decision for is now held back until the snapshot stops listing it; one whose
  decision failed to publish stays open. Applies to the panel, the region, a
  "yes", `/allow`, `/disallow` and a task grant alike.

## [0.17.0] — 2026-09-11

### Added

- **A destructive call is asked as a question, in the Prompt pane**
  (`20260911-question-envelope`, UX-02; operator answer 2026-09-11: *file
  deletion, data modification … are double checked as questions to the user*).
  An agent can now ask the operator a typed question: the text, the options, and
  for each one whether the action runs. It rides on the oversight row, so
  finality, the two-approver rule and the way a decision reaches a worker are
  unchanged; the chosen option comes back as `answer` on the same
  OVERSIGHT_DECISION, and an answer that does not fit the decision is refused.
  The first thing it asks: a call that **deletes or overwrites data**. That is
  judged by an exec skill's command (`rm`, `find -delete`, `git push --force`,
  `kubectl delete`, `DROP TABLE`, `shutil.rmtree`, …), a manifest's
  `destructive` / `destructive_tools`, or the name. Such a call is asked in
  every mode but PLAN — in AUTO too — at HIGH at least, and refused outright
  where there is no queue to ask. The question names what will be destroyed and
  is answered **on its own** in the decision panel: never batched, never covered
  by "allow for this task", never approved by "yes" or `/allow`, and always a
  double press. A call a destructive or CRITICAL gate let through is marked
  **critical** in the transcript and the session trace, and the answered
  question is journalled with who chose what. A gate that stops waiting now
  expires its row, so a late approval is refused instead of recorded for a call
  that never ran.

- **Every package is signed with its channel's key** (repo signing, operator
  answer 2026-09-11: *a must have*). Each channel has one GPG key, created once
  by lab-gitops `ansible/satellite-content/playbooks/channels.yml` and kept only
  in OpenBao, so a rebuilt lab gets the same key back and hosts keep trusting
  the channel. `packaging/rpm/sign-rpms.sh` signs on the build host inside a
  signer container with **no network** and its keyring on a **tmpfs** — the
  private key is piped in, never written to a disk, and every signature is
  checked against the channel's public key alone. The release pipeline signs
  both packages before publishing. `publish-satellite.sh` **refuses an unsigned
  package into a channel that carries a key**, checking against the key the
  channel itself serves, because a subscribed host is handed `gpgcheck=1` there.
  `--unsigned` exists only for a channel with no key yet.

## [0.16.0] — 2026-09-10

### Added

- **`acc-runtime` — the half of the package that belongs in a container**
  (`IN-11`). The RPM now splits: `acc-runtime` carries the vendored virtualenv,
  the commands and the data trees under `/usr/share/acc`; the full `acc` package
  adds the host layer on top (the configuration under `/etc/acc`, the state root,
  `acc-deploy`, the systemd unit and the `acc` system user) and requires the
  runtime. The host package is now 21 KB. In a pod those host parts are dead
  weight or worse: the unit drives podman-compose inside a container, the `acc`
  user is not the UID OpenShift assigns, and a `0750 acc:acc` state root is
  unwritable by it. Measured on a real image, an arbitrary UID runs the commands
  and reads the content.
- **The agent image can be built from the released RPM**
  (`container/production/Containerfile.agent-core-rpm`,
  `packaging/images/build-agent-rpm.sh`), so the version a host installs and the
  version a pod runs are the same NEVRA from the same channel. It installs
  `acc-runtime`, pins the version, keeps configuration out of the image, and runs
  under an arbitrary UID in group 0. The pipeline builds from a **tag**, then
  checks the package it carries, that `acc --version` agrees with it, that it
  runs as an arbitrary UID, and that `acc.agent` imports; pushing stays the
  operator's step.

  **Only this image.** The RPM vendors one dependency set while the other images
  each carry a hand-picked one — the web GUI installs ten packages and no ML,
  which is why it is 487 MB, and from the RPM it would be about 2.5 GB. The agent
  is the one component whose weight already matches, because it does embeddings.
  A test guards the decision rather than trusting it.

- **The release pipeline is one command** (`packaging/rpm/release-pipeline.sh`,
  driven by the `acc-package-release` skill). The internal Satellite is the
  distribution base, so a tag that is not in the channel is a release the hosts
  cannot get. The pipeline takes a tag the whole way: `git archive` of the **tag**
  (never the working tree) to the build host, build, refuse a package carrying
  CUDA or missing the layout, publish to the channel, then upgrade a real client
  **from the channel** and require `rpm -q` and `acc --version` to agree. That
  last check is why it exists: an upgrade once installed 0.14.4 and left the host
  running 0.14.3, and `rpm -q` alone called it a success. `RELEASE=2` rebuilds
  the same source as a new package, `--dry-run` says what would happen, and
  `--no-client` skips the proof and says so.

- **`acc-prompt` — the decision panel in the Prompt pane** (`UX-01`). When one
  request of one step is waiting, the Prompt pane now renders it in full: the
  question in the operator's terms, the numbered options, and **beside them what
  the highlighted option actually does** — its consequence, whether it is
  remembered for the task or asked again, the destination, the risk, and how many
  approvals it still needs. `PENDING 1/2` and who has approved show where the
  decision is made rather than only in the Compliance queue. `↑`/`↓` and `Enter`
  or the digit decide; a HIGH or CRITICAL approval still takes the key twice.
  `n` types a note onto the decision without leaving it, and `c` asks a question
  about the decision **while it stays pending** — a doubt no longer has to become
  a dismissal. `Esc` leaves it pending and `Ctrl+G` returns to it. A reply
  proposing several steps keeps the compact request region, which can approve all
  or take rows one at a time: depth for one decision, a list for many.
  `ACC_PROMPT_PANEL=0` sends everything to the compact region. No new signal — a
  decision still resolves through the same `_OversightAction` the Compliance
  queue uses.

### Changed

- **An approval can carry the operator's reason.** `OversightQueue.approve()`
  takes a `note` and keeps it on **that person's** approval record, so a
  two-approver row carries a reason per signature rather than one shared field;
  the agent's decision handler passes the wire's existing `reason` through on
  APPROVE instead of dropping it. Rejection is unchanged.

## [0.15.0] — 2026-09-09

### Added

- **ACC speaks the Risk Atlas Nexus vocabulary** (`20260908-asago-alignment`
  AS-01). asago — Red Hat's policy-to-controls loop, whose evaluation stage
  is OpenShift AI's EvalHub — keys everything it produces on IBM AI Atlas
  Nexus ids. A pinned, dated snapshot of those ids lives in
  `regulatory_layer/nexus/vocabulary.yaml`; framework controls carry
  `nexus_ids` (the NIST AI RMF catalog its own `nist-gv|mp|ms|mg-<n.m>`
  ids; the threat model its risks, in the withheld twin only); `acc/nexus.py`
  validates every id offline, indexes `nexus id → ACC controls`, and exports
  the mappings as an **SSSOM** TSV; `acc-cli compliance mappings | nexus <id>
  | coverage`; gap reports carry the ids per control. EU AI Act, ISO 42001
  and SOC 2 stay unmapped — the Nexus has no entities for them.
- **Drop an asago policy into ACC** (`acc-cli compliance risks
  risk-extraction.json`): for every risk asago's policy mapper extracted, the
  ACC controls and threats that carry the same Nexus id (or one of the
  risk's cross-mapped ids) and whether the deterministic gap analysis finds
  a loaded rule covering them; unanswered risks are named; `--json` is the
  record. `docs/HOWTO-asago-policy.md` is the end-to-end guide.
- **The two-approver hub gate, beside single approval**
  (`20260906-enterprise-brain-hub-scope` Phase 2b, HG-40.1 §2.5; operator
  decision 2026-09-07). A publish proposal into a **hub** whose note is
  **HIGH or CRITICAL** now asks for **two distinct operator-tier approvals**
  (`assistant_proposal.approvals_required`; the proposal says
  `[2 operator approvals]`); MEDIUM and below, and any destination inside the
  collective, keep the single decision D-013 made final. The oversight row
  carries `required_approvals` and an `approvals` record (who, tier, when):
  the first operator's approval is recorded and the row stays `PENDING 1/2`
  (CLI `oversight pending`, the Compliance queue, the heartbeat); the same
  person again — in any room, or the fan-out replay of one decision — is one
  approval; an approver below operator tier is refused on such a row; a
  reject at any point is final. The publish dispatcher re-checks the record
  fail-closed (two distinct people, all at operator tier) and journals
  `note_publish_refused` with the record otherwise. Decision-history
  statistics never feed the count.
- **The internal Satellite is the distribution base** for ACC packages
  (`20260909-acc-install` IN-10). `packaging/rpm/publish-satellite.sh` uploads a
  built RPM into the `acc-spearhead` repository of the `ACC` product, running
  `hammer` on the Satellite itself so no API credentials sit on the build host,
  and refusing a snapshot build unless `ALLOW_SNAPSHOT=1` says so on purpose.
  A spearhead build still never reaches a public repository; COPR is fed from the
  mirror only.
- **Semantic versions map onto RPM versions** (`packaging/rpm/version.py`). RPM
  cannot hold a semantic version directly — `-` is illegal in both fields and
  ordering is per field — so `0.15.0-rc.1` becomes Version `0.15.0`, Release
  `0.rc.1`, which sorts *below* the `1` of the release, and a build that is not
  the clean, tagged tree takes `0.<commit stamp>.g<sha>` and can never
  impersonate one in the channel. `RELEASE=2` covers a packaging-only rebuild of
  the same source. `rpm.labelCompare` is asserted on every ordering.

### Fixed

- **An upgrade could leave the host running the old code**
  (`20260909-acc-install`, found upgrading acc1 from 0.14.3 to 0.14.4 through the
  Satellite channel: `rpm -q acc` said 0.14.4 and `acc --version` said 0.14.3).
  The first `acc` run had written `.pyc` files into the venv — root can write
  there — owned by no package and using Python's default *timestamp*
  invalidation. RPM restores each packaged file's recorded mtime, so the new
  source never looked newer than the cache it was compared against and Python
  went on serving the old bytecode. The package now compiles the venv itself, so
  those exact paths are package-owned and an upgrade replaces them, and it
  compiles them **checked-hash**, so a cache that survives anyway is revalidated
  against the source it claims to cache rather than against a clock.

## [0.14.4] — 2026-09-09

### Added

- **`acc` — the one command an operator starts with** (`20260909-acc-install`
  IN-02, proposal 055). `acc` attaches the TUI to the running collective;
  when nothing answers on the bus it says so in one line (`start it with
  'acc stack up'`) and exits 3 instead of drawing an empty screen. `acc stack
  up|down|status` runs the deploy script from the host's ACC layout, `acc
  doctor` / `acc setup` delegate to `acc-cli`, `acc paths` prints where this
  host's ACC lives. `acc-cli --version` and `acc-pkg --version` exist
  (`acc-cli -v` stays verbose).
- **The RPM** (IN-06, artefacts). `packaging/rpm/`: `acc.spec` (a vendored
  virtualenv under `/usr/lib/acc/venv`; `/usr/bin/{acc,acc-cli,acc-pkg,
  acc-tui,acc-webgui,acc-deploy}`; `/etc/acc` with the four configs as
  `%config(noreplace)` and `acc.env` 0640 root:acc; `/usr/share/acc` = the
  wheel's data trees; `/var/lib/acc` owned by the `acc` system user), the
  `acc-stack.service` unit (`acc-deploy up --webgui` / `down` as `acc`,
  `NoNewPrivileges`, the layout in its environment), sysusers and tmpfiles,
  `build.sh` (wheel → `rpmbuild -ba`, `MOCK_ROOT` for other targets) and a
  README with the two channels (Satellite for the spearhead build, COPR fed
  from the mirror) and the proof order acc1 → bb3 → saturate3. The `acc`
  user never gains root: no sudoers entry, no capabilities, rootless podman
  under its own sub-uid range. `ACC_ENV_FILE` names the secrets file for
  the deploy script and the compose (default `.env` beside the configuration).
  The package is arch-specific: the vendored virtualenv carries compiled
  wheels (pydantic-core, LanceDB), which `rpmbuild` refuses in a `noarch`
  package. The spec pins the FHS directories and `build.sh` computes a clean
  `%dist`, because a build host may carry decorated macros of its own. The
  vendored virtualenv installs **CPU torch** from PyTorch's own index before
  the wheel resolves its dependencies: `sentence-transformers` (the local
  embedding fallback) makes `torch` a core dependency and the PyPI default
  pulls ~5 GB of CUDA wheels that a host running the collective in containers
  never executes; `%install` fails if any of them slip in anyway.
- **The data trees leave the checkout** (IN-05). The wheel now ships
  `roles/`, `skills/`, `mcps/`, `collectives/`, `container/production/`,
  `regulatory_layer/`, the `*.example` configs and `acc-deploy.sh` under
  `acc/_share` (`packaging/build_share.py`, run by `setup.py` at build
  time); `acc.paths.share()` finds that tree next to the package, after an
  operator's home and before `<prefix>/share/acc`. `acc-deploy.sh` reads
  `ACC_HOME` (configuration + state) and `ACC_SHARE` (the data trees) and
  exports `ACC_HOME_DIR` / `ACC_SHARE_DIR` / `ACC_IMAGE_PREFIX` for the
  compose file, whose host mounts and image names now interpolate them
  (default `../..` and `localhost`: a checkout behaves exactly as before);
  the version falls back to the package's when there is no git. The overlay
  generators (`collective.roles_to_compose`, `instances.cell_volumes`) emit
  the same interpolated layout, and everything the runtime writes (logs,
  workspaces, instances, `.acc-apply`) interpolates `ACC_STATE_DIR` —
  `ACC_STATE`, else beside the configuration. A first
  `setup` on a host with no configuration writes into `~/.config/acc/`,
  seeded from the shipped template, never into the template. `docs/INSTALL.md`
  is the install guide (wheel, checkout, the RPM's layout).
- **The first-run tour** (IN-09; the operator's remark that first-time users
  need a guided onboarding, in ACC's own terms). The first `acc` on a host
  with no configuration runs the guided setup (HG-08) and then a seven-step
  tour over the TUI: who you are here (principal, tier, ceiling), the
  Prompt and the operating mode (AUTO is never the default), the Board,
  Compliance with **one sample gate queued for you to decide** (nothing runs
  on it either way), your workspace and its trust, `/new-agent` and the
  signed AgentBOM (prod-locked), and what stayed at its floor. Once on an
  installed layout (a marker under `~/.config/acc`), on request via `acc
  tour`, never inside a developer's checkout unasked; skippable at any step.
- **Trusted directories** (IN-03 / IN-04). `acc` started in a directory
  proposes it as the session's workspace and asks once — `[y = this session
  / N / always / below = and everything under it]`; a `below` answer on a
  directory holding many repositories is confirmed a second time. The
  answer is recorded in `~/.config/acc/trust.yaml` (path, scope, decision,
  since, by = the resolved principal; a "no" is remembered too), the
  directory rides the session as `ACC_WORKSPACE_HOST_DIR` — the D-007 mount
  — with the `.acc-workspace-trust` sentinel the cells check written on
  grant, `acc stack up` mounts a trusted current directory, and the
  Select-Directory dialog opens there. A directory never trusted is never
  mounted, never read by an `fs_*` skill, never selected. `acc-cli
  workspace list|check|trust [--below]|deny|revoke` manages the record;
  `--workspace <dir>` / `--no-workspace` on `acc`. The filesystem root, the
  user's home itself and ACC's own home are never proposed.
- **One discovery rule for where ACC lives** (`acc/paths.py`, IN-01). Every
  default that was relative to the current directory — `acc-config.yaml`,
  `models.yaml`, the roles root (which had two different defaults), the
  skills and mcps trees — now asks one rule: `$ACC_HOME`, else
  `~/.config/acc` when it holds `acc-config.yaml`, else `/etc/acc`, else the
  checkout the current directory is in, else the legacy default. Environment
  variables keep winning; a developer inside a checkout sees no change; a
  container's `/app/...` is just a home the image sets. `acc-cli doctor
  --paths` prints each resolved path with its source (`env` / `home` /
  `share` / `checkout` / `cwd` / `default`). This is why `acc-cli` and
  `acc-pkg` worked inside the checkout and nowhere else.

### Fixed

- **Discovery survives a candidate it may not read** (`20260909-acc-install`,
  found by the first install on acc1). `/etc/acc` is `root:acc`, so an operator
  outside that group cannot `stat` inside it and `acc paths` died with a
  `PermissionError` instead of moving on to the next candidate. Every probe in
  `acc.paths` now treats an unreadable candidate as "not ours". The package
  also makes `/etc/acc` traversable (0755 root:acc): the four `*.yaml` are
  already 0644 and the operator's own `acc` must read them without joining
  group `acc`; `acc.env`, the secrets, stays 0640 root:acc.

## [0.14.3] — 2026-09-07

### Added

- **Plan steps run as the person whose plan it is**
  (`20260906-principal-category-ceiling`, the task it left for a separate
  change; D-017's second gap). The executor's step `TASK_ASSIGN` now carries
  the plan's attribution — `requested_by`, subject, source, tier, **ceiling**,
  channel, scope (`attribution.ATTRIBUTION_KEYS`, `inherit_attribution`) — on
  every dispatch, fan-out member and re-issue. So D-014 holds one hop down (a
  requester's step is refused above the requester's ceiling, not run under
  the role's grants alone), the step's episode lands in the requester's
  memory scope, and the note distilled from it carries their ceiling instead
  of reading CRITICAL and hiding from them. A step that names its own
  requester keeps it; an unattributed plan keeps its wire shape.

### Changed

- **`acc-cli plan submit` stamps the full attribution** of the submitting
  principal (it stamped `requested_by` alone since v0.14.2), as surface `cli`;
  `cli` is a **pooled** memory source like `tui` and `webgui`, so the
  operator's plan work is not isolated from their other work.

## [0.14.2] — 2026-09-07

### Added

- **Per-requester views** (`20260906-enterprise-brain-hub-scope` item 4,
  HG-40.1b). The shared surfaces showed the whole collective to anyone who
  could open them. One policy now applies everywhere (`work_board.visible_to`
  / `filter_snapshot`): an **operator sees everything**; anyone else sees
  the items **they asked for** — matched by person, scope suffix dropped —
  and nothing unattributed (the operator's own). Applied by the TUI Board,
  Compliance (pending queue and decision history) and Comms, and by the Web
  GUI board, snapshot and WebSocket (each socket receives its principal's
  view). For it to be decidable the requester now reaches every view source:
  the observer's signal log, the plan snapshot, `acc-cli plan submit`
  (stamps the submitting principal) and the executor's heartbeat summaries.

### Fixed

- **The Board never saw a single prompt task live.** The TUI observer only
  logs signal types it has a handler for, and it had none for `TASK_ASSIGN`,
  so the Board's single-task source (`20260903-work-board-tui` 1.2) only
  worked in tests that seed the log. A handler is registered, and the
  signal ring holds 300 entries instead of 30 (one task's progress lines plus
  a six-agent collective's heartbeats evicted a 30-entry ring in under a
  minute); Comms still renders the last 30.
- **Web GUI prompts were attributed to the server's OS user.** Since v0.13.0
  the inherited TUI attribution stamped `system:<server user>` on every web
  prompt; the Web GUI now stamps its own session (`webgui:<user>`, the
  session's tier and ceiling, `requester_source=webgui`).

## [0.14.1] — 2026-09-07

The hub curator (HG-40.1b Phase 2, #362): the enterprise brain's only agent,
and hub promotions approved at operator tier only.

### Added

- **The hub curator** (`20260906-enterprise-brain-hub-scope` Phase 2, HG-40.1b
  item 11). A control role (`roles/hub_curator`) with no chat surface and no
  skills that, every `curate_interval_s`, looks at the shared tiers of the
  instances bound to its hub and queues one publish proposal into the hub's
  enterprise tier per note that rests on two people, is past probation and is
  not in the hub yet (a note proposed once is not re-proposed for a week).
  People approve. Roles gain `chat_surface` (a role without one drops every
  `TASK_ASSIGN`) and `curate_interval_s`. The curator ships in `roles/` (the
  operator's catalogue knows it); it is not in the signed control-roles pack.
- **Hub promotions need an operator-tier approver.** Every decision surface
  (TUI, Web GUI, `acc-cli oversight approve`) now stamps `approver_tier`;
  a publish proposal into `hub:<cid>` is refused, and journalled as
  `note_publish_refused`, unless the approver is at operator tier (fail
  closed for a surface that sends no tier). Ordinary destinations are
  unchanged.

### Changed

### Fixed

## [0.14.0] — 2026-09-07

The enterprise brain (HG-40.1b Phase 1, #359): a hub memory scope as the only
cross-instance read path, and the information rule enforced at every memory
boundary. Minor bump: memory retrieval now filters by the requester's ceiling.

### Added

- **The enterprise brain: a hub memory scope** (`20260906-enterprise-brain-hub-scope`,
  HG-40.1b Phase 1). A publish proposal may name `hub:<hub collective id>`
  as its destination; on approval the note lands in the hub's **enterprise
  tier** under the hub's collective id, which every instance bound to that
  hub (`hub_collective_id`) reads on the prompt path — the only
  cross-instance read path (instances never read each other's shared
  tiers). `acc-cli memory notes --hub`, `memory propose --to …`
  (queues a publish proposal from the cache; a person approves),
  `memory curate --hub … [--propose]` (the curator's job by hand: shared
  notes across the bound instances that meet quorum and are past
  probation); `memory forget` unpublishes from the hub too.
- **The information rule is enforced.** A memory note carries the highest
  category ceiling among its source tasks (an unattributed source counts as
  the operator's, CRITICAL); hot-cache entries and published copies carry
  it; the prompt-path read skips any note above the requester's ceiling —
  at every boundary, hub included. Cache entries also carry the note id and
  the people behind it, so a note can be proposed from the cache alone.

### Changed

### Fixed

- **Two instances on one host collided on container names.** The compose
  renderer names cells by role; an instance's overlay now prefixes every
  service and container name with the instance id (`acc-<id>-analyst-1`).
  Found by the HG-40.1b Phase 2 run, which brought up two instances and got
  one set of cells.
- **`acc-cli memory forget` never had a vector backend.** `_backends()` read a
  misspelt config attribute (`cfg.vector.path`), so every erasure was refused
  with "no vector backend"; it now opens `vector_db.lancedb_path`, and its
  diagnostics go to stderr so `--json` output stays parseable.

## [0.13.1] — 2026-09-06

One fix from HG-40.1a Phase 2 on lighthouse (#356): instances run under
rootless podman.

### Fixed

- **Instance cells could not write their roots under rootless podman.** The
  cells now mount `lancedb/` and `trace/` with `U,z` like the base volumes
  (the cell's sub-uid owns them on start), `overlays/` read-only (the
  operator's to edit) and do not mount `sessions/` (the TUI's, on the host);
  the first lighthouse run of v0.13.0 crash-looped every cell on
  `Permission denied: …/lancedb/<aid>`. Reading a cell-owned root from the
  host is `podman unshare`.

## [0.13.0] — 2026-09-06

Instances (HG-40.1a, #354): a collective bound to an owner, a posture and its
own state, and a TUI that carries its owner. Minor bump: new surface, new
attribution on every TUI prompt.

### Added

- **Instances** (`20260906-acc-instance`, HG-40.1a). A collective bound to
  an **owner** (a principal the substrate vouches for), a **posture** (a
  deployment profile) and its **own state roots** — LanceDB, sessions, trace,
  overlays — under `instances/<id>/`, with the instance id as the collective
  id. `acc-cli instance create|list|show|archive|export|import|synth|env`;
  `./acc-deploy.sh instance up|down|synth <id>` runs it beside the base stack
  with the instance's environment and mount (`roles_to_compose(extra_env=,
  extra_volumes=)`); cells read their overlay dir from `ACC_COLLECTIVE_DIR`.
  `export` carries the definition (installed set, overlays, posture), never
  state, and says so; the signature field is reserved.
- **The TUI carries its owner.** Decisions and board actions are stamped with
  the resolved principal (`system:<user>`, `kubernetes:<sa>`) instead of
  `tui:anonymous`, and every prompt from the TUI is attributed
  (`requested_by`, tier, ceiling; memory source stays `tui`). An
  unattributed TUI still works.

### Changed

### Fixed

## [0.12.1] — 2026-09-06

Two fixes from the v0.12.0 lighthouse smokes (#351).

### Fixed

- **A lost LLM connection no longer strands a task** (found by the v0.12.0
  fold smoke on lighthouse). `process_task` raising inside the task loop used
  to escape the bus callback with no `TASK_COMPLETE`, so a PLAN step whose
  member lost its gateway connection stayed RUNNING for good; the task now
  ends as a **blocked completion** carrying `task_error: <type>: <message>`,
  and the executor cascades. The OpenAI-compatible backend also retries a
  broken transport (`httpx.HTTPError`, e.g. "Server disconnected without
  sending a response") like a timeout and raises a typed `LLMCallError`
  after the attempts are gone.
- **Board: a member folded under its step shows the step's role** when the
  cluster topology row carries no `target_role` (the observer never learns
  it).

## [0.12.0] — 2026-09-06

The governance floor under multi-user deployments (#348, D-014) and the work
board's durability (#349). Minor bump: compat-endpoint callers change behaviour.

### Added

- **Per-principal category ceiling (D-014,
  `20260906-principal-category-ceiling`).** Every principal now carries a
  ceiling on the `LOW < MEDIUM < HIGH < CRITICAL` scale, defaulting from the
  tier (`viewer` LOW, `requester` **MEDIUM**, `operator` CRITICAL) and
  narrowable per admission (`acc-cli access admit --ceiling`,
  `Grant.ceiling` in `access.yaml`; never widenable). Admitting surfaces
  stamp `requester_ceiling` on the task; `identity.ceiling_of()` reads it
  back with a tier fallback and no ceiling for unattributed work. In
  `capability_dispatch` an invocation above the ceiling is **refused before
  any escalation or gate** (no oversight row); in the cognitive core an
  assistant proposal above it is dropped with a line in the reasoning trace.
  `access list/check/whoami` show the ceiling.
- **Work board Phase 2 — durability** (`20260903-work-board-tui`). Every PLAN
  step transition is a `KIND_PLAN_STEP` tracelog record (session
  `plan-<plan_id>`); the executor mirrors each broadcast body to Redis under
  `acc:plan:<cid>:<plan_id>` for 24 h; the arbiter heartbeat carries
  `active_plans` summaries and the TUI / WebGUI observer cold-starts the Board
  from them when it joins after the PLAN was broadcast.

### Changed

- **Board: members fold into their step.** A cluster member whose task id
  carries a plan step's prefix is shown under the step (`parent`, `plan_id`,
  `step_id`; the step card counts `members`) instead of as a second cluster —
  including a finished member the topology no longer lists, which used to
  appear as a solo DONE task with an empty role.

- **OpenAI-compatible endpoint callers are requesters at MEDIUM.** A role
  reached through `ACC_COMPAT_API_KEYS` can no longer run a HIGH skill or
  queue an INFUSE / ROLE_UPDATE / PUBLISH proposal on the key's word; the
  caller's attribution now also carries `requester_tier`.

### Fixed

## [0.11.4] — 2026-09-06

One fix, found by the v0.11.3 Prompt-pane smoke on lighthouse (#345).

### Fixed

- **`acc-cli oversight submit` produced one row per agent.** Every agent
  subscribes to `oversight.submit` (deliberately — decisions must reach all)
  and each minted its own id, so one synthetic submit showed up as N pending
  rows on an N-agent collective (four on lighthouse, 2026-09-06). The CLI now
  mints the `oversight_id` and sends it; `HumanOversightQueue.submit` accepts
  an explicit id and leaves an existing row untouched; an agent that receives
  a submit without an id derives the same UUID5 from the event on every agent.
  The CLI's stale "the arbiter must subscribe" note is gone.

## [0.11.3] — 2026-09-06

The three oversight-queue warts found by the v0.11.1 Prompt-pane smokes (#343).

### Changed

- **A decision on an oversight row is final.** `HumanOversightQueue.approve` /
  `reject` now return a bool and refuse a row that is already decided the
  other way (or EXPIRED): the first decision stands, a late or conflicting
  one is logged and dropped, and the agent handler skips the dispatch. The
  same decision arriving again (every agent applies it) is an idempotent
  no-op that keeps the first approver. Before, a REJECT after an APPROVE
  flipped the row, and a late APPROVE on an expired gate would have
  dispatched (lighthouse 2026-09-05).

### Fixed

- **The DECISION HISTORY showed one row per agent.** Every agent applies the
  same `OVERSIGHT_DECISION`, and each pushed the id onto the decided list, so
  a six-agent collective showed six copies of one decision. `_push_decided`
  removes an earlier copy before pushing, and `recent_decisions` de-duplicates
  a list written before the fix.
- **`acc-cli oversight pending` truncated ids that `approve` / `reject` then
  could not find.** The table prints the full id, and `approve` / `reject`
  accept a unique prefix, resolved against the next arbiter heartbeat; an
  ambiguous or unknown prefix is an error, never a guess. A cleanup loop that
  fed the old table's ids back in had every reject answered "item not found"
  while the CLI printed "published".

## [0.11.2] — 2026-09-05

One fix, found by the v0.11.1 Prompt-pane approve/deny smoke on lighthouse.

### Fixed

- **Proposal rationale, outcome notices and TASK_PROGRESS never reached the
  TUI observer.** `NATSBackend.publish` takes JSON *bytes* and msgpack-packs
  them; the assistant-proposal publishers (pending payload, outcomes, the
  infuse continuation) and the TASK_PROGRESS emitter hand it a **dict**, so
  a msgpack *map* went on the wire. Agents tolerate that (`_payload_bytes`),
  the observer's `unpackb → json.loads` does not — on lighthouse the hub
  counted 81 decode errors and the Prompt pane showed the generic gate card
  (no rationale, one request per row instead of per reply) and no live
  progress line. The backend now normalises any non-bytes payload to JSON
  bytes, and the observer accepts a map from an older agent. Found by the
  v0.11.1 Prompt-pane approve/deny smoke (2026-09-05).
## [0.11.1] — 2026-09-05

The work board (HG-39: `PLAN_STEP_CONTROL`, the TUI Board, the WebGUI kanban)
and the TUI profiles (`acc-tui --profile user|operator`, one screen registry),
both merged after v0.11.0. The three work-board design questions were answered
by the operator on 2026-09-05 and recorded in `20260903-work-board-tui/proposal.md`.

### Added

- **The Board in the WebGUI.** A real kanban — five columns of cards over the
  same pure projection the TUI Board renders (`GET /api/board/{cid}`), with
  Cancel / Retry / Reassign buttons that publish `PLAN_STEP_CONTROL` or
  `TASK_CANCEL` through `POST /api/board/control` with the logged-in
  principal as `actor` — the WebGUI is the surface where an intervention can
  be attributed. Re-fetched on every WebSocket snapshot push. Nobody drags a
  card to Done. `openspec/changes/20260903-work-board-webgui`.

- **The Board — work in flight, and the interventions a human may make.**
  A new TUI screen (`Ctrl+A` + digit / `Ctrl+P`, on both profiles) listing
  what the runtime is doing under QUEUED · RUNNING · BLOCKED · DONE · FAILED:
  PLAN DAG steps (with reviewer iteration and critique), cluster fan-out
  members, single prompt tasks, and the oversight gates that block them — the
  join between a step's task and its pending gate the runtime never made.
  `c` cancel / `r` retry / `a` reassign publish the new **`PLAN_STEP_CONTROL`**
  signal that the arbiter's `PlanExecutor.on_step_control` applies (cancel
  sends `TASK_CANCEL` to a running agent and skips dependents; retry resets a
  failed / cancelled step and its skipped dependents; reassign retries under a
  new role; all idempotent); `g` goes to the Prompt pane to answer the gate.
  The projection is one pure function (`acc/work_board.py`) shared with the
  WebGUI board (`20260903-work-board-webgui`). Nobody drags a card to Done.
  `openspec/changes/20260903-work-board-tui` (HG-39).

- **`acc-tui --profile user|operator`** (`ACC_TUI_PROFILE`). `operator` is
  today's TUI byte for byte and stays the default. `user` puts only
  **Prompt** and **Compliance** on the strip and opens on Prompt — the
  conversation and its decisions live in the Prompt pane since 0.11.0, and
  Compliance keeps the record and history; the other nine screens are one
  `Ctrl+A` chord (or `Ctrl+P`) away. A profile is a view choice: nothing about
  what agents may do, what is asked, or what Compliance records differs.
  Passed through the container stack as `ACC_TUI_PROFILE`. An unknown value
  falls back to `operator` with a warning.
  `openspec/changes/20260902-tui-profiles` Phase 1b.

- **One registry for TUI screens.** `acc/tui/registry.py` is now the only place
  a screen is declared (name, digit, label, class, help id, whether it receives
  snapshots). The nav strip and its bindings, `ACCTUIApp.SCREENS` (aliases
  included), the `?` help map and the snapshot fan-out all derive from it;
  the four hand-maintained copies are gone. `tests/test_screen_registry.py`
  fails if a screen class declares a `snapshot` reactive without being
  registered for it — the class of bug that left the Prompt pane without
  snapshots for months (#321), and that still had **Diagnostics** unfed until
  this change. The operator-facing strip is unchanged.
  `openspec/changes/20260902-tui-profiles` Phase 1a.

### Changed

### Fixed

- **`acc-webgui` could not import its own app since v0.8.0.** `routes_attachments`
  (image input) declares an `UploadFile`, and FastAPI refuses to build that route
  unless `python-multipart` is installed — the `webgui` extra never declared it and
  `Containerfile.webgui` installs its deps by an explicit list that omitted it, so
  `create_app()` raised at start-up in the shipped image (verified against the
  v0.10.2 image on lighthouse during the v0.11.1 smoke). Both now carry
  `python-multipart>=0.0.13`.

- **The Comms ACTIVE PLAN DAG never moved.** The arbiter re-broadcasts the
  PLAN with `step_progress` on every transition, but the TUI observer only
  initialised every step to PENDING and, on a re-broadcast, "preserved
  progress" — it never read the field. It does now (and learns `CANCELLED`,
  `step_tasks`, `step_meta`). Same bug class as #321.

## [0.11.0] — 2026-09-03

### Added

- **The assistant's own prompt says what is actually asked.**
  `roles/assistant/role.yaml` no longer tells the model that infusion
  "ALWAYS routes through the Compliance queue" or that "HIGH-risk skills are
  oversight-gated"; it now states the rule the runtime enforces since D-011:
  a curated infuse / spawn / route executes under `AUTO` / `ACCEPT_EDITS`
  (an unsigned pack is refused), system access and acting in the operator's
  name are asked in the Prompt pane, an ungranted skill is asked as an
  escalation, and the model should give a one-line reason so the operator
  can decide. Reasoning-affecting role edit — see the PR for the bench run.
  Also: `docs/WORKFLOW_infusion_to_prompt.md` §3 (the spawn path and where
  the decision is made) and `acc/tui/help/prompt.md` (the permission request,
  keys, outcome lines, `/done`).
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals` 1.6.

### Added

- **Outcomes and continuation replies land in the Prompt thread.** What
  became of a proposal was a log line in a container. The agents now stamp
  their outcome notices (`infuse_completed`, `proposal_dispatch_failed`) with
  `ASSISTANT_PROPOSAL_OUTCOME`, and the arbiter publishes a `reconcile_result`
  (assigned / unmet) after a reconcile that did or could not do something; the
  TUI observer keeps them on the snapshot and the Prompt pane renders each
  once as a `system` line — *"✓ installed @acc/redhat-sre-roles@0.1.0"*,
  *"✓ spawned product_security_advisor → worker-00"*, *"✗ spawn …: no dormant
  worker — raise `worker_pool` … or run `./acc-deploy.sh apply
  worker-pool`"*. After a reply the pane **holds the thread**: a later
  TASK_COMPLETE on the same task id (the infuse continuation) is delivered
  through a new follow-up listener registry on the observer and appended under
  the originating exchange (`↩`), instead of being dropped as "already
  received". The thread is released on the next send or `/done`.
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals` 1.5.

- **The permission request lives in the Prompt pane.** The 044 B8 gate-card
  region is now a focusable **PermissionRequest**: when a gate arrives it takes
  focus (the pop-up, refitted to the pane), shows one request per originating
  reply — every step with the assistant's own rationale and the operator's
  goal — and offers numbered options by what it is: a proposal batch
  (`1 approve all · 2 reject all · a/d this row`), a capability gate
  (`1 allow once · 2 allow for this task · 3 deny`), an escalation (`1 allow
  for this task · 2 deny`), a publication (`1 approve · 2 reject`). HIGH /
  CRITICAL approvals take the key twice (inline confirm). `Esc` leaves
  everything pending and hands focus back; `Ctrl+G` returns; `r` prefills
  `/oversight reject <id> ` for a reason. "Allow for this task" is a pane-held
  grant keyed `(task, kind, target)`; later matching gates resolve themselves
  with reason `allowed-for-task` — still a row. Every option posts the same
  `_OversightAction` as Compliance, which keeps the record. `/oversight
  pending|approve|reject` is wired in the pane. The observer now routes the
  assistant's `ASSISTANT_PROPOSAL` payload (stamped with a `signal_type`) onto
  the snapshot so the *why* reaches the pane. `ACC_PROMPT_PERMISSION_REGION=0`
  degrades to the plain card.
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals` 1.4.

- **An off-role skill or MCP tool is now a question, not a bare refusal.**
  When the enforcing A-017 / A-018 guard would refuse an invocation on the
  role's side (not in `allowed_skills` / `allowed_mcps`, a missing
  `requires_action`, above the role's risk ceiling) and an oversight queue is
  present, the dispatcher submits an `ESCALATION …` row naming the missing
  grant and blocks on it; on APPROVE the role is widened for **that one call**
  (a `model_copy` — the role definition is untouched) and the call runs
  without a second category question. REJECT, EXPIRED, headless and "no
  queue" all still refuse. A manifest's own `denied_tools` sandbox is never
  escalated. Operator decision 2026-09-02 (D-011).
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals` 1.2b.

- **Gate categories: system access and acting-on-behalf are asked under
  `AUTO`.** Two optional flags on skill and MCP manifests, `system_access`
  and `acts_on_behalf`, orthogonal to `risk_level`; a manifest that declares
  neither inherits from a name table (`shell`, `exec`, `fs_write`, `deploy`…
  / `send`, `post`, `publish`, `mail`…) so a third-party skill cannot escape
  by omission, and a declared `false` opts out. `should_gate_invocation`
  gates either category in `AUTO` and `ACCEPT_EDITS` (CRITICAL and
  `ASK_PERMISSIONS` unchanged). The oversight row leads with the category
  (`SYSTEM-ACCESS skill shell_exec: …`) and carries the manifest's own risk
  instead of a blanket CRITICAL. Declared on `shell_exec`, `python_exec`,
  `fs_write` (system access) and `telegram_send`, `slack_post`,
  `mattermost_post` (acts on behalf); `google_workspace` tool names such as
  `gmail_send` fall to the name table. **Behaviour change for the
  assistant:** `shell_exec` / `python_exec` were admitted silently under
  `AUTO` at HIGH; they are now asked — in the Prompt pane once 1.4 lands, in
  Compliance until then.
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals` 1.2.

- **Auto-executed proposals are tracked as `AUTO_APPROVED` oversight rows.**
  Since the dispatch change below, a curated infuse / spawn / route under
  `AUTO` or `ACCEPT_EDITS` left only a log line. Now the agent's EXECUTE branch
  records a row on the oversight queue that is born resolved — status
  `AUTO_APPROVED`, `approver_id = policy:<mode>`, `outcome` `dispatched` /
  `dispatch_failed` — and a `KIND_OVERSIGHT` tracelog record that outlives the
  queue's TTL. The queue keeps a capped, newest-first **decided list** (human
  and policy alike; decided rows now live 24 h in Redis instead of the gate
  window), the arbiter HEARTBEAT carries it as `oversight_recent_items`, and
  the Compliance pane gains a **DECISION HISTORY** table under the pending
  queue with the approver in a `By` column. No `OVERSIGHT_DECISION` is
  published for an auto row (that signal would re-dispatch on every agent),
  and the reward harness ignores `policy:*` approvers so a mode cannot score
  its own decisions as operator praise.
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals` 1.3.

### Changed

- **`AUTO` / `ACCEPT_EDITS` now execute a curated infuse and a spawn; only
  `ASK_PERMISSIONS` asks.** `PROPOSAL_INFUSE` leaves `_NEVER_AUTOEXEC`
  (`acc/assistant_proposal.py`); `SPAWN` and `INFUSE` join `ROUTE` in the
  `ACCEPT_EDITS` auto-execute set; `ROLE_UPDATE` (changes what a role *may
  do*) still queues below `AUTO`; `PUBLISH` and `ROLE_GAP` are unchanged. This
  deliberately reverses the Stage 1.4 decision ("INFUSE always routes through
  the Compliance pane", `7f49a9e`) and retires its dev-mode escape: the trust
  anchor is the catalog's `required_signer` verified at install, and a human
  click cannot make an unsigned pack signed — a signing-floor failure is now
  **refused** (`proposal_dispatch_failed` notice with the installer's reason),
  never queued. `decide_dispatch` keeps its `operator_mode` kwarg for
  call-site compatibility and ignores it; `allow_unsigned` stays dev-only at
  the installer. Operator direction 2026-09-02; D-011 in `docs/DECISIONS.md`;
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals`
  Phase 1.1. Tracking of auto-executed proposals as `AUTO_APPROVED` rows is
  the **Added** entry above (1.3).

### Fixed

- **Approvals reach the Prompt pane, and an approved spawn actually spawns.**
  Lighthouse 2026-09-02: the assistant proposed `@acc/redhat-sre-roles`, the
  operator typed "approved" in the Prompt pane, then approved both gates in
  Compliance — and nothing happened, in `ASK_PERMISSIONS` and in `AUTO`. Two
  independent breaks. (1) `ACCTUIApp._apply_snapshot` never listed the Prompt
  screen, so the inline GATE CARD / `/allow` / "approved"-resolves-the-gate
  path from proposal 044 B8 never received a snapshot: no card, "Clusters: 0",
  and the operator's "approved" went to the LLM as a prompt. (2) The arbiter's
  `_on_reconcile` discarded the `collective.reconcile` payload that named the
  approved role and re-read `collective.yaml` — which the agent containers do
  not mount — so desired state was always empty ("0 assigning", no error). The
  arbiter now records trigger-named slots and merges them into the spec on
  every reconcile; a bare `{}` nudge stays inert. Follow-up design (one review
  per reply in the pane, outcomes reported into the thread):
  `openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals`.

## [0.10.2] — 2026-09-01

### Fixed

- **`acc-pkg install` / `verify` now pass the sigstore bundle in keyless mode.**
  `verify_pkg()` already used `cosign verify-blob --bundle` when handed a bundle,
  but the CLI never discovered or forwarded it: `install` only defaulted
  `--signature` to `<pkg>.sig` and `verify` required it, so both handed cosign a
  detached signature in keyless mode — which cosign refuses (*"provide a key …
  or a bundle with --bundle"*). Keyless verification of a signed catalog package
  could therefore never succeed via the CLI, even though the bundle sat next to
  the `.sig` and verified fine. The CLI now auto-discovers `<pkg>.bundle` (and
  accepts an explicit `--bundle`), and the signing-floor check is satisfied by
  either a detached signature or a bundle. Keypair mode is unchanged.

## [0.10.1] — 2026-08-31

### Fixed

- **Removed references to a document the public mirror does not carry.** The
  threat model is withheld from `flg77/acc`; fourteen pointers to it survived
  elsewhere — a generated doc, its generator, a test, a module docstring and
  four CHANGELOG lines — so the promoted build carried dead links and named a
  private document repeatedly.

  Fixed here rather than patched in the curated mirror tree on purpose:
  `docs/tool-catalog.md` is generated and has a drift test asserting it matches
  its generator, so a mirror-only edit would have shipped a failing test on
  every promote. Earlier CHANGELOG entries were **de-linked, not rewritten** — a
  release note is a historical record.

  The threat model keeps its own identifiers; only the pointers into it from
  publicly-shipped files are gone.

## [0.10.0] — 2026-08-31

### Added

- **Conversational turn continuity — the wire**
  (`20260825-conversational-turn-continuity`, RP-02 Phase 1). ACC had no
  continuity anywhere: `PromptChannel` was `send()` → `receive()` over one
  TASK_ASSIGN/TASK_COMPLETE pair, and while the agent *read* `session_id`
  off the payload with a `task_id` fallback, **no channel ever set it** — so
  every prompt was a session of exactly one turn. The TUI screen had held a
  real session id all along and never sent it.

  That is a model-tier problem before it is an ergonomic one. With no
  continuity each turn must be self-contained — re-derive the intent,
  reconstruct where in the task you are, hold the remaining plan, emit a
  correct marker, all in one pass with no memory of the question you just
  asked — and ACC handed that identical shape to a frontier model and to a
  3B-class model on an edge box. `session_id` now travels on the contract
  (`PromptChannel.send`, TUI, Slack, WebGUI by inheritance) and
  [`acc.thread_continuity`](acc/thread_continuity.py) replays earlier turns
  into the user message.

  Three properties are load-bearing. **A channel names a thread; it never
  supplies one** — the replay is read from the durable tracelog, so DS-01's
  *model-visible means logged* holds by construction rather than by review;
  a client-supplied transcript would be model-visible text with no durable
  origin. **Scope is enforced at assembly**, on the same
  [`acc.memory_scope`](acc/memory_scope.py) key episodes use, so continuity
  cannot reopen the cross-requester path v0.9.0's attributed memory just
  closed; a thread whose scope does not match replays *empty* — never
  partially, and never as an error that would confirm it exists. Records
  written before this shipped carry no scope and are never replayed: a
  record that never had an owner must not acquire one by being read.

  **A guardrail-blocked turn is dropped whole, prompt included.** Replaying
  the prompt a guardrail or Cat-A refused would put it back in front of the
  model on the next turn, which is a block that lasts exactly one turn.

  Opt-in per role via `RoleDefinitionConfig.thread_continuity` (default
  `False`, set only on `roles/assistant/role.yaml`); a role without it
  assembles a byte-identical prompt to before. `ACC_THREAD_TURNS` (6) and
  `ACC_THREAD_CHARS` (4000) bound the replay and are **a stopgap, labelled
  as one in the code**: ACC still has no context compaction, so an uncapped
  thread is a context-overflow bug aimed at the smallest deployments.
  `ACC_THREAD_CONTINUITY=0` is a kill switch, not a feature gate.
  `sessions.context_for()` gains `max_chars`, trimming from the front so the
  newest turns survive.

  **Not shipped: the measurement.** RP-02's G4 — a 3B model completing a
  conversation end-to-end, *measured* — is not demonstrated. `GoldenPrompt`
  is single-turn by construction (`extra="forbid"`, one `prompt` field) and
  four runners would have to honour a multi-turn form, so the two-turn
  golden prompt is its own change. Phase 1 makes the small-model claim
  possible; it does not make it measured, and should not be cited as
  evidence for it.

- **Model-visible means logged — the prompt corpus as evidence (DS-01).**
  The audit chain recorded what the runtime *decided* (Cat-A verdict,
  guardrail violations, outcome) but never what the model was *shown* — so
  an incident could not be reconstructed and EU AI Act Art. 12 was answered
  for decisions, not inputs. [`acc.prompt_record`](acc/prompt_record.py)
  digests every ``(system, user)`` corpus under a length-prefixed framing
  and [`AuditRecord.prompt_records`](acc/audit.py) carries them, so they are
  covered by the record's `evidence_hash` and the HMAC chain — tamper-evident
  on the same terms as everything else. **Enforced at construction**, not at
  each call site: ACC instantiates a concrete LLM backend in exactly two
  places — `acc.config.build_llm_backend` (which `llm_failover.backend_for_entry`
  deliberately routes through) and `acc.cli.llm_cmd._build_llm_only` (a
  duplicate that keeps LanceDB/pymilvus out of the CLI image) — and both now
  wrap their result, with a test pinning the set so a third cannot appear
  unrecorded. This closes the three paths that previously reached a model with
  *no* audit record at all: the failover chain, memory reflection, and
  `acc-cli llm`. Prompts are tagged with their
  call path via a ContextVar, because the agent shares one backend between
  its task loop and its out-of-band reflection loop; the audit drain is
  scoped so a reflection pass cannot be attributed to the next task.
  **Digest-only by default** — full prompt text is retained only under
  `ACC_PROMPT_RECORD_FULL`, since a byte-faithful record of everything a
  model saw is also a record of everything it was given. See ACC Roadmap:
  *DS-01*.

- **Generated, CI-verified model-facing capability catalog (DS-04).**
  [`tools/gen_tool_catalog.py`](tools/gen_tool_catalog.py) **boots** the
  skill and MCP registries — importing adapters, deep-merging `_base`
  defaults, validating the Pydantic manifests — and writes
  [`docs/tool-catalog.md`](docs/tool-catalog.md). A completeness guard globs
  `skills/*/` and `mcps/*/` and fails if anything on disk did not reach the
  registry, turning `load_from()`'s deliberate log-and-drop into a red build
  (correct at runtime, dangerous at inventory time). Current surface: 55
  skills, 12 MCP servers — **5 of which carry `allowed_tools: []`**, i.e.
  their tool surface is owned by an upstream and can grow between runs with
  no ACC change; that set is now pinned by a test. See ACC Roadmap: *DS-04*,
  and the ACC threat model.

- **Adversarial threat model (OC-02).**
  [`regulatory_layer/frameworks/atlas_threat_model.yaml`](regulatory_layer/frameworks/atlas_threat_model.yaml)
  — 24 threats mapped to MITRE ATLAS and scoped to ACC's real surface (NATS
  spine, A2A federation, `.accpkg` supply chain, MCP servers, docstore
  poisoning, the oversight queue as a target, adaptive Cat-C governance,
  evidence integrity). Shipped as a *framework catalog*, so `acc.frameworks`,
  `acc.gap_analysis` and the Compliance pane carry it with no new code.
  Narrative and contribution process ship with the private spearhead corpus.
  Note when reading the pane: gap analysis only sees Rego rule summaries, so
  controls implemented in Python (DoS shield, oversight queue) read as
  uncovered — the per-threat verdict in the catalog is authoritative.
  See ACC Roadmap: *OC-02*.

- **An OpenAI-compatible endpoint, served** (`20260829-openai-compat-server`,
  HG-24). `acc/compat_endpoint.py` had decided what a completion means when the
  responder is a governed collective — 202 with a pollable handle — and had no
  socket: nothing imported it but its own tests. It is now mounted on
  `acc-webgui` as `POST /v1/chat/completions`, `GET /v1/models`, and
  `GET /v1/tasks/{task_id}` — the poll route the 202 body had been promising to
  clients that had nowhere to poll.

  **Off unless `ACC_COMPAT_API_KEYS` is configured**, and then not mounted at
  all rather than mounted-and-401: a 401 still tells a prober ACC is listening,
  and this is the surface most likely to be pointed at by something the operator
  did not write. `model` names a **role**, not a model.

  **It serves non-gated work only.** ACC gates *actions* — `capability_dispatch`
  queues before invoking a capability — while `cognitive_core` classifies an
  ordinary prompt's EU AI Act risk *after* the work, for the audit record.
  Nothing holds execution, so a HIGH-or-above role is refused with 403 rather
  than handed a 202 pointing at an oversight item nobody created.

- **A thread over the compat endpoint** — `X-ACC-Session` names the conversation
  a completion continues. Named: only the latest user message is sent, and the
  tracelog supplies the rest. Unnamed: the client's array is joined, as before.
  Never both — an OpenAI client resends everything each turn, so honouring both
  would put the same turns in front of the model twice and charge them twice
  against the context budget.

- **A default catalog** (`20260603-acc-pkg-pilot`). A stock host reported
  `catalogs: (none configured)`: all three catalog layers default to paths that
  do not exist, so a fresh install resolved against nothing.
  `acc.pkg.catalog.builtin_catalogs()` is now a fourth, broadest layer carrying
  `acc-canonical`, overridable by id from any file layer.

### Changed

- **The web surface can hold a thread** (`20260830-webgui-tui-alignment`
  Phase 1). RP-02 gave the TUI and Slack conversational continuity and the web
  never got it — every web prompt started over, forever. The channel had
  accepted `session_id` all along (`WebPromptChannel` inherits
  `TUIPromptChannel.send`); the *route* never passed one. `POST /prompt` now
  carries it, along with `operating_mode` and `workspace` — the same defect,
  found alongside it.

### Fixed

- **`cryptography` was pinned inside its own advisory.** GHSA-g6cj-pr64-35w5
  (HIGH) covers `>=44.0.0,<50.0.0` and is first patched in 50.0.0; the declared
  `>=48.0.1,<49` was both vulnerable and unable to reach the fix, so Dependabot
  could not resolve it. Now `>=50.0.1,<51`, with the Ed25519 signature suite run
  on 46, 48 and 50 in turn.

- **Container test guards skipped nothing — they exploded.** Five modules
  guarded on `subprocess.run(["podman", ...]).returncode`, which *raises* when
  podman is absent rather than returning a status, aborting collection and with
  it the whole session. The cost was the 74 container tests that need no
  container runtime at all.

- **The UBI lint read a build ARG literally**, firing on
  `Containerfile.redis`'s `FROM ${REDIS_BASE}`. The resolved base is a
  documented decision — Redis has no subscription-free RPM on RHEL 9, so the
  unentitled tier must use the community image. The rule now resolves ARG
  defaults and honours a recorded exemption keyed on the exact base.

## [0.9.0] — 2026-08-24

### Added

- **Attributed memory — who asked survives past admission**
  (`20260823-attributed-memory`, Phases 1–2). v0.8.0 resolved a requester at the
  admission boundary and then lost it one hop later: not a column on `episodes`,
  not a field on `SessionInfo`, not a property of a memory note. Retrieval
  filtered on `agent_id` alone — the right axis for one operator, the wrong one
  for two, so **two people on one surface shared an episode pool** and either
  one's prompt could retrieve the other's history.

  Episodes now carry `requester` and `scope` columns;
  [`acc.attribution`](acc/attribution.py) names the "nobody in particular"
  sentinel so a row that never had a requester can never be mistaken for one;
  [`acc.memory_scope`](acc/memory_scope.py) decides which memory an episode
  belongs to. `memory_notes` gain `source_ids` + `source_requesters` in place of
  a bare `source_count` — provenance was destroyed at distillation, so a
  contribution could not be traced back or removed, and a quorum could not tell
  ten episodes from one person apart from one episode each from ten.
  `SessionInfo` gains `owner`. `source_count` is kept and derived, so nothing
  that reads it changes behaviour.

  **The scoping mode is applied when an episode is written, not when it is
  read**, so retrieval is one equality test with no policy in it, and changing a
  surface's mode later cannot silently re-partition history. Defaults are per
  surface and deliberately not uniform — pooled for tui/webgui, **per channel**
  for Slack (a DM is not a room, and falls back to per-requester), **isolated**
  for compat/webhook/subscription, and **isolated for any surface not in the
  table**, because the next adapter added is the one most likely to be missing
  from it.

  **A single-operator deployment is unaffected**: work that never passed
  admission scopes to `local`, existing rows are backfilled to the same scope,
  and retrieval is unchanged. Existing LanceDB databases are migrated in place
  on open, with pre-existing rows backfilled as *unattributed* — never as the
  current requester.

  **Phase 3** adds the private/shared split. Notes bypass episode retrieval
  entirely, so none of the scoping above reached them: reflection clustered the
  whole recent ring and wrote the result to one per-role Redis key read on every
  prompt-build, meaning a single note could be distilled from two channels at
  once — a leak that arrives *already summarised*. Clustering now happens
  strictly within a scope, notes carry `tier` + `scope`, and the hot cache is
  per scope. Episodes from **isolated** surfaces (compat, webhook, subscription,
  and any surface not in the mode table) are never distilled, so unattended
  ingress cannot write what every future prompt reads. Reflection only ever
  writes `private`; nothing promotes to `shared` without a reviewed decision.

  **Phase 4** makes promotion a decision. A new `publish` proposal kind carries
  the note, both contexts and its quorum evidence; it is HIGH risk, never
  auto-executes in any operating mode (including AUTO, and unlike `infuse` it
  has no dev-mode escape), and its summary names both contexts so an approver
  can see the flow rather than just the text. Publication is **directed** — a
  published note is keyed on its destination and is readable exactly where a
  person put it, which is what answers "may this fragment be retrieved in that
  context?" without the per-principal ceilings that do not exist yet. The
  approving principal, which the oversight queue already had and was dropping
  before dispatch, is now recorded on the proposal and the journal entry; a
  publication with no nameable approver is **refused**.

  **Phase 5** makes promotion answer to a quorum. It counts distinct **people**
  — `Principal.attribution()` renders as `source:subject@scope`, so counting
  requester strings would let a quorum of two be met by one person talking to
  themselves in a second room. The floor is 2; an operator may override for a
  single-source note, which is then marked as such *and* with who overrode it.
  Contradictions the summariser names are recorded as **dissent** on the note
  and rendered with it, rather than averaged away. A published note serves a
  probation window before it is read on the prompt path — a chance to revoke it,
  not a fix for drift. And `memory_note_bandwidth` is now a **role field**, so
  how much distilled memory reaches a prompt goes through `ROLE_UPDATE` and is
  countersigned; the default matches the constant it replaced.

  **Phase 6** makes erasure possible. `acc-cli memory forget --person <id>`
  removes a person's episodes and reconciles every note drawn from them: a note
  with no sources left is deleted, one that falls below quorum is **demoted to
  private and pulled out of every context it was published into**, and one still
  above quorum is rebuilt without the erased sources. It **defaults to a dry
  run**. Erasure touches the memory tier only — the audit record of a request
  survives it, and the erasure leaves its own journal entry, so there is no path
  that produces a silent deletion. A vector backend that cannot erase is
  reported as *unsupported* rather than as done.

  Known limits: both retrieval filters run *after* the vector search, so
  retrieval over-fetches to protect recall — that bounds the problem rather than
  removing it, and a backend-side prefilter is the real fix. And a rebuilt note
  keeps its original summary, which was distilled from episodes that no longer
  exist; erasure removes the sources and the attribution and cannot unwrite a
  sentence already written from them. Both are recorded as follow-up work.

- **OKF knowledge packs — P5 (runtime).** A `.accpkg` can now ship curated OKF
  *content*, not just capabilities: an `AccPkgManifest.bundles` list points at
  OKF v0.1 bundles under `bundles/<name>/`. Build + install carry them like any
  other tree; [`acc.pkg.knowledge`](acc/pkg/knowledge.py) discovers installed
  bundles and indexes them into the collective document store
  (`index_installed_bundles`, idempotent per pack+collective via a marker) so
  agents **retrieve** the content — the P3 boundary then scopes it per role.
  Opt-in via the role flag `index_knowledge_packs` (bind it to ONE role per
  collective, e.g. the `okf_transformer`); default off. The **Marketplace** pane
  gains a **Bundles** column so a knowledge pack renders legibly (a role/skill
  pack shows `—`).

- **Open Knowledge Format (OKF) foundation — P0–P3.** A pure-Python
  [`acc.lib.okf`](acc/lib/okf/) toolkit for OKF v0.1 bundles: parse, three-rule
  conformance validation (tolerant of the soft failures the spec says
  consumers MUST accept), emit, and a **non-destructive** `from_obsidian`
  transform (a messy vault → a conformant *parallel* bundle: type inference,
  `[[wikilink]]` → bundle-relative markdown links, front-matter enrichment,
  generated `index.md`). Surfaced to agents as two skills: the pure **`okf`**
  conformance helper (`format` / `validate_text` / `infer_type`) — LOW-risk and
  **granted to every role by default** (format discipline, not data access) —
  and the workspace-gated **`okf_transform`** (`validate_bundle` / `query` /
  `write_concept` / `from_vault`, HIGH-risk, trust-flag enforced). P2 indexes a
  bundle into the collective document store (`acc.lib.okf.index_bundle`), stamping
  each concept's `type` / `domain` / `sensitivity` / path as tags. **P3** adds a
  governance-sourced **retrieval boundary** (`acc.docstore.RetrievalBoundary`):
  opt-in per role (`memory_domain_scoping` + `memory_sensitivity_clearance`),
  it filters RAG retrieval to concepts within the role's `domain_receptors` /
  sensitivity — a *filter over the shared corpus, not a copy*; untagged
  (non-OKF) documents stay shared, and it is **off by default** (retrieval
  byte-identical). See ACC Roadmap: *Open Knowledge Format (OKF) in ACC*.

## [0.5.17 – 0.5.49] — 2026-06-29 → 2026-07-06

> Published incrementally across the 0.5.x line; the authoritative
> per-version boundaries are the annotated git tags (`git tag`,
> `git show vX.Y.Z`) and the GitHub release notes. Latest release:
> **v0.5.49** (2026-07-06). Everything below is additive and opt-in.

### Added

- **OpenShell kernel-enforced exec sandbox (Model 2).** An opted-in agent's
  code execution (`shell_exec` / `python_exec`) is delegated into a per-agent
  [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandbox
  (Landlock + seccomp + per-binary egress) carrying the corpus's Cat-A/B/C
  policy — the operator provisions it (policy `ConfigMap` +
  `openshell sandbox create` initContainer + OIDC/SPIFFE gateway auth) and the
  runtime (`acc/sandbox`) delegates **fail-closed**. Opt-in via `spec.sandbox`
  + `gatewayURL`; default-OFF and inert otherwise; live kernel-denial smoke
  pending. Adds a `[sandbox]` extra (the `openshell` CLI) + `features/sandbox.yaml`
  + `spec.sandbox.{image,credentialsSecret}`. (v0.5.49)
- **Golden-prompt eval-history + MLflow experiments (045/G).** Run-by-`task_id`
  enrichment (tokens · compliance · verdict), golden→eval-pack promotion,
  MLflow run-logging (TUI + CLI) with RHOAI trace deep-links, and an
  edge↔DC round-trip via MLflow; the WebGUI Diagnostics screen reaches parity
  (run / history / MLflow / promote). (v0.5.25, v0.5.27–28, v0.5.35, v0.5.42)
- **Golden-prompt portability.** Golden-pack boot auto-detect, an `acc-pkg`
  golden-pack export, and a Diagnostics → Pack button. (v0.5.36–38)
- **Golden Prompt pane redesign (047).** A stacked focus-resize layout, a
  version picker + a Form editor (Title/Desc, New/Export/Save), CSV + JSON
  import/export (human + agentic interchange), and a watch-dir picker. (v0.5.39–42)
- **Global command palette (`Ctrl+P`) + navigation unification (050).** A shared
  `NavScreen` base, the palette, and a `Ctrl+A` leader for the overflow panes
  (Marketplace / Catalogs). (v0.5.44, v0.5.48)
- **Writable, cosign-verifying catalog endpoint (marketplace P0)** — publishes
  and verifies packages against the catalog's `required_signer`. (v0.5.29)
- **Signed-image release build.** The `flavour` builder gains `--signer-key` +
  cosign; **acc-deploy semantic versioning** ties image tags + the TUI banner to
  the git code release. (v0.5.30–31)
- **TUI prompt persistence + durable sessions** — sent-prompt history and
  detach/resume survive restarts (+ `acc-deploy restart`). (v0.5.34)
- **Visible role→model mapping + assistant-loop closers (044).** `models.yaml`
  `role_models` + `/model`, surfaced in Nucleus + Configuration; an inline GATE
  CARD resolves oversight from the Prompt window; the assistant continues after
  an infuse install; promoted-role model re-resolve on `ROLE_ASSIGN`; golden
  prompts gain durable export/import. (v0.5.17–24)

### Changed

- **TUI 050 layout convergence** — `%`→`fr` + min-height sizing, a single
  styling home, help coverage, and a dead-rule sweep. (v0.5.45–47)
- Diagnostics reachability + catalog-discovery UX polish (045 slices 1–2). (v0.5.33)

### Fixed

- Marketplace / Catalogs **crash-on-open** (a shared `NavScreen` base) + the
  Ecosystem `m` / `c` entries. (v0.5.43)
- Operator `AgentCorpus` **admission** — empty-OTel-endpoint backfill (G1) and
  Milvus gated on `vectorBackend` (G2). (v0.5.32, PR #147)

### Security

- Overlay `allow_unsigned` **prod-guard** + CLI roots-root alignment. (v0.5.22, PR #135)
- **Dependabot** clearance — `uv.lock` refresh + `cryptography` 48.0.1. (v0.5.26, PR #141)

## [0.3.1 – 0.5.16] — 2026-05-14 → 2026-06-28

> These changes were published incrementally across the 0.3.x and 0.5.x
> releases; the changelog was not carved per tag during that run. The
> authoritative per-version boundaries are the annotated git tags
> (`git tag`, `git show v0.5.16`) and the GitHub release notes. Latest
> release: **v0.5.16** (2026-06-28).

### Removed

- **The 43 movable roles have been extracted from `roles/` to four
  published packages (Stage 2 cutover).**  The dual-source
  `RoleLoader` now resolves them from the installed-package path —
  add `required_packages:` to your `collective.yaml` before
  upgrading:

  ```yaml
  required_packages:
    - "@acc/workspace-roles@^1.0"   # coding_agent + variants, analyst, synthesizer
    - "@acc/research-roles@^1.0"    # research_planner, research_critic, ...
    - "@acc/business-roles@^1.0"    # 25 business roles
    - "@acc/devops-roles@^1.0"      # data_engineer, ml_engineer, ...
  ```

  Packages are signed (Sigstore keyless OIDC) and served from
  `https://flg77.github.io/acc-ecosystem`.  The 7 CONTROL roles
  (arbiter, assistant, compliance_officer, ingester, observer,
  orchestrator, reviewer) stay in core — they ARE the substrate.

  Runbook: `docs/CUTOVER-PLAN.md`.  Migration guide:
  `docs/MIGRATING-FROM-INTREE.md`.  The deprecation warning that
  surfaced in the preceding release is gone — the in-tree dir no
  longer exists for movable roles, so the warning code path is
  removed too.


### Added

- **acc-webgui — optional FastAPI + React web frontend (proposal acc-webgui).**
  A new optional container: a browser frontend with feature parity to
  the terminal UI `acc-tui` plus enhanced tracing views.  Opt-in — a
  separate image + compose profile; nothing changes for existing
  deployments, and `acc-tui` is unchanged and not deprecated.

  - **PR-1** — FastAPI backend (`acc/webgui/`): `ObserverHub` reuses
    `acc.tui.client.NATSObserver` + `CollectiveSnapshot` (parity is
    structural, not a fork); WebSocket `/ws/{cid}` live push + REST
    read endpoints; `pyproject.toml` `[webgui]` extra + `acc-webgui`
    console script.
  - **PR-2** — React + Vite + TypeScript frontend (`webgui/` tree):
    app shell, collective switcher, the 8 parity screens.
  - **PR-3** — action endpoints (`infuse` / `prompt` / `oversight` /
    `test-llm`) + `acc/channels/webgui.py` `WebPromptChannel`.
  - **PR-4** — enhanced tracing: a task-step waterfall, a PLAN DAG
    view, and a tamper-evident audit-chain timeline (the audit endpoint
    re-verifies each record's `evidence_hash`).
  - **PR-5** — capability-tiered auth (`oauth-proxy` / `oidc` /
    `token`); viewer/operator RBAC; the server refuses a non-loopback
    bind when no auth is configured.
  - **PR-6** — multi-stage `Containerfile.webgui` (Node build stage
    discarded — the runtime image is Python-only); compose `webgui`
    profile; operator `acc_webgui_deployment.yaml` sample.

  The backend reuses the TUI's framework-agnostic data layer, so a new
  signal type appears in both UIs for free.  See `docs/webgui.md`.

- **Runtime-evidence Cat-A (proposal 015, Phase 3).**  A
  provider-agnostic kernel-event evidence layer for Category-A
  governance — ACC folds `execve`/`openat`/`connect` evidence (what an
  agent process *actually did*) into Cat-A, alongside the existing
  metadata evaluation.  Opt-in via `governance.runtimeEvidence.enabled`
  (default `false`); observe-by-default.

  - **PR-1** — `RuntimeEvidenceSpec` on `GovernanceSpec`; status
    `runtimeEvidence` + `RuntimeEvidenceReady` condition;
    `PrerequisiteStatus` gains `rhacsInstalled` / `falcoInstalled` /
    `tetragonInstalled` / `netobservInstalled`; resource-level
    `HasAPIResource` detection (Tetragon's `TracingPolicy` is detected
    by *kind*, not the `cilium.io` group it shares with the CNI).
  - **PR-2** — new `acc-runtime-evidence-bridge` image
    (`acc/runtime_evidence_bridge.py`) with an adapter framework + the
    Tetragon and NetObserv adapters; operator
    `RuntimeEvidenceBridgeReconciler`.
  - **PR-3** — RHACS (Red Hat-preferred) and Falco adapters; backend
    auto-selection (RHACS > Falco > Tetragon).
  - **PR-4** — `KERNEL_EVENT` NATS signal; `CognitiveCore` subscribes,
    correlates events to its own pod, and folds them into Cat-A via
    the new `KernelEventEvaluator`; downward-API pod identity on agent
    pods.
  - **PR-5** — `regulatory_layer/category_a/kernel_events.rego`
    (K-001/K-002/K-003) + recommended Tetragon/Falco rule samples.

  Provider-agnostic: ACC consumes whichever runtime-security tool the
  cluster runs and never installs one.  See `docs/runtime-evidence.md`
  and proposal 015.

- **L7 / eBPF NetworkPolicy (proposal 014, Phase 1).**  An opt-in,
  capability-tiered network-isolation layer for ACC agent pods,
  delivered entirely operator-side (no Python runtime change).
  Opt-in via `spec.networkPolicy.enabled` (default `false`) — upgrading
  the operator never drops traffic.

  - **PR-1/PR-2** — `NetworkPolicySpec` on `AgentCorpusSpec`; status
    `networkPolicy` block + `NetworkPolicyReady` condition;
    `PrerequisiteStatus` gains `ciliumInstalled` /
    `ovnEgressFirewallSupported`; `APIGroupChecker` detects `cilium.io`
    and `k8s.ovn.org` plus a CNI-enforcement heuristic.
  - **PR-3** — new `NetworkPolicyReconciler` (`internal/reconcilers/
    security/`); Tier 1 = default-deny + DNS + same-namespace +
    coarse external-HTTPS standard `NetworkPolicy` objects for agent
    pods.  Honest about K3s/Flannel (emits objects, reports
    `CNIDoesNotEnforce`).
  - **PR-4** — Tier 2 FQDN egress via OVN `EgressFirewall` or Cilium
    `CiliumNetworkPolicy` (auto-selected, emitted as unstructured
    objects — no heavy CRD-type vendoring).
  - **PR-5** — Tier 3 Cilium L7 (HTTP-method-scoped egress); `mode:
    audit` emits the policy set without the default-deny as a safe
    canary.

  Cilium is **not** a prerequisite — Tier 1 standard `NetworkPolicy`
  is the portable must-have; Tiers 2/3 auto-negotiate from detected
  cluster capability.  See `docs/network-policy.md` and proposal 014.

- **NATS NKey authentication (proposal 013, Phase 0c).**  Per-role
  NKey identities + a server-enforced publish/subscribe permission
  matrix for the NATS bus, integrating into all three deploy modes
  (standalone / edge / rhoai).  Opt-in via `security.nkey.enabled`
  (default `false`) — with the switch off, every NATS connection is
  byte-for-byte unchanged.

  - **PR-1** — split the shared `acc.{cid}.task` subject into
    `acc.{cid}.task.assign` (TASK_ASSIGN) and `acc.{cid}.task.complete`
    (TASK_COMPLETE) so the permission matrix can grant "assign work"
    and "report completion" independently per role.  `subject_task()`
    is retained for one release as a deprecated alias.
  - **PR-2** — new `NKeyConfig` model under `SecurityConfig`
    (`ACC_NKEY_*` env overrides); canonical `acc/nats_permissions.yaml`
    permission matrix consumed by both the operator's Go renderer and
    the Python CLI; `acc/nats_permissions.py` loader; contract test
    `tests/test_nats_permissions.py` fails CI if a subject in
    `acc/signals.py` is left uncovered.  TUI Configuration screen
    surfaces the resolved `nkey.enabled` / `nkey.role`.
  - **PR-5** — `NATSBackend` and the TUI `NATSObserver` thread an
    NKey seed into `nats.connect()` when enabled (fail closed on a
    missing seed, never silently anonymous); new `acc/nkeys.py`
    (pure-Python Ed25519 NKey generation + `nats.conf` authorization
    rendering) and the `scripts/acc-nkeys` CLI (`generate` /
    `render-conf`) for standalone mode; `podman-compose.yml` gains an
    opt-in `nats.conf` mount.

  Eight identities: the six agent roles plus a read-only `tui`
  surface and an edge `leaf`-node link.  See `docs/nats-nkeys.md`.

- **Edge SPIFFE guide + cross-mode compatibility e2e (proposal 012
  PR-4).**  Closes proposal 012.  New `docs/spiffe-edge.md` — the
  edge SPIFFE guide: the topology decision tree (nested / federated
  / ed25519), per-topology config, offline survival + the
  `offline_action` table, the ed25519→spiffe migration path,
  troubleshooting, and the six-direction bi-directional
  compatibility matrix.  `docs/howto-edge.md` gains a SPIFFE
  optional section; `docs/spiffe.md` gains an edge-interoperability
  section.

  New `tests/integration/test_spiffe_edge_e2e.py` — a crypto-level
  e2e that models each trust topology with synthetic SPIRE
  keypairs, mints JWT-SVIDs as a SPIRE workload API would, and
  verifies them through the production `acc.spiffe_verify` path.
  13 tests cover all six matrix directions (rhoai↔rhoai,
  rhoai↔edge, edge↔rhoai, edge↔edge same/different nested sites,
  edge↔edge federated), the federation negative control (cross-
  trust fails without a bundle exchange), strict subject binding,
  and the offline partition→degrade/rotate→recovery timeline.
  No cluster required — runs unattended in CI.

  Proposal 012 status with this PR: all four PRs landed.  Edge
  SPIFFE ships as opt-in; edge stays on `ed25519` by default.

- **SPIFFE operator guide + v0.5.0 default-flip plan (proposal 011
  PR-5).**  Closes proposal 011.  New `docs/spiffe.md` — the
  operator-facing guide for SPIFFE workload identity: prerequisites
  (SPIRE / spire-controller-manager / SPIFFE CSI driver), the
  `security.signing_mode` + `security.spiffe.*` config surface,
  trust-domain naming, the three-stage migration
  (`ed25519` → `spiffe`+fallback → `spiffe` strict), an end-to-end
  flow diagram, verification commands, and a troubleshooting table.
  `docs/howto-rhoai.md` gains a SPIFFE optional-prerequisite entry;
  `docs/role-sync.md` cross-links it.

  **Planned for v0.5.0**: the `rhoai` row of
  `_SIGNING_MODE_BY_DEPLOY_MODE` flips from `ed25519` to `spiffe`,
  so a fresh rhoai operator gets SPIFFE by default
  (`allow_ed25519_fallback` still defaults true, so the flip is
  safe).  `standalone` + `edge` stay on `ed25519`.  Operators pin
  `signing_mode: ed25519` explicitly to opt out.

- **Edge federation + configurable offline action (proposal 012
  PR-3).**  Completes the edge SPIFFE story: cross-trust between
  federated edge sites, and a configurable response to a partitioned
  (stale) trust bundle.

  - `SpiffeSpec` gains `federationPeers []string`.  When
    `edgeTopology: federated`, `SpiffeReconciler` issues one
    `ClusterFederatedTrustDomain` custom resource per peer so this
    edge's SPIRE trusts SVIDs from the peer trust domains.  Peer
    entries are `<trust-domain>@<bundle-endpoint-url>` pairs;
    malformed entries are skipped + surfaced in `status.spiffeError`
    (one bad peer doesn't block the others).  Operator RBAC gains
    `clusterfederatedtrustdomains`.
  - New `acc/spiffe_offline.py` — `OfflineBundleMonitor` watches the
    SPIRE trust-bundle file age and, when it crosses
    `offline_max_age_h`, applies the configured `offline_action`:
    `rotate` (keep serving — the edge SPIRE rotates), `degrade`
    (read-only), or `shutdown` (fail-safe exit).  It is a building
    block — `check()` classifies + `start()` runs a poll loop that
    emits an `acc.spiffe.offline` audit event and invokes a handler;
    the agent bootstrap wires the handler that performs the actual
    degrade/shutdown (same module-then-wire-up split as proposal
    010's `RoleSyncListener`).
  - New `deploy/edge-spire/federation-peer.yaml.example` +
    expanded `README.md` (federated-topology install runbook +
    the `offline_action` behaviour table).
  - 31 new tests — 15 Go in `spiffe_federation_test.go` /
    extensions (per-peer CR issuance, no-peers + malformed-peer
    error paths, nested-ignores-federation) + 16 Python in
    `tests/test_spiffe_offline.py` (freshness classification,
    missing-bundle, all three actions, event publication, poll
    loop with handler).

  Inert by design — no behaviour change until an operator sets
  `deployMode: edge` + `edgeTopology: federated` (or wires the
  offline monitor).  012 PR-4 (docs + cross-mode e2e) closes the
  proposal.

- **Agent-side SPIFFE JWT-SVID verification (proposal 011 PR-4).**
  When `security.signing_mode` is `spiffe`, a ROLE_UPDATE carries the
  arbiter's JWT-SVID in its `signature` field; the agent verifies it
  against the SPIRE trust bundle the `spiffe-helper` sidecar writes
  to disk.

  - New `acc/spiffe_verify.py` — `verify_jwt_svid()` checks signature
    (against the JWKS bundle), `aud`, `exp`, optionally `sub`;
    `SpiffeVerifier` re-reads the bundle each call so SPIRE bundle
    rotation is honoured without an agent restart.  `PyJWT` is a new
    dependency (small, pure-Python, reuses the existing
    `cryptography` dep).
  - `acc/role_store.py` — `apply_update` dispatches verification
    through `_verify_signature`, switching on `signing_mode`:
    `spiffe` → `_verify_spiffe`, `ed25519` → the existing path.
    When `security.spiffe.allow_ed25519_fallback` is true a SPIFFE
    failure degrades to the Ed25519 path — a transient SPIRE problem
    doesn't strand the collective during the migration window.
  - New `security.spiffe.arbiter_spiffe_id` config field
    (`ACC_SPIFFE_ARBITER_ID` env) — when set, the verifier enforces
    the JWT `sub` claim; when blank, arbiter identity rests on the
    existing `approver_id` check.
  - `RenderSpiffeHelperConfig` (operator) emits `jwt_bundle_file_name`
    so spiffe-helper writes the JWKS bundle the verifier needs.
  - 25 new tests — 20 in `tests/test_spiffe_verify.py`, 5 in
    `tests/test_role_store.py::TestApplyUpdateSpiffe`.

  A JWT-SVID attests arbiter identity + audience, not ROLE_UPDATE
  content integrity (that stays the `approver_id` + role-version
  checks) — see the `acc/spiffe_verify.py` module docstring.

- **Nested SPIRE topology + edge-qualified SPIFFE IDs (proposal 012
  PR-2).**  Extends the operator-side SPIFFE provisioning to edge
  deployments.

  - `SpiffeSpec` gains `edgeTopology` (`nested | federated | ed25519`,
    default `nested`) + `edgeSiteID`.  `AgentCollectiveStatus` gains
    `edgeSiteID`.  CRD bases hand-updated to match.
  - `SpiffeReconciler` now site-qualifies the SPIFFE ID when the
    owning `AgentCorpus` has `deployMode: edge` **and**
    `spiffe.edgeTopology: nested`:
    `spiffe://<trust-domain>/edge/<site-id>/role/<collective>`.
    Federated / ed25519 topologies and all non-edge deploy modes keep
    the flat `spiffe://<trust-domain>/role/<collective>` form.
    `nested` without an `edgeSiteID` reports a config error via
    `status.spiffeError` rather than failing reconciliation.
  - New `deploy/edge-spire/` manifests: `nested-spire-server.values.yaml`
    (Helm values overlay for the upstream `spiffe/spire` chart in
    nested mode), `edge-bundle-fetcher.yaml` (PVC + CronJob caching
    the parent trust bundle for offline survival), and a `README.md`
    install runbook.
  - 5 unit tests in `operator/test/unit/spiffe_edge_test.go` —
    site-qualified ID, missing-site-id error, federated plain ID,
    non-edge topology ignored, ed25519 topology plain ID.

  Inert by design — no behaviour change until an operator sets
  `deployMode: edge` + `spec.spiffe`.  012 PR-3 adds federation +
  the configurable offline action.

- **`spiffe-helper` sidecar injection (proposal 011 PR-3).**  When an
  `AgentCollective` has `spec.spiffe.enabled: true`, every agent pod
  gains a `spiffe-helper` sidecar that materialises the pod's
  X.509-SVID + JWT-SVID into a shared `emptyDir`.  The agent container
  reads the SVID files from there — no agent code change beyond the
  env vars the operator now sets.

  - New `operator/internal/reconcilers/collective/spiffe_sidecar.go`:
    `ApplySpiffeSidecar` mutates a built agent `Deployment` to add the
    sidecar + three volumes (`spiffe-svids` emptyDir,
    `spiffe-workload-api` CSI volume for the SPIRE Workload API socket,
    `spiffe-helper-config` ConfigMap) + the
    `spiffe.io/spire-managed-identity` pod annotation.  No-op when
    SPIFFE is disabled.
  - `RenderSpiffeHelperConfig` produces the `helper.conf` HCL;
    `AgentDeploymentReconciler` upserts it as a per-collective
    ConfigMap (`<collective>-spiffe-helper`).
  - Agent container gets a read-only `/run/spire/sockets` mount plus
    `ACC_SPIFFE_SVID_MOUNT_PATH`, `ACC_SVID_X509_PATH`,
    `ACC_SVID_JWT_PATH` env vars.
  - 6 unit tests in `operator/test/unit/spiffe_sidecar_test.go`.

  Inert by design — pods are unchanged until an operator sets
  `spec.spiffe.enabled`.  PR-4 wires the agent-side verifier that
  consumes these SVID files.

- **Operator-side SPIFFE provisioning — `ClusterSPIFFEID` issuance
  (proposal 011 PR-2).**  When an `AgentCollective` carries
  `spec.spiffe.enabled: true`, the operator issues a matching
  `ClusterSPIFFEID` custom resource so spire-controller-manager
  attests the collective's agent pods.

  - New `SpiffeSpec` on `AgentCollectiveSpec` (`enabled`,
    `trustDomain`) + three status fields (`spiffeID`,
    `spiffeIssued`, `spiffeError`).  CRD bases + deepcopy
    hand-updated to match.
  - New `SpireInstalled` prerequisite — `PrerequisiteReconciler`
    detects the `spire.spiffe.io` API group via the new
    `APIGroupChecker.SpireInstalled()` helper.
  - New `collective.SpiffeReconciler` issues / updates one
    `ClusterSPIFFEID` per SPIFFE-enabled collective.  SPIFFE ID
    format `spiffe://<trust-domain>/role/<collective-name>`;
    trust domain defaults to `<corpus>.acc.local` when blank;
    `podSelector` targets pods by the `acc.io/collective` label.
  - Strict no-op when `spec.spiffe` is absent/disabled or when
    spire-controller-manager is not installed — the latter
    surfaces a reason in `status.spiffeError` rather than
    failing reconciliation.  SPIFFE stays opt-in.
  - RBAC: operator ClusterRole gains
    `spire.spiffe.io/clusterspiffeids` (full verbs).
  - 7 unit tests in `operator/test/unit/spiffe_reconciler_test.go`.

  Inert by design — no `AgentCollective` carries `spec.spiffe`
  until an operator opts in.  PR-3 mounts the `spiffe-helper`
  sidecar; PR-4 wires the agent-side verifier.

- **`security.spiffe` edge fields + cross-field validators
  (proposal 012 PR-1).**  Extends proposal 011's `SpiffeConfig`
  with 11 fields covering the edge-deployment topology, offline
  survival, and NATS-mTLS fallback (Q1–Q6 resolutions from
  proposal 012 §8).  Inert by design — every existing deployment
  still defaults to `signing_mode: ed25519` so the new fields are
  ignored.

  New fields on `SpiffeConfig`:

  | Field | Type | Default |
  |---|---|---|
  | `edge_topology` | `nested \| federated \| ed25519` | `nested` |
  | `edge_site_id` | str | `""` |
  | `parent_spire_url` | str | `""` |
  | `federation_peers` | list[str] | `[]` |
  | `offline_bundle_cache_path` | str | `/run/spire/cache/bundle.pem` |
  | `offline_max_age_h` | float | `72.0` |
  | `bundle_refresh_h` | float | `6.0` |
  | `offline_action` | `rotate \| degrade \| shutdown` | `rotate` |
  | `parent_unreachable_action` | `block \| degrade` | `degrade` |
  | `nats_mtls_cert_path` | str | `""` |
  | `nats_mtls_key_path` | str | `""` |

  New `ACCConfig._validate_edge_spiffe_fields` model validator
  enforces topology-specific requirements **only when
  `deploy_mode: edge` AND SPIFFE is enabled AND `signing_mode: spiffe`**:

  - `edge_topology: nested` requires `parent_spire_url` +
    `edge_site_id` (Q5 resolution: operator-supplied,
    consistency with `trust_domain` + `parent_spire_url`).
  - `edge_topology: federated` requires ≥ 1 `federation_peers` entry.
  - `offline_action: rotate` requires `edge_topology: nested`
    (rotation needs a local SPIRE server).

  Non-edge deploy modes ignore the edge fields entirely.
  Operators who run `deploy_mode: edge` with `signing_mode: ed25519`
  also skip the topology checks — SPIFFE-aware fields stay
  advisory until SPIFFE is actually consumed.

  Nine new env-var overrides for the 9 string/scalar edge fields
  (`ACC_SPIFFE_EDGE_TOPOLOGY`, `ACC_SPIFFE_EDGE_SITE_ID`,
  `ACC_SPIFFE_PARENT_URL`, `ACC_SPIFFE_OFFLINE_MAX_AGE_H`,
  `ACC_SPIFFE_BUNDLE_REFRESH_H`, `ACC_SPIFFE_OFFLINE_ACTION`,
  `ACC_SPIFFE_PARENT_UNREACHABLE_ACTION`,
  `ACC_NATS_MTLS_CERT_PATH`, `ACC_NATS_MTLS_KEY_PATH`).
  `federation_peers` (list) stays YAML-only since `_apply_env`
  writes scalar strings.

  19 new tests in `tests/test_config.py::TestSpiffeEdgeDefaults`
  covering defaults, every cross-field validator path, non-edge
  topology skip, all three `edge_topology` happy paths, env-var
  roundtrip on every scalar override, and invalid-value rejection
  for both new `Literal` types.

- **`security.signing_mode` + `security.spiffe` config surface
  (proposal 011 PR-1).**  Foundational PR for SPIFFE workload
  identity.  Inert by design — every existing deployment sees
  zero behaviour change because every `deploy_mode` still defaults
  to `signing_mode: ed25519` in v0.4.x.

  New fields under `security:` in `acc-config.yaml`:

  | Field | Type | Default |
  |---|---|---|
  | `signing_mode` | `ed25519 \| spiffe \| auto` | `auto` |
  | `spiffe.enabled` | bool | `false` |
  | `spiffe.trust_domain` | str | `""` |
  | `spiffe.svid_mount_path` | str | `/run/spire/sockets` |
  | `spiffe.jwt_audience` | str | `acc-role-update` |
  | `spiffe.allow_ed25519_fallback` | bool | `true` |

  `signing_mode: auto` resolves to a per-`deploy_mode` default via
  `_SIGNING_MODE_BY_DEPLOY_MODE` (mirrors proposal 010's
  `_ROLE_SOURCE_BY_DEPLOY_MODE` pattern).  Resolver entry exists
  for every deploy_mode in v0.4.x.  v0.5.0 flips the `rhoai` row
  from `ed25519` → `spiffe` once 011 PR-2..PR-5 land.

  Six env-var overrides: `ACC_SIGNING_MODE`, `ACC_SPIFFE_ENABLED`,
  `ACC_SPIFFE_TRUST_DOMAIN`, `ACC_SPIFFE_SVID_MOUNT_PATH`,
  `ACC_SPIFFE_JWT_AUDIENCE`, `ACC_SPIFFE_ALLOW_ED25519_FALLBACK`.

  TUI Configuration screen surfaces the resolved values read-only
  ("Signing mode: ed25519 (spiffe.enabled=no; proposal 011)").
  Existing `arbiter_verify_key` (the legacy Ed25519 static key)
  is untouched and remains fully functional.

  15 new tests in `tests/test_config.py::TestSpiffeDefaults`
  covering defaults, per-deploy-mode resolution, explicit
  overrides, env-var roundtrip, invalid-value rejection, and a
  meta-test that fails if a future `deploy_mode` is added
  without updating `_SIGNING_MODE_BY_DEPLOY_MODE`.

- **Proposal 010 wire-up — projector ↔ detector ↔ listener ↔ TUI badge.**
  Connects the building blocks that landed inert in PR-3 / PR-4 / PR-5
  so they actually fire end-to-end:

  - `RoleCRDProjector.__init__` gains an optional
    `conflict_detector` kwarg.  When supplied, every successful
    `project_one()` calls `detector.record_our_write(role_id, body)`,
    so subsequent file-watcher events for that role classify as
    `echo` (within `conflict_window_s` with matching content) instead
    of false-positive conflicts.  Detector exceptions are caught + logged
    so a misbehaving detector never breaks the projector's hot loop.

  - `ACCTUIApp` instantiates a single shared `RoleSyncListener`
    (`app._role_sync_listener`) and subscribes the first connected
    NATS client to `acc.role.sync.>`.  Every received event routes
    through `listener.handle_event` and broadcasts a `_RoleSyncEvent`
    message so screens re-render without polling.  Subscription
    failures log + degrade gracefully (badge stays empty).

  - `EcosystemScreen` gains a `#role-sync-badge` Static widget at the
    top of the role detail panel.  Refreshes on row select and on
    every `_RoleSyncEvent` broadcast.  Three rendering tiers from
    PR-5's `render_badge()`: fresh conflict (red), aged conflict
    (dim), applied-only (dim), missing-state (empty / hidden).

  - 5 new integration tests in `tests/test_role_sync_wireup.py`
    cover projector→detector recording, projector backwards-compat
    when no detector is supplied, detector exception isolation, and
    a detector→listener round-trip that proves the on-wire JSON
    payload is the same on both sides of the NATS hop.

  This is the closing piece of proposal 010 — bi-directional file ↔
  CRD sync is now operator-observable end-to-end.

- **TUI role-sync listener + comprehensive docs (proposal 010 PR-5).**
  Closes proposal 010 — the role-sync feature is now operator-facing.

  - `acc/tui/role_sync_listener.py` — subscribes to
    `acc.role.sync.>`, maintains a per-role `RoleSyncState` (last
    conflict, last applied, counters), and exposes `render_badge()`
    returning Rich-markup for a Static widget.  Three rendering
    tiers: fresh conflict (red, within `badge_window_s` = 5 min),
    aged conflict (dim), applied-only (dim).  15 unit tests in
    `tests/test_role_sync_listener.py`.
  - `docs/role-sync.md` — comprehensive operator-facing doc
    covering the three modes (`files | crd | mirror`), defaults per
    `deploy_mode`, conflict-window semantics + sequence diagram for
    echo handling in mirror mode, plus a runbook for switching
    modes safely.

  Wiring the listener into the existing TUI NATS subscription
  (which is collective-scoped, but role-sync events are global) is
  the only deferred piece — handled by a tiny follow-up PR.  The
  listener itself is inert today and ready to consume events as
  soon as it's connected.

- **Mirror-mode conflict detection + NATS events (proposal 010 PR-4).**
  New module `acc.role_sync_conflict.ConflictDetector` classifies every
  file-watcher event as **echo** (our own CRD-driven write coming back
  through the watcher), **applied** (genuine operator edit propagating
  forward), or **conflict** (concurrent file + CRD write within the
  `conflict_window_s` window).  Conflicts publish on
  `<events_subject>.conflict` carrying enough payload (winner/loser
  source, loser snippet, RFC3339 timestamp) for an audit log + the
  PR-5 TUI badge.

  - Last-writer-wins semantics — no three-way merge.  Operators see
    the conflict event; correction is the next edit.
  - Time source injectable (`now=` kwarg) so unit tests drive the
    window deterministically without `time.sleep`.
  - NATS publisher injectable — production wires
    `acc.backends.signaling_nats`; tests inject a recording fake.
  - Counters (`applied_count`, `echo_count`, `conflict_count`)
    exposed for future `/metrics` integration.

  12 new unit tests in `tests/test_role_sync_conflict.py` cover all
  three classification outcomes, per-role isolation, counter
  increments, publisher absence + exception swallowing, and subject
  normalisation.

  Wiring into `RoleCRDProjector` is intentionally deferred to a
  separate small PR so the classifier can land + be reviewed in
  isolation.  The detector is currently inert in production builds.

- **Agent-side CRD → file projection (proposal 010 PR-3).**  The Python
  mirror of PR-2's Go-side watcher.  When `role_sync.role_source` is
  `crd` or `mirror`, the new `acc.role_crd_loader.RoleCRDProjector`
  polls the Kubernetes API for `AgentCollective` resources and writes
  their `spec.roleDefinition` block to `roles/<id>/role.yaml`.  The
  existing `acc.role_loader.RoleLoader` file watcher then picks up the
  write naturally — no new code path inside the agent's hot loop.

  - New module `acc/role_crd_loader.py` (~330 LOC):
    - `CRDClient` Protocol so tests can supply a fake without a real
      cluster.
    - `KubernetesCRDClient` lazy-imports `kubernetes` only when
      `role_source` requires it — agents in the `files` default mode
      don't pay the dependency cost.
    - `RoleCRDProjector` polls every `poll_interval_s` (default 30),
      writes files atomically (`*.tmp` + `os.replace`), and is fully
      idempotent: in-memory cache + on-disk content check both
      short-circuit no-op rewrites.
    - Generated files carry a sentinel comment naming the source CRD
      so operators can `cat` and understand the origin.
  - 19 new unit tests in `tests/test_role_crd_loader.py` covering
    sentinel-strip, idempotency, atomic writes, exception swallowing,
    polling lifecycle, and field-translation.

  Production `KubernetesCRDClient` exercised only by integration
  tests on acc1 (no live-cluster requirement in CI).

- **Operator-side file → CRD projection (proposal 010 PR-2).**  When
  the operator binary is started with `--role-source files` (or
  `mirror`), it watches `<roles-root>/<id>/role.yaml` on disk and
  patches the matching `AgentCollective.spec.roleDefinition` whenever
  the file changes.  Default behaviour is unchanged: `--role-source`
  defaults to `crd` so existing deployments see no difference.

  - New `operator/internal/filewatch/` package: `Watcher` wraps
    `fsnotify` with debouncing (500 ms default — collapses
    editor write-rename storms); `ParseRoleFile` reads the on-disk
    snake_case YAML and translates to the camelCase CRD shape;
    `RoleDefinitionsEqual` short-circuits no-op patches.
  - `AgentCollectiveReconciler` gains `RoleSource`, `RolesRoot`,
    `Namespace` fields and a public `ProjectRoleFile(ctx, roleID)`
    method called by the file-watcher goroutine.
  - `operator/cmd/main.go` adds `--role-source`, `--roles-root`,
    `--role-sync-namespace` flags (each fall back to the matching
    `ACC_*` env var) and registers the watcher as a `manager.Runnable`
    so it joins the manager's start/stop lifecycle.
  - CR patches are tagged with annotation
    `acc.io/role-sync-source: file-mirror@<RFC3339-ts>` so observers
    can attribute the change.  PR-4's conflict detector will use this.
  - On operator startup the watcher does a one-shot sweep of every
    existing `<id>/role.yaml` so CR state catches up to whatever was
    edited while the operator was down.

  PR-3 (CRD → file projection) and PR-4 (mirror-mode conflict
  events) build on this foundation.

- **`role_sync` config section + `role_source` flag (proposal 010
  PR-1).**  New top-level `role_sync:` block in `acc-config.yaml`
  with three fields:

  | Field | Type | Default |
  |---|---|---|
  | `role_source` | `files \| crd \| mirror \| auto` | `auto` |
  | `conflict_window_s` | float | `2.0` |
  | `events_subject` | str | `acc.role.sync` |

  When `role_source` is `auto` (the default) it resolves at
  validation time to a per-`deploy_mode` value:

  | `deploy_mode` | resolved `role_source` |
  |---|---|
  | `standalone` | `files` |
  | `edge` | `mirror` |
  | `rhoai` | `crd` |

  Environment overrides: `ACC_ROLE_SOURCE`,
  `ACC_ROLE_SYNC_CONFLICT_WINDOW_S`,
  `ACC_ROLE_SYNC_EVENTS_SUBJECT`.

  **PR-1 is inert** — no behaviour change in the operator
  reconciler or `role_loader`.  This PR only lands the flag and
  its resolution so PR-2/PR-3/PR-4 can switch on it.  The TUI's
  Configuration screen surfaces the resolved value read-only
  (`Role sync: files (deploy_mode=standalone; proposal 010)`).

### Fixed

- **TUI repo-root discovery for pip-installed acc-tui.**  The
  Ecosystem + Configuration screens silently rendered empty
  Role / Skills / MCPs tables when ``acc-tui`` was run from
  outside the repo with no env vars set — the operator's actual
  failure mode from ``ACC TUI / ACC REVIEW 14.5.md``.

  ``acc/tui/path_resolution.py`` gains a new discovery tier
  between the module-anchored fallback and the cwd fallback:

  1. ``$ACC_REPO_ROOT`` env var (new) — if set, the directory's
     ``roles/`` / ``skills/`` / ``mcps/`` are used.
  2. Cwd walk-up — up to 8 ancestors are scanned for an
     ``acc-deploy.sh`` marker (or ``pyproject.toml`` + an ``acc/``
     subdirectory).  An operator who ``cd``'s anywhere inside
     their checkout gets the repo's manifests surfaced
     automatically.

  Existing env-var-per-dir (``ACC_ROLES_ROOT`` etc.) and
  module-anchored paths still take precedence, so nothing breaks
  in development or container layouts.

- **Empty-roles diagnostic on the Ecosystem screen.**  When the
  resolver can't find any roles (all four tiers miss), the
  screen now surfaces an operator-facing warning notify listing
  the env-var options + the walk-up convention, instead of
  silently rendering an empty table.

### Added

- **`tests/test_tui_user_experience.py`** — 19 UX-flow tests
  that exercise the operator's actual workflow against the
  repo's real ``roles/`` / ``skills/`` / ``mcps/`` (not synthetic
  ``tmp_path`` fixtures).  Covers all six issues from
  ``ACC REVIEW 14.5.md``: roles load, row-highlight populates
  detail, Schedule infusion button arms and fires, Edit
  role.yaml / role.md buttons invoke spawn, Skills + MCPs
  surface on Configuration, LLM Endpoints documents the config
  path.  Plus four new tests pinning the repo-discovery fix
  (env-var override, cwd walk-up, graceful fallback, typo'd
  env-var tolerance).

## [0.3.0] — 2026-05-14 — Slots 004 → 009 (operator-requested follow-ups)

### Added

- **`parent_role: str | None` on RoleDefinitionConfig** — proposal
  004.  First-class subrole hierarchy.  Default `None` keeps every
  existing role working with no migration.
- **Migrated `coding_agent_*` roles** declare
  `parent_role: coding_agent`.  Research roles stay flat
  (no top-level `research` parent).
- **Ecosystem subrole listing prefers declared parent_role.**  Two-
  pass lookup in `_subrole_siblings`: declared (scans every
  role.yaml's `parent_role`) → falls back to directory-name glob
  for unmigrated roles.  Markdown section label flips between
  "Subroles (declared)" and "Subroles (directory-derived)" so
  operators see which surface populated the list.
- **`acc/scheduler` package** — `Schedule` dataclass +
  `ScheduleStore` (YAML round-trip) + `next_fire_time` cron
  evaluator (subset: `* * * * *`, `*/N * * * *`, `M * * * *`,
  `0 H * * *`, `M H * * *`).  Proposal 005.
- **`acc-cli schedule` subcommand group** — `add` / `list` /
  `remove` / `run-once`.  Run-once is the daemon entry-point;
  operator wires it into cron / systemd-timer / Windows Task
  Scheduler.  Fires due schedules as TASK_ASSIGN signals on
  `acc.{cid}.task` with `from_agent=acc-scheduler`,
  `task_type=SCHEDULED`, `plan_id=schedule-<name>`.
- **`schedules/_example.yaml`** + `.gitignore` entry for
  `schedules/*.yaml` (operator-local schedules stay out of git;
  `_example.yaml` ships in-repo as a template).
- **`docs/role-authoring.md`** — boundary doc codifying the
  proposal 003 §10 memo: role.md owns narrative, role.yaml owns
  identity + defaults, Nucleus owns per-infusion deltas, Prompt
  owns task content only.  Proposal 006.
- **`acc-cli role audit <name>`** — content-drift linter.
  Codes LINT001 (yaml missing) → LINT005 (md H1 unrelated to
  yaml purpose).  Warnings-only by default; `--strict` exits 1.
  Heuristic substring-match for shared morphology (`research`
  matches `researcher`).
- **TUI Infuse form parity with CLI** — proposal 008.  The
  TUI's `action_apply` now loads the selected role's full
  `RoleDefinitionConfig.model_dump()` from disk and overlays the
  9 form fields, so the published `role_definition` is a
  superset of the CLI's wire shape (previously the TUI dropped
  ~6 fields).  `category_b_overrides` preserves disk-only keys
  and overlays only `token_budget` + `rate_limit_rpm`.  Closes
  the known parity gap noted in v0.2.0.
- **TUI Ecosystem: "Edit role.yaml" + "Edit role.md" buttons.**
  Proposal 007.  Spawns the operator's `$EDITOR` (resolved via
  env var with `$VISUAL` + platform fallback) on the selected
  role's files.  Non-blocking `Popen`; file-watcher from
  proposal 003 PR-3 catches the save and refreshes the detail
  pane.  Missing `role.md` is auto-created with a stub
  template + pointer at `docs/role-authoring.md`.

### Removed

- **TUI Ecosystem: Skills + MCPs + Active LLM Backends widgets.**
  Proposal 009.  These three tables (kept on Ecosystem for one
  release as a back-compat migration aid in proposal 003 PR-4)
  are removed.  Canonical home is the Configuration pane
  (pane 8) since v0.2.0.  Upload buttons (`Upload skill` /
  `Upload MCP`) move along with them.  Tests targeting the
  removed widgets are deleted; coverage lives in
  `tests/test_configuration_screen_pilot.py`.

## [0.2.0] — 2026-05-14 — TUI usability hardening (proposal 003)

Closes proposal 003 (operator's Obsidian vault — `ACC
Implementation/003 - ACC TUI usability hardening.md`).  Six PRs
landed on main between 2026-05-13 and 2026-05-14: #54 (PR-1),
#55 (PR-2), #56 (PR-3), #57 (PR-4), #58 (PR-5), #59 (PR-6).

### Added

- **TUI Ecosystem: `role.md` narrative surface.**  The role detail
  panel now reads `roles/<name>/role.md` alongside `role.yaml` and
  renders it in a `Markdown` widget at the top of a two-section
  collapsible.  The raw yaml is preserved under a second
  collapsible (closed by default).  Roles without a `role.md`
  show a friendly placeholder pointing operators at the
  forthcoming authoring guideline (slot 006).  (PR-2 of proposal
  003 — PR #55.)
- **TUI Ecosystem: role search filter.**  An `Input` widget above
  the ROLE LIBRARY DataTable narrows the visible rows by
  case-insensitive substring match against name / domain /
  persona.  Clearing the input restores the full list.  Backed by
  an in-memory cache (`_all_role_rows`) so the filter doesn't
  re-read disk per keystroke.  (PR-2.)
- **TUI Ecosystem: roles/ directory watcher.**  A polling task
  (default 2 s; configurable via `ACC_TUI_ROLE_WATCH_INTERVAL_S`)
  diffs a fingerprint of role names + per-file mtimes and posts
  a `RolesChangedMessage` when external edits to `role.yaml` or
  `role.md` are detected.  The handler reloads the role cache +
  re-applies the current filter substring (preserved across
  refresh) + re-renders the detail pane for the active row.
  Operator gets a 3-second toast confirming the refresh.
  (PR-3 of proposal 003 — PR #56.)
- **TUI Ecosystem: advisory selection lock.**  Selecting a role
  row takes an advisory `filelock.FileLock` on the role's
  `role.yaml`.lock; released on row change, screen unmount, or
  process exit.  Lock failure (another process holds it) surfaces
  as a warning toast — the operator can still proceed.  Most
  external editors ignore advisory locks, so this primarily
  protects against two TUI sessions stomping on each other.
  (PR-3.)
- **`RolesChangedMessage`** public message added to
  `acc/tui/messages.py` (PR-3).
- **TUI: Configuration pane (pane 8).**  New `ConfigurationScreen`
  at `acc/tui/screens/configuration.py` with three tabs:
  *LLM Endpoints*, *Skills*, *MCPs*.  Reachable via the new `8`
  keybinding from any screen.  (PR-4 of proposal 003 — PR #57.)
- **TUI: LLM Endpoints tab.**  Shows the configured
  `ACCConfig.llm` summary (backend, model, base_url, timeout) as
  read-only text plus a live per-agent table fed from snapshots.
  *Test connection* button HEAD-pings the configured `base_url`
  via stdlib `urllib.request` (no new dependency) and surfaces
  latency + status / unreachable reason.  Writing back to
  role.yaml under a new `llm_endpoint` key is deferred to a
  follow-up.  (PR-4.)
- **TUI: Skills + MCPs tabs (canonical home).**  The Skills and
  MCP-servers tables (plus their *Upload skill* / *Upload MCP*
  file-picker flows) now have their canonical home on the
  Configuration pane.  The Ecosystem copies remain for one
  release as a migration aid; a follow-up PR will remove them.
  (PR-4.)

### Changed

- **NavigationBar extended to 8 panes.**  Module docstring + key
  list + `BINDINGS` updated; every screen's local BINDINGS list
  now includes `("8", "navigate('configuration')",
  "Configuration")`.  (PR-4.)
- **Snapshot fan-out** in `acc/tui/app.py:_apply_snapshot` now
  pushes the active snapshot into the Configuration screen too,
  so its live LLM-backends table refreshes per HEARTBEAT.  (PR-4.)
- **TUI Performance: per-agent table extended.**  New columns
  Cluster, Intent, Subagents, Active task.  Cluster cell shows
  the short cluster_id when the agent is a member of an active
  cluster (sourced from `snap.cluster_topology`); Intent shows
  the first 80 chars of the agent's `task_progress_label`;
  Subagents shows the cluster's total member count; Active task
  shows `current/total` step + age since last heartbeat.
  (PR-5 of proposal 003 — PR #58.)
- **TUI Performance: CLUSTER OVERVIEW panel.**  Reuses the
  ClusterPanel widget from the Prompt screen so the same
  rendering produces consistent cluster_id / target_role /
  members / skill_in_use info across screens.  (PR-5.)
- **TUI Soma / Dashboard: governance counters get definitions.**
  Each Cat-A / Cat-B / Cat-C counter row is paired with a
  one-line definition pulled from a single
  `GOVERNANCE_TAXONOMY` constant at `acc/tui/screens/dashboard.py`
  module bottom (not view-hardcoded so the taxonomy text is
  editable in one place).  (PR-5.)
- **TUI Soma / Dashboard: TOKEN BUDGET BY CLUSTER panel.**  New
  panel rolls up per-agent `token_budget_utilization` grouped by
  `cluster_topology` membership; renders one row per active
  cluster as `cluster_id · target_role · N agents · avg X% /
  worst Y%` with colour coding (green < 75% / yellow < 90% /
  red ≥ 90% on worst single agent).  Empty state shows a calm
  placeholder.  (PR-5.)
- **TUI Ecosystem: directory-derived subrole listing.**  When the
  selected role has sibling directories matching `<role>_*` glob
  with a `role.yaml` (e.g. `coding_agent` → `coding_agent_architect`,
  `coding_agent_implementer`, …), they're listed under a "Subroles
  (directory-derived)" markdown section appended to the detail
  pane's `role.md` view.  Labelled explicitly as directory-derived
  because the first-class `parent_role` field is deferred to
  proposal 004.  (PR-6 of proposal 003 — PR #59.)

### Tests

- **CLI ↔ TUI infuse parity** (`tests/test_infuse_parity.py`,
  NEW, 7 cases).  Pins the ROLE_UPDATE payload envelope as
  byte-equivalent across both surfaces (modulo `ts`), pins the
  role_definition intersection-only parity (recursive — covers
  nested `category_b_overrides`), documents the TUI form's
  known field omissions vs the CLI's full pydantic
  `model_dump()` so regression in either direction surfaces
  immediately, and asserts that neither path leaks
  secret-shaped tokens (`api_key=` / `password=` / …) in the
  payload string form.  (PR-6.)

### Known parity gap (deferred follow-up)

The TUI Infuse form emits a 9-field subset of the full
`RoleDefinitionConfig`.  The CLI emits the full pydantic
`model_dump()`.  The test suite pins this state as the current
reality; closing it (either by extending the TUI form or by
teaching the arbiter to default-fill missing keys) is tracked
as out-of-scope and deferred to a follow-up proposal.

### Fixed

- **TUI Prompt: cancel-on-timeout.**  The Prompt screen now
  publishes `TASK_CANCEL` on `acc.{cid}.task.cancel` when the
  receive loop times out, instead of silently abandoning the
  in-flight task.  Without this fix, vLLM / llama.cpp backends
  kept generating against the dropped task; the operator's work
  was discarded and the late `TASK_COMPLETE` landed on a screen
  the operator had moved past.  (PR-1 of proposal 003 — PR #54.)

### Changed

- **TUI Prompt timeout default** raised from 60 s to 180 s for
  slow local LLM backends.  Configurable via the
  `ACC_PROMPT_TIMEOUT_S` environment variable.  (PR-1.)
- **TUI Prompt transcript message** when receive times out now
  reads "(cancelled after Ns — no reply; TASK_CANCEL published)"
  instead of "(timeout after 60s — no reply)".  Reflects the
  fact that the system actually cancelled rather than gave up.
  (PR-1.)

### Added

- `CHANGELOG.md` (this file) — Keep-a-Changelog format,
  introduced alongside the proposal 003 development cycle.
- `acc/tui/screens/prompt.py:_resolve_timeout()` helper —
  reads `ACC_PROMPT_TIMEOUT_S` with safe fallback to the
  default; warns on malformed / non-positive values.  (PR-1.)
- `acc/tui/screens/prompt.py:_mark_cancelled()` /
  `_is_cancelled()` — 256-entry FIFO of task_ids cancelled by
  the timeout path, for late-TASK_COMPLETE suppression.  Public
  API; not yet wired into the agent-entry append path (the
  channel layer already returns on first signal, so no
  late-reply hazard exists today).  (PR-1.)

## [0.1.0] — pre-proposal-003 baseline

Reconstructable from `git log`; not back-filled here.  Notable
landmarks for context:

- Sub-agent clustering (PRs #26–#30).
- Autoresearcher demo + iteration loop (PRs #41–#46).
- Operator (Kubernetes/OpenShift) scaffold (PRs #47–#51).
- Podman Desktop extension sibling repo
  (`flg77/acc-podman-desktop`) shipped to v0.3.0 in parallel.

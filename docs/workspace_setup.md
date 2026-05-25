# Trusted working directory (workspace sandbox) — D-007

This document describes how ACC agents get **scoped filesystem access**
to a host directory the operator explicitly trusts, mirroring the
"trust this folder" gesture in Claude Code.

> **TL;DR** — The host dir `./workspaces` is bind-mounted to
> `/workspace` in every agent and the TUI. In the **Prompt** screen,
> click **Select Directory** (bottom-left), create-or-pick a project
> folder, confirm. That folder is marked *trusted* and rides the next
> task as a `workspace` field; agents with `workspace_access` then read
> and write files **only** under it.

---

## 1. The three gates

A filesystem write only happens when **all three** of these pass. They
are independent layers (defence-in-depth):

1. **Path sandbox** — `acc/workspace.py:safe_resolve` resolves every
   agent-supplied path against the workspace root to a real
   (symlink-collapsed) absolute path and asserts containment. It
   rejects absolute paths (`/etc/passwd`), parent traversal
   (`../../etc`), and symlink escape. This is the chokepoint **every**
   filesystem skill goes through; it cannot be bypassed by a
   hallucinated or hostile path in an LLM tool call.
2. **Trust flag** — writes additionally require the directory to carry
   a `.acc-workspace-trust` sentinel, written by the TUI when the
   operator confirms. An untrusted directory blocks writes even when
   the path is in-bounds. The sentinel survives restarts and — because
   the workspace is a shared mount — is visible to every agent.
3. **Operating-mode gate (D-003)** — the write skill id is `fs_write`,
   which the operating-mode write-action classifier flags. Under
   `ACCEPT_EDITS` / `ASK_PERMISSIONS`, every write is funnelled through
   the human-oversight queue before touching disk; under `AUTO` it
   proceeds (still bounded by gates 1 + 2 and the constitution).

---

## 2. Which roles can touch the filesystem

Filesystem access is opt-in per role via a single flag in `role.yaml`:

```yaml
# roles/<role>/role.yaml
workspace_access: true        # default: false
```

When `workspace_access: true`, a pydantic `model_validator` on
`RoleDefinitionConfig` automatically:

* appends `fs_read` + `fs_write` to `allowed_skills` and `default_skills`,
* raises `max_skill_risk_level` to `HIGH` (so `fs_write` is dispatchable).

You do **not** list the skills by hand — flipping the flag is enough.

| Role | `workspace_access` |
|------|--------------------|
| `coding_agent` and its 5 subroles (`*_architect`, `*_dependency`, `*_implementer`, `*_reviewer`, `*_tester`) | **`true`** (default) |
| every other role | `false` (flag present, deactivated) |

To grant another role access, set `workspace_access: true` in its
`role.yaml` and redeploy (or hot-reload the role via the Ecosystem /
Nucleus screens).

The two skills:

* **`fs_read`** — risk MEDIUM, read-only, no trust required. Reads a
  file from the workspace.
* **`fs_write`** — risk HIGH, **trust required**, write-action. Writes
  a file into the workspace.

---

## 3. The mount model — recreate-on-select (PR-X)

A containerised TUI **cannot** mount a new host path into
already-running agent containers. So picking a directory does not mount
it directly — it triggers a host-side recreate of **only the agent
services** onto the chosen directory. The TUI session and the agents'
LanceDB / Redis / NATS named volumes (their memory) survive.

Flow:

```
TUI "+" picker  ──writes──▶  .acc-apply/workspace.request  (host path)
                                      │
                       acc-apply-watcher.sh (host) polls it
                                      │
                  ./acc-deploy.sh apply-workspace <host_path>
                    • mkdir -p <host_path>
                    • write .acc-workspace-trust there (host-side uid)
                    • re-point agents' /workspace → <host_path>
                    • force-recreate acc-agent-* ONLY (acc-tui stays up)
```

Relevant mounts in `container/production/podman-compose.yml`:

* Agents: `${ACC_WORKSPACE_HOST_DIR:-../../workspaces}:/workspace:z` —
  `apply-workspace` rewrites `ACC_WORKSPACE_HOST_DIR` in `./.env` to the
  selected path, so the *selected directory itself becomes* `/workspace`.
* acc-tui: `${ACC_WORKSPACE_BASE:-/root}:/host-home:ro` — the browse
  root, mounted **read-only** so the container never writes to your
  home; `../../.acc-apply:/app/.acc-apply:rw` — the request channel.

`ACC_WORKSPACE_BASE` (default the deploying user's `$HOME`) is the
*only* tree the picker can browse and the *only* root `apply-workspace`
will accept — any path outside it is refused. Narrow it (e.g.
`ACC_WORKSPACE_BASE=~/acc-workspaces`) to reduce host exposure.

### One-time setup

```bash
./acc-deploy.sh setup          # scaffolds ./.env + .acc-apply/, starts the watcher
./acc-deploy.sh watcher status # confirm it's running
```

`setup` starts `scripts/acc-apply-watcher.sh` (a dependency-free
polling loop — no inotify/jq). Manage it with
`./acc-deploy.sh watcher {start|stop|status}`; its log is
`.acc-apply/watcher.log`.

---

## 4. Operator workflow

1. Open the TUI → **Prompt** screen.
2. Click **`+`** (bottom-left of the input row; tooltip "Select working
   directory"). The Mode picker sits just left of it.
3. In the modal (a small file-manager):
   * the **location bar** at the top shows where you are — type any
     path + Enter (or click **Go**) to jump there, and **↑ Up** (or
     Alt+↑) climbs toward the filesystem root, so the full directory
     structure is navigable;
   * highlight an existing directory in the tree, **or**
   * type a new folder name in the *"new folder name"* box (path
     separators and `..` are rejected) to create one.
   * **Confirm** (Ctrl+S). In host-mapped mode this writes the apply
     request and closes; the selection must stay **under the browse
     root** (the only host-mounted subtree) or Confirm is refused.
4. The chosen host path shows beside the input: `Workspace: <path>
   (applying — agents restarting, ~a few seconds)`.

> **Local mode.** When the TUI runs directly on the workstation (no
> `ACC_WORKSPACE_BASE`, i.e. no host/container split) the picker browses
> the **real local filesystem**, creates the new folder directly, and
> Confirm returns the chosen absolute path with no agent restart.
5. The host watcher recreates the agents onto that directory. Once
   they're back (watch the Soma pane heartbeats), **Send** your prompt.
   Agents write to the `/workspace` root (= your selected directory),
   which `apply-workspace` already marked trusted.

If no directory is selected, agents use the default `/workspace` mount
(`./workspaces`); writes still require trust, so an untrusted root
blocks writes — fail-closed.

---

## 5. Wire-level detail

Apply request (`.acc-apply/workspace.request`, written by the TUI):

```json
{ "host_path": "/home/flg/projects/foo", "ts": 1747000000.0, "requested_by": "tui" }
```

* `acc/workspace_apply.py:write_apply_request` writes it atomically;
  `is_within_base` is the symlink-safe guard the watcher reuses.
* `acc-deploy.sh apply-workspace` re-validates the path is under
  `ACC_WORKSPACE_BASE` (pure-bash `realpath`), mkdir's it, writes the
  trust sentinel, and `up -d --force-recreate`s the baseline agents.
* The agents then resolve `fs_read`/`fs_write` under `/workspace` =
  the selected directory; `safe_resolve` + `locked_atomic_write`
  (atomic + flock) still apply.

> The legacy per-task `workspace` subpath field (PR-U2b) +
> `_resolve_task_workspace_dir` remain in the codebase and still work if
> a subpath is ever sent, but the recreate-on-select flow leaves it
> empty — the whole mount is the project.

---

## 6. Security notes & foot-guns

* **The container browses read-only.** `/host-home` is `:ro`; the TUI
  never writes to your home. mkdir + trust happen host-side with the
  correct uid via `apply-workspace`.
* **Out-of-base paths are refused twice** — once in
  `acc.workspace_apply.is_within_base` (TUI/watcher) and again in
  `acc-deploy.sh apply-workspace` (host, `realpath` containment).
* **Path escapes within the workspace** are caught by
  `acc.workspace.safe_resolve` (absolute / `..` / symlink escape) for
  every `fs_read`/`fs_write`.
* **Concurrent writes** go through `locked_atomic_write` — atomic
  temp+replace under a per-root `flock` + in-process lock, so
  cooperating agents can't tear or interleave files.
* **Trust is per-directory.** The `.acc-workspace-trust` sentinel lives
  at the selected directory's root; removing the dir drops trust.
* **Agents restart on each pick** (a few seconds). Memory (named
  volumes) survives; any in-flight task on those agents is interrupted.
* **`ACC_WORKSPACE_BASE` bounds the blast radius.** Set it to a
  dedicated projects dir rather than leaving it at `$HOME` if you want
  agents/operators unable to reach the rest of your home.

---

## 7. Tests

* `tests/test_workspace_sandbox.py` — `safe_resolve` escape vectors
  (absolute / traversal / symlink), trust flag, skill round-trip,
  D-003 write-classifier integration.
* `tests/test_agent.py::TestResolveTaskWorkspaceDir` — per-task
  workspace resolution + the defensive guards.
* `tests/test_prompt_channel.py` — `workspace` field threading on the
  TUIPromptChannel.
* `tests/test_workspace_apply.py` — apply-request write/read + the
  `is_within_base` containment guard (traversal + symlink escape).
* `tests/test_prompt_screen_pilot.py` — `+` button, Mode-in-input-row.
* `tests/test_workspace_select_modal.py` — the modal: browse + apply
  request with the host path, new-folder append, traversal-name +
  missing-base rejection, cancel.

Run the slice:

```bash
pytest tests/test_workspace_sandbox.py \
       tests/test_workspace_apply.py \
       tests/test_workspace_select_modal.py \
       tests/test_prompt_channel.py \
       tests/test_prompt_screen_pilot.py \
       'tests/test_agent.py::TestResolveTaskWorkspaceDir' -v
```

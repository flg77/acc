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

## 3. The mount

`container/production/podman-compose.yml` bind-mounts the host
workspace into every agent **and** acc-tui:

```yaml
- ${ACC_WORKSPACE_HOST_DIR:-../../workspaces}:/workspace:z
```

* `${ACC_WORKSPACE_HOST_DIR}` defaults to `./workspaces` at the repo
  root (the path is relative to `container/production/`). Override it
  in `./.env` to point somewhere else on the host.
* `:z` applies the shared SELinux relabel so every container in the
  pod can access it (required on RHEL / Fedora hosts like acc1).
* The same host path is mounted into the TUI so its **Select
  Directory** modal browses the *same* tree the agents see — a folder
  trusted in the TUI is immediately trusted for the agents.

`./workspaces/.gitkeep` ships in-repo so the mount source exists on a
clean checkout (podman would otherwise create it root-owned). Project
subdirectories created at runtime are git-ignored.

---

## 4. Operator workflow

1. Open the TUI → **Prompt** screen.
2. Click **Select Directory** (bottom-left of the prompt input row).
3. In the modal:
   * highlight an existing project directory under `/workspace`, **or**
   * type a new folder name in the *"new folder name"* box to create
     one (path separators and `..` are rejected).
   * **Confirm** (Ctrl+S). This creates the folder if needed, writes
     the `.acc-workspace-trust` sentinel, and closes the modal.
4. The chosen path is shown beside the button: `Workspace:
   /workspace/<project> (trusted)`.
5. Type your prompt and **Send**. The task carries a `workspace`
   field (relative to the mount, e.g. `myproject`); the receiving
   agent points `ACC_WORKSPACE_DIR` at `/workspace/myproject` for that
   task, so `fs_read` / `fs_write` resolve under it.

If no directory is selected, the task is sent without a `workspace`
field and agents fall back to the default `/workspace` root (writes
still require a trusted root, so an unselected, untrusted root blocks
writes — fail-closed).

---

## 5. Wire-level detail

`TASK_ASSIGN` payload (only present when a directory was selected):

```json
{
  "signal_type": "TASK_ASSIGN",
  "task_id": "…",
  "content": "write a web scraper",
  "target_role": "coding_agent",
  "workspace": "myproject"
}
```

Agent side (`acc/agent.py`):

* `_resolve_task_workspace_dir(data)` validates the field
  (rejects absolute / `..` / empty) and returns
  `<ACC_WORKSPACE_MOUNT or /workspace>/<project>`, or `None`.
* On a non-`None` result the handler sets `os.environ["ACC_WORKSPACE_DIR"]`
  before dispatching capabilities. Tasks are handled serially per
  agent, so this per-task env set is safe.
* `acc/workspace.py:workspace_root()` reads `ACC_WORKSPACE_DIR`
  (falling back to `/workspace`); `safe_resolve` enforces containment
  regardless of what the field claimed.

---

## 6. Security notes & foot-guns

* **Absolute paths and `..` are rejected twice** — once defensively in
  `_resolve_task_workspace_dir` (the `workspace` field can't repoint
  the mount root outside `/workspace`) and again, authoritatively, in
  `safe_resolve` (the actual file path the skill resolves).
* **Symlink escape** is caught: `safe_resolve` resolves through
  symlinks and asserts the *real* path is under the *real* root.
* **Trust is per-directory, not global.** Trusting `myproject` does
  not trust its siblings. The sentinel lives at the project root.
* **Removing a trusted dir on the host** drops access immediately
  (the sentinel disappears with it).
* **Do not** point `ACC_WORKSPACE_HOST_DIR` at a sensitive host path
  (`/`, `$HOME`, a repo with secrets). The sandbox bounds agents
  *within* the mount, but the mount itself is whatever you bind in.

---

## 7. Tests

* `tests/test_workspace_sandbox.py` — `safe_resolve` escape vectors
  (absolute / traversal / symlink), trust flag, skill round-trip,
  D-003 write-classifier integration.
* `tests/test_agent.py::TestResolveTaskWorkspaceDir` — per-task
  workspace resolution + the defensive guards.
* `tests/test_prompt_channel.py` — `workspace` field threading on the
  TUIPromptChannel.
* `tests/test_prompt_screen_pilot.py` — Select-Directory button +
  payload threading from the Prompt screen.
* `tests/test_workspace_select_modal.py` — the modal: confirm trusts +
  returns, new-folder creation, traversal-name rejection, cancel.

Run the slice:

```bash
pytest tests/test_workspace_sandbox.py \
       tests/test_workspace_select_modal.py \
       tests/test_prompt_channel.py \
       tests/test_prompt_screen_pilot.py \
       'tests/test_agent.py::TestResolveTaskWorkspaceDir' -v
```

# 20260926-secrets-from-kubernetes-and-a-live-broker — proposal

## Why

Lane F3 on the 2026-09-25 priority list: *one external secret source, and the broker
on a real call path*. Both halves were left open by earlier changes, and both look
finished from the outside.

**The source.** `20260817-scoped-secret-delivery` shipped per-role scoping and
marked task [7], "a source interface with the file-based implementation as
reference", done. There is no such interface in the tree: every credential is read
with `os.environ.get(...)` at the point of use. Task [8], one external source, was
left open with the question of which one. The operator answered it on 2026-09-26:
**ACC supports OpenBao/Vault, and for now uses Kubernetes Secrets.**

On Kubernetes the operator already puts Secrets into agent pods as **environment
variables** (`SecretKeyRef`, `spec.secretEnv`). That is a source, but it is the wrong
kind for the two things still missing:

* **Rotation without a restart** (task [6]). A container's environment is fixed at
  start. `llm_openai_compat._headers()` re-reads its key on every call "so that key
  rotation takes effect without restarting the agent" — true on no deployment,
  because nothing ever changes a running process's environment.
* **A credential the agent does not hold.** The egress broker's promise — "it cannot
  be exfiltrated from a process that never had it" — needs somewhere to read the
  credential from that is not the agent's environment.

A Secret **mounted as a volume** gives both: the kubelet refreshes the files in place
when the Secret changes (within its sync period, about a minute), and reading a file
at call time puts nothing in `os.environ`. It is also where OpenBao/Vault lands on a
cluster without ACC speaking Vault's API: the External Secrets Operator and the Vault
Secrets Operator both sync into ordinary Kubernetes Secrets. So *mounted Secrets* is
the first source, and OpenBao/Vault is supported through it.

**The broker.** `acc/credentials/broker.py` (per-operator OAuth, PKCE, a sealed token
store) is complete and tested, and has no caller. `mcps/google_workspace/mcp.yaml`
declares `auth: oauth`, but `MCPClient` builds its transport without a
`bearer_resolver`, so an OAuth manifest today sends its requests **unauthenticated**
and says nothing. There is no surface to connect an account either
(`docs/howto-office-connections.md` step 4: "lands next").

## What changes

### Phase 1 (this ship — runtime)

* **`acc/secret_source.py`** — the source interface task [7] claimed: `get(name)`,
  `names()`, `describe()`. Two sources:
  * `env` (default) — today's behaviour, unchanged;
  * `mounted` — `ACC_SECRET_SOURCE=mounted`, files under `ACC_SECRET_DIR`
    (default `/var/run/acc/secrets`), one file per name, read **at call time**.
    A name with no file falls back to the environment, so a half-migrated
    deployment keeps working and says which one it used.
  Values are never logged, returned in status output or written anywhere.
* **Call-time reads through the source** at the credentialed call paths:
  `openai_compat` (every request), the MCP HTTP transports' `api_key_env`
  (per request, not once at construction), the egress broker's
  `headers_for()`, and the sealed token store's key (`ACC_CRED_KEY`).
* **The OAuth broker, live.** `dispatch_invocations` records the task's requester
  for the duration of the call; an `auth: oauth` MCP manifest gets a
  `bearer_resolver` that mints for **that requester's person**
  (`attribution.person_of`). Not connected → the call is refused with the command
  that fixes it, never sent unauthenticated.
* **`acc-cli oauth`** — `connect <provider>` prints the consent URL and keeps the
  PKCE verifier sealed; `complete <provider> --code …` exchanges it; `status`;
  `disconnect`. The human consents in the provider's own screen — ACC never enters a
  credential.
* **`acc-cli doctor --check secrets`** — the source, the directory, how many names it
  holds (names, never values), and any name a bound model or MCP manifest needs that
  no source can supply.

### Phase 2 (deferred — operator)

* `AgentCorpus.spec.secretMount` (a Secret name, optional `items`) mounted read-only
  at `/var/run/acc/secrets` on every agent, and `ACC_SECRET_SOURCE=mounted` set.
  Per-role `items` from the same derivation `secret_scope` uses. An operator release
  (0.2.28), built by the pipeline.

### Phase 3 (deferred — UX-07)

* Masked secret input in the decision panel, written into the source: on the edge a
  file in the mounted directory's host path; on a cluster a patch of one named
  Secret, which needs an RBAC grant the UI ServiceAccount does not have
  (`resourceNames`-restricted `patch`). A decision, not a fix.

### Phase 4 (optional)

* OpenBao/Vault read directly (KV v2 with Kubernetes auth), for a deployment that
  runs neither ESO nor VSO.

## Impact

* **Affected code:** new `acc/secret_source.py`, `acc/cli/oauth_cmd.py`; changed
  `acc/backends/llm_openai_compat.py`, `acc/mcp/transports.py`, `acc/mcp/client.py`,
  `acc/mcp/registry.py`, `acc/capability_dispatch.py`, `acc/egress.py`,
  `acc/credentials/broker.py`, `acc/preflight.py`, `acc/cli/__init__.py`.
* **New env knobs:** `ACC_SECRET_SOURCE` (`env` | `mounted`), `ACC_SECRET_DIR`,
  `ACC_CRED_DIR` (the sealed OAuth token store).
* **Tests:** one new file; every source read, the fallback, rotation by rewriting a
  file, the OAuth resolver per requester, the refusal when not connected, and that no
  value reaches a log, a status line or an exception message.
* **Backward compatibility:** with `ACC_SECRET_SOURCE` unset every read is the same
  `os.environ` read as before. An `auth: oauth` manifest that used to send an
  unauthenticated request now refuses — deliberately.

## What stays open after Phase 1

* The mount itself on a cluster (Phase 2): until the operator mounts a Secret, a
  cluster deployment keeps the `env` source.
* **The agent process still reads the file.** A mounted credential is out of
  `os.environ`, but a tool that can read the filesystem can read it. Keeping it from
  the agent entirely means the broker runs in another process — the OpenShell
  gateway, or an egress sidecar. This change does not claim otherwise, and
  `doctor` says so.
* Anthropic's client is built once with its key; rotating it needs the client
  rebuilt (the openai-compatible path already re-reads per call).

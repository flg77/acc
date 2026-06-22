# `acc-pkg` — the ACC role-package toolchain

`acc-pkg` is the command-line tool for **building, signing, publishing, installing,
inspecting and querying** ACC role packages (`.accpkg`). Its query verbs are
deliberately modelled on **`rpm`** and its build/scaffold verbs on **`rpmbuild`**, so
if you know the RPM toolchain the muscle memory carries over.

> **Two different CLIs — don't confuse them.**
> - **`acc-pkg`** (this document) — the *package* toolchain. Console script
>   `acc.pkg.cli:main`. Build/sign/install/query `.accpkg` files.
> - **`acc-cli`** — the *operator/runtime* CLI (`acc.cli:main`). Drives a running
>   collective (infuse, eval, serve, …). Documented separately in
>   [`acc-cli.md`](./acc-cli.md).

To install the `acc-pkg` toolchain itself (container image or `pip install acc`), see
the **Prerequisites** section of [`CONTRIBUTING-ROLE.md`](./CONTRIBUTING-ROLE.md) —
that is the canonical install path (two options: a zero-Python container, or an
editable Python install).

---

## Global options

Both flags sit **before** the subcommand and apply to every command:

| Flag | Effect |
|---|---|
| `--quiet` | Suppress all non-error stdout (logging drops to `WARNING`). The `--allow-unsigned` audit line still prints — it logs at `WARNING`. |
| `--json` | Emit machine-readable JSON for the commands that produce data (`build`, `install`, `verify`, `inspect`, `list`, and all query verbs). Without it, output is rendered human-readable. |

```bash
acc-pkg --json install ./dist/@you-my_role-0.1.0.accpkg --allow-unsigned
acc-pkg --quiet build . -o dist/pkg.accpkg
```

There are **no interactive prompts** anywhere in `acc-pkg` — it is built for CI and
automation.

## Exit codes

`acc-pkg` uses deterministic exit codes so scripts can branch on the failure class:

| Code | Meaning |
|---|---|
| `0` | OK |
| `1` | User / argument error (missing file, bad CLI args, unsafe path, already-installed) |
| `2` | Manifest schema failure / Pydantic validation / lint errors (`validate`) |
| `3` | Dependency resolution failure (missing dep; or `uninstall`/`-e` refused because something still requires the package) |
| `4` | Content-hash / sha256 mismatch (tamper or corruption) |
| `5` | Signature missing or rejected |
| `6` | Enterprise Contract policy violation (`--ec-policy`) |

---

## rpm / rpmbuild → `acc-pkg` cheat-sheet

This is the "full list of rpm(build)-inspired flags" — the RPM verb you know on the
left, the `acc-pkg` equivalent on the right.

| RPM / rpmbuild | `acc-pkg` | What it does |
|---|---|---|
| `rpmbuild -bb` (build binary) | `acc-pkg build <src> -o <out>` | Build a `.accpkg` from a source tree |
| `rpmdev-newspec` / `%setup` scaffolding | `acc-pkg init <name> --scope @you` | Scaffold a fillable, buildable pack skeleton |
| (add a sub-package to a spec) | `acc-pkg new-role <role> --pack <dir>` | Add + register a role in an existing pack |
| `rpmlint` | `acc-pkg validate [<dir>]` | Lint a pack source tree before building |
| `rpm -K` / `--checksig` | `acc-pkg verify <pkg> --signature <sig> …` | Verify a detached signature without installing |
| `rpm -i` / `-U` | `acc-pkg install <pkg> …` | Install (idempotent; verifies signature first) |
| `rpm -qi` (query info) | `acc-pkg info` / `acc-pkg qi <name>` | Package detail, or which package owns a capability |
| `rpm -ql` (query list) | `acc-pkg contents` / `acc-pkg ql <@scope/name>` | List roles/skills/mcps a package provides |
| `rpm -qf` (query file owner) | `acc-pkg owner` / `acc-pkg qf <name>` | Which installed package provides a role/skill/mcp |
| `rpm -qa` (query all) | `acc-pkg list [--available]` | List installed packages, or catalog availability |
| `rpm -V` (verify) | `acc-pkg verify-installed` / `acc-pkg qv [<@scope/name>]` | Re-check on-disk content hashes (tamper detection) |
| `rpm -e` (erase) | `acc-pkg uninstall` / `acc-pkg remove <@scope/name>` | Remove an installed package + registry entry |
| `rpm -q --whatrequires` | `acc-pkg rdeps <@scope/name>` | Installed packages that `depend_on` this one |

The short rpm-style aliases (`qf`, `ql`, `qi`, `qv`, `remove`) are first-class — e.g.
`acc-pkg qf coding_agent` is identical to `acc-pkg owner coding_agent`.

---

## Authoring & building

### `acc-pkg init <name> --scope @you` — scaffold a new pack

Renders a complete, fillable, **buildable + evaluable** pack skeleton in `./<name>`
(or `--output <dir>`). This is the "full skeleton" generator: it writes `accpkg.yaml`,
`roles/<name>/{role.yaml, system_prompt.md, eval_rubric.yaml}`, behaviour + safety
eval stubs under `evals/`, a curated-model list, a `README.md` and a `Makefile`. Every
TODO it leaves is caught later by `acc-pkg validate`.

| Argument / flag | Default | Meaning |
|---|---|---|
| `name` | — (required) | Role/pack base name; lowercase snake or kebab case (`^[a-z][a-z0-9_-]*`) |
| `--scope` | — (required) | Your publishing scope, e.g. `@you` → package `@you/<name>` |
| `--kind` | `role` | `role` or `agentset` |
| `--domain` | `custom` | `domain_id` stamped into the generated role(s) |
| `--version` | `0.1.0` | Initial pack version |
| `--output` | `./<name>` | Target directory (must be empty / non-existent) |

```bash
acc-pkg init my-analyst --scope @you --domain research
cd my_analyst          # note: kebab → snake in the dir name
$EDITOR roles/my_analyst/role.yaml   # fill the TODOs
```

### `acc-pkg new-role <role> --pack <dir>` — add a role to an existing pack

Writes a new role's files **and registers it** in the pack's `accpkg.yaml` `roles:`
list. A pack can carry many roles (an *agentset / roleset*).

| Argument / flag | Default | Meaning |
|---|---|---|
| `role` | — (required) | New role name (lowercase snake) |
| `--pack` | `.` | Pack source dir (must contain `accpkg.yaml`) |
| `--domain` | `custom` | `domain_id` for the new role |

### `acc-pkg validate [<dir>]` — lint before building

Validates `accpkg.yaml` against the manifest schema, every `role.yaml` against
`RoleDefinitionConfig`, loads the `evals/` tree, and **flags any unfilled `TODO`
markers**. Exit `0` = clean, `2` = errors (printed as a list).

```bash
acc-pkg validate .        # default: current dir
```

### `acc-pkg build <src> -o <out>` — build a `.accpkg`

Builds a signed-ready package from a source tree. Runs the **capability-validation
gate** first (proposal 033 WS-A: "verify before packaging") so a malformed manifest
never ships.

| Argument / flag | Meaning |
|---|---|
| `source` | Path to the source tree containing `accpkg.yaml` |
| `-o`, `--output` | Output `.accpkg` path (required) |
| `--no-validate` | Skip the capability-validation gate (escape hatch) |

JSON output includes `content_sha256` and `tarball_sha256` (the latter is what the
published catalog records, so a local build can be checked byte-for-byte against the
registry).

---

## Signing, publishing, installing

### `acc-pkg verify <pkg> --signature <sig>` — check a signature, no install

| Flag | Meaning |
|---|---|
| `--signature` | Path to the detached signature (required) |
| `--key` | Cosign public-key PEM (keypair mode) |
| `--issuer` + `--subject` | OIDC issuer + subject regex (keyless mode) |
| `--attestations` | Attestation bundle YAML (Stage 1.2) |
| `--ec-policy` | Enterprise Contract policy YAML (default `/etc/acc/policy/enterprise-contract.yaml`) |

You must supply **either** `--key` **or** (`--issuer` + `--subject`).

### `acc-pkg install <pkg>` — install a package

Enforces the **signing floor** by default: a package is verified before it is
installed. Re-installing identical `(name, version, content-hash)` is a no-op
(`was_already_installed=true`).

| Flag | Meaning |
|---|---|
| `--signature` | Detached signature path (default: `<pkg>.sig` next to the file) |
| `--key` | Cosign public-key PEM (keypair mode) |
| `--issuer` + `--subject` | OIDC keyless verification |
| `--allow-unsigned` | **Bypass** verification. Operator-explicit and **audit-logged at `WARNING`**. Pair with `ACC_ALLOW_UNSIGNED=1` for local dev installs from a `tier: self` catalog. |
| `--attestations` | Attestation bundle YAML (Stage 1.2) |
| `--ec-policy` | Enterprise Contract policy YAML |

### `acc-pkg login` — report publish readiness

Surfaces OIDC token + issuer status so you know whether `publish` will succeed
(Stage 1.3). No arguments.

### `acc-pkg publish <pkg> --catalog-url <url>` — sign + upload

OIDC-keyless signs the package and uploads it to a catalog endpoint.

| Flag | Meaning |
|---|---|
| `--catalog-url` | Base URL of the catalog upload endpoint (required) |
| `--token` | Bearer token for the endpoint (optional) |
| `--issuer` | OIDC issuer URL (default: public Sigstore) |

> Cutting a *release* (tag → publish workflow) is an operator-gated action, not
> something you run ad-hoc against the production catalog. See the release/promote
> runbooks.

---

## Inspecting & evaluating

### `acc-pkg inspect <pkg>` — print a package's manifest

Pretty-prints the `accpkg.yaml` manifest from inside a built `.accpkg` (name, version,
roles, **skills, mcps**, depends_on, description). This is how you read a roleset's
declared capability surface without installing it.

### `acc-pkg eval <installed-pkg-dir>` — summarise the evals tree

Loads + validates every YAML under the package's `evals/` and prints counts (behaviour
+ safety) and the curated-model panel size. (Stage 1.1 — the real-LLM run is the eval
harness; see `tools/run_evals.py` and `CONTRIBUTING-ROLE.md`.)

---

## Querying installed packages (the rpm verbs)

These operate over the **local install registry + capability index** — the equivalent
of querying the RPM database.

### `acc-pkg list [--available]` — `rpm -qa`

- No flags → list **installed** packages (name, version, install path, installed-at).
- `--available` → list packages offered by the configured **catalogs**.
  - `--name <@scope/name>` filter the available list.
  - `--workspace <dir>` also include that workspace's `.acc/catalogs.yaml`.

### `acc-pkg owner <name>` / `qf` — `rpm -qf`

Which installed package provides a given role/skill/mcp.

| Flag | Meaning |
|---|---|
| `name` | The capability name (a role, skill, or mcp) |
| `--kind` | Narrow to `role` \| `skill` \| `mcp` |

### `acc-pkg contents <@scope/name>` / `ql` — `rpm -ql`

List the roles / skills / mcps an installed package provides.

### `acc-pkg info <name>` / `qi` — `rpm -qi`

- `@scope/name` → full package detail (version, install path, installed-at,
  content hash, `provides`).
- a bare capability name → which package(s) own it (`--kind` to narrow).

### `acc-pkg verify-installed [<@scope/name>]` / `qv` — `rpm -V`

Re-hash installed content and compare to the recorded hash (tamper detection). No
argument = verify **all** installed packages. Exit `4` if any hash mismatches.

### `acc-pkg uninstall <@scope/name>` / `remove` — `rpm -e`

Remove an installed package's tree + registry entry. **Refuses** (exit `3`) if another
installed package still `depend_on`s it, unless you pass `--force`.

| Flag | Meaning |
|---|---|
| `package` | `@scope/name` |
| `--version` | Pin which installed version to remove |
| `--force` | Remove even if depended upon |

### `acc-pkg rdeps <@scope/name>` — `rpm -q --whatrequires`

List the installed packages that `depend_on` the given package.

---

## Related: the Agent Bill of Materials (A-BOM)

As of **v0.5.4** the runtime ships a first-class **Agent Bill of Materials**
(`acc/pkg/agent_bom.py`, proposal 040): a signed, CRD-shaped manifest that pins an
entire customized agentset to **exact `@scope/name@version`** packages from a signed
catalog — the reproducible, air-gap-installable backbone behind the `/new-agent`
"launch your agent" flow.

The A-BOM today is a **schema + verifier library** (resolution + signing-floor +
deploy-target checks). The dedicated `acc-pkg` resolve verb, the `acc-deploy` adapters,
and the operator `AgentBOM` CRD are declared **follow-ons** — they are *not* yet
`acc-pkg` subcommands. See [`agent-bom-and-new-agent.md`](./agent-bom-and-new-agent.md)
for the model and the onboarding flow.

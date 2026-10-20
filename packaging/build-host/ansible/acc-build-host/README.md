# acc-build-host — the dedicated ACC release build host
| Declaration | |
|---|---|
| **What** | provisions `acc-build`, the host the ACC release pipelines build and sign on (mock chroots x86_64 + aarch64, rootless podman, the build user, the signing keyring, the operator's ssh access) |
| **Acts on** | inventory group `build_hosts` over ssh with become |
| **Entrypoints** | `site.yml` with `acc_build_host_operation=status\|provision\|smoke\|teardown`, or `playbooks/<verb>.yml` |
| **Mutates** | only with `-e apply=true`; teardown `-e confirm=YES` (+ `wipe_data`, `wipe_key`) |
| **Secrets** | none in the role; the signing key is MOVED to the host by hand (HOWTO §3), never generated or carried here |
| **Supply chain** | 5 — the RPM: this is where it is built and signed |
| **Overview** | `docs/ACC-in-lab-gitops.md` in lab-gitops — how every ACC automation fits |

`20261004-public-release-channels` (acc-spearhead), Phase 3, decision D2: the
release RPMs and images were built on lighthouse, the inference box, with the
signing key in a home directory there and nothing provisioned by lab-gitops.
This role makes the replacement host reproducible. Host/runtime tier of the
CRUD-role standard (`docs/STANDARD-ansible-crud-roles.md`), a self-contained
galaxy role like `ansible/acc-distribute`.

**Where it lives.** Authored in acc-spearhead at
`packaging/build-host/ansible/acc-build-host/` next to the pipelines it serves;
its home is `lab-gitops/ansible/acc-build-host/` (copy the directory, add the
row to `docs/ACC-in-lab-gitops.md`, link the runbook as
`docs/HOWTO-acc-build-host.md`). Runbook: `packaging/build-host/HOWTO.md`.

## The verbs

| Verb | Command | Mutates? | What it does |
|---|---|---|---|
| **status** (R) | `-e acc_build_host_operation=status` | no | runs the verification script in report mode: tools, user, chroots, key, space |
| **provision** (C/U) | `-e acc_build_host_operation=provision -e apply=true` | yes (opt-in) | packages, the `acc-build` user (group mock, sub-ids, lingering), directories, authorized keys, binfmt, every chroot config present; then smoke. `apply=false` prints the plan |
| **smoke** (verify) | `-e acc_build_host_operation=smoke` | no | `files/verify-build-host.sh` as the build user; fails on the first missing thing |
| **teardown** (D) | `-e acc_build_host_operation=teardown -e confirm=YES` | yes (guarded) | stops lingering, removes the work trees; `wipe_data=true` removes the user and home, refused while the keyring holds a key unless `wipe_key=YES` |

```bash
cd ansible/acc-build-host
ansible-playbook site.yml -e acc_build_host_operation=status
ansible-playbook site.yml -e acc_build_host_operation=provision                       # the plan
ansible-playbook site.yml -e acc_build_host_operation=provision -e apply=true \
    -e acc_build_host_authorized_keys_file=~/.ssh/id_rsa.pub
ansible-playbook site.yml -e acc_build_host_operation=smoke -e acc_build_host_key_fingerprint=<FPR>
ansible-playbook site.yml -e acc_build_host_operation=teardown -e confirm=YES
```

## Knobs (`defaults/main.yml`)

| Knob | Default | Meaning |
|---|---|---|
| `acc_build_host_user` / `_home` | `acc-build` / `/srv/acc-build` | the build user; `BUILD_ROOT` for the pipelines is the home |
| `acc_build_host_gnupghome` | `/srv/acc-build/.acc-rpm-gpg` | the standalone key's keyring (created empty, 0700) |
| `acc_build_host_key_fingerprint` | `""` | when set, smoke/status require the key to be this one |
| `acc_build_host_channel_key_url` | `""` | the Satellite's served public key, compared instead |
| `acc_build_host_authorized_keys[_file]` | `[]` / `""` | the operator's ssh public keys for the build user |
| `acc_build_host_mock_chroots[_aarch64]` | EL9, EL10, Fedora 42 × x86_64 / aarch64 | what `packaging/rpm/mock-matrix.sh` builds |
| `acc_build_host_aarch64` | `true` | install `qemu-user-static-aarch64`, check the aarch64 chroots |
| `acc_build_host_min_free_gb` | `100` | smoke asserts this much free under the build root |
| `apply` / `smoke` / `confirm` / `wipe_data` / `wipe_key` | standard guards | see the verbs |

## AAP Controller

One Job Template per verb at `ansible/acc-build-host/playbooks/<verb>.yml`, or
`site.yml` with `acc_build_host_operation` as a survey; a Machine credential
for the host; `ANSIBLE_ROLES_PATH=ansible` if the Execution Environment ignores
per-directory configs.

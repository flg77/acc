# HOWTO: `acc-build`, the dedicated release build host

`20261004-public-release-channels`, Phase 3 (decision D2). The runbook the
operator follows once; the role under `ansible/acc-build-host/` is what makes
the host reproducible afterwards.

## What problem this solves

Every release RPM was built on **lighthouse**, the inference box: the standalone
signing key sat in `/home/flg/.acc-rpm-gpg` there, the work trees and the pip
cache under `/git/ml/agentic`, and nothing about the host was provisioned by
lab-gitops. A release and a model evaluation competed for the same machine, a
rebuild of lighthouse would have lost the build tree, the cache and the key in
one go, and there was no second copy of how the host was set up.

After this: one VM, `acc-build`, provisioned by a role; the key moved there
(moved, not regenerated, so every host that trusts the channel keeps trusting
it); the pipelines' default `BUILD_HOST` switched; lighthouse builds nothing.

## What the host is

| | |
|---|---|
| name | `acc-build.ic3net.internal` (DNS: lab-gitops `ansible/dns`) |
| base | RHEL 10 (RHEL 9 also works; the chroots are mock's, not the host's) |
| size | 4 vCPU, 16 GB RAM, **200 GB** disk — six mock chroots, a ~2.5 GB venv per build, the image builds |
| GPU | none |
| build user | `acc-build`, home `/srv/acc-build`, in group `mock`, sub-uid range for rootless podman, lingering |
| `BUILD_ROOT` | `/srv/acc-build` — work trees `acc-rpm-<v>` / `acc-images-<v>`, `pip-cache/`, `tmp/` |
| keyring | `/srv/acc-build/.acc-rpm-gpg` (0700), the **standalone key** moved from lighthouse |
| reached by | the operator's workstation over ssh, as `acc-build` (`Host acc-build` in `~/.ssh/config`) |
| tools | `mock` + `mock-core-configs`, `qemu-user-static-aarch64`, `podman`, `copr-cli`, `rpm-sign`, `rpm-build`, `createrepo_c`, `python3.12`, `gcc`, `git` |

The host is registered to the Satellite like every lab node (it needs AppStream,
EPEL for `mock`, and `python3.12`), and it must reach PyPI, the PyTorch CPU index
and GitHub: `packaging/rpm/build.sh` installs the venv from there.

## 1. Create the VM

With lab-gitops' VM builder (`ansible/vm-builder`, kickstart on the libvirt
host), then register it to the Satellite and give it a DNS record:

```bash
cd ansible/vm-builder && ansible-playbook build-vm.yml
#   VM / role name: acc-build     FQDN: acc-build.ic3net.internal     distro: rhel9 (or the RHEL 10 ISO)
#   200 GB disk, 4 vCPU, 16 GB
# then: subscription-manager register --org ic3net_internal --activationkey <lab key>
# then: ansible/dns  — the A record (see ansible/dns/group_vars/bind_servers/zones.yml)
```

## 2. Provision the host (the role)

The role lives in **this** repository at `packaging/build-host/ansible/acc-build-host/`
in the lab's galaxy-role layout (`docs/STANDARD-ansible-crud-roles.md` in
lab-gitops). Its home is `lab-gitops/ansible/acc-build-host/`: copy the
directory there, add its row to `docs/ACC-in-lab-gitops.md`, and link this file
as its `docs/HOWTO-acc-build-host.md`.

```bash
cd ansible/acc-build-host
ansible-playbook site.yml -e acc_build_host_operation=status           # read-only: what is there
ansible-playbook site.yml -e acc_build_host_operation=provision         # the plan, nothing changes
ansible-playbook site.yml -e acc_build_host_operation=provision -e apply=true \
    -e acc_build_host_authorized_keys_file=~/.ssh/id_rsa.pub            # creates the user, installs, smokes
ansible-playbook site.yml -e acc_build_host_operation=smoke             # any time: verify-build-host.sh as acc-build
```

The smoke check fails on one thing after a fresh provision, by design: **no
secret key in the keyring**. That is step 3.

The operator's ssh config:

```
Host acc-build
    HostName acc-build.ic3net.internal
    User acc-build
    IdentityFile ~/.ssh/id_rsa          # lighthouse runs FIPS: RSA; the new host takes either
```

## 3. Move the signing key — move, never regenerate

The channel `ic3net_internal/ACC/acc-spearhead` is signed with the standalone
key (RSA 4096, UID `ACC Release Signer <hostmaster@ic3net.internal>`), whose
public half is the Satellite content credential `acc-spearhead-signing`. A new
key would mean every subscribed host re-imports a key and every package already
in the channel verifies against the old one. So the same key moves.

The private key crosses the wire **inside one ssh pipe**, never through a file
on either side, and nothing is deleted on lighthouse until a signed release has
been proven from the new host (step 4).

```bash
# 3a. the fingerprint the channel serves (what the moved key MUST be)
curl -fsSL https://sat1.ic3net.internal/katello/api/v2/repositories/<id>/gpg_key_content \
    | gpg --show-keys --with-colons | awk -F: '$1=="fpr"{print $10; exit}'

# 3b. the fingerprint on lighthouse — must be the same
ssh lighthouse 'gpg --homedir /home/flg/.acc-rpm-gpg --batch --list-secret-keys --with-colons' \
    | awk -F: '$1=="fpr"{print $10; exit}'

# 3c. the move: export on lighthouse, import on acc-build, one pipe, no file
ssh lighthouse 'gpg --homedir /home/flg/.acc-rpm-gpg --batch --export-secret-keys --armor "ACC Release Signer"' \
    | ssh acc-build 'gpg --homedir /srv/acc-build/.acc-rpm-gpg --batch --import'
ssh lighthouse 'gpg --homedir /home/flg/.acc-rpm-gpg --batch --export --armor "ACC Release Signer"' \
    | ssh acc-build 'gpg --homedir /srv/acc-build/.acc-rpm-gpg --batch --import'
ssh acc-build 'echo "$(gpg --homedir /srv/acc-build/.acc-rpm-gpg --batch --list-secret-keys --with-colons | awk -F: '"'"'$1=="fpr"{print $10; exit}'"'"'):6:" | gpg --homedir /srv/acc-build/.acc-rpm-gpg --import-ownertrust'

# 3d. the smoke check now passes, and the fingerprint is the channel's
cd ansible/acc-build-host && ansible-playbook site.yml -e acc_build_host_operation=smoke \
    -e acc_build_host_key_fingerprint=<fingerprint from 3a>
```

The key has **no passphrase** (it was made that way on lighthouse, for
`rpmsign --addsign` in a pipeline). It is protected by the keyring directory's
mode, the build user's account and the host's ssh access, and by nothing else —
the trade-off `packaging/rpm/README.md` records. The OpenBao path
(`sign-rpms.sh`, lab-gitops `satellite-content/playbooks/channels.yml`) is
still the harder version, and switching to it later changes nothing here but
the keyring that is not used any more.

## 4. Prove a release from the new host

The pipelines default to `acc-build` now (`BUILD_HOST`, `BUILD_ROOT=/srv/acc-build`);
nothing is passed on the command line. From the operator's workstation:

```bash
packaging/rpm/release-pipeline.sh v<next> --standalone-key --dry-run    # says acc-build:/srv/acc-build/acc-rpm-<v>
packaging/rpm/release-pipeline.sh v<next> --standalone-key              # tag → acc-build → sign → Satellite → acc1 agrees
ssh acc1 'rpm -K /var/cache/dnf/acc-spearhead-*/packages/acc-<v>*.rpm'  # the signature verifies against the channel's key
packaging/images/build-images.sh v<next> --with-operator --dry-run      # the images, same host
packaging/rpm/mock-matrix.sh                                             # on acc-build: every chroot, both arches
```

The proof row in the proposal: `release-pipeline.sh` end to end from `acc-build`,
acc1 upgrades from the Satellite, `rpm -q` and `acc --version` agree, the
signature verifies against the served key.

## 5. Retire lighthouse as a build host

Only after step 4 has passed:

```bash
ssh lighthouse 'rm -rf /git/ml/agentic/acc-rpm-* /git/ml/agentic/acc-images-* /git/ml/agentic/acc-image-* /git/ml/agentic/pip-cache /git/ml/agentic/tmp'
ssh lighthouse 'gpg --homedir /home/flg/.acc-rpm-gpg --batch --yes --delete-secret-and-public-keys "$(gpg --homedir /home/flg/.acc-rpm-gpg --batch --list-secret-keys --with-colons | awk -F: '"'"'$1=="fpr"{print $10; exit}'"'"')" && shred -u /home/flg/.acc-rpm-gpg/private-keys-v1.d/* 2>/dev/null; rm -rf /home/flg/.acc-rpm-gpg'
```

lighthouse keeps serving models (`deploy/host-configs/lighthouse-*.yaml`) and
nothing else from this pipeline. `deploy/host-configs/README.md` says so.

## 6. Verify after the fact, any time

```bash
ssh acc-build bash -s < packaging/build-host/ansible/acc-build-host/files/verify-build-host.sh
cd ansible/acc-build-host && ansible-playbook site.yml -e acc_build_host_operation=status
```

## Deliberately out of scope

- **The OpenBao-held key.** D2 moves the key as it is; the switch stays available.
- **GitHub Actions / a hosted runner.** The spearhead tree and the Satellite
  are only reachable from the lab; a hosted runner would need a self-hosted
  agent inside it, which is this VM with extra moving parts.
- **The public channel's builder.** That is COPR (Phase 2/4), not this host;
  `copr-cli` is installed so the host *can* trigger builds, but
  `release-copr.sh` runs from the workstation.
- **ARM on the Satellite channel** (proposal decision 3): the aarch64 chroots
  are provisioned, `mock-matrix.sh` builds them, and whether their packages
  enter the Satellite channel is the operator's call.

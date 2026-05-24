# Ansible / AAP ops (aap1)

Declarative, re-runnable operations for the blackbox3 bring-up — wired as
**AAP Job Templates** on `aap1` so repetitive steps are one click and don't
burn agent tokens.

| Playbook | Job Template | Does |
|----------|-------------|------|
| `bringup.yml` | "ACC – bring up operator (blackbox3)" | trigger Tekton build → wait for CSV Succeeded → apply AgentCorpus/Collective |
| `smoke.yml` | "ACC – smoke check (blackbox3)" | assert CSV Succeeded + corpus Ready + list agent pods |

## AAP setup (once)

1. **Project**: point at this repo, branch `main`, playbook dir `gitops/ansible`.
2. **Execution environment**: one with the `kubernetes.core` collection
   (`ansible-galaxy collection install kubernetes.core` or a custom EE).
3. **Credential**: an OpenShift/Kubernetes API credential (or a `KUBECONFIG`
   for blackbox3) attached to the Job Templates.
4. **Job Templates**: one per playbook above; expose `git-revision`/`version`
   as survey vars if you want to parameterise the build.

## Run order

`bring up operator` → (watch) → `smoke check`. Both are idempotent. ArgoCD can
own the steady-state (catalog + corpus) while AAP owns the imperative build
kickoff + verification.

> These playbooks assume the GitOps manifests in `../olm` and `../samples`
> and the Tekton `../tekton/pipelinerun.yaml`. Paths are relative to this dir.

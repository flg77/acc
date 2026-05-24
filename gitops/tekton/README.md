# Tekton build pipeline

Builds the ACC operator image → OLM bundle → opm index, all pushed to the
blackbox3 **internal registry** (`image-registry.openshift-image-registry.svc:5000/acc-operator`).

## One-time setup (on blackbox3)

```bash
oc new-project acc-operator-system
# Pipeline SA with permission to push to the internal registry namespace
oc create sa pipeline -n acc-operator-system
oc policy add-role-to-user system:image-builder \
  system:serviceaccount:acc-operator-system:pipeline -n acc-operator
# OpenShift Pipelines (Tekton) operator must be installed (provides the
# git-clone + buildah ClusterTasks this Pipeline references).
oc apply -f gitops/tekton/pipeline.yaml
```

## Run

```bash
oc create -f gitops/tekton/pipelinerun.yaml     # generateName → a fresh run
tkn pipelinerun logs -f -n acc-operator-system  # follow
```

Or let Ansible/AAP kick it (`gitops/ansible/bringup.yml`).

## Notes / cluster-specific knobs

- **ClusterTask vs resolver** — recent OpenShift Pipelines removes `ClusterTask`.
  If `taskRef.kind: ClusterTask` fails, switch to the resolver form
  (`resolver: cluster`) or install the tasks from Artifact Hub.
- **opm index + push** — `opm index add --container-tool none` emits a
  Dockerfile; the simplest portable variant builds + pushes that with a final
  `buildah` step. The `build-index` task here runs opm; wire the push to your
  registry the same way `build-operator` does (it's commented in pipeline.yaml).
  Equivalent local command: `make -C operator catalog-build catalog-push
  CATALOG_IMG=...:5000/acc-operator/acc-operator-index:0.1.0`.
- **TLSVERIFY=false** is set for the in-cluster registry service hostname; tighten
  with the service CA bundle for production.

#!/usr/bin/env bash
# acc-operator-ops.sh — repeatable runbook for the ACC operator on the RHOAI/OLM
# cluster (acc1). Captures the tasks done in the 2026-06-16 webgui-crash session:
#   diagnose a CrashLoopBackOff, pre-flight the operator image, cut + ship a new
#   operator version through OLM, verify, and the emergency live unblock.
#
# RUNS FROM THE WORKSTATION: every cluster/build step is executed on acc1 over
# SSH (the workstation has no oc/podman/Go). If you run it ON acc1, set
# ACC_LOCAL=1 to skip SSH.
#
# Secrets come ONLY from your environment / your own `podman login` + `oc login`
# on acc1 — this script never embeds tokens. The `push` + `ship` steps mutate
# shared infra and your private registry: run them yourself, deliberately.
#
# Usage:
#   ./acc-operator-ops.sh diagnose [deploy] [container]   # default: acc-demo-coding-webgui webgui
#   ./acc-operator-ops.sh preflight                       # build operator image locally on acc1 (no push)
#   ./acc-operator-ops.sh build                           # build+push operator/bundle/index at VER (NEEDS quay login)
#   ./acc-operator-ops.sh ship                            # patch CatalogSource -> index-VER; watch OLM upgrade
#   ./acc-operator-ops.sh verify                          # CSV upgraded + webgui env wired + pod healthy
#   ./acc-operator-ops.sh release                         # build + ship + verify
#   ./acc-operator-ops.sh hotpatch                        # fast dev: build+push operator image only, patch the CSV deploy image
#   ./acc-operator-ops.sh unblock [deploy]                # EMERGENCY: scale operator->0 + inject NATS env (reverted by next reconcile)
#   ./acc-operator-ops.sh rebuild-operator-up             # scale operator back to 1
#   ./acc-operator-ops.sh fleet-log "<message>"           # manually append a FLEET decisions-log entry + push
#
# `release` auto-appends a FLEET decisions-log line on success (opt out FLEET_LOG=0).
#
# Key knobs (override via env):
#   VER=0.2.10            # version to cut (required for build/ship/release/hotpatch)
#   PREV=<auto>           # version being replaced (auto-detected from the live CSV)
#   NS=acc-demo           # workload namespace
#   OPNS=acc-system       # operator namespace
#   MKTNS=openshift-marketplace
#   REG=quay.io/flg77/acc_images
set -euo pipefail

# ---- config ---------------------------------------------------------------
ACC1_HOST="${ACC1_HOST:-acc1.ic3net.internal}"
ACC1_USER="${ACC1_USER:-flg}"
ACC1_KEY="${ACC1_KEY:-$HOME/.ssh/rsa-key-acc1}"
# Derive the repo root from this script's location (<repo>/operator/hack/). The
# /git mount is shared workstation<->acc1, so the path is valid on both. Falls
# back to the canonical path when run from outside a checkout (e.g. piped).
_self="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
case "$_self" in */operator/hack) _repo="${_self%/operator/hack}";; *) _repo="/git/development/agentic/acc-spearhead";; esac
REPO="${REPO:-$_repo}"
NS="${NS:-acc-demo}"
OPNS="${OPNS:-acc-system}"
MKTNS="${MKTNS:-openshift-marketplace}"
REG="${REG:-quay.io/flg77/acc_images}"
CATSRC="${CATSRC:-acc-catalog}"
SUB="${SUB:-acc-operator}"
GO_TOOLSET="${GO_TOOLSET:-registry.access.redhat.com/ubi10/go-toolset:10.0}"
HARNESS="${ACC_HARNESS_DIR:-${HARNESS:-/git/development/agentic/acc-dev-harness}}"
VER="${VER:-}"

say() { printf '\n\033[1m### %s\033[0m %s\n' "$*" "$(date +%T)"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# Run a command string on acc1 (or locally with ACC_LOCAL=1). All local vars
# are expanded before sending; use \$ for remote-shell vars inside heredocs.
run() {
  if [ "${ACC_LOCAL:-0}" = 1 ]; then
    bash -c "$1"
  else
    ssh -i "$ACC1_KEY" -o StrictHostKeyChecking=no "$ACC1_USER@$ACC1_HOST" "$1" \
      2> >(grep -v 'Agent pid' >&2)
  fi
}

need_ver() { [ -n "$VER" ] || die "set VER=<x.y.z> (the version to cut)"; }

# Append a one-line entry to the FLEET decisions log + push (acc-fleet skill).
# Runs LOCALLY (git auth lives on whoever runs this), pull-before-edit, append-
# only. Best-effort: never fails the release. Opt out with FLEET_LOG=0.
fleet_log() {
  [ "${FLEET_LOG:-1}" = 1 ] || return 0
  local ledger="$HARNESS/coordination/FLEET.md"
  [ -f "$ledger" ] || { echo "fleet: ledger not at $ledger — skipping log"; return 0; }
  local entry="- **$(date +%F) (workstation) — $1**"
  say "fleet: appending decisions-log entry + pushing"
  (
    cd "$HARNESS"
    git pull --ff-only --quiet || true
    awk -v e="$entry" '{print} /^## Decisions log \(append-only\)/ && !d {print e; d=1}' \
      "$ledger" > "$ledger.tmp" && mv "$ledger.tmp" "$ledger"
    git commit -aqm "fleet: operator ${VER:-?} shipped (acc-operator-ops.sh)" && git push --quiet
  ) || echo "fleet: log/push failed (non-fatal)"
}

# Auto-detect the currently installed CSV version (the one we replace).
current_ver() {
  run "oc -n '$OPNS' get csv -o name | sed -n 's#.*/${SUB}.v##p' | sort -V | tail -1"
}

# ---- subcommands ----------------------------------------------------------

diagnose() {
  local deploy="${1:-acc-demo-coding-webgui}" ctr="${2:-webgui}"
  say "diagnose $deploy (container: $ctr) in $NS"
  run "
    set -e
    POD=\$(oc -n '$NS' get pod -o name --sort-by=.metadata.creationTimestamp | grep '$deploy' | tail -1 | cut -d/ -f2)
    [ -n \"\$POD\" ] || { echo 'no pod found for $deploy'; exit 1; }
    echo \"pod: \$POD\"
    echo '--- containers (name -> ready/restarts/lastExit) ---'
    oc -n '$NS' get pod \"\$POD\" -o jsonpath='{range .status.containerStatuses[*]}{.name}{\": ready=\"}{.ready}{\" restarts=\"}{.restartCount}{\" lastExit=\"}{.lastState.terminated.exitCode}{\"/\"}{.lastState.terminated.reason}{\"\n\"}{end}'
    echo '--- current logs ('$ctr') ---'
    oc -n '$NS' logs \"\$POD\" -c '$ctr' --tail=40 2>&1 || true
    echo '--- previous (crashed) logs ('$ctr') ---'
    oc -n '$NS' logs -p \"\$POD\" -c '$ctr' --tail=40 2>&1 || true
    echo '--- env ('$ctr') ---'
    oc -n '$NS' get pod \"\$POD\" -o jsonpath='{range .spec.containers[?(@.name==\"$ctr\")].env[*]}{.name}{\"=\"}{.value}{\"\n\"}{end}'
    echo '--- NATS/COLLECTIVE env on the (healthy) TUI for comparison ---'
    oc -n '$NS' set env deploy/\$(oc -n '$NS' get deploy -o name | sed 's#.*/##' | grep -m1 tui) --list 2>/dev/null | grep -E 'NATS|COLLECTIVE' || true
  "
}

preflight() {
  say "pre-flight: build operator image locally on acc1 (no push)"
  run "cd '$REPO/operator' && make docker-build IMG=localhost/acc-operator:preflight CONTAINER_TOOL=podman && echo PREFLIGHT_OK && podman images localhost/acc-operator:preflight"
}

build() {
  need_ver
  local prev="${PREV:-$(current_ver)}"
  [ -n "$prev" ] || die "could not detect PREV; set PREV=<x.y.z>"
  say "build + push operator $VER (replaces $prev) -> $REG"
  # Guard: confirm a quay login exists (the push fails otherwise — and the agent
  # can't do it for you; this must be your authenticated session).
  run "podman login --get-login quay.io >/dev/null 2>&1 || { echo 'NOT LOGGED IN: run  podman login quay.io  first'; exit 1; }"
  run "
    set -euo pipefail
    cd '$REPO/operator'

    echo '### 1/4 operator image (go build runs in-container via Containerfile)'
    make docker-build IMG='$REG:acc-operator-$VER' CONTAINER_TOOL=podman
    podman push '$REG:acc-operator-$VER'

    echo '### 2/4 regenerate bundle manifests at $VER (Go tooling -> go-toolset container)'
    podman run --rm --user 0 -e HOME=/tmp -e GOFLAGS=-mod=mod -e GOCACHE=/tmp/gocache -e GOPATH=/tmp/gopath \
      -v \"\$PWD\":/w:Z -w /w '$GO_TOOLSET' bash -lc 'make bundle VERSION=$VER IMG=$REG:acc-operator-$VER'
    # Force the OLM upgrade edge to the version actually live on the cluster.
    sed -i 's#^  replaces: .*#  replaces: ${SUB}.v$prev#' bundle/manifests/*clusterserviceversion.yaml
    grep -E '^  (version|replaces):|containerImage:' bundle/manifests/*clusterserviceversion.yaml | head

    echo '### 3/4 bundle image'
    make bundle-build BUNDLE_IMG='$REG:acc-operator-bundle-$VER' CONTAINER_TOOL=podman
    podman push '$REG:acc-operator-bundle-$VER'

    echo '### 4/4 catalog index (carry the upgrade graph from index-$prev)'
    make catalog-build OPM=\"\$PWD/bin/opm\" CONTAINER_TOOL=podman \
      CATALOG_IMG='$REG:acc-operator-index-$VER' \
      BUNDLE_IMG='$REG:acc-operator-bundle-$VER' \
      FROM_INDEX='$REG:acc-operator-index-$prev'
    podman push '$REG:acc-operator-index-$VER'
    echo BUILD_PUSH_OK
  "
}

ship() {
  need_ver
  say "ship: point CatalogSource $CATSRC at index-$VER (Automatic Subscription upgrades)"
  run "oc -n '$MKTNS' patch catalogsource '$CATSRC' --type=merge -p '{\"spec\":{\"image\":\"$REG:acc-operator-index-$VER\"}}'"
  say "watching install (Ctrl-C is safe; OLM continues server-side)"
  run "
    for i in \$(seq 1 40); do
      CSV=\$(oc -n '$OPNS' get sub '$SUB' -o jsonpath='{.status.installedCSV}' 2>/dev/null)
      PH=\$(oc -n '$OPNS' get csv \"\$CSV\" -o jsonpath='{.status.phase}' 2>/dev/null)
      echo \"  installedCSV=\$CSV phase=\$PH\"
      [ \"\$CSV\" = '${SUB}.v$VER' ] && [ \"\$PH\" = Succeeded ] && { echo UPGRADE_OK; break; }
      sleep 15
    done
  "
}

verify() {
  say "verify: operator version + webgui signaling env + pod health"
  run "
    echo '--- operator CSV ---'
    oc -n '$OPNS' get csv | grep '$SUB' || true
    echo '--- webgui container env (expect ACC_NATS_URL + ACC_COLLECTIVE_IDS) ---'
    oc -n '$NS' set env deploy/acc-demo-coding-webgui --list 2>/dev/null | grep -E 'NATS|COLLECTIVE' || echo '(no ACC_NATS_URL/COLLECTIVE env on the webgui deploy yet — operator fix not landed)'
    echo '--- webgui pod (expect 2/2 Running) ---'
    oc -n '$NS' get pod | grep webgui || true
  "
}

release() {
  need_ver
  PREV="${PREV:-$(current_ver)}"; export PREV
  build; ship; verify
  fleet_log "operator $VER shipped via acc-operator-ops.sh (replaces ${PREV:-?}). CatalogSource $CATSRC -> $REG:acc-operator-index-$VER; Subscription $SUB Automatic upgrade. Carries webgui NATS wiring + observer resilience (PR #98)."
}

hotpatch() {
  need_ver
  local tag="acc-operator-$VER-dev"
  say "hotpatch (fast dev): build+push $tag, patch the CSV deploy image"
  run "podman login --get-login quay.io >/dev/null 2>&1 || { echo 'NOT LOGGED IN: run  podman login quay.io  first'; exit 1; }"
  run "
    set -euo pipefail
    cd '$REPO/operator'
    make docker-build IMG='$REG:$tag' CONTAINER_TOOL=podman
    podman push '$REG:$tag'
    CSV=\$(oc -n '$OPNS' get sub '$SUB' -o jsonpath='{.status.installedCSV}')
    echo \"patching CSV \$CSV deployment image -> $REG:$tag (OLM reconciles the Deployment from the CSV)\"
    oc -n '$OPNS' patch csv \"\$CSV\" --type=json -p \
      '[{\"op\":\"replace\",\"path\":\"/spec/install/spec/deployments/0/spec/template/spec/containers/0/image\",\"value\":\"$REG:$tag\"}]'
    oc -n '$OPNS' rollout status deploy/${SUB}-controller-manager --timeout=120s || true
  "
  printf '\033[33mNOTE: a CatalogSource re-sync will revert this hand-edit. Use `release` to ship for real.\033[0m\n'
}

unblock() {
  local deploy="${1:-acc-demo-coding-webgui}"
  local corpus="${deploy%-webgui}"     # acc-demo-coding-webgui -> acc-demo-coding
  say "EMERGENCY unblock: scale operator->0, inject NATS env into $deploy"
  printf '\033[33mThis pauses ALL operator reconciliation (every corpus) and is reverted once you scale the operator back up. Prefer `release`.\033[0m\n'
  run "
    set -e
    oc -n '$OPNS' scale deploy/${SUB}-controller-manager --replicas=0
    oc -n '$OPNS' rollout status deploy/${SUB}-controller-manager --timeout=60s || true
    oc -n '$NS' set env deploy/'$deploy' -c webgui \
       ACC_NATS_URL=nats://${corpus}-nats:4222 \
       ACC_COLLECTIVE_IDS=${corpus}-ws
    oc -n '$NS' rollout status deploy/'$deploy' --timeout=120s || true
  "
  printf '\033[33mRemember: ./acc-operator-ops.sh rebuild-operator-up   (scale the operator back to 1)\033[0m\n'
}

rebuild-operator-up() {
  say "scale operator back to 1"
  run "oc -n '$OPNS' scale deploy/${SUB}-controller-manager --replicas=1 && oc -n '$OPNS' rollout status deploy/${SUB}-controller-manager --timeout=120s"
}

# ---- dispatch -------------------------------------------------------------
cmd="${1:-}"; shift || true
case "$cmd" in
  diagnose)              diagnose "$@" ;;
  preflight)             preflight ;;
  build)                 build ;;
  ship)                  ship ;;
  verify)                verify ;;
  release)               release ;;
  hotpatch)              hotpatch ;;
  unblock)               unblock "$@" ;;
  rebuild-operator-up)   rebuild-operator-up ;;
  fleet-log)             fleet_log "$*" ;;
  *) sed -n '2,40p' "$0"; exit 2 ;;
esac

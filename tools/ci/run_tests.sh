#!/usr/bin/env bash
# The one test entry point every pipeline calls, so GitHub Actions
# (.github/workflows/ci.yml) and the in-house Tekton acc-build gate
# (lab-gitops tekton/acc-build) run the same thing and cannot drift.
# Backlog CI-01: both ways stay; in-house must be able to test everything.
#
#   tools/ci/run_tests.sh gate  [pytest args...]   # 061 role QA gate + capability checksums (~1 min)
#   tools/ci/run_tests.sh suite [pytest args...]   # everything but tests/container
#
# Extra args go to pytest (e.g. --junitxml=pytest.xml, or the Tekton pod's
# --deselect list). tests/container is not here: those tests build images or
# drive a compose stack, and never run on a host with a live ACC stack.
set -euo pipefail
cd "$(dirname "$0")/../.."

what="${1:-}"
[ $# -gt 0 ] && shift
pytest_cmd=(python -m pytest -o addopts= -p no:cacheprovider -q)

case "$what" in
  gate)
    python -m acc.pkg.builtin_capabilities --check
    "${pytest_cmd[@]}" \
      tests/test_role_profiles_posture.py \
      tests/test_capability_verify.py \
      tests/pkg/test_capability_digests.py \
      tests/test_agent_bom_capabilities.py \
      "$@"
    ;;
  suite)
    "${pytest_cmd[@]}" --ignore=tests/container tests "$@"
    ;;
  *)
    echo "usage: $0 gate|suite [pytest args...]" >&2
    exit 2
    ;;
esac

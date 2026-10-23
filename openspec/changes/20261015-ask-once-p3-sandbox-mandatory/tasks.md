# 20261015-ask-once-p3-sandbox-mandatory — tasks

- [x] 1.1 `ACC_SANDBOX_REQUIRED`, `sandbox_required()`; fail closed in `maybe_run_sandboxed`
- [x] 1.2 doctor `sandbox`: BROKEN / DRIFT / OK
- [x] 1.3 confinement: sandboxed exec is not a system-access question under AUTO / ASK_ONCE
- [x] 1.4 tests: `test_058_p3_sandbox_mandatory.py`, `test_preflight.py`, `test_sandbox_runner.py`; conftest opt-out
- [x] 2.1 `deploy/openshell/` provisioning kit
- [ ] 2.2 **[op]** run `setup-edge-sandbox.sh` on lighthouse (bind mounts: operator decision)
- [ ] 2.3 **[op]** agent image with the `sandbox` feature; apply the compose overlay; `doctor --check sandbox` OK
- [ ] 2.4 re-run the getdone baseline with the sandbox: expect 0 asks for exec, 1 mission card for `fs_write`

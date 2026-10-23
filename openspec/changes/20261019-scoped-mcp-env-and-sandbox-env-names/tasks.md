# 20261019-scoped-mcp-env-and-sandbox-env-names — tasks

## 1. Scoped stdio environment
- [x] `StdioTransport`: base allowlist + declared `env:`; empty value = pass through; absent name left out
- [x] Manifest docstring and the two bundled manifests' comments say what an empty value means
- [x] Tests in `tests/test_mcp_stdio_transport.py`

## 2. Sandbox env names
- [x] `acc/configschema.py`: names from `acc.sandbox.runner`; add `ACC_SANDBOX_REQUIRED`, `ACC_OPENSHELL_BIN`; drop `OPENSHELL_BIN`; `REQUIRES` on `ACC_SANDBOX_NAME`
- [x] `acc/preflight.py` `check_sandbox`: enabled via `SandboxConfig.from_env()` over process env + env file
- [x] Tests: every runner `ENV_*` in the schema; `ACC_SANDBOX_ENABLED=0` in the env file is off

## Verification
- [x] `pytest tests/test_mcp_stdio_transport.py tests/test_sandbox_runner.py tests/test_preflight.py tests/test_058_p3_sandbox_mandatory.py tests/test_configschema*.py tests/test_configstore*.py`

"""acc.sandbox — OpenShell kernel-enforced execution delegation (Model 2).

The OpenShell integration's Phase-3 enforcement path (ACC Implementation
proposal 051). Model 1 ("cage the whole agent pod") proved infeasible — an
OpenShell sandbox is a single-image runtime, so it cannot carry ACC's rich
multi-container agent pod. Model 2 keeps the agent as its normal pod and
delegates CODE EXECUTION (shell/python/ssh_exec + MCP tools) to
gateway-created OpenShell sandboxes carrying the corpus's Cat-A/B/C policy —
the primary untrusted surface (what the agent runs) is caged at the kernel.

This package is the runtime shim; wiring it into the exec skills is a later
slice. It is inert until an agent is provisioned with gateway access.
"""

from __future__ import annotations

from acc.sandbox.runner import ExecResult, SandboxConfig, run_in_sandbox

__all__ = ["ExecResult", "SandboxConfig", "run_in_sandbox"]

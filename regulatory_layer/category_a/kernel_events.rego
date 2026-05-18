# regulatory_layer/category_a/kernel_events.rego
# ==========================================================================
# ACC Kernel-Event Rules — Category A (proposal 015, Phase 3)
# ==========================================================================
#
# These rules judge KERNEL_EVENT signals — process/file/network evidence
# observed BELOW the application layer by a runtime-security backend
# (RHACS / Falco / Tetragon / NetObserv) and republished onto the bus by
# the runtime-evidence bridge.
#
# Where the constitutional rules in constitutional_rhoai.rego judge
# signal metadata the agent process itself supplies (forgeable), these
# rules judge what the process ACTUALLY did (ground truth).
#
# This file is the OPA-equivalent REFERENCE of the runtime evaluator
# acc.governance.KernelEventEvaluator.  The agent runs the Python
# evaluator inline (the kernel rule set is small and stable); this Rego
# is kept in lock step for operators who route KERNEL_EVENTs through a
# central OPA, and as the canonical statement of intent.
#
# Version: 0.1.0
# Date:    2026-05-16
# ==========================================================================

package acc.membrane.kernel_events

import future.keywords.if
import future.keywords.in

# --------------------------------------------------------------------------
# Tunable allow-sets.  Operators narrow these from the observe-window log
# (proposal 015 §5).  Keep in lock step with
# acc.governance.DEFAULT_ALLOWED_BINARY_PREFIXES.
# --------------------------------------------------------------------------

allowed_binary_prefixes := [
	"/usr/bin/python",
	"/usr/local/bin/python",
	"/usr/bin/",
	"/bin/",
	"/app/",
]

# --------------------------------------------------------------------------
# K-001 — execve of a binary outside the agent image's known paths.
# A drifted or compromised agent spawning an unexpected binary.
# --------------------------------------------------------------------------

deny_unexpected_execve contains msg if {
	input.event.hook == "execve"
	binary := input.event.detail.binary
	binary != ""
	not binary_allowed(binary)
	msg := sprintf("K-001: unexpected execve of %q", [binary])
}

binary_allowed(binary) if {
	some prefix in allowed_binary_prefixes
	startswith(binary, prefix)
}

# --------------------------------------------------------------------------
# K-002 — openat on /proc/<pid>/mem.  Reading another process's memory
# is unambiguously malicious; no legitimate agent path does this.
# --------------------------------------------------------------------------

deny_proc_mem_read contains msg if {
	input.event.hook == "openat"
	path := input.event.detail.path
	startswith(path, "/proc/")
	endswith(path, "/mem")
	msg := sprintf("K-002: openat on process memory %q", [path])
}

# --------------------------------------------------------------------------
# K-003 — outbound connect.  Recorded as evidence; NOT denied inline —
# enforcing it needs the approved-CIDR context (proposal 015 §8 Q3).
# Surfaced here so a central-OPA deployment can choose to act on it.
# --------------------------------------------------------------------------

observe_connect contains msg if {
	input.event.hook == "connect"
	dst := input.event.detail.dst_ip
	dst != ""
	msg := sprintf("K-003: outbound connect to %s", [dst])
}

# --------------------------------------------------------------------------
# Aggregate verdict.
# --------------------------------------------------------------------------

violations := deny_unexpected_execve | deny_proc_mem_read

# allow_signal is false when any enforced rule fired.
default allow_signal := true

allow_signal := false if {
	count(violations) > 0
}

"""ACC integrations — thin, shared helpers for the role-facing integration
skills/MCPs (messenger / office / speech).

Kept deliberately small: the heavy protocol work is consumed from upstream
projects; this package holds the in-process glue (HTTP, credential brokering)
that ACC's governed skills/MCPs call.  See the ACC-PR integration proposals.
"""

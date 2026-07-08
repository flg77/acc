"""``acc.lib`` — bundled optional libraries that support ACC skills/roles.

Code here is deliberately kept *out of the runtime core* (``acc.cognitive_core``,
``acc.config``, the agent/arbiter loop): these are self-contained,
dependency-light libraries that a skill adapter, a memory indexer, or a role can
import, without being part of the always-on runtime.  Adding a library here
signals "supporting capability, not core" — a home for OKF today and further
libraries later.
"""

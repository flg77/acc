"""Echo skill — trivial round-trip adapter.

Doubles as the canary for the loader: if `EchoSkill` fails to import or
instantiate, the registry's discovery pipeline is broken at a layer
that doesn't depend on any external service.

Cat-A safety: pure-function, no side effects, no I/O.  Risk level
`LOW` per the manifest.
"""

from __future__ import annotations

from typing import Any

from acc.skills import Skill


class EchoSkill(Skill):
    """Return the supplied text verbatim plus its length."""

    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args["text"]  # already validated as a non-empty string by the registry
        return {"echo": text, "length": len(text)}

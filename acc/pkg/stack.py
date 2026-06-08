"""`acc-deploy.sh new-stack` generator — emit a stack's collective.yaml.

A *stack* = base + chosen packs + a collective + a runtime profile. This
module produces the validated `collective.yaml` (the agents that run + the
`required_packages` to provision); `acc-deploy.sh new-stack` wraps it with
the flavour build (roles baked) + tag/push to the registry.

`deploy_mode` is NOT a collective.yaml field (it lives in acc-config / the
`ACC_DEPLOY_MODE` env) — `new-stack` passes it to the build/profile, not here.
Per D1, governance is always *available* (baked); the **profile** chooses
which control agents actually RUN.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import yaml

from acc.collective import CollectiveSpec

CONTROL_FULL = (
    "arbiter", "assistant", "compliance_officer", "ingester",
    "observer", "orchestrator", "reviewer",
)

# Profile -> which control agents RUN (governance is always baked/available).
PROFILES: dict[str, tuple[str, ...]] = {
    "edge-min": ("arbiter", "ingester", "observer"),
    "edge": ("arbiter", "ingester", "observer", "orchestrator"),
    "full": CONTROL_FULL,
    "dc": CONTROL_FULL,
}


def generate_stack(
    name: str, *, packs: list[str], agents: list[str] | None = None,
    profile: str = "full", replicas: int = 1, cluster_id: str = "dom",
) -> dict[str, Any]:
    """Build + validate a collective spec dict for a stack.

    Raises if ``profile`` is unknown or the resulting spec is invalid
    (e.g. a malformed `required_packages` entry).
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; known: {sorted(PROFILES)}")
    control = PROFILES[profile]
    agent_specs: list[dict[str, Any]] = [
        {"role": r, "cluster_id": "ctl", "replicas": 1} for r in control
    ]
    for r in (agents or []):
        agent_specs.append({"role": r, "cluster_id": cluster_id, "replicas": replicas})
    spec = {
        "collective_id": name,
        "agents": agent_specs,
        "required_packages": list(packs),
    }
    CollectiveSpec.model_validate(spec)   # fail fast on an invalid stack
    return spec


def render_stack_yaml(name: str, **kw: Any) -> str:
    return yaml.safe_dump(generate_stack(name, **kw), sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m acc.pkg.stack",
        description="Emit a stack's collective.yaml (used by acc-deploy.sh new-stack).",
    )
    p.add_argument("--name", required=True)
    p.add_argument("--packs", default="", help="space- or comma-separated @scope/name@constraint")
    p.add_argument("--agents", default="", help="space- or comma-separated domain role names")
    p.add_argument("--profile", default="full", choices=sorted(PROFILES))
    p.add_argument("--replicas", type=int, default=1)
    p.add_argument("--cluster-id", default="dom")
    p.add_argument("--out", default="-", help="output path, or - for stdout")
    args = p.parse_args(argv)

    def _split(s: str) -> list[str]:
        return [t for t in s.replace(",", " ").split() if t]

    try:
        text = render_stack_yaml(
            args.name, packs=_split(args.packs), agents=_split(args.agents),
            profile=args.profile, replicas=args.replicas, cluster_id=args.cluster_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out == "-":
        sys.stdout.write(text)
    else:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
        print(args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Instances — a collective bound to an owner, a posture and its own state.

`20260906-acc-instance` (HG-40.1a). A *deployment profile* (``acc/profiles.py``)
is a configuration posture; a *TUI profile* is a view. Neither isolates state.
An **instance** is what Hermes means by a profile: a collective that carries
its own memory, sessions, trace and overlays, is owned by one principal the
substrate vouches for, and runs under one posture.

Nothing here is a new isolation mechanism. The runtime already partitions by
``collective_id`` — NATS subjects ``acc.<cid>.*``, Redis keys ``acc:<cid>:…``,
the LanceDB ``collective_id`` column — and the two roots that are *not*
partitioned that way (the tracelog, the TUI's sessions) are plain directories
read from the environment. An instance is the **binding**: one directory that
holds the collective definition (the installed set), the overlays and every
state root, plus ``instance.yaml`` naming the owner, the posture and the hub.
The cells of an instance mount that directory and get their roots from it.

State is never exported. ``export_instance`` carries the posture, the
installed set and the overlays — what makes the instance *this* instance —
and lists what it left behind. The signature field is reserved (the profiles
spec §12.3): the moment an archive travels, signing is live.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.instances")

INSTANCES_DIR_VAR = "ACC_INSTANCES_DIR"
DEFAULT_INSTANCES_DIR = "instances"
INSTANCE_FILE = "instance.yaml"
COLLECTIVE_FILE = "collective.yaml"

#: Where an instance directory is mounted inside its cells.
CONTAINER_ROOT = "/app/instances"
#: The host path of the instances directory relative to the compose file
#: (``container/production/``), the way the base compose reaches ``../../logs``.
HOST_PREFIX = "../../instances"

#: The state an instance owns. ``overlays`` holds ``collective.md`` (the
#: instance's voice above the shared roles); the rest is runtime state.
STATE_DIRS = ("lancedb", "sessions", "trace", "overlays")

#: Sources whose principals may own an instance. An external identity nothing
#: vouches for cannot own one, for the same reason it cannot be an operator.
OWNER_SOURCES = ("kubernetes", "system", "web", "webgui")

_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")


class InstanceError(Exception):
    """An operator-facing refusal."""


@dataclass
class Instance:
    """The binding: collective id ↔ owner ↔ posture ↔ state roots."""

    id: str
    owner: str                       # ``source:subject`` — a Principal attribution
    profile: str = ""                # deployment profile (posture); "" = none
    hub: str = ""                    # hub collective id; "" = standalone
    stack_profile: str = "edge"      # which control agents run (acc.pkg.stack.PROFILES)
    created_by: str = ""
    created_at: float = 0.0
    note: str = ""
    archived: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner,
            "profile": self.profile,
            "hub": self.hub,
            "stack_profile": self.stack_profile,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "note": self.note,
            "archived": self.archived,
        }


@dataclass
class Export:
    """What ``export_instance`` produces: the definition, never the state."""

    document: dict[str, Any]
    state_excluded: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def instances_dir(repo_root: Path | None = None) -> Path:
    raw = os.environ.get(INSTANCES_DIR_VAR, "").strip()
    if raw:
        return Path(raw)
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / DEFAULT_INSTANCES_DIR


def instance_dir(instance_id: str, repo_root: Path | None = None) -> Path:
    return instances_dir(repo_root) / instance_id


def list_instances(repo_root: Path | None = None) -> list[str]:
    directory = instances_dir(repo_root)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if (p / INSTANCE_FILE).is_file())


def load_instance(instance_id: str, repo_root: Path | None = None) -> Instance:
    """Read one instance.

    Raises:
        InstanceError: missing or unreadable.
    """
    path = instance_dir(instance_id, repo_root) / INSTANCE_FILE
    if not path.is_file():
        known = ", ".join(list_instances(repo_root)) or "(none)"
        raise InstanceError(f"no instance {instance_id!r}. Known: {known}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise InstanceError(f"{path} is unreadable: {exc}") from exc
    if not isinstance(raw, dict) or not raw.get("owner"):
        raise InstanceError(f"{path} is not an instance record (no owner)")
    return Instance(
        id=str(raw.get("id") or instance_id),
        owner=str(raw["owner"]),
        profile=str(raw.get("profile", "") or ""),
        hub=str(raw.get("hub", "") or ""),
        stack_profile=str(raw.get("stack_profile", "edge") or "edge"),
        created_by=str(raw.get("created_by", "") or ""),
        created_at=float(raw.get("created_at", 0) or 0),
        note=str(raw.get("note", "") or ""),
        archived=bool(raw.get("archived", False)),
    )


def save_instance(inst: Instance, repo_root: Path | None = None) -> Path:
    path = instance_dir(inst.id, repo_root) / INSTANCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, yaml.safe_dump(inst.as_dict(), sort_keys=False, allow_unicode=True),
        mode=0o644, tmp_prefix=".instance.tmp.",
    )
    return path


def state_roots(inst: Instance, repo_root: Path | None = None) -> dict[str, Path]:
    """The instance's state directories on the host."""
    base = instance_dir(inst.id, repo_root)
    return {name: base / name for name in STATE_DIRS}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _check_id(instance_id: str) -> str:
    instance_id = str(instance_id or "").strip()
    if not _DNS_LABEL_RE.match(instance_id):
        raise InstanceError(
            f"instance id {instance_id!r} must be a DNS label (it is a collective id): "
            f"lowercase letters, digits, '-', at most 63 characters"
        )
    return instance_id


def _check_owner(owner: str) -> str:
    """``source:subject`` from a substrate that vouches for it."""
    owner = str(owner or "").strip()
    source, sep, subject = owner.partition(":")
    if not sep or not subject.strip():
        raise InstanceError(
            f"owner {owner!r} must be a principal attribution 'source:subject' "
            f"(e.g. kubernetes:alice, system:flg); acc-cli access whoami prints yours"
        )
    if source not in OWNER_SOURCES:
        raise InstanceError(
            f"owner {owner!r}: an instance is owned by a principal the substrate vouches "
            f"for ({', '.join(OWNER_SOURCES)}); an external identity cannot own one"
        )
    return owner


def _check_profile(profile: str, repo_root: Path | None) -> str:
    profile = str(profile or "").strip()
    if not profile:
        return ""
    from acc import profiles as P  # noqa: PLC0415
    try:
        P.load_profile(profile, repo_root)
    except P.ProfileError as exc:
        raise InstanceError(str(exc)) from exc
    return profile


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def create(
    instance_id: str,
    *,
    owner: str,
    profile: str = "",
    hub: str = "",
    packs: Iterable[str] = (),
    agents: Iterable[str] = (),
    stack_profile: str = "edge",
    created_by: str = "",
    note: str = "",
    repo_root: Path | None = None,
) -> Instance:
    """Create an instance: its record, its collective definition and its roots.

    The collective definition is what ``acc-deploy.sh new-stack`` would write —
    the control set for *stack_profile* plus *agents*, with *packs* as the
    installed set — under the instance's own directory, with the instance id
    as the collective id.

    Raises:
        InstanceError: a bad id / owner / profile, or the instance exists.
    """
    instance_id = _check_id(instance_id)
    owner = _check_owner(owner)
    profile = _check_profile(profile, repo_root)
    hub = str(hub or "").strip()
    if hub:
        _check_id(hub)
        if hub == instance_id:
            raise InstanceError("an instance cannot be its own hub")
    base = instance_dir(instance_id, repo_root)
    if (base / INSTANCE_FILE).exists():
        raise InstanceError(f"instance {instance_id!r} already exists at {base}")

    from acc.pkg.stack import generate_stack  # noqa: PLC0415
    try:
        spec = generate_stack(
            instance_id, packs=[str(p) for p in packs], agents=[str(a) for a in agents],
            profile=stack_profile,
        )
    except (ValueError, Exception) as exc:  # noqa: BLE001 -- pydantic or ours
        raise InstanceError(f"collective definition: {exc}") from exc

    inst = Instance(
        id=instance_id, owner=owner, profile=profile, hub=hub,
        stack_profile=stack_profile, created_by=created_by, created_at=time.time(),
        note=note,
    )
    for root in state_roots(inst, repo_root).values():
        root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        base / COLLECTIVE_FILE, yaml.safe_dump(spec, sort_keys=False),
        mode=0o644, tmp_prefix=".collective.tmp.",
    )
    overlay = base / "overlays" / "collective.md"
    if not overlay.exists():
        overlay.write_text(
            f"# {instance_id}\n\nOwned by {owner}. This file is the instance's voice above "
            f"the shared roles (an overlay, `collective` layer).\n",
            encoding="utf-8",
        )
    save_instance(inst, repo_root)
    logger.info("instances: created %s for %s (profile=%s hub=%s)",
                instance_id, owner, profile or "-", hub or "-")
    return inst


def archive(instance_id: str, repo_root: Path | None = None) -> Instance:
    """Mark an instance archived. Its state stays on disk — archiving is not
    erasure; ``acc-cli memory forget`` is."""
    inst = load_instance(instance_id, repo_root)
    inst.archived = True
    save_instance(inst, repo_root)
    return inst


# ---------------------------------------------------------------------------
# What the cells and the surfaces need
# ---------------------------------------------------------------------------


def cell_env(inst: Instance) -> dict[str, str]:
    """Environment for every cell of the instance (container paths).

    ``{aid}`` in a value is replaced per cell by the compose renderer.
    """
    root = f"{CONTAINER_ROOT}/{inst.id}"
    env = {
        "ACC_INSTANCE_ID": inst.id,
        "ACC_INSTANCE_OWNER": inst.owner,
        "ACC_COLLECTIVE_ID": inst.id,
        "ACC_LANCEDB_PATH": f"{root}/lancedb/{{aid}}",
        "ACC_TRACELOG_DIR": f"{root}/trace",
        "ACC_COLLECTIVE_DIR": f"{root}/overlays",
    }
    if inst.hub:
        env["ACC_HUB_COLLECTIVE_ID"] = inst.hub
    return env


#: What the cells mount, and how.  ``lancedb`` and ``trace`` are written by
#: the cells: ``U`` the way the base compose mounts its volumes -- under
#: rootless podman the cell runs as a sub-uid and a directory the host user
#: just created is not writable by it (the first lighthouse run crash-looped
#: every cell on ``Permission denied: …/lancedb/analyst-1``); ``U`` chowns
#: the mount to the cell's uid on start.  ``overlays`` is the operator's to
#: edit and the cells' to read: read-only, no chown.  ``sessions`` is the
#: TUI's on the host and is not mounted at all.  ``z`` relabels for SELinux.
CELL_MOUNTS = (("lancedb", "U,z"), ("trace", "U,z"), ("overlays", "ro,z"))


def cell_volumes(inst: Instance, *, host_prefix: str = HOST_PREFIX) -> list[str]:
    """The mounts every cell of the instance needs (see :data:`CELL_MOUNTS`)."""
    return [
        f"{host_prefix}/{inst.id}/{name}:{CONTAINER_ROOT}/{inst.id}/{name}:{opts}"
        for name, opts in CELL_MOUNTS
    ]


def surface_env(inst: Instance, repo_root: Path | None = None) -> dict[str, str]:
    """Environment for the operator surfaces on the host (TUI, acc-cli):
    the same instance, host paths."""
    roots = state_roots(inst, repo_root)
    return {
        "ACC_INSTANCE_ID": inst.id,
        "ACC_INSTANCE_OWNER": inst.owner,
        "ACC_COLLECTIVE_ID": inst.id,
        "ACC_COLLECTIVE_IDS": inst.id,
        "ACC_SESSIONS_DIR": str(roots["sessions"]),
        "ACC_TRACELOG_DIR": str(roots["trace"]),
        "ACC_COLLECTIVE_DIR": str(roots["overlays"]),
    }


def compose_overlay(
    inst: Instance, *, image: str = "localhost/acc-agent-core:0.2.0",
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """The podman-compose overlay that runs the instance's collective with the
    instance's roots. ``podman-compose -f <base> -f <overlay> up -d``."""
    from acc.collective import load_collective, roles_to_compose  # noqa: PLC0415
    spec = load_collective(instance_dir(inst.id, repo_root) / COLLECTIVE_FILE)
    overlay = roles_to_compose(spec, image=image, extra_env=cell_env(inst), extra_volumes=cell_volumes(inst))
    # Service and container names carry the instance id: the renderer names
    # cells by role (``acc-cell-analyst-1``), so two instances on one host
    # would collide on the same container names (the HG-40.1b Phase 2 run
    # brought up two instances and got one set of cells).
    services = {}
    for name, svc in overlay.get("services", {}).items():
        new_name = f"acc-{inst.id}-{name.removeprefix('acc-cell-').removeprefix('acc-')}"
        svc = dict(svc)
        if svc.get("container_name"):
            svc["container_name"] = new_name
        services[new_name] = svc
    overlay["services"] = services
    return overlay


# ---------------------------------------------------------------------------
# Distribution — definition travels, state does not
# ---------------------------------------------------------------------------


def export_instance(instance_id: str, repo_root: Path | None = None) -> Export:
    """A portable document: the collective definition (installed set), the
    overlays, the posture (as ``profile export`` renders it) and the hub
    binding. The owner is not carried — it is a principal of *this* substrate;
    the importing site names its own. State is not carried, and the document
    says so."""
    inst = load_instance(instance_id, repo_root)
    base = instance_dir(instance_id, repo_root)
    collective = yaml.safe_load((base / COLLECTIVE_FILE).read_text(encoding="utf-8")) or {}
    overlays: dict[str, str] = {}
    odir = base / "overlays"
    if odir.is_dir():
        for p in sorted(odir.glob("*.md")):
            overlays[p.name] = p.read_text(encoding="utf-8")
    profile_doc: dict[str, Any] | None = None
    if inst.profile:
        from acc import profiles as P  # noqa: PLC0415
        try:
            profile_doc = P.export_profile(inst.profile, repo_root=repo_root)
        except P.ProfileError as exc:
            raise InstanceError(str(exc)) from exc
    roots = state_roots(inst, repo_root)
    excluded = [f"{name}/ ({roots[name]})" for name in STATE_DIRS if name != "overlays"]
    document = {
        "kind": "acc-instance",
        "version": 1,
        "instance": {
            "id": inst.id, "profile": inst.profile, "hub": inst.hub,
            "stack_profile": inst.stack_profile, "note": inst.note,
        },
        "collective": collective,
        "overlays": overlays,
        "profile": profile_doc,
        "state_excluded": excluded,
        # Reserved (profiles spec §12.3): set when archives are signed.
        "signature": None,
    }
    return Export(document=document, state_excluded=excluded)


def import_instance(
    document: dict[str, Any],
    *,
    owner: str,
    instance_id: str | None = None,
    created_by: str = "",
    repo_root: Path | None = None,
) -> Instance:
    """Create a new instance from an exported document, owned by *owner*.

    The posture travels with the document; it is installed under its name when
    this site does not have it (``profile import``, never applied). State is
    not restored because none was carried.
    """
    if not isinstance(document, dict) or document.get("kind") != "acc-instance":
        raise InstanceError("not an instance document (kind != acc-instance)")
    meta = document.get("instance") or {}
    if not isinstance(meta, dict):
        raise InstanceError("instance document: 'instance' must be a mapping")
    new_id = _check_id(instance_id or str(meta.get("id", "")))
    owner = _check_owner(owner)
    profile = str(meta.get("profile", "") or "")
    profile_doc = document.get("profile")
    if profile and isinstance(profile_doc, dict):
        from acc import profiles as P  # noqa: PLC0415
        if profile not in P.list_profiles(repo_root):
            try:
                P.import_profile(profile_doc, repo_root=repo_root)
            except P.ProfileError as exc:
                raise InstanceError(f"profile {profile!r}: {exc}") from exc
    collective = document.get("collective") or {}
    if not isinstance(collective, dict) or not collective.get("agents"):
        raise InstanceError("instance document carries no collective definition")

    inst = create(
        new_id, owner=owner, profile=profile, hub=str(meta.get("hub", "") or ""),
        stack_profile=str(meta.get("stack_profile", "edge") or "edge"),
        created_by=created_by, note=str(meta.get("note", "") or ""), repo_root=repo_root,
    )
    base = instance_dir(new_id, repo_root)
    collective = dict(collective)
    collective["collective_id"] = new_id          # the id is the collective id
    from acc.collective import CollectiveSpec  # noqa: PLC0415
    try:
        CollectiveSpec.model_validate(collective)
    except Exception as exc:  # noqa: BLE001 -- pydantic's own message is the useful one
        raise InstanceError(f"collective definition: {exc}") from exc
    atomic_write_text(
        base / COLLECTIVE_FILE, yaml.safe_dump(collective, sort_keys=False),
        mode=0o644, tmp_prefix=".collective.tmp.",
    )
    overlays = document.get("overlays") or {}
    if isinstance(overlays, dict):
        for name, text in overlays.items():
            if str(name).endswith(".md") and "/" not in str(name):
                (base / "overlays" / str(name)).write_text(str(text), encoding="utf-8")
    return inst

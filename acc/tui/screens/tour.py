"""The first-run tour -- what ACC is, in ACC's own terms (IN-09).

`20260909-acc-install` IN-09 (operator remark 2026-09-09: first-time users
should have a guided onboarding, leaning into the Hermes gap but true to the
project's design).  The guided *setup* already exists (HG-08, ``acc-cli
setup``: posture, model, storage, validated as it goes).  This is what comes
after it: a short walk through the surfaces a person meets, each step
saying what the floor is rather than lowering it --

1. who you are here (the principal, tier and ceiling this TUI acts as);
2. the Prompt: asking, the operating mode, why nothing runs on its own;
3. the Board: the work the collective is doing;
4. Compliance: one sample gate is queued for you to decide -- the oversight
   model felt, not read (nothing runs on it either way);
5. the workspace: the directory ``acc`` was started in, trusted or not;
6. ``/new-agent``: launching your own governed agentset (a signed AgentBOM,
   prod-locked until you switch to dev);
7. done -- ``acc tour`` brings it back.

It runs once on an installed layout (a marker under ``~/.config/acc``
remembers), on request via ``acc tour`` / ``ACC_TUI_TOUR=1``, and never
inside a developer's checkout unless asked.  Skippable at any step.  It
never widens anything: no ceiling above the tier default, no ``AUTO``, no
directory trusted without the prompt, nothing sent anywhere but the
collective's own bus.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from acc import paths

TOUR_ENV = "ACC_TUI_TOUR"
FIRST_RUN_ENV = "ACC_FIRST_RUN"
SAMPLE_GATE_SUMMARY = "Tour: a sample gate -- approve or reject it; nothing runs either way"


def marker_path() -> Path:
    raw = os.environ.get("ACC_TOUR_MARKER", "").strip()
    return Path(raw).expanduser() if raw else paths.user_config_dir() / "tour.done"


def tour_wanted() -> bool:
    """Once on an installed layout, on request, never in a checkout unasked."""
    flag = os.environ.get(TOUR_ENV, "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    if marker_path().is_file():
        return False
    if os.environ.get(FIRST_RUN_ENV, "").strip() == "1":
        return True
    found = paths.home()
    return found is not None and found[1] == "home"


def mark_done() -> Path:
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"done_at={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n", encoding="utf-8")
    return p


@dataclass(frozen=True)
class Step:
    title: str
    body: str
    action: str = ""          # "sample_gate" queues the gate when the step is shown


def _who() -> tuple[str, str, str]:
    try:
        from acc.tui.actor import tui_actor, tui_actor_tier, tui_principal  # noqa: PLC0415
        p = tui_principal()
        ceiling = getattr(p, "effective_ceiling", "") if p is not None else ""
        return tui_actor(), tui_actor_tier() or "operator", str(ceiling or "CRITICAL")
    except Exception:  # noqa: BLE001
        return "tui:anonymous", "operator", "CRITICAL"


def _workspace_line() -> str:
    ws = os.environ.get("ACC_WORKSPACE_HOST_DIR", "").strip()
    if not ws:
        return ("No workspace this session. Start `acc` inside a project directory and it asks "
                "whether to trust it (`[y / N / always / below]`); a directory never trusted is never "
                "mounted, never read by a filesystem skill, never selected.")
    try:
        from acc import workspace_trust as T  # noqa: PLC0415
        rec, how = T.lookup(ws)
    except Exception:  # noqa: BLE001
        rec, how = None, ""
    if rec is not None and rec.trusted:
        via = "" if how == "exact" else f" (below {rec.path})"
        return (f"Workspace: {ws} -- trusted {rec.scope}{via} since {rec.since}. Roles with "
                "`workspace_access` (the coding agent by default) read it through the sandbox and "
                "write after review; `acc-cli workspace list` shows every trusted directory.")
    return (f"Workspace: {ws} -- trusted for this session only (not recorded). "
            "`acc-cli workspace trust <dir>` records it; `--below` for a whole repository.")


def steps(collective_id: str = "") -> list[Step]:
    actor, tier, ceiling = _who()
    mode = os.environ.get("ACC_OPERATING_MODE", "").strip() or "PLAN"
    cid = collective_id or os.environ.get("ACC_COLLECTIVE_ID", "") or "the collective"
    return [
        Step("Welcome to ACC",
             f"This TUI acts as {actor} -- tier {tier}, ceiling {ceiling}. A collective is a set "
             f"of governed roles ({cid}) on one bus: the assistant routes and proposes, specialists "
             "answer, the arbiter dispatches plans, and people approve. Nothing here runs on its own "
             "authority; the rules that bound it (constitutional / setpoint / learned) are in the "
             "Compliance pane, and your tier's ceiling is the floor under everything you ask for."),
        Step("The Prompt",
             f"Type what you want and pick a role; the operating mode is {mode}. Under PLAN the "
             "assistant lays out steps and asks; under ASK_PERMISSIONS it asks before anything "
             "gated; AUTO executes what the policy layer has learned to trust -- and it is never "
             "the default. A proposal (spawn a role, install a pack, publish a lesson) becomes a "
             "card in this pane or a row in Compliance; you decide it. `/help` lists the slash "
             "commands, `/mode` switches the mode, `/status` shows where you stand."),
        Step("The Board",
             "Plans, their steps, fanned-out members and single tasks, under QUEUED / RUNNING / "
             "BLOCKED / DONE / FAILED. An operator sees the whole collective; anyone else sees what "
             "they asked for. `c` cancels, `r` retries, `a` reassigns, `g` jumps to the gate that "
             "blocks a step. Nobody drags a card to Done."),
        Step("Compliance -- decide a sample gate",
             "A sample gate has been queued for you (HIGH risk, from the tour). Open Compliance, "
             "select it, and approve or reject it: `a` / `r`, with your identity stamped on the "
             "decision. Nothing runs on it either way -- this one exists so the oversight model is "
             "felt, not read. A real gate is the same row, raised by a role that reached a limit "
             "you set; a decision is final; a hub promotion of a sensitive lesson needs two "
             "operators.", action="sample_gate"),
        Step("Your workspace", _workspace_line()),
        Step("Launch your own agent",
             "`/new-agent <what it should do>` is the concierge: it turns plain English into a "
             "governed agentset -- roles from the signed catalog, a model per role, pinned "
             "packages, a deploy target -- and hands you a signed Agent Bill of Materials to "
             "review before anything runs. It is prod-locked: switch to dev mode to use it, and "
             "keep prod for the day the agentset is yours to ship."),
        Step("That is the tour",
             "Everything you saw keeps its floor: the ceiling stays at your tier's default, AUTO "
             "stays off until you turn it on, no directory is trusted without the prompt, and "
             "nothing left this host but the collective's own bus. `acc tour` brings this back; "
             "`acc doctor` says what is healthy; `acc paths` says where ACC lives. Enjoy."),
    ]


class TourScreen(ModalScreen[None]):
    """The seven steps, one card at a time."""

    BINDINGS = [
        Binding("escape", "skip", "Skip", priority=True),
        Binding("right", "next", "Next", priority=True),
        Binding("n", "next", "Next", priority=True),
        Binding("left", "back", "Back", priority=True),
    ]

    DEFAULT_CSS = """
    TourScreen { align: center middle; }
    #tour-card { width: 92; height: auto; max-height: 90%; border: round $accent; background: $surface; padding: 1 2; }
    #tour-title { text-style: bold; margin-bottom: 1; }
    #tour-body { height: auto; }
    #tour-step { color: $text-muted; margin-top: 1; }
    #tour-buttons { height: 3; margin-top: 1; align-horizontal: right; }
    """

    def __init__(self, collective_id: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._steps = steps(collective_id)
        self._index = 0
        self._gate_queued = False

    def compose(self) -> ComposeResult:
        with Vertical(id="tour-card"):
            yield Static("", id="tour-title")
            yield Static("", id="tour-body")
            yield Static("", id="tour-step")
            with Horizontal(id="tour-buttons"):
                yield Button("Skip", id="tour-skip")
                yield Button("Back", id="tour-back")
                yield Button("Next", id="tour-next", variant="primary")

    def on_mount(self) -> None:
        self._show_step()

    @property
    def index(self) -> int:
        return self._index

    def _show_step(self) -> None:
        step = self._steps[self._index]
        last = self._index == len(self._steps) - 1
        self.query_one("#tour-title", Static).update(step.title)
        self.query_one("#tour-body", Static).update(step.body)
        self.query_one("#tour-step", Static).update(f"step {self._index + 1} of {len(self._steps)}")
        self.query_one("#tour-next", Button).label = "Done" if last else "Next"
        self.query_one("#tour-back", Button).disabled = self._index == 0
        if step.action == "sample_gate" and not self._gate_queued:
            self._gate_queued = True
            self.run_worker(self._queue_sample_gate(), exclusive=False)

    async def _queue_sample_gate(self) -> None:
        """One OVERSIGHT_SUBMIT on the collective's bus, HIGH, from the tour."""
        publish = getattr(self.app, "publish_json", None)
        if publish is None:
            return
        try:
            from acc.cli.oversight_cmd import build_submit_payload  # noqa: PLC0415
            cid = getattr(self.app, "active_collective_id", "") or os.environ.get("ACC_COLLECTIVE_ID", "sol-01")
            payload = build_submit_payload(cid, "tour-sample-gate", "tour", "HIGH", SAMPLE_GATE_SUMMARY)
            await publish(f"acc.{cid}.oversight.submit", payload)
        except Exception:  # noqa: BLE001
            self.query_one("#tour-body", Static).update(
                self._steps[self._index].body + "\n\n(The sample gate could not be queued -- no bus "
                "in this session; the Compliance pane is still the place to look.)")

    def action_next(self) -> None:
        if self._index >= len(self._steps) - 1:
            mark_done()
            self.dismiss(None)
            return
        self._index += 1
        self._show_step()

    def action_back(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._show_step()

    def action_skip(self) -> None:
        mark_done()
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tour-next":
            self.action_next()
        elif event.button.id == "tour-back":
            self.action_back()
        elif event.button.id == "tour-skip":
            self.action_skip()

"""ACC TUI — internal Textual messages used between screens and the App.

Public messages live here so any screen can post them without circular imports.
Per-screen private messages (e.g. ``_PublishMessage`` in infuse.py) stay in
their owning module.

Conventions:
    * Internal messages start with an underscore.
    * Public messages do not — they are part of the inter-screen contract.
    * Every message carries its own constructor doc with the field semantics.
"""

from __future__ import annotations

from textual.message import Message


class RolePreloadMessage(Message):
    """Request that InfuseScreen pre-fill its form from the named role.

    Posted by the Ecosystem screen when the user clicks the "Schedule
    infusion" button after selecting a role row.  The App routes this
    message to the active InfuseScreen instance and switches the active
    screen to ``nucleus`` so the operator can review and Apply.

    Attributes:
        role_name: The role directory name under ``roles/`` (e.g.
            ``"coding_agent"``, ``"account_executive"``).  The InfuseScreen
            uses ``RoleLoader(roles_root, role_name).load()`` to resolve the
            full ``RoleDefinitionConfig``.
    """

    def __init__(self, role_name: str) -> None:
        super().__init__()
        self.role_name = role_name


class RolesChangedMessage(Message):
    """Posted by the Ecosystem screen's file-watcher when an external
    edit to the ``roles/`` directory is detected (a role.yaml or
    role.md added, removed, or modified).

    The Ecosystem screen handles this by re-running ``_load_roles()``
    + re-applying the current filter.  Detail-pane content stays in
    sync because ``_show_role_detail()`` reads from disk each time.

    Posted by the polling task ``_watch_roles_loop()``; routed back to
    the UI thread via Textual's message bus.  Proposal 003 PR-3.

    Attributes:
        reason: One of ``"added"``, ``"removed"``, ``"modified"``,
            ``"initial"``.  ``"initial"`` lets the watcher post a
            first-pass event harmlessly so the handler is exercised
            even when no operator-side change happened — useful in
            tests.
    """

    def __init__(self, reason: str = "modified") -> None:
        super().__init__()
        self.reason = reason


class HelpRequestMessage(Message):
    """Request the App show a help overlay for the current screen.

    Posted by any screen's ``?`` keybinding.  The App picks the active
    screen's id (e.g. ``"soma"``, ``"nucleus"``) and mounts a HelpScreen
    modal with the matching markdown from ``acc/tui/help/{screen_id}.md``.

    Attributes:
        screen_id: Logical screen identifier — must match a markdown
            filename under ``acc/tui/help/``.
    """

    def __init__(self, screen_id: str) -> None:
        super().__init__()
        self.screen_id = screen_id


class PromptLoadMessage(Message):
    """Request that the Prompt screen load a prompt and optionally send it.

    Posted by the Diagnostics screen's golden-prompt "Send" action
    (proposal 033 WS-B).  The App routes it to the Prompt screen
    (switching there), which populates its target-role / agent / mode /
    textarea from these fields and — when ``auto_send`` is True — fires
    the send so the reply streams on the Prompt pane (the operator's
    "going back to Prompt shows the golden prompt we just sent" + its
    feedback).

    Attributes:
        prompt_text: The prompt body to place in the Prompt textarea.
        target_role: Role to target (added to the dropdown if it isn't a
            built-in option).
        target_agent_id: Optional specific agent id ("" = any).
        operating_mode: PLAN / ACCEPT_EDITS / ASK_PERMISSIONS / AUTO.
        auto_send: When True, the Prompt screen fires the send after
            populating.
    """

    def __init__(
        self,
        *,
        prompt_text: str,
        target_role: str = "",
        target_agent_id: str = "",
        operating_mode: str = "AUTO",
        auto_send: bool = False,
    ) -> None:
        super().__init__()
        self.prompt_text = prompt_text
        self.target_role = target_role
        self.target_agent_id = target_agent_id
        self.operating_mode = operating_mode
        self.auto_send = auto_send

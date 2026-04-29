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

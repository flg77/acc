"""ACC TUI shared widgets — independently importable, no cross-screen deps."""

from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo
from acc.tui.widgets.agent_card import AgentCard
from acc.tui.widgets.collective_tabs import CollectiveTabStrip, SwitchCollective

__all__ = [
    "NavigationBar",
    "NavigateTo",
    "AgentCard",
    "CollectiveTabStrip",
    "SwitchCollective",
]

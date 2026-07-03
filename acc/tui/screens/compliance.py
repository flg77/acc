"""ACC TUI — ComplianceScreen: OWASP grading, oversight queue, violation log.

All data is sourced exclusively from the CollectiveSnapshot built by NATSObserver.
No direct NATS, Redis, or LanceDB access.

Displays (REQ-TUI-023 – REQ-TUI-027):
  - OWASP LLM Top 10 grading table (Code, Grade A–F, Pass%, Description)
  - Collective compliance health score progress bar
  - Human oversight queue DataTable (approve/reject via keyboard)
  - Scrollable violation log (last 50 entries)

This screen imports only from acc.tui.models and acc.tui.widgets (REQ-TUI-051).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Footer,
    Input,
    Label,
    ProgressBar,
    Static,
)

from acc.tui.widgets.nav_bar import NavigationBar, NavScreen

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

# OWASP LLM Top 10 2025 — codes and descriptions
_OWASP_CODES: list[tuple[str, str]] = [
    ("LLM01", "Prompt Injection"),
    ("LLM02", "Insecure Output Handling"),
    ("LLM03", "Training Data Poisoning"),
    ("LLM04", "Model Denial of Service"),
    ("LLM05", "Supply Chain Vulnerabilities"),
    ("LLM06", "Sensitive Information Disclosure"),
    ("LLM07", "Insecure Plugin Design"),
    ("LLM08", "Excessive Agency"),
    ("LLM09", "Overreliance"),
    ("LLM10", "Model Theft"),
]


def _owasp_grade(pass_rate: float) -> str:
    """Convert pass rate (0.0–1.0) to letter grade A–F."""
    if pass_rate >= 0.95:
        return "A"
    if pass_rate >= 0.85:
        return "B"
    if pass_rate >= 0.70:
        return "C"
    if pass_rate >= 0.55:
        return "D"
    return "F"


def _compute_owasp_grades(
    violation_log: list[dict],
) -> dict[str, tuple[str, float]]:
    """Compute per-code (grade, pass_rate) from the session violation log.

    Returns dict: code → (grade_letter, pass_rate).
    Codes with no violations are graded A (1.0 pass rate).
    """
    # Count violations per code
    violation_counts: dict[str, int] = defaultdict(int)
    total_observations = max(len(violation_log), 1)

    for entry in violation_log:
        code = entry.get("code", "")
        if code:
            violation_counts[code] += 1

    result: dict[str, tuple[str, float]] = {}
    for code, _desc in _OWASP_CODES:
        vcount = violation_counts.get(code, 0)
        pass_rate = 1.0 - (vcount / total_observations)
        pass_rate = max(0.0, min(1.0, pass_rate))
        result[code] = (_owasp_grade(pass_rate), pass_rate)

    return result


class ComplianceScreen(NavScreen):
    """Compliance and governance monitoring screen (REQ-TUI-023 – REQ-TUI-027)."""

    # Approve / reject use letter keys (NOT Enter) so the screen-level
    # binding wins.  Pressing Enter while the oversight DataTable has
    # focus triggers the table's own RowSelected handler — the screen
    # binding never fires.  Confirmed via Pilot:
    #   focus(table); press('enter')  →  no action_approve_oversight call
    #   focus(table); press('r')      →  reject dispatched correctly
    # We use 'a' / 'r' as the mnemonic pair and mark both priority=True
    # so they fire even if a future child widget claims the keys.
    DEFAULT_CSS = """
    ComplianceScreen #owasp-table { height: 13; }
    /* 050 Slice 4 — governance (1fr) shares the column with the fixed-height
       OWASP table; a min-height keeps it usable on a short terminal instead of
       being squeezed toward zero (the parent scrolls past that point). */
    ComplianceScreen #governance-layers { height: 1fr; min-height: 8; margin-top: 1; }
    ComplianceScreen .gov-table { height: auto; max-height: 10; }
    """

    BINDINGS = [
        Binding("a", "approve_oversight", "Approve", priority=True),
        Binding("r", "reject_oversight", "Reject", priority=True),
        # PR-Z1b/c — focus the governance layers (g) or the oversight
        # queue (o) so the operator can navigate them by keyboard.
        Binding("g", "focus_governance", "Governance", priority=True),
        Binding("o", "focus_oversight", "Oversight", priority=True),
        Binding("p", "focus_proposals", "Proposals", priority=True),
    ]

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="compliance", id="nav")
        yield Label("ACC Compliance — Dendritic Immune Layer", id="compliance-title")

        with Horizontal(id="compliance-main"):
            # Left column: OWASP grading + health score
            with Vertical(id="compliance-left"):
                yield Label("OWASP LLM TOP 10 GRADING", classes="panel-label")
                yield DataTable(id="owasp-table", show_cursor=False)

                yield Label("COMPLIANCE HEALTH", classes="panel-label")
                yield Static(id="health-score-value")
                yield ProgressBar(id="health-progress-bar", total=100, show_eta=False)

                # PR-Z1b — Governance layers: three collapsible sections
                # (Cat A / B / C) showing WHAT is loaded.  Cat-A starts
                # expanded (the constitution); B/C collapsed to keep the
                # dashboard scannable.  Each holds a rule_id | summary
                # table; selecting a row opens the source policy file in
                # a read-only viewer.  Press `g` to focus this area.
                yield Label("GOVERNANCE LAYERS", classes="panel-label")
                with ScrollableContainer(id="governance-layers"):
                    with Collapsible(
                        title="Cat A", collapsed=False, id="gov-cat-a",
                    ):
                        yield DataTable(id="gov-table-a", classes="gov-table")
                    with Collapsible(
                        title="Cat B", collapsed=True, id="gov-cat-b",
                    ):
                        yield DataTable(id="gov-table-b", classes="gov-table")
                    with Collapsible(
                        title="Cat C", collapsed=True, id="gov-cat-c",
                    ):
                        yield DataTable(id="gov-table-c", classes="gov-table")
                    # PR-Z2c — enterprise frameworks: built-in + imported
                    # catalogs to gap-analyse the loaded rules against.
                    with Collapsible(
                        title="Frameworks", collapsed=True, id="gov-frameworks",
                    ):
                        yield DataTable(id="fw-table", classes="gov-table")
                        with Horizontal(id="fw-actions"):
                            yield Input(
                                placeholder="catalog to import, e.g. "
                                "/host-home/bsi_c5.yaml",
                                id="fw-add-input",
                            )
                            yield Button("+ Add", id="btn-fw-add",
                                         variant="default")
                            yield Button("Run gap scan", id="btn-fw-scan",
                                         variant="primary")
                        yield Static("", id="fw-status")
                    # PR-Z3d — proposed Cat-B/C rules (from gap analysis,
                    # violation learning, self-challenge) awaiting human
                    # review.  Select a row + Approve (→ signed-bundle
                    # overlay) or Reject.  Press `p` to focus.
                    with Collapsible(
                        title="Rule Proposals", collapsed=True,
                        id="gov-proposals",
                    ):
                        yield DataTable(id="proposals-table", classes="gov-table")
                        with Horizontal(id="proposals-actions"):
                            yield Button("Approve", id="btn-proposal-approve",
                                         variant="primary")
                            yield Button("Reject", id="btn-proposal-reject",
                                         variant="default")
                            yield Button("Self-challenge Cat-A",
                                         id="btn-self-challenge",
                                         variant="default")
                        yield Static("", id="proposals-status")
                    # Stage 1.4 visual surface — PROPOSE_INFUSE proposals
                    # routed through the same AoA-P2b oversight queue,
                    # filtered + rendered with infuse-specific columns
                    # (package + constraint + tier + signer).  Approve /
                    # Reject post the same _OversightAction message the
                    # main oversight queue uses, so the dispatch path is
                    # the existing one — no parallel wire.
                    with Collapsible(
                        title="Package Proposals (PROPOSE_INFUSE)",
                        collapsed=True,
                        id="gov-pkg-proposals",
                    ):
                        yield DataTable(id="pkg-proposals-table", classes="gov-table")
                        with Horizontal(id="pkg-proposals-actions"):
                            yield Button("Approve", id="btn-pkg-proposal-approve",
                                         variant="primary")
                            yield Button("Reject", id="btn-pkg-proposal-reject",
                                         variant="default")
                        yield Static("", id="pkg-proposals-status")

            # Right column: oversight queue + master/detail context + violation log
            #
            # PR-H (D-004) — operator-reported: pre-PR-H the table
            # showed only ``ID · Agent · Risk · Submitted · Status``,
            # leaving the operator to Approve / Reject blind.  The
            # master/detail layout below surfaces the inbound HEARTBEAT
            # ``summary`` (gate reason), the originating ``task_id``,
            # and explicit Approve / Reject preview lines so the
            # operator sees what they're consenting to before pressing
            # ``a`` / ``r``.
            with Vertical(id="compliance-right"):
                yield Label("HUMAN OVERSIGHT QUEUE", classes="panel-label")
                yield DataTable(id="oversight-table")
                yield Label(
                    "  [bold]o[/bold]=focus queue  [bold]↑/↓[/bold]=move  "
                    "[bold]a[/bold]=Approve highlighted  "
                    "[bold]r[/bold]=Reject highlighted  "
                    "[dim](detail panel below tracks the highlighted row)[/dim]",
                    classes="key-hint",
                )

                yield Label(
                    "PENDING ITEM DETAIL", classes="panel-label",
                    id="oversight-detail-label",
                )
                with ScrollableContainer(id="oversight-detail-container"):
                    yield Static(
                        "[dim]Highlight a row above to see its full "
                        "context (gate reason, payload preview, "
                        "consequence of Approve vs Reject).[/dim]",
                        id="oversight-detail",
                    )

                yield Label("OWASP VIOLATION LOG (last 50)", classes="panel-label")
                with ScrollableContainer(id="violation-log-container"):
                    yield Static(id="violation-log")

                # Personalization overlay (proposal agent-personalization-overlay)
                # — read-only effective-profile view per agent, sourced purely
                # from the HEARTBEAT-published ``overlay_summary`` on the
                # snapshot (REQ-TUI-051: no business logic in the screen).
                yield Label("ROLE OVERLAY PROFILES", classes="panel-label")
                with Collapsible(
                    title="Effective overlay profiles (per agent)",
                    collapsed=True,
                ):
                    yield DataTable(id="overlay-profiles-table", classes="gov-table")

        yield Footer()

    def on_mount(self) -> None:
        """Initialise DataTable columns."""
        owasp = self.query_one("#owasp-table", DataTable)
        owasp.add_columns("Code", "Grade", "Pass%", "Description")

        oversight = self.query_one("#oversight-table", DataTable)
        # PR-H — added "Gate reason" (truncated summary) so the operator
        # can scan the queue without expanding every row into the
        # detail panel.
        oversight.add_columns(
            "ID", "Agent", "Risk", "Submitted", "Gate reason", "Status",
        )
        # PR-Z1c — whole-row cursor so the operator clearly sees which
        # item `a`/`r` will act on (matches the Ecosystem table feel).
        oversight.cursor_type = "row"

        # PR-Z1b — load the governance layers once (display-only; they
        # don't change per snapshot).
        self._gov_rules_by_key: dict[str, object] = {}
        self._populate_governance()
        # PR-Z2c — load the framework catalogs (built-in + imported).
        self._coverage_by_fw: dict[str, str] = {}
        self._populate_frameworks()
        # PR-Z3d — load any pending rule proposals.
        self._proposals_by_id: dict[str, object] = {}
        self._refresh_proposals()
        # Stage 1.4 — Package Proposals (PROPOSE_INFUSE) sit in the
        # same AoA-P2b oversight queue.  Table is filtered + projected
        # by `_render_pkg_proposals` from `oversight_pending_items`.
        pkg_proposals = self.query_one("#pkg-proposals-table", DataTable)
        pkg_proposals.add_columns(
            "ID", "Package", "Constraint", "Tier", "Signer", "Status",
        )
        pkg_proposals.cursor_type = "row"
        self._pkg_proposals_by_id: dict[str, dict] = {}

        # Personalization overlay — effective-profile-per-agent table.
        overlay_tbl = self.query_one("#overlay-profiles-table", DataTable)
        overlay_tbl.add_columns(
            "Agent", "Role", "Profile", "Enabled", "Local", "Dropped",
        )
        overlay_tbl.cursor_type = "row"

    def _populate_governance(self) -> None:
        """Fill the Cat-A/B/C tables + titles from the inventory loader."""
        from acc.governance_inventory import load_all_layers  # noqa: PLC0415

        try:
            layers = load_all_layers()
        except Exception:
            layers = []
        by_cat = {layer.category: layer for layer in layers}
        for cat, tbl_id, coll_id in (
            ("A", "gov-table-a", "gov-cat-a"),
            ("B", "gov-table-b", "gov-cat-b"),
            ("C", "gov-table-c", "gov-cat-c"),
        ):
            layer = by_cat.get(cat)
            try:
                table = self.query_one(f"#{tbl_id}", DataTable)
            except Exception:
                continue
            if not table.columns:
                table.add_columns("Rule", "Summary")
            table.clear()
            collapsible = None
            try:
                collapsible = self.query_one(f"#{coll_id}", Collapsible)
            except Exception:
                pass
            if layer is None or layer.rule_count == 0:
                if collapsible is not None:
                    collapsible.title = f"Cat {cat} — (none loaded)"
                continue
            for rule in layer.rules:
                self._gov_rules_by_key[rule.rule_id] = rule
                summary = rule.summary or "[dim]—[/dim]"
                table.add_row(rule.rule_id, summary[:80], key=rule.rule_id)
            if collapsible is not None:
                lock = " 🔒" if layer.immutable else ""
                ver = f" v{layer.version}" if layer.version else ""
                collapsible.title = (
                    f"{layer.title}{ver} — {layer.rule_count} rules{lock}"
                )

    # ------------------------------------------------------------------
    # PR-Z2c — frameworks + gap analysis
    # ------------------------------------------------------------------

    def _populate_frameworks(self) -> None:
        """Fill the frameworks table from built-in + imported catalogs."""
        from acc.frameworks import load_all_frameworks  # noqa: PLC0415

        try:
            table = self.query_one("#fw-table", DataTable)
        except Exception:
            return
        if not table.columns:
            table.add_columns("Framework", "Name", "Controls", "Coverage")
        table.clear()
        try:
            frameworks = load_all_frameworks()
        except Exception:
            frameworks = []
        for fw in frameworks:
            cov = self._coverage_by_fw.get(fw.framework_id, "—")
            table.add_row(
                fw.framework_id, fw.name[:34], str(fw.control_count), cov,
                key=fw.framework_id,
            )

    def _set_fw_status(self, markup: str) -> None:
        try:
            self.query_one("#fw-status", Static).update(markup)
        except Exception:
            pass

    def _refresh_proposals(self) -> None:
        """Refresh the Rule Proposals table (PR-Z3d wires the widget;
        safe no-op until then)."""
        try:
            from acc.rule_proposals import list_proposals  # noqa: PLC0415
            table = self.query_one("#proposals-table", DataTable)
        except Exception:
            return
        if not table.columns:
            table.add_columns("ID", "Src", "Cat", "Sev", "Status", "Rationale")
        table.clear()
        self._proposals_by_id = {}
        for p in list_proposals():
            self._proposals_by_id[p.proposal_id] = p
            status_cell = {
                "PROPOSED": "[yellow]PROPOSED[/yellow]",
                "APPROVED": "[green]APPROVED[/green]",
                "REJECTED": "[dim]REJECTED[/dim]",
            }.get(p.status, p.status)
            table.add_row(
                p.proposal_id[:8], p.source, p.category, p.severity,
                status_cell, (p.rationale or "")[:50],
                key=p.proposal_id,
            )

    def _selected_framework_id(self) -> str | None:
        try:
            table = self.query_one("#fw-table", DataTable)
            if table.row_count == 0:
                return None
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            value = getattr(row_key, "value", None) or str(row_key)
            return str(value) if value else None
        except Exception:
            return None

    def _import_framework(self) -> None:
        from acc.frameworks import import_framework  # noqa: PLC0415

        try:
            path = self.query_one("#fw-add-input", Input).value.strip()
        except Exception:
            path = ""
        if not path:
            self._set_fw_status("[yellow]Enter a catalog path to import.[/yellow]")
            return
        try:
            out = import_framework(path)
        except Exception as exc:
            self._set_fw_status(f"[red]import failed: {exc}[/red]")
            return
        self._set_fw_status(f"[green]✓ imported[/green] [dim]{out.name}[/dim]")
        self._populate_frameworks()

    def _run_gap_scan(self) -> None:
        """Run the deterministic gap analysis for the highlighted
        framework, write the audit doc, and open the markdown report."""
        from acc.frameworks import load_all_frameworks  # noqa: PLC0415
        from acc.gap_analysis import analyze_gaps, dump_gap_report  # noqa: PLC0415
        from acc.governance_inventory import load_all_layers  # noqa: PLC0415

        fw_id = self._selected_framework_id()
        if fw_id is None:
            self._set_fw_status("[yellow]Highlight a framework first.[/yellow]")
            return
        framework = next(
            (f for f in load_all_frameworks() if f.framework_id == fw_id), None,
        )
        if framework is None:
            self._set_fw_status("[red]framework not found[/red]")
            return
        try:
            report = analyze_gaps(load_all_layers(), framework)
            json_path = dump_gap_report(report)
        except Exception as exc:
            self._set_fw_status(f"[red]gap scan failed: {exc}[/red]")
            return
        # PR-Z3b — turn each gap into a Cat-B/C rule proposal (auto-
        # approved into the overlay or left PENDING per the
        # learned_rule_promotion setpoint).  Best-effort.
        n_proposals = 0
        mode = "propose"
        try:
            from acc.rule_proposals import (  # noqa: PLC0415
                promotion_mode, proposals_from_gap_report,
            )
            mode = promotion_mode()
            n_proposals = len(proposals_from_gap_report(report))
        except Exception:
            logger.debug("compliance: proposal emit failed", exc_info=True)
        cov = f"{report.coverage_pct:.0f}% ({report.gap_count} gaps)"
        self._coverage_by_fw[fw_id] = cov
        self._populate_frameworks()
        self._refresh_proposals()
        self._set_fw_status(
            f"[green]✓ scanned[/green] {fw_id}: {cov} — "
            f"{n_proposals} proposal(s) [{mode}] "
            f"[dim]→ {json_path.with_suffix('.md').name}[/dim]"
        )
        # Show the markdown audit doc in the read-only viewer.
        from acc.tui.widgets.policy_viewer_modal import (  # noqa: PLC0415
            PolicyViewerModal,
        )
        self.app.push_screen(PolicyViewerModal(json_path.with_suffix(".md")))

    def action_focus_governance(self) -> None:
        """`g` — focus the Cat-A governance table for keyboard nav."""
        try:
            self.query_one("#gov-table-a", DataTable).focus()
        except Exception:
            pass

    def action_focus_oversight(self) -> None:
        """`o` — focus the human-oversight queue table."""
        try:
            self.query_one("#oversight-table", DataTable).focus()
        except Exception:
            pass

    def action_focus_proposals(self) -> None:
        """`p` — focus the rule-proposals table."""
        try:
            self.query_one("#proposals-table", DataTable).focus()
        except Exception:
            pass

    def _set_proposals_status(self, markup: str) -> None:
        try:
            self.query_one("#proposals-status", Static).update(markup)
        except Exception:
            pass

    def _selected_proposal_id(self) -> str | None:
        try:
            table = self.query_one("#proposals-table", DataTable)
            if table.row_count == 0:
                return None
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            value = getattr(row_key, "value", None) or str(row_key)
            return str(value) if value else None
        except Exception:
            return None

    def _decide_proposal(self, approve: bool) -> None:
        pid = self._selected_proposal_id()
        if pid is None:
            self._set_proposals_status("[yellow]Highlight a proposal first.[/yellow]")
            return
        try:
            from acc.rule_proposals import (  # noqa: PLC0415
                approve_proposal, reject_proposal,
            )
            if approve:
                approve_proposal(pid, by="operator")
                msg = f"[green]✓ approved[/green] {pid[:8]} → bundle overlay"
            else:
                reject_proposal(pid, by="operator")
                msg = f"[dim]rejected {pid[:8]}[/dim]"
        except Exception as exc:
            self._set_proposals_status(f"[red]decision failed: {exc}[/red]")
            return
        self._refresh_proposals()
        self._set_proposals_status(msg)

    def on_button_pressed(self, event: "Button.Pressed") -> None:
        bid = event.button.id or ""
        if bid == "btn-fw-add":
            self._import_framework()
        elif bid == "btn-fw-scan":
            self._run_gap_scan()
        elif bid == "btn-proposal-approve":
            self._decide_proposal(approve=True)
        elif bid == "btn-proposal-reject":
            self._decide_proposal(approve=False)
        elif bid == "btn-self-challenge":
            self._run_self_challenge()
        elif bid == "btn-pkg-proposal-approve":
            self._decide_pkg_proposal(approve=True)
        elif bid == "btn-pkg-proposal-reject":
            self._decide_pkg_proposal(approve=False)

    # ------------------------------------------------------------------
    # Stage 1.4 visual surface — Package Proposals (PROPOSE_INFUSE)
    # ------------------------------------------------------------------

    # Keyword fragments that classify an oversight-queue item as a
    # PROPOSE_INFUSE proposal when the wire payload doesn't yet carry
    # ``kind="infuse"`` (mixed-version arbiter fleets).
    _PKG_SUMMARY_PREFIX = "Install @"

    @classmethod
    def _is_pkg_proposal(cls, item: dict) -> bool:
        """True iff ``item`` represents a PROPOSE_INFUSE proposal.

        Prefers the explicit ``kind`` field; falls back to a summary
        prefix heuristic for compat with older arbiter HEARTBEATs.
        """
        kind = str(item.get("kind") or "").lower()
        if kind == "infuse":
            return True
        summary = str(item.get("summary") or "")
        return summary.startswith(cls._PKG_SUMMARY_PREFIX)

    @staticmethod
    def _pkg_proposal_columns(item: dict) -> tuple[str, str, str, str]:
        """Extract (name, constraint, tier, signer) for the table row.

        Falls back to summary parsing when ``params`` isn't on the wire.
        """
        params = item.get("params") or {}
        name = str(params.get("name") or "")
        constraint = str(params.get("constraint") or "")
        if not name:
            # Summary shape: "Install @scope/name@constraint"
            summary = str(item.get("summary") or "")
            tail = summary[len("Install "):].strip() if summary.startswith("Install ") else ""
            if "@" in tail[1:]:  # skip leading @ in @scope/name
                at = tail.index("@", 1)
                name, constraint = tail[:at], tail[at + 1:]
        tier = str(item.get("tier") or item.get("catalog_tier") or "—")
        signer = str(item.get("signer_identity") or item.get("signer") or "—")
        return name or "—", constraint or "—", tier, signer

    def _render_pkg_proposals(self, snap: "CollectiveSnapshot") -> None:
        """Filter ``snap.oversight_pending_items`` for PROPOSE_INFUSE and
        render them in the Package Proposals table.

        Items co-exist in the main oversight queue; this is a
        package-aware projection, NOT a separate queue.
        """
        table = self.query_one("#pkg-proposals-table", DataTable)
        table.clear()
        self._pkg_proposals_by_id = {}
        for item in snap.oversight_pending_items or []:
            if not self._is_pkg_proposal(item):
                continue
            if str(item.get("status") or "PENDING") != "PENDING":
                continue
            oid = str(item.get("oversight_id") or "")
            if not oid:
                continue
            self._pkg_proposals_by_id[oid] = dict(item)
            name, constraint, tier, signer = self._pkg_proposal_columns(item)
            table.add_row(
                oid[:14],
                name[:28],
                constraint[:16],
                tier[:10],
                signer[:30],
                "PENDING",
                key=oid,
            )

    def _set_pkg_proposals_status(self, markup: str) -> None:
        try:
            self.query_one("#pkg-proposals-status", Static).update(markup)
        except Exception:  # pragma: no cover — widget not mounted yet
            pass

    def _selected_pkg_proposal_id(self) -> str | None:
        table = self.query_one("#pkg-proposals-table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key((table.cursor_row, 0))
            return str(row_key.value) if row_key and row_key.value else None
        except Exception:  # pragma: no cover
            return None

    def _decide_pkg_proposal(self, *, approve: bool) -> None:
        """Approve / reject the highlighted package proposal.

        Posts the same ``_OversightAction`` message the main oversight
        queue uses — the package-proposals table is a filtered view of
        the same queue, so the dispatch path is the existing
        :func:`acc.assistant_proposal.dispatch_approved_proposal`.
        """
        oid = self._selected_pkg_proposal_id()
        if oid is None:
            self._set_pkg_proposals_status(
                "[yellow]Highlight a package proposal first.[/yellow]"
            )
            return
        verb = "approve" if approve else "reject"
        self.app.post_message(
            _OversightAction(action=verb, oversight_id=oid),
        )
        self._set_pkg_proposals_status(
            f"[green]✓ {verb} dispatched for {oid[:14]}[/green]"
        )

    def _run_self_challenge(self) -> None:
        """Red-team the Cat-A constitution: write the audit doc, emit
        Cat-B/C mitigation proposals, and open the report."""
        from acc.governance_inventory import load_all_layers  # noqa: PLC0415
        from acc.self_challenge import (  # noqa: PLC0415
            challenge_cat_a, dump_challenge_report, proposals_from_challenge,
        )
        try:
            report = challenge_cat_a(load_all_layers())
            json_path = dump_challenge_report(report)
            n = len(proposals_from_challenge(report))
        except Exception as exc:
            self._set_proposals_status(f"[red]self-challenge failed: {exc}[/red]")
            return
        self._refresh_proposals()
        self._set_proposals_status(
            f"[green]✓ self-challenge[/green] {report.total} findings, "
            f"{n} mitigation proposal(s) "
            f"[dim]→ {json_path.with_suffix('.md').name}[/dim]"
        )
        from acc.tui.widgets.policy_viewer_modal import (  # noqa: PLC0415
            PolicyViewerModal,
        )
        self.app.push_screen(PolicyViewerModal(json_path.with_suffix(".md")))

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        if snap is None:
            return
        self._render_owasp_table(snap)
        self._render_health_score(snap)
        self._render_oversight_queue(snap)
        self._render_pkg_proposals(snap)
        self._render_violation_log(snap)
        self._render_overlay_profiles(snap)

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _render_owasp_table(self, snap: "CollectiveSnapshot") -> None:
        """Populate OWASP grading table from violation log (REQ-TUI-023)."""
        table = self.query_one("#owasp-table", DataTable)
        table.clear()
        grades = _compute_owasp_grades(snap.owasp_violation_log)
        for code, desc in _OWASP_CODES:
            grade, pass_rate = grades.get(code, ("A", 1.0))
            colour = (
                "green" if grade == "A"
                else "yellow" if grade in ("B", "C")
                else "red"
            )
            table.add_row(
                code,
                f"[{colour}]{grade}[/{colour}]",
                f"{pass_rate * 100:.0f}%",
                desc,
            )

    def _render_overlay_profiles(self, snap: "CollectiveSnapshot") -> None:
        """Render the per-agent effective-overlay table (read-only).

        Sourced purely from each agent's HEARTBEAT-published
        ``overlay_summary`` (proposal agent-personalization-overlay); agents
        with no overlay (empty summary) are skipped, so the table lists only
        roles an operator/project has actually personalised.
        """
        table = self.query_one("#overlay-profiles-table", DataTable)
        table.clear()
        for agent_id, agent in sorted(snap.agents.items()):
            summary = getattr(agent, "overlay_summary", None) or {}
            if not summary:
                continue
            enabled = summary.get("enabled") or []
            local = summary.get("local_grants") or []
            dropped = int(summary.get("dropped", 0) or 0)
            profile = summary.get("user_profile") or "—"
            enabled_txt = ", ".join(enabled) if enabled else "—"
            local_txt = (
                f"[yellow]{', '.join(local)}[/yellow]" if local else "—"
            )
            dropped_txt = f"[red]{dropped}[/red]" if dropped else "0"
            table.add_row(
                agent_id[:12],
                agent.role or "—",
                profile,
                enabled_txt,
                local_txt,
                dropped_txt,
                key=agent_id,
            )

    def _render_health_score(self, snap: "CollectiveSnapshot") -> None:
        """Render compliance health score bar (REQ-TUI-024)."""
        score = snap.compliance_health_score
        pct = score * 100
        colour = "green" if score >= 0.80 else "yellow" if score >= 0.50 else "red"

        self.query_one("#health-score-value", Static).update(
            f"[{colour}]{score:.4f}[/{colour}]  [{pct:.0f}/100]"
        )
        bar = self.query_one("#health-progress-bar", ProgressBar)
        bar.progress = pct

    def _render_oversight_queue(self, snap: "CollectiveSnapshot") -> None:
        """Populate oversight queue DataTable (REQ-TUI-025).

        Items come from the arbiter's HEARTBEAT — ``oversight_pending_items``
        is a list of dicts mirroring the public surface of
        :class:`acc.oversight.OversightItem`.  Each row's key is the real
        ``oversight_id`` so the approve/reject actions can pick it up.

        Falls back to the legacy per-agent count rendering when the new
        list is empty (mixed-version deployments where some arbiters
        haven't been redeployed yet).
        """
        table = self.query_one("#oversight-table", DataTable)
        table.clear()
        # PR-H — cache the full item dicts keyed by oversight_id so the
        # detail panel can render rich context without re-walking the
        # snapshot on every cursor move.
        self._pending_items_by_id: dict[str, dict] = {}

        items = snap.oversight_pending_items or []
        if items:
            for item in items:
                if item.get("status", "PENDING") != "PENDING":
                    continue
                oid = str(item.get("oversight_id", ""))
                if not oid:
                    continue
                self._pending_items_by_id[oid] = dict(item)
                submitted_ms = int(item.get("submitted_at_ms") or 0)
                ts_str = (
                    time.strftime("%H:%M:%S", time.localtime(submitted_ms / 1000.0))
                    if submitted_ms
                    else "—"
                )
                summary_full = str(item.get("summary") or "")
                summary_cell = (
                    summary_full[:40] + "…" if len(summary_full) > 40
                    else summary_full or "—"
                )
                table.add_row(
                    oid[:14],
                    str(item.get("agent_id", ""))[:16],
                    str(item.get("risk_level", "HIGH")),
                    ts_str,
                    summary_cell,
                    "PENDING",
                    key=oid,
                )
            # PR-H — refresh detail panel against the (possibly new) cursor
            # row so the operator sees something useful immediately after
            # the snapshot tick, without an explicit cursor move.
            self._refresh_detail_for_cursor()
            return

        # Legacy fallback: aggregate per-agent count when no per-item list
        # is available (e.g. arbiter HEARTBEAT not yet upgraded).
        for agent_id, agent in snap.agents.items():
            if agent.oversight_pending_count > 0:
                table.add_row(
                    f"ov-{agent_id[:8]}",
                    agent_id[:16],
                    "HIGH",
                    time.strftime("%H:%M:%S"),
                    "[dim]legacy aggregate — no per-item detail[/dim]",
                    f"{agent.oversight_pending_count} pending",
                    key=f"agg-{agent_id}",
                )
        # No PENDING items at all → clear the detail panel back to the
        # placeholder so a stale prior selection doesn't mislead.
        if not self._pending_items_by_id:
            self._render_oversight_detail(None)

    # ------------------------------------------------------------------
    # PR-H — master/detail context renderer
    # ------------------------------------------------------------------

    # Risk levels / summary substrings that demand a confirmation modal
    # before the operator's Approve actually publishes the decision.
    # Reject is never gated — withholding consent is always safe.
    _HIGH_CONSEQUENCE_RISK = frozenset({"HIGH", "CRITICAL", "UNACCEPTABLE"})
    _HIGH_CONSEQUENCE_SUMMARY_MARKERS = (
        "CRITICAL invocation",
        "delete", "destroy", "drop", "rm ",
        "A-017", "A-018",  # ACC's hardcoded Cat-A skill/MCP gates
        "spawn",
        "external network",
    )

    @classmethod
    def _is_high_consequence(cls, item: dict) -> bool:
        """Decide whether an Approve action needs the confirmation modal.

        PR-H rule: any of
        * ``risk_level`` in ``{HIGH, CRITICAL, UNACCEPTABLE}``, OR
        * ``summary`` contains a known dangerous marker
          (case-insensitive substring of the gate reason).
        Reject never needs confirmation — pulling consent is always
        safe.  Returns ``False`` for unknown / aggregate rows.
        """
        if not item:
            return False
        risk = str(item.get("risk_level") or "").upper()
        if risk in cls._HIGH_CONSEQUENCE_RISK:
            return True
        summary = str(item.get("summary") or "").lower()
        return any(
            marker.lower() in summary
            for marker in cls._HIGH_CONSEQUENCE_SUMMARY_MARKERS
        )

    def _refresh_detail_for_cursor(self) -> None:
        """Re-render the detail panel against the table's current cursor.

        Called on snapshot ticks (to keep the panel fresh against the
        latest item state) and on RowHighlighted events (cursor move).
        Best-effort — a missing widget or empty cache renders the
        placeholder instead of raising.
        """
        try:
            table = self.query_one("#oversight-table", DataTable)
        except Exception:
            return
        if table.row_count == 0:
            self._render_oversight_detail(None)
            return
        try:
            row_key = table.coordinate_to_cell_key(
                table.cursor_coordinate,
            ).row_key
            value = getattr(row_key, "value", None) or str(row_key)
        except Exception:
            self._render_oversight_detail(None)
            return
        if not value or value.startswith("agg-"):
            self._render_oversight_detail(None)
            return
        item = getattr(self, "_pending_items_by_id", {}).get(str(value))
        self._render_oversight_detail(item)

    def _render_oversight_detail(self, item: dict | None) -> None:
        """Update ``#oversight-detail`` with the highlighted item's
        full context — or restore the placeholder when no row is
        selected / cache lookup misses.

        Rendered as a rich-markup multi-line block so the operator can
        see at a glance:

        * Identity — agent_id, oversight_id, task_id.
        * Risk classification — risk_level (colour-coded).
        * Submitted timestamp.
        * Gate reason — the agent's free-text summary explaining WHY
          this item is gated; the most important field for an informed
          approve/reject.
        * Approve preview / Reject preview — one-line summary of what
          each action will publish on NATS.
        * High-consequence banner when the row qualifies for the
          confirm-modal path.
        """
        try:
            panel = self.query_one("#oversight-detail", Static)
        except Exception:
            return
        if not item:
            panel.update(
                "[dim]Highlight a row above to see its full context "
                "(gate reason, payload preview, consequence of Approve "
                "vs Reject).[/dim]"
            )
            return

        oid = str(item.get("oversight_id", "—"))
        agent_id = str(item.get("agent_id", "—"))
        task_id = str(item.get("task_id", "—"))
        risk = str(item.get("risk_level") or "HIGH").upper()
        summary = str(item.get("summary") or "—")
        submitted_ms = int(item.get("submitted_at_ms") or 0)
        submitted = (
            time.strftime(
                "%H:%M:%S",
                time.localtime(submitted_ms / 1000.0),
            )
            if submitted_ms else "—"
        )

        risk_colour = (
            "red" if risk in {"CRITICAL", "UNACCEPTABLE"}
            else "yellow" if risk == "HIGH"
            else "green"
        )

        approve_preview = (
            f"publish [b]OVERSIGHT_DECISION[/b] decision=APPROVE "
            f"oversight_id={oid[:12]} approver_id=<operator> — "
            f"agent [b]{agent_id}[/b] resumes task [b]{task_id[:12]}[/b]."
        )
        reject_preview = (
            f"publish [b]OVERSIGHT_DECISION[/b] decision=REJECT "
            f"oversight_id={oid[:12]} approver_id=<operator> — "
            f"agent [b]{agent_id}[/b] aborts task [b]{task_id[:12]}[/b]; "
            f"TASK_COMPLETE will carry blocked=True, "
            f"block_reason='oversight rejected'."
        )

        consequence_banner = ""
        if self._is_high_consequence(item):
            consequence_banner = (
                "\n[red on white][b] ⚠ HIGH-CONSEQUENCE [/b][/red on white]  "
                "[red]Approve will require an explicit confirmation; "
                "Reject does not.[/red]\n"
            )

        block = (
            f"[b]oversight_id:[/b] {oid}\n"
            f"[b]agent_id:[/b]     {agent_id}\n"
            f"[b]task_id:[/b]      {task_id}\n"
            f"[b]risk_level:[/b]   [{risk_colour}]{risk}[/{risk_colour}]\n"
            f"[b]submitted:[/b]    {submitted}\n"
            f"\n"
            f"[b]Gate reason[/b]\n"
            f"  {summary}\n"
            f"{consequence_banner}\n"
            f"[b]On [green]Approve[/green][/b] (key: [bold]a[/bold]) →\n"
            f"  {approve_preview}\n"
            f"\n"
            f"[b]On [yellow]Reject[/yellow][/b] (key: [bold]r[/bold]) →\n"
            f"  {reject_preview}"
        )
        panel.update(block)

    def on_data_table_row_highlighted(
        self, event: "DataTable.RowHighlighted",
    ) -> None:
        """PR-H — refresh the detail panel as the operator scrolls
        through the oversight queue.  Other DataTables on this screen
        (the OWASP grading table) are show_cursor=False so they don't
        fire RowHighlighted; the table-id filter is defensive."""
        if event.data_table.id != "oversight-table":
            return
        row_key = event.row_key
        value = getattr(row_key, "value", None) or str(row_key)
        if not value or value.startswith("agg-"):
            self._render_oversight_detail(None)
            return
        item = getattr(self, "_pending_items_by_id", {}).get(str(value))
        self._render_oversight_detail(item)

    def on_data_table_row_selected(
        self, event: "DataTable.RowSelected",
    ) -> None:
        """PR-Z1b — selecting a governance rule row opens the source
        policy file in a read-only viewer.  Only the gov-table-* tables
        react; the oversight table uses `a`/`r` instead of RowSelected."""
        table_id = getattr(event.data_table, "id", "") or ""
        if not table_id.startswith("gov-table-"):
            return
        row_key = event.row_key
        value = getattr(row_key, "value", None) or str(row_key)
        rule = getattr(self, "_gov_rules_by_key", {}).get(str(value))
        if rule is None:
            return
        from acc.tui.widgets.policy_viewer_modal import (  # noqa: PLC0415
            PolicyViewerModal,
        )
        self.app.push_screen(
            PolicyViewerModal(rule.source_path, highlight_line=rule.line),
        )

    def _render_violation_log(self, snap: "CollectiveSnapshot") -> None:
        """Render scrollable violation log (REQ-TUI-027)."""
        if not snap.owasp_violation_log:
            self.query_one("#violation-log", Static).update(
                "[dim]No violations recorded this session.[/dim]"
            )
            return

        lines: list[str] = []
        for entry in reversed(snap.owasp_violation_log[-50:]):
            ts_str = time.strftime(
                "%H:%M:%S", time.localtime(entry.get("ts", 0))
            )
            code = entry.get("code", "?")
            agent = entry.get("agent_id", "?")[:12]
            risk = entry.get("risk_level", "?")
            pattern = entry.get("pattern", "")[:40]
            colour = "red" if risk in ("HIGH", "CRITICAL") else "yellow"
            lines.append(
                f"[dim]{ts_str}[/dim]  [{colour}]{code}[/{colour}]"
                f"  {agent}  {risk}  {pattern}"
            )

        self.query_one("#violation-log", Static).update("\n".join(lines))

    # ------------------------------------------------------------------
    # Actions (REQ-TUI-026)
    # ------------------------------------------------------------------

    async def action_approve_oversight(self) -> None:
        """Approve the selected oversight queue item via NATS (REQ-TUI-026).

        PR-H — when the highlighted item is *high-consequence*
        (:meth:`_is_high_consequence`), open the confirmation modal
        first; the operator must explicitly press ``Confirm Approve``
        before the OVERSIGHT_DECISION is published.  Cancelling the
        modal (Escape / Cancel button) leaves the item PENDING and
        publishes nothing.  Reject is never gated."""
        oid = self._selected_oversight_id()
        if oid is None:
            return
        item = getattr(self, "_pending_items_by_id", {}).get(oid)
        if item and self._is_high_consequence(item):
            from acc.tui.widgets.oversight_confirm_modal import (  # noqa: PLC0415
                OversightConfirmModal,
            )

            def _on_confirm(confirmed: bool | None) -> None:
                """Callback resolved when the modal dismisses with a
                bool: ``True`` means the operator pressed
                ``Confirm Approve`` → publish the OVERSIGHT_DECISION;
                anything else (Cancel, Escape, X-close) → no-op so the
                item stays PENDING."""
                if confirmed:
                    self.app.post_message(
                        _OversightAction(action="approve", oversight_id=oid),
                    )

            # Callback form (matches the codebase's existing modal
            # pattern in configuration.py / prompt.py — no
            # ``push_screen_wait`` dependency on a specific Textual
            # minor version).
            self.app.push_screen(
                OversightConfirmModal(item),
                _on_confirm,
            )
            return
        # The app's observer handles publishing; delegate up
        self.app.post_message(_OversightAction(action="approve", oversight_id=oid))

    async def action_reject_oversight(self) -> None:
        """Reject the selected oversight queue item via NATS (REQ-TUI-026)."""
        oid = self._selected_oversight_id()
        if oid is None:
            return
        self.app.post_message(_OversightAction(action="reject", oversight_id=oid))

    def _selected_oversight_id(self) -> str | None:
        """Return the oversight_id of the currently-highlighted table row.

        Returns ``None`` when the table is empty or the cursor lands on a
        legacy fallback row (key prefix ``agg-``) that has no per-item id.
        """
        table = self.query_one("#oversight-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            value = row_key.value if hasattr(row_key, "value") else str(row_key)
            if not value or value.startswith("agg-"):
                return None
            return str(value)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

from textual.message import Message  # noqa: E402


class _OversightAction(Message):
    """Request an oversight approve/reject action.

    Attributes:
        action: ``"approve"`` or ``"reject"`` — the operator decision.
        oversight_id: Identifier of the item the operator has highlighted.
            Empty string when the legacy aggregate fallback is in use; the
            App handler skips publishing in that case.
        reason: Optional free-text rejection reason (Phase 1.3 keeps it
            empty; future TUI prompt can populate it).
    """

    def __init__(self, action: str, oversight_id: str = "", reason: str = "") -> None:
        super().__init__()
        self.action = action  # "approve" | "reject"
        self.oversight_id = oversight_id
        self.reason = reason

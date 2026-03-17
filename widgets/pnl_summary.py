"""
Portfolio P&L summary panel widget.

Shows aggregate unrealized P&L, realized P&L, and total portfolio value
across all open positions. Updates reactively on every ticker event.
"""

from textual.app import ComposeResult
from textual.widgets import Static
from textual.reactive import reactive

from app.pnl import PortfolioSummary, format_pnl, format_pnl_pct


class PnlSummary(Static):
    """
    A compact summary bar showing portfolio-level P&L metrics.
    """

    DEFAULT_CSS = """
    PnlSummary {
        height: 3;
        border: solid $primary;
        padding: 0 1;
        layout: horizontal;
    }
    PnlSummary .summary-item {
        width: 1fr;
        content-align: center middle;
    }
    .pnl-positive { color: $success; }
    .pnl-negative { color: $error; }
    .pnl-neutral  { color: $text-muted; }
    """

    _summary: reactive[PortfolioSummary | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Static("Unrealized: —", id="unrealized", classes="summary-item")
        yield Static("Realized: —", id="realized", classes="summary-item")
        yield Static("Open Value: —", id="open-value", classes="summary-item")
        yield Static("Positions: 0", id="pos-count", classes="summary-item")

    def update_summary(self, summary: PortfolioSummary) -> None:
        """Refresh all summary labels with the latest open-positions data."""
        upnl = summary.total_unrealized_pnl
        rpnl = summary.total_realized_pnl

        upnl_color = "pnl-positive" if upnl >= 0 else "pnl-negative"
        rpnl_color = "pnl-positive" if rpnl >= 0 else "pnl-negative"

        self.query_one("#unrealized", Static).update(
            f"Unrealized: [{upnl_color}]{format_pnl(upnl)} "
            f"({format_pnl_pct(summary.overall_pnl_pct)})[/{upnl_color}]"
        )
        self.query_one("#realized", Static).update(
            f"Realized: [{rpnl_color}]{format_pnl(rpnl)}[/{rpnl_color}]"
        )
        self.query_one("#open-value", Static).update(
            f"Open Value: ${summary.total_current_value:,.2f}"
        )
        self.query_one("#pos-count", Static).update(
            f"Positions: {summary.position_count}"
        )

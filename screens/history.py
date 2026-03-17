"""
History screen — closed positions and realized P&L over time.

Reads from the local SQLite database. Refreshes on a configurable interval
and also refreshes automatically when a new fill arrives via WebSocket.

Layout:
  - Summary bar (total realized P&L, fees, position count)
  - P&L line chart (cumulative realized P&L over time)
  - Closed positions table
"""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, DataTable
from textual.containers import Vertical
from textual_plotext import PlotextPlot

from app import database as db
from app.config import HISTORY_REFRESH_SECONDS


class PnlChart(PlotextPlot):
    """
    Cumulative realized P&L line chart.

    X axis: close date of each position (ISO date string)
    Y axis: cumulative realized P&L in USD (positive and negative)

    The zero baseline is always drawn so gains and losses are visually clear.
    The line colour is green when net P&L is positive, red when negative.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._dates: list[str] = []
        self._pnl: list[float] = []

    def on_mount(self) -> None:
        self.plt.date_form("Y-m-d")
        self.plt.title("Cumulative Realized P&L")
        self.plt.xlabel("Date")
        self.plt.ylabel("P&L (USD)")

    def update_data(self, dates: list[str], pnl: list[float]) -> None:
        """Replace chart data and redraw."""
        self._dates = dates
        self._pnl = pnl
        self._replot()

    def _replot(self) -> None:
        self.plt.clear_data()

        if not self._dates:
            self.refresh()
            return

        # Zero baseline — makes gains/losses immediately readable
        self.plt.horizontal_line(0, color="white")

        color = "green" if self._pnl[-1] >= 0 else "red"
        self.plt.plot(self._dates, self._pnl, color=color, marker="braille")

        self.refresh()


class HistoryScreen(Screen):
    """
    Displays a cumulative P&L chart and closed positions table.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
    ]

    DEFAULT_CSS = """
    HistoryScreen {
        layout: vertical;
    }
    HistoryScreen .summary-bar {
        height: 3;
        border: solid $primary;
        padding: 0 1;
        content-align: center middle;
    }
    HistoryScreen PnlChart {
        height: 20;
        border: solid $primary;
        margin-bottom: 1;
    }
    HistoryScreen .section-title {
        color: $primary;
        text-style: bold;
        padding: 0 1;
        height: 1;
    }
    HistoryScreen DataTable {
        height: 1fr;
    }
    .pnl-positive { color: $success; }
    .pnl-negative { color: $error; }
    """

    COLUMNS = [
        ("symbol", "Symbol"),
        ("size", "Size"),
        ("entry", "Avg Entry"),
        ("close_price", "Close Price"),
        ("realized_pnl", "Realized P&L"),
        ("fees", "Fees Paid"),
        ("opened", "Opened"),
        ("closed", "Closed"),
        ("duration", "Duration"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("", id="summary-bar", classes="summary-bar")
            yield PnlChart(id="pnl-chart")
            yield Static("● Closed Positions", classes="section-title")
            table = DataTable(id="history-table", cursor_type="row")
            for col_key, col_label in self.COLUMNS:
                table.add_column(col_label, key=col_key)
            yield table
        yield Footer()

    def on_mount(self) -> None:
        self.load_history()
        self.set_interval(HISTORY_REFRESH_SECONDS, self.load_history)

    def load_history(self) -> None:
        """Load closed positions, populate the table, and redraw the chart."""
        positions = db.get_closed_positions(limit=200)

        self._populate_table(positions)
        self._update_summary(positions)
        self._update_chart(positions)

    def _populate_table(self, positions) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear()

        for pos in positions:
            duration = "—"
            if pos.opened_at and pos.closed_at:
                delta = pos.closed_at - pos.opened_at
                hours = int(delta.total_seconds() // 3600)
                minutes = int((delta.total_seconds() % 3600) // 60)
                duration = f"{hours}h {minutes}m"

            pnl_color = "pnl-positive" if pos.realized_pnl >= 0 else "pnl-negative"
            sign = "+" if pos.realized_pnl >= 0 else ""

            table.add_row(
                pos.symbol,
                "—",
                f"${pos.avg_entry_price:,.2f}",
                "—",
                f"[{pnl_color}]{sign}${pos.realized_pnl:,.2f}[/{pnl_color}]",
                f"${pos.total_fees_paid:,.4f}",
                pos.opened_at.strftime("%Y-%m-%d %H:%M") if pos.opened_at else "—",
                pos.closed_at.strftime("%Y-%m-%d %H:%M") if pos.closed_at else "—",
                duration,
                key=str(pos.id),
            )

    def _update_summary(self, positions) -> None:
        total_realized = sum(p.realized_pnl for p in positions)
        total_fees = sum(p.total_fees_paid for p in positions)
        pnl_color = "pnl-positive" if total_realized >= 0 else "pnl-negative"
        sign = "+" if total_realized >= 0 else ""
        self.query_one("#summary-bar", Static).update(
            f"Closed Positions: {len(positions)}  │  "
            f"Total Realized P&L: [{pnl_color}]{sign}${total_realized:,.2f}[/{pnl_color}]  │  "
            f"Total Fees: ${total_fees:,.4f}"
        )

    def _update_chart(self, positions) -> None:
        """
        Build a cumulative P&L series from closed positions sorted by close date.
        Each point represents the running total after each position closes.
        """
        # Sort oldest-first so the line reads left-to-right chronologically
        closed = [p for p in positions if p.closed_at is not None]
        closed.sort(key=lambda p: p.closed_at)

        if not closed:
            self.query_one(PnlChart).update_data([], [])
            return

        dates: list[str] = []
        pnl: list[float] = []
        cumulative = 0.0

        # Anchor the chart at zero on the day before the first close
        # so the line visibly starts from a baseline rather than mid-chart
        from datetime import timedelta
        first_date = closed[0].closed_at.date() - timedelta(days=1)
        dates.append(first_date.isoformat())
        pnl.append(0.0)

        for pos in closed:
            cumulative += pos.realized_pnl
            dates.append(pos.closed_at.date().isoformat())
            pnl.append(round(cumulative, 4))

        self.query_one(PnlChart).update_data(dates, pnl)

    def action_refresh(self) -> None:
        self.load_history()

    def notify_new_fill(self) -> None:
        """Called by the app when a new fill arrives via WebSocket."""
        self.load_history()

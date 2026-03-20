"""
Dashboard screen — the main view of the application.

Shows:
- Active open positions with live P&L (updated via WebSocket ticker)
- Portfolio P&L summary bar
- Risk management panel
- Quick-action keybindings to open trade/add-to-position flows
"""

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Header, Footer, Static
from textual.containers import Vertical

from widgets.position_table import PositionTable
from widgets.pnl_summary import PnlSummary
from widgets.risk_panel import RiskPanel
from app.pnl import PositionSnapshot, PortfolioSummary


class IndicatorsBar(Static):
    DEFAULT_CSS = """
    IndicatorsBar {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """


class DashboardScreen(Screen):
    """Main dashboard: positions, P&L, and risk overview."""

    BINDINGS = [
        Binding("b", "buy", "Buy", priority=True),
        Binding("s", "sell", "Sell", priority=True),
        Binding("a", "add_to_position", "Add to Position", priority=True),
        Binding("c", "close_position", "Close Position", priority=True),
        Binding("l", "set_stop_loss", "Set Stop-Loss", priority=True),
        Binding("4", "show_alerts", "Alerts", priority=True),
    ]

    DEFAULT_CSS = """
    DashboardScreen {
        layout: vertical;
    }
    DashboardScreen .section-title {
        color: $primary;
        text-style: bold;
        padding: 0 1;
        height: 1;
    }
    DashboardScreen .no-positions {
        color: $text-muted;
        padding: 1;
        height: 3;
        content-align: center middle;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield PnlSummary(id="pnl-summary")
            yield IndicatorsBar("", id="indicators-bar")
            yield Static("● Active Positions", classes="section-title")
            yield PositionTable(id="position-table")
            yield Static("● Risk Management", classes="section-title")
            yield RiskPanel(id="risk-panel")
        yield Footer()

    # -----------------------------------------------------------------------
    # Update methods — called by the app when WebSocket data arrives
    # -----------------------------------------------------------------------

    def update_positions(
        self,
        snapshots: list[PositionSnapshot],
        summary: PortfolioSummary,
    ) -> None:
        """Refresh position table, P&L summary, and risk panel."""
        self._snapshots = snapshots
        self.query_one(PositionTable).update_snapshots(snapshots)
        self.query_one(PnlSummary).update_summary(summary)
        self.query_one(RiskPanel).update_snapshots(snapshots)

    def update_indicators(
        self,
        vwap: float | None,
        rsi: float | None,
        atr: float | None,
        current_price: float = 0.0,
    ) -> None:
        """Update the indicators bar with latest VWAP, RSI, and ATR values."""
        if vwap is not None:
            vwap_color = "green" if vwap < current_price else "red"
            vwap_str = f"[{vwap_color}]VWAP: ${vwap:,.2f}[/{vwap_color}]"
        else:
            vwap_str = "VWAP: —"
        if rsi is not None:
            if rsi >= 70:
                rsi_color = "red"
            elif rsi >= 60:
                rsi_color = "dark_orange"
            elif rsi <= 30:
                rsi_color = "green"
            elif rsi <= 40:
                rsi_color = "dark_sea_green"
            else:
                rsi_color = None
            rsi_val = f"RSI(14): {rsi:.1f}"
            rsi_str = f"[{rsi_color}]{rsi_val}[/{rsi_color}]" if rsi_color else rsi_val
        else:
            rsi_str = "RSI(14): —"
        atr_str = f"ATR(14): ${atr:,.2f}" if atr is not None else "ATR(14): —"
        self.query_one("#indicators-bar", IndicatorsBar).update(
            f"{vwap_str}  ·  {rsi_str}  ·  {atr_str}"
        )

    def on_mount(self) -> None:
        self._snapshots: list[PositionSnapshot] = []

    # -----------------------------------------------------------------------
    # Actions — delegate to the parent app for screen navigation
    # -----------------------------------------------------------------------

    _READ_ONLY_MSG = "Read-only session — close the other session to enable trading"

    def _is_read_only(self) -> bool:
        return getattr(self.app, "_read_only", False)

    def action_buy(self) -> None:
        if self._is_read_only():
            self.notify(self._READ_ONLY_MSG, severity="warning")
            return
        self.app.push_screen("trade_buy")

    def action_sell(self) -> None:
        if self._is_read_only():
            self.notify(self._READ_ONLY_MSG, severity="warning")
            return
        self.app.push_screen("trade_sell")

    def action_add_to_position(self) -> None:
        """Pre-fill the buy form with the currently selected position's symbol."""
        if self._is_read_only():
            self.notify(self._READ_ONLY_MSG, severity="warning")
            return
        table = self.query_one(PositionTable)
        symbol = table.get_selected_symbol()
        self.app.open_add_to_position(symbol)

    def action_close_position(self) -> None:
        """Pre-fill the sell form with the currently selected position."""
        if self._is_read_only():
            self.notify(self._READ_ONLY_MSG, severity="warning")
            return
        table = self.query_one(PositionTable)
        symbol = table.get_selected_symbol()
        self.app.open_close_position(symbol)

    def action_set_stop_loss(self) -> None:
        """Open the stop-loss modal for the currently selected position."""
        if self._is_read_only():
            self.notify(self._READ_ONLY_MSG, severity="warning")
            return
        symbol = self.query_one(PositionTable).get_selected_symbol()
        if not symbol:
            self.notify("Select a position first", severity="warning")
            return

        # Find the snapshot for this symbol
        snap = next((s for s in self._snapshots if s.symbol == symbol), None)
        if not snap:
            return

        from screens.stop_loss_modal import StopLossModal

        def _on_confirm(stop_price: Optional[float]) -> None:
            self.app.set_stop_loss_for_symbol(symbol, stop_price)

        self.app.push_screen(
            StopLossModal(
                symbol=snap.symbol,
                avg_entry=snap.avg_entry_price,
                current_price=snap.current_price,
                current_stop=snap.suggested_stop_price,
                stop_is_manual=snap.stop_is_manual,
                on_confirm=_on_confirm,
            )
        )

    def action_show_alerts(self) -> None:
        self.app.push_screen("alerts")

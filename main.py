"""
tui-trader — Kraken Pro trading TUI

Entry point. Wires together:
- WebSocket stream workers (ticker, order book, private fills)
- Screen navigation (dashboard, trade, order book, history, alerts)
- Reactive state (live prices, positions, balance)
- Alert evaluation on every ticker event
- Local database sync on fills

Usage:
    python main.py

Keyboard shortcuts (global):
    1  — Dashboard
    2  — Trade (buy)
    3  — Order Book
    4  — Alerts
    5  — History
    b  — Buy (from dashboard)
    s  — Sell (from dashboard)
    a  — Add to position (from dashboard)
    q  — Quit
"""

import logging
from typing import Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer
from textual.reactive import reactive

from app.config import DEFAULT_SYMBOL, DEFAULT_STOP_LOSS_PCT
from app import database as db
from app.models import Position
from app.pnl import (
    calculate_snapshot,
    calculate_portfolio_summary,
    PositionSnapshot,
)
from app.alerts import AlertManager
from app.notifications import send_notification
from app.streams import stream_manager
from app import exchange as kraken_rest

from screens.dashboard import DashboardScreen
from screens.trade import TradeScreen
from screens.orderbook import OrderBookScreen
from screens.history import HistoryScreen
from screens.alerts_screen import AlertsScreen

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


class TradeApp(App):
    """
    Main Textual application.

    Manages WebSocket workers and routes live data to the appropriate screens.
    """

    TITLE = "tui-trader"
    SUB_TITLE = f"Kraken Pro  ·  {DEFAULT_SYMBOL}"

    CSS = """
    Screen {
        background: $surface;
    }
    Header {
        background: $primary-darken-2;
    }
    """

    BINDINGS = [
        ("1", "switch_screen('dashboard')", "Dashboard"),
        ("2", "push_screen('trade_buy')", "Buy"),
        ("3", "push_screen('orderbook')", "Order Book"),
        ("4", "push_screen('alerts')", "Alerts"),
        ("5", "push_screen('history')", "History"),
        ("q", "quit", "Quit"),
    ]

    # ---------------------------------------------------------------------------
    # Reactive state — updated by WebSocket workers, read by screens/widgets
    # ---------------------------------------------------------------------------

    _current_price: reactive[float] = reactive(0.0)
    _free_usd: float = 0.0                    # uninvested USD cash on Kraken
    _asset_balances: dict[str, float] = {}    # all non-USD holdings: {"BTC": 0.5, ...}
    _open_positions: list[Position] = []

    def __init__(self) -> None:
        super().__init__()
        self._symbol = DEFAULT_SYMBOL
        self._prices: dict[str, float] = {}  # last known price per symbol
        self._asset_balances = {}
        self._alert_manager = AlertManager(on_trigger=self._on_alert_triggered)

    # ---------------------------------------------------------------------------
    # App lifecycle
    # ---------------------------------------------------------------------------

    def on_mount(self) -> None:
        """Initialise DB, load state, start WebSocket workers."""
        db.init_db()
        self._alert_manager.reload()
        self._open_positions = db.get_open_positions()

        # Fetch full wallet balance via REST on startup so portfolio value and
        # risk % are correct from the first ticker update, before the private
        # WebSocket stream has connected.
        try:
            balance = kraken_rest.fetch_balance()
            self._free_usd = float(balance.get("USD", {}).get("total") or 0)
            self._asset_balances = {
                currency: float(amounts.get("total") or 0)
                for currency, amounts in balance.items()
                if isinstance(amounts, dict)
                and currency != "USD"
                and float(amounts.get("total") or 0) > 0
            }
        except Exception as e:
            log.warning("Could not fetch initial balance: %s", e)

        # Start WebSocket workers
        self.run_worker(
            stream_manager.ticker_worker(self, self._symbol),
            exclusive=False,
            name="ticker",
        )
        self.run_worker(
            stream_manager.orderbook_worker(self, self._symbol),
            exclusive=False,
            name="orderbook",
        )
        self.run_worker(
            stream_manager.private_worker(self),
            exclusive=False,
            name="private",
        )

    async def on_unmount(self) -> None:
        """Clean up WebSocket connections on exit."""
        await stream_manager.close()

    # ---------------------------------------------------------------------------
    # Screen registration
    # ---------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # Screens are registered here; the app starts on the dashboard
        yield Header()
        yield Footer()

    def on_ready(self) -> None:
        """Register all screens after the app is ready."""
        self.install_screen(DashboardScreen(), name="dashboard")
        self.install_screen(TradeScreen(side="buy"), name="trade_buy")
        self.install_screen(TradeScreen(side="sell"), name="trade_sell")
        self.install_screen(OrderBookScreen(), name="orderbook")
        self.install_screen(HistoryScreen(), name="history")
        self.install_screen(AlertsScreen(self._alert_manager), name="alerts")
        self.push_screen("dashboard")

    # ---------------------------------------------------------------------------
    # WebSocket event handlers — called by stream workers
    # ---------------------------------------------------------------------------

    def on_ticker_update(self, ticker) -> None:
        """
        Fired on every WebSocket ticker event.
        Updates live price, recalculates P&L, evaluates alerts.
        """
        price = float(ticker.get("last") or 0)
        if price <= 0:
            return

        self._current_price = price
        self._prices[self._symbol] = price

        # Evaluate price alerts
        self._alert_manager.evaluate(self._symbol, price)

        # Recalculate P&L snapshots and push to dashboard
        self._refresh_dashboard(price)

        # Update trade screen price display if it's active
        try:
            trade_screen = self.get_screen("trade_buy")
            if self.screen is trade_screen:
                trade_screen.update_price(self._symbol, price)
        except Exception:
            pass
        try:
            trade_screen = self.get_screen("trade_sell")
            if self.screen is trade_screen:
                trade_screen.update_price(self._symbol, price)
        except Exception:
            pass

    def on_orderbook_update(self, orderbook) -> None:
        """Fired on every WebSocket order book update."""
        try:
            ob_screen = self.get_screen("orderbook")
            if self.screen is ob_screen:
                # Pass current position levels for annotation
                pos = next(
                    (p for p in self._open_positions if p.symbol == self._symbol),
                    None,
                )
                if pos:
                    from app.pnl import calculate_snapshot
                    snap = calculate_snapshot(pos, self._current_price, 1.0, DEFAULT_STOP_LOSS_PCT)
                    ob_screen.set_position_levels(pos.avg_entry_price, snap.suggested_stop_price)
                else:
                    ob_screen.set_position_levels(None, None)
                ob_screen.update_orderbook(self._symbol, orderbook)
        except Exception:
            pass

    def on_orders_update(self, orders) -> None:
        """Fired when order status changes (fill, cancel, etc.)."""
        # Reload open positions in case a fill changed them
        self._open_positions = db.get_open_positions()
        self._refresh_dashboard(self._current_price)

    def on_balance_update(self, balance) -> None:
        """Fired when account balance changes (after a fill)."""
        self._free_usd = float(balance.get("USD", {}).get("total") or 0)
        self._asset_balances = {
            currency: float(amounts.get("total") or 0)
            for currency, amounts in balance.items()
            if isinstance(amounts, dict)
            and currency != "USD"
            and float(amounts.get("total") or 0) > 0
        }

    def on_my_trades_update(self, trades) -> None:
        """
        Fired when a new fill arrives via the private executions channel.
        Notifies the history screen to reload.
        """
        try:
            history_screen = self.get_screen("history")
            history_screen.notify_new_fill()
        except Exception:
            pass

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _refresh_dashboard(self, price: float) -> None:
        """Recalculate all position snapshots and push to the dashboard."""
        if price <= 0:
            return

        # Resolve live price for every open position
        pos_prices: dict[str, float] = {}
        for pos in self._open_positions:
            pos_prices[pos.symbol] = self._prices.get(pos.symbol, pos.avg_entry_price)
        if self._symbol in pos_prices:
            pos_prices[self._symbol] = price

        # Total portfolio value = free USD cash + market value of ALL wallet assets.
        # Uses _asset_balances (populated from Kraken's full balance) so assets not
        # tracked as local positions (e.g. other coins, partial holdings) are included.
        # For each asset, look up its price as BASE/USD in _prices; skip if unknown.
        assets_value = sum(
            amount * self._prices.get(f"{currency}/USD", 0.0)
            for currency, amount in self._asset_balances.items()
        )
        portfolio_usd = self._free_usd + assets_value

        self.sub_title = (
            f"Kraken Pro  ·  {self._symbol}  ·  ${price:,.2f}"
            f"  ·  Portfolio: ${portfolio_usd:,.2f}"
        )

        snapshots: list[PositionSnapshot] = []
        for pos in self._open_positions:
            pos_price = pos_prices.get(pos.symbol, pos.avg_entry_price)
            snap = calculate_snapshot(
                pos,
                pos_price,
                portfolio_usd,
                DEFAULT_STOP_LOSS_PCT,
            )
            snapshots.append(snap)

        summary = calculate_portfolio_summary(snapshots)

        try:
            dashboard = self.get_screen("dashboard")
            # Only update if the dashboard is currently the active screen;
            # calling query_one on an inactive screen raises NoMatches.
            if self.screen is dashboard:
                dashboard.update_positions(snapshots, summary)
        except Exception:
            pass

    def _on_alert_triggered(self, alert, price: float) -> None:
        """Called by AlertManager when a price alert fires."""
        direction = "above" if alert.direction == "above" else "below"
        body = (
            f"{alert.symbol} is {direction} ${alert.target_price:,.2f}  "
            f"(current: ${price:,.2f})"
        )

        # In-terminal toast (always shown)
        self.notify(
            f"🔔 {body}",
            title="Price Alert",
            severity="warning",
            timeout=10,
        )

        # OS-level desktop notification (visible even when terminal is minimised)
        send_notification(
            title="tui-trader — Price Alert",
            body=body,
            urgency="normal",
            timeout_ms=10000,
        )

        try:
            alerts_screen = self.get_screen("alerts")
            alerts_screen.notify_triggered(alert, price)
        except Exception:
            pass

    # ---------------------------------------------------------------------------
    # App-level actions called by screens
    # ---------------------------------------------------------------------------

    def reload_positions(self) -> None:
        """
        Reload open positions from the database and refresh the dashboard.
        Called immediately after a buy or sell is recorded locally, so the
        dashboard updates without waiting for the WebSocket fill notification.
        """
        self._open_positions = db.get_open_positions()
        self._refresh_dashboard(self._current_price)

    def open_add_to_position(self, symbol: Optional[str]) -> None:
        """Open the buy screen pre-filled with the selected position's symbol."""
        screen = self.get_screen("trade_buy")
        if symbol:
            screen.prefill(symbol=symbol)
        self.push_screen("trade_buy")

    def open_close_position(self, symbol: Optional[str]) -> None:
        """Open the sell screen pre-filled with the selected position's symbol."""
        screen = self.get_screen("trade_sell")
        if symbol:
            pos = db.get_position_by_symbol(symbol)
            amount = pos.total_amount if pos else 0.0
            screen.prefill(symbol=symbol, amount=amount)
        self.push_screen("trade_sell")

    def set_stop_loss_for_symbol(self, symbol: str, stop_price: Optional[float]) -> None:
        """
        Persist a manual stop-loss price (or clear it) for the given symbol,
        then reload positions and immediately refresh the dashboard.
        """
        pos = db.get_position_by_symbol(symbol)
        if pos and pos.id is not None:
            db.set_stop_loss(pos.id, stop_price)
            # Reload so _open_positions reflects the new stop
            self._open_positions = db.get_open_positions()
            self._refresh_dashboard(self._current_price)
            if stop_price is not None:
                self.notify(
                    f"Stop-loss set: {symbol} @ ${stop_price:,.2f}",
                    severity="information",
                )
            else:
                self.notify(
                    f"Stop-loss cleared for {symbol} — using default %",
                    severity="information",
                )


if __name__ == "__main__":
    app = TradeApp()
    app.run()

"""
tui-trader — Kraken Pro trading TUI

Entry point. Wires together:
- WebSocket stream workers (ticker, order book, private fills)
- Screen navigation (dashboard, trade, order book, history, alerts)
- Reactive state (live prices, positions, balance)
- Alert evaluation on every ticker event
- Local database sync on fills
- Optional cloud database sync with single-writer locking

Usage:
    python main.py                 # normal start
    python main.py --force-unlock  # clear a stale cloud lock (crash recovery)

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

import argparse
import asyncio
import logging
import sys
from typing import Optional
from uuid import uuid4

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.reactive import reactive

from app.config import DEFAULT_SYMBOL, DEFAULT_STOP_LOSS_PCT
from app import database as db
from app import cloud_sync
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


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tui-trader",
        description="Kraken Pro terminal trading UI",
    )
    parser.add_argument(
        "--force-unlock",
        action="store_true",
        help=(
            "Force-clear a stale cloud lock and start a new session. "
            "Use this when a previous session crashed without releasing the lock."
        ),
    )
    parser.add_argument(
        "--check-sync",
        action="store_true",
        help="Print cloud sync status and exit (useful for diagnosing lock issues).",
    )
    return parser.parse_args()


def _handle_check_sync() -> None:
    """Print cloud sync diagnostics to stdout and exit."""
    import json

    print("=== tui-trader cloud sync diagnostics ===")
    print()

    configured = cloud_sync.is_configured()
    print(f"is_configured : {configured}")
    if not configured:
        from app import config as cfg

        print()
        print("One or more required vars are missing or CLOUD_SYNC_ENABLED=false.")
        print(f"  CLOUD_SYNC_ENABLED     = {cfg.CLOUD_SYNC_ENABLED}")
        print(
            f"  CLOUD_SYNC_ENDPOINT_URL= {cfg.CLOUD_SYNC_ENDPOINT_URL or '(not set)'}"
        )
        print(f"  CLOUD_SYNC_BUCKET      = {cfg.CLOUD_SYNC_BUCKET or '(not set)'}")
        print(
            f"  CLOUD_SYNC_KEY_ID      = {'(set)' if cfg.CLOUD_SYNC_KEY_ID else '(not set)'}"
        )
        print(
            f"  CLOUD_SYNC_KEY_SECRET  = {'(set)' if cfg.CLOUD_SYNC_KEY_SECRET else '(not set)'}"
        )
        print(f"  CLOUD_SYNC_OBJECT_KEY  = {cfg.CLOUD_SYNC_OBJECT_KEY}")
        sys.exit(0)

    from app import config as cfg

    print(f"endpoint      : {cfg.CLOUD_SYNC_ENDPOINT_URL or '(default AWS)'}")
    print(f"bucket        : {cfg.CLOUD_SYNC_BUCKET}")
    print(f"object key    : {cfg.CLOUD_SYNC_OBJECT_KEY}")
    print(f"lock key      : {cfg.CLOUD_SYNC_OBJECT_KEY}.lock")
    print()

    local_session = cloud_sync.load_local_session_id()
    print(f"local session file : {cloud_sync._session_file()}")
    print(f"local session ID   : {local_session or '(none)'}")
    print()

    print("checking cloud lock...")
    try:
        lock = cloud_sync.check_lock()
        if lock is None:
            print("cloud lock     : NOT PRESENT (no lock file found in bucket)")
        else:
            print("cloud lock     : PRESENT")
            print(json.dumps(lock, indent=2))
            if local_session and local_session == lock.get("session_id"):
                print()
                print("→ This machine owns the lock (crash recovery Path A)")
            else:
                print()
                print(
                    "→ Lock is held by a DIFFERENT session — this machine would be READ-ONLY"
                )
    except Exception as e:
        print(f"cloud lock     : ERROR reading lock file — {e}")

    print()
    sys.exit(0)


def _handle_force_unlock() -> None:
    """
    Interactive terminal flow to clear a stale cloud lock.

    Prints lock details, explains the data-loss risk, and requires the user to
    type CONFIRM before proceeding.  Returns normally on confirmation so the
    app can continue to start; calls sys.exit() otherwise.
    """
    if not cloud_sync.is_configured():
        print("Error: cloud sync is not configured — there is no lock to clear.")
        print(f"Check CLOUD_SYNC_ENABLED and related vars in your config file.")
        sys.exit(1)

    lock = cloud_sync.check_lock()
    if lock is None:
        print("No cloud lock found. Nothing to unlock.")
        sys.exit(0)

    print()
    print("⚠  Stale cloud lock detected")
    print(
        f"   Held by:  {lock.get('hostname', 'unknown')} (PID {lock.get('pid', '?')})"
    )
    print(f"   Since:    {lock.get('locked_at', 'unknown')}")
    print()
    print("Forcing unlock will start a new session from the last cloud-synced state.")
    print("Any trades recorded in the crashed session that were NOT synced to the")
    print("cloud before the crash will be absent from the database.")
    print()
    print("Recovery steps (after unlock):")
    print("  1. Run: .venv/bin/python scripts/import_orders.py <ORDER_ID> [...]")
    print("     to re-import any missing trades from Kraken order history.")
    print("  2. Or review your full trade history at:")
    print("     https://www.kraken.com/u/history/trades")
    print()
    try:
        answer = input("Type CONFIRM to proceed, or press Ctrl+C to cancel: ")
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)

    if answer.strip() != "CONFIRM":
        print("Aborted.")
        sys.exit(1)

    cloud_sync.force_clear_lock()
    cloud_sync.clear_local_session_id()
    print("Lock cleared. Starting application...")
    print()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


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
    #read-only-banner {
        display: none;
        dock: top;
        background: $warning-darken-2;
        color: $text;
        content-align: center middle;
        height: 1;
    }
    #read-only-banner.visible {
        display: block;
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
    _free_usd: float = 0.0  # uninvested USD cash on Kraken
    _asset_balances: dict[str, float] = {}  # all non-USD holdings: {"BTC": 0.5, ...}
    _open_positions: list[Position] = []

    def __init__(self) -> None:
        super().__init__()
        self._symbol = DEFAULT_SYMBOL
        self._prices: dict[str, float] = {}  # last known price per symbol
        self._asset_balances = {}
        self._alert_manager = AlertManager(on_trigger=self._on_alert_triggered)
        # Cloud sync state
        self._read_only: bool = False
        self._cloud_session_id: Optional[str] = None
        self._lock_info: Optional[dict] = None

    # ---------------------------------------------------------------------------
    # App lifecycle
    # ---------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="read-only-banner")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialise DB, load state, start WebSocket workers."""

        # --- Cloud sync: download latest DB and acquire (or check) the lock ---
        if cloud_sync.is_configured():
            try:
                await asyncio.to_thread(cloud_sync.sync_down)
                await self._setup_cloud_lock()
            except Exception as e:
                log.error("cloud_sync: startup error — %s", e)
                self.notify(
                    f"Cloud sync error at startup: {e}\n"
                    "Running in local-only mode. Check logs for details.",
                    severity="error",
                    timeout=20,
                )

        # --- Initialise local database (using the downloaded file if synced) ---
        db.init_db()
        self._alert_manager.reload()
        self._open_positions = db.get_open_positions()

        # Show read-only banner after DB is ready (so screens can be queried)
        if self._read_only:
            lock = self._lock_info or {}
            banner = self.query_one("#read-only-banner", Static)
            banner.update(
                f"READ-ONLY  ·  Locked by {lock.get('hostname', 'unknown')} "
                f"since {lock.get('locked_at', 'unknown')}  ·  "
                "Close that session to enable trading here"
            )
            banner.add_class("visible")

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

    async def _setup_cloud_lock(self) -> None:
        """
        Determine whether this session owns the cloud lock or should be read-only.

        - No lock in cloud       → acquire it, proceed normally
        - Lock matches local session ID → crash recovery Path A: resume as owner
        - Lock held by other session → enter read-only mode
        """
        lock = await asyncio.to_thread(cloud_sync.check_lock)

        if lock is None:
            # No lock — acquire it
            session_id = str(uuid4())
            self._cloud_session_id = session_id
            await asyncio.to_thread(cloud_sync.acquire_lock, session_id)
        else:
            local_session_id = cloud_sync.load_local_session_id()
            if local_session_id and local_session_id == lock.get("session_id"):
                # This machine owns the lock (crash recovery Path A)
                log.info(
                    "cloud_sync: resuming ownership of stale lock (session %s)",
                    local_session_id,
                )
                self._cloud_session_id = local_session_id
            else:
                # A different session holds the lock — enter read-only mode
                self._read_only = True
                self._alert_manager.read_only = True
                self._lock_info = lock
                log.info(
                    "cloud_sync: read-only mode — lock held by %s (session %s)",
                    lock.get("hostname"),
                    lock.get("session_id"),
                )

    async def on_unmount(self) -> None:
        """Clean up WebSocket connections and release cloud lock on exit."""
        await stream_manager.close()
        if cloud_sync.is_configured() and self._cloud_session_id:
            await asyncio.to_thread(cloud_sync.sync_up)
            await asyncio.to_thread(cloud_sync.release_lock, self._cloud_session_id)
            cloud_sync.clear_local_session_id()

    # ---------------------------------------------------------------------------
    # Screen registration
    # ---------------------------------------------------------------------------

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

                    snap = calculate_snapshot(
                        pos, self._current_price, 1.0, DEFAULT_STOP_LOSS_PCT
                    )
                    ob_screen.set_position_levels(
                        pos.avg_entry_price, snap.suggested_stop_price
                    )
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

    def set_stop_loss_for_symbol(
        self, symbol: str, stop_price: Optional[float]
    ) -> None:
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
    args = _parse_args()
    if args.check_sync:
        _handle_check_sync()  # always exits
    if args.force_unlock:
        _handle_force_unlock()  # returns on CONFIRM, exits otherwise
    TradeApp().run()

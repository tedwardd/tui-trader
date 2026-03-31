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
import collections
import logging
import sys
import time
from typing import Optional
from uuid import uuid4

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.reactive import reactive

from app.config import DATA_DIR, DEFAULT_SYMBOL, DEFAULT_STOP_LOSS_PCT, PAPER_DATABASE_PATH
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
from app.exchange import canonical_fee
from app.indicators import compute_atr, compute_rsi

from screens.dashboard import DashboardScreen
from screens.trade import TradeScreen
from screens.orderbook import OrderBookScreen
from screens.history import HistoryScreen
from screens.alerts_screen import AlertsScreen
from screens.open_orders import OpenOrdersScreen

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    filename=DATA_DIR / "tui-trader.log",
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
    parser.add_argument(
        "--paper",
        action="store_true",
        help=(
            "Run in paper trading mode — orders are simulated locally and never "
            "submitted to the exchange. Uses a separate database (paper_trades.db)."
        ),
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
    App.read-only Header {
        background: darkred;
    }
    App.paper-mode Header {
        background: darkorange;
        color: #000000;
    }
    """

    BINDINGS = [
        ("1", "switch_screen('dashboard')", "Dashboard"),
        ("2", "push_screen('trade_buy')", "Buy"),
        ("3", "push_screen('orderbook')", "Order Book"),
        ("4", "push_screen('alerts')", "Alerts"),
        ("5", "push_screen('history')", "History"),
        ("6", "push_screen('open_orders')", "Open Orders"),
        ("q", "quit", "Quit"),
    ]

    # ---------------------------------------------------------------------------
    # Reactive state — updated by WebSocket workers, read by screens/widgets
    # ---------------------------------------------------------------------------

    _current_price: reactive[float] = reactive(0.0)
    _free_usd: float = 0.0  # uninvested USD cash on Kraken
    _asset_balances: dict[str, float] = {}  # all non-USD holdings: {"BTC": 0.5, ...}
    _open_positions: list[Position] = []

    def __init__(self, paper_mode: bool = False) -> None:
        super().__init__()
        self.paper_mode: bool = paper_mode
        from datetime import datetime, timezone
        self._started_at: datetime = datetime.now(timezone.utc)
        self._symbol = DEFAULT_SYMBOL
        self._prices: dict[str, float] = {}  # last known price per symbol
        self._asset_balances = {}
        self._alert_manager = AlertManager(on_trigger=self._on_alert_triggered)
        # Cloud sync state
        self._read_only: bool = False
        self._cloud_session_id: Optional[str] = None
        self._lock_info: Optional[dict] = None
        # Indicator state
        self._atr: float | None = None
        self._rsi: float | None = None
        self._vwap: float | None = None
        self._hourly_closes: collections.deque = collections.deque(maxlen=100)
        self._candle_open_ts: int = 0
        self._candle_close: float = 0.0

    # ---------------------------------------------------------------------------
    # App lifecycle
    # ---------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        """Initialise DB, load state, start WebSocket workers."""

        # --- Paper trading mode: use a separate database, skip cloud sync ---
        if self.paper_mode:
            db.configure_engine(PAPER_DATABASE_PATH)
        else:
            # Cloud sync DB download must complete before db.init_db() so the
            # correct database file is in place before we open it.
            if cloud_sync.is_configured():
                try:
                    cloud_sync.sync_down()
                    self._setup_cloud_lock()
                except Exception as e:
                    log.error("cloud_sync: startup error — %s", e)
                    self._cloud_startup_error = str(e)

        db.init_db()
        self._open_positions = db.get_open_positions()

        # Start WebSocket workers immediately — they connect in parallel with
        # the REST startup calls below.
        self.run_worker(
            stream_manager.ticker_worker(self, self._symbol),
            exclusive=False,
            exit_on_error=False,
            name="ticker",
        )
        self.run_worker(
            stream_manager.orderbook_worker(self, self._symbol),
            exclusive=False,
            exit_on_error=False,
            name="orderbook",
        )
        self.run_worker(
            stream_manager.private_worker(self),
            exclusive=False,
            exit_on_error=False,
            name="private",
        )

        self.set_interval(30 * 60, self._refresh_atr)

        # Fetch balance, indicators, and reconcile fills in a background thread
        # so the UI renders immediately rather than waiting on REST round-trips.
        self.run_worker(
            self._startup_rest_worker,
            exclusive=False,
            exit_on_error=False,
            thread=True,
            name="startup_rest",
        )

    def _startup_rest_worker(self) -> None:
        """
        Background thread: fetch balance, ATR/RSI indicators, and reconcile
        offline fills. Runs concurrently with WebSocket connection and the
        initial UI render. All three REST calls run in parallel via threads.
        """
        import concurrent.futures

        def fetch_balance() -> None:
            try:
                balance = kraken_rest.fetch_balance()
                self.call_from_thread(self._apply_balance, balance)
            except Exception as e:
                log.warning("Could not fetch initial balance: %s", e)

        def fetch_indicators() -> None:
            try:
                ohlcv = kraken_rest.fetch_ohlcv(self._symbol, timeframe="1d", limit=20)
                self.call_from_thread(setattr, self, "_atr", compute_atr(ohlcv))
            except Exception as e:
                log.warning("Could not fetch OHLCV for ATR: %s", e)
            try:
                ohlcv_1h = kraken_rest.fetch_ohlcv(self._symbol, timeframe="1h", limit=30)
                closes = [c[4] for c in ohlcv_1h]
                self.call_from_thread(self._apply_hourly_closes, closes)
            except Exception as e:
                log.warning("Could not fetch hourly OHLCV for RSI: %s", e)

        def reconcile() -> None:
            if not self.paper_mode:
                self._reconcile_fills()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(fetch_balance),
                pool.submit(fetch_indicators),
                pool.submit(reconcile),
            ]
            concurrent.futures.wait(futures)

    def _apply_balance(self, balance: dict) -> None:
        self._free_usd = float(balance.get("USD", {}).get("total") or 0)
        self._asset_balances = {
            currency: float(amounts.get("total") or 0)
            for currency, amounts in balance.items()
            if isinstance(amounts, dict)
            and currency != "USD"
            and float(amounts.get("total") or 0) > 0
        }

    def _apply_hourly_closes(self, closes: list) -> None:
        self._hourly_closes = collections.deque(closes, maxlen=100)
        self._rsi = compute_rsi(list(self._hourly_closes))

    def _setup_cloud_lock(self) -> None:
        """
        Determine whether this session owns the cloud lock or should be read-only.
        Called synchronously from on_mount before init_db().

        - No lock in cloud       → acquire it, proceed normally
        - Lock matches local session ID → crash recovery Path A: resume as owner
        - Lock held by other session → enter read-only mode
        """
        lock = cloud_sync.check_lock()

        if lock is None:
            # No lock — acquire it
            session_id = str(uuid4())
            self._cloud_session_id = session_id
            cloud_sync.acquire_lock(session_id)
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

    def _reconcile_fills(self) -> None:
        """
        On startup, fetch recent fills from Kraken and record any that are not
        already in the local DB.

        To avoid pulling in old trades by mistake, fills for a symbol are only
        considered if their timestamp is after the opened_at of the current open
        position for that symbol. If no local position exists for a symbol, the
        fill's own timestamp acts as the cutoff (i.e. it is always considered).
        """
        from app import trade_recorder
        from app.database import trade_exists_by_order_id
        from datetime import datetime, timezone

        try:
            trades = kraken_rest.fetch_my_trades(limit=50)
        except Exception as e:
            log.warning("Startup reconciliation: could not fetch trades — %s", e)
            return

        # Build a cutoff map: symbol → opened_at of current open position.
        # Fills at or before this timestamp belong to a previous position cycle.
        cutoffs: dict[str, datetime] = {}
        for pos in self._open_positions:
            cutoffs[pos.symbol] = pos.opened_at

        recorded = 0
        for trade in trades:
            order_id = str(trade.get("order") or "")
            if trade_exists_by_order_id(order_id):
                continue

            symbol = str(trade.get("symbol") or "")
            side = str(trade.get("side") or "")
            amount = float(trade.get("amount") or 0)
            price = float(trade.get("price") or 0)
            if not symbol or not side or amount == 0 or price == 0:
                continue

            # Apply the timestamp cutoff for this symbol.
            ts_ms = trade.get("timestamp")
            if ts_ms and symbol in cutoffs:
                fill_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                if fill_dt <= cutoffs[symbol]:
                    continue

            fee_info = trade.get("fee") or {}
            raw_fee = float(fee_info.get("cost") or 0)
            order_type = str(trade.get("type") or "limit")
            fee = canonical_fee(raw_fee, amount, price, order_type)
            fee_currency = str(fee_info.get("currency") or "USD")

            if side == "buy":
                trade_recorder.record_buy(
                    symbol, amount, price, fee, fee_currency, order_id, order_type, self._atr
                )
            else:
                trade_recorder.record_sell(
                    symbol, amount, price, fee, fee_currency, order_id, order_type
                )
            recorded += 1
            log.info("Reconciled offline fill: %s %s %s @ %s", side, amount, symbol, price)

        if recorded:
            self._open_positions = db.get_open_positions()
            log.info("Startup reconciliation: recorded %d missing fill(s)", recorded)

    async def _refresh_atr(self) -> None:
        """Refresh ATR every 30 minutes from daily OHLCV data."""
        import asyncio
        try:
            ohlcv = await asyncio.to_thread(kraken_rest.fetch_ohlcv, self._symbol, "1d", 20)
            self._atr = compute_atr(ohlcv)
        except Exception as e:
            log.warning("ATR refresh failed: %s", e)

    async def on_unmount(self) -> None:
        """Clean up WebSocket connections and release cloud lock on exit."""
        await stream_manager.close()
        if not self.paper_mode and cloud_sync.is_configured() and self._cloud_session_id:
            cloud_sync.sync_up()
            cloud_sync.release_lock(self._cloud_session_id)
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
        self.install_screen(OpenOrdersScreen(), name="open_orders")
        self.push_screen("dashboard")

        if self.paper_mode:
            self.add_class("paper-mode")
            self.sub_title = f"PAPER TRADING  ·  {DEFAULT_SYMBOL}  ·  No real orders will be placed"
            self.call_after_refresh(
                self.notify,
                "Paper trading mode — orders are simulated and never sent to Kraken. "
                f"Data stored in paper_trades.db.",
                severity="warning",
                timeout=15,
            )

        if self._read_only:
            lock = self._lock_info or {}
            self.add_class("read-only")
            # Stamp the header subtitle as a permanent indicator — visible on
            # every screen without relying on widget layers or CSS display.
            self.sub_title = (
                f"READ-ONLY  ·  Locked by {lock.get('hostname', 'unknown')} "
                f"since {lock.get('locked_at', 'unknown')}"
            )
            # Toast fires after the first frame via call_after_refresh so
            # there is an active screen to render it onto.
            self.call_after_refresh(
                self.notify,
                f"Read-only session — {lock.get('hostname', 'unknown')} has this "
                "session open. All write operations are disabled.",
                severity="warning",
                timeout=30,
            )
        elif hasattr(self, "_cloud_startup_error"):
            self.call_after_refresh(
                self.notify,
                f"Cloud sync error: {self._cloud_startup_error} — running local-only.",
                severity="error",
                timeout=20,
            )

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

        hour_ts = int(time.time()) // 3600
        if self._candle_open_ts == 0:
            self._candle_open_ts = hour_ts
        if hour_ts > self._candle_open_ts:
            self._hourly_closes.append(self._candle_close)
            self._rsi = compute_rsi(list(self._hourly_closes))
            self._candle_open_ts = hour_ts
        self._candle_close = price

        raw_vwap = ticker.get("vwap")
        self._vwap = float(raw_vwap) if raw_vwap else None

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
        self._open_positions = db.get_open_positions()
        self._refresh_dashboard(self._current_price)
        try:
            open_orders_screen = self.get_screen("open_orders")
            if self.screen is open_orders_screen:
                open_orders_screen.update_orders(orders)
        except Exception:
            pass

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
        Records any fills not already in the local DB (i.e. limit order fills),
        then reloads positions and notifies the history screen.
        """
        from app import trade_recorder
        from app.database import trade_exists_by_order_id

        recorded = False
        for trade in trades:
            order_id = str(trade.get("order") or "")
            if trade_exists_by_order_id(order_id):
                continue  # already recorded at placement (market order)

            # Skip historical fills replayed by Kraken on WebSocket connect
            from datetime import datetime, timezone
            ts_ms = trade.get("timestamp")
            if ts_ms is not None:
                trade_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                if trade_dt < self._started_at:
                    continue

            symbol = str(trade.get("symbol") or "")
            side = str(trade.get("side") or "")
            amount = float(trade.get("amount") or 0)
            price = float(trade.get("price") or 0)
            if not symbol or not side or amount == 0 or price == 0:
                continue

            fee_info = trade.get("fee") or {}
            raw_fee = float(fee_info.get("cost") or 0)
            order_type = str(trade.get("type") or "limit")
            fee = canonical_fee(raw_fee, amount, price, order_type)
            fee_currency = str(fee_info.get("currency") or "USD")

            if side == "buy":
                trade_recorder.record_buy(
                    symbol, amount, price, fee, fee_currency, order_id, order_type, self._atr
                )
            else:
                trade_recorder.record_sell(
                    symbol, amount, price, fee, fee_currency, order_id, order_type
                )
            recorded = True

        if recorded:
            self.reload_positions()
            self.notify(
                f"Limit order filled — position updated",
                severity="information",
                timeout=6,
            )

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
                dashboard.update_indicators(self._vwap, self._rsi, self._atr, self._current_price)
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
        # Use call_after_refresh so the screen transition (trade → dashboard)
        # completes before we check self.screen in _refresh_dashboard.
        # Without this, the "if self.screen is dashboard" guard in
        # _refresh_dashboard fires while the trade screen is still active,
        # and the dashboard silently skips the update until the next ticker tick.
        self.call_after_refresh(lambda: self._refresh_dashboard(self._current_price))

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
    TradeApp(paper_mode=args.paper).run()

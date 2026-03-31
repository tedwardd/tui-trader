"""
Trade screen — buy and sell order entry.

Wraps the OrderForm widget in a full screen with:
- Live price display for the entered symbol (from WebSocket)
- Estimated cost calculation
- Order confirmation before submission
- Success/error feedback after order placement
"""

from typing import Optional
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, Static
from textual.containers import Vertical

from widgets.order_form import OrderForm
import app.exchange as exchange
from app.exchange import canonical_fee
from app import cloud_sync
from app import trade_recorder


class TradeScreen(Screen):
    """
    Full-screen order entry form.

    Instantiated with side="buy" or side="sell".
    The app registers two instances: "trade_buy" and "trade_sell".
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
    ]

    DEFAULT_CSS = """
    TradeScreen {
        layout: vertical;
        align: center middle;
    }
    TradeScreen .trade-container {
        width: 60;
        height: auto;
    }
    TradeScreen .price-display {
        height: 3;
        border: solid $primary;
        padding: 0 1;
        content-align: center middle;
        margin-bottom: 1;
    }
    TradeScreen .status-msg {
        height: 2;
        padding: 0 1;
        content-align: center middle;
    }
    .status-success { color: $success; }
    .status-error   { color: $error; }
    """

    def __init__(self, side: str = "buy", **kwargs) -> None:
        super().__init__(**kwargs)
        self._side = side
        self._current_price: Optional[float] = None
        self._prefill_symbol: Optional[str] = None
        self._prefill_amount: Optional[float] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="trade-container"):
            yield Static("Price: —", id="price-display", classes="price-display")
            yield OrderForm(
                on_submit=self._handle_order,
                side=self._side,
                id="order-form",
            )
            yield Static("", id="status-msg", classes="status-msg")
        yield Footer()

    def on_screen_resume(self) -> None:
        """Re-apply prefill every time the screen becomes active (used for close position flow)."""
        if self._prefill_symbol or self._prefill_amount:
            form = self.query_one(OrderForm)
            form.prefill(
                symbol=self._prefill_symbol or "",
                amount=self._prefill_amount or 0.0,
                side=self._side,
            )

    def prefill(self, symbol: str, amount: float = 0.0) -> None:
        """Pre-fill the form. Called by the app for the 'close position' flow."""
        self._prefill_symbol = symbol
        self._prefill_amount = amount

    def on_order_form_cancelled(self, event: OrderForm.Cancelled) -> None:
        """Clear the form and close the screen when the user presses Cancel."""
        self.query_one(OrderForm).clear()
        self.app.pop_screen()

    def update_price(self, symbol: str, price: float) -> None:
        """
        Called by the app when a ticker update arrives for the current symbol.
        Updates the price display and passes the live price to the form for
        USD↔QTY conversion and estimate display.
        """
        self._current_price = price
        try:
            self.query_one("#price-display", Static).update(
                f"{symbol}  Last: ${price:,.2f}"
            )
            self.query_one(OrderForm).set_live_price(symbol, price)
        except Exception:
            pass

    def _handle_order(
        self,
        side: str,
        symbol: str,
        amount: float,
        price: Optional[float],
        order_type: str,
    ) -> None:
        """
        Submit the order to Kraken via REST and update local position tracking.
        Runs as a thread worker so the blocking REST call doesn't freeze the UI.
        """
        if getattr(self.app, "_read_only", False):
            self.app.notify(
                "Read-only session — close the other session to trade",
                severity="warning",
            )
            return
        status = self.query_one("#status-msg", Static)
        status.remove_class("status-success", "status-error")
        status.update("Placing order...")

        self.run_worker(
            lambda: self._submit_order(side, symbol, amount, price, order_type),
            thread=True,
            exclusive=True,
            name="order-submit",
        )

    def _submit_order(
        self,
        side: str,
        symbol: str,
        amount: float,
        price: Optional[float],
        order_type: str,
    ) -> None:
        """Blocking REST call — runs in a thread worker."""
        try:
            # Place order via REST (or simulate it in paper trading mode)
            if getattr(self.app, "paper_mode", False):
                from app import paper_exchange
                live = self._current_price or 0.0
                if side == "buy":
                    if order_type == "market":
                        order = paper_exchange.place_market_buy(symbol, amount, live)
                    else:
                        order = paper_exchange.place_limit_buy(symbol, amount, float(price))  # type: ignore[arg-type]
                else:
                    if order_type == "market":
                        order = paper_exchange.place_market_sell(symbol, amount, live)
                    else:
                        order = paper_exchange.place_limit_sell(symbol, amount, float(price))  # type: ignore[arg-type]
            elif side == "buy":
                if order_type == "market":
                    order = exchange.place_market_buy(symbol, amount)
                else:
                    order = exchange.place_limit_buy(symbol, amount, float(price))  # type: ignore[arg-type]
            else:
                if order_type == "market":
                    order = exchange.place_market_sell(symbol, amount)
                else:
                    order = exchange.place_limit_sell(symbol, amount, float(price))  # type: ignore[arg-type]

            # Extract fill details — prefer 'average' (actual fill price for
            # market orders), fall back to 'price' (limit price), then the
            # user-entered price, then the current live price.
            fill_price = float(
                order.get("average")
                or order.get("price")
                or price
                or self._current_price
                or 0
            )
            fee_info = order.get("fee") or {}
            raw_fee = float(fee_info.get("cost") or 0)
            fee = canonical_fee(raw_fee, filled_amount, fill_price, order_type)
            fee_currency = str(fee_info.get("currency") or "USD")
            order_id = str(order.get("id", ""))

            # For limit orders that haven't filled yet, skip local recording —
            # the fill will arrive via watch_my_trades and be recorded then.
            filled_amount = float(order.get("filled") or 0)
            if order_type == "limit" and filled_amount == 0:
                self.app.call_from_thread(
                    self.app.notify,
                    f"Limit order placed — will appear in positions when filled",
                    severity="information",
                    timeout=8,
                )
                self.app.call_from_thread(self.app.pop_screen)
                return

            # Market orders (and immediately-filled limits): record now
            if filled_amount == 0:
                filled_amount = float(order.get("amount") or amount)

            atr = getattr(self.app, "_atr", None)
            if side == "buy":
                trade_recorder.record_buy(
                    symbol, filled_amount, fill_price, fee, fee_currency, order_id, order_type, atr
                )
            else:
                trade_recorder.record_sell(
                    symbol, filled_amount, fill_price, fee, fee_currency, order_id, order_type
                )

            self.app.call_from_thread(
                self._on_order_success, side, filled_amount, symbol, fill_price
            )
            # Immediately reload positions in the app so the dashboard reflects
            # the change without waiting for the WebSocket fill notification.
            self.app.call_from_thread(self.app.reload_positions)
            # Push the updated DB to the cloud so a crash between here and
            # clean shutdown doesn't lose this trade from the cloud copy.
            # (Skipped in paper trading mode — paper DB is never synced to cloud.)
            if (
                not getattr(self.app, "paper_mode", False)
                and cloud_sync.is_configured()
                and getattr(self.app, "_cloud_session_id", None)
            ):
                cloud_sync.sync_up()

        except Exception as e:
            self.app.call_from_thread(self._on_order_error, str(e))

    def _on_order_success(
        self, side: str, amount: float, symbol: str, price: float
    ) -> None:
        self.query_one(OrderForm).clear()
        self.app.notify(
            f"✓ {side.capitalize()} {amount} {symbol.split('/')[0]} @ ${price:,.2f}",
            severity="information",
            timeout=6,
        )
        self.app.pop_screen()

    def _on_order_error(self, error: str) -> None:
        status = self.query_one("#status-msg", Static)
        status.add_class("status-error")
        status.update(f"✗ Order failed: {error}")


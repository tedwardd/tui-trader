"""
Buy/sell order entry form widget.

Supports:
- Market and limit orders
- Toggling between asset quantity (QTY) and USD dollar amount input
- Pre-filling symbol and amount when adding to an existing position
- Inline validation before submission
- Displays estimated cost/proceeds before confirming
"""

from typing import Callable, Optional
from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Static, Input, Button, Select, Label
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive

from app.config import DEFAULT_SYMBOL


# Callback type: called when the user confirms an order
# Args: side, symbol, amount (always in base asset qty), price (None = market), order_type
OrderCallback = Callable[[str, str, float, Optional[float], str], None]


class OrderForm(Static):
    """
    A form for entering buy or sell orders.

    The amount field toggles between:
      QTY mode -- user enters base asset quantity directly (e.g. 0.001 BTC)
      USD mode -- user enters a dollar amount; converted to qty at submission
                  using the live price supplied via set_live_price()

    Messages:
        OrderForm.Cancelled -- posted when the user presses Cancel.
                               The parent screen should listen and pop itself.

    Usage:
        form = OrderForm(on_submit=handle_order, side="buy")
        form.set_live_price("BTC/USD", 75000.0)
        form.prefill(symbol="BTC/USD", amount=0.001)
    """

    class Cancelled(Message):
        """Posted when the user cancels the order form."""

    DEFAULT_CSS = """
    OrderForm {
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }
    OrderForm .form-title {
        text-style: bold;
        margin-bottom: 1;
    }
    OrderForm .form-row {
        height: 3;
        margin-bottom: 1;
    }
    OrderForm .form-label {
        width: 12;
        content-align: right middle;
        padding-right: 1;
    }
    OrderForm .form-input {
        width: 1fr;
    }
    OrderForm .toggle-btn {
        width: 7;
        margin-left: 1;
        min-width: 7;
    }
    OrderForm .form-actions {
        height: 3;
        margin-top: 1;
    }
    OrderForm .btn-buy  { background: $success; }
    OrderForm .btn-sell { background: $error; }
    OrderForm .error-msg { color: $error; height: 1; }
    OrderForm .info-msg  { color: $text-muted; height: 1; }
    """

    _side: reactive[str] = reactive("buy")
    _usd_mode: bool = False          # False = QTY mode, True = USD mode
    _live_price: Optional[float] = None
    _live_symbol: Optional[str] = None

    def __init__(
        self,
        on_submit: Optional[OrderCallback] = None,
        side: str = "buy",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_submit = on_submit
        self._side = side
        self._usd_mode = side == "buy"  # buys default to USD, sells to QTY
        self._live_price = None
        self._live_symbol = None

    def compose(self) -> ComposeResult:
        side_label = "Buy" if self._side == "buy" else "Sell"
        yield Static(f"{side_label} Order", classes="form-title")

        with Horizontal(classes="form-row"):
            yield Label("Symbol", classes="form-label")
            yield Input(placeholder=DEFAULT_SYMBOL, value=DEFAULT_SYMBOL, id="symbol-input", classes="form-input")

        with Horizontal(classes="form-row"):
            yield Label("Order Type", classes="form-label")
            yield Select(
                [("Market", "market"), ("Limit", "limit")],
                value="market",
                id="order-type-select",
                classes="form-input",
            )

        with Horizontal(classes="form-row"):
            yield Label("Amount", classes="form-label")
            if self._usd_mode:
                yield Input(placeholder="Dollar amount (e.g. 75.00)", id="amount-input", classes="form-input")
                yield Button("USD", id="toggle-mode-btn", classes="toggle-btn", variant="warning")
            else:
                yield Input(placeholder="Qty (e.g. 0.001)", id="amount-input", classes="form-input")
                yield Button("QTY", id="toggle-mode-btn", classes="toggle-btn", variant="primary")

        with Horizontal(classes="form-row", id="price-row"):
            yield Label("Limit Price", classes="form-label")
            yield Input(
                placeholder="Leave blank for market",
                id="price-input",
                classes="form-input",
                disabled=True,
            )

        yield Static("", id="error-msg", classes="error-msg")
        yield Static("", id="info-msg", classes="info-msg")

        btn_class = "btn-buy" if self._side == "buy" else "btn-sell"
        with Horizontal(classes="form-actions"):
            yield Button(
                f"Place {side_label} Order",
                id="submit-btn",
                classes=btn_class,
            )
            yield Button("Cancel", id="cancel-btn", variant="default")

    # -----------------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "order-type-select":
            price_input = self.query_one("#price-input", Input)
            price_input.disabled = event.value == "market"
            if event.value == "market":
                price_input.value = ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "toggle-mode-btn":
            self._toggle_mode()
            return
        if event.button.id == "cancel-btn":
            self.clear()
            self.post_message(OrderForm.Cancelled())
            return
        if event.button.id == "submit-btn":
            self._submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Update the estimated value display as the user types."""
        if event.input.id == "amount-input":
            self._update_estimate()

    # -----------------------------------------------------------------------
    # Mode toggle
    # -----------------------------------------------------------------------

    def _toggle_mode(self) -> None:
        """Switch between QTY and USD input modes, preserving the entered value."""
        amount_input = self.query_one("#amount-input", Input)
        toggle_btn = self.query_one("#toggle-mode-btn", Button)
        raw = amount_input.value.strip()

        if not self._usd_mode:
            # Switching QTY → USD
            self._usd_mode = True
            toggle_btn.label = "USD"
            toggle_btn.variant = "warning"
            amount_input.placeholder = "Dollar amount (e.g. 75.00)"
            # Convert existing qty value to USD if possible
            if raw and self._live_price:
                try:
                    qty = float(raw)
                    amount_input.value = f"{qty * self._live_price:.2f}"
                except ValueError:
                    amount_input.value = ""
            else:
                amount_input.value = ""
        else:
            # Switching USD → QTY
            self._usd_mode = False
            toggle_btn.label = "QTY"
            toggle_btn.variant = "primary"
            amount_input.placeholder = "Qty (e.g. 0.001)"
            # Convert existing USD value back to qty if possible
            if raw and self._live_price:
                try:
                    usd = float(raw)
                    amount_input.value = f"{usd / self._live_price:.8f}"
                except ValueError:
                    amount_input.value = ""
            else:
                amount_input.value = ""

        self._update_estimate()

    # -----------------------------------------------------------------------
    # Live price / estimate
    # -----------------------------------------------------------------------

    def set_live_price(self, symbol: str, price: float) -> None:
        """Called by the trade screen on every ticker update."""
        self._live_price = price
        self._live_symbol = symbol
        self._update_estimate()

    def _update_estimate(self) -> None:
        """Show estimated cost/proceeds below the amount field."""
        if not self._live_price:
            return
        info = self.query_one("#info-msg", Static)
        raw = self.query_one("#amount-input", Input).value.strip()
        if not raw:
            info.update("")
            return
        try:
            entered = float(raw)
        except ValueError:
            info.update("")
            return

        base = self._live_symbol.split("/")[0] if self._live_symbol else "asset"

        if self._usd_mode:
            qty = entered / self._live_price
            label = "cost" if self._side == "buy" else "proceeds"
            info.update(f"≈ {qty:.8f} {base}  ·  ${entered:,.2f} {label} at ${self._live_price:,.2f}")
        else:
            usd = entered * self._live_price
            label = "cost" if self._side == "buy" else "proceeds"
            info.update(f"≈ ${usd:,.2f} {label} at ${self._live_price:,.2f}")

    # -----------------------------------------------------------------------
    # Submission
    # -----------------------------------------------------------------------

    def _submit(self) -> None:
        """Validate inputs, convert USD→qty if needed, and call the submit callback."""
        error = self.query_one("#error-msg", Static)
        error.update("")

        symbol = self.query_one("#symbol-input", Input).value.strip().upper()
        amount_str = self.query_one("#amount-input", Input).value.strip()
        order_type = self.query_one("#order-type-select", Select).value
        price_str = self.query_one("#price-input", Input).value.strip()

        if not symbol:
            error.update("Symbol is required")
            return
        if "/" not in symbol:
            error.update("Symbol must be in format BASE/QUOTE (e.g. BTC/USD)")
            return

        try:
            entered = float(amount_str)
            if entered <= 0:
                raise ValueError
        except ValueError:
            error.update("Amount must be a positive number")
            return

        # Convert USD → qty if in USD mode
        if self._usd_mode:
            if not self._live_price:
                error.update("No live price available — cannot convert USD to quantity")
                return
            amount = entered / self._live_price
        else:
            amount = entered

        price: Optional[float] = None
        if order_type == "limit":
            try:
                price = float(price_str)
                if price <= 0:
                    raise ValueError
            except ValueError:
                error.update("Limit price must be a positive number")
                return

        if self._on_submit:
            self._on_submit(self._side, symbol, amount, price, order_type)

    # -----------------------------------------------------------------------
    # Public helpers
    # -----------------------------------------------------------------------

    def prefill(
        self,
        symbol: str = "",
        amount: float = 0.0,
        side: str = "buy",
    ) -> None:
        """
        Pre-fill the form. Amount is always in base asset quantity.
        Resets to QTY mode so the prefilled quantity is shown directly.
        """
        self._side = side
        # Always reset to QTY mode for prefill (e.g. close position)
        if self._usd_mode:
            self._toggle_mode()
        self.query_one("#symbol-input", Input).value = symbol or DEFAULT_SYMBOL
        if amount > 0:
            self.query_one("#amount-input", Input).value = f"{amount:.8f}"

    def clear(self) -> None:
        """Reset all form fields and return to the default mode for this side."""
        default_usd = self._side == "buy"
        if self._usd_mode != default_usd:
            self._toggle_mode()
        self.query_one("#symbol-input", Input).value = DEFAULT_SYMBOL
        self.query_one("#amount-input", Input).value = ""
        self.query_one("#price-input", Input).value = ""
        self.query_one("#error-msg", Static).update("")
        self.query_one("#info-msg", Static).update("")

    def set_info(self, message: str) -> None:
        self.query_one("#info-msg", Static).update(message)

    def set_error(self, message: str) -> None:
        self.query_one("#error-msg", Static).update(message)

"""
Open Orders screen — pending limit orders on Kraken.

Shows all open (unfilled) orders fetched from the exchange.
Updates live via the watch_orders WebSocket feed.
Allows cancelling a selected order with 'x'.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, DataTable

import app.exchange as exchange


COLUMNS = [
    ("order_id", "Order ID"),
    ("side", "Side"),
    ("type", "Type"),
    ("amount", "Amount"),
    ("price", "Price"),
    ("filled", "Filled"),
    ("symbol", "Symbol"),
    ("datetime", "Placed"),
]


class OpenOrdersScreen(Screen):
    """Displays open (pending) orders with the ability to cancel them."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        Binding("x", "cancel_order", "Cancel Order", priority=True),
        ("r", "refresh", "Refresh"),
    ]

    DEFAULT_CSS = """
    OpenOrdersScreen {
        layout: vertical;
    }
    OpenOrdersScreen .summary-bar {
        height: 3;
        border: solid $primary;
        padding: 0 1;
        content-align: center middle;
    }
    OpenOrdersScreen DataTable {
        height: 1fr;
    }
    .side-buy  { color: $success; }
    .side-sell { color: $error; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="summary-bar", classes="summary-bar")
        table = DataTable(id="orders-table", cursor_type="row")
        for col_key, col_label in COLUMNS:
            table.add_column(col_label, key=col_key)
        yield table
        yield Footer()

    def on_mount(self) -> None:
        self._load_orders()

    def on_screen_resume(self) -> None:
        self._load_orders()

    def _load_orders(self) -> None:
        self.run_worker(self._fetch_and_populate, thread=True, exclusive=True, name="fetch-open-orders")

    def _fetch_and_populate(self) -> None:
        try:
            orders = exchange.fetch_open_orders()
        except Exception as e:
            self.app.call_from_thread(
                self.app.notify, f"Failed to fetch open orders: {e}", severity="error"
            )
            return
        self.app.call_from_thread(self._populate, orders)

    def _populate(self, orders: list[dict]) -> None:
        table = self.query_one("#orders-table", DataTable)
        table.clear()

        limit_orders = [o for o in orders if o.get("type") == "limit"]

        self.query_one("#summary-bar", Static).update(
            f"Open Limit Orders: {len(limit_orders)}  ·  Press [bold]x[/bold] to cancel selected"
        )

        for order in limit_orders:
            side = str(order.get("side", "")).lower()
            side_color = "side-buy" if side == "buy" else "side-sell"
            filled = float(order.get("filled") or 0)
            amount = float(order.get("amount") or 0)
            price = float(order.get("price") or 0)
            dt = str(order.get("datetime") or "")[:16]
            order_id = str(order.get("id", ""))

            table.add_row(
                order_id,
                f"[{side_color}]{side.upper()}[/{side_color}]",
                str(order.get("type", "")),
                f"{amount:.6f}",
                f"${price:,.2f}",
                f"{filled:.6f}",
                str(order.get("symbol", "")),
                dt,
                key=order_id,
            )

    def update_orders(self, orders: list[dict]) -> None:
        """Called by the app when watch_orders fires. Repopulates the table."""
        self._populate(orders)

    def action_cancel_order(self) -> None:
        table = self.query_one("#orders-table", DataTable)
        if table.row_count == 0:
            return
        try:
            rows = list(table.ordered_rows)
            order_id = rows[table.cursor_row].key.value
        except Exception:
            return

        # Resolve symbol from the row before removing it
        try:
            symbol = str(table.get_cell(order_id, "symbol"))
        except Exception:
            self.app.notify("Could not determine symbol for order", severity="error")
            return

        self.run_worker(
            lambda: self._cancel(order_id, symbol),
            thread=True,
            exclusive=True,
            name="cancel-order",
        )

    def _cancel(self, order_id: str, symbol: str) -> None:
        try:
            exchange.cancel_order(order_id, symbol)
            self.app.call_from_thread(
                self.app.notify,
                f"Order {order_id} cancelled",
                severity="information",
            )
            self.app.call_from_thread(self._load_orders)
        except Exception as e:
            self.app.call_from_thread(
                self.app.notify, f"Cancel failed: {e}", severity="error"
            )

    def action_refresh(self) -> None:
        self._load_orders()

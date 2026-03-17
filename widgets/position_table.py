"""
Live positions DataTable widget.

Displays all open positions with real-time P&L that updates on every
WebSocket ticker event. Supports selecting a position to add to or close it.
"""

from textual.app import ComposeResult
from textual.widgets import DataTable, Static
from textual.reactive import reactive

from app.pnl import PositionSnapshot, format_pnl, format_pnl_pct, pnl_color_class


COLUMNS = [
    ("symbol", "Symbol"),
    ("size", "Size"),
    ("entry", "Avg Entry"),
    ("price", "Price"),
    ("value", "Value"),
    ("pnl", "Unrealized P&L"),
    ("pnl_pct", "P&L %"),
]


class PositionTable(Static):
    """
    A DataTable showing all open positions with live P&L.

    The parent app calls update_snapshots() whenever prices change.
    """

    DEFAULT_CSS = """
    PositionTable {
        height: auto;
        border: solid $primary;
        padding: 0 1;
    }
    PositionTable > DataTable {
        height: auto;
        max-height: 12;
    }
    .pnl-positive { color: $success; }
    .pnl-negative { color: $error; }
    .pnl-neutral  { color: $text-muted; }
    """

    # Track which symbols currently have rows — avoids relying on RowKey str()
    _row_symbols: set[str]

    def on_mount(self) -> None:
        self._row_symbols = set()

    def compose(self) -> ComposeResult:
        table = DataTable(id="positions-table", cursor_type="row")
        for col_key, col_label in COLUMNS:
            table.add_column(col_label, key=col_key)
        yield table

    def update_snapshots(self, snapshots: list[PositionSnapshot]) -> None:
        """
        Refresh the table with the latest position snapshots.
        Called by the app on every ticker update.
        """
        table = self.query_one(DataTable)
        incoming_keys = {s.symbol for s in snapshots}

        # Remove rows for positions that are no longer open
        for symbol in list(self._row_symbols - incoming_keys):
            table.remove_row(symbol)
            self._row_symbols.discard(symbol)

        for snap in snapshots:
            pnl_str = format_pnl(snap.unrealized_pnl)
            pnl_pct_str = format_pnl_pct(snap.unrealized_pnl_pct)
            row = (
                snap.symbol,
                f"{snap.total_amount:.6f}",
                f"${snap.avg_entry_price:,.2f}",
                f"${snap.current_price:,.2f}",
                f"${snap.current_value:,.2f}",
                pnl_str,
                pnl_pct_str,
            )

            if snap.symbol in self._row_symbols:
                # Row exists — update each cell in place
                col_keys = [c[0] for c in COLUMNS]
                for col_key, value in zip(col_keys, row):
                    table.update_cell(snap.symbol, col_key, value)
            else:
                # New position — add a row keyed by symbol string
                table.add_row(*row, key=snap.symbol)
                self._row_symbols.add(snap.symbol)

    def get_selected_symbol(self) -> str | None:
        """Return the symbol of the currently highlighted row, if any."""
        table = self.query_one(DataTable)
        if table.cursor_row < 0 or table.row_count == 0:
            return None
        try:
            rows = list(table.ordered_rows)
            return rows[table.cursor_row].key.value
        except Exception:
            return None

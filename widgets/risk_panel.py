"""
Risk management panel widget.

Shows per-position risk metrics:
- % of portfolio at risk (cost basis / total portfolio value)
- Suggested stop-loss price at the configured percentage
- Distance from current price to stop

Updates reactively on every ticker event alongside the position table.
"""

from textual.app import ComposeResult
from textual.widgets import Static, DataTable

from app.pnl import PositionSnapshot
from app.config import DEFAULT_STOP_LOSS_PCT


RISK_COLUMNS = [
    ("symbol", "Symbol"),
    ("risk_pct", "% at Risk"),
    ("stop_price", "Stop Price"),
    ("stop_pct", "Stop %"),
    ("dist_to_stop", "Dist to Stop"),
]


class RiskPanel(Static):
    """
    Displays risk metrics for each open position.
    """

    DEFAULT_CSS = """
    RiskPanel {
        height: auto;
        border: solid $warning;
        padding: 0 1;
    }
    RiskPanel > Static.panel-title {
        color: $warning;
        text-style: bold;
        padding: 0 0 1 0;
    }
    RiskPanel > DataTable {
        height: auto;
        max-height: 8;
    }
    .risk-high   { color: $error; }
    .risk-medium { color: $warning; }
    .risk-low    { color: $success; }
    """

    _row_symbols: set[str] = set()

    def on_mount(self) -> None:
        pass

    def compose(self) -> ComposeResult:
        yield Static("⚠ Risk Management", classes="panel-title")
        table = DataTable(id="risk-table", cursor_type="none", show_cursor=False)
        for col_key, col_label in RISK_COLUMNS:
            table.add_column(col_label, key=col_key)
        yield table

    def update_snapshots(self, snapshots: list[PositionSnapshot]) -> None:
        """Refresh risk metrics from the latest position snapshots."""
        table = self.query_one(DataTable)
        incoming_keys = {s.symbol for s in snapshots}

        for symbol in list(self._row_symbols - incoming_keys):
            table.remove_row(symbol)
            self._row_symbols.discard(symbol)

        for snap in snapshots:
            dist_to_stop = snap.current_price - snap.suggested_stop_price
            dist_pct = (dist_to_stop / snap.current_price) * 100 if snap.current_price > 0 else 0

            # Colour-code risk level
            if snap.risk_pct > 20:
                risk_class = "risk-high"
            elif snap.risk_pct > 10:
                risk_class = "risk-medium"
            else:
                risk_class = "risk-low"

            if snap.stop_source == "atr":
                stop_label = f"${snap.suggested_stop_price:,.2f} [A]"
            elif snap.stop_source == "manual":
                stop_label = f"${snap.suggested_stop_price:,.2f} [M]"
            else:
                stop_label = f"${snap.suggested_stop_price:,.2f}"

            row = (
                snap.symbol,
                f"[{risk_class}]{snap.risk_pct:.1f}%[/{risk_class}]",
                stop_label,
                f"{snap.stop_loss_pct:.1f}%",
                f"${dist_to_stop:,.2f} ({dist_pct:.1f}%)",
            )

            if snap.symbol in self._row_symbols:
                for col_key, value in zip([c[0] for c in RISK_COLUMNS], row):
                    table.update_cell(snap.symbol, col_key, value)
            else:
                table.add_row(*row, key=snap.symbol)
                self._row_symbols.add(snap.symbol)

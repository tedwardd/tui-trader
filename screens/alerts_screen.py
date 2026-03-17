"""
Price alerts management screen.

Allows the user to:
- View all active and triggered alerts
- Create new price alerts (above/below a target price)
- Delete alerts
- See which alerts have been triggered and when
"""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import (
    Header,
    Footer,
    Static,
    DataTable,
    Input,
    Button,
    Select,
    Label,
)
from textual.containers import Vertical, Horizontal

from app.models import PriceAlert
from app.alerts import AlertManager
from app import database as db


class AlertsScreen(Screen):
    """
    Full-screen price alert management.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("d", "delete_selected", "Delete Alert"),
    ]

    DEFAULT_CSS = """
    AlertsScreen {
        layout: vertical;
    }
    AlertsScreen .alerts-table-container {
        height: 1fr;
        border: solid $primary;
        padding: 0 1;
    }
    AlertsScreen .add-alert-form {
        height: auto;
        border: solid $accent;
        padding: 1 2;
        margin-top: 1;
    }
    AlertsScreen .form-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    AlertsScreen .form-row {
        height: 3;
        margin-bottom: 1;
    }
    AlertsScreen .form-label {
        width: 12;
        content-align: right middle;
        padding-right: 1;
    }
    AlertsScreen .form-input {
        width: 1fr;
    }
    AlertsScreen .form-actions {
        height: 3;
    }
    AlertsScreen .error-msg { color: $error; height: 1; }
    .triggered { color: $text-muted; }
    .alert-above { color: $success; }
    .alert-below { color: $error; }
    """

    COLUMNS = [
        ("symbol", "Symbol"),
        ("direction", "Direction"),
        ("target", "Target Price"),
        ("status", "Status"),
        ("created", "Created"),
        ("triggered_at", "Triggered At"),
        ("note", "Note"),
    ]

    def __init__(self, alert_manager: AlertManager, **kwargs) -> None:
        super().__init__(**kwargs)
        self._alert_manager = alert_manager

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Vertical(classes="alerts-table-container"):
                table = DataTable(id="alerts-table", cursor_type="row")
                for col_key, col_label in self.COLUMNS:
                    table.add_column(col_label, key=col_key)
                yield table

            with Vertical(classes="add-alert-form"):
                yield Static("+ New Price Alert", classes="form-title")

                with Horizontal(classes="form-row"):
                    yield Label("Symbol", classes="form-label")
                    yield Input(
                        placeholder="BTC/USD", id="alert-symbol", classes="form-input"
                    )

                with Horizontal(classes="form-row"):
                    yield Label("Direction", classes="form-label")
                    yield Select(
                        [("Price goes above", "above"), ("Price goes below", "below")],
                        value="above",
                        id="alert-direction",
                        classes="form-input",
                    )

                with Horizontal(classes="form-row"):
                    yield Label("Target Price", classes="form-label")
                    yield Input(
                        placeholder="0.00", id="alert-price", classes="form-input"
                    )

                with Horizontal(classes="form-row"):
                    yield Label("Note (opt.)", classes="form-label")
                    yield Input(
                        placeholder="Optional note",
                        id="alert-note",
                        classes="form-input",
                    )

                yield Static("", id="alert-error", classes="error-msg")

                with Horizontal(classes="form-actions"):
                    yield Button("Add Alert", id="add-alert-btn", variant="primary")

        yield Footer()

    def on_mount(self) -> None:
        self.refresh_table()
        if getattr(self.app, "_read_only", False):
            # Hide the add-alert form entirely in read-only sessions
            try:
                self.query_one(".add-alert-form").display = False
            except Exception:
                pass

    def refresh_table(self) -> None:
        """Reload all alerts from the database and repopulate the table."""
        alerts = db.get_all_alerts()
        table = self.query_one("#alerts-table", DataTable)
        table.clear()

        for alert in alerts:
            direction_color = (
                "alert-above" if alert.direction == "above" else "alert-below"
            )
            direction_symbol = "▲" if alert.direction == "above" else "▼"
            status = "✓ Triggered" if alert.triggered else "⏳ Active"
            status_class = "triggered" if alert.triggered else ""

            table.add_row(
                alert.symbol,
                f"[{direction_color}]{direction_symbol} {alert.direction.capitalize()}[/{direction_color}]",
                f"${alert.target_price:,.2f}",
                f"[{status_class}]{status}[/{status_class}]"
                if status_class
                else status,
                alert.created_at.strftime("%Y-%m-%d %H:%M")
                if alert.created_at
                else "—",
                alert.triggered_at.strftime("%Y-%m-%d %H:%M")
                if alert.triggered_at
                else "—",
                alert.note or "—",
                key=str(alert.id),
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-alert-btn":
            self._add_alert()

    def _add_alert(self) -> None:
        if getattr(self.app, "_read_only", False):
            self.notify(
                "Read-only session — close the other session to manage alerts",
                severity="warning",
            )
            return
        error = self.query_one("#alert-error", Static)
        error.update("")

        symbol = self.query_one("#alert-symbol", Input).value.strip().upper()
        direction = self.query_one("#alert-direction", Select).value
        price_str = self.query_one("#alert-price", Input).value.strip()
        note = self.query_one("#alert-note", Input).value.strip()

        if not symbol or "/" not in symbol:
            error.update("Symbol required (e.g. BTC/USD)")
            return

        try:
            target_price = float(price_str)
            if target_price <= 0:
                raise ValueError
        except ValueError:
            error.update("Target price must be a positive number")
            return

        alert = PriceAlert(
            symbol=symbol,
            target_price=target_price,
            direction=direction,
            note=note,
        )
        self._alert_manager.add_alert(alert)

        # Clear form
        self.query_one("#alert-symbol", Input).value = ""
        self.query_one("#alert-price", Input).value = ""
        self.query_one("#alert-note", Input).value = ""

        self.refresh_table()
        self.notify(
            f"Alert set: {symbol} {direction} ${target_price:,.2f}",
            severity="information",
        )

    def action_delete_selected(self) -> None:
        if getattr(self.app, "_read_only", False):
            self.notify(
                "Read-only session — close the other session to manage alerts",
                severity="warning",
            )
            return
        table = self.query_one("#alerts-table", DataTable)
        if table.cursor_row < 0 or table.row_count == 0:
            return
        try:
            rows = list(table.ordered_rows)
            alert_id = int(rows[table.cursor_row].key.value)
            self._alert_manager.remove_alert(alert_id)
            self.refresh_table()
            self.notify("Alert deleted", severity="information")
        except Exception as e:
            self.notify(f"Could not delete alert: {e}", severity="error")

    def notify_triggered(self, alert: PriceAlert, price: float) -> None:
        """Called by the app when an alert fires — refresh the table."""
        self.refresh_table()

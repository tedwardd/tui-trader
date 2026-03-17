"""
Stop-loss adjustment modal.

Triggered from the dashboard with `l` on a selected position.
Allows the user to set a manual stop-loss price or revert to the
default percentage-based calculation.
"""

from typing import Callable, Optional

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Input, Button, Label
from textual.containers import Vertical, Horizontal


# Callback: called with the new stop price (float) or None to clear
StopLossCallback = Callable[[Optional[float]], None]


class StopLossModal(ModalScreen):
    """
    Modal dialog for setting or clearing a manual stop-loss price.
    """

    DEFAULT_CSS = """
    StopLossModal {
        align: center middle;
    }
    StopLossModal > Vertical {
        width: 50;
        height: auto;
        border: solid $warning;
        background: $surface;
        padding: 1 2;
    }
    StopLossModal .modal-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    StopLossModal .info-row {
        height: 1;
        color: $text-muted;
        margin-bottom: 0;
    }
    StopLossModal .input-row {
        height: 3;
        margin-top: 1;
    }
    StopLossModal .input-label {
        width: 14;
        content-align: right middle;
        padding-right: 1;
    }
    StopLossModal .error-msg {
        color: $error;
        height: 1;
        margin-top: 0;
    }
    StopLossModal .actions {
        height: 3;
        margin-top: 1;
    }
    StopLossModal .btn-set   { margin-right: 1; }
    StopLossModal .btn-clear { margin-right: 1; }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(
        self,
        symbol: str,
        avg_entry: float,
        current_price: float,
        current_stop: float,
        stop_is_manual: bool,
        on_confirm: StopLossCallback,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._symbol = symbol
        self._avg_entry = avg_entry
        self._current_price = current_price
        self._current_stop = current_stop
        self._stop_is_manual = stop_is_manual
        self._on_confirm = on_confirm

    def compose(self) -> ComposeResult:
        stop_source = "manual" if self._stop_is_manual else "calculated"
        with Vertical():
            yield Static(f"Set Stop-Loss — {self._symbol}", classes="modal-title")
            yield Static(
                f"Current price:  ${self._current_price:,.2f}", classes="info-row"
            )
            yield Static(f"Avg entry:      ${self._avg_entry:,.2f}", classes="info-row")
            yield Static(
                f"Current stop:   ${self._current_stop:,.2f}  ({stop_source})",
                classes="info-row",
            )
            with Horizontal(classes="input-row"):
                yield Label("New stop price:", classes="input-label")
                yield Input(
                    placeholder=f"{self._current_stop:,.2f}",
                    id="stop-input",
                )
            yield Static("", id="error-msg", classes="error-msg")
            with Horizontal(classes="actions"):
                yield Button("Set", id="btn-set", variant="warning", classes="btn-set")
                yield Button("Clear to default", id="btn-clear", classes="btn-clear")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#stop-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss()
            return

        if getattr(self.app, "_read_only", False):
            self.query_one("#error-msg", Static).update(
                "Read-only session — close the other session to modify stop-loss"
            )
            return

        if event.button.id == "btn-clear":
            self._on_confirm(None)
            self.dismiss()
            return

        if event.button.id == "btn-set":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Allow pressing Enter in the input to confirm."""
        self._submit()

    def _submit(self) -> None:
        error = self.query_one("#error-msg", Static)
        raw = self.query_one("#stop-input", Input).value.strip().replace(",", "")

        try:
            price = float(raw)
        except ValueError:
            error.update("Please enter a valid number")
            return

        if price <= 0:
            error.update("Stop price must be greater than zero")
            return

        if price >= self._current_price:
            error.update(
                f"Stop price must be below current price (${self._current_price:,.2f})"
            )
            return

        self._on_confirm(price)
        self.dismiss()

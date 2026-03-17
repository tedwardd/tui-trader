"""
Price alert evaluation logic.

Alerts are evaluated on every WebSocket ticker update — zero polling needed.
When an alert triggers, it fires a callback that the TUI uses to show a notification.
"""

from datetime import datetime
from typing import Callable, Optional

from app.models import PriceAlert
from app import database as db


# Type alias for the notification callback
AlertCallback = Callable[[PriceAlert, float], None]


class AlertManager:
    """
    Evaluates active price alerts against incoming price updates.

    Usage:
        manager = AlertManager(on_trigger=app.notify_alert)
        manager.reload()  # load alerts from DB on startup

        # Called by the WebSocket ticker worker on every price update:
        manager.evaluate("BTC/USD", 67500.0)
    """

    def __init__(self, on_trigger: Optional[AlertCallback] = None) -> None:
        self._on_trigger = on_trigger
        self._active_alerts: list[PriceAlert] = []

    def reload(self) -> None:
        """Reload active (untriggered) alerts from the database."""
        self._active_alerts = db.get_active_alerts()

    def add_alert(self, alert: PriceAlert) -> PriceAlert:
        """Persist a new alert and add it to the active set."""
        saved = db.save_alert(alert)
        self._active_alerts.append(saved)
        return saved

    def remove_alert(self, alert_id: int) -> None:
        """Delete an alert from the database and remove from active set."""
        db.delete_alert(alert_id)
        self._active_alerts = [a for a in self._active_alerts if a.id != alert_id]

    def evaluate(self, symbol: str, current_price: float) -> list[PriceAlert]:
        """
        Check all active alerts for the given symbol against the current price.
        Triggers and removes any alerts that have been hit.

        Returns a list of alerts that were triggered this evaluation.
        """
        triggered: list[PriceAlert] = []
        remaining: list[PriceAlert] = []

        for alert in self._active_alerts:
            if alert.symbol != symbol:
                remaining.append(alert)
                continue

            hit = (
                alert.direction == "above" and current_price >= alert.target_price
            ) or (
                alert.direction == "below" and current_price <= alert.target_price
            )

            if hit:
                triggered.append(alert)
                db.mark_alert_triggered(alert.id)
                if self._on_trigger:
                    self._on_trigger(alert, current_price)
            else:
                remaining.append(alert)

        self._active_alerts = remaining
        return triggered

    @property
    def active_alerts(self) -> list[PriceAlert]:
        return list(self._active_alerts)

    def get_alerts_for_symbol(self, symbol: str) -> list[PriceAlert]:
        return [a for a in self._active_alerts if a.symbol == symbol]

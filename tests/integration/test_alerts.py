"""
Integration tests for app/alerts.py (AlertManager)

Uses the db_engine fixture for a real in-memory database.
"""

import pytest
from unittest.mock import MagicMock, patch
from app.models import PriceAlert
from app.alerts import AlertManager
import app.database as db


@pytest.fixture
def alert_manager(db_engine):
    """Fresh AlertManager with a real in-memory DB."""
    manager = AlertManager()
    return manager


class TestAlertManagerInit:
    def test_starts_empty(self, alert_manager):
        assert alert_manager.active_alerts == []

    def test_reload_loads_from_db(self, db_engine):
        db.save_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        db.save_alert(PriceAlert(symbol="BTC/USD", target_price=70000, direction="below"))
        manager = AlertManager()
        manager.reload()
        assert len(manager.active_alerts) == 2

    def test_reload_excludes_triggered(self, db_engine):
        a = db.save_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        db.mark_alert_triggered(a.id)
        manager = AlertManager()
        manager.reload()
        assert len(manager.active_alerts) == 0


class TestAlertManagerAddRemove:
    def test_add_alert_persists_and_tracks(self, alert_manager, db_engine):
        alert = PriceAlert(symbol="BTC/USD", target_price=80000, direction="above")
        saved = alert_manager.add_alert(alert)
        assert saved.id is not None
        assert len(alert_manager.active_alerts) == 1

    def test_remove_alert_deletes_and_untracks(self, alert_manager, db_engine):
        alert = PriceAlert(symbol="BTC/USD", target_price=80000, direction="above")
        saved = alert_manager.add_alert(alert)
        alert_manager.remove_alert(saved.id)
        assert len(alert_manager.active_alerts) == 0
        assert db.get_all_alerts() == []

    def test_active_alerts_returns_copy(self, alert_manager, db_engine):
        alert_manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        copy = alert_manager.active_alerts
        copy.clear()
        assert len(alert_manager.active_alerts) == 1


class TestAlertManagerEvaluate:
    def test_above_alert_fires_when_price_reaches_target(self, alert_manager, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append((a, p)))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))

        result = manager.evaluate("BTC/USD", 80000.0)
        assert len(result) == 1
        assert len(triggered) == 1
        assert triggered[0][1] == 80000.0

    def test_above_alert_fires_when_price_exceeds_target(self, alert_manager, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append(a))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        manager.evaluate("BTC/USD", 85000.0)
        assert len(triggered) == 1

    def test_above_alert_does_not_fire_below_target(self, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append(a))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        manager.evaluate("BTC/USD", 79999.0)
        assert len(triggered) == 0

    def test_below_alert_fires_when_price_reaches_target(self, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append(a))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=70000, direction="below"))
        manager.evaluate("BTC/USD", 70000.0)
        assert len(triggered) == 1

    def test_below_alert_fires_when_price_drops_below_target(self, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append(a))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=70000, direction="below"))
        manager.evaluate("BTC/USD", 65000.0)
        assert len(triggered) == 1

    def test_below_alert_does_not_fire_above_target(self, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append(a))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=70000, direction="below"))
        manager.evaluate("BTC/USD", 70001.0)
        assert len(triggered) == 0

    def test_triggered_alert_removed_from_active(self, db_engine):
        manager = AlertManager()
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        manager.evaluate("BTC/USD", 80000.0)
        assert len(manager.active_alerts) == 0

    def test_different_symbol_alert_not_triggered(self, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append(a))
        manager.add_alert(PriceAlert(symbol="ETH/USD", target_price=4000, direction="above"))
        manager.evaluate("BTC/USD", 90000.0)
        assert len(triggered) == 0
        assert len(manager.active_alerts) == 1

    def test_multiple_alerts_partial_trigger(self, db_engine):
        triggered = []
        manager = AlertManager(on_trigger=lambda a, p: triggered.append(a))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=90000, direction="above"))
        manager.evaluate("BTC/USD", 85000.0)
        assert len(triggered) == 1
        assert len(manager.active_alerts) == 1  # second alert still active

    def test_evaluate_returns_triggered_alerts(self, db_engine):
        manager = AlertManager()
        a1 = manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=90000, direction="above"))
        result = manager.evaluate("BTC/USD", 85000.0)
        assert len(result) == 1
        assert result[0].id == a1.id

    def test_get_alerts_for_symbol(self, db_engine):
        manager = AlertManager()
        manager.add_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        manager.add_alert(PriceAlert(symbol="ETH/USD", target_price=4000, direction="above"))
        btc_alerts = manager.get_alerts_for_symbol("BTC/USD")
        assert len(btc_alerts) == 1
        assert btc_alerts[0].symbol == "BTC/USD"

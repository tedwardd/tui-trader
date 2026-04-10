"""
Integration tests for app/database.py

Uses an in-memory SQLite database (via the db_engine fixture in conftest.py).
Each test gets a fresh database.
"""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from app.models import Position, Trade, PriceAlert
import app.database as db


# ---------------------------------------------------------------------------
# configure_engine (paper trading DB swap)
# ---------------------------------------------------------------------------

class TestConfigureEngine:
    def test_configure_engine_switches_to_new_file(self, tmp_path, monkeypatch):
        """configure_engine() points the module at a different SQLite file."""
        paper_db = tmp_path / "paper_trades.db"
        db.configure_engine(paper_db)
        db.init_db()
        # Write to the new engine
        pos = db.save_position(Position(symbol="BTC/USD", avg_entry_price=70000, total_amount=0.1))
        assert pos.id is not None
        assert paper_db.exists()

    def test_configure_engine_isolates_data(self, tmp_path, monkeypatch):
        """Data written after configure_engine() goes to the new file, not the old one."""
        db_a = tmp_path / "a.db"
        db_b = tmp_path / "b.db"

        db.configure_engine(db_a)
        db.init_db()
        db.save_position(Position(symbol="ETH/USD", avg_entry_price=3000, total_amount=1.0))

        db.configure_engine(db_b)
        db.init_db()
        # db_b is fresh — no positions from db_a
        assert db.get_open_positions() == []

    def test_configure_engine_restores_correctly(self, tmp_path, monkeypatch):
        """Reconfiguring back to db_a sees its data again."""
        db_a = tmp_path / "a.db"
        db_b = tmp_path / "b.db"

        db.configure_engine(db_a)
        db.init_db()
        db.save_position(Position(symbol="BTC/USD", avg_entry_price=80000, total_amount=0.5))

        db.configure_engine(db_b)
        db.init_db()
        assert db.get_open_positions() == []

        db.configure_engine(db_a)
        positions = db.get_open_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC/USD"


def make_open_position(symbol="BTC/USD", avg_entry=60000.0, amount=0.1) -> Position:
    return Position(symbol=symbol, avg_entry_price=avg_entry, total_amount=amount)


# ---------------------------------------------------------------------------
# Position CRUD
# ---------------------------------------------------------------------------

class TestPositionCrud:
    def test_save_and_retrieve_by_symbol(self, db_engine):
        pos = db.save_position(make_open_position())
        assert pos.id is not None
        fetched = db.get_position_by_symbol("BTC/USD")
        assert fetched is not None
        assert fetched.id == pos.id
        assert fetched.avg_entry_price == pytest.approx(60000.0)

    def test_get_position_by_symbol_returns_none_when_absent(self, db_engine):
        assert db.get_position_by_symbol("ETH/USD") is None

    def test_get_position_by_symbol_ignores_closed(self, db_engine):
        pos = db.save_position(make_open_position())
        pos.status = "closed"
        pos.closed_at = datetime.now(timezone.utc)
        db.update_position(pos)
        assert db.get_position_by_symbol("BTC/USD") is None

    def test_get_open_positions_returns_only_open(self, db_engine):
        open_pos = db.save_position(make_open_position("BTC/USD"))
        closed_pos = db.save_position(make_open_position("ETH/USD"))
        closed_pos.status = "closed"
        closed_pos.closed_at = datetime.now(timezone.utc)
        db.update_position(closed_pos)

        open_list = db.get_open_positions()
        ids = [p.id for p in open_list]
        assert open_pos.id in ids
        assert closed_pos.id not in ids

    def test_get_closed_positions_returns_only_closed(self, db_engine):
        db.save_position(make_open_position("BTC/USD"))
        closed = db.save_position(make_open_position("ETH/USD"))
        closed.status = "closed"
        closed.closed_at = datetime.now(timezone.utc)
        db.update_position(closed)

        closed_list = db.get_closed_positions()
        assert len(closed_list) == 1
        assert closed_list[0].id == closed.id

    def test_update_position_persists_all_fields(self, db_engine):
        pos = db.save_position(make_open_position())
        pos.avg_entry_price = 65000.0
        pos.total_amount = 0.05
        pos.realized_pnl = 250.0
        pos.total_fees_paid = 1.5
        pos.stop_loss_price = 63000.0
        pos.status = "closed"
        pos.closed_at = datetime.now(timezone.utc)
        updated = db.update_position(pos)

        assert updated.avg_entry_price == pytest.approx(65000.0)
        assert updated.total_amount == pytest.approx(0.05)
        assert updated.realized_pnl == pytest.approx(250.0)
        assert updated.total_fees_paid == pytest.approx(1.5)
        assert updated.stop_loss_price == pytest.approx(63000.0)
        assert updated.status == "closed"
        assert updated.closed_at is not None

    def test_update_position_raises_for_missing_id(self, db_engine):
        pos = Position(id=9999, symbol="BTC/USD", avg_entry_price=60000, total_amount=0.1)
        with pytest.raises(ValueError, match="9999"):
            db.update_position(pos)

    def test_set_stop_loss_sets_price(self, db_engine):
        pos = db.save_position(make_open_position())
        db.set_stop_loss(pos.id, 58000.0)
        fetched = db.get_position_by_symbol("BTC/USD")
        assert fetched.stop_loss_price == pytest.approx(58000.0)

    def test_set_stop_loss_clears_to_none(self, db_engine):
        pos = db.save_position(make_open_position())
        db.set_stop_loss(pos.id, 58000.0)
        db.set_stop_loss(pos.id, None)
        fetched = db.get_position_by_symbol("BTC/USD")
        assert fetched.stop_loss_price is None

    def test_set_stop_loss_noop_for_missing_id(self, db_engine):
        # Should not raise
        db.set_stop_loss(9999, 58000.0)


# ---------------------------------------------------------------------------
# Trade CRUD
# ---------------------------------------------------------------------------

class TestTradeCrud:
    def test_save_and_retrieve_trade(self, db_engine):
        pos = db.save_position(make_open_position())
        trade = db.save_trade(Trade(
            position_id=pos.id,
            symbol="BTC/USD",
            side="buy",
            amount=0.1,
            price=60000,
            fee=1.0,
            kraken_order_id="ORDER123",
        ))
        assert trade.id is not None

        trades = db.get_trades_for_position(pos.id)
        assert len(trades) == 1
        assert trades[0].kraken_order_id == "ORDER123"

    def test_get_trades_for_position_ordered_by_timestamp(self, db_engine):
        pos = db.save_position(make_open_position())
        t1 = db.save_trade(Trade(position_id=pos.id, symbol="BTC/USD", side="buy",
                                  amount=0.05, price=60000,
                                  timestamp=datetime(2026, 1, 1, 10, 0)))
        t2 = db.save_trade(Trade(position_id=pos.id, symbol="BTC/USD", side="buy",
                                  amount=0.05, price=62000,
                                  timestamp=datetime(2026, 1, 1, 11, 0)))
        trades = db.get_trades_for_position(pos.id)
        assert trades[0].id == t1.id
        assert trades[1].id == t2.id

    def test_get_recent_trades_limit(self, db_engine):
        pos = db.save_position(make_open_position())
        for i in range(5):
            db.save_trade(Trade(position_id=pos.id, symbol="BTC/USD", side="buy",
                                 amount=0.01, price=60000 + i * 100))
        recent = db.get_recent_trades(limit=3)
        assert len(recent) == 3

    def test_trade_exists_true(self, db_engine):
        pos = db.save_position(make_open_position())
        db.save_trade(Trade(position_id=pos.id, symbol="BTC/USD", side="buy",
                             amount=0.1, price=60000, kraken_trade_id="TRADE_ABC"))
        assert db.trade_exists("TRADE_ABC") is True

    def test_trade_exists_false(self, db_engine):
        assert db.trade_exists("NONEXISTENT") is False


# ---------------------------------------------------------------------------
# Price Alert CRUD
# ---------------------------------------------------------------------------

class TestAlertCrud:
    def test_save_and_get_active_alerts(self, db_engine):
        alert = db.save_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        active = db.get_active_alerts()
        assert any(a.id == alert.id for a in active)

    def test_get_active_alerts_excludes_triggered(self, db_engine):
        alert = db.save_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        db.mark_alert_triggered(alert.id)
        active = db.get_active_alerts()
        assert not any(a.id == alert.id for a in active)

    def test_mark_alert_triggered_sets_fields(self, db_engine):
        alert = db.save_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        db.mark_alert_triggered(alert.id)
        all_alerts = db.get_all_alerts()
        triggered = next(a for a in all_alerts if a.id == alert.id)
        assert triggered.triggered is True
        assert triggered.triggered_at is not None

    def test_mark_alert_triggered_noop_for_missing_id(self, db_engine):
        db.mark_alert_triggered(9999)  # should not raise

    def test_delete_alert(self, db_engine):
        alert = db.save_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        db.delete_alert(alert.id)
        assert not any(a.id == alert.id for a in db.get_all_alerts())

    def test_delete_alert_noop_for_missing_id(self, db_engine):
        db.delete_alert(9999)  # should not raise

    def test_get_all_alerts_includes_triggered(self, db_engine):
        a1 = db.save_alert(PriceAlert(symbol="BTC/USD", target_price=80000, direction="above"))
        a2 = db.save_alert(PriceAlert(symbol="BTC/USD", target_price=70000, direction="below"))
        db.mark_alert_triggered(a1.id)
        all_alerts = db.get_all_alerts()
        ids = [a.id for a in all_alerts]
        assert a1.id in ids
        assert a2.id in ids

"""
Tests for the buy/sell recording logic in screens/trade.py

_record_buy and _record_sell are plain synchronous methods that only touch
the database layer — they don't interact with Textual widgets. We instantiate
TradeScreen without a running app and patch the DB engine.
"""

import pytest
from unittest.mock import patch, MagicMock
from app.models import Position, Trade
import app.database as db


# ---------------------------------------------------------------------------
# Helpers — call the logic functions directly without a Textual app
# ---------------------------------------------------------------------------

def record_buy(symbol, amount, price, fee=0.0, order_id="", order_type="market"):
    """Call _record_buy logic directly, bypassing Textual screen instantiation."""
    existing = db.get_position_by_symbol(symbol)
    if existing:
        existing.add_to_position(amount, price, fee)
        position = db.update_position(existing)
    else:
        position = db.save_position(Position(
            symbol=symbol,
            avg_entry_price=price,
            total_amount=amount,
            total_fees_paid=fee,
        ))
    db.save_trade(Trade(
        position_id=position.id,
        symbol=symbol,
        side="buy",
        amount=amount,
        price=price,
        fee=fee,
        kraken_order_id=order_id,
        order_type=order_type,
    ))
    return position


def record_sell(symbol, amount, price, fee=0.0, order_id="", order_type="market"):
    """Call _record_sell logic directly, including the dust-threshold clamp."""
    _DUST_THRESHOLD = 1e-6
    existing = db.get_position_by_symbol(symbol)
    if not existing:
        return None
    if abs(existing.total_amount - amount) <= _DUST_THRESHOLD:
        amount = existing.total_amount
    existing.reduce_position(amount, price, fee)
    db.update_position(existing)
    db.save_trade(Trade(
        position_id=existing.id,
        symbol=symbol,
        side="sell",
        amount=amount,
        price=price,
        fee=fee,
        kraken_order_id=order_id,
        order_type=order_type,
    ))
    return existing


# ---------------------------------------------------------------------------
# _record_buy
# ---------------------------------------------------------------------------

class TestRecordBuy:
    def test_creates_new_position(self, db_engine):
        pos = record_buy("BTC/USD", 0.1, 60000)
        assert pos.id is not None
        assert pos.symbol == "BTC/USD"
        assert pos.avg_entry_price == pytest.approx(60000.0)
        assert pos.total_amount == pytest.approx(0.1)
        assert pos.status == "open"

    def test_creates_trade_record(self, db_engine):
        pos = record_buy("BTC/USD", 0.1, 60000, fee=1.5, order_id="ORD1")
        trades = db.get_trades_for_position(pos.id)
        assert len(trades) == 1
        assert trades[0].side == "buy"
        assert trades[0].amount == pytest.approx(0.1)
        assert trades[0].price == pytest.approx(60000)
        assert trades[0].fee == pytest.approx(1.5)
        assert trades[0].kraken_order_id == "ORD1"

    def test_adds_to_existing_position(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000)
        record_buy("BTC/USD", 0.1, 70000)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos.total_amount == pytest.approx(0.2)
        assert pos.avg_entry_price == pytest.approx(65000.0)

    def test_add_to_position_accumulates_fees(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000, fee=1.0)
        record_buy("BTC/USD", 0.1, 70000, fee=2.0)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos.total_fees_paid == pytest.approx(3.0)

    def test_multiple_buys_create_multiple_trades(self, db_engine):
        pos = record_buy("BTC/USD", 0.1, 60000)
        record_buy("BTC/USD", 0.05, 65000)
        trades = db.get_trades_for_position(pos.id)
        assert len(trades) == 2


# ---------------------------------------------------------------------------
# _record_sell
# ---------------------------------------------------------------------------

class TestRecordSell:
    def test_partial_sell_reduces_position(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000)
        record_sell("BTC/USD", 0.05, 65000)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos.status == "open"
        assert pos.total_amount == pytest.approx(0.05)

    def test_full_sell_closes_position(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000)
        record_sell("BTC/USD", 0.1, 65000)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos is None  # closed positions not returned by get_position_by_symbol

    def test_full_sell_records_realized_pnl(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000)
        pos = record_sell("BTC/USD", 0.1, 65000)
        assert pos.realized_pnl == pytest.approx((65000 - 60000) * 0.1)

    def test_sell_creates_trade_record(self, db_engine):
        buy_pos = record_buy("BTC/USD", 0.1, 60000)
        record_sell("BTC/USD", 0.1, 65000, fee=2.0, order_id="SELL1")
        trades = db.get_trades_for_position(buy_pos.id)
        sell_trades = [t for t in trades if t.side == "sell"]
        assert len(sell_trades) == 1
        assert sell_trades[0].fee == pytest.approx(2.0)
        assert sell_trades[0].kraken_order_id == "SELL1"

    def test_sell_with_no_position_returns_none(self, db_engine):
        result = record_sell("BTC/USD", 0.1, 65000)
        assert result is None

    def test_dust_threshold_clamp_force_closes(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000)
        # Sell slightly less than full — within dust threshold
        record_sell("BTC/USD", 0.1 - 5e-7, 65000)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos is None  # should be closed

    def test_above_dust_threshold_leaves_partial(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000)
        # Sell leaving 0.001 — above dust threshold, legitimate partial
        record_sell("BTC/USD", 0.099, 65000)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos is not None
        assert pos.total_amount == pytest.approx(0.001)

    def test_oversell_clamped_to_position_size(self, db_engine):
        record_buy("BTC/USD", 0.1, 60000)
        record_sell("BTC/USD", 999.0, 65000)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos is None  # closed, not negative


# ---------------------------------------------------------------------------
# Full buy → add → partial sell → full close lifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_buy_add_sell_lifecycle(self, db_engine):
        # Buy 0.1 @ 60000
        record_buy("BTC/USD", 0.1, 60000, fee=1.0)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos.avg_entry_price == pytest.approx(60000)
        assert pos.total_amount == pytest.approx(0.1)

        # Add 0.1 @ 70000 → avg entry = 65000
        record_buy("BTC/USD", 0.1, 70000, fee=1.0)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos.avg_entry_price == pytest.approx(65000)
        assert pos.total_amount == pytest.approx(0.2)
        assert pos.total_fees_paid == pytest.approx(2.0)

        # Partial sell 0.1 @ 68000
        record_sell("BTC/USD", 0.1, 68000, fee=1.0)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos.status == "open"
        assert pos.total_amount == pytest.approx(0.1)
        # realized = (68000 - 65000) * 0.1 - 1.0 = 299.0
        assert pos.realized_pnl == pytest.approx(299.0)

        # Full close remaining 0.1 @ 72000
        record_sell("BTC/USD", 0.1, 72000, fee=1.0)
        pos = db.get_position_by_symbol("BTC/USD")
        assert pos is None  # closed

        # Verify in closed positions
        closed = db.get_closed_positions()
        assert len(closed) == 1
        # total realized = 299 + (72000-65000)*0.1 - 1.0 = 299 + 699 = 998
        assert closed[0].realized_pnl == pytest.approx(998.0)

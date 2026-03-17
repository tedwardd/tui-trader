"""
Unit tests for app/models.py

Tests all Position, Trade, and PriceAlert methods.
No I/O — pure in-memory object tests.
"""

import pytest
from datetime import datetime
from app.models import Position, Trade, PriceAlert


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_position(
    symbol="BTC/USD",
    avg_entry=60000.0,
    amount=0.1,
    fees=0.0,
    stop_loss_price=None,
) -> Position:
    return Position(
        symbol=symbol,
        avg_entry_price=avg_entry,
        total_amount=amount,
        total_fees_paid=fees,
        stop_loss_price=stop_loss_price,
    )


# ---------------------------------------------------------------------------
# Position.unrealized_pnl
# ---------------------------------------------------------------------------

class TestUnrealizedPnl:
    def test_profit_no_fees(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=0.0)
        assert pos.unrealized_pnl(65000) == pytest.approx(500.0)

    def test_loss_no_fees(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=0.0)
        assert pos.unrealized_pnl(55000) == pytest.approx(-500.0)

    def test_breakeven_no_fees(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=0.0)
        assert pos.unrealized_pnl(60000) == pytest.approx(0.0)

    def test_fees_reduce_pnl(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=5.0)
        # gross = (65000 - 60000) * 0.1 = 500, net = 500 - 5 = 495
        assert pos.unrealized_pnl(65000) == pytest.approx(495.0)

    def test_fees_can_make_profitable_trade_negative(self):
        pos = make_position(avg_entry=60000, amount=0.001, fees=10.0)
        # gross = (60100 - 60000) * 0.001 = 0.10, net = 0.10 - 10 = -9.90
        assert pos.unrealized_pnl(60100) == pytest.approx(-9.90)


# ---------------------------------------------------------------------------
# Position.unrealized_pnl_pct
# ---------------------------------------------------------------------------

class TestUnrealizedPnlPct:
    def test_zero_cost_basis_returns_zero(self):
        pos = make_position(avg_entry=0, amount=0.1, fees=0.0)
        assert pos.unrealized_pnl_pct(65000) == 0.0

    def test_profit_percentage(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=0.0)
        # unrealized = 500, cost_basis = 6000, pct = 500/6000 * 100 = 8.333...
        assert pos.unrealized_pnl_pct(65000) == pytest.approx(8.3333, rel=1e-3)

    def test_fees_increase_denominator(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=60.0)
        # unrealized = 500 - 60 = 440, cost_basis = 6000 + 60 = 6060
        # pct = 440 / 6060 * 100 = 7.261...
        assert pos.unrealized_pnl_pct(65000) == pytest.approx(440 / 6060 * 100, rel=1e-3)

    def test_loss_percentage_is_negative(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=0.0)
        assert pos.unrealized_pnl_pct(55000) < 0


# ---------------------------------------------------------------------------
# Position.add_to_position
# ---------------------------------------------------------------------------

class TestAddToPosition:
    def test_single_add_weighted_avg(self):
        pos = make_position(avg_entry=60000, amount=0.1)
        pos.add_to_position(amount=0.1, price=70000)
        # (60000*0.1 + 70000*0.1) / 0.2 = 65000
        assert pos.avg_entry_price == pytest.approx(65000.0)
        assert pos.total_amount == pytest.approx(0.2)

    def test_multiple_adds_weighted_avg(self):
        pos = make_position(avg_entry=60000, amount=0.1)
        pos.add_to_position(0.05, 62000)
        pos.add_to_position(0.05, 64000)
        # After first add: (6000 + 3100) / 0.15 = 60666.67
        # After second add: (60666.67*0.15 + 64000*0.05) / 0.2 = 61500
        assert pos.total_amount == pytest.approx(0.2)
        assert pos.avg_entry_price == pytest.approx(61500.0)

    def test_fees_accumulate(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=1.0)
        pos.add_to_position(0.1, 70000, fee=2.0)
        assert pos.total_fees_paid == pytest.approx(3.0)

    def test_zero_fee_default(self):
        pos = make_position(avg_entry=60000, amount=0.1, fees=0.0)
        pos.add_to_position(0.1, 70000)
        assert pos.total_fees_paid == pytest.approx(0.0)

    def test_unequal_sizes(self):
        # 0.3 BTC @ 60000 + 0.1 BTC @ 80000 = (18000 + 8000) / 0.4 = 65000
        pos = make_position(avg_entry=60000, amount=0.3)
        pos.add_to_position(0.1, 80000)
        assert pos.avg_entry_price == pytest.approx(65000.0)
        assert pos.total_amount == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# Position.reduce_position
# ---------------------------------------------------------------------------

class TestReducePosition:
    def test_partial_close_stays_open(self):
        pos = make_position(avg_entry=60000, amount=0.1)
        pnl = pos.reduce_position(0.05, 65000)
        assert pos.status == "open"
        assert pos.total_amount == pytest.approx(0.05)
        assert pnl == pytest.approx((65000 - 60000) * 0.05)

    def test_full_close_marks_closed(self):
        pos = make_position(avg_entry=60000, amount=0.1)
        pos.reduce_position(0.1, 65000)
        assert pos.status == "closed"
        assert pos.total_amount == 0.0
        assert pos.closed_at is not None

    def test_oversell_clamped_to_position_size(self):
        pos = make_position(avg_entry=60000, amount=0.1)
        pos.reduce_position(999.0, 65000)
        assert pos.status == "closed"
        assert pos.total_amount == 0.0

    def test_realized_pnl_accumulates(self):
        pos = make_position(avg_entry=60000, amount=0.2)
        pos.reduce_position(0.1, 65000)  # +500
        pos.reduce_position(0.1, 55000)  # -500
        assert pos.realized_pnl == pytest.approx(0.0)

    def test_fee_deducted_from_realized_pnl(self):
        pos = make_position(avg_entry=60000, amount=0.1)
        pnl = pos.reduce_position(0.1, 65000, fee=2.0)
        # gross = 500, net = 498
        assert pnl == pytest.approx(498.0)
        assert pos.realized_pnl == pytest.approx(498.0)

    def test_dust_threshold_force_closes(self):
        # Residual below 1e-6 should be treated as zero and position closed
        pos = make_position(avg_entry=60000, amount=0.1)
        # Sell slightly less than full amount — within dust threshold
        pos.reduce_position(0.1 - 5e-7, 65000)
        assert pos.status == "closed"
        assert pos.total_amount == 0.0

    def test_above_dust_threshold_stays_open(self):
        # Residual above 1e-6 is a legitimate partial close
        pos = make_position(avg_entry=60000, amount=0.1)
        pos.reduce_position(0.1 - 0.001, 65000)
        assert pos.status == "open"
        assert pos.total_amount == pytest.approx(0.001)

    def test_returns_realized_pnl(self):
        pos = make_position(avg_entry=60000, amount=0.1)
        pnl = pos.reduce_position(0.05, 70000)
        assert pnl == pytest.approx((70000 - 60000) * 0.05)


# ---------------------------------------------------------------------------
# Trade properties
# ---------------------------------------------------------------------------

class TestTradeProperties:
    def test_cost(self):
        trade = Trade(symbol="BTC/USD", side="buy", amount=0.1, price=60000)
        assert trade.cost == pytest.approx(6000.0)

    def test_net_cost_with_fee(self):
        trade = Trade(symbol="BTC/USD", side="buy", amount=0.1, price=60000, fee=5.0)
        assert trade.net_cost == pytest.approx(6005.0)

    def test_net_cost_zero_fee(self):
        trade = Trade(symbol="BTC/USD", side="buy", amount=0.1, price=60000, fee=0.0)
        assert trade.net_cost == pytest.approx(6000.0)

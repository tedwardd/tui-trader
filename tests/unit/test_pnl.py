"""
Unit tests for app/pnl.py

All functions are pure (no I/O) — tests run without any fixtures.
"""

import pytest
from app.models import Position
from app.pnl import (
    calculate_snapshot,
    calculate_weighted_avg_entry,
    calculate_realized_pnl,
    calculate_portfolio_summary,
    format_pnl,
    format_pnl_pct,
    pnl_color_class,
    PositionSnapshot,
    PortfolioSummary,
)


# ---------------------------------------------------------------------------
# calculate_weighted_avg_entry
# ---------------------------------------------------------------------------


class TestCalculateWeightedAvgEntry:
    def test_equal_sizes(self):
        # 0.1 @ 60000 + 0.1 @ 70000 = 65000
        result = calculate_weighted_avg_entry(0.1, 60000, 0.1, 70000)
        assert result == pytest.approx(65000.0)

    def test_unequal_sizes(self):
        # 0.3 @ 60000 + 0.1 @ 80000 = (18000 + 8000) / 0.4 = 65000
        result = calculate_weighted_avg_entry(0.3, 60000, 0.1, 80000)
        assert result == pytest.approx(65000.0)

    def test_zero_existing_amount(self):
        result = calculate_weighted_avg_entry(0.0, 0.0, 0.1, 70000)
        assert result == pytest.approx(70000.0)

    def test_zero_total_returns_zero(self):
        result = calculate_weighted_avg_entry(0.0, 60000, 0.0, 70000)
        assert result == 0.0


# ---------------------------------------------------------------------------
# calculate_realized_pnl
# ---------------------------------------------------------------------------


class TestCalculateRealizedPnl:
    def test_profit(self):
        pnl = calculate_realized_pnl(60000, 70000, 0.1)
        assert pnl == pytest.approx(1000.0)

    def test_loss(self):
        pnl = calculate_realized_pnl(60000, 50000, 0.1)
        assert pnl == pytest.approx(-1000.0)

    def test_fee_deducted(self):
        pnl = calculate_realized_pnl(60000, 70000, 0.1, fee=5.0)
        assert pnl == pytest.approx(995.0)

    def test_zero_fee_default(self):
        pnl = calculate_realized_pnl(60000, 70000, 0.1)
        assert pnl == pytest.approx(1000.0)

    def test_breakeven_minus_fee(self):
        pnl = calculate_realized_pnl(60000, 60000, 0.1, fee=3.0)
        assert pnl == pytest.approx(-3.0)


# ---------------------------------------------------------------------------
# format_pnl
# ---------------------------------------------------------------------------


class TestFormatPnl:
    def test_positive_with_sign(self):
        assert format_pnl(1234.56) == "+$1,234.56"

    def test_negative_with_sign(self):
        assert format_pnl(-1234.56) == "-$1,234.56"

    def test_zero_with_sign(self):
        assert format_pnl(0.0) == "+$0.00"

    def test_without_sign(self):
        assert format_pnl(1234.56, include_sign=False) == "$1,234.56"

    def test_negative_without_sign(self):
        assert format_pnl(-1234.56, include_sign=False) == "$1,234.56"


# ---------------------------------------------------------------------------
# format_pnl_pct
# ---------------------------------------------------------------------------


class TestFormatPnlPct:
    def test_positive(self):
        assert format_pnl_pct(8.33) == "+8.33%"

    def test_negative(self):
        assert format_pnl_pct(-3.14) == "-3.14%"

    def test_zero(self):
        assert format_pnl_pct(0.0) == "+0.00%"


# ---------------------------------------------------------------------------
# pnl_color_class
# ---------------------------------------------------------------------------


class TestPnlColorClass:
    def test_positive(self):
        assert pnl_color_class(100.0) == "pnl-positive"

    def test_negative(self):
        assert pnl_color_class(-100.0) == "pnl-negative"

    def test_zero(self):
        assert pnl_color_class(0.0) == "pnl-neutral"


# ---------------------------------------------------------------------------
# calculate_snapshot
# ---------------------------------------------------------------------------


def make_pos(avg_entry=60000.0, amount=0.1, fees=0.0, stop_loss_price=None):
    return Position(
        symbol="BTC/USD",
        avg_entry_price=avg_entry,
        total_amount=amount,
        total_fees_paid=fees,
        stop_loss_price=stop_loss_price,
    )


class TestCalculateSnapshot:
    def test_basic_fields_populated(self):
        pos = make_pos(avg_entry=60000, amount=0.1)
        snap = calculate_snapshot(pos, 65000, 10000, stop_loss_pct=2.0)
        assert snap.symbol == "BTC/USD"
        assert snap.avg_entry_price == 60000
        assert snap.total_amount == 0.1
        assert snap.current_price == 65000

    def test_unrealized_pnl_fee_adjusted(self):
        pos = make_pos(avg_entry=60000, amount=0.1, fees=5.0)
        snap = calculate_snapshot(pos, 65000, 10000)
        # gross = 500, net = 495
        assert snap.unrealized_pnl == pytest.approx(495.0)

    def test_cost_basis(self):
        pos = make_pos(avg_entry=60000, amount=0.1)
        snap = calculate_snapshot(pos, 65000, 10000)
        assert snap.cost_basis == pytest.approx(6000.0)

    def test_current_value(self):
        pos = make_pos(avg_entry=60000, amount=0.1)
        snap = calculate_snapshot(pos, 65000, 10000)
        assert snap.current_value == pytest.approx(6500.0)

    def test_risk_pct(self):
        pos = make_pos(avg_entry=60000, amount=0.1)
        snap = calculate_snapshot(pos, 65000, 10000)
        # cost_basis = 6000, portfolio = 10000, risk = 60%
        assert snap.risk_pct == pytest.approx(60.0)

    def test_zero_portfolio_risk_pct_is_zero(self):
        pos = make_pos(avg_entry=60000, amount=0.1)
        snap = calculate_snapshot(pos, 65000, 0.0)
        assert snap.risk_pct == 0.0

    def test_default_stop_calculated(self):
        pos = make_pos(avg_entry=60000, amount=0.1)
        snap = calculate_snapshot(pos, 65000, 10000, stop_loss_pct=2.0)
        assert snap.suggested_stop_price == pytest.approx(60000 * 0.98)
        assert snap.stop_loss_pct == pytest.approx(2.0)
        assert snap.stop_is_manual is False

    def test_manual_stop_used_when_set(self):
        pos = make_pos(avg_entry=60000, amount=0.1, stop_loss_price=58000.0)
        snap = calculate_snapshot(pos, 65000, 10000, stop_loss_pct=2.0)
        assert snap.suggested_stop_price == pytest.approx(58000.0)
        assert snap.stop_is_manual is True
        # back-calculated pct: (1 - 58000/60000) * 100 = 3.333...
        assert snap.stop_loss_pct == pytest.approx((1 - 58000 / 60000) * 100, rel=1e-3)

    def test_manual_stop_overrides_default_pct(self):
        pos = make_pos(avg_entry=60000, amount=0.1, stop_loss_price=57000.0)
        snap = calculate_snapshot(pos, 65000, 10000, stop_loss_pct=2.0)
        # Default would be 58800, manual is 57000
        assert snap.suggested_stop_price == pytest.approx(57000.0)

    def test_clearing_manual_stop_reverts_to_default(self):
        pos = make_pos(avg_entry=60000, amount=0.1, stop_loss_price=None)
        snap = calculate_snapshot(pos, 65000, 10000, stop_loss_pct=2.0)
        assert snap.stop_is_manual is False
        assert snap.suggested_stop_price == pytest.approx(60000 * 0.98)


# ---------------------------------------------------------------------------
# calculate_portfolio_summary
# ---------------------------------------------------------------------------


def make_snapshot(**kwargs) -> PositionSnapshot:
    defaults = dict(
        symbol="BTC/USD",
        avg_entry_price=60000,
        total_amount=0.1,
        current_price=65000,
        unrealized_pnl=500,
        unrealized_pnl_pct=8.33,
        gross_pct=8.33,
        cost_basis=6000,
        current_value=6500,
        realized_pnl=0,
        suggested_stop_price=58800,
        stop_loss_pct=2.0,
        stop_is_manual=False,
        stop_source=None,
        portfolio_value_usd=10000,
        risk_pct=60.0,
    )
    defaults.update(kwargs)
    return PositionSnapshot(**defaults)


class TestCalculatePortfolioSummary:
    def test_empty_list(self):
        summary = calculate_portfolio_summary([])
        assert summary.total_unrealized_pnl == 0.0
        assert summary.total_realized_pnl == 0.0
        assert summary.total_cost_basis == 0.0
        assert summary.total_current_value == 0.0
        assert summary.position_count == 0

    def test_single_snapshot(self):
        snap = make_snapshot(
            unrealized_pnl=500,
            realized_pnl=100,
            cost_basis=6000,
            current_value=6500,
            risk_pct=60,
        )
        summary = calculate_portfolio_summary([snap])
        assert summary.total_unrealized_pnl == pytest.approx(500)
        assert summary.total_realized_pnl == pytest.approx(100)
        assert summary.position_count == 1

    def test_multiple_snapshots_aggregated(self):
        s1 = make_snapshot(
            unrealized_pnl=500,
            realized_pnl=0,
            cost_basis=6000,
            current_value=6500,
            risk_pct=60,
        )
        s2 = make_snapshot(
            symbol="ETH/USD",
            unrealized_pnl=-200,
            realized_pnl=50,
            cost_basis=3000,
            current_value=2800,
            risk_pct=30,
        )
        summary = calculate_portfolio_summary([s1, s2])
        assert summary.total_unrealized_pnl == pytest.approx(300)
        assert summary.total_realized_pnl == pytest.approx(50)
        assert summary.total_cost_basis == pytest.approx(9000)
        assert summary.total_current_value == pytest.approx(9300)
        assert summary.position_count == 2

    def test_total_pnl_property(self):
        snap = make_snapshot(unrealized_pnl=500, realized_pnl=100)
        summary = calculate_portfolio_summary([snap])
        assert summary.total_pnl == pytest.approx(600)

    def test_overall_pnl_pct_property(self):
        snap = make_snapshot(unrealized_pnl=600, cost_basis=6000)
        summary = calculate_portfolio_summary([snap])
        assert summary.overall_pnl_pct == pytest.approx(10.0)

    def test_overall_pnl_pct_zero_cost_basis(self):
        snap = make_snapshot(unrealized_pnl=0, cost_basis=0)
        summary = calculate_portfolio_summary([snap])
        assert summary.overall_pnl_pct == 0.0

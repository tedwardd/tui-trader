"""
Unit tests for app/indicators.py

All functions are pure (no I/O) — tests run without any fixtures.
"""

import pytest
from app.models import Position
from app.indicators import compute_atr, compute_rsi, compute_win_rate, compute_avg_r


def _make_candle(ts, o, h, l, c, v=0.0):
    return [ts, o, h, l, c, v]


def _flat_candles(n, h=110.0, l=90.0):
    """n candles with fixed high/low/open/close and TR=20."""
    return [_make_candle(i, 100.0, h, l, 100.0) for i in range(n)]


def _pos(realized_pnl, avg_entry_price=100.0, total_amount=1.0):
    return Position(
        symbol="BTC/USD",
        avg_entry_price=avg_entry_price,
        total_amount=total_amount,
        realized_pnl=realized_pnl,
    )


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------


class TestComputeAtr:
    def test_none_when_too_few_candles(self):
        assert compute_atr(_flat_candles(14)) is None

    def test_none_on_empty(self):
        assert compute_atr([]) is None

    def test_flat_candles_atr_equals_tr(self):
        # All TRs are 20 (h=110, l=90, consecutive close=100 so |h-pc|=10, |l-pc|=10)
        # seed ATR = mean(20*14) = 20; Wilder EMA of all-20 stays 20
        candles = _flat_candles(20)
        result = compute_atr(candles)
        assert result == pytest.approx(20.0)

    def test_exact_period_plus_one(self):
        # Minimum valid: period+1 = 15 candles → seed only, no smoothing step
        candles = _flat_candles(15)
        result = compute_atr(candles)
        assert result == pytest.approx(20.0)

    def test_returns_float(self):
        result = compute_atr(_flat_candles(20))
        assert isinstance(result, float)

    def test_custom_period(self):
        # period=3 needs 4 candles
        candles = _flat_candles(4)
        result = compute_atr(candles, period=3)
        assert result == pytest.approx(20.0)

    def test_none_custom_period_too_few(self):
        assert compute_atr(_flat_candles(3), period=3) is None


# ---------------------------------------------------------------------------
# compute_rsi
# ---------------------------------------------------------------------------


class TestComputeRsi:
    def test_none_when_too_few_prices(self):
        assert compute_rsi([100.0] * 14) is None

    def test_none_on_empty(self):
        assert compute_rsi([]) is None

    def test_all_gains_returns_100(self):
        # All prices rising → avg_loss == 0 → RSI = 100
        prices = [float(i) for i in range(1, 20)]
        assert compute_rsi(prices) == pytest.approx(100.0)

    def test_all_losses_returns_0(self):
        # All prices falling → avg_gain == 0
        prices = [float(20 - i) for i in range(20)]
        assert compute_rsi(prices) == pytest.approx(0.0)

    def test_alternating_near_50(self):
        # Alternating +1 / -1 → avg_gain ≈ avg_loss → RSI ≈ 50
        prices = [100.0 + (1 if i % 2 == 0 else -1) for i in range(30)]
        result = compute_rsi(prices)
        assert result is not None
        assert 40.0 < result < 60.0

    def test_exact_period_plus_one(self):
        # 15 prices = period+1, only seed step
        prices = [float(i) for i in range(1, 16)]
        result = compute_rsi(prices)
        assert result == pytest.approx(100.0)

    def test_returns_float(self):
        result = compute_rsi([float(i) for i in range(1, 20)])
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# compute_win_rate
# ---------------------------------------------------------------------------


class TestComputeWinRate:
    def test_empty_returns_none(self):
        assert compute_win_rate([]) is None

    def test_all_winners(self):
        positions = [_pos(100.0), _pos(50.0)]
        assert compute_win_rate(positions) == pytest.approx(100.0)

    def test_all_losers(self):
        positions = [_pos(-100.0), _pos(-50.0)]
        assert compute_win_rate(positions) == pytest.approx(0.0)

    def test_zero_pnl_counts_as_loss(self):
        positions = [_pos(100.0), _pos(0.0)]
        assert compute_win_rate(positions) == pytest.approx(50.0)

    def test_mixed(self):
        positions = [_pos(100.0), _pos(-50.0), _pos(25.0), _pos(-10.0)]
        assert compute_win_rate(positions) == pytest.approx(50.0)

    def test_single_winner(self):
        assert compute_win_rate([_pos(1.0)]) == pytest.approx(100.0)

    def test_single_loser(self):
        assert compute_win_rate([_pos(-1.0)]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_avg_r
# ---------------------------------------------------------------------------


class TestComputeAvgR:
    def test_empty_returns_none(self):
        assert compute_avg_r([]) is None

    def test_zero_cost_basis_skipped(self):
        # Position with avg_entry_price=0 should be skipped, not ZeroDivisionError
        positions = [_pos(100.0, avg_entry_price=0.0, total_amount=1.0)]
        assert compute_avg_r(positions) is None

    def test_zero_amount_skipped(self):
        positions = [_pos(100.0, avg_entry_price=100.0, total_amount=0.0)]
        assert compute_avg_r(positions) is None

    def test_basic_arithmetic(self):
        # pnl=10, entry=100, amount=1 → R = 10/100 = 10%
        positions = [_pos(10.0, avg_entry_price=100.0, total_amount=1.0)]
        assert compute_avg_r(positions) == pytest.approx(10.0)

    def test_average_of_multiple(self):
        # 10% and 20% → avg 15%
        positions = [
            _pos(10.0, avg_entry_price=100.0, total_amount=1.0),
            _pos(20.0, avg_entry_price=100.0, total_amount=1.0),
        ]
        assert compute_avg_r(positions) == pytest.approx(15.0)

    def test_negative_r(self):
        positions = [_pos(-5.0, avg_entry_price=100.0, total_amount=1.0)]
        assert compute_avg_r(positions) == pytest.approx(-5.0)

    def test_mixed_valid_and_zero_basis(self):
        # One valid (10%), one with zero basis (skipped)
        positions = [
            _pos(10.0, avg_entry_price=100.0, total_amount=1.0),
            _pos(50.0, avg_entry_price=0.0, total_amount=1.0),
        ]
        assert compute_avg_r(positions) == pytest.approx(10.0)

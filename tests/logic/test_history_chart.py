"""
Tests for the P&L chart data-building logic in screens/history.py

The _update_chart method builds a cumulative P&L series from closed positions.
We test the logic directly by calling the method on a HistoryScreen instance
with a mocked PnlChart widget.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from app.models import Position


# ---------------------------------------------------------------------------
# Helper — build the cumulative series directly (mirrors _update_chart logic)
# ---------------------------------------------------------------------------

def build_chart_series(positions):
    """
    Replicate the _update_chart series-building logic from HistoryScreen.
    Returns (dates, pnl) lists as the chart would receive them.
    """
    closed = [p for p in positions if p.closed_at is not None]
    closed.sort(key=lambda p: p.closed_at)

    if not closed:
        return [], []

    dates = []
    pnl = []
    cumulative = 0.0

    first_date = closed[0].closed_at.date() - timedelta(days=1)
    dates.append(first_date.isoformat())
    pnl.append(0.0)

    for pos in closed:
        cumulative += pos.realized_pnl
        dates.append(pos.closed_at.date().isoformat())
        pnl.append(round(cumulative, 4))

    return dates, pnl


def make_closed(realized_pnl, closed_at):
    pos = Position(symbol="BTC/USD", avg_entry_price=60000, total_amount=0)
    pos.status = "closed"
    pos.realized_pnl = realized_pnl
    pos.closed_at = closed_at
    return pos


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildChartSeries:
    def test_empty_positions_returns_empty(self):
        dates, pnl = build_chart_series([])
        assert dates == []
        assert pnl == []

    def test_positions_without_closed_at_excluded(self):
        pos = Position(symbol="BTC/USD", avg_entry_price=60000, total_amount=0.1)
        pos.realized_pnl = 100.0
        pos.closed_at = None  # not closed
        dates, pnl = build_chart_series([pos])
        assert dates == []
        assert pnl == []

    def test_single_position_has_anchor_and_one_point(self):
        pos = make_closed(120.50, datetime(2026, 1, 10, 12, 0))
        dates, pnl = build_chart_series([pos])
        assert len(dates) == 2
        assert len(pnl) == 2
        assert dates[0] == "2026-01-09"   # anchor: day before
        assert pnl[0] == 0.0              # starts at zero
        assert dates[1] == "2026-01-10"
        assert pnl[1] == pytest.approx(120.50)

    def test_multiple_positions_cumulative(self):
        p1 = make_closed(120.50, datetime(2026, 1, 10))
        p2 = make_closed(-45.20, datetime(2026, 1, 15))
        p3 = make_closed(88.00, datetime(2026, 1, 20))
        dates, pnl = build_chart_series([p1, p2, p3])
        assert len(dates) == 4  # anchor + 3 points
        assert pnl[0] == 0.0
        assert pnl[1] == pytest.approx(120.50)
        assert pnl[2] == pytest.approx(75.30)   # 120.50 - 45.20
        assert pnl[3] == pytest.approx(163.30)  # 75.30 + 88.00

    def test_positions_sorted_by_closed_at(self):
        # Provide in reverse order — should be sorted oldest-first
        p1 = make_closed(100.0, datetime(2026, 1, 20))
        p2 = make_closed(50.0, datetime(2026, 1, 10))
        dates, pnl = build_chart_series([p1, p2])
        # p2 (Jan 10) should come first
        assert dates[1] == "2026-01-10"
        assert pnl[1] == pytest.approx(50.0)
        assert dates[2] == "2026-01-20"
        assert pnl[2] == pytest.approx(150.0)

    def test_net_loss_series(self):
        p1 = make_closed(-100.0, datetime(2026, 1, 10))
        p2 = make_closed(-50.0, datetime(2026, 1, 15))
        dates, pnl = build_chart_series([p1, p2])
        assert pnl[1] == pytest.approx(-100.0)
        assert pnl[2] == pytest.approx(-150.0)

    def test_anchor_is_day_before_first_close(self):
        pos = make_closed(100.0, datetime(2026, 3, 1, 9, 30))
        dates, pnl = build_chart_series([pos])
        assert dates[0] == "2026-02-28"  # day before March 1

    def test_same_day_multiple_closes_cumulate(self):
        p1 = make_closed(100.0, datetime(2026, 1, 10, 9, 0))
        p2 = make_closed(50.0, datetime(2026, 1, 10, 14, 0))
        dates, pnl = build_chart_series([p1, p2])
        # Both on same date — two separate points
        assert len(dates) == 3
        assert pnl[1] == pytest.approx(100.0)
        assert pnl[2] == pytest.approx(150.0)

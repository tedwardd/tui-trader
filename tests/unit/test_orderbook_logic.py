"""
Unit tests for order book analysis logic in screens/orderbook.py

Tests the pure calculation functions:
- calculate_imbalance_ratio
- find_walls
- build_depth_bars
- annotate_levels
"""

import pytest
from screens.orderbook import (
    calculate_imbalance_ratio,
    find_walls,
    build_depth_bars,
    annotate_levels,
)


# ---------------------------------------------------------------------------
# calculate_imbalance_ratio
# ---------------------------------------------------------------------------

class TestCalculateImbalanceRatio:
    def test_equal_sides_returns_one(self):
        bids = [[60000, 1.0], [59900, 1.0]]
        asks = [[60100, 1.0], [60200, 1.0]]
        assert calculate_imbalance_ratio(bids, asks) == pytest.approx(1.0)

    def test_bids_dominating(self):
        bids = [[60000, 3.0]]
        asks = [[60100, 1.0]]
        assert calculate_imbalance_ratio(bids, asks) == pytest.approx(3.0)

    def test_asks_dominating(self):
        bids = [[60000, 1.0]]
        asks = [[60100, 4.0]]
        assert calculate_imbalance_ratio(bids, asks) == pytest.approx(0.25)

    def test_empty_bids_returns_zero(self):
        assert calculate_imbalance_ratio([], [[60100, 1.0]]) == 0.0

    def test_empty_asks_returns_zero(self):
        assert calculate_imbalance_ratio([[60000, 1.0]], []) == 0.0

    def test_both_empty_returns_zero(self):
        assert calculate_imbalance_ratio([], []) == 0.0

    def test_uses_all_levels(self):
        bids = [[60000, 1.0], [59900, 2.0], [59800, 1.0]]  # total = 4.0
        asks = [[60100, 2.0]]                                # total = 2.0
        assert calculate_imbalance_ratio(bids, asks) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# find_walls
# ---------------------------------------------------------------------------

class TestFindWalls:
    def test_no_walls_when_uniform(self):
        levels = [[60000 + i * 100, 1.0] for i in range(5)]
        walls = find_walls(levels, multiplier=2.0)
        assert walls == set()

    def test_detects_wall_above_threshold(self):
        # avg = (1+1+1+1+10)/5 = 2.8, threshold = 2.8 * 2 = 5.6
        # level at index 4 (amount=10) exceeds threshold
        levels = [[60000, 1.0], [60100, 1.0], [60200, 1.0], [60300, 1.0], [60400, 10.0]]
        walls = find_walls(levels, multiplier=2.0)
        assert 60400.0 in walls

    def test_non_wall_levels_not_flagged(self):
        levels = [[60000, 1.0], [60100, 1.0], [60200, 1.0], [60300, 1.0], [60400, 10.0]]
        walls = find_walls(levels, multiplier=2.0)
        assert 60000.0 not in walls
        assert 60100.0 not in walls

    def test_empty_levels_returns_empty(self):
        assert find_walls([], multiplier=2.0) == set()

    def test_single_level_no_wall(self):
        # Can't determine average with one level
        assert find_walls([[60000, 5.0]], multiplier=2.0) == set()

    def test_multiple_walls(self):
        levels = [
            [60000, 1.0], [60100, 1.0], [60200, 1.0],
            [60300, 20.0],  # wall
            [60400, 1.0],
            [60500, 25.0],  # wall
        ]
        walls = find_walls(levels, multiplier=2.0)
        assert 60300.0 in walls
        assert 60500.0 in walls
        assert 60000.0 not in walls


# ---------------------------------------------------------------------------
# build_depth_bars
# ---------------------------------------------------------------------------

class TestBuildDepthBars:
    def test_returns_one_bar_per_level(self):
        levels = [[60000, 1.0], [59900, 2.0], [59800, 3.0]]
        bars = build_depth_bars(levels, bar_width=10)
        assert len(bars) == 3

    def test_last_level_has_full_bar(self):
        levels = [[60000, 1.0], [59900, 2.0], [59800, 3.0]]
        bars = build_depth_bars(levels, bar_width=10)
        # Cumulative at last level = 6.0 = max, so bar should be full width
        assert bars[-1] == 10

    def test_first_level_has_smallest_bar(self):
        levels = [[60000, 1.0], [59900, 2.0], [59800, 3.0]]
        bars = build_depth_bars(levels, bar_width=10)
        # Cumulative: 1, 3, 6 → bars: 1/6*10≈1, 3/6*10=5, 6/6*10=10
        assert bars[0] < bars[1] < bars[2]

    def test_bars_proportional_to_cumulative(self):
        levels = [[60000, 2.0], [59900, 2.0]]  # cumulative: 2, 4
        bars = build_depth_bars(levels, bar_width=10)
        assert bars[0] == 5   # 2/4 * 10
        assert bars[1] == 10  # 4/4 * 10

    def test_empty_levels_returns_empty(self):
        assert build_depth_bars([], bar_width=10) == []

    def test_single_level_full_bar(self):
        bars = build_depth_bars([[60000, 1.0]], bar_width=10)
        assert bars == [10]

    def test_bar_width_respected(self):
        levels = [[60000, 1.0], [59900, 1.0]]
        bars = build_depth_bars(levels, bar_width=20)
        assert max(bars) == 20


# ---------------------------------------------------------------------------
# annotate_levels
# ---------------------------------------------------------------------------

class TestAnnotateLevels:
    def test_entry_price_annotated(self):
        levels = [[75200, 1.0], [75100, 1.0], [75000, 1.0], [74900, 1.0]]
        annotations = annotate_levels(levels, entry_price=75050.0, stop_price=74800.0)
        # entry is between 75000 and 75100 — should annotate between those rows
        assert "entry" in annotations

    def test_stop_price_annotated(self):
        levels = [[75200, 1.0], [75100, 1.0], [75000, 1.0], [74900, 1.0]]
        annotations = annotate_levels(levels, entry_price=75050.0, stop_price=74950.0)
        assert "stop" in annotations

    def test_no_annotation_when_price_outside_range(self):
        levels = [[75200, 1.0], [75100, 1.0]]
        # entry is below all levels — no annotation in visible range
        annotations = annotate_levels(levels, entry_price=70000.0, stop_price=69000.0)
        assert "entry" not in annotations
        assert "stop" not in annotations

    def test_no_annotation_when_no_position(self):
        levels = [[75200, 1.0], [75100, 1.0]]
        annotations = annotate_levels(levels, entry_price=None, stop_price=None)
        assert annotations == {}

    def test_annotation_index_is_correct(self):
        # Bids: descending prices. Entry at 75050 sits between index 1 (75100) and 2 (75000)
        levels = [[75300, 1.0], [75100, 1.0], [75000, 1.0], [74900, 1.0]]
        annotations = annotate_levels(levels, entry_price=75050.0, stop_price=None)
        # Should insert after index 1 (after 75100, before 75000)
        assert annotations["entry"] == 2

    def test_stop_annotation_index_is_correct(self):
        levels = [[75300, 1.0], [75100, 1.0], [75000, 1.0], [74900, 1.0]]
        annotations = annotate_levels(levels, entry_price=None, stop_price=74950.0)
        # Stop at 74950 sits between index 2 (75000) and 3 (74900)
        assert annotations["stop"] == 3

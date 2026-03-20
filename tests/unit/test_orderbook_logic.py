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
    group_levels,
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


# ---------------------------------------------------------------------------
# group_levels
# ---------------------------------------------------------------------------

class TestGroupLevels:
    def test_zero_tick_returns_original(self):
        levels = [[80100, 1.0], [80050, 0.5], [80000, 0.3]]
        assert group_levels(levels, 0) == levels

    def test_negative_tick_returns_original(self):
        levels = [[80100, 1.0], [80050, 0.5]]
        assert group_levels(levels, -10) == levels

    def test_empty_returns_empty(self):
        assert group_levels([], 100) == []

    def test_no_merging_when_levels_already_bucketed(self):
        # Each price is already a multiple of 100 — no merging
        levels = [[80200, 1.0], [80100, 2.0], [80000, 3.0]]
        result = group_levels(levels, 100)
        assert len(result) == 3

    def test_merges_levels_into_buckets(self):
        # 80099 and 80050 both floor to 80000 under tick_size=100
        levels = [[80099, 1.0], [80050, 0.5], [79999, 0.3]]
        result = group_levels(levels, 100)
        # 80099 → bucket 80000, 80050 → bucket 80000, 79999 → bucket 79900
        assert len(result) == 2
        buckets = {r[0]: r[1] for r in result}
        assert buckets[80000.0] == pytest.approx(1.5)
        assert buckets[79900.0] == pytest.approx(0.3)

    def test_amounts_summed_within_bucket(self):
        levels = [[80010, 1.0], [80005, 2.0], [80001, 3.0]]  # all floor to 80000
        result = group_levels(levels, 100)
        assert len(result) == 1
        assert result[0][1] == pytest.approx(6.0)

    def test_descending_order_preserved_for_bids(self):
        # Bids are descending
        levels = [[80150, 1.0], [80090, 0.5], [79950, 0.8], [79910, 0.2]]
        result = group_levels(levels, 100)
        prices = [r[0] for r in result]
        assert prices == sorted(prices, reverse=True)

    def test_ascending_order_preserved_for_asks(self):
        # Asks are ascending
        levels = [[80010, 0.2], [80060, 0.3], [80110, 0.5], [80190, 1.0]]
        result = group_levels(levels, 100)
        prices = [r[0] for r in result]
        assert prices == sorted(prices)

    def test_single_level_returns_bucketed_price(self):
        result = group_levels([[80075, 2.0]], 100)
        assert len(result) == 1
        assert result[0][0] == pytest.approx(80000.0)
        assert result[0][1] == pytest.approx(2.0)

    def test_reduces_number_of_rows(self):
        # 10 levels with tick_size=50 should produce fewer rows
        levels = [[80000 - i * 10, 1.0] for i in range(10)]  # 80000 to 79910 in $10 steps
        result = group_levels(levels, 50)
        assert len(result) < len(levels)

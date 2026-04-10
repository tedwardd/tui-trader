"""
Unit tests for app/config.py — internal constants and config defaults.
"""

import pytest
from app.config import (
    DUST_THRESHOLD,
    ATR_REFRESH_SECONDS,
    ORDERBOOK_FETCH_DEPTH,
    DEFAULT_STOP_LOSS_PCT,
    WS_RECONNECT_BACKOFF,
    HISTORY_REFRESH_SECONDS,
    ORDER_BOOK_DEPTH,
)


class TestInternalConstants:
    def test_dust_threshold_is_positive_small_number(self):
        assert DUST_THRESHOLD > 0
        assert DUST_THRESHOLD < 1e-4

    def test_dust_threshold_is_exactly_one_micro(self):
        assert DUST_THRESHOLD == 1e-6

    def test_atr_refresh_seconds_is_30_minutes(self):
        assert ATR_REFRESH_SECONDS == 30 * 60
        assert ATR_REFRESH_SECONDS == 1800

    def test_orderbook_fetch_depth_is_500(self):
        assert ORDERBOOK_FETCH_DEPTH == 500


class TestConfigDefaults:
    def test_default_stop_loss_pct(self):
        assert DEFAULT_STOP_LOSS_PCT == 2.0
        assert isinstance(DEFAULT_STOP_LOSS_PCT, float)

    def test_ws_reconnect_backoff(self):
        assert WS_RECONNECT_BACKOFF == 5.0
        assert isinstance(WS_RECONNECT_BACKOFF, float)

    def test_history_refresh_seconds(self):
        assert HISTORY_REFRESH_SECONDS == 60
        assert isinstance(HISTORY_REFRESH_SECONDS, int)

    def test_order_book_depth(self):
        assert ORDER_BOOK_DEPTH in {10, 25, 100, 500, 1000}
        assert isinstance(ORDER_BOOK_DEPTH, int)

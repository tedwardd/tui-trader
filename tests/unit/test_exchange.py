"""
Unit tests for app/exchange.py — singleton behavior and basic functions.
"""

import pytest
from app import exchange as exchange_module


class TestExchangeSingleton:
    def test_get_exchange_returns_same_instance(self):
        exchange1 = exchange_module.get_exchange()
        exchange2 = exchange_module.get_exchange()
        assert exchange1 is exchange2

    def test_get_exchange_returns_ccxt_exchange(self):
        ex = exchange_module.get_exchange()
        assert ex is not None
        # Should have ccxt-style methods
        assert hasattr(ex, "fetch_balance")
        assert hasattr(ex, "fetch_ohlcv")
        assert hasattr(ex, "create_order")


class TestCanonicalFee:
    def test_canonical_fee_returns_fee_when_nonzero(self):
        # When fee > 0, it returns the fee as-is
        fee = exchange_module.canonical_fee(
            fee=1.0,
            amount=100.0,
            price=50000.0,
            order_type="market",
        )
        assert fee == 1.0

    def test_canonical_fee_zero_fee_falls_back_to_estimate(self):
        # When fee is 0, it falls back to estimate_fee
        fee = exchange_module.canonical_fee(
            fee=0.0,
            amount=100.0,
            price=50000.0,
            order_type="market",
        )
        # estimate_fee for market is amount * price * 0.004
        expected = 100.0 * 50000.0 * 0.004
        assert fee == expected

    def test_canonical_fee_types(self):
        # Should work with int inputs
        fee = exchange_module.canonical_fee(
            fee=10,
            amount=100,
            price=50000,
            order_type="limit",
        )
        # When fee > 0, it returns the fee directly
        assert fee == 10

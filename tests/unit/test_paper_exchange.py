"""
Unit tests for app/paper_exchange.py

All functions are pure (no I/O) — they just build and return a dict.
"""

import pytest
from app import paper_exchange


SYMBOL = "BTC/USD"
AMOUNT = 0.1
PRICE = 80000.0


# ---------------------------------------------------------------------------
# Shared shape assertions
# ---------------------------------------------------------------------------

def assert_order_shape(order: dict, side: str, symbol: str, amount: float, price: float, order_type: str) -> None:
    """Assert the returned dict matches the ccxt order shape trade.py expects."""
    assert order["id"].startswith("PAPER-")
    assert order["status"] == "closed"
    assert order["symbol"] == symbol
    assert order["side"] == side
    assert order["type"] == order_type
    assert order["filled"] == pytest.approx(amount)
    assert order["amount"] == pytest.approx(amount)
    assert order["average"] == pytest.approx(price)
    assert order["price"] == pytest.approx(price)
    assert isinstance(order["fee"], dict)
    assert "cost" in order["fee"]
    assert order["fee"]["currency"] == "USD"
    assert "datetime" in order


# ---------------------------------------------------------------------------
# Market buy
# ---------------------------------------------------------------------------

class TestPlaceMarketBuy:
    def test_returns_correct_shape(self):
        order = paper_exchange.place_market_buy(SYMBOL, AMOUNT, PRICE)
        assert_order_shape(order, "buy", SYMBOL, AMOUNT, PRICE, "market")

    def test_fee_is_taker_rate(self):
        order = paper_exchange.place_market_buy(SYMBOL, AMOUNT, PRICE)
        expected_fee = AMOUNT * PRICE * 0.0040
        assert order["fee"]["cost"] == pytest.approx(expected_fee)

    def test_id_is_unique(self):
        a = paper_exchange.place_market_buy(SYMBOL, AMOUNT, PRICE)
        b = paper_exchange.place_market_buy(SYMBOL, AMOUNT, PRICE)
        assert a["id"] != b["id"]

    def test_fill_price_is_current_price(self):
        live = 75123.45
        order = paper_exchange.place_market_buy(SYMBOL, 0.01, live)
        assert order["average"] == pytest.approx(live)


# ---------------------------------------------------------------------------
# Market sell
# ---------------------------------------------------------------------------

class TestPlaceMarketSell:
    def test_returns_correct_shape(self):
        order = paper_exchange.place_market_sell(SYMBOL, AMOUNT, PRICE)
        assert_order_shape(order, "sell", SYMBOL, AMOUNT, PRICE, "market")

    def test_fee_is_taker_rate(self):
        order = paper_exchange.place_market_sell(SYMBOL, AMOUNT, PRICE)
        expected_fee = AMOUNT * PRICE * 0.0040
        assert order["fee"]["cost"] == pytest.approx(expected_fee)

    def test_fill_price_is_current_price(self):
        live = 79999.99
        order = paper_exchange.place_market_sell(SYMBOL, 0.05, live)
        assert order["average"] == pytest.approx(live)


# ---------------------------------------------------------------------------
# Limit buy
# ---------------------------------------------------------------------------

class TestPlaceLimitBuy:
    def test_returns_correct_shape(self):
        order = paper_exchange.place_limit_buy(SYMBOL, AMOUNT, PRICE)
        assert_order_shape(order, "buy", SYMBOL, AMOUNT, PRICE, "limit")

    def test_fee_is_maker_rate(self):
        order = paper_exchange.place_limit_buy(SYMBOL, AMOUNT, PRICE)
        expected_fee = AMOUNT * PRICE * 0.0016
        assert order["fee"]["cost"] == pytest.approx(expected_fee)

    def test_maker_fee_lower_than_taker(self):
        limit = paper_exchange.place_limit_buy(SYMBOL, AMOUNT, PRICE)
        market = paper_exchange.place_market_buy(SYMBOL, AMOUNT, PRICE)
        assert limit["fee"]["cost"] < market["fee"]["cost"]

    def test_fill_price_is_limit_price(self):
        limit_price = 78500.0
        order = paper_exchange.place_limit_buy(SYMBOL, 0.02, limit_price)
        assert order["average"] == pytest.approx(limit_price)


# ---------------------------------------------------------------------------
# Limit sell
# ---------------------------------------------------------------------------

class TestPlaceLimitSell:
    def test_returns_correct_shape(self):
        order = paper_exchange.place_limit_sell(SYMBOL, AMOUNT, PRICE)
        assert_order_shape(order, "sell", SYMBOL, AMOUNT, PRICE, "limit")

    def test_fee_is_maker_rate(self):
        order = paper_exchange.place_limit_sell(SYMBOL, AMOUNT, PRICE)
        expected_fee = AMOUNT * PRICE * 0.0016
        assert order["fee"]["cost"] == pytest.approx(expected_fee)

    def test_fill_price_is_limit_price(self):
        limit_price = 82000.0
        order = paper_exchange.place_limit_sell(SYMBOL, 0.03, limit_price)
        assert order["average"] == pytest.approx(limit_price)


# ---------------------------------------------------------------------------
# Fee extraction compatibility — trade.py reads fee via order.get("fee", {})
# ---------------------------------------------------------------------------

class TestFeeExtraction:
    """Verify the fee dict is readable the same way trade.py reads real ccxt orders."""

    def test_fee_cost_readable(self):
        order = paper_exchange.place_market_buy(SYMBOL, 0.1, 80000.0)
        fee_info = order.get("fee") or {}
        fee = float(fee_info.get("cost") or 0)
        assert fee == pytest.approx(0.1 * 80000.0 * 0.0040)

    def test_fee_currency_readable(self):
        order = paper_exchange.place_market_buy(SYMBOL, 0.1, 80000.0)
        fee_info = order.get("fee") or {}
        assert fee_info.get("currency") == "USD"

    def test_fill_price_readable_via_average(self):
        order = paper_exchange.place_market_buy(SYMBOL, 0.1, 80000.0)
        fill_price = float(order.get("average") or order.get("price") or 0)
        assert fill_price == pytest.approx(80000.0)

    def test_filled_amount_readable(self):
        order = paper_exchange.place_market_buy(SYMBOL, 0.1, 80000.0)
        filled = float(order.get("filled") or order.get("amount") or 0)
        assert filled == pytest.approx(0.1)

    def test_order_id_readable(self):
        order = paper_exchange.place_market_buy(SYMBOL, 0.1, 80000.0)
        order_id = str(order.get("id", ""))
        assert order_id.startswith("PAPER-")

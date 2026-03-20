"""
Paper trading exchange — simulates order fills without touching the real exchange.

Returns the same dict shape as ccxt order objects so the caller (trade.py)
doesn't need to special-case paper vs live paths for fill extraction.

Fees are approximated using Kraken's retail fee schedule:
  Market orders (taker): 0.40%
  Limit orders  (maker): 0.16%
"""

from datetime import datetime, timezone
from uuid import uuid4


_TAKER_FEE_PCT = 0.0040  # market orders
_MAKER_FEE_PCT = 0.0016  # limit orders


def _make_order(
    side: str, symbol: str, amount: float, fill_price: float, order_type: str
) -> dict:
    fee_pct = _TAKER_FEE_PCT if order_type == "market" else _MAKER_FEE_PCT
    fee_cost = amount * fill_price * fee_pct
    return {
        "id": f"PAPER-{uuid4().hex[:12].upper()}",
        "datetime": datetime.now(timezone.utc).isoformat(),
        "status": "closed",
        "symbol": symbol,
        "type": order_type,
        "side": side,
        "amount": amount,
        "filled": amount,
        "average": fill_price,
        "price": fill_price,
        "fee": {
            "cost": fee_cost,
            "currency": "USD",
        },
    }


def place_market_buy(symbol: str, amount: float, current_price: float) -> dict:
    return _make_order("buy", symbol, amount, current_price, "market")


def place_market_sell(symbol: str, amount: float, current_price: float) -> dict:
    return _make_order("sell", symbol, amount, current_price, "market")


def place_limit_buy(symbol: str, amount: float, price: float) -> dict:
    return _make_order("buy", symbol, amount, price, "limit")


def place_limit_sell(symbol: str, amount: float, price: float) -> dict:
    return _make_order("sell", symbol, amount, price, "limit")

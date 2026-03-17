"""
Kraken REST API wrapper using ccxt.

This module handles all REST operations:
- Placing and cancelling orders
- Fetching trade/order history on startup
- One-time balance fetch on startup

Live price data comes from streams.py (WebSocket), NOT from here.
"""

import ccxt
from typing import Optional

from app.config import KRAKEN_API_KEY, KRAKEN_API_SECRET


def _make_exchange() -> ccxt.kraken:
    return ccxt.kraken(
        {
            "apiKey": KRAKEN_API_KEY,
            "secret": KRAKEN_API_SECRET,
            "enableRateLimit": True,
        }
    )


# Module-level singleton — one REST client shared across the app
_exchange: Optional[ccxt.kraken] = None


def get_exchange() -> ccxt.kraken:
    global _exchange
    if _exchange is None:
        _exchange = _make_exchange()
    return _exchange


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


def fetch_balance() -> dict:
    """
    Fetch current account balance.
    Returns a dict keyed by currency: {"BTC": {"free": 0.1, "used": 0.0, "total": 0.1}, ...}
    """
    exchange = get_exchange()
    balance = exchange.fetch_balance()
    # Filter out zero balances for cleaner display
    return {
        currency: amounts
        for currency, amounts in balance.items()
        if isinstance(amounts, dict) and amounts.get("total", 0) > 0
    }


def fetch_total_usd_value(balance: Optional[dict] = None) -> float:
    """
    Estimate total portfolio value in USD.
    Uses the balance dict if provided, otherwise fetches fresh.
    Note: This is an approximation — for accurate value use live prices from streams.
    """
    if balance is None:
        balance = fetch_balance()
    usd = balance.get("USD", {}).get("total", 0.0)
    return float(usd)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def place_market_buy(symbol: str, amount: float) -> dict:
    """
    Place a market buy order.

    Args:
        symbol: Trading pair, e.g. "BTC/USD"
        amount: Amount in base currency (e.g. 0.01 BTC)

    Returns:
        ccxt order dict with id, status, filled, price, fee, etc.
    """
    exchange = get_exchange()
    return exchange.create_market_buy_order(symbol, amount)


def place_market_sell(symbol: str, amount: float) -> dict:
    """Place a market sell order."""
    exchange = get_exchange()
    return exchange.create_market_sell_order(symbol, amount)


def place_limit_buy(symbol: str, amount: float, price: float) -> dict:
    """Place a limit buy order."""
    exchange = get_exchange()
    return exchange.create_limit_buy_order(symbol, amount, price)


def place_limit_sell(symbol: str, amount: float, price: float) -> dict:
    """Place a limit sell order."""
    exchange = get_exchange()
    return exchange.create_limit_sell_order(symbol, amount, price)


def cancel_order(order_id: str, symbol: str) -> dict:
    """Cancel an open order by ID."""
    exchange = get_exchange()
    return exchange.cancel_order(order_id, symbol)


def fetch_open_orders(symbol: Optional[str] = None) -> list[dict]:
    """Fetch all open orders, optionally filtered by symbol."""
    exchange = get_exchange()
    return exchange.fetch_open_orders(symbol)


# ---------------------------------------------------------------------------
# Trade history (used on startup to sync local DB)
# ---------------------------------------------------------------------------


def fetch_my_trades(symbol: Optional[str] = None, limit: int = 100) -> list[dict]:
    """
    Fetch recent trade fills from Kraken.
    Used on startup to reconcile local DB with exchange history.
    """
    exchange = get_exchange()
    return exchange.fetch_my_trades(symbol, limit=limit)


def fetch_closed_orders(symbol: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Fetch closed/filled orders from Kraken."""
    exchange = get_exchange()
    return exchange.fetch_closed_orders(symbol, limit=limit)


# ---------------------------------------------------------------------------
# Market info
# ---------------------------------------------------------------------------


def fetch_markets() -> list[dict]:
    """Fetch all available markets on Kraken."""
    exchange = get_exchange()
    return exchange.fetch_markets()


def get_tradeable_symbols() -> list[str]:
    """Return a sorted list of spot trading pair symbols, e.g. ['BTC/USD', 'ETH/USD', ...]"""
    markets = fetch_markets()
    return sorted(
        m["symbol"]
        for m in markets
        if m.get("spot") and m.get("active") and "/" in m.get("symbol", "")
    )

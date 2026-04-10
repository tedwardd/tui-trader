"""
WebSocket stream workers using ccxt Pro.

Three persistent workers share a single ccxtpro.kraken exchange instance:
  1. ticker_worker   — public: live price, bid/ask, 24h change
  2. orderbook_worker — public: live order book depth
  3. private_worker  — private: order fills, balance changes, trade log

Workers are async coroutines run via run_worker(coroutine). Textual runs
these as asyncio Tasks on the same event loop as the app — NOT in a thread
pool. This means UI methods can be called directly; call_from_thread() must
NOT be used (it raises RuntimeError when called from the event loop).
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

import ccxt.pro as ccxtpro

from app.config import (
    KRAKEN_API_KEY,
    KRAKEN_API_SECRET,
    ORDER_BOOK_DEPTH,
    ORDERBOOK_FETCH_DEPTH,
    WS_RECONNECT_BACKOFF,
)

if TYPE_CHECKING:
    from main import TradeApp

log = logging.getLogger(__name__)


class StreamManager:
    """
    Manages all WebSocket connections for the trading app.

    One ccxtpro.kraken instance is shared across all workers to minimise
    connections (ccxt Pro multiplexes subscriptions over one WS per endpoint).
    """

    def __init__(self) -> None:
        self._exchange: Optional[ccxtpro.kraken] = None

    def _get_exchange(self) -> ccxtpro.kraken:
        if self._exchange is None:
            self._exchange = ccxtpro.kraken(
                {
                    "apiKey": KRAKEN_API_KEY,
                    "secret": KRAKEN_API_SECRET,
                    "enableRateLimit": True,
                }
            )
        return self._exchange

    async def close(self) -> None:
        """Clean up WebSocket connections. Call from app.on_unmount()."""
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    # -----------------------------------------------------------------------
    # Public: Ticker worker
    # -----------------------------------------------------------------------

    async def ticker_worker(self, app: "TradeApp", symbol: str) -> None:
        """
        Subscribe to live ticker updates for a symbol.
        Fires on every trade event — updates price, bid/ask, 24h change.

        Calls app.on_ticker_update(ticker) on each update.
        """
        exchange = self._get_exchange()
        log.info("ticker_worker: starting for %s", symbol)

        while True:
            try:
                ticker = await exchange.watch_ticker(symbol)
                app.on_ticker_update(ticker)
            except asyncio.CancelledError:
                raise
            except ccxtpro.AuthenticationError as e:
                log.error("ticker_worker: auth error — %s", e)
                break  # fatal: bad API keys
            except ccxtpro.RateLimitExceeded:
                log.warning("ticker_worker: rate limit exceeded, backing off")
                await asyncio.sleep(30)
            except ccxtpro.NetworkError as e:
                log.warning("ticker_worker: network error (%s), reconnecting in %ss", e, WS_RECONNECT_BACKOFF)
                await asyncio.sleep(WS_RECONNECT_BACKOFF)
            except Exception as e:
                log.error("ticker_worker: unexpected error — %s", e)
                await asyncio.sleep(WS_RECONNECT_BACKOFF)

    # -----------------------------------------------------------------------
    # Public: Order book worker
    # -----------------------------------------------------------------------

    async def orderbook_worker(self, app: "TradeApp", symbol: str) -> None:
        """
        Subscribe to live order book updates for a symbol.
        ccxt Pro handles snapshot + incremental delta merging automatically.

        Calls app.on_orderbook_update(orderbook) on each update.
        """
        exchange = self._get_exchange()
        log.info("orderbook_worker: starting for %s (fetch depth=%d, display depth=%d)", symbol, ORDERBOOK_FETCH_DEPTH, ORDER_BOOK_DEPTH)

        while True:
            try:
                ob = await exchange.watch_order_book(symbol, ORDERBOOK_FETCH_DEPTH)
                app.on_orderbook_update(ob)
            except asyncio.CancelledError:
                raise
            except ccxtpro.AuthenticationError as e:
                log.error("orderbook_worker: auth error — %s", e)
                break
            except ccxtpro.RateLimitExceeded:
                await asyncio.sleep(30)
            except ccxtpro.NetworkError as e:
                log.warning("orderbook_worker: network error (%s), reconnecting in %ss", e, WS_RECONNECT_BACKOFF)
                await asyncio.sleep(WS_RECONNECT_BACKOFF)
            except Exception as e:
                log.error("orderbook_worker: unexpected error — %s", e)
                await asyncio.sleep(WS_RECONNECT_BACKOFF)

    # -----------------------------------------------------------------------
    # Private: Orders + balance + fills worker
    # -----------------------------------------------------------------------

    async def private_worker(self, app: "TradeApp") -> None:
        """
        Subscribe to the private executions channel.
        ccxt Pro demultiplexes watch_orders, watch_balance, and watch_my_trades
        from the same underlying 'executions' WebSocket channel.

        Runs three concurrent tasks sharing the same exchange instance.
        """
        log.info("private_worker: starting")
        exchange = self._get_exchange()

        async def _watch_orders() -> None:
            while True:
                try:
                    orders = await exchange.watch_orders()
                    app.on_orders_update(orders)
                except asyncio.CancelledError:
                    raise
                except ccxtpro.AuthenticationError as e:
                    log.error("watch_orders: auth error — %s", e)
                    break
                except ccxtpro.RateLimitExceeded:
                    await asyncio.sleep(30)
                except ccxtpro.NetworkError as e:
                    log.warning("watch_orders: network error (%s), reconnecting", e)
                    await asyncio.sleep(WS_RECONNECT_BACKOFF)
                except Exception as e:
                    log.error("watch_orders: unexpected error — %s", e)
                    await asyncio.sleep(WS_RECONNECT_BACKOFF)

        async def _watch_balance() -> None:
            while True:
                try:
                    balance = await exchange.watch_balance()
                    app.on_balance_update(balance)
                except asyncio.CancelledError:
                    raise
                except ccxtpro.AuthenticationError as e:
                    log.error("watch_balance: auth error — %s", e)
                    break
                except ccxtpro.RateLimitExceeded:
                    await asyncio.sleep(30)
                except ccxtpro.NetworkError as e:
                    log.warning("watch_balance: network error (%s), reconnecting", e)
                    await asyncio.sleep(WS_RECONNECT_BACKOFF)
                except Exception as e:
                    log.error("watch_balance: unexpected error — %s", e)
                    await asyncio.sleep(WS_RECONNECT_BACKOFF)

        async def _watch_my_trades() -> None:
            while True:
                try:
                    trades = await exchange.watch_my_trades()
                    app.on_my_trades_update(trades)
                except asyncio.CancelledError:
                    raise
                except ccxtpro.AuthenticationError as e:
                    log.error("watch_my_trades: auth error — %s", e)
                    break
                except ccxtpro.RateLimitExceeded:
                    await asyncio.sleep(30)
                except ccxtpro.NetworkError as e:
                    log.warning("watch_my_trades: network error (%s), reconnecting", e)
                    await asyncio.sleep(WS_RECONNECT_BACKOFF)
                except Exception as e:
                    log.error("watch_my_trades: unexpected error — %s", e)
                    await asyncio.sleep(WS_RECONNECT_BACKOFF)

        await asyncio.gather(
            _watch_orders(),
            _watch_balance(),
            _watch_my_trades(),
        )


# Module-level singleton
stream_manager = StreamManager()

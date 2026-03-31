"""
Trade recording logic — create or update local positions and trade records.

Extracted from TradeScreen so it can be reused from both the order submission
path (market orders, immediately-filled limits) and the WebSocket fill path
(limit orders that fill asynchronously after placement).
"""

from app import database as db
from app.models import Position, Trade

_ATR_MULTIPLIER = 1.5
_DUST_THRESHOLD = 1e-6


def record_buy(
    symbol: str,
    amount: float,
    price: float,
    fee: float,
    fee_currency: str,
    order_id: str,
    order_type: str,
    atr: float | None = None,
) -> None:
    """Create or update a local Position and record the Trade for a buy fill."""
    existing = db.get_position_by_symbol(symbol)

    if existing:
        existing.add_to_position(amount, price, fee)
        # Stop is intentionally not updated when adding to a position —
        # keep the original stop fixed regardless of the new average entry.
        position = db.update_position(existing)
    else:
        stop_price = (price - _ATR_MULTIPLIER * atr) if atr is not None else None
        position = db.save_position(
            Position(
                symbol=symbol,
                avg_entry_price=price,
                total_amount=amount,
                total_fees_paid=fee,
                stop_loss_price=stop_price,
                stop_source="atr" if atr is not None else None,
            )
        )

    db.save_trade(
        Trade(
            position_id=position.id,
            symbol=symbol,
            side="buy",
            amount=amount,
            price=price,
            fee=fee,
            fee_currency=fee_currency,
            kraken_order_id=order_id,
            order_type=order_type,
        )
    )


def record_sell(
    symbol: str,
    amount: float,
    price: float,
    fee: float,
    fee_currency: str,
    order_id: str,
    order_type: str,
) -> None:
    """Reduce or close a local Position and record the Trade for a sell fill."""
    existing = db.get_position_by_symbol(symbol)
    if not existing:
        return

    if abs(existing.total_amount - amount) <= _DUST_THRESHOLD:
        amount = existing.total_amount

    existing.reduce_position(amount, price, fee)
    db.update_position(existing)

    db.save_trade(
        Trade(
            position_id=existing.id,
            symbol=symbol,
            side="sell",
            amount=amount,
            price=price,
            fee=fee,
            fee_currency=fee_currency,
            kraken_order_id=order_id,
            order_type=order_type,
        )
    )

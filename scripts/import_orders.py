"""
One-off script to import specific Kraken orders into the local tui-trader database.

Usage:
    python scripts/import_orders.py O4SYLI-PFOEB-4MBYTV ONPNZ5-6QUFD-22WNNX OP24LZ-GHXDW-Q5NWTO

Fetches each order's fill details from Kraken, groups fills on the same symbol
into a single Position with a weighted average entry price, then writes the
Position and individual Trade records to trades.db.

Safe to re-run — skips any order IDs already present in the database.
"""

import sys
import os

# Allow running from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from app.config import KRAKEN_API_KEY, KRAKEN_API_SECRET
from app import database as db
from app.models import Position, Trade
import ccxt


def fetch_order_details(exchange: ccxt.kraken, order_id: str) -> dict:
    """Fetch a single closed/filled order by ID from Kraken."""
    # fetch_order works for both open and closed orders on Kraken
    # We pass None as symbol since we don't know it yet
    order = exchange.fetch_order(order_id)
    return order


def main(order_ids: list[str]) -> None:
    db.init_db()

    exchange = ccxt.kraken({
        "apiKey": KRAKEN_API_KEY,
        "secret": KRAKEN_API_SECRET,
        "enableRateLimit": True,
    })

    print(f"Fetching {len(order_ids)} orders from Kraken...\n")

    # Collect fill details, skipping duplicates
    fills: list[dict] = []
    for order_id in order_ids:
        if db.trade_exists(order_id):
            print(f"  SKIP  {order_id} — already in database")
            continue

        try:
            order = fetch_order_details(exchange, order_id)
        except Exception as e:
            print(f"  ERROR {order_id} — {e}")
            sys.exit(1)

        symbol   = order["symbol"]
        side     = order["side"]
        amount   = float(order.get("filled") or order.get("amount") or 0)
        price    = float(order.get("average") or order.get("price") or 0)
        fee      = float((order.get("fee") or {}).get("cost") or 0)
        fee_ccy  = str((order.get("fee") or {}).get("currency") or "USD")
        ts_ms    = order.get("timestamp")
        ts       = datetime.utcfromtimestamp(ts_ms / 1000) if ts_ms else datetime.utcnow()
        otype    = order.get("type", "market")

        if amount == 0 or price == 0:
            print(f"  WARN  {order_id} — zero amount or price, skipping")
            continue

        fills.append({
            "order_id": order_id,
            "symbol":   symbol,
            "side":     side,
            "amount":   amount,
            "price":    price,
            "fee":      fee,
            "fee_ccy":  fee_ccy,
            "timestamp": ts,
            "order_type": otype,
        })

        print(f"  OK    {order_id}  {side.upper()} {amount} {symbol} @ ${price:,.4f}  fee=${fee:.4f} {fee_ccy}")

    if not fills:
        print("\nNothing to import.")
        return

    # Group by symbol so multiple buys on the same pair become one Position
    by_symbol: dict[str, list[dict]] = {}
    for f in fills:
        by_symbol.setdefault(f["symbol"], []).append(f)

    print(f"\nImporting into database...\n")

    for symbol, symbol_fills in by_symbol.items():
        buys  = [f for f in symbol_fills if f["side"] == "buy"]
        sells = [f for f in symbol_fills if f["side"] == "sell"]

        # --- Handle buys: create/update Position ---
        if buys:
            existing = db.get_position_by_symbol(symbol)

            if existing:
                # Add to the existing open position
                for f in buys:
                    existing.add_to_position(f["amount"], f["price"], f["fee"])
                position = db.update_position(existing)
                print(f"  Updated existing position for {symbol}")
            else:
                # Calculate weighted avg entry across all new buys
                total_amount = sum(f["amount"] for f in buys)
                total_cost   = sum(f["amount"] * f["price"] for f in buys)
                total_fees   = sum(f["fee"] for f in buys)
                avg_entry    = total_cost / total_amount
                opened_at    = min(f["timestamp"] for f in buys)

                position = db.save_position(Position(
                    symbol=symbol,
                    avg_entry_price=avg_entry,
                    total_amount=total_amount,
                    total_fees_paid=total_fees,
                    opened_at=opened_at,
                ))
                print(f"  Created new position for {symbol}:")
                print(f"    size={total_amount}  avg_entry=${avg_entry:,.4f}  fees=${total_fees:.4f}")

            # Write individual Trade records
            for f in buys:
                db.save_trade(Trade(
                    position_id=position.id,
                    symbol=symbol,
                    side="buy",
                    amount=f["amount"],
                    price=f["price"],
                    fee=f["fee"],
                    fee_currency=f["fee_ccy"],
                    timestamp=f["timestamp"],
                    kraken_order_id=f["order_id"],
                    order_type=f["order_type"],
                ))

        # --- Handle sells (unusual for an import of open positions, but handle gracefully) ---
        for f in sells:
            existing = db.get_position_by_symbol(symbol)
            if existing:
                existing.reduce_position(f["amount"], f["price"], f["fee"])
                db.update_position(existing)
            db.save_trade(Trade(
                position_id=existing.id if existing else None,
                symbol=symbol,
                side="sell",
                amount=f["amount"],
                price=f["price"],
                fee=f["fee"],
                fee_currency=f["fee_ccy"],
                timestamp=f["timestamp"],
                kraken_order_id=f["order_id"],
                order_type=f["order_type"],
            ))

    print("\nDone. Run `python main.py` to view your positions.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_orders.py <ORDER_ID> [ORDER_ID ...]")
        sys.exit(1)
    main(sys.argv[1:])

"""
Database setup and CRUD operations using SQLModel + SQLite.

The database file (trades.db) is created automatically on first run.
All position and trade history is stored locally for fast access and
to support features that Kraken's API doesn't provide (e.g. weighted
average entry tracking, cumulative P&L over time).
"""

from typing import Optional
from sqlmodel import SQLModel, Session, create_engine, select

from app.models import Position, Trade, PriceAlert
from app.config import DATABASE_PATH

DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
engine = create_engine(DATABASE_URL, echo=False)


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    SQLModel.metadata.create_all(engine)
    _migrate_add_stop_loss_price()


def _migrate_add_stop_loss_price() -> None:
    """
    Add stop_loss_price column to position table if it doesn't exist.
    Handles databases created before this column was introduced.
    """
    import sqlite3
    with sqlite3.connect(DATABASE_PATH) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(position)")]
        if "stop_loss_price" not in cols:
            conn.execute("ALTER TABLE position ADD COLUMN stop_loss_price REAL")


# ---------------------------------------------------------------------------
# Position CRUD
# ---------------------------------------------------------------------------


def get_open_positions() -> list[Position]:
    with Session(engine) as session:
        return session.exec(
            select(Position).where(Position.status == "open")
        ).all()


def get_position_by_symbol(symbol: str) -> Optional[Position]:
    """Return the most recent open position for a symbol, if any."""
    with Session(engine) as session:
        return session.exec(
            select(Position)
            .where(Position.symbol == symbol, Position.status == "open")
            .order_by(Position.opened_at.desc())
        ).first()


def get_closed_positions(limit: int = 100) -> list[Position]:
    with Session(engine) as session:
        return session.exec(
            select(Position)
            .where(Position.status == "closed")
            .order_by(Position.closed_at.desc())
            .limit(limit)
        ).all()


def save_position(position: Position) -> Position:
    with Session(engine) as session:
        session.add(position)
        session.commit()
        session.refresh(position)
        return position


def update_position(position: Position) -> Position:
    """Merge changes to an existing position back to the database."""
    with Session(engine) as session:
        db_position = session.get(Position, position.id)
        if db_position is None:
            raise ValueError(f"Position {position.id} not found")
        db_position.avg_entry_price = position.avg_entry_price
        db_position.total_amount = position.total_amount
        db_position.realized_pnl = position.realized_pnl
        db_position.total_fees_paid = position.total_fees_paid
        db_position.status = position.status
        db_position.closed_at = position.closed_at
        db_position.stop_loss_price = position.stop_loss_price
        session.add(db_position)
        session.commit()
        session.refresh(db_position)
        return db_position


def set_stop_loss(position_id: int, stop_price: Optional[float]) -> None:
    """Set or clear the manual stop-loss price for a position."""
    with Session(engine) as session:
        pos = session.get(Position, position_id)
        if pos:
            pos.stop_loss_price = stop_price
            session.add(pos)
            session.commit()


# ---------------------------------------------------------------------------
# Trade CRUD
# ---------------------------------------------------------------------------


def save_trade(trade: Trade) -> Trade:
    with Session(engine) as session:
        session.add(trade)
        session.commit()
        session.refresh(trade)
        return trade


def get_trades_for_position(position_id: int) -> list[Trade]:
    with Session(engine) as session:
        return session.exec(
            select(Trade)
            .where(Trade.position_id == position_id)
            .order_by(Trade.timestamp.asc())
        ).all()


def get_recent_trades(limit: int = 50) -> list[Trade]:
    with Session(engine) as session:
        return session.exec(
            select(Trade).order_by(Trade.timestamp.desc()).limit(limit)
        ).all()


def trade_exists(kraken_trade_id: str) -> bool:
    """Prevent duplicate inserts when replaying history on startup."""
    with Session(engine) as session:
        result = session.exec(
            select(Trade).where(Trade.kraken_trade_id == kraken_trade_id)
        ).first()
        return result is not None


# ---------------------------------------------------------------------------
# Price Alert CRUD
# ---------------------------------------------------------------------------


def get_active_alerts() -> list[PriceAlert]:
    with Session(engine) as session:
        return session.exec(
            select(PriceAlert).where(PriceAlert.triggered == False)  # noqa: E712
        ).all()


def get_all_alerts() -> list[PriceAlert]:
    with Session(engine) as session:
        return session.exec(
            select(PriceAlert).order_by(PriceAlert.created_at.desc())
        ).all()


def save_alert(alert: PriceAlert) -> PriceAlert:
    with Session(engine) as session:
        session.add(alert)
        session.commit()
        session.refresh(alert)
        return alert


def mark_alert_triggered(alert_id: int) -> None:
    from datetime import datetime

    with Session(engine) as session:
        alert = session.get(PriceAlert, alert_id)
        if alert:
            alert.triggered = True
            alert.triggered_at = datetime.utcnow()
            session.add(alert)
            session.commit()


def delete_alert(alert_id: int) -> None:
    with Session(engine) as session:
        alert = session.get(PriceAlert, alert_id)
        if alert:
            session.delete(alert)
            session.commit()

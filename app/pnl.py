"""
Pure P&L calculation logic — no I/O, no database, no UI dependencies.

Keeping this module side-effect-free makes it easy to:
- Unit test in isolation
- Reuse in future features (DCA calculator, CSV export, fee tracking)
"""

from dataclasses import dataclass
from typing import Optional

from app.models import Position


@dataclass
class PositionSnapshot:
    """
    A point-in-time view of a position's P&L at a given market price.
    Used to populate UI widgets without coupling them to the database model.
    """

    symbol: str
    avg_entry_price: float
    total_amount: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    gross_pct: float  # (current_price - avg_entry) / avg_entry * 100, no fees
    cost_basis: float  # avg_entry * total_amount
    current_value: float  # current_price * total_amount
    realized_pnl: float
    suggested_stop_price: float
    stop_loss_pct: float
    stop_is_manual: bool  # True if stop was set manually, False if calculated
    stop_source: Optional[str]  # "atr" | "manual" | None
    portfolio_value_usd: float  # total portfolio for risk % calc
    risk_pct: float  # cost_basis / portfolio_value * 100


def calculate_snapshot(
    position: Position,
    current_price: float,
    portfolio_value_usd: float,
    stop_loss_pct: float = 2.0,
) -> PositionSnapshot:
    """
    Build a PositionSnapshot for a position at the given current price.

    Args:
        position: The open Position from the database.
        current_price: Latest market price from WebSocket ticker.
        portfolio_value_usd: Total portfolio value for risk % calculation.
        stop_loss_pct: Stop-loss percentage for suggested stop price (default 2%).
    """
    unrealized = position.unrealized_pnl(current_price)
    unrealized_pct = position.unrealized_pnl_pct(current_price)
    gross_pct = (
        (current_price - position.avg_entry_price) / position.avg_entry_price * 100
        if position.avg_entry_price > 0
        else 0.0
    )
    # cost_basis is fee-exclusive so it matches Avg Entry × Size visible in the table.
    # current_value deducts buy-side fees so that cost_basis + unrealized_pnl = current_value.
    # risk_pct uses the fee-inclusive total (true capital deployed).
    cost_basis = position.avg_entry_price * position.total_amount
    current_value = current_price * position.total_amount - position.total_fees_paid
    risk_pct = (
        ((cost_basis + position.total_fees_paid) / portfolio_value_usd * 100)
        if portfolio_value_usd > 0
        else 0.0
    )

    # Use manual stop if set, otherwise calculate from default %
    if position.stop_loss_price is not None:
        suggested_stop = position.stop_loss_price
        effective_stop_pct = (
            (1 - suggested_stop / position.avg_entry_price) * 100
            if position.avg_entry_price > 0
            else stop_loss_pct
        )
        stop_is_manual = True
        stop_source = position.stop_source
    else:
        suggested_stop = position.avg_entry_price * (1 - stop_loss_pct / 100)
        effective_stop_pct = stop_loss_pct
        stop_is_manual = False
        stop_source = None

    return PositionSnapshot(
        symbol=position.symbol,
        avg_entry_price=position.avg_entry_price,
        total_amount=position.total_amount,
        current_price=current_price,
        unrealized_pnl=unrealized,
        unrealized_pnl_pct=unrealized_pct,
        gross_pct=gross_pct,
        cost_basis=cost_basis,
        current_value=current_value,
        realized_pnl=position.realized_pnl,
        suggested_stop_price=suggested_stop,
        stop_loss_pct=effective_stop_pct,
        stop_is_manual=stop_is_manual,
        stop_source=stop_source,
        portfolio_value_usd=portfolio_value_usd,
        risk_pct=risk_pct,
    )


def calculate_weighted_avg_entry(
    existing_amount: float,
    existing_avg: float,
    new_amount: float,
    new_price: float,
) -> float:
    """
    Calculate the new weighted average entry price after adding to a position.

    Formula: (existing_cost + new_cost) / total_amount
    """
    total_amount = existing_amount + new_amount
    if total_amount == 0:
        return 0.0
    total_cost = (existing_avg * existing_amount) + (new_price * new_amount)
    return total_cost / total_amount


def calculate_realized_pnl(
    entry_price: float,
    exit_price: float,
    amount: float,
    fee: float = 0.0,
) -> float:
    """
    Calculate realized P&L for a closed or partially closed position.

    Args:
        entry_price: Weighted average entry price.
        exit_price: Fill price of the sell order.
        amount: Amount sold (base currency).
        fee: Exchange fee paid on the sell order.
    """
    gross_pnl = (exit_price - entry_price) * amount
    return gross_pnl - fee


def format_pnl(pnl: float, include_sign: bool = True) -> str:
    """Format a P&L value as a human-readable string with sign and 2 decimal places."""
    if include_sign:
        sign = "+" if pnl >= 0 else "-"
        return f"{sign}${abs(pnl):,.2f}"
    return f"${abs(pnl):,.2f}"


def format_pnl_pct(pct: float) -> str:
    """Format a P&L percentage with sign."""
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def pnl_color_class(pnl: float) -> str:
    """Return a Textual CSS class name based on P&L sign."""
    if pnl > 0:
        return "pnl-positive"
    if pnl < 0:
        return "pnl-negative"
    return "pnl-neutral"


@dataclass
class PortfolioSummary:
    """Aggregate P&L summary across all open positions."""

    total_unrealized_pnl: float
    total_realized_pnl: float
    total_cost_basis: float
    total_current_value: float
    total_risk_pct: float  # sum of all position risk %
    position_count: int

    @property
    def total_pnl(self) -> float:
        return self.total_unrealized_pnl + self.total_realized_pnl

    @property
    def overall_pnl_pct(self) -> float:
        if self.total_cost_basis == 0:
            return 0.0
        return (self.total_unrealized_pnl / self.total_cost_basis) * 100


def calculate_portfolio_summary(
    snapshots: list[PositionSnapshot],
) -> PortfolioSummary:
    """Aggregate multiple PositionSnapshots into a portfolio-level summary."""
    return PortfolioSummary(
        total_unrealized_pnl=sum(s.unrealized_pnl for s in snapshots),
        total_realized_pnl=sum(s.realized_pnl for s in snapshots),
        total_cost_basis=sum(s.cost_basis for s in snapshots),
        total_current_value=sum(s.current_value for s in snapshots),
        total_risk_pct=sum(s.risk_pct for s in snapshots),
        position_count=len(snapshots),
    )

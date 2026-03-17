"""
SQLModel data models for local trade tracking.

Positions and trades are tracked locally so we can:
- Calculate weighted average entry price across multiple buys
- Track realized P&L on partial/full closes
- Support future features: DCA calculator, fee tracking, CSV export
"""

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Position(SQLModel, table=True):
    """
    Represents an open or closed long position on a single symbol.
    Created when a buy is placed, updated when adding to or closing the position.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)  # e.g. "BTC/USD"

    # Size and entry tracking
    avg_entry_price: float  # weighted average entry, recalculated on each add
    total_amount: float  # current position size in base currency

    # P&L
    realized_pnl: float = Field(default=0.0)  # updated on partial/full close
    total_fees_paid: float = Field(
        default=0.0
    )  # buy-side fees only; used in unrealized P&L calculation

    # Stop-loss — None means use the default % from config
    stop_loss_price: Optional[float] = Field(default=None)

    # Status
    status: str = Field(default="open")  # "open" | "closed"
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = Field(default=None)

    def unrealized_pnl(self, current_price: float) -> float:
        """
        Calculate unrealized P&L at a given market price, net of buy-side fees.
        Fees paid on entry are a real cost that must be recovered before the
        position is profitable. Sell-side fees are excluded here — they are
        deducted from realized_pnl when a reduction is recorded.
        """
        gross = (current_price - self.avg_entry_price) * self.total_amount
        return gross - self.total_fees_paid

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """
        Calculate unrealized P&L as a percentage of total cost basis including
        buy-side fees. cost_basis = (avg_entry * amount) + buy_fees_paid
        """
        cost_basis = (self.avg_entry_price * self.total_amount) + self.total_fees_paid
        if cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl(current_price) / cost_basis) * 100

    def add_to_position(self, amount: float, price: float, fee: float = 0.0) -> None:
        """
        Add to this position, recalculating the weighted average entry price.
        Formula: new_avg = (old_avg * old_size + new_price * new_size) / (old_size + new_size)
        """
        total_cost = (self.avg_entry_price * self.total_amount) + (price * amount)
        self.total_amount += amount
        self.avg_entry_price = total_cost / self.total_amount
        self.total_fees_paid += fee

    def reduce_position(self, amount: float, price: float, fee: float = 0.0) -> float:
        """
        Reduce or close this position, calculating realized P&L for the closed portion.
        Returns the realized P&L for this reduction.

        The sell-side fee is deducted from realized_pnl only. It is NOT added to
        total_fees_paid, which tracks buy-side fees exclusively for use in
        unrealized_pnl(). Adding sell fees there would cause them to be subtracted
        again from any remaining open position's unrealized P&L — a double-deduction.
        """
        close_amount = min(amount, self.total_amount)
        pnl = (price - self.avg_entry_price) * close_amount - fee
        self.realized_pnl += pnl
        self.total_amount -= close_amount

        if self.total_amount <= 1e-6:  # treat as zero — below any exchange minimum
            self.total_amount = 0.0
            self.status = "closed"
            self.closed_at = datetime.utcnow()

        return pnl


class Trade(SQLModel, table=True):
    """
    Individual fill record. Each buy or sell that executes creates a Trade.
    Linked to a Position via position_id.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    position_id: Optional[int] = Field(default=None, index=True)  # FK → Position
    symbol: str = Field(index=True)
    side: str  # "buy" | "sell"
    amount: float  # quantity in base currency
    price: float  # fill price
    fee: float = Field(default=0.0)
    fee_currency: str = Field(default="USD")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    kraken_order_id: str = Field(default="")
    kraken_trade_id: str = Field(default="")
    order_type: str = Field(default="market")  # "market" | "limit"

    @property
    def cost(self) -> float:
        """Total cost of this trade in quote currency."""
        return self.amount * self.price

    @property
    def net_cost(self) -> float:
        """Total cost including fees."""
        return self.cost + self.fee


class PriceAlert(SQLModel, table=True):
    """
    A user-defined price alert for a symbol.
    Evaluated on every WebSocket ticker update.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)
    target_price: float
    direction: str  # "above" | "below"
    triggered: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    triggered_at: Optional[datetime] = Field(default=None)
    note: str = Field(
        default=""
    )  # optional user note (pre-wires post-MVP journal feature)

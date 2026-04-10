"""
Order book screen — live bid/ask depth display with analysis.

Features:
  #1 Bid/Ask imbalance ratio — single number showing buy vs sell pressure
  #2 Cumulative depth bars — visual representation of liquidity at each level
  #3 Wall highlighting — flags levels with unusually large volume
  #5 Position annotation — marks entry price and stop-loss in the book

Updated in real-time via the WebSocket orderbook worker.
"""

import logging
from typing import Optional
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, DataTable
from textual.containers import Horizontal, Vertical

from app.config import ORDER_BOOK_DEPTH

log = logging.getLogger(__name__)

# Width of the depth bar in characters
_BAR_WIDTH = 12
# Character used to draw depth bars
_BAR_CHAR = "█"
# How many times the average level size a level must be to count as a wall
_WALL_MULTIPLIER = 2.5
# Available grouping tick sizes in dollars (0 = no grouping / raw levels)
_TICK_SIZES = [0, 1, 5, 10, 25, 50, 100, 500]


# ---------------------------------------------------------------------------
# Pure analysis functions (tested independently)
# ---------------------------------------------------------------------------

def calculate_imbalance_ratio(
    bids: list[list[float]],
    asks: list[list[float]],
) -> float:
    """
    Calculate the bid/ask volume imbalance ratio.

    Returns bid_total / ask_total.
      > 1.0 → more buy-side pressure
      < 1.0 → more sell-side pressure
      = 0.0 → one or both sides empty
    """
    bid_total = sum(level[1] for level in bids)
    ask_total = sum(level[1] for level in asks)
    if bid_total == 0 or ask_total == 0:
        return 0.0
    return bid_total / ask_total


def find_walls(
    levels: list[list[float]],
    multiplier: float = _WALL_MULTIPLIER,
) -> set[float]:
    """
    Identify price levels with unusually large volume (walls).

    A level is a wall if its amount exceeds (average amount * multiplier).
    Returns a set of wall price levels.
    """
    if len(levels) < 2:
        return set()
    amounts = [level[1] for level in levels]
    avg = sum(amounts) / len(amounts)
    threshold = avg * multiplier
    return {level[0] for level in levels if level[1] >= threshold}


def build_depth_bars(
    levels: list[list[float]],
    bar_width: int = _BAR_WIDTH,
) -> list[int]:
    """
    Build cumulative depth bar widths for each price level.

    Each bar width is proportional to the cumulative volume up to that level
    relative to the total volume across all levels.

    Returns a list of integer bar widths (0..bar_width).
    """
    if not levels:
        return []

    cumulative = 0.0
    cumulative_amounts = []
    for _, amount in levels:
        cumulative += amount
        cumulative_amounts.append(cumulative)

    total = cumulative_amounts[-1]
    if total == 0:
        return [0] * len(levels)

    return [round((c / total) * bar_width) for c in cumulative_amounts]


def annotate_levels(
    levels: list[list[float]],
    entry_price: Optional[float],
    stop_price: Optional[float],
) -> dict[str, int]:
    """
    Find insertion indices for position annotations within the level list.

    For a bid-side (descending prices) or ask-side (ascending prices) list,
    returns a dict mapping annotation name to the index *before* which the
    annotation line should be inserted.

    Works for both sides: finds the first level whose price is below the
    annotation price (for bids) or above it (for asks).

    Returns e.g. {"entry": 2, "stop": 4}
    """
    if not levels:
        return {}

    annotations: dict[str, int] = {}
    prices = [level[0] for level in levels]
    descending = prices[0] > prices[-1]  # True for bids, False for asks

    def find_index(target: float) -> Optional[int]:
        for i, price in enumerate(prices):
            if descending and price < target:
                return i
            if not descending and price > target:
                return i
        return None

    if entry_price is not None:
        idx = find_index(entry_price)
        if idx is not None:
            annotations["entry"] = idx

    if stop_price is not None:
        idx = find_index(stop_price)
        if idx is not None:
            annotations["stop"] = idx

    return annotations


def group_levels(
    levels: list[list[float]],
    tick_size: float,
) -> list[list[float]]:
    """
    Aggregate order book levels into price buckets of size tick_size.

    Each price is floored to the nearest multiple of tick_size and amounts
    within the same bucket are summed. The result preserves the original
    sort order (descending for bids, ascending for asks).

    tick_size <= 0 returns the original list unchanged.
    """
    import math

    if tick_size <= 0 or not levels:
        return levels

    buckets: dict[float, float] = {}
    for price, amount in levels:
        bucket = math.floor(price / tick_size) * tick_size
        buckets[bucket] = buckets.get(bucket, 0.0) + amount

    descending = levels[0][0] > levels[-1][0]
    return [[p, a] for p, a in sorted(buckets.items(), reverse=descending)]


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class OrderBookScreen(Screen):
    """
    Displays live bid/ask order book depth for the active symbol.
    Updated by the app via update_orderbook() on every WS event.
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("[", "decrease_grouping", "Finer"),
        ("]", "increase_grouping", "Coarser"),
    ]

    DEFAULT_CSS = """
    OrderBookScreen {
        layout: vertical;
    }
    OrderBookScreen .info-bar {
        height: 3;
        border: solid $primary;
        content-align: center middle;
        color: $text-muted;
    }
    OrderBookScreen .book-container {
        layout: horizontal;
        height: 1fr;
    }
    OrderBookScreen .book-side {
        width: 1fr;
        border: solid $primary;
        padding: 0 1;
    }
    OrderBookScreen .book-title {
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    OrderBookScreen .bids-title { color: $success; }
    OrderBookScreen .asks-title { color: $error; }
    OrderBookScreen .annotation {
        color: $warning;
        text-style: bold;
        height: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entry_price: Optional[float] = None
        self._stop_price: Optional[float] = None
        self._tick_idx: int = 0  # index into _TICK_SIZES; 0 = no grouping
        self._last_symbol: str = ""
        self._last_orderbook: Optional[dict] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="info-bar", classes="info-bar")
        with Horizontal(classes="book-container"):
            with Vertical(classes="book-side", id="bids-side"):
                yield Static("▲ Bids", classes="book-title bids-title")
            with Vertical(classes="book-side", id="asks-side"):
                yield Static("▼ Asks", classes="book-title asks-title")
        yield Footer()

    def set_position_levels(
        self,
        entry_price: Optional[float],
        stop_price: Optional[float],
    ) -> None:
        """Called by the app to pass current position levels for annotation."""
        self._entry_price = entry_price
        self._stop_price = stop_price

    def update_orderbook(self, symbol: str, orderbook: dict) -> None:
        """
        Refresh the order book display with the latest data.
        Called by the app on every WebSocket orderbook update.
        """
        self._last_symbol = symbol
        self._last_orderbook = orderbook
        self._render_orderbook()

    def _render_orderbook(self) -> None:
        """Re-render using the last received data and current tick size."""
        if not self._last_orderbook:
            return
        tick = _TICK_SIZES[self._tick_idx]
        bids: list[list[float]] = group_levels(
            self._last_orderbook.get("bids", []), tick
        )[:ORDER_BOOK_DEPTH]
        asks: list[list[float]] = group_levels(
            self._last_orderbook.get("asks", []), tick
        )[:ORDER_BOOK_DEPTH]
        self._render_side("bids-side", bids, is_bid=True)
        self._render_side("asks-side", asks, is_bid=False)
        self._update_info_bar(self._last_symbol, bids, asks, tick)

    def action_increase_grouping(self) -> None:
        """Coarsen the price buckets (] key)."""
        if self._tick_idx < len(_TICK_SIZES) - 1:
            self._tick_idx += 1
            self._render_orderbook()

    def action_decrease_grouping(self) -> None:
        """Finer price buckets / back to raw levels ([ key)."""
        if self._tick_idx > 0:
            self._tick_idx -= 1
            self._render_orderbook()

    def _update_info_bar(
        self,
        symbol: str,
        bids: list[list[float]],
        asks: list[list[float]],
        tick_size: float = 0,
    ) -> None:
        """Update the combined spread + imbalance info bar."""
        try:
            bar = self.query_one("#info-bar", Static)
        except Exception:
            log.warning("_update_info_bar: info bar not mounted", exc_info=True)
            return

        if not bids or not asks:
            bar.update(f"{symbol}  │  No data")
            return

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread = best_ask - best_bid
        spread_pct = (spread / best_ask) * 100 if best_ask > 0 else 0
        mid = (best_bid + best_ask) / 2

        ratio = calculate_imbalance_ratio(bids, asks)
        if ratio == 0:
            ratio_str = "—"
            ratio_color = "white"
        elif ratio >= 1.5:
            ratio_str = f"{ratio:.2f}x"
            ratio_color = "green"
        elif ratio <= 0.67:
            ratio_str = f"{ratio:.2f}x"
            ratio_color = "red"
        else:
            ratio_str = f"{ratio:.2f}x"
            ratio_color = "yellow"

        tob_ratio = calculate_imbalance_ratio(bids[:3], asks[:3])
        if tob_ratio == 0:
            tob_str = "—"
        else:
            tob_color = "green" if tob_ratio >= 1.5 else ("red" if tob_ratio <= 0.67 else "yellow")
            tob_str = f"[{tob_color}]{tob_ratio:.2f}x[/{tob_color}]"

        grouping_str = f"  │  Grouped: ${tick_size:g}" if tick_size > 0 else ""

        bar.update(
            f"{symbol}  │  "
            f"Bid: [green]${best_bid:,.2f}[/green]  "
            f"Ask: [red]${best_ask:,.2f}[/red]  "
            f"Mid: ${mid:,.2f}  "
            f"Spread: ${spread:,.2f} ({spread_pct:.3f}%)  │  "
            f"TOB: {tob_str}  Depth: [{ratio_color}]{ratio_str}[/{ratio_color}]"
            f"{grouping_str}"
        )

    def _render_side(
        self,
        container_id: str,
        levels: list[list[float]],
        is_bid: bool,
    ) -> None:
        """
        Rebuild the widgets for one side of the book.

        Uses Static widgets instead of a DataTable so we can insert
        annotation rows between price levels.
        """
        container = self.query_one(f"#{container_id}", Vertical)

        # Remove all children except the title (first child)
        children = list(container.children)
        for child in children[1:]:
            child.remove()

        if not levels:
            container.mount(Static("No data", classes="annotation"))
            return

        color = "green" if is_bid else "red"
        bars = build_depth_bars(levels, bar_width=_BAR_WIDTH)
        walls = find_walls(levels)
        annotations = annotate_levels(
            levels,
            entry_price=self._entry_price,
            stop_price=self._stop_price,
        )

        widgets_to_mount = []

        for i, (price, amount) in enumerate(levels):
            # Insert annotation line before this row if needed
            for label, idx in annotations.items():
                if idx == i:
                    if label == "entry":
                        widgets_to_mount.append(
                            Static(f"── Entry ${self._entry_price:,.2f} ──", classes="annotation")
                        )
                    elif label == "stop":
                        widgets_to_mount.append(
                            Static(f"── Stop  ${self._stop_price:,.2f} ──", classes="annotation")
                        )

            bar_filled = _BAR_CHAR * bars[i]
            bar_empty = " " * (_BAR_WIDTH - bars[i])
            is_wall = price in walls
            wall_marker = " ◀" if is_wall else ""
            wall_style = "bold" if is_wall else ""

            if wall_style:
                row = (
                    f"[{color} {wall_style}]${price:,.2f}[/{color} {wall_style}]  "
                    f"[{color}]{bar_filled}[/{color}]{bar_empty}  "
                    f"{amount:.4f}{wall_marker}"
                )
            else:
                row = (
                    f"[{color}]${price:,.2f}[/{color}]  "
                    f"[{color}]{bar_filled}[/{color}]{bar_empty}  "
                    f"{amount:.4f}{wall_marker}"
                )

            widgets_to_mount.append(Static(row))

        container.mount(*widgets_to_mount)

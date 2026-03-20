"""
Technical indicator computations — pure functions, no I/O.

All functions return None when data is insufficient rather than 0,
so callers can distinguish "no data" from a genuine zero reading.
"""


def compute_atr(ohlcv: list[list[float]], period: int = 14) -> float | None:
    """
    Average True Range (Wilder's smoothing).

    ohlcv: list of [timestamp, open, high, low, close, volume], oldest-first.
    Needs at least period + 1 candles.
    """
    if len(ohlcv) < period + 1:
        return None

    trs = []
    for i in range(1, len(ohlcv)):
        _, _, h, l, pc_close, _ = ohlcv[i - 1]
        _, _, high, low, close, _ = ohlcv[i]
        tr = max(high - low, abs(high - pc_close), abs(low - pc_close))
        trs.append(tr)

    # Seed: simple mean of first `period` TR values
    atr = sum(trs[:period]) / period
    k = 2 / (period + 1)

    # Wilder EMA over remaining bars
    for tr in trs[period:]:
        atr = tr * k + atr * (1 - k)

    return atr


def compute_rsi(prices: list[float], period: int = 14) -> float | None:
    """
    Relative Strength Index (Wilder's smoothing).

    prices: list of closing prices, oldest-first.
    Needs at least period + 1 prices.
    """
    if len(prices) < period + 1:
        return None

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    # Seed avg_gain / avg_loss from first `period` deltas
    gains = [d if d > 0 else 0.0 for d in deltas[:period]]
    losses = [abs(d) if d < 0 else 0.0 for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder smooth over remaining deltas
    for d in deltas[period:]:
        gain = d if d > 0 else 0.0
        loss = abs(d) if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_win_rate(closed_positions: list) -> float | None:
    """
    Win rate as a percentage (0-100).

    A position is a win if realized_pnl > 0; zero counts as a loss.
    Returns None for an empty list.
    """
    if not closed_positions:
        return None
    winners = len([p for p in closed_positions if p.realized_pnl > 0])
    return winners / len(closed_positions) * 100


def compute_avg_r(closed_positions: list) -> float | None:
    """
    Average return per trade as a percentage of cost basis.

    Skips positions where avg_entry_price * total_amount == 0 to avoid
    ZeroDivisionError.  Returns None when no valid positions exist.
    """
    valid = [
        p for p in closed_positions
        if p.avg_entry_price * p.total_amount != 0
    ]
    if not valid:
        return None
    returns = [
        p.realized_pnl / (p.avg_entry_price * p.total_amount) * 100
        for p in valid
    ]
    return sum(returns) / len(returns)

# tui-trader

A terminal UI (TUI) trading assistant for **Kraken Pro spot exchange**.

Built for personal use by a single trader. Not a library, not multi-user, not intended for deployment.

---

## Features

- **Live dashboard** — open positions with real-time P&L, updated via WebSocket on every trade event
- **Technical indicators** — RSI (hourly candles, seeded from REST on startup), ATR, and VWAP displayed on the dashboard
- **Paper trading mode** — simulate orders locally without touching the exchange (`--paper` flag, orange header banner, separate database)
- **Buy / Sell** — place market and limit spot orders directly from the terminal
- **Add to position** — buy more of an existing position with automatic weighted average entry recalculation
- **Close position** — sell with the full position size pre-filled
- **USD / QTY toggle** — enter order amounts in dollars or asset quantity, converted at the live price
- **Risk management panel** — % of portfolio at risk and suggested stop-loss per position
- **Manual stop-loss** — override the default stop % with a specific price per position, updated live
- **Price alerts** — set above/below price triggers; fires both an in-terminal toast and an OS desktop notification (via D-Bus) when hit
- **Order book** — live bid/ask depth display with spread and depth chart, updated via WebSocket
- **History** — closed positions table with a cumulative realized P&L line chart over time
- **Portfolio value** — total wallet value (all assets + free USD) shown live in the header

---

## Stack

| Layer | Technology |
|---|---|
| TUI | [Textual](https://github.com/Textualize/textual) |
| Exchange API | [ccxt Pro](https://github.com/ccxt/ccxt) (WebSocket + REST) |
| Charts | [textual-plotext](https://github.com/Textualize/textual-plotext) |
| Database | SQLite via [SQLModel](https://sqlmodel.tiangolo.com/) |
| Notifications | D-Bus via [dasbus](https://dasbus.readthedocs.io/) |
| Config | [python-dotenv](https://github.com/theskumar/python-dotenv) |

---

## Requirements

### System packages (Arch Linux)

```bash
sudo pacman -S python-dasbus python-gobject libnotify
```

| Package | Purpose |
|---|---|
| `python-dasbus` | D-Bus bindings for OS desktop notifications |
| `python-gobject` | GLib/GObject bindings required by dasbus |
| `libnotify` | Provides `notify-send` (optional, not used directly) |

> These are system packages, not pip packages. The venv must be created with
> `--system-site-packages` (see below) for the app to see them.

### Kraken API key

Generate at **Kraken → Security → API**. Required permissions:

| Permission | Used for |
|---|---|
| Query Funds | Balance on startup + WebSocket balance stream |
| Query Open Orders & Trades | Order status, fill notifications |
| Query Closed Orders & Trades | Trade history |
| Create & Modify Orders | Placing buy/sell orders |
| Cancel & Close Orders | Cancelling open orders |

---

## Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd tui-trader

# 2. Create venv — must use --system-site-packages for dasbus/gobject
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt

# 3. First run — creates config template and exits with instructions
.venv/bin/python main.py

# 4. Add your Kraken API credentials
#    Config is written to: ~/.config/tui-trader/config.env
$EDITOR ~/.config/tui-trader/config.env

# 5. Run
.venv/bin/python main.py

# Run in paper trading mode (simulated orders, no real exchange activity)
.venv/bin/python main.py --paper
```

---

## File locations (XDG Base Directory Specification)

| Purpose | Path |
|---|---|
| Config / credentials | `~/.config/tui-trader/config.env` |
| Database | `~/.local/share/tui-trader/trades.db` |
| Paper trading database | `~/.local/share/tui-trader/paper_trades.db` |

---

## Configuration

All settings live in `~/.config/tui-trader/config.env`:

```env
# Required
KRAKEN_API_KEY=your_api_key_here
KRAKEN_API_SECRET=your_api_secret_here

# Optional (defaults shown)
DEFAULT_SYMBOL=BTC/USD
DEFAULT_STOP_LOSS_PCT=2.0
ORDER_BOOK_DEPTH=10
HISTORY_REFRESH_SECONDS=60
WS_RECONNECT_BACKOFF=5
```

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `1` | Dashboard |
| `2` | Buy |
| `3` | Order book |
| `4` | Alerts |
| `5` | History |
| `b` | Buy (from dashboard) |
| `s` | Sell (from dashboard) |
| `a` | Add to selected position |
| `c` | Close selected position |
| `l` | Set stop-loss for selected position |
| `d` | Delete selected alert (on alerts screen) |
| `q` | Quit |
| `Escape` | Go back / close screen |

---

## Importing existing orders

If you have existing Kraken orders you want to track, use the import script:

```bash
.venv/bin/python scripts/import_orders.py ORDER_ID [ORDER_ID ...]
```

Fetches fill details from Kraken, groups buys on the same symbol into a single
position with weighted average entry, and writes to the local database. Safe to
re-run — skips orders already present.

---

## Running tests

```bash
# Install dev dependencies
.venv/bin/pip install -r requirements-dev.txt

# Run all tests
.venv/bin/pytest tests/ -v
```

Tests complete in under 2 seconds and cover position/P&L logic, database CRUD,
alert evaluation, trade recording, technical indicators, paper exchange fills,
order book logic, and the history chart series.

---

## Architecture notes

- **WebSocket-first** — prices never polled. Ticker, order book, and private fills all stream via ccxt Pro WebSocket workers running as asyncio tasks on Textual's event loop.
- **Locally tracked positions** — open positions are stored in a local SQLite database, not read from Kraken directly. This enables weighted average entry tracking, add-to-position logic, and fee-adjusted P&L.
- **Long-only** — tracks long spot positions only.
- **Single ticker subscription** — only `DEFAULT_SYMBOL` has a live price feed. Other wallet assets are valued at $0 until multi-pair support is added.

For full architecture details, patterns, and gotchas see [AGENTS.md](AGENTS.md).

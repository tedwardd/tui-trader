# AGENTS.md — tui-trader project context

This file is intended for AI agents and developers picking up this codebase.
It captures architecture decisions, known patterns, active bugs/quirks, and
the post-MVP roadmap.

---

## What this project is

A terminal UI (TUI) trading assistant for **Kraken Pro spot exchange**.
Built in Python using Textual (TUI), ccxt Pro (WebSocket + REST), SQLModel
(SQLite), and python-dotenv.

The tool is for **personal use** by a single trader. It is not a library,
not multi-user, and not intended for deployment.

---

## Running the app

```bash
# First run — creates config template and exits with instructions
.venv/bin/python main.py

# Subsequent runs
.venv/bin/python main.py

# Paper trading mode — simulated orders, no real exchange activity
.venv/bin/python main.py --paper
```

### Setup

#### 1. System packages (Arch Linux)

These are not pip-installable — they must be installed via pacman:

```bash
sudo pacman -S python-dasbus python-gobject libnotify
```

| Package | Purpose |
|---|---|
| `python-dasbus` | D-Bus bindings for OS desktop notifications |
| `python-gobject` | GLib/GObject Python bindings (required by dasbus) |
| `libnotify` | Provides `notify-send` CLI (optional fallback, not used directly) |

#### 2. Python venv

The venv **must** be created with `--system-site-packages` so that
`python-dasbus` and `python-gobject` (system packages) are visible inside it:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

> ⚠️ If the venv was created without `--system-site-packages`, desktop
> notifications will silently degrade — the in-terminal toast still shows
> but no OS popup will appear. Recreate the venv if this happens.

#### 3. Dev dependencies (for running tests)

```bash
.venv/bin/pip install -r requirements-dev.txt
```

---

## File locations (XDG Base Directory Specification)

| Purpose | Path |
|---|---|
| Config / credentials | `~/.config/tui-trader/config.env` |
| Database | `~/.local/share/tui-trader/trades.db` |
| Paper trading database | `~/.local/share/tui-trader/paper_trades.db` |
| Cloud sync session ID | `~/.local/share/tui-trader/cloud_sync.session` |

On first run, if `~/.config/tui-trader/config.env` does not exist, it is
created from a template and the app exits with instructions. The user must
add their Kraken API key and secret before the app will start.

The project-local `.env` file is **not used** — it is a legacy artifact from
before the XDG migration and can be deleted.

---

## Architecture overview

### Data flow

```
Kraken WebSocket (ccxt Pro)
  ├── Public endpoint (wss://ws.kraken.com/v2)
  │     ├── watch_ticker()      → on_ticker_update()  → P&L recalc, alerts, header
  │     └── watch_order_book()  → on_orderbook_update() → order book screen
  └── Private endpoint (wss://ws-auth.kraken.com/v2)
        ├── watch_orders()      → on_orders_update()  → reload positions
        ├── watch_balance()     → on_balance_update() → update free USD + asset balances
        └── watch_my_trades()   → on_my_trades_update() → refresh history screen

Kraken REST (ccxt) — used only for:
  - Placing / cancelling orders
  - Fetching full wallet balance on startup
  - Fetching OHLCV data on startup (ATR seed: 20 daily candles; RSI seed: 30 hourly candles)
  - ATR refresh every 30 minutes via set_interval
  - Fetching trade history on startup (import script)

SQLite (trades.db) — local source of truth for:
  - Open and closed positions (with weighted avg entry)
  - Individual trade fills
  - Price alerts
```

### Worker threading model

All WebSocket workers are **async coroutines** passed to Textual's
`run_worker()`. They run as asyncio Tasks on the **same event loop as the
app** — NOT in a thread pool. This means:

- ✅ Workers can call app methods directly (e.g. `app.on_ticker_update(ticker)`)
- ❌ `call_from_thread()` must NEVER be used from these workers — it raises
  `RuntimeError: call_from_thread must run in a different thread from the app`

The **order submission worker** (`TradeScreen._submit_order`) is the exception:
it uses `run_worker(thread=True)` because `exchange.place_market_sell()` is a
blocking REST call. It uses `self.app.call_from_thread()` to post results back
to the UI.

### Screen update guards

`get_screen(name)` returns the screen object regardless of whether it is
currently mounted/visible. Calling `query_one()` on an inactive screen raises
`NoMatches`. All screen update calls in `main.py` are guarded with:

```python
if self.screen is target_screen:
    target_screen.update_something(...)
```

### Row tracking in DataTable widgets

Textual's `RowKey` objects do **not** stringify to the key value passed to
`add_row(key=...)`. Both `PositionTable` and `RiskPanel` maintain their own
`_row_symbols: set[str]` to track which rows exist, rather than iterating
`table.rows` and calling `str(row_key)`.

To retrieve the key value of the currently selected row, use:
```python
rows = list(table.ordered_rows)
key_value = rows[table.cursor_row].key.value
```

### Screen reuse and prefill

Screens are installed once via `install_screen()` and reused — `on_mount()`
only fires the first time. Use `on_screen_resume()` to re-apply prefill values
every time a screen becomes active.

### Order form amount modes

`OrderForm` supports two input modes toggled by a button:
- **QTY mode** (default) — user enters base asset quantity directly (e.g. `0.001`)
- **USD mode** — user enters a dollar amount; converted to qty at submission
  using the live price from `set_live_price()`

The callback always receives quantity in base asset regardless of mode.
`prefill()` always resets to QTY mode.

---

## Module reference

### `app/cloud_sync.py`
- Optional cloud database sync to any S3-compatible provider (R2, S3, B2, etc.)
- All functions silently no-op when `CLOUD_SYNC_ENABLED=false` or vars are missing
- `is_configured()` — returns True only when all required vars are set and enabled
- `sync_down()` — downloads DB from bucket if remote `LastModified` > local mtime;
  called during `on_mount` before `init_db()` so the app boots from the latest copy
- `sync_up()` — flushes WAL (`PRAGMA wal_checkpoint(TRUNCATE)`) then uploads DB;
  called in `on_unmount` and after each trade write in `screens/trade.py`
- `acquire_lock(session_id)` / `release_lock(session_id)` — writes/deletes the
  lock file at `{CLOUD_SYNC_OBJECT_KEY}.lock`; release checks ownership first
- `force_clear_lock()` — unconditional lock delete; used by `--force-unlock` only
- `check_lock()` — returns parsed lock dict or `None`
- `load_local_session_id()` / `save_local_session_id()` / `clear_local_session_id()`
  — manage `~/.local/share/tui-trader/cloud_sync.session` for crash recovery

**Lock file format** (JSON in bucket):
```json
{
    "session_id": "<uuid4>",
    "hostname":   "machine-a.local",
    "pid":        12345,
    "locked_at":  "2025-01-01T10:00:00Z"
}
```

**Read-only mode**: when a second session finds the lock held by another session,
`TradeApp._read_only` is set to `True`. All write operations across every screen
check `self.app._read_only` and show a warning instead of proceeding. A persistent
banner is shown at the top of the app. `AlertManager.read_only` is also set so
alert notifications still fire but triggered status is not written to the DB.

**Crash recovery — Path A** (same machine): the app writes `session_id` to
`cloud_sync.session` on lock acquisition. On restart, if that file's session_id
matches the cloud lock, the app resumes as lock owner and proceeds normally.

**Crash recovery — Path B** (different machine, or session file gone):
```bash
.venv/bin/python main.py --force-unlock
```
This prints the stale lock details, explains the data-loss risk, and requires
typing `CONFIRM`. After clearing the lock, the app starts as a normal session.
Recovery of any lost trades: `scripts/import_orders.py <ORDER_ID> [...]`

### `app/config.py`
- Handles XDG path resolution and first-run bootstrap
- Exports: `CONFIG_DIR`, `DATA_DIR`, `CONFIG_FILE`, `DATABASE_PATH`, `PAPER_DATABASE_PATH`
- Exports all settings as module-level constants
- Calls `sys.exit(0)` on first run if config file doesn't exist yet
- Also exports `CLOUD_SYNC_ENABLED`, `CLOUD_SYNC_ENDPOINT_URL`,
  `CLOUD_SYNC_BUCKET`, `CLOUD_SYNC_KEY_ID`, `CLOUD_SYNC_KEY_SECRET`,
  `CLOUD_SYNC_OBJECT_KEY` (all optional, default to disabled/empty)

### `app/models.py`
- SQLModel table definitions: `Position`, `Trade`, `PriceAlert`
- `Position.add_to_position()` — recalculates weighted average entry in place
- `Position.reduce_position()` — calculates realized P&L, marks closed when
  `total_amount <= 1e-6` (dust threshold to handle float rounding and minor
  Kraken fill discrepancies)
- `Position.unrealized_pnl()` — net of buy-side fees (`total_fees_paid`)
- `Position.unrealized_pnl_pct()` — denominator includes fees paid
- `Position.stop_loss_price` — optional manual stop override; `None` means
  use `DEFAULT_STOP_LOSS_PCT` from config

### `app/database.py`
- All SQLite CRUD — no business logic
- `configure_engine(path)` — switch to a different database file; must be
  called before `init_db()`. Used by paper trading mode to redirect writes to
  `paper_trades.db` without touching the live database.
- `init_db()` — safe to call on every startup (idempotent); runs schema
  migrations (e.g. `_migrate_add_stop_loss_price()`)
- `set_stop_loss(position_id, price)` — set or clear manual stop (`None` = clear)
- `trade_exists(kraken_trade_id)` — deduplication guard for import script

### `app/pnl.py`
- **Pure functions, no I/O** — safe to unit test in isolation
- `calculate_snapshot()` — builds a `PositionSnapshot` at a given price;
  uses `position.stop_loss_price` if set, otherwise calculates from
  `DEFAULT_STOP_LOSS_PCT`
- `PositionSnapshot.stop_is_manual` — `True` if stop was set manually
- `calculate_portfolio_summary()` — aggregates snapshots into portfolio totals
- `PositionSnapshot.risk_pct` — `cost_basis / portfolio_value * 100`

### `app/exchange.py`
- Thin ccxt REST wrapper
- Module-level singleton `_exchange` — one REST client shared across the app
- Used for writes (orders), startup balance fetch, and OHLCV data
- `fetch_ohlcv(symbol, timeframe, limit)` — returns list of
  `[timestamp, open, high, low, close, volume]` candles, oldest-first

### `app/indicators.py`
- **Pure functions, no I/O** — safe to unit test in isolation
- All functions return `None` when data is insufficient (callers can distinguish
  "no data" from a genuine zero reading)
- `compute_rsi(prices, period=14)` — Wilder-smoothed RSI from a list of closing
  prices; needs `period + 1` prices minimum
- `compute_atr(ohlcv, period=14)` — Wilder-smoothed ATR from OHLCV candles;
  needs `period + 1` candles minimum
- `compute_win_rate(closed_positions)` — win rate as a percentage (0–100)
- `compute_avg_r(closed_positions)` — average return per trade as % of cost basis

### `app/paper_exchange.py`
- Simulates order fills without touching the real exchange
- Returns the same dict shape as ccxt order objects so `screens/trade.py`
  needs no special-casing between paper and live paths
- Fees approximate Kraken's retail schedule: taker 0.40% (market), maker 0.16% (limit)
- Order IDs are prefixed `PAPER-` for easy identification in the database
- Functions: `place_market_buy`, `place_market_sell`, `place_limit_buy`, `place_limit_sell`

### `app/streams.py`
- `StreamManager` — owns the single `ccxtpro.kraken` instance
- Three workers: `ticker_worker`, `orderbook_worker`, `private_worker`
- `private_worker` runs `watch_orders`, `watch_balance`, `watch_my_trades`
  concurrently via `asyncio.gather()`
- Module-level singleton `stream_manager` imported by `main.py`

### `app/alerts.py`
- `AlertManager` — evaluates active alerts on every ticker event
- Calls `on_trigger` callback (set to `app._on_alert_triggered`) when hit
- `reload()` must be called on startup to load alerts from DB
- `read_only` property — when True, triggered alerts still fire the notification
  callback but `db.mark_alert_triggered()` is skipped (used in locked sessions)

### `app/notifications.py`
- `send_notification(title, body, urgency, timeout_ms)` — sends an OS desktop
  notification via D-Bus (`org.freedesktop.Notifications`) using dasbus
- `URGENCY` dict maps `"low"/"normal"/"critical"` to freedesktop byte values
- Lazy-initialised proxy singleton — one D-Bus connection reused across calls
- **Silently degrades** if dasbus/gi.repository are unavailable (CI, headless,
  missing system packages) — in-terminal Textual toast is always shown regardless
- Requires system packages: `python-dasbus`, `python-gobject`
- Requires venv created with `--system-site-packages` to see those packages

### `main.py`
- `TradeApp(App)` — the root Textual application
- `TradeApp(paper_mode=False)` — pass `True` (via `--paper` CLI flag) to enable
  paper trading; switches DB to `paper_trades.db`, skips cloud sync, shows an
  orange header banner
- Key state: `_prices` (dict, last known price per symbol), `_free_usd`,
  `_asset_balances` (full Kraken wallet), `_open_positions`
- `_read_only: bool` — True when another session holds the cloud lock; all
  write actions across every screen check this flag
- `_cloud_session_id: str | None` — UUID of the current cloud lock; None if
  cloud sync is not configured or this session is read-only
- `_lock_info: dict | None` — lock metadata (hostname, locked_at, etc.) from
  the cloud, set when entering read-only mode; used to populate the banner
- Indicator state: `_atr`, `_rsi`, `_vwap` (all `float | None`); `_hourly_closes`
  (deque, maxlen=100); `_candle_open_ts` (int, unix hour); `_candle_close` (float)
- RSI is seeded on startup from 30 hourly REST candles and updated once per hour
  when a candle closes in `on_ticker_update`. It does **not** update on every tick.
- ATR is seeded from 20 daily candles on startup and refreshed every 30 minutes
  via `set_interval(30 * 60, self._refresh_atr)`
- `_refresh_dashboard()` — called on every ticker event; computes portfolio
  value as `_free_usd + sum(amount * price for each asset in _asset_balances)`;
  also calls `dashboard.update_indicators(vwap, rsi, atr, price)`
- `reload_positions()` — called immediately after a buy/sell is recorded
  locally so the dashboard updates without waiting for the WS fill notification
- Portfolio value in header subtitle is set inside `_refresh_dashboard()`
- `_handle_force_unlock()` — standalone function (not a method); runs before
  the TUI starts when `--force-unlock` is passed; interactive terminal prompt

### `screens/stop_loss_modal.py`
- `StopLossModal(ModalScreen)` — triggered by `l` on the dashboard
- Pre-fills with current stop price and source (manual vs calculated)
- **Set** saves the price; **Clear to default** sets `stop_loss_price = None`
- Validates that stop price is below current market price

### `screens/history.py`
- Contains `PnlChart(PlotextPlot)` — cumulative realized P&L line chart
- Chart anchors at `$0` on the day before the first closed position
- Line is green when net P&L is positive, red when negative
- Zero baseline always drawn for visual reference
- `_update_chart()` sorts positions by `closed_at` and builds a cumulative series

### `widgets/order_form.py`
- `OrderForm.Cancelled` message — posted on Cancel; `TradeScreen` listens and
  calls `app.pop_screen()`
- After successful order placement, `_on_order_success` clears the form,
  shows a toast notification, and calls `app.pop_screen()`

---

## Portfolio value calculation

```
portfolio_usd = _free_usd + sum(
    amount * _prices.get(f"{currency}/USD", 0.0)
    for currency, amount in _asset_balances.items()
)
```

- `_free_usd` — USD cash balance from Kraken (REST on startup, WS thereafter)
- `_asset_balances` — full non-USD wallet from Kraken (e.g. `{"BTC": 0.00345905}`)
- `_prices` — keyed as `"BTC/USD"`, populated by WebSocket ticker events
- Assets with no live price feed yet are valued at $0 (only `DEFAULT_SYMBOL`
  has a ticker subscription in the current MVP)

---

## P&L calculation

### Unrealized P&L
```
unrealized_pnl = (current_price - avg_entry_price) * total_amount - total_fees_paid
unrealized_pnl_pct = unrealized_pnl / (avg_entry_price * total_amount + total_fees_paid) * 100
```
Buy-side fees are deducted from unrealized P&L — they are a real cost that
must be recovered before the position is profitable.

### Realized P&L
```
realized_pnl += (exit_price - avg_entry_price) * close_amount - sell_fee
```
Sell-side fees are deducted at close time inside `reduce_position()`.

### Stop-loss
- Default: `avg_entry_price * (1 - DEFAULT_STOP_LOSS_PCT / 100)`
- Manual override: stored as `Position.stop_loss_price`; `stop_loss_pct` is
  back-calculated from the manual price for display in the risk panel

---

## Scripts

### `scripts/import_orders.py`
One-off utility to import Kraken order IDs into the local database.

```bash
.venv/bin/python scripts/import_orders.py ORDER_ID [ORDER_ID ...]
```

- Fetches fill details from Kraken REST API
- Groups buys on the same symbol into one `Position` with weighted avg entry
- Skips orders already present in the DB (`trade_exists()` deduplication)
- Safe to re-run

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `1` | Dashboard |
| `2` | Buy (trade screen) |
| `3` | Order book |
| `4` | Alerts |
| `5` | History |
| `b` | Buy (from dashboard) |
| `s` | Sell (from dashboard) |
| `a` | Add to selected position |
| `c` | Close selected position |
| `l` | Set stop-loss for selected position |
| `q` | Quit |
| `Escape` | Close current screen / go back |

---

## Kraken API key permissions required

| Permission | Used for |
|---|---|
| Query Funds | Balance fetch (startup + WebSocket) |
| Query Open Orders & Trades | `watch_orders()`, open order fetch |
| Query Closed Orders & Trades | History screen, startup sync |
| Create & Modify Orders | Placing buy/sell orders |
| Cancel & Close Orders | Cancelling open orders |

---

## Post-MVP TODO list

### Medium priority
- **In-app settings screen** — configure options without editing files
- **Smaller terminal usability** — responsive layout, collapsible panels
- **TradingView integration** — pressing Enter on a symbol opens the pair in browser

### Low priority
- **DCA calculator** — plan averaging down to a target entry price
- **Trade journal** — attach notes to trades
- **Multi-pair dashboard** — monitor multiple symbols simultaneously (requires multiple ticker subscriptions)
- **Fee tracking** — realized fees per trade and cumulative totals
- **CSV export** — trade history export for tax purposes

---

## Testing

### Running tests

```bash
.venv/bin/pytest tests/ -v
```

Tests complete in under 2 seconds. Run after every change to existing logic.

### Test structure

```
tests/
├── conftest.py                    # Env vars, temp XDG dirs, db_engine fixture
├── unit/
│   ├── test_models.py             # Position/Trade methods — pure, no I/O
│   ├── test_pnl.py                # All functions in app/pnl.py — pure, no I/O
│   ├── test_indicators.py         # compute_rsi, compute_atr, compute_win_rate, compute_avg_r
│   ├── test_paper_exchange.py     # Paper fill dicts, fee math, order ID format
│   ├── test_orderbook_logic.py    # Order book depth/spread logic
│   └── test_notifications.py     # send_notification with mocked D-Bus/GLib
├── integration/
│   ├── test_database.py           # All CRUD with in-memory SQLite
│   └── test_alerts.py             # AlertManager evaluate/trigger logic
└── logic/
    ├── test_trade_logic.py        # _record_buy, _record_sell, full lifecycle
    └── test_history_chart.py      # Cumulative P&L series building
```

### TDD workflow

Write a failing test first, then implement. The test suite is fast enough to
run on every save. For any change to existing logic, run the relevant test
file before and after to confirm the regression is caught and then fixed.

### conftest.py gotcha

`app.config` runs side-effecting code at import time (`sys.exit(0)` on first
run, `EnvironmentError` for missing credentials). `conftest.py` sets env vars
and pre-creates the config file at **module level** (not inside a fixture) so
they are in place before pytest imports any test module.

### Mocking system packages in tests

`python-dasbus` and `python-gobject` are system packages not available in CI.
`tests/unit/test_notifications.py` injects mock modules into `sys.modules`
at module level before any app import, so notification tests run without
system dependencies installed.

---

## Known design constraints

- **Single symbol ticker** — only `DEFAULT_SYMBOL` has a WebSocket ticker
  subscription. Other held assets are valued at $0 in portfolio calculations
  until multi-pair support is added.
- **Positions are locally tracked** — the app does not read open positions
  from Kraken directly. Positions must be entered via the trade screen or
  imported via `scripts/import_orders.py`. This is intentional: it enables
  weighted average entry tracking and add-to-position logic.
- **Long-only** — the app tracks long spot positions only. Short/margin
  positions are not modelled.
- **Fill price fallback** — for market orders, `fill_price` is read from
  `order["average"]` → `order["price"]` → user-entered price → live price.
  If Kraken's response doesn't immediately include the fill price, the live
  price at submission time is used as a fallback.
- **Dust threshold** — `reduce_position()` treats `total_amount <= 1e-6` as
  zero to handle float rounding and minor Kraken fill discrepancies. Residuals
  smaller than this are force-closed rather than left as phantom open positions.

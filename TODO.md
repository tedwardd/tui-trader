# TODO

## Configuration
- [ ] **Configurable maker/taker fees per exchange** — fee rates are currently hardcoded in `app/exchange.py` (`estimate_fee`: 0.40% taker, 0.16% maker). As part of broader multi-exchange support, fee rates should be configurable alongside API keys and other exchange-specific settings (endpoint, symbol format, etc.).

## Medium priority
- [ ] **In-app settings screen** — configure options without editing files
- [ ] **Smaller terminal usability** — responsive layout, collapsible panels
- [ ] **TradingView integration** — pressing Enter on a symbol opens the pair in browser

## Low priority
- [ ] **DCA calculator** — plan averaging down to a target entry price
- [ ] **Trade journal** — attach notes to trades
- [ ] **Multi-pair dashboard** — monitor multiple symbols simultaneously (requires multiple ticker subscriptions)
- [ ] **Fee tracking** — realized fees per trade and cumulative totals
- [ ] **CSV export** — trade history export for tax purposes

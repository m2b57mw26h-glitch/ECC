# Daily crypto screen: procedure

Run once a day. Read-only: never place, modify or cancel orders.

1. `D=$(date -u +%F)`; create `/mnt/project-files/trading-agent/daily/raw/` (clear yesterday's raw/*.json first).
2. Universe = the 48 coins in `universe.txt`. Every month, refresh it from `search_markets` (top main-dex crypto by volume; skip `LIT:`, `para:`, `xyz:` symbols).
3. Candles: for each coin call `mcp__liquid_trading__show_chart` (interval `1d`, limit 15; for BTC use limit 25, the market filter needs 20 days) and save
   `daily/raw/<SYM>.json` = `{"symbol": "<SYM>", "candles": [...]}` copied verbatim.
   Use 4 parallel general-purpose workers of 12 coins each; tell them to call only show_chart, never hearthbot tools.
4. Snapshot: call `mcp__liquid_trading__analyze_markets_batch` in groups of ≤10 and write
   `daily/markets_$D.json` = `{"markets": {SYM: {px, chg24, funding, oi, long_pct, bias}}}`
   (funding as a number in %, oi in USD, long_pct = longSharePct or null).
5. `python3 /mnt/project-files/trading-agent/screener.py /mnt/project-files/trading-agent/daily $D`
   → `daily/report_$D.md` and `daily/screen_$D.csv`.
5b. Strategy B (RSI(3) pullback, added 2026-10-11): `python3 /mnt/project-files/trading-agent/mr_signals.py
   /mnt/project-files/trading-agent/backtest/raw /mnt/project-files/trading-agent/daily/raw <coins held under strategy B>`
   (merges today's candles into the 200-day history first). Append its output to `daily/report_$D.md`.
   Strategy B legs: 1/8 of equity notional, leverage 5 (user rule 2026-10-11, confirmed 2026-10-12), market at open, stop-loss -9%, no take-profit; exit when
   the script says RSI(3) > 55 (close_position preview + user confirmation). Mark them `strategy=B` in journal.csv.
5c. Turtle 2 + MACD/EMA50 (paper, added 2026-10-12, see backtest/TV_STRATEGIES.md): `python3 /mnt/project-files/trading-agent/backtest/tv/live_signals.py
   /mnt/project-files/trading-agent/daily/raw <paper equity> $D <coins held by the screener basket or strategy B>`. Append its output to the report.
   Open strategy positions live in `backtest/tv/live_positions.json` ({"positions": [{coin, strategy T2|ME, side, entry, stop, opened}]}):
   add a row only after the user's "да" and a confirmed fill, remove it when the position is closed (signal exit or stop).
   Entries: market, leverage 5, stop = −9% from the current mark if the script's stop is already above it (Turtle stop is 2N capped at 9%);
   skip a coin whose max leverage on Liquid is below 5 (e.g. AERO 3x). Exits: close_position preview for each listed exit.
   Propose entries as their own basket (suggest_trades_batch, or suggest_trade for one leg), separate from the screener basket, and wait for "да".
6. News (required): `get_news` + WebSearch (mode extended) for (a) the general crypto/macro picture and the week's
   calendar (CPI, FOMC, ETF flows, token unlocks), (b) each picked coin and each open position.
   Append a "## Новости и их влияние" section to `daily/report_$D.md`: general backdrop, calendar, and per coin
   "supports" / "against" / — none, with source links. Drop or flag picks with clearly contrary news.
6b. Idea review (TradingAgents method, added 2026-10-12): first `python3 /mnt/project-files/trading-agent/review/ideas.py settle`
   and write a lesson for every `NEEDS LESSON` line. Then, for the top 3-5 picks, follow `review/TEMPLATE.md`
   (bull / bear / risk check / rating, using the last 10 lessons) and log each with `review/ideas.py add`.
   Only coins rated Buy/Overweight (long) or Sell/Underweight (short) go into the basket in step 8.
7. Paper positions: `get_portfolio` and `get_position_history`. For every position closed since the last run, fill its
   row in `journal.csv` (exit, reason, pnl). For each loss, write `loss_review`: which signal failed (trend/momentum,
   stop too tight vs ATR, bad entry timing e.g. after a spike, news against, whole market moved), and keep a running
   tally in `lessons.md`. Change screener rules only after ≥10 closed trades and only if a pattern repeats; note changes in `lessons.md`.
8. New basket: from today's picks (excluding coins already held), size each leg so stop risk ≈ 1% of equity, leverage 5 (user rule 2026-10-11; stop at −9% or tighter),
   max 8 open positions. Call `suggest_trades_batch`, save the proposal, and ask the user to confirm the exact basket.
   The Liquid connector requires the user's explicit confirmation for every basket: never execute without a "да" to that exact basket.
9. Reply in the thread in Russian: picks with news verdict and their review rating, new lessons from settled ideas, open positions P&L, closed trades and lessons, the proposed
   basket and the question to confirm it; attach `daily/report_$D.md`.

10. Trades page (<https://claude.ai/artifact/GjmHGbnwyxmxmQ2evAMP2x>): after updating journal.csv, save `get_portfolio` as
   `daily/portfolio_$D.json` = `{"time": ISO-UTC, "equity": n, "positions": [{"coin", "mark", "upnl"}]}`, run
   `python3 dashboard/build_dashboard.py`, copy `dashboard/index.html` + `dashboard/data.json` into the scratchpad
   and publish with the Artifact tool to that URL (`url`, `files: {"data.json": ...}`); mention the link in the reply.

## Pending reminders
- 2026-10-19: propose closing the ZRO long (paper) before the 2026-10-20 token unlock (close_position preview + user confirmation).

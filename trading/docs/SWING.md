# Swing signals (strategy S): hourly procedure

Chosen by the user on 2026-10-11: 4h pullback in trend, 15m entry, stop 4%, take 6%, no trailing.
Signals go to the separate thread "Свинг-сигналы" (user asked 2026-10-11; not Telegram). Paper only; every trade needs the user's "да".

Each hourly run:
1. `get_portfolio`: list the held coins (any strategy) and the equity. Max 6 open strategy S positions.
2. 4h scan, only when `swing/setups.json` "updated" is older than the last 4h close (00/04/08/12/16/20 UTC):
   refresh `backtest/raw4h/<SYM>.json` for the 48 coins in universe.txt via `show_chart` (interval 4h, limit 100),
   using 4 parallel general-purpose workers of 12 coins each (tell them to call only show_chart and to write files
   verbatim as `{"symbol", "candles"}`; never hearthbot tools). Then
   `python3 swing_signals.py setups backtest/raw4h <held coins>`.
3. For each active setup in `swing/setups.json`: `show_chart` interval 15m, limit 60 → `swing/raw15m/<SYM>.json`.
   Then `python3 swing_signals.py entries swing/raw15m`.
4. For each entry signal: `suggest_trade` market order on perpetual futures, leverage 5 (user, 2026-10-11: futures only, 5x), notional = equity × 1% / 4% (≈25% of equity, margin ≈5%; the 4% stop = ~20% of margin = ~1% of equity),
   stop-loss and take-profit from the script, paper mode. Mark that setup `"used": true` in setups.json.
   Reply in Russian: coin, side, entry, SL −4%, TP +6%, why (4h RSI(3), trend), and ask "да?".
   On "да": `execute_order` with the exact previewed fields. If the price has moved more than 1.5% from the
   signal's entry by then, make a fresh preview and ask again. Log to journal.csv with strategy=S.
5. Nothing to report → no reply (no_reply_needed). Closed strategy S trades are reviewed in the daily run (DAILY.md step 7).

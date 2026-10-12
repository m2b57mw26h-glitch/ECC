# Strategy M5: weekly futures momentum (paper)

Chosen by the user on 2026-10-11 (top of TOP6.md). Futures only, leverage 5 (user rule). Every basket needs the user's "да".

Rules:
- Universe: universe.txt coins with >= $5M average daily volume over 14 days.
- Rank by 14-day return on the last closed daily candle; take the top 5.
- Skip a coin already held by another strategy (daily screener, B, swing S) and coins whose maxLeverage < 5; take the next one.
- Each position: $500 margin, 5x isolated ($2,500 notional), market order, stop -9% from entry (liquidation is ~-10% at 5x; never put the stop at -10% or further). No take-profit.
- Hold one week. Rebalance every Sunday.

Weekly rebalance (Sunday):
1. get_portfolio: list M5 positions (journal.csv strategy=M5, not closed) and other held coins.
2. Refresh backtest/raw daily candles for universe coins via show_chart interval 1d (same as DAILY.md), then rank with
   `python3 -I -c` using backtest/compare.py (load, prep, mom14) — see the 2026-10-11 thread for the snippet.
3. Coins in M5 that dropped out of the new top-5: close (close_positions_batch preview).
   Coins still in: keep, leave the stop. New coins: suggest_trades_batch with the rules above (analyze_markets_batch for maxLeverage and price).
4. Reply in Russian with closes + new basket in one message, ask "да". On "да", execute exactly the previewed actions.
5. Log closes and opens to journal.csv (strategy=M5).

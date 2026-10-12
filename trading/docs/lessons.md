# Lessons from losing trades

## 2026-10-11 — backtest (244 simulated trades, 26.03–10.10.2026), see backtest/REPORT.md
- Shorts against a rising BTC lose; shorts overall PF 0.9 and unstable between halves. → v2: BTC 20-day filter.
- Stop at 1 daily range is hit by noise (133/244 trades stopped). → v2: stop 1.5 ranges, target 3.
- Watch (not a rule yet): longs with 7d change < 10% lose (31% win); best longs at +10–20%.
- Rule version now: v2 (from 2026-10-12). Open paper positions from 2026-10-11 keep their v1 stops/targets.

## Live paper trades
Closed trades: 1 (0 wins, 1 loss). Change rules only after ≥10 closed trades and a repeating pattern.
- 2026-10-11 VVV short (A-v1): stop, −$100. Shorted at the bottom of the range after −25% in a week; alts bounced together. Watch: shorts with range_pos < 0.2 after a big drop (bounce risk).

## 2026-10-11 — idea from a reel: strategy B "RSI(3) pullback" (backtest/mr_backtest.py)
Rules: RSI(3) < 22, EMA100 rising vs 5 days ago, close ≥1% below 10-day VWAP, buy next open, exit when RSI(3) > 55; 1/8 equity, 1x.
Backtest on 48 coins (signals from ~July, after the EMA100 warm-up): 50 trades, 80% winners, +3.7% avg per trade, PF 4.4, +25% total, max drawdown 4.6%; positive in both halves; robust to RSI 15/30 and EMA50.
Risks: only 50 trades over ~100 days of a rising market; no stop in the original (worst trade −13.9%), so live version adds a −15% emergency stop.
Decision: added as a separate strategy B alongside v2 (paper), not merged into v2.

## 2026-10-11 — swing test on 4h candles (user's SL 4% / TP 6% / trail 8%)
48 coins, 199 4h bars (33 days), BTC daily SMA20 filter, risk 1%, max 6 open (backtest/swing4h.py).
- trend entries (momentum + EMA20/50): about −20% with every exit. Rejected.
- 4h breakout + volume: −5…−18%. Rejected.
- pullback in trend (RSI(3)<15 / >85, EMA20 above/below EMA50): SL4 TP6 +7.8%, PF 1.33, 44 trades, 50% winners, DD 7.3%, both halves positive.
  Trailing 8% made it worse (+1.6…+3.4%): with a 4% stop, a far trail gives profits back.
- The calm-coins filter (daily range ≤7%) leaves too few trades to judge.
Conclusion: swing = 4h pullback entries, SL 4%, TP 6%, no trailing. Only 33 days of data, so treat it as a paper-trading candidate, not a proven edge.

## 2026-10-12 — reset by the user
User closed everything to start clean ("позиции все закрой и начнём с чистого счёта"). Hourly swing signals and momentum were stopped; only the daily review remains.
Manual closes at 07:51 UTC (net of fees): ZRO +166.44, NEAR +87.57, SOL +31.98 (B), JUP −2.03, HYPE −6.63, PENGU −17.12, UNI −23.87, DOGE −48.50 → +187.84. Earlier: VVV −100.38 (stop), XMR +4.56 (B).
These manual closes are not rule outcomes; the loss tally for rule changes counts only stop/target/signal exits (so far: VVV).
Paper equity after reset: $9,618.20 (start $10,000; losses mostly from the momentum basket and fees).

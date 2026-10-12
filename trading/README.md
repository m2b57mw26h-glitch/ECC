# Trading

Scripts for the crypto trading project: daily screener, strategy signals,
backtests, dashboard and the order guard. Market data and orders go through
the Liquid Co-Invest connector; these scripts only read files and compute.

## Hard rules

- Paper trading only until the owner explicitly allows real money.
- Futures at 5x leverage, stop-loss at most 9% from entry (liquidation is ~10%).
- No order without the owner's explicit "да" for that exact order.

`order_guard.py` checks all three before any order is sent:

```bash
python3 trading/order_guard.py order.json approval.json   # exit 0 = allowed, 1 = refused
```

The approval file carries the fingerprint of the exact order the owner
approved (`order_fingerprint()` in `order_guard.py`); changing size, stop,
target, leverage or mode invalidates it.

## Layout

| Path | What it is |
| --- | --- |
| `order_guard.py` | Pre-send check of every order against the hard rules |
| `screener.py` | Daily screen of the 48-coin universe (`universe.txt`) |
| `mr_signals.py` | Strategy B, RSI(3) pullback signals |
| `swing_signals.py` | 4h/15m swing setups |
| `backtest/` | Backtests and strategy comparison |
| `dashboard/` | Builds the trades page from the journal |
| `nautilus/` | Nautilus Trader version of the swing strategy |
| `tradingview/` | Pine scripts of strategies B and S |
| `docs/` | Daily procedure, strategy notes, backtest reports, lessons |
| `tests/` | Unit tests |

Data (candles, journal, reports) stays in the project files, not in git.

## Tests

```bash
python3 -m unittest discover -s trading/tests
```

## Known gap

`docs/DAILY.md` still sizes baskets at leverage 2 and strategy B at leverage 1
with a -15% stop. The guard refuses those orders under the current 5x / -9%
rule, so the procedure needs updating before the next basket.

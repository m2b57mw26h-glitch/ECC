#!/usr/bin/env python3
"""Common backtest engine for porting TradingView strategies.

Data: TradingView daily candles of Binance USDT perpetuals (backtest/tv_daily, 41 coins, 2022-01-01..2026-10-10).
Same rules for every strategy:
- a signal on a day's close fills at the next day's open;
- futures at 5x leverage: hard stop at most 9% from the entry price (liquidation at 5x is about -10%);
  a strategy may ask for a tighter stop, never a wider one; a stop fills at the stop price or at the open on a gap;
- risk 1% of equity per trade: notional = 1% equity / stop distance, capped so margin (notional / 5) <= equity / 8;
- fee 0.1% of notional per side (covers commission and slippage); funding is ignored;
- up to 8 positions at once; only coins with >= $5M average daily dollar volume over the last 14 days;
- when more signals than free slots, the most liquid coins go first.

A strategy is a function sig(df) -> dict with numpy bool arrays of len(df):
  le (long entry), lx (long exit), se (short entry), sx (short exit)
and optionally stop (float array: stop distance as a fraction of the close, e.g. 0.05 = 5%).
df is a pandas DataFrame with columns t, o, h, l, c, v (one row per day, oldest first).
All arrays are evaluated on the bar's close; never look at future rows.
"""
import glob
import json
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "tv_daily")
FEE = 0.001
MAX_OPEN = 8
LEV = 5.0
MAX_STOP = 0.09
RISK = 0.01
MIN_DV = 5e6
START = 10000.0


def load(d=DATA):
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        j = json.load(open(p))
        df = pd.DataFrame(j["candles"])[["t", "o", "h", "l", "c", "v"]].astype(float)
        df["t"] = df["t"].astype(np.int64)
        out[j["symbol"]] = df.reset_index(drop=True)
    return out


def _arr(x, n):
    """Signal array -> bool array; None and NaN count as False."""
    if x is None:
        return np.zeros(n, bool)
    a = np.asarray(x)
    if a.dtype != bool:
        a = np.nan_to_num(a.astype(float), nan=0.0) != 0
    return a


def run(data, sig_fn, long_only=False, short_only=False):
    sigs, dv = {}, {}
    for s, df in data.items():
        r = sig_fn(df)
        n = len(df)
        sigs[s] = {k: _arr(r.get(k), n) for k in ("le", "lx", "se", "sx")}
        st = r.get("stop")
        sigs[s]["stop"] = np.full(n, MAX_STOP) if st is None else np.nan_to_num(np.asarray(st, float), nan=MAX_STOP)
        dv[s] = (df["c"] * df["v"]).rolling(14).mean().to_numpy()
    days = sorted({int(t) for df in data.values() for t in df["t"]})
    idx = {s: {int(t): i for i, t in enumerate(df["t"])} for s, df in data.items()}
    equity, peak, dd = START, START, 0.0
    pos, trades, pend_x, pend_e = {}, [], set(), []
    curve = []

    def close(s, px, day, why):
        nonlocal equity
        p = pos.pop(s)
        d = 1 if p["side"] == "long" else -1
        pnl = p["qty"] * (px - p["entry"]) * d - FEE * p["qty"] * (p["entry"] + px)
        equity += pnl
        trades.append({"sym": s, "side": p["side"], "pnl": pnl, "t": p["t"], "exit_t": day, "why": why,
                       "ret": (px / p["entry"] - 1) * d, "notional": p["qty"] * p["entry"]})

    for day in days:
        # 1) fills at today's open
        for s in list(pend_x):
            i = idx[s].get(day)
            if s in pos and i is not None:
                close(s, data[s]["o"].iat[i], day, "signal")
        pend_x.clear()
        for _, s, side, stop_pct in pend_e:
            i = idx[s].get(day)
            if i is None or s in pos or len(pos) >= MAX_OPEN:
                continue
            o = data[s]["o"].iat[i]
            stop_pct = min(max(stop_pct, 0.005), MAX_STOP)
            notional = min(RISK * equity / stop_pct, equity * LEV / MAX_OPEN)
            stop_px = o * (1 - stop_pct) if side == "long" else o * (1 + stop_pct)
            pos[s] = {"side": side, "entry": o, "qty": notional / o, "stop": stop_px, "t": day}
        pend_e = []
        # 2) intraday stops
        for s in list(pos):
            i = idx[s].get(day)
            if i is None:
                continue
            p, bar = pos[s], data[s].iloc[i]
            if p["side"] == "long" and bar["l"] <= p["stop"]:
                close(s, min(bar["o"], p["stop"]), day, "stop")
            elif p["side"] == "short" and bar["h"] >= p["stop"]:
                close(s, max(bar["o"], p["stop"]), day, "stop")
        # 3) close: mark to market, then signals for tomorrow's open
        mtm = equity
        for s, p in pos.items():
            i = idx[s].get(day)
            if i is not None:
                p["last"] = data[s]["c"].iat[i]
            mtm += p["qty"] * (p.get("last", p["entry"]) - p["entry"]) * (1 if p["side"] == "long" else -1)
        peak = max(peak, mtm)
        dd = max(dd, (peak - mtm) / peak if peak > 0 else 1.0)
        curve.append((day, mtm))
        if mtm <= 0:
            break
        for s, p in pos.items():
            i = idx[s].get(day)
            if i is None:
                continue
            if (p["side"] == "long" and sigs[s]["lx"][i]) or (p["side"] == "short" and sigs[s]["sx"][i]):
                pend_x.add(s)
        cands = []
        for s in data:
            i = idx[s].get(day)
            if i is None or i < 14 or (s in pos and s not in pend_x) or not (dv[s][i] >= MIN_DV):
                continue
            g = sigs[s]
            side = None
            if g["le"][i] and not short_only:
                side = "long"
            elif g["se"][i] and not long_only:
                side = "short"
            if side and s in pos and pos[s]["side"] == side:
                continue
            if side:
                cands.append((-dv[s][i], s, side, float(g["stop"][i])))
        free = MAX_OPEN - (len(pos) - len(pend_x))
        pend_e = sorted(cands)[:max(free, 0)]
    # close what is left at the last close
    last = days[-1]
    for s in list(pos):
        close(s, pos[s].get("last", pos[s]["entry"]), last, "end")
    return equity, dd, trades, curve


def year(t):
    return datetime.fromtimestamp(t, timezone.utc).year


def stats(eq, dd, tr, curve):
    w = [t["pnl"] for t in tr if t["pnl"] > 0]
    loss = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    rets = np.diff([c[1] for c in curve]) / np.array([c[1] for c in curve][:-1]) if len(curve) > 2 else np.array([0.0])
    years = {}
    prev = START
    for y in sorted({year(c[0]) for c in curve}):
        end = [c[1] for c in curve if year(c[0]) == y][-1]
        years[y] = round(float(end / prev - 1) * 100, 1)
        prev = end
    n_years = (curve[-1][0] - curve[0][0]) / 86400 / 365.25 if curve else 1
    side = lambda sd: round(sum(t["pnl"] for t in tr if t["side"] == sd))
    return {
        "ret_pct": round((eq / START - 1) * 100, 1),
        "cagr_pct": round(((max(eq, 1) / START) ** (1 / n_years) - 1) * 100, 1) if n_years > 0 else None,
        "max_dd_pct": round(dd * 100, 1),
        "n": len(tr),
        "win_pct": round(len(w) / len(tr) * 100) if tr else None,
        "pf": round(sum(w) / loss, 2) if loss else None,
        "sharpe": round(float(rets.mean() / rets.std() * math.sqrt(365)), 2) if rets.std() > 0 else None,
        "stops_pct": round(sum(t["why"] == "stop" for t in tr) / len(tr) * 100) if tr else None,
        "long_pnl": side("long"), "short_pnl": side("short"),
        "years": years,
    }


def benchmarks(data):
    out = {}
    btc = data["BTC"]
    out["Buy & hold BTC (1x)"] = round((btc["c"].iat[-1] / btc["o"].iat[0] - 1) * 100, 1)
    old = [s for s, df in data.items() if df["t"].iat[0] <= data["BTC"]["t"].iat[0]]
    out[f"Buy & hold {len(old)} old coins equal weight (1x)"] = round(
        np.mean([data[s]["c"].iat[-1] / data[s]["o"].iat[0] - 1 for s in old]) * 100, 1)
    return out

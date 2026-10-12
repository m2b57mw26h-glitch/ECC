#!/usr/bin/env python3
"""Weekly cross-sectional momentum on 4.7 years of daily Binance perps (backtest/tv_daily).

Every 7 days, on the day's close: rank coins with >= $5M average daily dollar volume (14 days)
by 14-day return; buy the top 5 (and short the bottom 5 in the long/short variant) at the next open,
hold until the next rebalance open, then close everything and reopen the new selection
(same mechanics as compare.py `rebalancer` + `mom14`, so a coin that stays in the top 5 pays fees again).

Rules (the user's live rules): futures 5x, hard stop 9% from entry (fills at the stop or the open on a gap),
loss of one position capped at its margin (isolated margin, liquidation), fee 0.1% of notional per side,
funding ignored. Equity is marked to market at every daily close; sizes are set from equity at each rebalance.
Survivorship bias: the 41 coins are today's liquid perps.

Run: python3 momentum_long.py   (writes results_momentum_long.json next to this file)
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import load  # noqa: E402

FEE = 0.001
STOP = 0.09
LEV = 5.0
MIN_DV = 5e6
START = 10000.0
N = 5
EVERY = 7
WARMUP = 50  # first signal day index (BTC SMA50 available)

VARIANTS = {
    "1. 1x, equity/5 per coin, no stop": dict(frac=0.20, lev=1.0, stop=None),
    "2. 5x, margin 5%/coin (notional 25%), stop 9%": dict(frac=0.25, lev=5.0, stop=STOP),
    "3. 5x, margin 2%/coin (notional 10%), stop 9%": dict(frac=0.10, lev=5.0, stop=STOP),
    "4. = 2 + BTC > SMA50 filter": dict(frac=0.25, lev=5.0, stop=STOP, btc_gate=True),
    "5. = 2 long/short 5+5": dict(frac=0.25, lev=5.0, stop=STOP, short=True),
}


def ts_year(t):
    return datetime.fromtimestamp(int(t), timezone.utc).year


def prepare(data):
    days = sorted({int(t) for df in data.values() for t in df["t"]})
    D, S = len(days), list(data)
    pos = {d: k for k, d in enumerate(days)}
    O, H, L, C, DV = (np.full((D, len(S)), np.nan) for _ in range(5))
    for j, s in enumerate(S):
        df = data[s]
        r = [pos[int(t)] for t in df["t"]]
        O[r, j], H[r, j], L[r, j], C[r, j] = df["o"], df["h"], df["l"], df["c"]
        DV[r, j] = (df["c"] * df["v"]).rolling(14).mean().to_numpy()
    MOM = C / np.roll(C, 14, axis=0) - 1
    MOM[:14] = np.nan
    b = S.index("BTC")
    btc_sma = np.convolve(C[:, b], np.ones(50) / 50, "full")[:D]
    btc_sma[:49] = np.nan
    return dict(days=days, S=S, O=O, H=H, L=L, C=C, DV=DV, MOM=MOM, btc=b, btc_sma=btc_sma)


def simulate(P, frac, lev, stop, short=False, btc_gate=False):
    days, O, H, L, C = P["days"], P["O"], P["H"], P["L"], P["C"]
    D = len(days)
    cash = START  # realized equity
    book = []  # dicts: j, side(+1/-1), qty, entry, stop, margin
    trades, curve = [], []
    reb_days = set(range(WARMUP, D - 1, EVERY))

    def close(p, px, k, why):
        nonlocal cash
        pnl = p["qty"] * (px - p["entry"]) * p["side"] - FEE * p["qty"] * (p["entry"] + px)
        if lev > 1:
            pnl = max(pnl, -p["margin"])  # isolated margin: cannot lose more than the margin
        cash += pnl
        trades.append({"sym": P["S"][p["j"]], "side": "long" if p["side"] > 0 else "short", "pnl": pnl,
                       "t": days[k], "why": why})

    pending = None  # selection made on yesterday's close
    for k in range(WARMUP, D):
        # 1) rebalance at today's open
        if pending is not None:
            for p in book:
                px = O[k, p["j"]]
                close(p, px if not np.isnan(px) else p["last"], k, "rebalance")
            book = []
            eq = cash
            for j, side in pending:
                e = O[k, j]
                if np.isnan(e) or eq <= 0:
                    continue
                notional = frac * eq
                st = None if stop is None else e * (1 - stop * side)
                book.append({"j": j, "side": side, "qty": notional / e, "entry": e, "stop": st,
                             "margin": notional / lev, "last": e})
            pending = None
        # 2) intraday stops
        keep = []
        for p in book:
            j = p["j"]
            if p["stop"] is not None and not np.isnan(L[k, j]):
                if p["side"] > 0 and L[k, j] <= p["stop"]:
                    close(p, min(O[k, j], p["stop"]), k, "stop")
                    continue
                if p["side"] < 0 and H[k, j] >= p["stop"]:
                    close(p, max(O[k, j], p["stop"]), k, "stop")
                    continue
            keep.append(p)
        book = keep
        # 3) mark to market at the close
        mtm = cash
        for p in book:
            if not np.isnan(C[k, p["j"]]):
                p["last"] = C[k, p["j"]]
            u = p["qty"] * (p["last"] - p["entry"]) * p["side"]
            mtm += max(u, -p["margin"]) if lev > 1 else u
        curve.append((days[k], mtm))
        if mtm <= 0:
            break
        # 4) signal for tomorrow's open
        if k in reb_days:
            ok = (P["DV"][k] >= MIN_DV) & ~np.isnan(P["MOM"][k]) & ~np.isnan(O[k + 1])
            js = np.where(ok)[0]
            js = js[np.argsort(-P["MOM"][k, js])]
            sel = [(int(j), 1) for j in js[:N]]
            if short:
                sel += [(int(j), -1) for j in js[-N:] if int(j) not in {a for a, _ in sel}]
            if btc_gate and not (C[k, P["btc"]] >= P["btc_sma"][k]):
                sel = [x for x in sel if x[1] < 0]
            pending = sel
    for p in book:
        close(p, p["last"], D - 1, "end")
    return cash, trades, curve


def report(eq, trades, curve):
    vals = np.array([c[1] for c in curve])
    peak = np.maximum.accumulate(np.r_[START, vals])[1:]
    dd = float(np.max((peak - vals) / peak))
    years = {}
    prev = START
    for y in sorted({ts_year(c[0]) for c in curve}):
        end = [c[1] for c in curve if ts_year(c[0]) == y][-1]
        years[y] = round((end / prev - 1) * 100, 1)
        prev = end
    n_years = (curve[-1][0] - curve[0][0]) / 86400 / 365.25
    w = [t["pnl"] for t in trades if t["pnl"] > 0]
    loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {
        "ret_pct": round((eq / START - 1) * 100, 1),
        "cagr_pct": round(((max(eq, 1e-9) / START) ** (1 / n_years) - 1) * 100, 1),
        "max_dd_pct": round(dd * 100, 1),
        "trades": len(trades),
        "win_pct": round(len(w) / len(trades) * 100, 1) if trades else None,
        "pf": round(sum(w) / loss, 2) if loss else None,
        "stops": sum(t["why"] == "stop" for t in trades),
        "long_pnl": round(sum(t["pnl"] for t in trades if t["side"] == "long")),
        "short_pnl": round(sum(t["pnl"] for t in trades if t["side"] == "short")),
        "years": years,
    }


def btc_hold(P):
    b, days = P["btc"], P["days"]
    e = P["O"][WARMUP, b]
    curve = [(days[k], START * P["C"][k, b] / e * (1 - FEE)) for k in range(WARMUP, len(days))]
    r = report(curve[-1][1] * (1 - FEE), [], curve)
    r["trades"] = 1
    return r


def main():
    P = prepare(load())
    res = {"period": [datetime.fromtimestamp(P["days"][WARMUP], timezone.utc).date().isoformat(),
                      datetime.fromtimestamp(P["days"][-1], timezone.utc).date().isoformat()]}
    for name, kw in VARIANTS.items():
        res[name] = report(*simulate(P, **kw))
    res["Buy & hold BTC (1x)"] = btc_hold(P)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_momentum_long.json")
    json.dump(res, open(out, "w"), indent=1)
    print("period", res["period"])
    hdr = f"{'variant':48} {'ret%':>8} {'CAGR':>6} {'maxDD':>6} {'trd':>5} {'win%':>5} {'PF':>5} {'stops':>5}  years"
    print(hdr)
    for name, r in res.items():
        if name == "period":
            continue
        ys = " ".join(f"{y}:{v:+.0f}" for y, v in r["years"].items())
        print(f"{name:48} {r['ret_pct']:8.1f} {r['cagr_pct']:6.1f} {r['max_dd_pct']:6.1f} {r['trades']:5} "
              f"{r['win_pct'] if r['win_pct'] is not None else '-':>5} {r['pf'] if r['pf'] else '-':>5} {r['stops']:5}  {ys}"
              + (f"  L/S pnl {r['long_pnl']}/{r['short_pnl']}" if r['short_pnl'] else ""))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Swing backtest on 4h candles with percent stops (user's rules: stop 4%, take 6%, trailing 8%).

Usage: python3 swing4h.py <raw4h_dir> <raw_daily_dir>
Signals are computed on a closed 4h bar and filled at the next bar's open. Exits are checked bar by bar
(stop first if a bar touches both levels). Risk 1% of equity per trade (notional = 1% / stop%), max 6 open.
Market filter: longs only when BTC's daily close is above its 20-day average, shorts only below (as in v2).
"""
import glob, json, os, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import manage_pct, FEE  # noqa: E402
from mr_backtest import rsi, ema  # noqa: E402

RISK, MAX_OPEN, WARM = 0.01, 6, 50
TODAY_MS = 1791676800000


def load(d, completed_before=None):
    out = {}
    for p in glob.glob(os.path.join(d, "*.json")):
        j = json.load(open(p))
        cs = [{k: (int(v) if k == "timestamp" else float(v)) for k, v in c.items()} for c in j["candles"]]
        out[j["symbol"]] = cs[:-1]          # drop the bar still forming
    return out


def btc_daily_filter(daily_dir):
    b = load(daily_dir)["BTC"]
    f = {}
    for i in range(19, len(b)):
        f[b[i]["timestamp"] // 86400000] = b[i]["close"] > statistics.mean(x["close"] for x in b[i - 19: i + 1])
    return f


def prep(cs):
    closes = [c["close"] for c in cs]
    vols = [c["volume"] * c["close"] for c in cs]
    return {"c": closes, "e20": ema(closes, 20), "e50": ema(closes, 50), "r3": rsi(closes, 3), "r14": rsi(closes, 14), "v": vols}


def signal(cs, ind, i, rule):
    c, e20, e50 = ind["c"], ind["e20"], ind["e50"]
    up, down = c[i] > e50[i] and e20[i] > e50[i], c[i] < e50[i] and e20[i] < e50[i]
    vol_avg = statistics.mean(ind["v"][i - 42: i])
    if rule == "trend":            # 4h port of the daily screener: momentum + trend + range position
        mom = c[i] / c[i - 42] - 1
        hi = max(x["high"] for x in cs[i - 42: i + 1]); lo = min(x["low"] for x in cs[i - 42: i + 1])
        pos = (c[i] - lo) / (hi - lo) if hi > lo else 0.5
        if up and mom > 0.03 and pos > 0.6: return "long"
        if down and mom < -0.03 and pos < 0.4: return "short"
    elif rule == "breakout":       # close beyond the last 20 bars' extreme on a volume spike, with the trend
        hh = max(x["high"] for x in cs[i - 20: i]); ll = min(x["low"] for x in cs[i - 20: i])
        spike = ind["v"][i] > 1.5 * vol_avg
        if up and c[i] > hh and spike: return "long"
        if down and c[i] < ll and spike: return "short"
    elif rule == "pullback":       # dip inside a trend: RSI(3) washed out while EMA20 > EMA50
        r = ind["r3"][i]
        if r is not None and up and r < 15: return "long"
        if r is not None and down and r > 85: return "short"
    return None


def run(data, btc_up, p):
    bars = sorted({c["timestamp"] for cs in data.values() for c in cs})
    idx = {s: {c["timestamp"]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    ind = {s: prep(cs) for s, cs in data.items()}
    med_range = {s: statistics.median((c["high"] - c["low"]) / c["open"] * 100 for c in cs) * 6 for s, cs in data.items()}
    equity = peak = 10000.0; max_dd = 0.0
    open_pos, trades = [], []
    for bi, t in enumerate(bars[:-1]):
        for pos in list(open_pos):
            i = idx[pos["sym"]].get(t)
            if i is None or t < pos["entry_t"]:
                continue
            c = data[pos["sym"]][i]
            pos["bars"] += 1
            px, why, equity = manage_pct(pos, c, p, equity)
            if px is None and pos["bars"] >= p["max_bars"]:
                px, why = c["close"], "time"
            if px is not None:
                d = 1 if pos["side"] == "long" else -1
                pnl = pos["qty"] * (px - pos["entry"]) * d - FEE * pos["qty"] * (pos["entry"] + px)
                equity += pnl
                total = pnl + pos["banked"]
                trades.append({"sym": pos["sym"], "side": pos["side"], "pnl": total, "R": total / pos["risk_usd"],
                               "reason": why, "bars": pos["bars"], "t": pos["entry_t"]})
                open_pos.remove(pos)
        peak = max(peak, equity); max_dd = max(max_dd, (peak - equity) / peak)
        nxt = bars[bi + 1]
        flt = btc_up.get(t // 86400000 - 1)           # yesterday's daily close: known at this bar
        for s, cs in data.items():
            if len(open_pos) >= MAX_OPEN:
                break
            i = idx[s].get(t)
            if i is None or i < WARM or nxt not in idx[s] or any(o["sym"] == s for o in open_pos):
                continue
            if statistics.mean(ind[s]["v"][i - 42: i]) * 6 < 5e6:
                continue
            if p.get("max_daily_range") and med_range[s] > p["max_daily_range"]:
                continue
            side = signal(cs, ind[s], i, p["rule"])
            if side is None or (flt is not None and (side == "long") != flt):
                continue
            o = data[s][idx[s][nxt]]["open"]; d = 1 if side == "long" else -1
            risk_usd = equity * RISK
            open_pos.append({"sym": s, "side": side, "entry": o, "entry_t": nxt, "bars": 0,
                             "sl": o * (1 - d * p["sl_pct"]), "tp": o * (1 + d * p["tp_pct"]) if p.get("tp_pct") else None,
                             "qty": risk_usd / (p["sl_pct"] * o), "risk_usd": risk_usd, "best": o, "banked": 0.0, "half": False})
    return equity, max_dd, trades


def summ(tr):
    if not tr:
        return "n=0"
    w = [x for x in tr if x["pnl"] > 0]; gl = -sum(x["pnl"] for x in tr if x["pnl"] <= 0)
    return (f"n={len(tr)} win={len(w)/len(tr)*100:.0f}% avgR={statistics.mean(x['R'] for x in tr):+.2f} "
            f"PF={(sum(x['pnl'] for x in w)/gl if gl else 99):.2f} pnl={sum(x['pnl'] for x in tr):+.0f}")


EXITS = {
    "SL4 TP6": {"sl_pct": 0.04, "tp_pct": 0.06, "mode": "fixed"},
    "SL4 TP6 half+trail8": {"sl_pct": 0.04, "tp_pct": 0.06, "trail_pct": 0.08, "mode": "partial"},
    "SL4 trail8 from +8%": {"sl_pct": 0.04, "trail_pct": 0.08, "trail_after": 0.08, "mode": "trail"},
    "SL4 TP6 half+trail4": {"sl_pct": 0.04, "tp_pct": 0.06, "trail_pct": 0.04, "mode": "partial"},
}

if __name__ == "__main__":
    data = load(sys.argv[1]); btc_up = btc_daily_filter(sys.argv[2])
    bars = sorted({c["timestamp"] for cs in data.values() for c in cs})
    mid = bars[WARM + (len(bars) - WARM) // 2]
    print(f"coins={len(data)} bars={len(bars)} ({(bars[-1]-bars[0])/86400000:.0f} days)")
    for rule in ("trend", "breakout", "pullback"):
        for vf in (None, 7.0):
            for name, ex in EXITS.items():
                p = {"rule": rule, "max_bars": 42, "max_daily_range": vf, **ex}
                eq, dd, tr = run(data, btc_up, p)
                h1 = [x for x in tr if x["t"] < mid]; h2 = [x for x in tr if x["t"] >= mid]
                tag = f"{rule:8s} {'calm coins' if vf else 'all coins':10s} {name:20s}"
                print(f"{tag} ret={(eq/1e4-1)*100:+6.1f}% DD={dd*100:4.1f}% | {summ(tr)} | H1 {summ(h1)} | H2 {summ(h2)}")

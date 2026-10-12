#!/usr/bin/env python3
"""Backtest of the "RSI(3) pullback" idea (Instagram reel, QQQ rules) on crypto daily candles.

Rules (original): RSI(3) closes < 22; EMA100 above its value 5 days ago; close >= 1% below 10-day rolling VWAP;
buy at next open; exit when RSI(3) closes > 55 (sold at the next open). One position per coin.
Portfolio: up to MAX_OPEN coins at once, each EQUITY/MAX_OPEN notional (1x), fees 0.1% per side.
Usage: python3 mr_backtest.py <raw_dir>
"""
import glob, json, os, statistics, sys

FEE = 0.001
MAX_OPEN = 8
TODAY_MS = 1791676800000


def load(raw_dir):
    data = {}
    for p in glob.glob(os.path.join(raw_dir, "*.json")):
        d = json.load(open(p))
        cs = [{k: (int(v) if k == "timestamp" else float(v)) for k, v in c.items()} for c in d["candles"]]
        data[d["symbol"]] = [c for c in cs if c["timestamp"] < TODAY_MS]
    return data


def rsi(closes, n=3):
    out = [None] * len(closes)
    gains = losses = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if i <= n:
            gains += g / n; losses += l / n
        else:
            gains = (gains * (n - 1) + g) / n; losses = (losses * (n - 1) + l) / n
        if i >= n:
            out[i] = 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    return out


def ema(xs, n):
    k, out, e = 2 / (n + 1), [], xs[0]
    for x in xs:
        e = x * k + e * (1 - k); out.append(e)
    return out


def indicators(cs, p):
    closes = [c["close"] for c in cs]
    r = rsi(closes, p["rsi_n"])
    e = ema(closes, p["ema_n"])
    vwap = [None] * len(cs)
    for i in range(9, len(cs)):
        w = cs[i - 9: i + 1]
        v = sum(c["volume"] for c in w)
        vwap[i] = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in w) / v if v else None
    return closes, r, e, vwap


def run(data, p, btc_up_by_day=None):
    days = sorted({c["timestamp"] for cs in data.values() for c in cs})
    idx = {s: {c["timestamp"]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    ind = {s: indicators(cs, p) for s, cs in data.items()}
    equity, peak, max_dd = 10000.0, 10000.0, 0.0
    open_pos, trades, pending_exit = {}, [], set()
    for di, day in enumerate(days[:-1]):
        nxt = days[di + 1]
        # exits decided on today's close, executed at next open
        for s in list(open_pos):
            i = idx[s].get(day)
            if i is None:
                continue
            pos = open_pos[s]
            closes, r, _, _ = ind[s]
            pos["days"] += 1
            hit_stop = p["stop_pct"] and closes[i] <= pos["entry"] * (1 - p["stop_pct"])
            if (r[i] is not None and r[i] > p["exit_rsi"]) or hit_stop or pos["days"] >= p["max_hold"]:
                j = idx[s].get(nxt)
                if j is None:
                    continue
                exit_px = data[s][j]["open"]
                pnl = pos["qty"] * (exit_px - pos["entry"]) - FEE * pos["qty"] * (pos["entry"] + exit_px)
                equity += pnl
                trades.append({"sym": s, "entry": pos["entry"], "exit": exit_px, "pnl": pnl,
                               "ret_pct": (exit_px / pos["entry"] - 1) * 100 - 0.2, "days": pos["days"],
                               "open_day": pos["day"], "reason": "stop" if hit_stop else "rsi" if r[i] > p["exit_rsi"] else "time"})
                del open_pos[s]
        # mark-to-market drawdown
        mtm = equity + sum(o["qty"] * (data[s][idx[s][day]]["close"] - o["entry"]) for s, o in open_pos.items() if day in idx[s])
        peak = max(peak, mtm); max_dd = max(max_dd, (peak - mtm) / peak)
        # entries
        if p["btc_filter"] and btc_up_by_day is not None and btc_up_by_day.get(day) is False:
            continue
        cands = []
        for s, cs in data.items():
            i = idx[s].get(day)
            if s in open_pos or i is None or i < p["ema_n"] or nxt not in idx[s]:
                continue
            closes, r, e, vwap = ind[s]
            vol_usd = statistics.mean(c["volume"] * c["close"] for c in cs[i - 13: i + 1])
            if vol_usd < 5e6 or r[i] is None or vwap[i] is None:
                continue
            if r[i] < p["entry_rsi"] and e[i] > e[i - 5] and closes[i] <= vwap[i] * (1 - p["vwap_gap"]):
                cands.append((r[i], s))
        for _, s in sorted(cands):
            if len(open_pos) >= MAX_OPEN:
                break
            o = data[s][idx[s][nxt]]["open"]
            open_pos[s] = {"entry": o, "qty": (equity / MAX_OPEN) / o, "days": 0, "day": nxt}
    return equity, max_dd, trades


def summary(trades):
    if not trades:
        return "n=0"
    w = [t for t in trades if t["pnl"] > 0]
    gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return (f"n={len(trades)} win={len(w)/len(trades)*100:.0f}% avg={statistics.mean(t['ret_pct'] for t in trades):+.2f}% "
            f"PF={(sum(t['pnl'] for t in w)/gl if gl else float('inf')):.2f} pnl={sum(t['pnl'] for t in trades):+.0f} "
            f"worst={min(t['ret_pct'] for t in trades):+.1f}% hold={statistics.mean(t['days'] for t in trades):.1f}d")


BASE = {"rsi_n": 3, "entry_rsi": 22, "exit_rsi": 55, "ema_n": 100, "vwap_gap": 0.01,
        "stop_pct": 0, "max_hold": 999, "btc_filter": False}
VARIANTS = {
    "original": {},
    "ema50": {"ema_n": 50},
    "rsi<15": {"entry_rsi": 15},
    "rsi<30": {"entry_rsi": 30},
    "exit_rsi>70": {"exit_rsi": 70},
    "stop15%": {"stop_pct": 0.15},
    "max_hold10": {"max_hold": 10},
    "btc_filter": {"btc_filter": True},
    "ema50_btc_filter": {"ema_n": 50, "btc_filter": True},
}

if __name__ == "__main__":
    data = load(sys.argv[1])
    btc = data["BTC"]
    btc_up = {c["timestamp"]: (c["close"] > statistics.mean(x["close"] for x in btc[i - 19: i + 1]) if i >= 19 else None)
              for i, c in enumerate(btc)}
    mid = 1774483200000 + 150 * 86400000   # signals start ~day 100, so split the tradable part in two
    for name, ov in VARIANTS.items():
        p = {**BASE, **ov}
        eq, dd, tr = run(data, p, btc_up)
        h1 = [t for t in tr if t["open_day"] < mid]; h2 = [t for t in tr if t["open_day"] >= mid]
        print(f"{name:17s} ret={(eq/10000-1)*100:+.1f}% maxDD={dd*100:.1f}% | {summary(tr)}\n"
              f"{'':17s} H1: {summary(h1)}\n{'':17s} H2: {summary(h2)}")

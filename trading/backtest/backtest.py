#!/usr/bin/env python3
"""Backtest of the daily screener rules on daily candles.

Usage: python3 backtest.py <raw_dir> [--json out.json]
Each day t (after the close): score every coin with the same rules as screener.py, using only candles up to t.
Enter picks at the next day's open; SL/TP from the 7-day average range; exits checked on daily high/low
(if SL and TP are both touched in one candle, SL is assumed: conservative). Time exit after MAX_HOLD days.
Positioning bias is not available historically, so that part of the score is off.
"""
import glob, json, os, statistics, sys

FEE = 0.001          # per side: Liquid 0.05% + exchange taker ~0.045%
RISK = 0.01          # equity risked per trade
MAX_OPEN = 8
MAX_HOLD = 14
TODAY_MS = 1791676800000   # 2026-10-11 00:00 UTC
WINDOW = 15          # same 15 candles the live screener sees (14 complete days + today)


def load(raw_dir):
    data = {}
    for p in glob.glob(os.path.join(raw_dir, "*.json")):
        d = json.load(open(p))
        cs = [{k: (int(v) if k == "timestamp" else float(v)) for k, v in c.items()} for c in d["candles"]]
        data[d["symbol"]] = [c for c in cs if c["timestamp"] < TODAY_MS]   # drop today's unfinished candle
    return data


def features(c, t):
    """Screener features on day t using candles[t-14..t] (t = last completed day, close = 'price')."""
    full = c[t - WINDOW + 2: t + 1]          # 14 completed days ending at t
    closes = [x["close"] for x in full]
    px = closes[-1]
    lo = min(x["low"] for x in full); hi = max(x["high"] for x in full)
    vols = [x["volume"] * x["close"] for x in full]
    ranges = [(x["high"] - x["low"]) / x["close"] * 100 for x in full]
    return {
        "price": px,
        "chg24_pct": (px / closes[-2] - 1) * 100,
        "chg7d_pct": (px / closes[-8] - 1) * 100,
        "vol_usd_avg": statistics.mean(vols),
        "vol_ratio": vols[-1] / statistics.mean(vols) if statistics.mean(vols) else 0,
        "atr_pct": statistics.mean(ranges[-7:]),
        "range_pos": (px - lo) / (hi - lo) if hi > lo else 0.5,
        "above_sma14": px > statistics.mean(closes),
    }


def score(r, p):
    s = max(-3, min(3, r["chg7d_pct"] / 5))
    s += 1 if r["above_sma14"] else -1
    s += (r["range_pos"] - 0.5) * 2
    if r["vol_ratio"] > 1.3:
        s += 0.5 if s > 0 else -0.5
    return s


def run(data, p):
    btc = data.get("BTC")
    days = sorted({c["timestamp"] for cs in data.values() for c in cs})
    idx = {s: {c["timestamp"]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    equity, peak, max_dd = 10000.0, 10000.0, 0.0
    open_pos, trades, curve = [], [], []
    for di, day in enumerate(days[:-1]):
        # 1) manage open positions on this day's candle
        for pos in list(open_pos):
            i = idx[pos["sym"]].get(day)
            if i is None or day <= pos["entry_day"] - 1:
                continue
            c = data[pos["sym"]][i]
            exit_px = reason = None
            if p.get("sl_pct"):
                exit_px, reason, equity = manage_pct(pos, c, p, equity)
            elif pos["side"] == "long":
                if c["low"] <= pos["sl"]: exit_px, reason = min(pos["sl"], c["open"]), "stop"
                elif c["high"] >= pos["tp"]: exit_px, reason = max(pos["tp"], c["open"]), "target"
            elif pos["side"] == "short":
                if c["high"] >= pos["sl"]: exit_px, reason = max(pos["sl"], c["open"]), "stop"
                elif c["low"] <= pos["tp"]: exit_px, reason = min(pos["tp"], c["open"]), "target"
            pos["days"] += 1
            if exit_px is None and pos["days"] >= p["max_hold"]:
                exit_px, reason = c["close"], "time"
            if exit_px is not None:
                d = 1 if pos["side"] == "long" else -1
                pnl = pos["qty"] * (exit_px - pos["entry"]) * d - FEE * pos["qty"] * (pos["entry"] + exit_px)
                equity += pnl
                pnl += pos.get("banked", 0.0)
                trades.append({**{k: pos[k] for k in ("sym", "side", "entry", "score", "atr_pct", "chg7d", "range_pos")},
                               "exit": exit_px, "reason": reason, "pnl": pnl, "R": pnl / pos["risk_usd"], "days": pos["days"],
                               "open_day": pos["entry_day"], "btc_up": pos["btc_up"]})
                open_pos.remove(pos)
        peak = max(peak, equity); max_dd = max(max_dd, (peak - equity) / peak)
        curve.append(equity)

        # 2) screen on day's close, enter next day's open
        nxt = days[di + 1]
        btc_up = None
        if btc and day in idx["BTC"] and idx["BTC"][day] >= 20:
            bi = idx["BTC"][day]
            btc_up = btc[bi]["close"] > statistics.mean(x["close"] for x in btc[bi - 19: bi + 1])
        cands = []
        for s, cs in data.items():
            i = idx[s].get(day)
            if i is None or i < WINDOW or nxt not in idx[s]:
                continue
            if any(o["sym"] == s for o in open_pos):
                continue
            r = features(cs, i)
            if r["vol_usd_avg"] < p["min_vol"] or abs(r["chg24_pct"]) >= 20:
                continue
            sc = score(r, p)
            side = "long" if sc >= p["thr"] else "short" if sc <= -p["thr"] else None
            if side is None or side not in p["sides"]:
                continue
            if p["btc_filter"] and btc_up is not None and ((side == "long") != btc_up):
                continue
            cands.append((abs(sc), s, side, r, sc))
        cands.sort(reverse=True)
        longs = [x for x in cands if x[2] == "long"][:5]
        shorts = [x for x in cands if x[2] == "short"][:5]
        for _, s, side, r, sc in sorted(longs + shorts, reverse=True):
            if len(open_pos) >= MAX_OPEN:
                break
            o = data[s][idx[s][nxt]]["open"]
            a = r["atr_pct"] / 100 * o
            d = 1 if side == "long" else -1
            sl, tp = o - d * p["sl_atr"] * a, o + d * p["tp_atr"] * a
            risk_usd = equity * RISK
            qty = risk_usd / (p["sl_atr"] * a)
            if p.get("sl_pct"):
                sl = o * (1 - d * p["sl_pct"])
                tp = o * (1 + d * p["tp_pct"]) if p.get("tp_pct") else None
                qty = risk_usd / (p["sl_pct"] * o)
            open_pos.append({"sym": s, "side": side, "entry": o, "sl": sl, "tp": tp, "qty": qty, "risk_usd": risk_usd,
                             "entry_day": nxt, "days": 0, "score": sc, "atr_pct": r["atr_pct"], "chg7d": r["chg7d_pct"],
                             "range_pos": r["range_pos"], "btc_up": btc_up, "best": o, "banked": 0.0, "half": False})
    return equity, max_dd, trades, curve


def manage_pct(pos, c, p, equity):
    """Percent-based exits. mode 'fixed': stop/target. 'trail': stop, then trailing stop trail_pct from the best price
    once the gain reaches trail_after. 'partial': close half at the target, move the rest's stop to breakeven and
    trail it trail_pct from the best price. Stops are checked before the day's new extreme (conservative)."""
    d = 1 if pos["side"] == "long" else -1
    hit_stop = (c["low"] <= pos["sl"]) if d == 1 else (c["high"] >= pos["sl"])
    if hit_stop:
        px = min(pos["sl"], c["open"]) if d == 1 else max(pos["sl"], c["open"])
        return px, ("trail" if pos.get("trailing") else "stop"), equity
    fav = c["high"] if d == 1 else c["low"]
    mode = p["mode"]
    if pos["tp"] is not None and ((fav >= pos["tp"]) if d == 1 else (fav <= pos["tp"])):
        if mode == "fixed":
            return (max(pos["tp"], c["open"]) if d == 1 else min(pos["tp"], c["open"])), "target", equity
        if mode == "partial" and not pos["half"]:
            q = pos["qty"] / 2
            pnl = q * (pos["tp"] - pos["entry"]) * d - FEE * q * (pos["entry"] + pos["tp"])
            equity += pnl; pos["banked"] += pnl; pos["qty"] -= q; pos["half"] = True
            pos["sl"] = pos["entry"]; pos["tp"] = None
    pos["best"] = max(pos["best"], fav) if d == 1 else min(pos["best"], fav)
    gain = (pos["best"] / pos["entry"] - 1) * d
    if p.get("trail_pct") and (mode == "partial" and pos["half"] or mode == "trail" and gain >= p.get("trail_after", 0)):
        t = pos["best"] * (1 - d * p["trail_pct"])
        if (d == 1 and t > pos["sl"]) or (d == -1 and t < pos["sl"]):
            pos["sl"] = t; pos["trailing"] = True
    return None, None, equity


def stats(trades):
    if not trades:
        return {"n": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in wins); gl = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {"n": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 1),
            "avg_R": round(statistics.mean(t["R"] for t in trades), 3),
            "profit_factor": round(gp / gl, 2) if gl else None,
            "pnl": round(sum(t["pnl"] for t in trades), 0)}


BASE = {"thr": 1.5, "sl_atr": 1.0, "tp_atr": 2.0, "max_hold": MAX_HOLD, "min_vol": 5e6,
        "sides": ("long", "short"), "btc_filter": False}

VARIANTS = {
    "current": {},
    "longs_only": {"sides": ("long",)},
    "shorts_only": {"sides": ("short",)},
    "btc_filter": {"btc_filter": True},
    "wider_stop_1.5_3": {"sl_atr": 1.5, "tp_atr": 3.0},
    "tight_1_1.5": {"sl_atr": 1.0, "tp_atr": 1.5},
    "strong_signal_thr3": {"thr": 3.0},
    "btc_filter_wider": {"btc_filter": True, "sl_atr": 1.5, "tp_atr": 3.0},
    "btc_filter_thr3": {"btc_filter": True, "thr": 3.0},
    "longs_btc_filter_wider": {"sides": ("long",), "btc_filter": True, "sl_atr": 1.5, "tp_atr": 3.0},
    "longs_wider": {"sides": ("long",), "sl_atr": 1.5, "tp_atr": 3.0},
    "longs_btc_filter": {"sides": ("long",), "btc_filter": True},
}

SWING = {
    "v2 (ATR stops)": {"btc_filter": True, "sl_atr": 1.5, "tp_atr": 3.0},
    "SL4 TP6": {"btc_filter": True, "sl_pct": 0.04, "tp_pct": 0.06, "mode": "fixed", "max_hold": 30},
    "SL4 TP6 half + trail8": {"btc_filter": True, "sl_pct": 0.04, "tp_pct": 0.06, "trail_pct": 0.08, "mode": "partial", "max_hold": 30},
    "SL4 trail8 from +8%": {"btc_filter": True, "sl_pct": 0.04, "trail_pct": 0.08, "trail_after": 0.08, "mode": "trail", "max_hold": 30},
    "SL4 trail4 from +6%": {"btc_filter": True, "sl_pct": 0.04, "trail_pct": 0.04, "trail_after": 0.06, "mode": "trail", "max_hold": 30},
    "SL4 TP6 half + trail4": {"btc_filter": True, "sl_pct": 0.04, "tp_pct": 0.06, "trail_pct": 0.04, "mode": "partial", "max_hold": 30},
    "SL4 TP12": {"btc_filter": True, "sl_pct": 0.04, "tp_pct": 0.12, "mode": "fixed", "max_hold": 30},
}


if __name__ == "__main__":
    data = load(sys.argv[1])
    out = {}
    sets = SWING if "--swing" in sys.argv else VARIANTS
    for name, ov in sets.items():
        p = {**BASE, **ov}
        eq, dd, trades, curve = run(data, p)
        st = stats(trades)
        st.update({"final_equity": round(eq), "return_pct": round((eq / 10000 - 1) * 100, 1), "max_dd_pct": round(dd * 100, 1),
                   "long": stats([t for t in trades if t["side"] == "long"]),
                   "short": stats([t for t in trades if t["side"] == "short"]),
                   "exits": {r: sum(t["reason"] == r for t in trades) for r in ("stop", "target", "time")}})
        out[name] = {"stats": st, "trades": trades}
        print(f"{name:22s} n={st.get('n')} win={st.get('win_rate')}% avgR={st.get('avg_R')} PF={st.get('profit_factor')} "
              f"ret={st['return_pct']}% maxDD={st['max_dd_pct']}% | L {st['long']} | S {st['short']} | {st['exits']}")
    if "--json" in sys.argv:
        json.dump(out, open(sys.argv[sys.argv.index("--json") + 1], "w"), default=str)

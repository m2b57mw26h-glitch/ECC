#!/usr/bin/env python3
"""Compare well-known crypto strategies on the same data and costs.

Usage: python3 compare.py <raw_daily_dir> [out.json]
Data: Liquid daily candles in backtest/raw (48 coins, 2026-03-26..2026-10-10).
Common rules for every strategy:
- signal on a day's close, fill at the next day's open (volatility breakout fills intraday at its level);
- fee 0.1% per side; no funding, no slippage beyond the fee;
- up to 8 positions, each equity/8 notional at 1x (rebalancers split equity across their basket);
- only coins with >= $5M average daily volume over the last 14 days;
- when more signals than free slots, the most liquid coins go first.
Halves: trades are split by entry date at the middle of the tradable period.
"""
import glob
import json
import os
import statistics
import sys

FEE = 0.001
MAX_OPEN = 8
TODAY_MS = 1791676800000
START = 1000.0 * 10


def load(d):
    out = {}
    for p in glob.glob(os.path.join(d, "*.json")):
        j = json.load(open(p))
        cs = [{k: (int(v) if k == "timestamp" else float(v)) for k, v in c.items()} for c in j["candles"]]
        out[j["symbol"]] = [c for c in cs if c["timestamp"] < TODAY_MS]
    return out


# ---------- indicators ----------
def sma(xs, n):
    return [None if i < n - 1 else sum(xs[i - n + 1: i + 1]) / n for i in range(len(xs))]


def ema(xs, n):
    k, out, e = 2 / (n + 1), [], xs[0]
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(xs, n):
    out, g, l = [None] * len(xs), 0.0, 0.0
    for i in range(1, len(xs)):
        ch = xs[i] - xs[i - 1]
        up, dn = max(ch, 0), max(-ch, 0)
        if i <= n:
            g += up / n
            l += dn / n
        else:
            g = (g * (n - 1) + up) / n
            l = (l * (n - 1) + dn) / n
        if i >= n:
            out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def stdev(xs, n):
    return [None if i < n - 1 else statistics.pstdev(xs[i - n + 1: i + 1]) for i in range(len(xs))]


def atr(cs, n):
    tr = [cs[0]["high"] - cs[0]["low"]] + [
        max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"])) for p, c in zip(cs, cs[1:])
    ]
    out, a = [], tr[0]
    for i, t in enumerate(tr):
        a = t if i == 0 else (a * (n - 1) + t) / n
        out.append(a)
    return out


def supertrend(cs, n=10, m=3.0):
    a = atr(cs, n)
    up_band = dn_band = None
    trend, out = 1, []
    for i, c in enumerate(cs):
        hl2 = (c["high"] + c["low"]) / 2
        bu, bd = hl2 + m * a[i], hl2 - m * a[i]
        if i == 0:
            up_band, dn_band = bu, bd
        else:
            pc = cs[i - 1]["close"]
            up_band = bu if bu < up_band or pc > up_band else up_band
            dn_band = bd if bd > dn_band or pc < dn_band else dn_band
            if trend == 1 and c["close"] < dn_band:
                trend = -1
            elif trend == -1 and c["close"] > up_band:
                trend = 1
        out.append(trend if i >= n else 0)
    return out


def prep(cs):
    c = [x["close"] for x in cs]
    h = [x["high"] for x in cs]
    lo = [x["low"] for x in cs]
    m20, s20 = sma(c, 20), stdev(c, 20)
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    vwap = [None] * len(cs)
    for i in range(9, len(cs)):
        w = cs[i - 9: i + 1]
        v = sum(x["volume"] for x in w)
        vwap[i] = sum((x["high"] + x["low"] + x["close"]) / 3 * x["volume"] for x in w) / v if v else None
    return {
        "c": c, "h": h, "l": lo, "m20": m20, "s20": s20, "e10": ema(c, 10), "e30": ema(c, 30), "e50": ema(c, 50),
        "e100": ema(c, 100), "r2": rsi(c, 2), "r3": rsi(c, 3), "macd": macd, "msig": sig, "st": supertrend(cs),
        "vwap": vwap, "dv": [x["volume"] * x["close"] for x in cs],
    }


# ---------- per-coin signal strategies ----------
def donchian(ind, i, pos):
    """Turtle system 1: enter on a 20-day breakout, exit on a 10-day opposite breakout."""
    c = ind["c"][i]
    if pos is None:
        if i < 20:
            return None
        if c > max(ind["h"][i - 20: i]):
            return "long"
        if c < min(ind["l"][i - 20: i]):
            return "short"
        return None
    if pos == "long":
        return c < min(ind["l"][i - 10: i])
    return c > max(ind["h"][i - 10: i])


def ema_cross(ind, i, pos):
    """Moving-average crossover 10/30: always in the direction of the fast average."""
    f, s, pf, ps = ind["e10"][i], ind["e30"][i], ind["e10"][i - 1], ind["e30"][i - 1]
    if pos is None:
        if i < 30:
            return None
        if f > s and pf <= ps:
            return "long"
        if f < s and pf >= ps:
            return "short"
        return None
    return f < s if pos == "long" else f > s


def supertrend_sig(ind, i, pos):
    """Supertrend(10, 3): enter on a flip, exit on the opposite flip."""
    t, pt = ind["st"][i], ind["st"][i - 1]
    if pos is None:
        if t == 1 and pt == -1:
            return "long"
        if t == -1 and pt == 1:
            return "short"
        return None
    return t == -1 if pos == "long" else t == 1


def macd_sig(ind, i, pos):
    """MACD(12,26,9) signal-line cross, taken only on the side of EMA50."""
    m, s, pm, ps, c, e = ind["macd"][i], ind["msig"][i], ind["macd"][i - 1], ind["msig"][i - 1], ind["c"][i], ind["e50"][i]
    if pos is None:
        if i < 50:
            return None
        if m > s and pm <= ps and c > e:
            return "long"
        if m < s and pm >= ps and c < e:
            return "short"
        return None
    return m < s if pos == "long" else m > s


def bollinger(ind, i, pos):
    """Bollinger(20, 2) mean reversion: fade a close outside the band, exit at the middle."""
    m, sd, c = ind["m20"][i], ind["s20"][i], ind["c"][i]
    if m is None:
        return None if pos is None else False
    if pos is None:
        if c < m - 2 * sd:
            return "long"
        if c > m + 2 * sd:
            return "short"
        return None
    return c >= m if pos == "long" else c <= m


def connors_rsi2(ind, i, pos):
    """Connors RSI(2): long only, above EMA100 (SMA200 needs more history than we have), RSI(2)<10, exit close>SMA5."""
    c = ind["c"][i]
    if pos is None:
        if i < 100 or ind["r2"][i] is None:
            return None
        return "long" if c > ind["e100"][i] and ind["r2"][i] < 10 else None
    return c > sum(ind["c"][i - 4: i + 1]) / 5


def strategy_b(ind, i, pos):
    """Our strategy B (reel, QQQ rules): RSI(3)<22, EMA100 rising over 5 days, close >=1% under 10-day VWAP; exit RSI(3)>55."""
    if pos is None:
        if i < 100 or ind["r3"][i] is None or ind["vwap"][i] is None:
            return None
        ok = ind["r3"][i] < 22 and ind["e100"][i] > ind["e100"][i - 5] and ind["c"][i] <= ind["vwap"][i] * 0.99
        return "long" if ok else None
    return ind["r3"][i] > 55


def run_signals(data, ind, fn, long_only=False):
    days = sorted({c["timestamp"] for cs in data.values() for c in cs})
    idx = {s: {c["timestamp"]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    equity, peak, dd = START, START, 0.0
    open_pos, trades = {}, []
    for di, day in enumerate(days[:-1]):
        nxt = days[di + 1]
        for s in list(open_pos):
            i, j = idx[s].get(day), idx[s].get(nxt)
            if i is None or j is None:
                continue
            p = open_pos[s]
            if fn(ind[s], i, p["side"]):
                px = data[s][j]["open"]
                d = 1 if p["side"] == "long" else -1
                pnl = p["qty"] * (px - p["entry"]) * d - FEE * p["qty"] * (p["entry"] + px)
                equity += pnl
                trades.append({"sym": s, "side": p["side"], "pnl": pnl, "t": p["t"], "days": di - p["di"]})
                del open_pos[s]
        mtm = equity + sum(
            o["qty"] * (data[s][idx[s][day]]["close"] - o["entry"]) * (1 if o["side"] == "long" else -1)
            for s, o in open_pos.items() if day in idx[s]
        )
        peak = max(peak, mtm)
        dd = max(dd, (peak - mtm) / peak)
        cands = []
        for s in data:
            i = idx[s].get(day)
            if s in open_pos or i is None or i < 14 or nxt not in idx[s]:
                continue
            liq = statistics.mean(ind[s]["dv"][i - 13: i + 1])
            if liq < 5e6:
                continue
            side = fn(ind[s], i, None)
            if side and not (long_only and side == "short"):
                cands.append((-liq, s, side))
        for _, s, side in sorted(cands):
            if len(open_pos) >= MAX_OPEN:
                break
            o = data[s][idx[s][nxt]]["open"]
            open_pos[s] = {"side": side, "entry": o, "qty": equity / MAX_OPEN / o, "t": nxt, "di": di + 1}
    return equity, dd, trades


# ---------- special strategies ----------
def vol_breakout(data, ind, k=0.5):
    """Larry Williams volatility breakout, long only, as popular in crypto: buy when price passes
    open + k * yesterday's range, only if yesterday's close is above SMA20; sell at the day's close."""
    days = sorted({c["timestamp"] for cs in data.values() for c in cs})
    idx = {s: {c["timestamp"]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    equity, peak, dd, trades = START, START, 0.0, []
    for day in days:
        cands = []
        for s, cs in data.items():
            i = idx[s].get(day)
            if i is None or i < 21:
                continue
            liq = statistics.mean(ind[s]["dv"][i - 14: i])
            if liq < 5e6 or ind[s]["c"][i - 1] <= ind[s]["m20"][i - 1]:
                continue
            lvl = cs[i]["open"] + k * (cs[i - 1]["high"] - cs[i - 1]["low"])
            if cs[i]["high"] >= lvl:
                cands.append((-liq, s, max(lvl, cs[i]["open"]), cs[i]["close"]))
        day_pnl = 0.0
        for _, s, e, x in sorted(cands)[:MAX_OPEN]:
            q = equity / MAX_OPEN / e
            pnl = q * (x - e) - FEE * q * (e + x)
            day_pnl += pnl
            trades.append({"sym": s, "side": "long", "pnl": pnl, "t": day, "days": 0})
        equity += day_pnl
        peak = max(peak, equity)
        dd = max(dd, (peak - equity) / peak)
    return equity, dd, trades


def rebalancer(data, ind, rank, n=5, every=7, short=False, btc_gate=False):
    """Weekly rebalance into the top n coins by `rank` (and short the bottom n when short=True)."""
    days = sorted({c["timestamp"] for cs in data.values() for c in cs})
    idx = {s: {c["timestamp"]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    equity, peak, dd, trades = START, START, 0.0, []
    book = []  # (sym, side, qty, entry, t)
    btc = data["BTC"]
    for di in range(30, len(days) - 1, every):
        day, nxt = days[di], days[di + 1]
        end = days[min(di + 1 + every, len(days) - 1)]  # next rebalance fills at this open
        scores = []
        for s, cs in data.items():
            i = idx[s].get(day)
            if i is None or i < 30 or nxt not in idx[s] or end not in idx[s]:
                continue
            if statistics.mean(ind[s]["dv"][i - 13: i + 1]) < 5e6:
                continue
            scores.append((rank(ind[s], i), s))
        scores.sort(reverse=True)
        legs = [(s, "long") for _, s in scores[:n]]
        if short:
            legs += [(s, "short") for _, s in scores[-n:]]
        if btc_gate:
            bi = idx["BTC"][day]
            if btc[bi]["close"] <= ind["BTC"]["m20"][bi]:
                legs = []
        period_pnl = 0.0
        for s, side in legs:
            e = data[s][idx[s][nxt]]["open"]
            x = data[s][idx[s][end]]["open"]
            q = equity / len(legs) / e
            d = 1 if side == "long" else -1
            pnl = q * (x - e) * d - FEE * q * (e + x)
            period_pnl += pnl
            trades.append({"sym": s, "side": side, "pnl": pnl, "t": nxt, "days": every})
        equity += period_pnl
        peak = max(peak, equity)
        dd = max(dd, (peak - equity) / peak)
    return equity, dd, trades


def mom14(ind, i):
    return ind["c"][i] / ind["c"][i - 14] - 1


def rev7(ind, i):
    return -(ind["c"][i] / ind["c"][i - 7] - 1)


def buy_hold(data, syms):
    rets = []
    for s in syms:
        cs = data[s]
        rets.append(cs[-1]["close"] / cs[30]["open"] - 1)
    return START * (1 + statistics.mean(rets) - 2 * FEE)


# ---------- reporting ----------
def stats(eq, dd, tr, mid):
    def part(ts):
        w = [t["pnl"] for t in ts if t["pnl"] > 0]
        loss = -sum(t["pnl"] for t in ts if t["pnl"] <= 0)
        return {"n": len(ts), "pnl": round(sum(t["pnl"] for t in ts)),
                "pf": round(sum(w) / loss, 2) if loss else None,
                "win": round(len(w) / len(ts) * 100) if ts else None}

    return {"ret_pct": round((eq / START - 1) * 100, 1), "max_dd_pct": round(dd * 100, 1), **part(tr),
            "h1": part([t for t in tr if t["t"] < mid]), "h2": part([t for t in tr if t["t"] >= mid]),
            "longs": part([t for t in tr if t["side"] == "long"]), "shorts": part([t for t in tr if t["side"] == "short"])}


if __name__ == "__main__":
    data = load(sys.argv[1])
    ind = {s: prep(cs) for s, cs in data.items()}
    days = sorted({c["timestamp"] for cs in data.values() for c in cs})
    mid = days[30 + (len(days) - 30) // 2]
    res = {}
    signal = {
        "Turtle / Donchian 20-10": donchian,
        "EMA cross 10/30": ema_cross,
        "Supertrend 10/3": supertrend_sig,
        "MACD + EMA50": macd_sig,
        "Bollinger mean reversion": bollinger,
        "Connors RSI(2)": connors_rsi2,
        "Strategy B (RSI3 pullback)": strategy_b,
    }
    for name, fn in signal.items():
        res[name] = stats(*run_signals(data, ind, fn), mid)
        if name not in ("Connors RSI(2)", "Strategy B (RSI3 pullback)"):
            res[name + " [long only]"] = stats(*run_signals(data, ind, fn, long_only=True), mid)
    res["Volatility breakout k=0.5"] = stats(*vol_breakout(data, ind), mid)
    res["Momentum top-5 weekly"] = stats(*rebalancer(data, ind, mom14), mid)
    res["Momentum top-5 weekly + BTC filter"] = stats(*rebalancer(data, ind, mom14, btc_gate=True), mid)
    res["Momentum long/short 5/5"] = stats(*rebalancer(data, ind, mom14, short=True), mid)
    res["Reversal top-5 weekly"] = stats(*rebalancer(data, ind, rev7), mid)
    liquid = [s for s in data if len(data[s]) > 150]
    res["Buy & hold all coins"] = {"ret_pct": round((buy_hold(data, liquid) / START - 1) * 100, 1)}
    res["Buy & hold BTC"] = {"ret_pct": round((buy_hold(data, ["BTC"]) / START - 1) * 100, 1)}
    for k, v in sorted(res.items(), key=lambda kv: -kv[1]["ret_pct"]):
        if "n" in v:
            print(f"{k:38s} ret={v['ret_pct']:+6.1f}% DD={v['max_dd_pct']:5.1f}% n={v['n']:3d} win={v['win']}% PF={v['pf']} "
                  f"| H1 pnl={v['h1']['pnl']:+5d} PF={v['h1']['pf']} | H2 pnl={v['h2']['pnl']:+5d} PF={v['h2']['pf']} "
                  f"| L {v['longs']['pnl']:+5d} S {v['shorts']['pnl']:+5d}")
        else:
            print(f"{k:38s} ret={v['ret_pct']:+6.1f}%")
    if len(sys.argv) > 2:
        json.dump(res, open(sys.argv[2], "w"), indent=1)

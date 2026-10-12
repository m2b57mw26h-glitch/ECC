#!/usr/bin/env python3
"""Swing signals (strategy S, chosen 2026-10-11): 4h pullback-in-trend setup, 15m entry trigger, SL 4% / TP 6%.

  python3 swing_signals.py setups  <raw4h_dir> [HELD...]   -> updates swing/setups.json, prints new/active setups
  python3 swing_signals.py entries <raw15m_dir>             -> prints entry signals for active setups (JSON lines)

Setup (on the last closed 4h bar): EMA20 > EMA50 and close > EMA50 and RSI(3) < 15 -> long;
mirror (EMA20 < EMA50, close < EMA50, RSI(3) > 85) -> short. Needs avg 4h dollar volume x6 >= $5M,
and BTC's daily close vs its 20-day average must agree (long only above, short only below).
A setup stays active for 3 4h bars (12h). Entry trigger on the last closed 15m bar:
long = close > EMA20(15m) and close > previous bar's high; short = mirror.
"""
import glob, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backtest"))
from mr_backtest import rsi, ema  # noqa: E402

STATE = os.path.join(HERE, "swing", "setups.json")
SL, TP, TTL_MS = 0.04, 0.06, 3 * 4 * 3600 * 1000
BAR4H, BAR15 = 4 * 3600 * 1000, 15 * 60 * 1000


def candles(path):
    j = json.load(open(path))
    return j["symbol"], [{k: (int(v) if k == "timestamp" else float(v)) for k, v in c.items()} for c in j["candles"]]


def closed(cs, bar_ms, now_ms):
    return [c for c in cs if c["timestamp"] + bar_ms <= now_ms]


def btc_up():
    days = {}
    for p in (os.path.join(HERE, "backtest", "raw", "BTC.json"), os.path.join(HERE, "daily", "raw", "BTC.json")):
        if os.path.exists(p):
            for c in candles(p)[1]:
                days[c["timestamp"]] = c["close"]
    import time
    today = int(time.time() * 1000) // 86400000 * 86400000
    cl = [days[t] for t in sorted(days) if t < today]
    if len(cl) < 20:
        return None
    return cl[-1] > statistics.mean(cl[-20:])


def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {"setups": []}


def setups(raw4h, held):
    import time
    now = int(time.time() * 1000)
    flt = btc_up()
    st = load_state()
    active = [s for s in st["setups"] if now - s["bar_t"] < TTL_MS and not s.get("used")]
    for p in sorted(glob.glob(os.path.join(raw4h, "*.json"))):
        sym, cs = candles(p)
        cs = closed(cs, BAR4H, now)
        if len(cs) < 60 or sym in held:
            continue
        c = [x["close"] for x in cs]
        e20, e50, r3 = ema(c, 20), ema(c, 50), rsi(c, 3)
        if statistics.mean(x["volume"] * x["close"] for x in cs[-43:-1]) * 6 < 5e6:
            continue
        side = None
        if e20[-1] > e50[-1] and c[-1] > e50[-1] and r3[-1] is not None and r3[-1] < 15:
            side = "long"
        elif e20[-1] < e50[-1] and c[-1] < e50[-1] and r3[-1] is not None and r3[-1] > 85:
            side = "short"
        if side is None or (flt is not None and (side == "long") != flt):
            continue
        if any(s["coin"] == sym for s in active):
            continue
        active.append({"coin": sym, "side": side, "bar_t": cs[-1]["timestamp"], "px4h": c[-1], "rsi3": round(r3[-1], 1)})
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"btc_up": flt, "updated": now, "setups": active}, open(STATE, "w"), indent=1)
    print(f"BTC filter: {'longs only' if flt else 'shorts only' if flt is False else 'n/a'}; active setups: {len(active)}")
    for s in active:
        print(json.dumps(s))


def entries(raw15):
    import time
    now = int(time.time() * 1000)
    st = load_state()
    for s in st["setups"]:
        if now - s["bar_t"] >= TTL_MS or s.get("used"):
            continue
        p = os.path.join(raw15, s["coin"] + ".json")
        if not os.path.exists(p):
            continue
        cs = closed(candles(p)[1], BAR15, now)
        if len(cs) < 25:
            continue
        c = [x["close"] for x in cs]
        e = ema(c, 20)
        hit = (c[-1] > e[-1] and c[-1] > cs[-2]["high"]) if s["side"] == "long" else (c[-1] < e[-1] and c[-1] < cs[-2]["low"])
        if hit:
            d = 1 if s["side"] == "long" else -1
            print(json.dumps({"coin": s["coin"], "side": s["side"], "entry": c[-1],
                              "sl": round(c[-1] * (1 - d * SL), 8), "tp": round(c[-1] * (1 + d * TP), 8),
                              "rsi3_4h": s["rsi3"], "bar15_t": cs[-1]["timestamp"]}))


if __name__ == "__main__":
    if sys.argv[1] == "setups":
        setups(sys.argv[2], set(sys.argv[3:]))
    elif sys.argv[1] == "entries":
        entries(sys.argv[2])

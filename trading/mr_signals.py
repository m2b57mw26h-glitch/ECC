#!/usr/bin/env python3
"""Daily signals for strategy B "RSI(3) pullback" (see backtest/mr_backtest.py).

Usage: python3 mr_signals.py <history_dir> <daily_raw_dir> [HELD_SYM ...]
1. Merges today's 15-candle files into the 200-day history (completed days only).
2. Entry: RSI(3) < 22, EMA100 above its value 5 days ago, close >= 1% below 10-day VWAP, avg volume >= $5M.
   Buy at the next open, 1/8 of equity, 1x, emergency stop -15%.
3. Exit for held coins: RSI(3) > 55 on the last close, or close <= entry -15% (checked by the exchange stop).
Prints a Markdown section for the daily report.
"""
import glob, json, os, statistics, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
from mr_backtest import rsi, ema, BASE  # noqa: E402

DAY = 86400000


def merge(hist_dir, daily_dir, today_ms):
    for p in glob.glob(os.path.join(daily_dir, "*.json")):
        d = json.load(open(p))
        hp = os.path.join(hist_dir, os.path.basename(p))
        h = json.load(open(hp)) if os.path.exists(hp) else {"symbol": d["symbol"], "candles": []}
        by_ts = {int(c["timestamp"]): c for c in h["candles"]}
        for c in d["candles"]:
            if int(c["timestamp"]) < today_ms:          # completed days only
                by_ts[int(c["timestamp"])] = c
        h["candles"] = [by_ts[k] for k in sorted(by_ts) if k < today_ms]
        json.dump(h, open(hp, "w"))


def main():
    hist_dir, daily_dir, held = sys.argv[1], sys.argv[2], set(sys.argv[3:])
    today_ms = int(time.time() * 1000) // DAY * DAY
    merge(hist_dir, daily_dir, today_ms)
    p = BASE
    entries, exits, watch = [], [], []
    for path in sorted(glob.glob(os.path.join(hist_dir, "*.json"))):
        d = json.load(open(path)); s = d["symbol"]
        cs = [{k: float(v) for k, v in c.items()} for c in d["candles"]]
        if len(cs) < p["ema_n"] + 5:
            continue
        closes = [c["close"] for c in cs]
        r = rsi(closes, p["rsi_n"])[-1]
        e = ema(closes, p["ema_n"])
        w = cs[-10:]
        v = sum(c["volume"] for c in w)
        vwap = sum((c["high"] + c["low"] + c["close"]) / 3 * c["volume"] for c in w) / v
        vol_usd = statistics.mean(c["volume"] * c["close"] for c in cs[-14:])
        up = e[-1] > e[-6]
        gap = (closes[-1] / vwap - 1) * 100
        if s in held:
            if r > p["exit_rsi"]:
                exits.append(f"- {s}: RSI(3) = {r:.0f} > 55, закрыть по открытию")
            continue
        if vol_usd < 5e6:
            continue
        if r < p["entry_rsi"] and up and gap <= -1:
            entries.append(f"- **{s}**: RSI(3) = {r:.0f}, цена {gap:+.1f}% к 10‑дневной VWAP, EMA100 растёт. "
                           f"Вход по открытию, аварийный стоп {closes[-1] * 0.85:.6g}")
        elif up and r < 35:
            watch.append(f"{s} (RSI {r:.0f})")
    out = ["## Стратегия B: покупка отката (RSI 3)", ""]
    out += entries or ["Сигналов на вход нет."]
    if watch:
        out += ["", "Близко к сигналу: " + ", ".join(watch)]
    if held:
        out += ["", "Выход:"] + (exits or ["- Сигналов на выход нет, держим."])
    print("\n".join(out))


if __name__ == "__main__":
    main()

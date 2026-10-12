#!/usr/bin/env python3
"""Daily paper signals for the two leaders of TV_STRATEGIES.md (added 2026-10-12):
  T2 = Turtle System 2 (55/20, stop 2N capped at 9%), long only
  ME = MACD + EMA50, long and short, stop 9%

Usage: python3 live_signals.py <daily_raw_dir> <equity> <date YYYY-MM-DD> [COIN ...]
  COIN ...: coins held in the paper account by anything else (screener basket etc.); skipped for new entries.
Steps:
1. Merges today's Liquid candles (daily/raw, completed days only) into tv/live_history/ (seeded from
   backtest/tv_daily, Binance history, or backtest/raw for coins without it).
2. Reads open strategy positions from tv/live_positions.json; exit signals for them.
3. Entry signals on the last completed day; size = 0.5% equity risk / stop distance, leverage 5, max 8 per strategy;
   one position per coin across everything; most liquid first.
Writes <daily_raw_dir>/../tv_signals_<date>.json and prints a Markdown section for the daily report.
Never trades: orders go through suggest_trades_batch / close_position and the user's "да".
"""
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util  # noqa: E402

HIST = os.path.join(HERE, "live_history")
STATE = os.path.join(HERE, "live_positions.json")
RISK, LEV, MAX_STOP, SLOTS, MIN_DV = 0.005, 5, 0.09, 8, 5e6
DAY = 86400


def _load(fname, name):
    spec = importlib.util.spec_from_file_location(fname[:-3], os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.STRATEGIES[name]


STRATS = {
    "T2": (_load("strat_d.py", "Turtle System 2 (55/20, 2N)"), True),
    "ME": (_load("strat_z_ours.py", "Ours: MACD + EMA50"), False),
}
NAMES = {"T2": "Turtle System 2 (только лонг)", "ME": "MACD + EMA50 (лонг и шорт)"}


def liquid_rows(cs):
    return [{"t": int(c["timestamp"]) // 1000, "o": float(c["open"]), "h": float(c["high"]),
             "l": float(c["low"]), "c": float(c["close"]), "v": float(c["volume"])} for c in cs]


def merge(raw_dir, today):
    os.makedirs(HIST, exist_ok=True)
    for p in glob.glob(os.path.join(raw_dir, "*.json")):
        d = json.load(open(p))
        sym = d["symbol"]
        hp = os.path.join(HIST, f"{sym}.json")
        if os.path.exists(hp):
            rows = json.load(open(hp))["candles"]
        elif os.path.exists(os.path.join(HERE, "..", "tv_daily", f"{sym}.json")):
            rows = json.load(open(os.path.join(HERE, "..", "tv_daily", f"{sym}.json")))["candles"]
        elif os.path.exists(os.path.join(HERE, "..", "raw", f"{sym}.json")):
            rows = liquid_rows(json.load(open(os.path.join(HERE, "..", "raw", f"{sym}.json")))["candles"])
        else:
            rows = []
        by_t = {int(r["t"]): r for r in rows}
        for r in liquid_rows(d["candles"]):
            by_t[r["t"]] = r
        rows = [by_t[k] for k in sorted(by_t) if k < today]
        json.dump({"symbol": sym, "candles": rows}, open(hp, "w"))


def main():
    raw_dir, equity, date = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    held_other = set(sys.argv[4:])
    today = int(time.time()) // DAY * DAY
    merge(raw_dir, today)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"positions": []}
    pos = state["positions"]
    held = held_other | {p["coin"] for p in pos}

    data = {}
    for p in sorted(glob.glob(os.path.join(HIST, "*.json"))):
        j = json.load(open(p))
        df = pd.DataFrame(j["candles"])[["t", "o", "h", "l", "c", "v"]].astype(float)
        if len(df) >= 60:
            data[j["symbol"]] = df.reset_index(drop=True)
    sigs = {k: {s: fn(df) for s, df in data.items()} for k, (fn, _) in STRATS.items()}

    exits = []
    for p in pos:
        g = sigs[p["strategy"]].get(p["coin"])
        if g is None:
            continue
        key = "lx" if p["side"] == "long" else "sx"
        if np.asarray(g.get(key, np.zeros(1)), bool)[-1]:
            exits.append(p)

    entries = []
    for k, (fn, long_only) in STRATS.items():
        free = SLOTS - sum(1 for p in pos if p["strategy"] == k and p not in exits)
        cands = []
        for s, df in data.items():
            if s in held:
                continue
            dv = float((df["c"] * df["v"]).tail(14).mean())
            if dv < MIN_DV:
                continue
            g = sigs[k][s]
            side = "long" if np.asarray(g["le"], bool)[-1] else (
                "short" if not long_only and np.asarray(g.get("se", np.zeros(1)), bool)[-1] else None)
            if not side:
                continue
            st = g.get("stop")
            stop = MAX_STOP if st is None else float(np.nan_to_num(np.asarray(st, float)[-1], nan=MAX_STOP))
            stop = min(max(stop, 0.005), MAX_STOP)
            px = float(df["c"].iat[-1])
            notional = min(RISK * equity / stop, equity * LEV / SLOTS)
            cands.append({"coin": s, "strategy": k, "side": side, "ref_close": px, "stop_pct": round(stop * 100, 2),
                          "stop_px": round(px * (1 - stop) if side == "long" else px * (1 + stop), 6),
                          "notional_usd": round(notional, 2), "margin_usd": round(notional / LEV, 2),
                          "leverage": LEV, "dv14": round(dv)})
        cands.sort(key=lambda c: -c["dv14"])
        for c in cands[:max(free, 0)]:
            entries.append(c)
            held.add(c["coin"])

    last_day = time.strftime("%Y-%m-%d", time.gmtime(max(df["t"].iat[-1] for df in data.values())))
    out = {"date": date, "signal_day": last_day, "equity": equity, "entries": entries,
           "exits": [{"coin": p["coin"], "strategy": p["strategy"], "side": p["side"]} for p in exits],
           "open": pos}
    json.dump(out, open(os.path.join(raw_dir, "..", f"tv_signals_{date}.json"), "w"), indent=1)

    print(f"## Стратегии Turtle 2 и MACD + EMA50 (бумажный счёт, сигналы по закрытию {last_day})\n")
    print(f"Правила: риск 0,5% счёта на сделку, плечо 5x, стоп не дальше −9%, до 8 позиций на стратегию. "
          f"Открыто по стратегиям: {len(pos)}.\n")
    if exits:
        print("**Закрыть (сигнал выхода):**")
        for p in exits:
            print(f"- {p['coin']} {p['side']} ({NAMES[p['strategy']]}), вход {p.get('entry')}")
        print()
    if entries:
        print("**Открыть:**\n\n| Монета | Стратегия | Сторона | Цена закрытия | Стоп | Объём | Маржа |\n|---|---|---|---|---|---|---|")
        for c in entries:
            print(f"| {c['coin']} | {NAMES[c['strategy']]} | {c['side']} | {c['ref_close']:g} | {c['stop_px']:g} (−{c['stop_pct']}%) "
                  f"| ${c['notional_usd']:,.0f} | ${c['margin_usd']:,.0f} |")
    else:
        print("Новых сигналов на вход нет.")


if __name__ == "__main__":
    main()

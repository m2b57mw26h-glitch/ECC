"""Idea journal for the daily review (method borrowed from TradingAgents' memory log).

Every coin that gets a bull/bear/risk review is logged here, traded or not.
After HORIZON full days the idea is settled against the daily candles in
daily/raw/, and Claude writes a 2-4 sentence lesson that later reviews read.

Usage:
  python3 ideas.py add DATE COIN RATING ENTRY STOP TARGET "THESIS"   # Hold: STOP TARGET = - -
  python3 ideas.py settle [DAILY_RAW_DIR]   # settle due ideas, list ones needing a lesson
  python3 ideas.py lesson IDEA_ID "TEXT"     # store the lesson for a settled idea
  python3 ideas.py lessons [N]               # last N lessons, to paste into the next review

RATING is Buy / Overweight / Hold / Underweight / Sell (Buy, Overweight = long;
Sell, Underweight = short; Hold = no trade, only the move is recorded).
Read-only with respect to the exchange: this never places orders.
"""
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL = os.path.join(HERE, "ideas.csv")
DEFAULT_RAW = os.path.join(HERE, "..", "daily", "raw")
HORIZON = 3          # full days after the idea's day
MAX_STOP_PCT = 9.0   # user's rule: 5x futures, liquidation ~-10%, stop at -9% or tighter
FIELDS = ["id", "date", "coin", "rating", "side", "entry", "stop", "target", "thesis",
          "status", "settled_on", "outcome", "exit_px", "move_pct", "btc_pct", "alpha_pct", "lesson"]
SIDES = {"Buy": 1, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -1}


def load():
    if not os.path.exists(JOURNAL):
        return []
    with open(JOURNAL, newline="") as f:
        return list(csv.DictReader(f))


def save(rows):
    tmp = JOURNAL + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, JOURNAL)


def add(day, coin, rating, entry, stop, target, thesis):
    rating = rating.capitalize()
    if rating not in SIDES:
        sys.exit(f"rating must be one of {', '.join(SIDES)}")
    date.fromisoformat(day)
    side = SIDES[rating]
    entry = float(entry)
    if entry <= 0:
        sys.exit("entry must be the price at review time")
    stop, target = (float(stop), float(target)) if side else ("", "")
    if side:
        risk_pct = (entry - stop) / entry * 100 * side
        reward_pct = (target - entry) / entry * 100 * side
        if not 0 < risk_pct <= MAX_STOP_PCT:
            sys.exit(f"stop is {risk_pct:.1f}% from entry; must be on the losing side and at most {MAX_STOP_PCT}%")
        if reward_pct <= 0:
            sys.exit("target must be on the winning side of entry")
    rows = load()
    idea_id = f"{day}-{coin}"
    if any(r["id"] == idea_id for r in rows):
        sys.exit(f"{idea_id} is already in the journal")
    rows.append({"id": idea_id, "date": day, "coin": coin, "rating": rating,
                 "side": {1: "long", -1: "short", 0: "none"}[side],
                 "entry": entry, "stop": stop, "target": target, "thesis": thesis,
                 "status": "pending"})
    save(rows)
    print(f"added {idea_id}")


def candles(raw_dir, coin):
    path = os.path.join(raw_dir, f"{coin}.json")
    if not os.path.exists(path):
        return None
    out = {}
    for c in json.load(open(path))["candles"]:
        d = datetime.fromtimestamp(c["timestamp"] / 1000, tz=timezone.utc).date()
        out[d] = {k: float(c[k]) for k in ("open", "high", "low", "close")}
    return out


def settle_one(r, bars, btc, today):
    """Walk the idea's day plus HORIZON full days. Stop wins a candle that touches both."""
    start = date.fromisoformat(r["date"])
    days = [start + timedelta(days=i) for i in range(HORIZON + 1)]
    if days[-1] >= today:
        return None  # the last day of the window has not closed yet
    if any(d not in bars for d in days) or any(d not in btc for d in (start, days[-1])):
        return "no-data"
    side = {"long": 1, "short": -1, "none": 0}[r["side"]]
    entry = float(r["entry"])
    outcome, exit_px = "time", bars[days[-1]]["close"]
    if side:
        stop, target = float(r["stop"]), float(r["target"])
        for d in days:
            b = bars[d]
            hit_stop = b["low"] <= stop if side > 0 else b["high"] >= stop
            hit_target = b["high"] >= target if side > 0 else b["low"] <= target
            if hit_stop or hit_target:
                outcome, exit_px = ("stop", stop) if hit_stop else ("target", target)
                break
    move = (exit_px - entry) / entry * 100
    btc_move = (btc[days[-1]]["close"] - btc[start]["open"]) / btc[start]["open"] * 100
    r.update({"status": "settled", "settled_on": today.isoformat(), "outcome": outcome,
              "exit_px": round(exit_px, 8), "move_pct": round(move * (side or 1), 2),
              "btc_pct": round(btc_move, 2),
              "alpha_pct": round((move - btc_move) * side, 2) if side else ""})
    return "settled"


def settle(raw_dir):
    rows, today = load(), datetime.now(timezone.utc).date()
    btc = candles(raw_dir, "BTC") or {}
    for r in rows:
        if r["status"] != "pending":
            continue
        bars = candles(raw_dir, r["coin"])
        res = settle_one(r, bars, btc, today) if bars else "no-data"
        if res == "no-data":
            print(f"{r['id']}: candles missing for its window, left pending")
    save(rows)
    need = [r for r in rows if r["status"] == "settled" and not r.get("lesson")]
    for r in need:
        print(f"NEEDS LESSON {r['id']} {r['rating']} {r['side']} entry {r['entry']} -> {r['outcome']} "
              f"{r['exit_px']}: move {r['move_pct']}%, BTC {r['btc_pct']}%, alpha {r['alpha_pct'] or '-'}% | "
              f"thesis: {r['thesis']}")
    if not need:
        print("no settled ideas waiting for a lesson")


def lesson(idea_id, text):
    rows = load()
    for r in rows:
        if r["id"] == idea_id:
            if r["status"] != "settled":
                sys.exit(f"{idea_id} is not settled yet")
            r["lesson"] = text
            save(rows)
            print(f"lesson stored for {idea_id}")
            return
    sys.exit(f"{idea_id} not found")


def lessons(n):
    done = [r for r in load() if r.get("lesson")]
    for r in done[-n:]:
        print(f"- {r['id']} {r['rating']} ({r['outcome']}, move {r['move_pct']}%): {r['lesson']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    if cmd == "add" and len(a) == 8:
        add(*a[1:])
    elif cmd == "settle":
        settle(a[1] if len(a) > 1 else DEFAULT_RAW)
    elif cmd == "lesson" and len(a) == 3:
        lesson(a[1], a[2])
    elif cmd == "lessons":
        lessons(int(a[1]) if len(a) > 1 else 10)
    else:
        sys.exit(__doc__)

#!/usr/bin/env python3
"""Build dashboard/data.json for the trades page.

Usage: python3 build_dashboard.py   (run from anywhere; paths are relative to trading-agent/)
Inputs: journal.csv, backtest/raw/ (history), daily/raw/ (latest candles incl. today's forming one),
        the newest daily/portfolio_*.json snapshot, dashboard/equity.csv (upserted with the snapshot).
"""
import csv, glob, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
DAYS = 90


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def candles(sym):
    by = {}
    for d in (P("backtest", "raw"), P("daily", "raw")):
        f = os.path.join(d, f"{sym}.json")
        if os.path.exists(f):
            for c in json.load(open(f))["candles"]:
                by[int(c["timestamp"])] = [int(c["timestamp"]) // 1000, float(c["open"]), float(c["high"]),
                                           float(c["low"]), float(c["close"])]
    return [by[k] for k in sorted(by)][-DAYS:]


def main():
    snap_path = sorted(glob.glob(P("daily", "portfolio_*.json")))[-1]
    snap = json.load(open(snap_path))
    marks = {p["coin"]: p for p in snap["positions"]}

    eq_path = P("dashboard", "equity.csv")
    curve = {r["time"]: float(r["equity"]) for r in csv.DictReader(open(eq_path))} if os.path.exists(eq_path) else {}
    curve[snap["time"]] = snap["equity"]
    with open(eq_path, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["time", "equity"])
        for k in sorted(curve): w.writerow([k, f"{curve[k]:.2f}"])

    positions, closed, coins = [], [], set()
    for r in csv.DictReader(open(P("journal.csv"))):
        t = {"id": r["trade_id"], "strategy": r.get("strategy", ""), "coin": r["coin"], "side": r["side"],
             "notional": num(r["notional_usd"]), "leverage": num(r["leverage"]), "entry": num(r["entry_px"]),
             "sl": num(r["sl"]), "tp": num(r["tp"]), "opened": r["opened_utc"], "closed": r["closed_utc"] or None,
             "exit": num(r["exit_px"]), "exit_reason": r["exit_reason"] or None, "pnl": num(r["pnl_usd"]),
             "review": r.get("loss_review") or None}
        coins.add(t["coin"])
        if t["closed"]:
            closed.append(t)
        else:
            m = marks.get(t["coin"], {})
            t["mark"], t["upnl"] = m.get("mark"), m.get("upnl")
            positions.append(t)

    data = {
        "updated": snap["time"], "start_equity": 10000.0, "equity": snap["equity"],
        "realized": sum(t["pnl"] or 0 for t in closed),
        "unrealized": sum(p.get("upnl") or 0 for p in positions),
        "positions": positions, "closed": closed,
        "equity_curve": [[k, curve[k]] for k in sorted(curve)],
        "candles": {c: candles(c) for c in sorted(coins)},
    }
    json.dump(data, open(P("dashboard", "data.json"), "w"), separators=(",", ":"))
    print(f"data.json: {len(positions)} open, {len(closed)} closed, {len(data['candles'])} charts, equity {snap['equity']}")


if __name__ == "__main__":
    main()

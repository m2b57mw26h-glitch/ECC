#!/usr/bin/env python3
"""Daily crypto screener.

Usage: python3 screener.py <daily_dir> <date YYYY-MM-DD>
Reads <daily_dir>/raw/<SYM>.json (daily candles from show_chart, last candle = today, forming)
and <daily_dir>/markets_<date>.json (price, funding, OI, positioning from analyze_markets_batch).
Writes <daily_dir>/screen_<date>.csv and <daily_dir>/report_<date>.md and prints the report.
"""
import csv, glob, json, os, statistics, sys

MIN_VOL_USD = 5e6      # min average daily volume (USD) to be tradeable
MIN_OI_USD = 10e6      # min open interest
HOT_24H = 20.0         # % move in 24h: too late to chase, flagged instead of picked
SL_ATR, TP_ATR = 1.5, 3.0   # v2 (backtest 2026-10-11): wider stop/target


def f(x):
    return float(x)


def load(daily_dir, date):
    mk = json.load(open(os.path.join(daily_dir, f"markets_{date}.json")))["markets"]
    rows = []
    for path in sorted(glob.glob(os.path.join(daily_dir, "raw", "*.json"))):
        d = json.load(open(path))
        sym = d["symbol"]
        c = d["candles"]
        if len(c) < 10 or sym not in mk:
            continue
        full, today = c[:-1], c[-1]          # completed days, today's forming candle
        closes = [f(x["close"]) for x in full]
        m = mk[sym]
        px = m["px"]
        last = full[-1]
        vols_usd = [f(x["volume"]) * f(x["close"]) for x in full]
        ranges = [(f(x["high"]) - f(x["low"])) / f(x["close"]) * 100 for x in full]
        rets = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))]
        hi14 = max(f(x["high"]) for x in c)
        lo14 = min(f(x["low"]) for x in c)
        sma = statistics.mean(closes)
        atr_pct = statistics.mean(ranges[-7:])
        rows.append({
            "symbol": sym,
            "price": px,
            "chg24_pct": m["chg24"],
            "chg7d_pct": round((px / closes[-7] - 1) * 100, 2),
            "vol_usd_yday": round(vols_usd[-1]),
            "vol_usd_avg": round(statistics.mean(vols_usd)),
            "vol_ratio": round(vols_usd[-1] / statistics.mean(vols_usd), 2),
            "volatility_pct": round(statistics.pstdev(rets), 2),   # stdev of daily returns
            "atr_pct": round(atr_pct, 2),                          # avg daily range, last 7 days
            "low_24h": min(f(last["low"]), f(today["low"])),
            "high_24h": max(f(last["high"]), f(today["high"])),
            "low_14d": lo14,
            "high_14d": hi14,
            "range_pos": round((px - lo14) / (hi14 - lo14), 2) if hi14 > lo14 else 0.5,
            "above_sma14": px > sma,
            "funding_pct": m["funding"],
            "oi_usd": m["oi"],
            "long_pct": m["long_pct"],
            "bias": m["bias"],
        })
    return rows


def score(r):
    """Positive = long candidate, negative = short candidate."""
    s = 0.0
    s += max(-3, min(3, r["chg7d_pct"] / 5))           # 7-day momentum
    s += 1 if r["above_sma14"] else -1                 # trend vs 14-day average
    s += (r["range_pos"] - 0.5) * 2                    # where price sits in the 14-day range
    if r["vol_ratio"] > 1.3:                           # volume confirms the move
        s += 0.5 if s > 0 else -0.5
    # Crowd positioning, contrarian: crowded shorts in an uptrend can squeeze, and vice versa
    if r["bias"] == "short_heavy" and s > 0:
        s += 0.75
    if r["bias"] == "long_heavy" and s < 0:
        s -= 0.75
    if r["bias"] == "long_heavy" and s > 0:
        s -= 0.5
    if r["bias"] == "short_heavy" and s < 0:
        s += 0.5
    return round(s, 2)


def levels(r, side):
    p, a = r["price"], r["atr_pct"] / 100 * r["price"]
    if side == "long":
        return p - SL_ATR * a, p + TP_ATR * a
    return p + SL_ATR * a, p - TP_ATR * a


def fmt(x):
    if x >= 100: return f"{x:,.1f}"
    if x >= 1: return f"{x:,.3f}"
    return f"{x:.6g}"


def main():
    daily_dir, date = sys.argv[1], sys.argv[2]
    rows = load(daily_dir, date)
    for r in rows:
        r["liquid"] = r["vol_usd_avg"] >= MIN_VOL_USD and r["oi_usd"] >= MIN_OI_USD
        r["hot"] = abs(r["chg24_pct"]) >= HOT_24H
        r["score"] = score(r)
    rows.sort(key=lambda r: -r["score"])
    with open(os.path.join(daily_dir, f"screen_{date}.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # v2 market filter: longs only when BTC is above its 20-day average, shorts only when below
    btc = json.load(open(os.path.join(daily_dir, "raw", "BTC.json")))["candles"][:-1]
    btc_closes = [f(x["close"]) for x in btc]
    btc_up = btc_closes[-1] > statistics.mean(btc_closes[-20:]) if len(btc_closes) >= 20 else None
    ok = [r for r in rows if r["liquid"] and not r["hot"]]
    longs = [r for r in ok if r["score"] >= 1.5][:5] if btc_up is not False else []
    shorts = [r for r in reversed(ok) if r["score"] <= -1.5][:5] if btc_up is not True else []
    hot = [r for r in rows if r["hot"]]

    out = [f"# Обзор крипторынка {date}", "",
           f"Монет проанализировано: {len(rows)}, ликвидных: {sum(r['liquid'] for r in rows)}.",
           "Фильтр рынка: " + ("BTC выше 20‑дневной средней, берём только лонги." if btc_up else
                               "BTC ниже 20‑дневной средней, берём только шорты." if btc_up is False else
                               "нет данных по BTC (нужно 21 свеча), фильтр выключен."), ""]

    def table(title, picks, side):
        out.append(f"## {title}")
        if not picks:
            out.append("Сильных кандидатов сегодня нет."); out.append(""); return
        out.append("| Монета | Цена | 24ч | 7д | Объём вчера | Волат. | Мин–макс 24ч | Стоп | Цель | Оценка |")
        out.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in picks:
            sl, tp = levels(r, side)
            out.append(f"| {r['symbol']} | {fmt(r['price'])} | {r['chg24_pct']:+.1f}% | {r['chg7d_pct']:+.1f}% | "
                       f"${r['vol_usd_yday']/1e6:,.0f}M | {r['atr_pct']:.1f}% | {fmt(r['low_24h'])}–{fmt(r['high_24h'])} | "
                       f"{fmt(sl)} | {fmt(tp)} | {r['score']:+.1f} |")
        out.append("")

    table("Кандидаты в лонг", longs, "long")
    table("Кандидаты в шорт", shorts, "short")
    if hot:
        out.append("## Резкие движения (не догонять)")
        for r in hot:
            out.append(f"- {r['symbol']}: {r['chg24_pct']:+.1f}% за 24ч, объём вчера ${r['vol_usd_yday']/1e6:,.1f}M")
        out.append("")
    out.append("## Все монеты")
    out.append("| Монета | Цена | 24ч | 7д | Объём вчера | Волат. (σ) | Мин 24ч | Макс 24ч | Мин 14д | Макс 14д | Оценка |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        out.append(f"| {r['symbol']} | {fmt(r['price'])} | {r['chg24_pct']:+.1f}% | {r['chg7d_pct']:+.1f}% | "
                   f"${r['vol_usd_yday']/1e6:,.1f}M | {r['volatility_pct']:.1f}% | {fmt(r['low_24h'])} | {fmt(r['high_24h'])} | "
                   f"{fmt(r['low_14d'])} | {fmt(r['high_14d'])} | {r['score']:+.1f} |")
    out += ["", "Стоп = 1,5 среднего дневного диапазона (за 7 дней), цель = 3 диапазона (риск/прибыль 1:2). "
            "Оценка: импульс за 7 дней, тренд к 14‑дневной средней, положение в 14‑дневном диапазоне, "
            "всплеск объёма и перекос позиций толпы (контр‑сигнал). Это не инвестиционная рекомендация."]
    text = "\n".join(out)
    open(os.path.join(daily_dir, f"report_{date}.md"), "w").write(text)
    print(text)


if __name__ == "__main__":
    main()

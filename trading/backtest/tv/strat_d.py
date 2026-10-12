"""Ports of popular TradingView community strategies/scripts (batch D). See engine.py for the contract.

Every value on row i uses rows <= i only. Signal-only scripts are traded stop-and-reverse.
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- helpers (Pine equivalents)
def rma(x, n):
    """Pine ta.rma: SMA seed over the first n valid values, then Wilder smoothing."""
    a = np.asarray(x, float)
    out = np.full(len(a), np.nan)
    valid = np.where(~np.isnan(a))[0]
    if len(valid) < n:
        return out
    s = valid[0]
    if np.isnan(a[s:s + n]).any():
        return pd.Series(a).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    out[s + n - 1] = a[s:s + n].mean()
    for i in range(s + n, len(a)):
        v = a[i] if not np.isnan(a[i]) else out[i - 1]
        out[i] = (out[i - 1] * (n - 1) + v) / n
    return out


def ema(x, n):
    return pd.Series(np.asarray(x, float)).ewm(span=n, adjust=False).mean().to_numpy().copy()


def wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    return pd.Series(np.asarray(x, float)).rolling(n).apply(lambda v: np.dot(v, w) / w.sum(), raw=True).to_numpy()


def true_range(df):
    h, l, c = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return tr


def rsi(c, n):
    c = np.asarray(c, float)
    d = np.r_[np.nan, np.diff(c)]
    up = rma(np.where(np.isnan(d), np.nan, np.maximum(d, 0)), n)
    dn = rma(np.where(np.isnan(d), np.nan, np.maximum(-d, 0)), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(dn == 0, 100.0, np.where(up == 0, 0.0, 100 - 100 / (1 + up / dn)))
    r[np.isnan(up) | np.isnan(dn)] = np.nan
    return r


def shift(a, k=1):
    a = np.asarray(a, float)
    out = np.full(len(a), np.nan)
    if k < len(a):
        out[k:] = a[:len(a) - k]
    return out


def crossover(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return (a > b) & (shift(a) <= shift(b))


def crossunder(a, b):
    return crossover(b, a)


def flip_on(cond):
    """First bar of a True run."""
    cond = np.asarray(cond, bool)
    return cond & ~np.r_[False, cond[:-1]]


def sar(up, dn):
    up, dn = np.asarray(up, bool), np.asarray(dn, bool)
    return {"le": up, "lx": dn, "se": dn, "sx": up}


# ---------------------------------------------------------------- 1. Ichimoku
def ichimoku_tk(df, conv=9, base=26, lag=52, disp=26):
    h, l, c = df["h"], df["l"], df["c"]
    mid = lambda n: ((h.rolling(n).max() + l.rolling(n).min()) / 2).to_numpy()
    tenkan, kijun = mid(conv), mid(base)
    span_a = (tenkan + kijun) / 2
    span_b = mid(lag)
    # cloud plotted at bar i (TV built-in uses offset = displacement - 1)
    a_now, b_now = shift(span_a, disp - 1), shift(span_b, disp - 1)
    top, bot = np.fmax(a_now, b_now), np.fmin(a_now, b_now)
    cc = c.to_numpy()
    up, dn = crossover(tenkan, kijun), crossunder(tenkan, kijun)
    return {"le": up & (cc > top), "lx": dn, "se": dn & (cc < bot), "sx": up}


# ---------------------------------------------------------------- 2. ADX and DI
def adx_di(df, n=14, adx_n=14, thr=20):
    h, l = df["h"].to_numpy(), df["l"].to_numpy()
    upm = np.r_[np.nan, np.diff(h)]
    dnm = np.r_[np.nan, -np.diff(l)]
    pdm = np.where(np.isnan(upm), np.nan, np.where((upm > dnm) & (upm > 0), upm, 0.0))
    mdm = np.where(np.isnan(dnm), np.nan, np.where((dnm > upm) & (dnm > 0), dnm, 0.0))
    tr = true_range(df)
    tr[0] = np.nan
    atr = rma(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * rma(pdm, n) / atr
        mdi = 100 * rma(mdm, n) / atr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    adx = rma(dx, adx_n)
    up, dn = crossover(pdi, mdi), crossunder(pdi, mdi)
    strong = adx > thr
    return {"le": up & strong, "lx": dn, "se": dn & strong, "sx": up}


# ---------------------------------------------------------------- 3. Hull Suite
def hull_suite(df, n=55):
    c = df["c"].to_numpy()
    hma = wma(2 * wma(c, int(n / 2)) - wma(c, n), int(round(np.sqrt(n))))
    green = hma > shift(hma, 2)
    red = hma < shift(hma, 2)
    return sar(flip_on(green), flip_on(red))


# ---------------------------------------------------------------- 4. Heikin Ashi + EMA
def heikin_ashi_ema(df, n=50):
    o, h, l, c = (df[k].to_numpy() for k in "ohlc")
    hc = (o + h + l + c) / 4
    ho = np.empty(len(c))
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    e = ema(c, n)
    e[:n - 1] = np.nan
    green, red = hc > ho, hc < ho
    return {"le": flip_on(green) & (hc > e), "lx": red,
            "se": flip_on(red) & (hc < e), "sx": green}


# ---------------------------------------------------------------- 5. QQE MOD (Mihkel00)
def _qqe(c, rsi_n, sf, qqe):
    wp = rsi_n * 2 - 1
    r = rsi(c, rsi_n)
    rsima = ema(np.nan_to_num(r, nan=50.0), sf)
    rsima[np.isnan(r)] = np.nan
    atr_rsi = np.abs(shift(rsima) - rsima)
    dar = ema(np.nan_to_num(ema(np.nan_to_num(atr_rsi), wp)), wp) * qqe
    n = len(c)
    lb, sb, tr, tl = np.zeros(n), np.zeros(n), np.ones(n), np.full(n, np.nan)
    for i in range(1, n):
        x, x1 = rsima[i], rsima[i - 1]
        if np.isnan(x) or np.isnan(x1):
            lb[i], sb[i] = (x - dar[i], x + dar[i]) if not np.isnan(x) else (0.0, 0.0)
            continue
        nl, ns = x - dar[i], x + dar[i]
        lb[i] = max(lb[i - 1], nl) if (x1 > lb[i - 1] and x > lb[i - 1]) else nl
        sb[i] = min(sb[i - 1], ns) if (x1 < sb[i - 1] and x < sb[i - 1]) else ns
        # Pine cross() = either direction
        cross_s = (x - sb[i - 1]) * (x1 - sb[i - 2] if i >= 2 else np.nan) < 0
        cross_l = (lb[i - 1] - x) * ((lb[i - 2] if i >= 2 else np.nan) - x1) < 0
        tr[i] = 1 if cross_s else (-1 if cross_l else tr[i - 1])
        tl[i] = lb[i] if tr[i] == 1 else sb[i]
    return rsima, tl


def qqe_mod(df):
    c = df["c"].to_numpy()
    rsima1, tl1 = _qqe(c, 6, 5, 3.0)
    rsima2, tl2 = _qqe(c, 6, 5, 1.61)
    thr2 = 3
    base = pd.Series(tl1 - 50)
    basis = base.rolling(50).mean().to_numpy()
    dev = 0.35 * base.rolling(50).std(ddof=0).to_numpy()
    upper, lower = basis + dev, basis - dev
    blue = (rsima2 - 50 > thr2) & (rsima1 - 50 > upper)
    pink = (rsima2 - 50 < -thr2) & (rsima1 - 50 < lower)
    lines_up = (tl1 - 50 > 0) & (tl2 - 50 > 0)
    lines_dn = (tl1 - 50 < 0) & (tl2 - 50 < 0)
    long_ok, short_ok = blue & lines_up, pink & lines_dn
    return {"le": flip_on(long_ok), "lx": ~blue, "se": flip_on(short_ok), "sx": ~pink}


# ---------------------------------------------------------------- 6. Turtle System 2
def turtle_s2(df, entry=55, exit_=20, atr_n=20, stop_n=2.0):
    h, l, c = df["h"], df["l"], df["c"].to_numpy()
    hh = h.rolling(entry).max().shift().to_numpy()
    ll = l.rolling(entry).min().shift().to_numpy()
    xh = h.rolling(exit_).max().shift().to_numpy()
    xl = l.rolling(exit_).min().shift().to_numpy()
    tr = true_range(df)
    tr[0] = np.nan
    N = rma(tr, atr_n)
    return {"le": c > hh, "lx": c < xl, "se": c < ll, "sx": c > xh, "stop": stop_n * N / c}


# ---------------------------------------------------------------- 7. Golden cross
def golden_cross(df):
    c = df["c"]
    f, s = c.rolling(50).mean().to_numpy(), c.rolling(200).mean().to_numpy()
    return sar(crossover(f, s), crossunder(f, s))


# ---------------------------------------------------------------- 8. Coral Trend (LazyBear)
def coral_trend(df, sm=21, cd=0.4):
    src = df["c"].to_numpy()
    di = (sm - 1.0) / 2.0 + 1.0
    c1 = 2 / (di + 1.0)
    c2 = 1 - c1
    c3 = 3.0 * (cd * cd + cd ** 3)
    c4 = -3.0 * (2.0 * cd * cd + cd + cd ** 3)
    c5 = 3.0 * cd + 1.0 + cd ** 3 + 3.0 * cd * cd
    n = len(src)
    i1 = np.zeros(n); i2 = np.zeros(n); i3 = np.zeros(n); i4 = np.zeros(n); i5 = np.zeros(n); i6 = np.zeros(n)
    p = [0.0] * 6
    for k in range(n):
        p[0] = c1 * src[k] + c2 * p[0]
        for j in range(1, 6):
            p[j] = c1 * p[j - 1] + c2 * p[j]
        i1[k], i2[k], i3[k], i4[k], i5[k], i6[k] = p
    bfr = -cd ** 3 * i6 + c3 * i5 + c4 * i4 + c5 * i3
    prev = shift(bfr)
    up, dn = bfr > prev, bfr < prev
    warm = np.arange(n) >= 60  # zero-seeded cascade needs warm-up
    return sar(flip_on(up) & warm, flip_on(dn) & warm)


STRATEGIES = {
    "Ichimoku TK cross": ichimoku_tk,
    "ADX and DI cross (ADX>20)": adx_di,
    "Hull Suite HMA 55": hull_suite,
    "Heikin Ashi + EMA 50": heikin_ashi_ema,
    "QQE MOD (Mihkel00)": qqe_mod,
    "Turtle System 2 (55/20, 2N)": turtle_s2,
    "Golden Cross 50/200": golden_cross,
    "Coral Trend (LazyBear)": coral_trend,
}

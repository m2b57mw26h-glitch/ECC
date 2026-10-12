"""Ports of popular open-source TradingView community scripts (batch C).

Each function follows the engine contract: sig(df) -> {le, lx, se, sx[, stop]}; values on row i use rows <= i only.
Pine semantics reproduced: ta.rma/ta.ema seeded with an SMA of the first `n` valid values, ta.stdev is the
population stdev, ta.tr on the first bar is high - low, nz() -> 0.
"""
import numpy as np
import pandas as pd


# ---------- Pine-like helpers ----------

def _seeded(x, n, alpha):
    """Recursive MA (ema/rma) seeded with the SMA of the first n valid values, like Pine."""
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    valid = np.where(~np.isnan(x))[0]
    if len(valid) < n:
        return out
    s = valid[0]
    i0 = s + n - 1
    out[i0] = np.mean(x[s:i0 + 1])
    for i in range(i0 + 1, len(x)):
        v = x[i]
        out[i] = out[i - 1] if np.isnan(v) else alpha * v + (1 - alpha) * out[i - 1]
    return out


def ema(x, n):
    return _seeded(x, n, 2.0 / (n + 1))


def rma(x, n):
    return _seeded(x, n, 1.0 / n)


def tr(df):
    h, l, c = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    pc = np.roll(c, 1)
    t = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    t[0] = h[0] - l[0]
    return t


def atr(df, n):
    return rma(tr(df), n)


def sma(x, n):
    return pd.Series(np.asarray(x, float)).rolling(n).mean().to_numpy()


def stdev(x, n):
    return pd.Series(np.asarray(x, float)).rolling(n).std(ddof=0).to_numpy()


def highest(x, n):
    return pd.Series(np.asarray(x, float)).rolling(n).max().to_numpy()


def lowest(x, n):
    return pd.Series(np.asarray(x, float)).rolling(n).min().to_numpy()


def linreg(x, n):
    """ta.linreg(x, n, 0): least-squares line over the last n bars, value at the newest bar."""
    xs = np.arange(n, dtype=float)
    xbar = xs.mean()
    sxx = ((xs - xbar) ** 2).sum()
    w = 1.0 / n + (xs - xbar) * (n - 1 - xbar) / sxx  # weight of y[k] (k=0 oldest)
    return pd.Series(np.asarray(x, float)).rolling(n).apply(lambda y: float(np.dot(w, y)), raw=True).to_numpy()


def _shift(a, k=1, fill=np.nan):
    a = np.asarray(a, float)
    out = np.full(len(a), fill)
    out[k:] = a[:-k]
    return out


def crossover(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return (a > b) & (_shift(a) <= _shift(b))


def crossunder(a, b):
    return crossover(b, a)


def _sar(buy, sell):
    buy, sell = np.asarray(buy, bool), np.asarray(sell, bool)
    return {"le": buy, "lx": sell, "se": sell, "sx": buy}


# ---------- 1. UT Bot Alerts ----------

def ut_bot(df, a=1.0, c_=10):
    src = df["c"].to_numpy()
    nloss = a * atr(df, c_)
    n = len(src)
    ts = np.zeros(n)
    prev = 0.0
    for i in range(n):
        if np.isnan(nloss[i]):
            ts[i] = np.nan
            prev = 0.0  # nz(na) = 0
            continue
        s1 = src[i - 1] if i > 0 else np.nan
        if src[i] > prev and s1 > prev:
            v = max(prev, src[i] - nloss[i])
        elif src[i] < prev and s1 < prev:
            v = min(prev, src[i] + nloss[i])
        elif src[i] > prev:
            v = src[i] - nloss[i]
        else:
            v = src[i] + nloss[i]
        ts[i] = v
        prev = v
    above = crossover(src, ts)
    below = crossover(ts, src)
    buy = (src > ts) & above
    sell = (src < ts) & below
    return _sar(buy, sell)


# ---------- 2. Squeeze Momentum (LazyBear) ----------

def squeeze_momentum(df, length=20, mult=2.0, length_kc=20, mult_kc=1.5):
    c, h, l = df["c"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy()
    basis = sma(c, length)
    dev = mult_kc * stdev(c, length)  # original LazyBear code uses multKC here (well-known quirk), kept as published
    upper_bb, lower_bb = basis + dev, basis - dev
    ma = sma(c, length_kc)
    rangema = sma(tr(df), length_kc)  # useTrueRange = true
    upper_kc, lower_kc = ma + rangema * mult_kc, ma - rangema * mult_kc
    sqz_on = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    sqz_off = (lower_bb < lower_kc) & (upper_bb > upper_kc)
    mid = ((highest(h, length_kc) + lowest(l, length_kc)) / 2 + sma(c, length_kc)) / 2
    val = linreg(c - mid, length_kc)
    vp = _shift(val)
    release = sqz_off & np.r_[False, sqz_on[:-1]]
    rising, falling = val > vp, val < vp
    return {"le": release & (val > 0) & rising, "lx": falling,
            "se": release & (val < 0) & falling, "sx": rising}


# ---------- 3. Chandelier Exit (everget) ----------

def chandelier_exit(df, length=22, mult=3.0):
    c = df["c"].to_numpy()
    a = mult * atr(df, length)
    ls_raw = highest(c, length) - a
    ss_raw = lowest(c, length) + a
    n = len(c)
    ls, ss = np.full(n, np.nan), np.full(n, np.nan)
    d = np.ones(n, int)
    dirv = 1
    for i in range(n):
        lsp = ls[i - 1] if i > 0 and not np.isnan(ls[i - 1]) else ls_raw[i]
        ssp = ss[i - 1] if i > 0 and not np.isnan(ss[i - 1]) else ss_raw[i]
        c1 = c[i - 1] if i > 0 else np.nan
        ls[i] = max(ls_raw[i], lsp) if c1 > lsp else ls_raw[i]
        ss[i] = min(ss_raw[i], ssp) if c1 < ssp else ss_raw[i]
        if c[i] > ssp:
            dirv = 1
        elif c[i] < lsp:
            dirv = -1
        d[i] = dirv
    dp = np.r_[d[0], d[:-1]]
    return _sar((d == 1) & (dp == -1), (d == -1) & (dp == 1))


# ---------- 4. HalfTrend (everget) ----------

def halftrend(df, amplitude=2, channel_dev=2):
    h, l, c = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    n = len(c)
    atr2 = atr(df, 100) / 2
    high_price = highest(h, amplitude)
    low_price = lowest(l, amplitude)
    highma, lowma = sma(h, amplitude), sma(l, amplitude)
    trend = np.zeros(n, int)
    t, nxt = 0, 0
    max_low = l[0]
    min_high = h[0]
    for i in range(n):
        l1 = l[i - 1] if i > 0 else l[i]
        h1 = h[i - 1] if i > 0 else h[i]
        if nxt == 1:
            max_low = max(low_price[i], max_low) if not np.isnan(low_price[i]) else max_low
            if highma[i] < max_low and c[i] < l1:
                t, nxt = 1, 0
                min_high = high_price[i]
        else:
            min_high = min(high_price[i], min_high) if not np.isnan(high_price[i]) else min_high
            if lowma[i] > min_high and c[i] > h1:
                t, nxt = 0, 1
                max_low = low_price[i]
        trend[i] = t
    tp = np.r_[trend[0], trend[:-1]]
    ok = ~np.isnan(atr2)  # arrows are na until atr(100) exists
    return _sar(ok & (trend == 0) & (tp == 1), ok & (trend == 1) & (tp == 0))


# ---------- 5. WaveTrend (LazyBear) ----------

def wavetrend(df, n1=10, n2=21, ob=53, os_=-53):
    ap = ((df["h"] + df["l"] + df["c"]) / 3).to_numpy()
    esa = ema(ap, n1)
    d = ema(np.abs(ap - esa), n1)
    ci = (ap - esa) / (0.015 * d)
    wt1 = ema(ci, n2)
    wt2 = sma(wt1, 4)
    up, dn = crossover(wt1, wt2), crossunder(wt1, wt2)
    return {"le": up & (wt1 < os_), "lx": dn, "se": dn & (wt1 > ob), "sx": up}


# ---------- 6. SSL Channel (ErwinBeckers) ----------

def ssl_channel(df, length=10):
    c = df["c"].to_numpy()
    sh, sl = sma(df["h"], length), sma(df["l"], length)
    n = len(c)
    hlv = np.zeros(n)
    prev = np.nan
    for i in range(n):
        prev = 1.0 if c[i] > sh[i] else (-1.0 if c[i] < sl[i] else prev)
        hlv[i] = prev
    ssl_down = np.where(hlv < 0, sh, sl)
    ssl_up = np.where(hlv < 0, sl, sh)
    return _sar(crossover(ssl_up, ssl_down), crossunder(ssl_up, ssl_down))


# ---------- 7. CM Williams Vix Fix (ChrisMoody) ----------

def williams_vix_fix(df, pd_=22, bbl=20, mult=2.0, lb=50, ph=0.85, hold=5):
    c, l = df["c"].to_numpy(), df["l"].to_numpy()
    hc = highest(c, pd_)
    wvf = (hc - l) / hc * 100
    upper = sma(wvf, bbl) + mult * stdev(wvf, bbl)
    range_high = highest(wvf, lb) * ph
    le = (wvf >= upper) | (wvf >= range_high)
    le = np.nan_to_num(le.astype(float)) > 0
    lx = np.r_[np.zeros(hold, bool), le[:-hold]]  # time exit: 5 bars after the entry signal
    n = len(c)
    return {"le": le, "lx": lx, "se": np.zeros(n, bool), "sx": np.zeros(n, bool)}


# ---------- 8. Range Filter (guikroth / DonovanWall) ----------

def range_filter(df, per=100, mult=3.0):
    src = df["c"].to_numpy()
    n = len(src)
    avrng = ema(np.abs(src - _shift(src)), per)
    smrng = ema(avrng, per * 2 - 1) * mult
    filt = np.zeros(n)
    prev = 0.0
    for i in range(n):
        r = smrng[i]
        x = src[i]
        if np.isnan(r):
            v = x  # rngfilt = x when r is na (comparisons with na are false -> nz(prev)/x+r na); keep x
        elif x > prev:
            v = prev if x - r < prev else x - r
        else:
            v = prev if x + r > prev else x + r
        filt[i] = v
        prev = v
    fp = _shift(filt)
    upward, downward = np.zeros(n), np.zeros(n)
    for i in range(1, n):
        upward[i] = upward[i - 1] + 1 if filt[i] > fp[i] else (0 if filt[i] < fp[i] else upward[i - 1])
        downward[i] = downward[i - 1] + 1 if filt[i] < fp[i] else (0 if filt[i] > fp[i] else downward[i - 1])
    sp = _shift(src)
    valid = ~np.isnan(smrng)
    long_c = valid & (src > filt) & ((src > sp) | (src < sp)) & (upward > 0)
    short_c = valid & (src < filt) & ((src > sp) | (src < sp)) & (downward > 0)
    cond = np.zeros(n, int)
    ci = 0
    for i in range(n):
        cond[i] = ci  # CondIni[1]
        ci = 1 if long_c[i] else (-1 if short_c[i] else ci)
    return _sar(long_c & (cond == -1), short_c & (cond == 1))


STRATEGIES = {
    "UT Bot 1/10": ut_bot,
    "Squeeze Momentum LB 20/2/20/1.5": squeeze_momentum,
    "Chandelier Exit 22/3": chandelier_exit,
    "HalfTrend 2/2": halftrend,
    "WaveTrend LB 10/21 +-53": wavetrend,
    "SSL Channel 10": ssl_channel,
    "CM Williams VixFix 22/20/2 long 5bar": williams_vix_fix,
    "Range Filter 100/3": range_filter,
}

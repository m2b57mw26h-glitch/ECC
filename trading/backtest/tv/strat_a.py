"""TradingView built-in strategies (Pine standard library), default inputs, daily bars.

Each function returns stop-and-reverse signals: long exit == short entry and short exit == long entry.
None of these built-ins has its own protective stop, so no `stop` array is returned (engine uses 9%).
Indicators follow Pine semantics: ta.ema / ta.rma are seeded with an SMA of the first `length` values,
ta.stdev is the population standard deviation, ta.atr = rma(true range).
Every value on row i uses rows <= i only.
"""
import numpy as np
import pandas as pd


# ---------- Pine-compatible helpers ----------

def _seeded(src, length, alpha):
    """Recursive average seeded with SMA(length) of the first valid values (Pine ta.ema / ta.rma)."""
    x = np.asarray(src, float)
    out = np.full(len(x), np.nan)
    valid = np.where(~np.isnan(x))[0]
    if len(valid) < length:
        return out
    s = valid[0]
    k = s + length - 1
    out[k] = x[s:k + 1].mean()
    for i in range(k + 1, len(x)):
        v = x[i]
        out[i] = out[i - 1] if np.isnan(v) else alpha * v + (1 - alpha) * out[i - 1]
    return out


def ema(src, length):
    return _seeded(src, length, 2.0 / (length + 1))


def rma(src, length):
    return _seeded(src, length, 1.0 / length)


def true_range(df):
    h, l, pc = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].shift().to_numpy()
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    return tr


def atr(df, length):
    return rma(true_range(df), length)


def _pair(a, b):
    a = np.asarray(a, float)
    b = np.broadcast_to(np.asarray(b, float), a.shape)
    pa, pb = np.roll(a, 1), np.roll(b, 1)
    pa[0] = pb[0] = np.nan
    return a, b, pa, pb


def crossover(a, b):
    a, b, pa, pb = _pair(a, b)
    return (a > b) & (pa <= pb)


def crossunder(a, b):
    a, b, pa, pb = _pair(a, b)
    return (a < b) & (pa >= pb)


def _sar(le, se):
    le, se = np.asarray(le, bool), np.asarray(se, bool)
    return {"le": le, "lx": se.copy(), "se": se, "sx": le.copy()}


# ---------- strategies ----------

def ma_cross_9(df):
    """'MovingAvg Cross' (length 9, confirmBars 1). Exact: market orders on the close of the bar where
    close has been above (below) SMA(9) for exactly confirmBars bars, i.e. the first close above/below."""
    c = df["c"]
    ma = c.rolling(9).mean()
    confirm = 1

    def count(cond):
        g = (~cond).cumsum()
        return cond.astype(int).groupby(g).cumsum().to_numpy()

    le = count(c > ma) == confirm
    se = count(c < ma) == confirm
    return _sar(le, se)


def macd_12_26_9(df):
    """'MACD Strategy' (12, 26, 9). Exact: market orders when the histogram (MACD - signal) crosses 0."""
    c = df["c"].to_numpy()
    m = ema(c, 12) - ema(c, 26)
    delta = m - ema(m, 9)
    return _sar(crossover(delta, 0), crossunder(delta, 0))


def rsi_14(df):
    """'RSI Strategy' (14, 30, 70). Exact: RSI crosses over 30 -> long, crosses under 70 -> short (market)."""
    c = df["c"]
    ch = c.diff().to_numpy()
    up = rma(np.where(np.isnan(ch), np.nan, np.maximum(ch, 0)), 14)
    dn = rma(np.where(np.isnan(ch), np.nan, np.maximum(-ch, 0)), 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(dn == 0, 100.0, np.where(up == 0, 0.0, 100 - 100 / (1 + up / dn)))
    r[np.isnan(up) | np.isnan(dn)] = np.nan
    return _sar(crossover(r, 30), crossunder(r, 70))


def bbands_20_2(df):
    """'Bollinger Bands Strategy' (20, 2). Close crosses over the lower band -> long, under the upper -> short.
    Approximation: the built-in places a stop order at the band; since the close is already beyond the band
    the stop is marketable, so it is modelled as a market order at the next open (TV would fill at
    max(open, band) for longs - nearly always the open)."""
    c = df["c"]
    basis = c.rolling(20).mean()
    dev = 2.0 * c.rolling(20).std(ddof=0)
    return _sar(crossover(c, basis - dev), crossunder(c, basis + dev))


def keltner_20_2(df):
    """'Keltner Channels Strategy' (EMA 20, mult 2, bands = ATR(10)). After close crosses over the upper
    channel the built-in rests a buy stop at that bar's high (short: sell stop at the cross bar's low);
    the order is cancelled when close falls back below the EMA basis (above it for shorts).
    Approximation: the stop is treated as triggered on the first later bar whose high reaches the level
    (low for shorts) and the entry fills at the following open instead of at the stop price."""
    c, h, l = df["c"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy()
    ma = ema(c, 20)
    rng = atr(df, 10)
    upper, lower = ma + 2 * rng, ma - 2 * rng
    cu, cl = crossover(c, upper), crossunder(c, lower)
    n = len(df)
    le, se = np.zeros(n, bool), np.zeros(n, bool)
    blvl = slvl = np.nan
    for i in range(n):
        if not np.isnan(blvl):
            if h[i] >= blvl:
                le[i], blvl = True, np.nan
            elif c[i] < ma[i]:
                blvl = np.nan
        if not np.isnan(slvl):
            if l[i] <= slvl:
                se[i], slvl = True, np.nan
            elif c[i] > ma[i]:
                slvl = np.nan
        if cu[i]:
            blvl = h[i]
        if cl[i]:
            slvl = l[i]
    both = le & se
    up_bar = c >= df["o"].to_numpy()
    le[both & ~up_bar] = False
    se[both & up_bar] = False
    return _sar(le, se)


def psar_002(df):
    """'Parabolic SAR Strategy' (0.02, 0.02, 0.2). The built-in rests a stop-and-reverse order at the next
    bar's SAR. Approximation: the bar on which price crosses the SAR (the trend flip in the built-in's own
    SAR recursion) gives the signal; the reversal fills at the next open instead of at the SAR level."""
    h, l, c = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    start, inc, mx = 0.02, 0.02, 0.2
    n = len(df)
    trend = np.zeros(n, int)  # 1 up, -1 down, 0 undefined
    up = None
    ep = sar = nxt = np.nan
    af = start
    for i in range(1, n):
        first = False
        sar = nxt
        if i == 1:
            if c[i] > c[i - 1]:
                up, ep, psar, pep = True, h[i], l[i - 1], h[i]
            else:
                up, ep, psar, pep = False, l[i], h[i - 1], l[i]
            first = True
            sar = psar + start * (pep - psar)
        if up:
            if sar > l[i]:
                first, up = True, False
                sar, ep, af = max(ep, h[i]), l[i], start
        else:
            if sar < h[i]:
                first, up = True, True
                sar, ep, af = min(ep, l[i]), h[i], start
        if not first:
            if up and h[i] > ep:
                ep, af = h[i], min(af + inc, mx)
            elif not up and l[i] < ep:
                ep, af = l[i], min(af + inc, mx)
        if up:
            sar = min(sar, l[i - 1])
            if i > 1:
                sar = min(sar, l[i - 2])
        else:
            sar = max(sar, h[i - 1])
            if i > 1:
                sar = max(sar, h[i - 2])
        nxt = sar + af * (ep - sar)
        trend[i] = 1 if up else -1
    prev = np.roll(trend, 1)
    prev[0] = 0
    le = (trend == 1) & (prev == -1)
    se = (trend == -1) & (prev == 1)
    return _sar(le, se)


def supertrend_10_3(df):
    """'Supertrend Strategy' (ATR 10, factor 3). Exact: market orders when ta.supertrend direction flips."""
    h, l, c = df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    a = atr(df, 10)
    src = (h + l) / 2
    ub_raw, lb_raw = src + 3 * a, src - 3 * a
    n = len(df)
    ub, lb = np.full(n, np.nan), np.full(n, np.nan)
    d = np.full(n, np.nan)
    st = np.full(n, np.nan)
    for i in range(n):
        plb = 0.0 if i == 0 or np.isnan(lb[i - 1]) else lb[i - 1]
        pub = 0.0 if i == 0 or np.isnan(ub[i - 1]) else ub[i - 1]
        pc = c[i - 1] if i > 0 else np.nan
        lb[i] = lb_raw[i] if (lb_raw[i] > plb or pc < plb) else plb
        ub[i] = ub_raw[i] if (ub_raw[i] < pub or pc > pub) else pub
        if np.isnan(a[i]):
            lb[i], ub[i] = lb_raw[i], ub_raw[i]
        if i == 0 or np.isnan(a[i - 1]):
            d[i] = 1
        elif st[i - 1] == pub:
            d[i] = -1 if c[i] > ub[i] else 1
        else:
            d[i] = 1 if c[i] < lb[i] else -1
        st[i] = lb[i] if d[i] == -1 else ub[i]
    ch = np.diff(d, prepend=np.nan)
    valid = ~np.isnan(a)
    return _sar((ch < 0) & valid, (ch > 0) & valid)


def channel_breakout_5(df):
    """'ChannelBreakOutStrategy' (length 5). The built-in rests a buy stop at highest(high, 5) and a sell stop
    at lowest(low, 5) every bar. Approximation: a bar whose high exceeds the prior bar's 5-bar highest high
    (low below the prior 5-bar lowest low) is the trigger; the entry fills at the next open instead of at the
    channel level. If both sides break on the same bar, the close vs open decides the direction."""
    h, l, c, o = df["h"], df["l"], df["c"].to_numpy(), df["o"].to_numpy()
    upb = h.rolling(5).max().shift()
    dnb = l.rolling(5).min().shift()
    ok = df["c"].shift(5).notna().to_numpy()
    le = (h > upb).to_numpy() & ok
    se = (l < dnb).to_numpy().copy()
    both = le & se
    le[both & (c < o)] = False
    se[both & (c >= o)] = False
    return _sar(le, se)


STRATEGIES = {
    "TV: MovingAvg Cross 9": ma_cross_9,
    "TV: MACD 12/26/9": macd_12_26_9,
    "TV: RSI 14 30/70": rsi_14,
    "TV: Bollinger Bands 20/2": bbands_20_2,
    "TV: Keltner Channels 20/2": keltner_20_2,
    "TV: Parabolic SAR 0.02/0.2": psar_002,
    "TV: Supertrend 10/3": supertrend_10_3,
    "TV: ChannelBreakOut 5": channel_breakout_5,
}

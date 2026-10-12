"""TradingView built-in strategies (batch B), ported to the engine.py contract.

All are stop-and-reverse (strategy.entry reverses an open position), so the exit of one side
equals the entry of the other side. None of these built-ins has its own stop-loss, so no
`stop` array is returned (the engine applies its 9% hard stop).

Built-ins that use stop *entry* orders (Momentum, Pivot Reversal, Price Channel, Volty Expan Close)
are approximated as follows: the order placed on the close of bar i-1 is live during bar i;
if bar i's range crosses the stop level, the signal is raised on the close of bar i and the engine
fills at the open of bar i+1 (one bar later and at a different price than TradingView's fill).
"""
import numpy as np
import pandas as pd


def _sar(long_sig, short_sig):
    le = np.asarray(long_sig, bool)
    se = np.asarray(short_sig, bool) & ~le
    return {"le": le, "lx": se.copy(), "se": se, "sx": le.copy()}


def _both_hit(o, h, l, up_hit, dn_hit):
    """Resolve a bar where both stop orders trigger like TV's broker emulator: if the open is
    closer to the high, the path is open->high->low (long first, then reversed short), else
    open->low->high (final long). Returns (long, short) with the final side only."""
    both = up_hit & dn_hit
    high_first = (h - o) <= (o - l)
    long_ = up_hit & ~(both & high_first)
    short_ = dn_hit & ~(both & ~high_first)
    return long_, short_


def momentum_12(df):
    """TV 'Momentum Strategy' (length 12). mom0 = close - close[12], mom1 = mom0 - mom0[1].
    Original: while mom0>0 and mom1>0, buy-stop at high+mintick (short: sell-stop at low-mintick
    while both <0). Approximation: long signal on close of bar i if the condition held on bar i-1
    and high[i] > high[i-1] (the stop would have filled during bar i); fill at next open."""
    c, h, l = df["c"], df["h"], df["l"]
    mom0 = c - c.shift(12)
    mom1 = mom0 - mom0.shift(1)
    cl = ((mom0 > 0) & (mom1 > 0)).shift(1, fill_value=False)
    cs = ((mom0 < 0) & (mom1 < 0)).shift(1, fill_value=False)
    up = cl & (h > h.shift(1))
    dn = cs & (l < l.shift(1))
    return _sar(up.to_numpy(), dn.to_numpy())


def stoch_slow_14_3_3(df):
    """TV 'Stochastic Slow Strategy' (14, smoothK 3, smoothD 3, OS 20 / OB 80). Market orders:
    long when K crosses over D with K < 20, short when K crosses under D with K > 80. Exact."""
    c, h, l = df["c"], df["h"], df["l"]
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    rng = (hh - ll).replace(0, np.nan)
    stoch = 100 * (c - ll) / rng
    k = stoch.rolling(3).mean()
    d = k.rolling(3).mean()
    co = (k > d) & (k.shift(1) <= d.shift(1))
    cu = (k < d) & (k.shift(1) >= d.shift(1))
    ok = k.notna() & d.notna()
    return _sar((ok & co & (k < 20)).to_numpy(), (ok & cu & (k > 80)).to_numpy())


def consecutive_3_3(df):
    """TV 'Consecutive Up/Down Strategy' (3/3). Market orders: long after >=3 consecutive higher
    closes, short after >=3 consecutive lower closes. Exact."""
    c = df["c"].to_numpy()
    n = len(c)
    ups = np.zeros(n)
    dns = np.zeros(n)
    for i in range(1, n):
        ups[i] = ups[i - 1] + 1 if c[i] > c[i - 1] else 0
        dns[i] = dns[i - 1] + 1 if c[i] < c[i - 1] else 0
    return _sar(ups >= 3, dns >= 3)


def inside_bar(df):
    """TV 'InSide Bar Strategy'. Market orders: inside bar (high<high[1], low>low[1]) that closes up
    -> long, closes down -> short. Exact."""
    o, h, l, c = df["o"], df["h"], df["l"], df["c"]
    ins = (h < h.shift(1)) & (l > l.shift(1))
    return _sar((ins & (c > o)).to_numpy(), (ins & (c < o)).to_numpy())


def outside_bar(df):
    """TV 'OutSide Bar Strategy'. Market orders: outside bar (high>high[1], low<low[1]) that closes
    up -> long, closes down -> short. Exact."""
    o, h, l, c = df["o"], df["h"], df["l"], df["c"]
    out = (h > h.shift(1)) & (l < l.shift(1))
    return _sar((out & (c > o)).to_numpy(), (out & (c < o)).to_numpy())


def pivot_reversal_4_2(df):
    """TV 'Pivot Reversal Strategy' (left 4, right 2). A pivot high is confirmed 2 bars after it
    (strictly above the 4 left bars, >= the 2 right bars), so it is only used from that bar on.
    Original: after a pivot high, buy-stop at the pivot price until it is hit (mirror for pivot
    lows with a sell-stop). Approximation: long signal on close of bar i when the buy-stop was live
    after bar i-1 and high[i] > pivot price; fill at next open. If both stops hit on one bar the
    TV open-proximity path rule picks the final side."""
    o, h, l = df["o"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy()
    n = len(h)
    L, R = 4, 2
    up_hit = np.zeros(n, bool)
    dn_hit = np.zeros(n, bool)
    hprice = lprice = np.nan
    le = se = False
    for i in range(n):
        # orders live during bar i were placed on close of i-1
        if le and not np.isnan(hprice) and h[i] > hprice:
            up_hit[i] = True
        if se and not np.isnan(lprice) and l[i] < lprice:
            dn_hit[i] = True
        # state update at close of bar i (pivot at p = i-R, known now)
        p = i - R
        swh = swl = False
        if p - L >= 0:
            if h[p] > h[p - L:p].max() and h[p] >= h[p + 1:i + 1].max():
                swh = True
            if l[p] < l[p - L:p].min() and l[p] <= l[p + 1:i + 1].min():
                swl = True
        if swh:
            hprice = h[p]
            le = True
        elif le and h[i] > hprice:
            le = False
        if swl:
            lprice = l[p]
            se = True
        elif se and l[i] < lprice:
            se = False
    long_, short_ = _both_hit(o, h, l, up_hit, dn_hit)
    return _sar(long_, short_)


def price_channel_20(df):
    """TV 'Price Channel Strategy' (length 20). Original: buy-stop at highest(high,20) and sell-stop
    at lowest(low,20), both computed including the current bar. Approximation: long signal on close
    of bar i if high[i] >= highest(high,20) as of bar i-1 (short: low[i] <= lowest(low,20)[i-1]);
    fill at next open. Both hit on one bar -> TV open-proximity path rule picks the final side."""
    o, h, l, c = df["o"], df["h"], df["l"], df["c"]
    hh = h.rolling(20).max().shift(1)
    ll = l.rolling(20).min().shift(1)
    ready = c.shift(20).notna().shift(1, fill_value=False)
    up = ready & (h >= hh)
    dn = ready & (l <= ll)
    long_, short_ = _both_hit(o.to_numpy(), h.to_numpy(), l.to_numpy(), up.to_numpy(), dn.to_numpy())
    return _sar(long_, short_)


def volty_expan_close_5(df):
    """TV 'Volty Expan Close Strategy' (length 5, numATRs 0.75). atrs = SMA(TR,5)*0.75; original
    places buy-stop at close+atrs and sell-stop at close-atrs every bar. Approximation: long signal
    on close of bar i if high[i] >= close[i-1]+atrs[i-1] (short: low[i] <= close[i-1]-atrs[i-1]);
    fill at next open. Both hit on one bar -> TV open-proximity path rule picks the final side."""
    o, h, l, c = df["o"], df["h"], df["l"], df["c"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - l.iloc[0]
    atrs = tr.rolling(5).mean() * 0.75
    ready = c.shift(5).notna()
    upl = (c + atrs).shift(1)
    dnl = (c - atrs).shift(1)
    rp = ready.shift(1, fill_value=False)
    up = rp & (h >= upl)
    dn = rp & (l <= dnl)
    long_, short_ = _both_hit(o.to_numpy(), h.to_numpy(), l.to_numpy(), up.to_numpy(), dn.to_numpy())
    return _sar(long_, short_)


STRATEGIES = {
    "TV: Momentum 12": momentum_12,
    "TV: Stochastic Slow 14/3/3": stoch_slow_14_3_3,
    "TV: Consecutive Up/Down 3/3": consecutive_3_3,
    "TV: InSide Bar": inside_bar,
    "TV: OutSide Bar": outside_bar,
    "TV: Pivot Reversal 4/2": pivot_reversal_4_2,
    "TV: Price Channel 20": price_channel_20,
    "TV: Volty Expan Close 5/0.75": volty_expan_close_5,
}

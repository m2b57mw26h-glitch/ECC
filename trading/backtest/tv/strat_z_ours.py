"""Our TOP6 strategies (backtest/compare.py) on the same engine, for comparison with the TradingView ports."""
import numpy as np
import pandas as pd


def _rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def macd_ema50(df):
    """MACD(12,26,9) signal-line cross, only on the side of EMA50; exit on the opposite cross."""
    c = df["c"]
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    s = m.ewm(span=9, adjust=False).mean()
    e = c.ewm(span=50, adjust=False).mean()
    up = (m > s) & (m.shift() <= s.shift())
    dn = (m < s) & (m.shift() >= s.shift())
    ok = np.arange(len(c)) >= 50
    return {"le": (up & (c > e)).to_numpy() & ok, "lx": dn.to_numpy(),
            "se": (dn & (c < e)).to_numpy() & ok, "sx": up.to_numpy()}


def turtle_20_10(df):
    """Turtle system 1: close above the prior 20-day high / below the prior 20-day low; exit on a 10-day opposite break."""
    c, h, l = df["c"], df["h"], df["l"]
    return {"le": (c > h.rolling(20).max().shift()).to_numpy(), "lx": (c < l.rolling(10).min().shift()).to_numpy(),
            "se": (c < l.rolling(20).min().shift()).to_numpy(), "sx": (c > h.rolling(10).max().shift()).to_numpy()}


def strategy_b(df):
    """Our strategy B: RSI(3)<22, EMA100 rising over 5 days, close >=1% under 10-day VWAP; exit RSI(3)>55. Long only."""
    c = df["c"]
    tp = (df["h"] + df["l"] + c) / 3
    vwap = (tp * df["v"]).rolling(10).sum() / df["v"].rolling(10).sum()
    e100 = c.ewm(span=100, adjust=False).mean()
    r3 = _rsi(c, 3)
    le = (r3 < 22) & (e100 > e100.shift(5)) & (c <= vwap * 0.99) & (np.arange(len(c)) >= 100)
    return {"le": le.to_numpy(), "lx": (r3 > 55).to_numpy()}


def connors_rsi2(df):
    """Connors RSI(2): close above SMA200, RSI(2)<10 -> long; exit close > SMA5. Long only."""
    c = df["c"]
    le = (c > c.rolling(200).mean()) & (_rsi(c, 2) < 10)
    return {"le": le.to_numpy(), "lx": (c > c.rolling(5).mean()).to_numpy()}


STRATEGIES = {
    "Ours: MACD + EMA50": macd_ema50,
    "Ours: Turtle 20/10": turtle_20_10,
    "Ours: Strategy B (RSI3 pullback)": strategy_b,
    "Ours: Connors RSI(2)": connors_rsi2,
}

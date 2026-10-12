"""Example strategy file. Each file exports STRATEGIES = {name: sig_fn}; see engine.py for the contract."""
import numpy as np


def sma_cross_9_18(df):
    """TradingView built-in 'MovingAvg2Line Cross': SMA(9) crosses SMA(18); stop-and-reverse."""
    c = df["c"]
    f, s = c.rolling(9).mean(), c.rolling(18).mean()
    up = (f > s) & (f.shift() <= s.shift())
    dn = (f < s) & (f.shift() >= s.shift())
    return {"le": up.to_numpy(), "lx": dn.to_numpy(), "se": dn.to_numpy(), "sx": up.to_numpy()}


STRATEGIES = {"_example SMA 9/18 cross": sma_cross_9_18}

"""Strategy S (4h pullback in trend, SL 4% / TP 6%) ported to NautilusTrader 1.231 BacktestEngine.

Usage: python nt_swing.py <raw4h_dir> [<raw_daily_dir> for the BTC filter]
Needs: pip install nautilus_trader==1.231.0
Mirrors swing4h.py "pullback / all coins / SL4 TP6" with its liquidity filter and, when the daily
dir is given, its BTC daily filter. No 42-bar time exit. RSI(3)<15 with close>EMA50 and EMA20>EMA50 -> long; mirrored -> short.
"""
import glob
import json
import os
import sys
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.backtest.models import MakerTakerFeeModel
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import CurrencyType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

VENUE = Venue("LIQUID")


class RSI3:
    """Plain Wilder RSI matching mr_backtest.rsi (0..100)."""

    def __init__(self, n):
        self.n, self.prev, self.ag, self.al, self.k, self.value = n, None, 0.0, 0.0, 0, None

    def update(self, c):
        if self.prev is not None:
            ch = c - self.prev
            g, l = max(ch, 0.0), max(-ch, 0.0)
            self.k += 1
            if self.k <= self.n:
                self.ag += g / self.n
                self.al += l / self.n
            else:
                self.ag = (self.ag * (self.n - 1) + g) / self.n
                self.al = (self.al * (self.n - 1) + l) / self.n
            if self.k >= self.n:
                self.value = 100.0 if self.al == 0 else 100 - 100 / (1 + self.ag / self.al)
        self.prev = c


class SwingConfig(StrategyConfig, frozen=True):
    bar_types: list[str]
    risk: float = 0.01
    sl_pct: float = 0.04
    tp_pct: float = 0.06
    max_open: int = 6
    warmup: int = 50


class SwingS(Strategy):
    def __init__(self, config: SwingConfig):
        super().__init__(config)
        self.bt = [BarType.from_str(b) for b in config.bar_types]
        self.ind = {}
        self.btc_up = None

    def on_start(self):
        for bt in self.bt:
            e20, e50 = ExponentialMovingAverage(20), ExponentialMovingAverage(50)
            self.register_indicator_for_bars(bt, e20)
            self.register_indicator_for_bars(bt, e50)
            self.ind[bt.instrument_id] = (e20, e50, RSI3(3), [0], [])
            self.subscribe_bars(bt)

    def on_bar(self, bar: Bar):
        iid = bar.bar_type.instrument_id
        e20, e50, r3, n, vols = self.ind[iid]
        c = float(bar.close)
        vols.append(float(bar.volume) * c)
        r3.update(c)
        n[0] += 1
        if n[0] < self.config.warmup or r3.value is None:
            return
        if self.cache.positions_open(instrument_id=iid) or self.cache.orders_open(instrument_id=iid):
            return
        if len(self.cache.positions_open()) >= self.config.max_open:
            return
        if sum(vols[-43:-1]) / 42 * 6 < 5e6:
            return
        flt = self.btc_up.get((bar.ts_event - 4 * 3_600_000_000_000) // 86_400_000_000_000 - 1) if self.btc_up is not None else None
        up = c > e50.value and e20.value > e50.value
        down = c < e50.value and e20.value < e50.value
        side = OrderSide.BUY if up and r3.value < 15 else OrderSide.SELL if down and r3.value > 85 else None
        if side is None or (flt is not None and (side == OrderSide.BUY) != flt):
            return
        inst = self.cache.instrument(iid)
        equity = float(self.portfolio.account(VENUE).balance_total(USDT))
        qty = equity * self.config.risk / (self.config.sl_pct * c)
        d = 1 if side == OrderSide.BUY else -1
        bracket = self.order_factory.bracket(
            instrument_id=iid,
            order_side=side,
            quantity=inst.make_qty(qty),
            sl_trigger_price=inst.make_price(c * (1 - d * self.config.sl_pct)),
            tp_price=inst.make_price(c * (1 + d * self.config.tp_pct)),
        )
        self.submit_order_list(bracket)


def decimals(s):
    return len(s.split(".")[1]) if "." in s else 0


def main(d):
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money(10_000, USDT)],
        default_leverage=Decimal(2),
        fee_model=MakerTakerFeeModel(),
        bar_execution=True,
    )
    bar_types = []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        j = json.load(open(p))
        sym, cs = j["symbol"], j["candles"][:-1]
        pp = max(decimals(c[k]) for c in cs for k in ("open", "high", "low", "close"))
        inst = CryptoPerpetual(
            instrument_id=InstrumentId(Symbol(f"{sym}-PERP"), VENUE),
            raw_symbol=Symbol(sym),
            base_currency=Currency(sym, 8, 0, sym, CurrencyType.CRYPTO),
            quote_currency=USDT,
            settlement_currency=USDT,
            is_inverse=False,
            price_precision=pp,
            size_precision=3,
            price_increment=Price(10 ** -pp, pp),
            size_increment=Quantity.from_str("0.001"),
            max_quantity=None, min_quantity=None, max_notional=None, min_notional=None,
            max_price=None, min_price=None,
            margin_init=Decimal("0.5"), margin_maint=Decimal("0.25"),
            maker_fee=Decimal("0.001"), taker_fee=Decimal("0.001"),
            ts_event=0, ts_init=0,
        )
        engine.add_instrument(inst)
        bt = BarType.from_str(f"{inst.id}-4-HOUR-LAST-EXTERNAL")
        bars = [
            Bar(bt, inst.make_price(float(c["open"])), inst.make_price(float(c["high"])),
                inst.make_price(float(c["low"])), inst.make_price(float(c["close"])),
                Quantity.from_str(f"{float(c['volume']):.3f}"),
                (c["timestamp"] + 4 * 3600_000) * 1_000_000, (c["timestamp"] + 4 * 3600_000) * 1_000_000)
            for c in cs
        ]
        engine.add_data(bars)
        bar_types.append(str(bt))
    strat = SwingS(SwingConfig(bar_types=bar_types))
    if len(sys.argv) > 2:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest"))
        import swing4h
        strat.btc_up = swing4h.btc_daily_filter(sys.argv[2])
    engine.add_strategy(strat)
    engine.run()
    pos = engine.trader.generate_positions_report()
    acct = engine.trader.generate_account_report(VENUE)
    closed = pos[pos["is_snapshot"] == False] if "is_snapshot" in pos else pos  # noqa: E712
    closed = closed[closed["ts_closed"].notna()]
    pnl = closed["realized_pnl"].astype(str).str.split(" ").str[0].astype(float)
    wins = (pnl > 0).sum()
    gl = -pnl[pnl <= 0].sum()
    closed = closed.copy()
    closed["ret"] = (closed["avg_px_close"].astype(float) / closed["avg_px_open"].astype(float) - 1) * closed["entry"].map({"BUY": 1, "SELL": -1}) * 100
    # print(closed[["instrument_id", "entry", "avg_px_open", "avg_px_close", "ret", "realized_pnl", "ts_opened", "ts_closed"]].to_string())
    print(f"coins={len(bar_types)} trades={len(pnl)} win={wins / max(len(pnl), 1) * 100:.0f}% "
          f"PF={pnl[pnl > 0].sum() / gl if gl else 99:.2f} pnl={pnl.sum():+.0f} "
          f"final={acct['total'].iloc[-1]}")


if __name__ == "__main__":
    main(sys.argv[1])

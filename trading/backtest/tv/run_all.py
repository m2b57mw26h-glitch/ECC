#!/usr/bin/env python3
"""Run every strategy from strat_*.py through engine.run (both sides and long only), write results.json.

Usage: python3 run_all.py [file_glob]   (default strat_*.py)
"""
import glob
import importlib.util
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = None


def _load_strats(pattern):
    out = {}
    for p in sorted(glob.glob(os.path.join(HERE, pattern))):
        spec = importlib.util.spec_from_file_location(os.path.basename(p)[:-3], p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        for k, fn in m.STRATEGIES.items():
            out[k] = (os.path.basename(p), fn.__name__)
    return out


def _one(args):
    name, (fname, fn_name), mode, pattern = args
    global DATA
    if DATA is None:
        DATA = engine.load()
    spec = importlib.util.spec_from_file_location(fname[:-3], os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    fn = getattr(m, fn_name)
    r = engine.stats(*engine.run(DATA, fn, long_only=(mode == "long only")))
    r["file"] = fname
    return f"{name} [{mode}]", r


if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else "strat_*.py"
    strats = _load_strats(pattern)
    jobs = [(n, v, mode, pattern) for n, v in strats.items() for mode in ("long+short", "long only")]
    with ProcessPoolExecutor(4) as ex:
        res = dict(ex.map(_one, jobs))
    out = os.path.join(HERE, "results.json" if pattern == "strat_*.py" else f"results_{pattern.replace('*', '').replace('.py', '')}.json")
    json.dump({"benchmarks": engine.benchmarks(engine.load()), "results": res}, open(out, "w"), indent=1)
    for k, v in sorted(res.items(), key=lambda kv: -kv[1]["ret_pct"]):
        print(f"{k:55s} ret={v['ret_pct']:+7.1f}% cagr={v['cagr_pct']}% DD={v['max_dd_pct']:5.1f}% n={v['n']:4d} "
              f"win={v['win_pct']}% PF={v['pf']} sh={v['sharpe']} stops={v['stops_pct']}% L={v['long_pnl']} S={v['short_pnl']} {v['years']}")

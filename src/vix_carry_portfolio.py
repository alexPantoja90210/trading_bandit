"""
vix_carry_portfolio.py — ¿el VIX carry (domado) AGREGA a la cartera STF+RSI2, o correlaciona?
Decide si vale la pena: correlaciones + comparar cartera 2-way (STF+RSI2) vs 3-way (+VIX carry),
vol-targeted. Reusa combined_portfolio + cache data/futures/. Solo LEE.
"""
import os
import sys
import numpy as np
import pandas as pd

from mt5_connect import ensure
from combined_portfolio import stf_daily, rsi2_daily, scale_to_vol

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = os.path.join(os.path.dirname(__file__), "data", "futures")


def vix_carry_daily():
    v = pd.read_csv(os.path.join(CACHE, "VIX.csv"), index_col=0, parse_dates=True).iloc[:, 0]
    v3 = pd.read_csv(os.path.join(CACHE, "VIX3M.csv"), index_col=0, parse_dates=True).iloc[:, 0]
    vy = pd.read_csv(os.path.join(CACHE, "VIXY.csv"), index_col=0, parse_dates=True).iloc[:, 0]
    d = pd.DataFrame({"VIX": v, "VIX3M": v3, "VIXY": vy}).dropna()
    ts = d["VIX"]/d["VIX3M"]
    sv = -d["VIXY"].pct_change()
    r = (sv*(ts.shift(1) < 1) - 0.0003*(ts.shift(1) < 1)).dropna()   # short-vol en contango
    r.index = pd.to_datetime(r.index).normalize()
    return r


def metrics(daily):
    r = daily.values
    ann = r.mean()*252; vol = r.std()*np.sqrt(252)
    sh = ann/vol if vol > 0 else 0
    eq = np.cumsum(r); dd = (eq - np.maximum.accumulate(eq)).min()
    return sh, dd*100


def main():
    ensure()
    print("Construyendo STF + RSI2 + VIX carry...")
    stf = stf_daily("XAUUSD").add(stf_daily("BTCUSD", start_year=2013), fill_value=0)
    rsi2 = rsi2_daily("US500").add(rsi2_daily("NAS100"), fill_value=0)
    vix = vix_carry_daily()
    # rango comun (VIX carry manda: 2011+)
    start = max(stf.index.min(), rsi2.index.min(), vix.index.min())
    end = min(stf.index.max(), rsi2.index.max(), vix.index.max())
    idx = pd.bdate_range(start, end)
    stf = scale_to_vol(stf.reindex(idx, fill_value=0.0))
    rsi2 = scale_to_vol(rsi2.reindex(idx, fill_value=0.0))
    vix = scale_to_vol(vix.reindex(idx, fill_value=0.0))
    print(f"Rango comun: {start.date()} -> {end.date()}  ({len(idx)} días)\n")

    print("=== CORRELACIONES diarias (lo que decide) ===")
    M = pd.DataFrame({"STF": stf, "RSI2": rsi2, "VIXcarry": vix})
    c = M.corr()
    print(f"  VIXcarry ~ STF : {c.loc['VIXcarry','STF']:+.2f}")
    print(f"  VIXcarry ~ RSI2: {c.loc['VIXcarry','RSI2']:+.2f}")
    print(f"  STF ~ RSI2     : {c.loc['STF','RSI2']:+.2f}")

    print("\n=== ¿Agregar VIX carry mejora la cartera? (vol-targeted) ===")
    p2 = 0.5*stf + 0.5*rsi2
    p3 = (stf + rsi2 + vix)/3.0
    print(f"{'Cartera':<24}{'Sharpe':>8}{'maxDD%':>9}")
    for name, p in [("STF solo", stf), ("RSI2 solo", rsi2), ("VIXcarry solo", vix),
                    ("2-way (STF+RSI2)", p2), ("3-way (+VIXcarry)", p3)]:
        sh, dd = metrics(p)
        print(f"  {name:<22}{sh:>+8.2f}{dd:>+9.1f}")


if __name__ == "__main__":
    main()

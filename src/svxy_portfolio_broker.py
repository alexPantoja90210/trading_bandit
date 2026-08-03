"""
svxy_portfolio_broker.py — aporte HONESTO del VIX carry a la cartera, en la ventana
REAL del bróker (2018-2026) y con el instrumento REAL (SVXY.US de Pepperstone).

vix_carry_portfolio.py usó VIXY con historia completa 2011+ (número optimista). Aquí:
  - VIX carry = LARGO SVXY.US (bróker) en contango (señal CBOE), sized igual que los otros;
  - todo recortado a la ventana común 2018-2026 (la que de verdad tenemos para operar);
  - 2-way (STF+RSI2) vs 3-way (+VIXcarry), vol-targeted, misma métrica.
Así el delta de cartera es apples-to-apples y sin inflar. Solo LEE.
"""
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from combined_portfolio import stf_daily, rsi2_daily, scale_to_vol
from svxy_live import _from_cboe
from svxy_broker_validate import broker_svxy

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COST = 0.0003


def vix_carry_broker_daily():
    """Retornos diarios de LARGO SVXY.US (bróker) en contango, señal CBOE."""
    svxy = broker_svxy()
    svxy.index = pd.to_datetime(svxy.index).normalize()
    vix = _from_cboe("VIX"); v3 = _from_cboe("VIX3M")
    vix.index = pd.to_datetime(vix.index).normalize(); v3.index = pd.to_datetime(v3.index).normalize()
    ts = (vix / v3).dropna()
    df = pd.DataFrame({"px": svxy, "TS": ts}).dropna()
    ret = df["px"].pct_change()
    sig = (df["TS"].shift(1) < 1.0).astype(float)
    r = (ret * sig - COST * sig).dropna()
    r.index = pd.to_datetime(r.index).normalize()
    return r


def metrics(daily):
    r = daily.values
    ann = r.mean() * 252; vol = r.std() * np.sqrt(252)
    sh = ann / vol if vol > 0 else 0
    eq = np.cumsum(r); dd = (eq - np.maximum.accumulate(eq)).min()
    return sh, dd * 100


def main():
    ensure()
    print("Construyendo STF + RSI2 + VIX carry (SVXY.US bróker)...")
    stf = stf_daily("XAUUSD").add(stf_daily("BTCUSD", start_year=2013), fill_value=0)
    rsi2 = rsi2_daily("US500").add(rsi2_daily("NAS100"), fill_value=0)
    vix = vix_carry_broker_daily()

    # ventana comun REAL del broker (SVXY manda: 2018+)
    start = max(stf.index.min(), rsi2.index.min(), vix.index.min())
    end = min(stf.index.max(), rsi2.index.max(), vix.index.max())
    idx = pd.bdate_range(start, end)
    stf = scale_to_vol(stf.reindex(idx, fill_value=0.0))
    rsi2 = scale_to_vol(rsi2.reindex(idx, fill_value=0.0))
    vixs = scale_to_vol(vix.reindex(idx, fill_value=0.0))
    print(f"Ventana común (bróker): {start.date()} -> {end.date()}  ({len(idx)} días)\n")

    print("=== CORRELACIONES diarias (ventana bróker 2018-2026) ===")
    M = pd.DataFrame({"STF": stf, "RSI2": rsi2, "VIXcarry": vixs})
    c = M.corr()
    print(f"  VIXcarry ~ STF : {c.loc['VIXcarry','STF']:+.2f}")
    print(f"  VIXcarry ~ RSI2: {c.loc['VIXcarry','RSI2']:+.2f}")
    print(f"  STF ~ RSI2     : {c.loc['STF','RSI2']:+.2f}")

    # correlacion condicional a estres (VIX>25 o S&P<-1.5%)
    vx = (_from_cboe("VIX")); vx.index = pd.to_datetime(vx.index).normalize(); vx = vx.reindex(idx).ffill()
    stress = vx > 25
    print(f"  VIXcarry ~ RSI2 en ESTRÉS (VIX>25, n={int(stress.sum())}): "
          f"{np.corrcoef(vixs[stress], rsi2[stress])[0,1]:+.2f}")

    print("\n=== ¿Aporta el VIX carry a la cartera? (vol-targeted, ventana bróker) ===")
    p2 = 0.5 * stf + 0.5 * rsi2
    p3 = (stf + rsi2 + vixs) / 3.0
    print(f"{'Cartera':<24}{'Sharpe':>8}{'maxDD%':>9}")
    for name, p in [("STF solo", stf), ("RSI2 solo", rsi2), ("VIXcarry solo", vixs),
                    ("2-way (STF+RSI2)", p2), ("3-way (+VIXcarry)", p3)]:
        sh, dd = metrics(p)
        print(f"  {name:<22}{sh:>+8.2f}{dd:>+9.1f}")

    print("\n=== Barrido de peso del VIX carry (overlay sobre el 2-way base) ===")
    print(f"  {'peso VIXcarry':>13}{'Sharpe':>8}{'maxDD%':>9}")
    for w in [0.0, 0.10, 0.15, 0.20, 0.30, 0.50]:
        blend = (1 - w) * p2 + w * vixs
        sh, dd = metrics(blend)
        print(f"  {w*100:>12.0f}%{sh:>+8.2f}{dd:>+9.1f}")


if __name__ == "__main__":
    main()

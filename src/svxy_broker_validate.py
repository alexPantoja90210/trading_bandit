"""
svxy_broker_validate.py — cierra el círculo: valida el VIX carry sobre la data REAL
del instrumento del bróker (Pepperstone SVXY.US D1), no la de yfinance.

Compara, en la MISMA ventana (2018-2026):
  (A) estrategia sobre SVXY de yfinance   (lo que validamos)
  (B) estrategia sobre SVXY.US del bróker (lo que realmente vamos a operar)
Señal idéntica: TS = VIX/VIX3M de ayer (CBOE oficial) < 1 → LARGO SVXY.
Además: tracking (correlación de retornos diarios bróker vs yfinance) y tail por año.
Solo LEE.
"""
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from svxy_live import _from_cboe

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COST = 0.0003


def sr(r):
    r = r.dropna()
    if len(r) < 30 or r.std() == 0:
        return 0.0, 0.0, 0.0
    eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min()
    return r.mean() / r.std() * np.sqrt(252), ((1 + r.mean())**252 - 1) * 100, dd * 100


def broker_svxy():
    ensure()
    mt5.symbol_select("SVXY.US", True)
    r = mt5.copy_rates_range("SVXY.US", mt5.TIMEFRAME_D1,
                             datetime(2010, 1, 1, tzinfo=timezone.utc),
                             datetime(2026, 8, 4, tzinfo=timezone.utc))
    idx = [datetime.utcfromtimestamp(x["time"]).date() for x in r]
    s = pd.Series([x["close"] for x in r], index=pd.to_datetime(idx))
    return s[~s.index.duplicated(keep="last")].sort_index()


def yf_svxy():
    import yfinance as yf
    d = yf.download("SVXY", period="max", interval="1d", progress=False, auto_adjust=True)
    s = d["Close"] if "Close" in d.columns else d.iloc[:, 0]
    return pd.Series(np.asarray(s).ravel(), index=pd.to_datetime(d.index).normalize()).dropna()


def strat(px, ts):
    """LARGO px cuando TS de ayer < 1 (contango), con costos. Devuelve retornos diarios."""
    df = pd.DataFrame({"px": px, "TS": ts}).dropna()
    ret = df["px"].pct_change()
    sig = (df["TS"].shift(1) < 1.0).astype(float)
    return (ret * sig - COST * sig).dropna()


def main():
    print("Cargando SVXY del bróker + yfinance + señal CBOE (VIX/VIX3M)...")
    bkr = broker_svxy()
    yfs = yf_svxy()
    vix = _from_cboe("VIX"); v3 = _from_cboe("VIX3M")
    vix.index = pd.to_datetime(vix.index).normalize(); v3.index = pd.to_datetime(v3.index).normalize()
    bkr.index = pd.to_datetime(bkr.index).normalize()
    ts = (vix / v3).dropna()

    # ventana comun (la del broker manda: 2018+)
    start = max(bkr.index.min(), yfs.index.min(), ts.index.min())
    end = min(bkr.index.max(), yfs.index.max(), ts.index.max())
    print(f"Ventana común: {start.date()} -> {end.date()}\n")
    bkr = bkr[(bkr.index >= start) & (bkr.index <= end)]
    yfs = yfs[(yfs.index >= start) & (yfs.index <= end)]

    # --- tracking: ¿el SVXY del bróker se mueve como el de yfinance? ---
    common = bkr.index.intersection(yfs.index)
    rb = bkr.reindex(common).pct_change(); ry = yfs.reindex(common).pct_change()
    m = pd.DataFrame({"b": rb, "y": ry}).dropna()
    corr = np.corrcoef(m["b"], m["y"])[0, 1]
    print(f"=== TRACKING (retornos diarios) — {len(m)} días comunes ===")
    print(f"  corr(SVXY bróker, SVXY yfinance) = {corr:+.4f}   (>0.99 = mismo instrumento)")
    print(f"  vol diaria bróker {rb.std()*100:.2f}%  vs yfinance {ry.std()*100:.2f}%\n")

    # --- estrategia sobre cada fuente ---
    rb_s = strat(bkr, ts); ry_s = strat(yfs, ts)
    print("=== ESTRATEGIA VIX carry (LARGO SVXY en contango, misma señal CBOE) ===")
    print(f"  {'fuente':<20}{'Sharpe':>8}{'annual%':>9}{'maxDD%':>9}")
    for lbl, rr in [("SVXY yfinance", ry_s), ("SVXY.US bróker", rb_s)]:
        s, a, dd = sr(rr)
        print(f"  {lbl:<20}{s:>+8.2f}{a:>+9.1f}{dd:>+9.1f}")

    print("\n=== Robustez por año (Sharpe) — instrumento del bróker ===")
    for y, g in rb_s.groupby(rb_s.index.year):
        s, a, dd = sr(g)
        if len(g) > 30:
            print(f"  {y}: Sharpe {s:+.2f}  ret {a:+.0f}%  DD {dd:+.0f}%")


if __name__ == "__main__":
    main()

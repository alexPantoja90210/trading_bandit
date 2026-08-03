"""
vix_carry_robust.py — RIGOR sobre el VIX carry (el mismo que mato al ORB). Cinco pruebas:
  [1] Robustez del UMBRAL de contango (TS<0.90/0.95/1.0/1.05) — ¿knife-edge?
  [2] Split OOS cronologico 60/40 — ¿aguanta fuera de muestra?
  [3] MECANISMO: E[retorno fwd de VIXY | contango vs backwardation] — ¿el edge ES el roll decay?
  [4] Cross-check en OTRO ETF (SVXY, inverso) — ¿artefacto de VIXY o edge real?
  [5] Correlacion de COLA con RSI2 (dias de estres) — ¿se comprime la diversificacion en crisis?
Solo LEE. Lee cache data/futures/ + MT5 (RSI2).
"""
import os
import sys
import numpy as np
import pandas as pd

from mt5_connect import ensure
from combined_portfolio import rsi2_daily, scale_to_vol

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = os.path.join(os.path.dirname(__file__), "data", "futures")
COST = 0.0003


def load1(n):
    return pd.read_csv(os.path.join(CACHE, f"{n}.csv"), index_col=0, parse_dates=True).iloc[:, 0]


def sr(r):
    r = r.dropna()
    if len(r) < 30 or r.std() == 0:
        return 0.0, 0.0, 0.0
    eq = (1+r).cumprod(); dd = (eq/eq.cummax()-1).min()
    return r.mean()/r.std()*np.sqrt(252), ((1+r.mean())**252-1)*100, dd*100


def main():
    ensure()
    vix, vix3m, vixy = load1("VIX"), load1("VIX3M"), load1("VIXY")
    df = pd.DataFrame({"VIX": vix, "VIX3M": vix3m, "VIXY": vixy}).dropna()
    df["TS"] = df["VIX"]/df["VIX3M"]
    df["sv"] = -df["VIXY"].pct_change()
    df = df.dropna()

    print("=== [1] Robustez del UMBRAL de contango (Sharpe invariante a escala) ===")
    print(f"    {'umbral TS<':>11}{'Sharpe':>8}{'annual%':>9}{'maxDD%':>9}{'% días activo':>14}")
    for thr in [0.90, 0.95, 1.00, 1.05]:
        sig = (df["TS"].shift(1) < thr).astype(float)
        r = df["sv"]*sig - COST*sig
        s, a, dd = sr(r)
        print(f"    {thr:>11.2f}{s:>+8.2f}{a:>+9.1f}{dd:>+9.1f}{sig.mean()*100:>13.0f}%")

    print("\n=== [2] Split OOS cronológico 60/40 (umbral base <1.0) ===")
    sig = (df["TS"].shift(1) < 1.0).astype(float)
    r = (df["sv"]*sig - COST*sig).dropna()
    k = int(len(r)*0.6)
    si, ai, ddi = sr(r.iloc[:k]); so, ao, ddo = sr(r.iloc[k:])
    print(f"    TRAIN (60%): Sharpe {si:+.2f}  annual {ai:+.0f}%  DD {ddi:+.0f}%")
    print(f"    TEST  (40%): Sharpe {so:+.2f}  annual {ao:+.0f}%  DD {ddo:+.0f}%")

    print("\n=== [3] MECANISMO: retorno fwd de VIXY por régimen (¿es el roll decay?) ===")
    fwd = df["VIXY"].pct_change().shift(-1)               # retorno de VIXY mañana (descriptivo)
    cont = df["TS"] < 1
    print(f"    E[VIXY fwd | CONTANGO]     = {fwd[cont].mean()*252*100:+.0f}%/año  (VIXY decae → short-vol gana)")
    print(f"    E[VIXY fwd | BACKWARDATION]= {fwd[~cont].mean()*252*100:+.0f}%/año  (VIXY sube → short-vol pierde)")

    print("\n=== [4] Cross-check en OTRO ETF: SVXY (inverso; LARGO en contango) ===")
    svxy = load1("SVXY")
    d2 = pd.DataFrame({"VIX": vix, "VIX3M": vix3m, "SVXY": svxy}).dropna()
    d2["TS"] = d2["VIX"]/d2["VIX3M"]
    d2["ret"] = d2["SVXY"].pct_change()                   # LARGO SVXY = short-vol
    d2 = d2.dropna()
    sig2 = (d2["TS"].shift(1) < 1.0).astype(float)
    s2, a2, dd2 = sr(d2["ret"]*sig2 - COST*sig2)
    print(f"    SVXY timed (largo en contango): Sharpe {s2:+.2f}  annual {a2:+.0f}%  DD {dd2:+.0f}%")
    print("    (nota: SVXY cambió de -1x a -0.5x tras feb-2018; el SIGNO/patrón es lo que valida)")

    print("\n=== [5] Correlación de COLA con RSI2 (¿se comprime en crisis?) ===")
    vix_c = (df["sv"]*sig - COST*sig).dropna(); vix_c.index = pd.to_datetime(vix_c.index).normalize()
    rsi2 = rsi2_daily("US500").add(rsi2_daily("NAS100"), fill_value=0)
    sp = load1("SP500"); spret = sp.pct_change(); spret.index = pd.to_datetime(spret.index).normalize()
    idx = pd.bdate_range(max(vix_c.index.min(), rsi2.index.min()), min(vix_c.index.max(), rsi2.index.max()))
    vc = vix_c.reindex(idx, fill_value=0.0); rs = rsi2.reindex(idx, fill_value=0.0)
    vx = df["VIX"].copy(); vx.index = pd.to_datetime(vx.index).normalize(); vx = vx.reindex(idx).ffill()
    spr = spret.reindex(idx).fillna(0)
    stress = (vx > 25) | (spr < -0.015)                  # dias de estres: VIX alto o S&P -1.5%
    print(f"    corr(VIXcarry, RSI2) TODOS los días : {np.corrcoef(vc, rs)[0,1]:+.2f}")
    print(f"    corr(VIXcarry, RSI2) días de ESTRÉS : {np.corrcoef(vc[stress], rs[stress])[0,1]:+.2f}  (n={stress.sum()})")
    print("    (si la corr de estrés es MUCHO mayor → la diversificación se comprime en crisis)")


if __name__ == "__main__":
    main()

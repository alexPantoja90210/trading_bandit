"""
Tesis del handoff Londres→NY: ¿el movimiento previo al cierre de Londres (~11:00 ET)
CONTINÚA o se REVIERTE en la tarde de NY? Investiga/verifica/prueba con rigor.

Definición (reloj ET del bróker, validado ET+7; anclado a la apertura de equities 9:30):
  - AM (drive de la mañana / solapamiento Londres-NY): open 08:00 ET → close 11:00 ET (cierre Londres).
  - PM (tarde de NY, ya sin Londres):                  close 11:00 ET → close 16:00 ET (cierre NY).
Diagnóstico: corr(AM,PM) y E[PM | signo de AM]. corr<0 = reversión (fade el drive al cierre Londres);
corr>0 = continuación.
Regla: a las 11:00 ET tomar posición = signo(AM)*dir, salir 16:00 ET, plano (sin overnight). dir=+1
continuación, −1 reversión. Costos 2 patas. WALK-FORWARD: elegir dir en TRAIN, aplicar en TEST (OOS).
Solo LEE. Data M30 vía intraday_cache (4.2a).
"""
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

from mt5_connect import ensure
import MetaTrader5 as mt5
from intraday_cache import load_m30, add_et

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYMBOLS = ["XAUUSD", "US500", "NAS100", "EURUSD"]
OPEN_AM, SPLIT, CLOSE_PM = "08:00", "11:00", "16:00"    # cierre Londres = SPLIT (11:00 ET)


def legs(df):
    """Devuelve DataFrame por día con AM (open08→close11) y PM (close11→close16)."""
    d = add_et(df)
    piv_o = d.pivot_table(index="date", columns="hm", values="open")
    piv_c = d.pivot_table(index="date", columns="hm", values="close")
    need_o = [OPEN_AM]; need_c = [SPLIT, CLOSE_PM]
    for s in need_o:
        if s not in piv_o.columns:
            return None
    for s in need_c:
        if s not in piv_c.columns:
            return None
    o8 = piv_o[OPEN_AM]; c11 = piv_c[SPLIT]; c16 = piv_c[CLOSE_PM]
    out = pd.DataFrame({"o8": o8, "c11": c11, "c16": c16}).dropna()
    out["AM"] = out["c11"] / out["o8"] - 1.0
    out["PM"] = out["c16"] / out["c11"] - 1.0
    return out


def dsharpe(v):
    v = np.asarray(v, float); v = v[np.isfinite(v)]
    return (v.mean() / v.std() * np.sqrt(252)) if len(v) > 1 and v.std() > 0 else 0.0


def pf(v):
    v = np.asarray(v, float)
    w = v[v > 0].sum(); l = -v[v < 0].sum()
    return w / l if l > 0 else 9.99


def main():
    ensure()
    for sym in SYMBOLS:
        df, _, n = load_m30(sym)
        L = legs(df)
        if L is None or len(L) < 300:
            print(f"### {sym}: data insuficiente"); continue
        info = mt5.symbol_info(sym)
        cost = (info.spread * info.point) / L["c11"].iloc[-1] * 100.0 if info else 0.0
        AM = L["AM"].values * 100; PM = L["PM"].values * 100
        dates = pd.to_datetime(L.index)
        corr = np.corrcoef(AM, PM)[0, 1]

        print(f"\n{'='*70}\n### {sym} · {len(L)} días ({dates[0].date()}→{dates[-1].date()})  costo≈{cost:.4f}%/pata")
        print(f"[verificar] corr(AM,PM) = {corr:+.3f}   "
              f"E[PM|AM>0]={PM[AM>0].mean():+.3f}%  E[PM|AM<0]={PM[AM<0].mean():+.3f}%")
        signo = "REVERSIÓN" if corr < 0 else "CONTINUACIÓN"
        print(f"           → sesgo: {signo}")

        # regla full-sample: signo(AM)*dir, dir = signo de la relación (cont/rev), costo 2 patas
        base = np.sign(AM) * PM - 2 * cost                 # continuación pura
        for dir_, name in [(+1, "continuación"), (-1, "reversión")]:
            r = dir_ * np.sign(AM) * PM - 2 * cost
            print(f"    regla {name:<13}: Sharpe {dsharpe(r):+.2f}  PF {pf(r):.2f}  "
                  f"ret {r.sum():+.1f}%  wr {(r>0).mean()*100:.0f}%")

        # WALK-FORWARD OOS: dir se elige en train (252d) y se aplica en test (63d)
        TRAIN, TEST = 252, 63
        oos = np.full(len(L), np.nan); s = TRAIN
        while s < len(L):
            e = min(s + TEST, len(L))
            tr = np.sign(AM[s-TRAIN:s]) * PM[s-TRAIN:s]
            dir_ = 1 if tr.mean() >= 0 else -1              # cont o rev según el train
            oos[s:e] = dir_ * np.sign(AM[s:e]) * PM[s:e] - 2 * cost
            s = e
        m = np.isfinite(oos); ov = oos[m]
        yrs = dates.year.values[m]
        yr = defaultdict(float)
        for i, y in enumerate(yrs):
            yr[y] += ov[i]
        posy = sum(1 for v in yr.values() if v > 0)
        print(f"    [JUEZ] WALK-FORWARD OOS: {m.sum()} días  Sharpe {dsharpe(ov):+.2f}  "
              f"PF {pf(ov):.2f}  ret {ov.sum():+.1f}%  años+ {posy}/{len(yr)}")
        print("           " + "  ".join(f"{y}:{v:+.1f}" for y, v in sorted(yr.items())))


if __name__ == "__main__":
    main()

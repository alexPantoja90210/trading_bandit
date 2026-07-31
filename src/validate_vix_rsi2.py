"""
validate_vix_rsi2.py — valida si CAPAR RSI2 en VIX alto reduce el DD sin matar el retorno.
Diagnóstico previo (analyze_vix.py): RSI2 (reversión) colapsa en VIX alto (reward medio
+0.388 en VIX medio -> +0.002 en VIX alto) y sus PEORES pérdidas crecen (-1.40 -> -2.07 ATR).
Historia económica: no comprar el dip en pleno pánico (el cuchillo sigue cayendo).

Aquí: curva de equity de la recompensa RSI2 (en ATR, del meta_dataset), con y sin las
entradas de VIX alto. Métricas: retorno, DD, Sharpe, Calmar (ret/DD) + robustez por año.
Solo LEE. Es validación; no toca la estrategia viva.
"""
import os
import sys

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

META = os.path.join(DATA_DIR, "meta_dataset.csv")


def curve_stats(r):
    r = np.asarray(r, float)
    eq = np.cumsum(r)
    dd = (eq - np.maximum.accumulate(eq)).min()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    calmar = eq[-1] / abs(dd) if dd < 0 else float("inf")
    return dict(ret=eq[-1], dd=dd, sharpe=sh, calmar=calmar, n=len(r))


def main():
    ensure()
    d = pd.read_csv(META)
    d = d[d["edge"] == "RSI2"].copy()
    d["date"] = pd.to_datetime(d["time"]).dt.date
    d = d.sort_values("time").reset_index(drop=True)

    mt5.symbol_select("VIX", True)
    r = mt5.copy_rates_from_pos("VIX", mt5.TIMEFRAME_D1, 0, 6000)
    vx = pd.DataFrame(r); vx["date"] = pd.to_datetime(vx["time"], unit="s").dt.date
    vlvl = pd.Series(vx["close"].values, index=vx["date"]).shift(1)   # VIX conocido al entrar
    uni = pd.Index(sorted(set(vlvl.index) | set(d["date"])))
    d["vix"] = d["date"].map(vlvl.reindex(uni).ffill())
    d = d.dropna(subset=["vix"])

    print(f"RSI2: {len(d)} entradas con VIX  ({d['date'].min()} -> {d['date'].max()})")
    print(f"VIX en entradas: mediana {d['vix'].median():.1f}  p90 {d['vix'].quantile(.9):.1f}\n")

    base = d["reward"].values
    b = curve_stats(base)
    print(f"{'variante':<22}{'ret(ATR)':>10}{'DD':>9}{'Sharpe':>9}{'Calmar':>9}{'n':>7}{'omit':>7}")
    print(f"{'TODAS (baseline)':<22}{b['ret']:>10.1f}{b['dd']:>9.1f}{b['sharpe']:>9.2f}"
          f"{b['calmar']:>9.2f}{b['n']:>7}{0:>7}")
    for thr in [20, 22, 25, 30]:
        f = d[d["vix"] <= thr]["reward"].values
        if len(f) < 30:
            continue
        s = curve_stats(f)
        print(f"{'cap VIX<=' + str(thr):<22}{s['ret']:>10.1f}{s['dd']:>9.1f}{s['sharpe']:>9.2f}"
              f"{s['calmar']:>9.2f}{s['n']:>7}{b['n']-s['n']:>7}")

    # robustez por año: retorno de las entradas OMITIDAS (VIX>25) — ¿son malas cada año?
    print("\nRetorno de las entradas OMITIDAS con cap VIX>25 (¿aportan o restan por año?):")
    d["yr"] = pd.to_datetime(d["time"]).dt.year
    omit = d[d["vix"] > 25]
    line = []
    for y in sorted(d["yr"].unique()):
        s = omit[omit["yr"] == y]["reward"]
        if len(s):
            line.append(f"{y}:{s.sum():+.1f}(n{len(s)})")
    print("  " + "  ".join(line))
    print(f"  TOTAL omitidas (VIX>25): suma={omit['reward'].sum():+.1f} ATR en {len(omit)} trades "
          f"(mean {omit['reward'].mean():+.3f})")


if __name__ == "__main__":
    main()

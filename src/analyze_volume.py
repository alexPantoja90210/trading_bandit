"""
analyze_volume.py — ¿el VOLUMEN (tick_volume del bróker) ayuda a construir EDGE o reducir DD?

Hipótesis clásicas:
  - Ruptura CON volumen = ruptura real (confirmación) → Zarattini debería pagar más si el
    volumen del slot de entrada es alto vs su normal.
  - Dip CON volumen (capitulación) → mejor rebote → RSI2 debería pagar más con volumen alto.
Método: recomputar la señal + recompensa (misma lógica del meta_dataset) añadiendo el
VOLUMEN RELATIVO al entrar (tick_volume / su media móvil), y condicionar la recompensa.
Nota: en CFD el volumen es tick_volume (nº de ticks), no volumen real, pero correlaciona con
actividad. Solo LEE. Diagnóstico, no cambia estrategias.
"""
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from reward_engine import compute_indicators
from rsi2_meanrev import rsi
from intraday_breakout_zarattini import load_m30, build_matrices, move_matrix

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def sharpe(r):
    r = np.asarray(r, float)
    return r.mean() / r.std() if len(r) > 1 and r.std() > 0 else 0.0


def report_by_vol(name, relvol, reward):
    relvol = np.asarray(relvol, float); reward = np.asarray(reward, float)
    m = np.isfinite(relvol) & np.isfinite(reward)
    relvol, reward = relvol[m], reward[m]
    if len(reward) < 60:
        print(f"  {name}: muestra chica (n={len(reward)})"); return
    q = np.quantile(relvol, [1/3, 2/3])
    print(f"  {name} (n={len(reward)}):  corr(reward,vol_rel)={np.corrcoef(relvol,reward)[0,1]:+.3f}")
    for lab, mask in [("vol BAJO ", relvol <= q[0]),
                      ("vol MEDIO", (relvol > q[0]) & (relvol <= q[1])),
                      ("vol ALTO ", relvol > q[1])]:
        s = reward[mask]
        print(f"      {lab}: mean={s.mean():+.3f}  sharpe={sharpe(s):+.2f}  n={len(s)}")


def zarattini_vol(sym, N, H=6):
    """Recompute entradas Zarattini + volumen relativo del slot de entrada + reward a H slots."""
    df, path, ntot = load_m30(sym)
    if df is None or ntot < 2000:
        return None, None
    M = build_matrices(df); mv = move_matrix(M["cum"])
    # V por slot desde build_matrices no se expone -> recomputar rel-vol por slot vía pivote
    from intraday_cache import add_et, RTH_SLOTS
    d = add_et(df); d = d[d["hm"].isin(RTH_SLOTS)].copy()
    d["slot"] = d["hm"].map({s: i for i, s in enumerate(RTH_SLOTS)})
    V = d.pivot_table(index="date", columns="slot", values="tick_volume").reindex(M["dates"]).values
    Vnorm = pd.DataFrame(V).rolling(20, min_periods=10).mean().shift(1).values   # normal del slot (previo)
    relv = V / np.where(Vnorm > 0, Vnorm, np.nan)
    C, cum = M["C"], M["cum"]
    n, ns = C.shape
    rv, rw = [], []
    for i in range(n):
        for t in range(1, ns - H):
            mvt = mv[i, t]
            if not np.isfinite(mvt) or mvt <= 0:
                continue
            ub = N * mvt
            sig = 1 if cum[i, t] > ub else (-1 if cum[i, t] < -ub else 0)
            if sig == 0 or not np.isfinite(C[i, t]) or not np.isfinite(C[i, t + H]):
                continue
            rv.append(relv[i, t]); rw.append(sig * (C[i, t + H] / C[i, t] - 1.0) * 100)
    return rv, rw


def bars_vol(sym, tf, kind, H):
    """RSI2 (D1) o STF (H4): señal + vol relativo (tick_volume/SMA20) + reward direccional."""
    ensure(); mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, 50000)
    if r is None or len(r) < 700:
        return None, None
    df = pd.DataFrame(r)
    c = df["close"].values; vol = df["tick_volume"].values.astype(float)
    dfi = compute_indicators(df.copy()); atr = dfi["atr"].values
    relv = vol / pd.Series(vol).rolling(20).mean().shift(1).values
    if kind == "RSI2":
        sma = pd.Series(c).rolling(200).mean().values; r2 = rsi(c, 2)
        sig = np.where((c > sma) & (r2 < 10.0), 1, 0)
    else:  # STF
        ema = pd.Series(c).ewm(span=200, adjust=False).mean().values
        dhi = pd.Series(df["high"]).rolling(55).max().shift(1).values
        dlo = pd.Series(df["low"]).rolling(55).min().shift(1).values
        sig = np.where((c > ema) & (c > dhi), 1, np.where((c < ema) & (c < dlo), -1, 0))
    rv, rw = [], []
    for t in range(260, len(c) - H):
        if sig[t] == 0 or not (np.isfinite(atr[t]) and atr[t] > 0):
            continue
        rv.append(relv[t]); rw.append(sig[t] * (c[t + H] - c[t]) / (atr[t] * np.sqrt(H)))
    return rv, rw


def main():
    ensure()
    print("=== VOLUMEN relativo al entrar -> ¿condiciona la recompensa? ===")
    print("\n[Zarattini · ruptura M30] (confirmación por volumen del slot):")
    for sym, N in [("US500", 1.0), ("NAS100", 1.5), ("US30", 1.0)]:
        rv, rw = zarattini_vol(sym, N)
        if rv:
            report_by_vol(f"{sym}", rv, rw)

    print("\n[RSI2 · dip D1] (capitulación por volumen):")
    for sym in ["NAS100", "US500", "US30", "US2000", "FRA40"]:
        rv, rw = bars_vol(sym, mt5.TIMEFRAME_D1, "RSI2", 5)
        if rv:
            report_by_vol(f"{sym}", rv, rw)

    print("\n[STF · ruptura H4] (confirmación por volumen):")
    for sym in ["XAUUSD", "BTCUSD", "ETHUSD"]:
        rv, rw = bars_vol(sym, mt5.TIMEFRAME_H4, "STF", 30)
        if rv:
            report_by_vol(f"{sym}", rv, rw)


if __name__ == "__main__":
    main()

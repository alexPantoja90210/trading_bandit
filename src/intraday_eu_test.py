"""
Experimento: ¿el momentum intradía (Zarattini) tiene edge en la sesión EUROPEA?

Reusa la maquinaria del Zarattini US pero con la sesión europea (detectada
empíricamente: DAX/CAC abren 10:00 broker = 09:00 CET, cierran ~18:00 broker).
Índices: GER40 (DAX), FRA40 (CAC). Mismo juez: barrido de N → walk-forward.
Si no supera al 1/N / no es robusto → descartado con datos. Solo LEE histórico.
"""
import sys
from collections import defaultdict, Counter

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from intraday_cache import load_m30
from intraday_breakout_zarattini import move_matrix, simulate, stats, N_GRID

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Sesión europea en hora del BROKER (no ET): 10:00 → 18:00 = 17 slots M30.
EU_SLOTS = [f"{h:02d}:{m:02d}" for h in range(10, 18) for m in (0, 30)] + ["18:00"]
SYMBOLS = ["GER40", "FRA40"]


def build_matrices_eu(df):
    d = df.copy()
    d["dt"] = pd.to_datetime(d["time"], unit="s")          # hora broker (sin shift ET)
    d["date"] = d["dt"].dt.date
    d["hm"] = d["dt"].dt.strftime("%H:%M")
    d = d[d["hm"].isin(EU_SLOTS)].copy()
    d["slot"] = d["hm"].map({s: i for i, s in enumerate(EU_SLOTS)})
    piv_c = d.pivot_table(index="date", columns="slot", values="close")
    piv_o = d.pivot_table(index="date", columns="slot", values="open")
    piv_h = d.pivot_table(index="date", columns="slot", values="high")
    piv_l = d.pivot_table(index="date", columns="slot", values="low")
    piv_v = d.pivot_table(index="date", columns="slot", values="tick_volume")
    full = piv_c.dropna().index
    for p in (piv_o, piv_h, piv_l, piv_v):
        full = full.intersection(p.dropna().index)
    C = piv_c.loc[full].values; O = piv_o.loc[full].values
    H = piv_h.loc[full].values; L = piv_l.loc[full].values; V = piv_v.loc[full].values
    oday = O[:, 0:1]
    cum = C / oday - 1.0
    typ = (H + L + C) / 3.0
    vwap = np.cumsum(typ * V, axis=1) / np.maximum(np.cumsum(V, axis=1), 1e-9)
    return dict(dates=np.array(list(full)), C=C, oday=oday[:, 0], cum=cum, vwap=vwap)


def dsharpe(v):
    v = v[np.isfinite(v)]
    return v.mean() / v.std() * np.sqrt(252) if len(v) > 1 and v.std() > 0 else 0.0


def run(sym):
    df, _, _ = load_m30(sym)
    if df is None:
        print(f"### {sym}: sin datos"); return
    M = build_matrices_eu(df); mv = move_matrix(M["cum"]); dates = M["dates"]
    info = mt5.symbol_info(sym)
    cost = (info.spread * info.point) / M["oday"][-1] * 100.0 if info else 0.0
    bh = (M["C"][:, -1] / M["oday"] - 1.0) * 100.0
    print(f"\n{'='*66}\n### {sym} · M30 sesión EU · {len(dates)} días ({dates[0]}->{dates[-1]})"
          f"  costo≈{cost:.4f}%  buy&hold Sharpe={dsharpe(bh)/np.sqrt(252)*np.sqrt(252):.2f}")

    dp_by_N = {}
    print(f"  {'N':>5}{'ret%':>9}{'Sharpe':>8}{'PF':>6}{'trades':>8}")
    for N in N_GRID:
        dp, tr = simulate(M, mv, N, cost); dp_by_N[N] = dp
        s = stats(tr)
        if s:
            print(f"  {N:>5}{s['ret']:>+9.1f}{s['sharpe']:>8.2f}{s['pf']:>6.2f}{s['n']:>8}")

    # walk-forward
    oos = np.full(len(dates), np.nan); picks = []; s0 = 252
    while s0 < len(dates):
        e = min(s0 + 63, len(dates))
        bN = max(N_GRID, key=lambda q: dsharpe(dp_by_N[q][s0 - 252:s0]))
        oos[s0:e] = dp_by_N[bN][s0:e]; picks.append(bN); s0 = e
    m = np.isfinite(oos); ov = oos[m]
    pf = ov[ov > 0].sum() / -ov[ov < 0].sum() if (ov < 0).any() else 9.9
    years = np.array([d.year for d in dates])[m]
    yr = defaultdict(float)
    for i, y in enumerate(years):
        yr[y] += ov[i]
    pos = sum(1 for v in yr.values() if v > 0)
    print(f"  WALK-FWD: OOS Sharpe={dsharpe(ov):+.2f} PF={pf:.2f} ret={ov.sum():+.0f}%  "
          f"años+={pos}/{len(yr)}  N={dict(Counter(picks))}")


def main():
    ensure()
    for s in SYMBOLS:
        run(s)


if __name__ == "__main__":
    main()

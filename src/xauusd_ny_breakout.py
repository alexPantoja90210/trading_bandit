"""
Breakout de apertura de la sesión NY sobre XAUUSD — ¿tiene el oro la MISMA estructura
de momentum intradía que validamos en índices (Zarattini)? Mismo motor y MISMO rigor.

Hipótesis del usuario: operar el oro SOLO en NY (sesión de mayor volumen + datos macro US)
puede tener edge de ESTRUCTURA (expansión de rango), no de predicción direccional.

Adaptación al oro (mismo mecanismo que intraday_breakout_zarattini):
- Ancla del rango = apertura de la sesión NY a las **08:00 ET** (captura el macro de 8:30 —
  NFP/CPI/FOMC, el mayor driver intradía del oro— y toda la sesión NY hasta 16:00 ET).
- Banda de ruido UB/LB = N * Move(t), Move(t)=media 14 días de |close/open_0800-1| por slot (sin lookahead).
- Entrada en cada M30 al romper la banda; salida por trailing de VWAP de sesión; PLANO a las 16:00 (sin swap).
- Validación idéntica: barrido N, buy&hold, robustez por año, sensibilidad a costos, y WALK-FORWARD OOS.
Solo LEE. Data vía intraday_cache (M30, 4.2a).
"""
import sys
from collections import defaultdict, Counter

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from intraday_cache import load_m30, add_et

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYMBOL = "XAUUSD"
# sesión NY para el oro: 08:00 → 16:00 ET (17 slots M30; el último = plano EOD)
NY_SLOTS = ["08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
            "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00"]
LOOKBACK = 14
N_GRID = [0.5, 0.75, 1.0, 1.5, 2.0]


def build_matrices(df, slots):
    d = add_et(df)
    d = d[d["hm"].isin(slots)].copy()
    slot_idx = {s: i for i, s in enumerate(slots)}
    d["slot"] = d["hm"].map(slot_idx)
    piv = {k: d.pivot_table(index="date", columns="slot", values=v)
           for k, v in [("C", "close"), ("O", "open"), ("H", "high"), ("L", "low"), ("V", "tick_volume")]}
    full = piv["C"].dropna().index
    for p in piv.values():
        full = full.intersection(p.dropna().index)
    C = piv["C"].loc[full].values; O = piv["O"].loc[full].values
    H = piv["H"].loc[full].values; L = piv["L"].loc[full].values; V = piv["V"].loc[full].values
    oday = O[:, 0:1]
    cum = C / oday - 1.0
    typ = (H + L + C) / 3.0
    vwap = np.cumsum(typ * V, axis=1) / np.maximum(np.cumsum(V, axis=1), 1e-9)
    return dict(dates=np.array(list(full)), C=C, oday=oday[:, 0], cum=cum, vwap=vwap)


def move_matrix(cum):
    return pd.DataFrame(np.abs(cum)).rolling(LOOKBACK).mean().shift(1).values


def simulate(M, mv, N, cost):
    C, cum, vwap = M["C"], M["cum"], M["vwap"]
    n, ns = C.shape
    day_pnl = np.zeros(n); trades = []
    for i in range(n):
        pos, entry = 0, 0.0
        for t in range(1, ns):
            mvt = mv[i, t]
            if not np.isfinite(mvt) or mvt <= 0:
                continue
            price = C[i, t]; ub = N * mvt
            if pos != 0:
                exit_now = (pos == 1 and price < vwap[i, t]) or (pos == -1 and price > vwap[i, t])
                if t == ns - 1:
                    exit_now = True
                if exit_now:
                    ret = pos * (price / entry - 1.0) * 100.0 - cost
                    trades.append(ret); day_pnl[i] += ret; pos = 0
            if pos == 0 and t < ns - 1:
                if cum[i, t] > ub:
                    pos, entry = 1, price
                elif cum[i, t] < -ub:
                    pos, entry = -1, price
    return day_pnl, np.array(trades)


def stats(R):
    if len(R) == 0:
        return None
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; l = R[R < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    sh = (R.mean() / R.std() * np.sqrt(252)) if R.std() > 0 else 0.0
    return dict(ret=R.sum(), sharpe=sh, dd=dd, wr=(R > 0).mean() * 100, pf=pf, n=len(R))


def dsharpe(v):
    v = v[np.isfinite(v)]
    return (v.mean() / v.std() * np.sqrt(252)) if len(v) > 1 and v.std() > 0 else 0.0


def main():
    ensure()
    df, path, ntot = load_m30(SYMBOL)
    M = build_matrices(df, NY_SLOTS)
    mv = move_matrix(M["cum"])
    dates = M["dates"]
    info = mt5.symbol_info(SYMBOL)
    cost = (info.spread * info.point) / M["oday"][-1] * 100.0 if info else 0.0
    print(f"{'='*72}\n### {SYMBOL} · NY 08:00-16:00 ET · M30 · {len(dates)} días "
          f"({dates[0]} -> {dates[-1]})  costo≈{cost:.4f}%/trade")

    bh = (M["C"][:, -1] / M["oday"] - 1.0) * 100.0
    sbh = stats(bh)
    print(f"    buy&hold sesión NY: ret={sbh['ret']:+.1f}%  Sharpe={sbh['sharpe']:+.2f}  DD={sbh['dd']:+.1f}%")

    print(f"\n[A] Barrido de N (multiplicador de banda):")
    print(f"    {'N':>5}{'ret%':>9}{'Sharpe':>8}{'DD%':>8}{'wr%':>7}{'PF':>6}{'trades':>8}{'tr/día':>8}")
    print("    " + "-" * 60)
    best, dp_by_N = None, {}
    for N in N_GRID:
        dp, tr = simulate(M, mv, N, cost)
        dp_by_N[N] = dp
        st = stats(tr)
        if st is None:
            continue
        print(f"    {N:>5}{st['ret']:>+9.1f}{st['sharpe']:>8.2f}{st['dd']:>+8.1f}"
              f"{st['wr']:>7.1f}{st['pf']:>6.2f}{st['n']:>8}{st['n']/len(dates):>8.2f}")
        if best is None or st["sharpe"] > best[1]["sharpe"]:
            best = (N, st, dp, tr)

    N, st, dp, tr = best
    years = np.array([d.year for d in dates])
    yr = defaultdict(float)
    for i, y in enumerate(years):
        yr[y] += dp[i]
    pos = sum(1 for v in yr.values() if v > 0)
    print(f"\n[B] Robustez por año — mejor N={N} (Sharpe {st['sharpe']:+.2f}): {pos}/{len(yr)} años+")
    print("    " + "  ".join(f"{y}:{v:+.1f}" for y, v in sorted(yr.items())))

    print(f"\n[C] Sensibilidad a costos — N={N} ({st['n']} trades):")
    for k in [1, 2, 3, 5]:
        _, trk = simulate(M, mv, N, cost * k)
        sk = stats(trk)
        flag = "" if sk["pf"] > 1.0 else "  ← PF<1 (muere)"
        print(f"    costo x{k} ({cost*k:.4f}%/trade): ret={sk['ret']:>+7.1f}%  "
              f"Sharpe={sk['sharpe']:>+5.2f}  PF={sk['pf']:.2f}{flag}")

    TRAIN, TEST = 252, 63
    oos = np.full(len(dates), np.nan); picks = []
    s = TRAIN
    while s < len(dates):
        e = min(s + TEST, len(dates))
        bN = max(N_GRID, key=lambda q: dsharpe(dp_by_N[q][s - TRAIN:s]))
        oos[s:e] = dp_by_N[bN][s:e]; picks.append(bN)
        s = e
    m = np.isfinite(oos); oosv = oos[m]
    eq = np.cumsum(oosv); dd = (eq - np.maximum.accumulate(eq)).min()
    wsum = oosv[oosv > 0].sum(); lsum = -oosv[oosv < 0].sum()
    pf = wsum / lsum if lsum > 0 else 9.99
    pk = ", ".join(f"N{n}:{c}" for n, c in sorted(Counter(picks).items()))
    print(f"\n[D] WALK-FORWARD (train {TRAIN}d → test {TEST}d, N out-of-sample) — EL JUEZ:")
    print(f"    OOS: {m.sum()} días  ret={oosv.sum():+.1f}%  Sharpe={dsharpe(oosv):+.2f}  DD={dd:+.1f}%  PF={pf:.2f}")
    print(f"    N elegidos por ventana: {pk}")
    yrs = np.array([d.year for d in dates])[m]
    yr = defaultdict(float)
    for i, y in enumerate(yrs):
        yr[y] += oosv[i]
    posy = sum(1 for v in yr.values() if v > 0)
    print(f"    años OOS positivos: {posy}/{len(yr)}  |  "
          + "  ".join(f"{y}:{v:+.1f}" for y, v in sorted(yr.items())))


if __name__ == "__main__":
    main()

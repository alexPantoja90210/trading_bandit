"""
Réplica (M30) de la estrategia de momentum intradía de Zarattini, Aziz & Barbon
(2024), "Beat the Market: An Effective Intraday Momentum Strategy for SPY".

Reglas:
- Banda de ruido alrededor del open del día: UB=Open*(1+N*Move(t)), LB=Open*(1-N*Move(t)),
  con Move(t)=promedio (14 días previos) de |precio_slot/open-1| en ese slot intradía
  (estacionalidad: cono que se abre en apertura y cierre). N = multiplicador.
- Entrada en cada HH:00/HH:30 (=barra M30): rompe UB→largo, rompe LB→corto.
- Salida: trailing por VWAP de sesión (largo sale si cae bajo VWAP; corto si sube sobre VWAP),
  flip si rompe la banda opuesta, y SIEMPRE plano al cierre (sin overnight → sin swap).

Adaptación: el paper usa 1-min; nosotros M30 (lo que da el bróker, 4.2a). Las entradas
son en HH:00/HH:30 → M30 encaja; perdemos granularidad en salidas → test CONSERVADOR.
Solo LEE mercado. Data vía intraday_cache (se cachea/acumula en cada corrida).
"""
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from intraday_cache import load_m30, add_et, RTH_SLOTS

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYMBOLS = ["US500", "NAS100"]
LOOKBACK = 14
N_GRID = [0.5, 0.75, 1.0, 1.5, 2.0]


def build_matrices(df):
    """Devuelve dict de matrices [n_dias x 13 slots]: close/open_day/vwap/cum + fechas."""
    d = add_et(df)
    d = d[d["hm"].isin(RTH_SLOTS)].copy()
    slot_idx = {s: i for i, s in enumerate(RTH_SLOTS)}
    d["slot"] = d["hm"].map(slot_idx)
    piv_c = d.pivot_table(index="date", columns="slot", values="close")
    piv_o = d.pivot_table(index="date", columns="slot", values="open")
    piv_h = d.pivot_table(index="date", columns="slot", values="high")
    piv_l = d.pivot_table(index="date", columns="slot", values="low")
    piv_v = d.pivot_table(index="date", columns="slot", values="tick_volume")
    full = piv_c.dropna().index.intersection(piv_o.dropna().index)
    for p in (piv_h, piv_l, piv_v):
        full = full.intersection(p.dropna().index)
    C = piv_c.loc[full].values; O = piv_o.loc[full].values
    H = piv_h.loc[full].values; L = piv_l.loc[full].values; V = piv_v.loc[full].values
    oday = O[:, 0:1]                                  # open del día (slot 0)
    cum = C / oday - 1.0
    # VWAP de sesión acumulado por slot
    typ = (H + L + C) / 3.0
    vwap = np.cumsum(typ * V, axis=1) / np.maximum(np.cumsum(V, axis=1), 1e-9)
    return dict(dates=np.array(list(full)), C=C, oday=oday[:, 0], cum=cum, vwap=vwap)


def move_matrix(cum):
    """Move(t)=media(14 días previos) de |cum| por slot, sin lookahead (shift 1)."""
    A = np.abs(cum)
    df = pd.DataFrame(A)
    return df.rolling(LOOKBACK).mean().shift(1).values


def simulate(M, mv, N, cost):
    """Devuelve (pnl_por_dia, lista_ret_trades). Un pase por todos los días."""
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
            # gestionar salida
            if pos != 0:
                exit_now = (pos == 1 and price < vwap[i, t]) or (pos == -1 and price > vwap[i, t])
                if t == ns - 1:
                    exit_now = True                 # EOD flat
                if exit_now:
                    ret = pos * (price / entry - 1.0) * 100.0 - cost
                    trades.append(ret); day_pnl[i] += ret; pos = 0
            # entrada (si quedó plano y no es el último slot)
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


def run(sym):
    df, path, ntot = load_m30(sym)
    if df is None or ntot < 2000:
        print(f"### {sym}: data insuficiente"); return
    M = build_matrices(df)
    mv = move_matrix(M["cum"])
    dates = M["dates"]
    info = mt5.symbol_info(sym)
    cost = (info.spread * info.point) / M["oday"][-1] * 100.0 if info else 0.0
    print(f"\n{'='*72}\n### {sym} · M30 · {len(dates)} días ({dates[0]} -> {dates[-1]})"
          f"  costo≈{cost:.4f}%/trade")

    # buy&hold RTH (open->close del día)
    bh = (M["C"][:, -1] / M["oday"] - 1.0) * 100.0
    sbh = stats(bh)
    print(f"    buy&hold RTH:  ret={sbh['ret']:+.1f}%  Sharpe={sbh['sharpe']:+.2f}  DD={sbh['dd']:+.1f}%")

    print(f"\n[A] Barrido de N (multiplicador de banda):")
    print(f"    {'N':>5}{'ret%':>9}{'Sharpe':>8}{'DD%':>8}{'wr%':>7}{'PF':>6}{'trades':>8}{'tr/día':>8}")
    print("    " + "-" * 60)
    best = None
    dp_by_N = {}
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

    # robustez por año del mejor N
    N, st, dp, tr = best
    years = np.array([d.year for d in dates])
    yr = defaultdict(float)
    for i, y in enumerate(years):
        yr[y] += dp[i]
    pos = sum(1 for v in yr.values() if v > 0)
    print(f"\n[B] Robustez por año — mejor N={N} (Sharpe {st['sharpe']:+.2f}):")
    print(f"    años positivos: {pos}/{len(yr)}")
    print("    " + "  ".join(f"{y}:{v:+.1f}" for y, v in sorted(yr.items())))

    # [C] sensibilidad a costos (el juez real en intradía: spread + slippage de breakout)
    print(f"\n[C] Sensibilidad a costos — N={N} ({st['n']} trades):")
    for k in [1, 2, 3, 5]:
        _, trk = simulate(M, mv, N, cost * k)
        sk = stats(trk)
        flag = "" if sk["pf"] > 1.0 else "  ← PF<1 (muere)"
        print(f"    costo x{k} ({cost*k:.4f}%/trade): ret={sk['ret']:>+7.1f}%  "
              f"Sharpe={sk['sharpe']:>+5.2f}  PF={sk['pf']:.2f}{flag}")

    # [D] WALK-FORWARD: elegir N en la ventana de ENTRENO (vieja) y operar la de
    #     TEST (nueva). Concatenar OOS. Es el juez de si el edge es real o ajuste.
    def dsharpe(v):
        v = v[np.isfinite(v)]
        return (v.mean() / v.std() * np.sqrt(252)) if len(v) > 1 and v.std() > 0 else 0.0
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
    from collections import Counter
    pk = ", ".join(f"N{n}:{c}" for n, c in sorted(Counter(picks).items()))
    print(f"\n[D] WALK-FORWARD (train {TRAIN}d → test {TEST}d, N out-of-sample):")
    print(f"    OOS: {m.sum()} días  ret={oosv.sum():+.1f}%  Sharpe={dsharpe(oosv):+.2f}  "
          f"DD={dd:+.1f}%  PF={pf:.2f}")
    print(f"    N elegidos por ventana: {pk}")
    yrs = np.array([d.year for d in dates])[m]
    yr = defaultdict(float)
    for i, y in enumerate(yrs):
        yr[y] += oosv[i]
    posy = sum(1 for v in yr.values() if v > 0)
    print(f"    años OOS positivos: {posy}/{len(yr)}  |  "
          + "  ".join(f"{y}:{v:+.1f}" for y, v in sorted(yr.items())))


def main():
    ensure()
    for s in SYMBOLS:
        run(s)


if __name__ == "__main__":
    main()

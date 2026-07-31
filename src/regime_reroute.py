"""
Test de re-ruteo del régimen: en activos con deriva alcista, ¿conviene COMPRAR
el dip (long) en regímenes bajistas/rango en vez de shortear?

Compara, por grupo de régimen, long vs short (hold H barras, entradas NO solapadas),
y dos estrategias completas: 'original' (long-alcista / short-bajista / flat-rango)
vs 're-ruteo' (long en todo lo que no sea caos). P&L en %, por año. Solo LEE.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from collections import defaultdict

from mt5_connect import ensure
from reward_engine import compute_indicators
from regime_master import classify, Params

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

N = 50000
H = 12
BULL = {0, 3, 4}
BEAR = {5, 6, 7}
RANGE = {2, 8}
CHAOS = {1, 9}


def trades(rid, close, year, valid_set, direction, cost, n):
    """Entradas no solapadas: en cada barra cuyo régimen ∈ valid_set, entra
    'direction' (+1/-1), hold H barras. Devuelve lista (ret%, year)."""
    out = []
    next_ok = 0
    for t in range(200, n - H):
        if t < next_ok:
            continue
        if np.isfinite(rid[t]) and int(rid[t]) in valid_set and np.isfinite(close[t]) and close[t] > 0:
            ret = direction * (close[t + H] - close[t]) / close[t] - cost
            out.append((ret * 100, year[t]))
            next_ok = t + H
    return out


def summ(tr, label):
    if not tr:
        print(f"  {label:<22} sin trades"); return
    R = np.array([r for r, _ in tr])
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; l = R[R < 0]; pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    print(f"  {label:<22} n={len(R):>4}  ret%={R.sum():>+7.1f}  PF={pf:.2f}  "
          f"wr={ (R>0).mean()*100:>4.1f}%  maxDD%={dd:>+6.1f}")


def strat(rid, close, year, cost, n, reroute):
    """Estrategia completa. reroute=False: original. True: long en bull+bear+range."""
    out = []; next_ok = 0
    for t in range(200, n - H):
        if t < next_ok:
            continue
        if not np.isfinite(rid[t]):
            continue
        r = int(rid[t]); d = 0
        if r in BULL:
            d = 1
        elif r in BEAR:
            d = 1 if reroute else -1
        elif r in RANGE:
            d = 1 if reroute else 0
        if d != 0 and close[t] > 0:
            ret = d * (close[t + H] - close[t]) / close[t] - cost
            out.append((ret * 100, year[t]))
            next_ok = t + H
    return out


def run(sym, tf, tf_name):
    ensure(); mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    rates = mt5.copy_rates_from_pos(sym, tf, 0, N)
    if rates is None or len(rates) < 3000:
        print(f"### {sym}: histórico insuficiente"); return
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    df = compute_indicators(df)
    print(f"\n{'='*70}\n### {sym} · {tf_name} · {len(df)} barras  H={H}")
    print("clasificando...")
    reg = classify(df, Params())
    close = df["close"].values; year = df["time"].dt.year.values
    rid = reg["id"].values; n = len(df)
    cost = (info.spread * info.point) / np.nanmean(close)

    print("\n-- Por grupo: LONG vs SHORT (comprar dip vs shortear) --")
    summ(trades(rid, close, year, BULL, 1, cost, n),  "ALCISTA long")
    summ(trades(rid, close, year, BEAR, -1, cost, n), "BAJISTA short (orig)")
    summ(trades(rid, close, year, BEAR, 1, cost, n),  "BAJISTA long (dip) ★")
    summ(trades(rid, close, year, RANGE, 1, cost, n), "RANGO long (dip) ★")

    print("\n-- Estrategia completa --")
    orig = strat(rid, close, year, cost, n, reroute=False)
    rr = strat(rid, close, year, cost, n, reroute=True)
    summ(orig, "ORIGINAL")
    summ(rr,   "RE-RUTEO (todo long) ★")

    # por año del re-ruteo
    yr = defaultdict(list)
    for r, y in rr:
        yr[y].append(r)
    line = "  re-ruteo por año: " + " ".join(f"{y}:{np.sum(v):+.0f}" for y, v in sorted(yr.items()) if len(v) >= 3)
    print(line)


def main():
    ensure()
    run("XAUUSD", mt5.TIMEFRAME_H4, "H4")
    run("US500", mt5.TIMEFRAME_D1, "D1")


if __name__ == "__main__":
    main()

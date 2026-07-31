"""
Smart Trend Follower en H4 sobre varios instrumentos (oro, BTC, WTI) para probar
diversificación de régimen: ¿trend­ean BTC/WTI cuando el oro está en rango?
Config base, todo relativo a ATR (se auto-adapta). Solo LEE histórico.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure

from smart_trend_follower import (backtest, atr_series, EMA_LEN, DONCHIAN,
                                  ATR_LEN, RISK_PCT, BALANCE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

N_BARS = 50000
TARGETS = [("Oro (ref)", "XAUUSD"), ("BTC", "BTCUSD"), ("WTI", "WTOIL-PERP")]


def run_symbol(label, sym):
    if not mt5.symbol_select(sym, True):
        print(f"\n### {label} ({sym}): no se pudo seleccionar"); return
    info = mt5.symbol_info(sym)
    cost = (info.spread * info.point) if info else 0.0
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, N_BARS)
    if rates is None or len(rates) < 2000:
        print(f"\n### {label} ({sym}): histórico insuficiente ({0 if rates is None else len(rates)} barras)")
        return
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    year = df["time"].dt.year.values
    ema = pd.Series(close).ewm(span=EMA_LEN, adjust=False).mean().values
    atr = atr_series(high, low, close, ATR_LEN)
    dhi = pd.Series(high).rolling(DONCHIAN).max().shift(1).values
    dlo = pd.Series(low).rolling(DONCHIAN).min().shift(1).values

    tr = backtest(high, low, close, ema, atr, dhi, dlo, year, cost)
    R = np.array([r for _, r, _ in tr]) if tr else np.array([])
    print(f"\n### {label} ({sym}) | {df['time'].iloc[0].date()} -> {df['time'].iloc[-1].date()} "
          f"| {len(df)} barras | cost={cost:.4f}")
    if len(R) == 0:
        print("  sin trades"); return
    risk_d = BALANCE * RISK_PCT; eq = np.cumsum(R * risk_d)
    dd = (eq - np.maximum.accumulate(eq)).min() / BALANCE * 100
    w = R[R > 0]; l = R[R < 0]; pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    print(f"  trades={len(R)}  ΣR={R.sum():+.1f}  PF={pf:.2f}  winrate={(R>0).mean()*100:.1f}%  "
          f"maxDD={dd:+.1f}%  mejorTrade={R.max():+.1f}R")
    # per-año (solo years con >=3 trades)
    from collections import defaultdict
    yr = defaultdict(list)
    for _, r, y in tr:
        yr[y].append(r)
    line = "  año: " + "  ".join(f"{y}:{np.sum(v):+.1f}" for y, v in sorted(yr.items()) if len(v) >= 3)
    print(line)
    return {y: float(np.sum(v)) for y, v in yr.items()}


def main():
    ensure()
    res = {}
    for label, sym in TARGETS:
        res[label] = run_symbol(label, sym)

    # ¿diversifican? comparar años de rango del oro (2021-2022) entre activos
    print("\n===== diversificación: ΣR por activo en años de rango del oro =====")
    print(f"  {'año':<6}{'Oro (ref)':>11}{'BTC':>9}{'WTI':>9}")
    for y in [2020, 2021, 2022, 2023]:
        vals = []
        for label, _ in TARGETS:
            v = (res.get(label) or {}).get(y)
            vals.append(f"{v:+.1f}" if v is not None else "n/d")
        print(f"  {y:<6}{vals[0]:>11}{vals[1]:>9}{vals[2]:>9}")


if __name__ == "__main__":
    main()

"""
Prueba REALISTA del re-ruteo del régimen como estrategia desplegable.

Regla: LONG cuando el régimen NO es caos/transición (comprar dips + seguir
tendencia en activos con deriva alcista). Stop inicial 2.5xATR + trailing
Chandelier 3xATR. Sale del mercado en caos. Costo = spread.
Compara vs BUY & HOLD (¿el régimen reduce el drawdown sin matar el retorno?).
Robustez por año. Solo LEE histórico.
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
STOP_ATR = 2.5
CH_ATR = 3.0
MAXHOLD = 120
CHAOS = {1, 9}


def atr_series(high, low, close, length=14):
    prev = np.roll(close, 1); prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).rolling(length).mean().values


def backtest(high, low, close, atr, rid, year, cost_pct):
    n = len(close); pos = None; trades = []
    for t in range(210, n):
        if not (np.isfinite(atr[t]) and atr[t] > 0 and np.isfinite(rid[t])):
            continue
        r = int(rid[t])
        if pos is not None:
            # trailing chandelier + gestión
            pos["ext"] = max(pos["ext"], high[t])
            pos["stop"] = max(pos["stop"], pos["ext"] - CH_ATR * atr[t])
            exit_price = None; reason = ""
            if low[t] <= pos["stop"]:
                exit_price = pos["stop"]; reason = "stop"
            elif r in CHAOS:
                exit_price = close[t]; reason = "caos"
            elif t - pos["bar"] >= MAXHOLD:
                exit_price = close[t]; reason = "maxhold"
            if exit_price is not None:
                ret = (exit_price - pos["entry"]) / pos["entry"] - cost_pct
                trades.append((ret * 100, pos["yr"], reason))
                pos = None
        if pos is None and r not in CHAOS:
            pos = {"entry": close[t], "stop": close[t] - STOP_ATR * atr[t],
                   "ext": high[t], "bar": t, "yr": year[t]}
    return trades


def stats(returns):
    R = np.asarray(returns, float)
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; l = R[R < 0]; pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    return R.sum(), pf, (R > 0).mean() * 100, dd, len(R)


def buyhold_dd(close, start):
    seg = close[start:]
    eq = (seg / seg[0] - 1) * 100
    dd = (eq - np.maximum.accumulate(eq)).min()
    return eq[-1], dd


def run(sym, tf, tf_name):
    ensure(); mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    rates = mt5.copy_rates_from_pos(sym, tf, 0, N)
    if rates is None or len(rates) < 3000:
        print(f"### {sym}: insuficiente"); return
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    df = compute_indicators(df)
    print(f"\n{'='*66}\n### {sym} · {tf_name} · {len(df)} barras "
          f"({df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()})")
    print("clasificando...")
    reg = classify(df, Params())
    close = df["close"].values; high = df["high"].values; low = df["low"].values
    year = df["time"].dt.year.values; rid = reg["id"].values
    atr = atr_series(high, low, close, 14)
    cost = (info.spread * info.point) / np.nanmean(close)

    tr = backtest(high, low, close, atr, rid, year, cost)
    s, pf, wr, dd, ntr = stats([r for r, _, _ in tr])
    bh_ret, bh_dd = buyhold_dd(close, 210)

    print(f"\n  {'estrategia':<22}{'ret%':>9}{'PF':>7}{'wr%':>7}{'maxDD%':>9}{'trades':>8}")
    print("  " + "-" * 62)
    print(f"  {'RE-RUTEO (long/caos-out)':<22}{s:>+9.1f}{pf:>7.2f}{wr:>7.1f}{dd:>+9.1f}{ntr:>8}")
    print(f"  {'BUY & HOLD':<22}{bh_ret:>+9.1f}{'—':>7}{'—':>7}{bh_dd:>+9.1f}{'—':>8}")
    print(f"  → ret/DD: re-ruteo {abs(s/dd):.2f}  vs  buy&hold {abs(bh_ret/bh_dd):.2f}  "
          f"({'régimen MEJORA risk-adj' if abs(s/dd) > abs(bh_ret/bh_dd) else 'buy&hold mejor'})")

    yr = defaultdict(list)
    for r, y, _ in tr:
        yr[y].append(r)
    pos_years = sum(1 for y, v in yr.items() if np.sum(v) > 0)
    print(f"  años positivos: {pos_years}/{len(yr)}")
    print("  por año: " + " ".join(f"{y}:{np.sum(v):+.0f}" for y, v in sorted(yr.items()) if len(v) >= 2))


def main():
    ensure()
    run("XAUUSD", mt5.TIMEFRAME_H4, "H4")
    run("US500", mt5.TIMEFRAME_D1, "D1")
    run("NAS100", mt5.TIMEFRAME_D1, "D1")


if __name__ == "__main__":
    main()

"""
RSI(2) con COSTOS realistas: spread + financiación (swap overnight) en largos.
La MR mantiene 2-5 días; el carry diario resta a cada trade. Prueba de robustez
a distintos niveles de financiación. Solo LEE histórico.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from rsi2_meanrev import rsi, atr_series

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYMBOLS = ["NAS100", "US500"]
N_BARS = 50000
SMA_TREND = 200
SMA_EXIT = 5
EXIT_TH = 70.0
MAX_HOLD = 10
CARRY_LEVELS = [0.0, 0.02, 0.04, 0.06]   # % por día (≈ 0, 5%, 10%, 15% anual)


def backtest(high, low, close, sma200, sma5, r2, year, entry_th):
    """Devuelve lista de (ret_bruto_frac, dias_mantenidos, year)."""
    n = len(close); warm = SMA_TREND + 2; pos = None; out = []
    for t in range(warm, n):
        if not (np.isfinite(sma200[t]) and np.isfinite(r2[t]) and np.isfinite(sma5[t])):
            continue
        if pos is None:
            if close[t] > sma200[t] and r2[t] < entry_th:
                pos = {"entry": close[t], "bar": t, "y": year[t]}
        else:
            if close[t] > sma5[t] or r2[t] > EXIT_TH or (t - pos["bar"]) >= MAX_HOLD:
                out.append(((close[t] - pos["entry"]) / pos["entry"], t - pos["bar"], pos["y"]))
                pos = None
    return out


def stats(trades, spread_pct, carry_pct):
    if not trades:
        return None
    R = np.array([(r - spread_pct - carry_pct * d) for r, d, _ in trades]) * 100
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; l = R[R < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    return R.sum(), pf, (R > 0).mean() * 100, dd


def main():
    ensure()
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym)
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, N_BARS)
        if r is None or len(r) < SMA_TREND + 50:
            continue
        df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
        close = df["close"].values; high = df["high"].values; low = df["low"].values
        year = df["time"].dt.year.values
        sma200 = pd.Series(close).rolling(SMA_TREND).mean().values
        sma5 = pd.Series(close).rolling(SMA_EXIT).mean().values
        r2 = rsi(close, 2)
        spread_pct = (info.spread * info.point) / np.nanmean(close)
        swap_long_pct = (info.swap_long * info.point) / np.nanmean(close) * 100 if info else 0.0

        trades = backtest(high, low, close, sma200, sma5, r2, year, entry_th=10.0)
        avg_days = np.mean([d for _, d, _ in trades]) if trades else 0
        print(f"\n### {sym} | {len(df)} barras | trades={len(trades)} | "
              f"días prom={avg_days:.1f} | spread%={spread_pct*100:.4f} | "
              f"swap_long≈{swap_long_pct:+.4f}%/día")
        print(f"  {'carry%/día':>11}{'ret%':>9}{'PF':>7}{'wr%':>7}{'maxDD%':>8}")
        for carry in CARRY_LEVELS:
            s = stats(trades, spread_pct, carry / 100.0)
            if s:
                print(f"  {carry:>11.2f}{s[0]:>+9.1f}{s[1]:>7.2f}{s[2]:>7.1f}{s[3]:>+8.1f}")


if __name__ == "__main__":
    main()

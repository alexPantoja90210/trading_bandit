"""
Cartera Smart Trend Follower: Oro + BTC en H4, 0.5% de riesgo cada uno.
Mide si diversificar reduce el drawdown vs cada activo por separado.
Período común 2013-2026 (BTC pre-2013 descartado por datos rotos). Solo LEE.
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
RISK_D = BALANCE * RISK_PCT          # $ arriesgados por trade
START_YEAR = 2013


def get_trades(sym):
    mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym); cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    year = df["time"].dt.year.values
    times = df["time"].values
    ema = pd.Series(close).ewm(span=EMA_LEN, adjust=False).mean().values
    atr = atr_series(high, low, close, ATR_LEN)
    dhi = pd.Series(high).rolling(DONCHIAN).max().shift(1).values
    dlo = pd.Series(low).rolling(DONCHIAN).min().shift(1).values
    tr = backtest(high, low, close, ema, atr, dhi, dlo, year, cost)
    # (exit_time, R, exit_year) filtrando a >= START_YEAR
    out = []
    for exit_bar, R, _ in tr:
        et = pd.Timestamp(times[exit_bar])
        if et.year >= START_YEAR:
            out.append((et, R, et.year))
    return out


def curve_stats(events):
    """events: lista (time, pnl$). Devuelve equity, net, maxDD$."""
    events = sorted(events, key=lambda e: e[0])
    eq = np.cumsum([p for _, p in events]) if events else np.array([0.0])
    dd = (eq - np.maximum.accumulate(eq)).min() if len(eq) else 0.0
    return eq, (eq[-1] if len(eq) else 0.0), dd


def main():
    ensure()
    gold = get_trades("XAUUSD")
    btc = get_trades("BTCUSD")
    print(f"Cartera STF Oro+BTC | H4 | {START_YEAR}-2026 | riesgo {RISK_PCT*100:.1f}%/trade")
    print(f"trades: oro={len(gold)}  btc={len(btc)}\n")

    g_ev = [(t, R * RISK_D) for t, R, _ in gold]
    b_ev = [(t, R * RISK_D) for t, R, _ in btc]
    c_ev = g_ev + b_ev

    for label, ev in [("Oro solo", g_ev), ("BTC solo", b_ev), ("CARTERA Oro+BTC", c_ev)]:
        _, net, dd = curve_stats(ev)
        sr = sum(p for _, p in ev) / RISK_D
        print(f"  {label:<18} net=${net:>+8.0f}  ΣR={sr:>+7.1f}  maxDD=${dd:>+8.0f} ({dd/BALANCE*100:+.1f}%)")

    _, _, dd_g = curve_stats(g_ev)
    _, _, dd_b = curve_stats(b_ev)
    _, _, dd_c = curve_stats(c_ev)
    print(f"\n  Suma de DD individuales: ${dd_g + dd_b:+.0f}  |  DD de la cartera: ${dd_c:+.0f}")
    if dd_g + dd_b < 0:
        print(f"  → la cartera recorta el drawdown un {(1 - dd_c/(dd_g+dd_b))*100:.0f}% vs sumar los peores momentos")

    # correlación de ΣR anual (diversificación)
    yrs = list(range(START_YEAR, 2027))
    gy = {y: 0.0 for y in yrs}; by = {y: 0.0 for y in yrs}
    for _, R, y in gold:
        gy[y] = gy.get(y, 0) + R
    for _, R, y in btc:
        by[y] = by.get(y, 0) + R
    gv = np.array([gy[y] for y in yrs]); bv = np.array([by[y] for y in yrs])
    corr = np.corrcoef(gv, bv)[0, 1]
    print(f"\n  Correlación de ΣR anual (oro vs BTC): {corr:+.2f}  "
          f"({'baja/negativa = buena diversificación' if corr < 0.4 else 'alta = comparten régimen'})")
    print(f"\n  {'año':<6}{'oro ΣR':>9}{'btc ΣR':>9}{'cartera':>9}")
    for y in yrs:
        print(f"  {y:<6}{gy[y]:>+9.1f}{by[y]:>+9.1f}{gy[y]+by[y]:>+9.1f}")


if __name__ == "__main__":
    main()

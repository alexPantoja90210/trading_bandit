"""
Cartera combinada: STF (trend, oro+BTC, H4) + RSI(2) (reversión, US500+NAS100, D1).

Agrega el P&L de cada estrategia a series DIARIAS, las escala a la misma vol
objetivo (10% anual) y las combina 50/50. Mide si diversificar mejora el Sharpe
y baja el drawdown, gracias a la baja correlación entre trend y reversión.
Solo LEE histórico.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from smart_trend_follower import (backtest as stf_bt, atr_series as stf_atr,
                                  EMA_LEN, DONCHIAN, ATR_LEN)
from rsi2_meanrev import backtest as rsi2_bt, rsi, atr_series as rsi_atr

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

N = 50000
TARGET_VOL = 0.10   # 10% anual por estrategia


def _daily(pairs):
    """pairs: lista (timestamp, valor). Devuelve serie diaria sumada."""
    if not pairs:
        return pd.Series(dtype=float)
    df = pd.DataFrame(pairs, columns=["t", "v"])
    df["d"] = pd.to_datetime(df["t"]).dt.normalize()
    return df.groupby("d")["v"].sum()


def stf_daily(sym, start_year=None):
    ensure(); mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym); cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, N))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    year = df["time"].dt.year.values; times = df["time"].values
    ema = pd.Series(close).ewm(span=EMA_LEN, adjust=False).mean().values
    atr = stf_atr(high, low, close, ATR_LEN)
    dhi = pd.Series(high).rolling(DONCHIAN).max().shift(1).values
    dlo = pd.Series(low).rolling(DONCHIAN).min().shift(1).values
    trades = stf_bt(high, low, close, ema, atr, dhi, dlo, year, cost)
    pairs = [(times[b], R) for b, R, y in trades if (start_year is None or y >= start_year)]
    return _daily(pairs)


def rsi2_daily(sym):
    ensure(); mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    df = pd.DataFrame(mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, N))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    year = df["time"].dt.year.values; times = df["time"].values
    sma200 = pd.Series(close).rolling(200).mean().values
    sma5 = pd.Series(close).rolling(5).mean().values
    r2 = rsi(close, 2); atr = rsi_atr(high, low, close, 14)
    spread = (info.spread * info.point) / np.nanmean(close)
    trades = rsi2_bt(high, low, close, sma200, sma5, r2, atr, year, spread, entry_th=10.0)
    pairs = [(times[b], ret * 100) for b, ret, y in trades]   # ret en %
    return _daily(pairs)


def metrics(daily):
    """daily: serie diaria (reindexada, con 0 en días sin trade). En unidades ya escaladas."""
    r = daily.values
    ann_ret = r.mean() * 252
    ann_vol = r.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    eq = np.cumsum(r)
    dd = (eq - np.maximum.accumulate(eq)).min()
    return ann_ret * 100, ann_vol * 100, sharpe, dd * 100


def scale_to_vol(daily, target=TARGET_VOL):
    vol = daily.std() * np.sqrt(252)
    k = target / vol if vol > 0 else 0
    return daily * k


def main():
    ensure()
    print("Construyendo estrategias (STF H4 + RSI2 D1)...")
    stf = stf_daily("XAUUSD").add(stf_daily("BTCUSD", start_year=2013), fill_value=0)
    rsi2 = rsi2_daily("US500").add(rsi2_daily("NAS100"), fill_value=0)

    # rango común
    start = max(stf.index.min(), rsi2.index.min())
    end = min(stf.index.max(), rsi2.index.max())
    idx = pd.bdate_range(start, end)
    stf = stf.reindex(idx, fill_value=0.0)
    rsi2 = rsi2.reindex(idx, fill_value=0.0)
    print(f"rango común: {start.date()} -> {end.date()}  ({len(idx)} días hábiles)\n")

    # escalar cada una a la misma vol objetivo
    stf_s = scale_to_vol(stf)
    rsi2_s = scale_to_vol(rsi2)
    comb = 0.5 * stf_s + 0.5 * rsi2_s

    corr = np.corrcoef(stf_s.values, rsi2_s.values)[0, 1]
    print(f"Correlación diaria STF vs RSI2: {corr:+.2f}  "
          f"({'baja → diversifica bien' if corr < 0.3 else 'alta'})\n")

    print(f"{'estrategia':<20}{'retAnual%':>10}{'volAnual%':>10}{'Sharpe':>8}{'maxDD%':>9}")
    print("-" * 57)
    for name, s in [("STF (oro+BTC)", stf_s), ("RSI2 (US500+NAS100)", rsi2_s),
                    ("CARTERA 50/50", comb)]:
        ar, av, sh, dd = metrics(s)
        print(f"{name:<20}{ar:>+10.1f}{av:>10.1f}{sh:>+8.2f}{dd:>+9.1f}")

    # ventaja de diversificación
    _, _, sh_stf, dd_stf = metrics(stf_s)
    _, _, sh_rsi, dd_rsi = metrics(rsi2_s)
    _, _, sh_c, dd_c = metrics(comb)
    print(f"\nSharpe: STF {sh_stf:.2f} · RSI2 {sh_rsi:.2f} · CARTERA {sh_c:.2f} "
          f"(mejor combinado = diversificación funciona)")
    print(f"maxDD:  STF {dd_stf:.1f}% · RSI2 {dd_rsi:.1f}% · CARTERA {dd_c:.1f}%")


if __name__ == "__main__":
    main()

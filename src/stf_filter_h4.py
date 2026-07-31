"""
Filtro de tendencia sobre el Smart Trend Follower en H4: probar si un gate (ADX
o pendiente de EMA200) recorta el sangrado de rango (2021-2022) sin matar el edge.
Solo LEE histórico.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure

from paths import load_config
from smart_trend_follower import (backtest, atr_series, EMA_LEN, DONCHIAN,
                                  ATR_LEN, RISK_PCT, BALANCE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

cfg = load_config()
SYMBOL = cfg["symbol"]
N_BARS = 50000


def adx_series(high, low, close, n=14):
    up = pd.Series(high).diff(); dn = -pd.Series(low).diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev = np.roll(close, 1); prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    atr = pd.Series(tr).ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm).ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan)
    mdi = 100 * pd.Series(minus_dm).ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().values


def metrics(trades):
    R = np.array([r for _, r, _ in trades]) if trades else np.array([])
    if len(R) == 0:
        return dict(n=0, sr=0, pf=0, dd=0)
    risk_d = BALANCE * RISK_PCT; eq = np.cumsum(R * risk_d)
    dd = (eq - np.maximum.accumulate(eq)).min() / BALANCE * 100
    w = R[R > 0]; l = R[R < 0]; pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    return dict(n=len(R), sr=R.sum(), pf=pf, dd=dd, wr=(R > 0).mean()*100)


def yr_sum(trades, years):
    return sum(r for _, r, y in trades if y in years)


def main():
    ensure()
    info = mt5.symbol_info(SYMBOL); cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H4, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"STF filtro | H4 | {df['time'].iloc[0].date()} -> {df['time'].iloc[-1].date()} | cost={cost:.3f}")
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    year = df["time"].dt.year.values
    ema = pd.Series(close).ewm(span=EMA_LEN, adjust=False).mean().values
    atr = atr_series(high, low, close, ATR_LEN)
    dhi = pd.Series(high).rolling(DONCHIAN).max().shift(1).values
    dlo = pd.Series(low).rolling(DONCHIAN).min().shift(1).values
    adx = adx_series(high, low, close, 14)

    # gate por pendiente de EMA200 (|ema[t]-ema[t-L]| / atr)
    L = 20
    ema_slope = np.abs(ema - np.roll(ema, L)) / np.where(atr > 0, atr, np.nan)

    reliable = set(range(2016, 2027))
    ranges = {2021, 2022}

    gates = {
        "base (sin filtro)": None,
        "ADX>=20": adx >= 20,
        "ADX>=25": adx >= 25,
        "EMA-slope>0.5": ema_slope > 0.5,
        "EMA-slope>1.0": ema_slope > 1.0,
    }

    print(f"\n{'filtro':<20}{'trades':>7}{'ΣR':>8}{'PF':>7}{'maxDD%':>8}{'wr%':>6}"
          f"{'ΣR 16-26':>10}{'rango21-22':>11}")
    print("-" * 82)
    for name, gate in gates.items():
        tr = backtest(high, low, close, ema, atr, dhi, dlo, year, cost, gate=gate)
        m = metrics(tr)
        s16 = yr_sum(tr, reliable); srg = yr_sum(tr, ranges)
        print(f"{name:<20}{m['n']:>7}{m['sr']:>+8.1f}{m['pf']:>7.2f}{m['dd']:>+8.1f}"
              f"{m['wr']:>6.1f}{s16:>+10.1f}{srg:>+11.1f}")


if __name__ == "__main__":
    main()

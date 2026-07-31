"""
Robustez por año en H1: P&L SL/TP + spread de cada estrategia FIJA, agregado por
año calendario, junto al retorno del oro de ese año (para ver el régimen).
Estrategias fijas = sin parámetros entrenados = imposible sobreajustar.
Solo LEE histórico.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure

from paths import load_config
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_master import classify, Params

cfg = load_config()
SYMBOL = cfg["symbol"]
FEATS = ["close", "volume", "rsi", "sma20", "returns", "volatility"]
SL_MULT = cfg["trading"].get("sl_atr_mult", 1.5)
TP_MULT = cfg["trading"].get("tp_atr_mult", 2.0)
N_BARS = 50000
MAXHOLD = 150


def sim(entry, d, sl_dist, tp_dist, high, low, close, t, cost):
    if d == 0:
        return 0.0
    end = min(t + MAXHOLD, len(close) - 1)
    if d == 1:
        sl, tp = entry - sl_dist, entry + tp_dist
        for k in range(t + 1, end + 1):
            if low[k] <= sl:
                return -sl_dist - cost
            if high[k] >= tp:
                return tp_dist - cost
        return (close[end] - entry) - cost
    else:
        sl, tp = entry + sl_dist, entry - tp_dist
        for k in range(t + 1, end + 1):
            if high[k] >= sl:
                return -sl_dist - cost
            if low[k] <= tp:
                return tp_dist - cost
        return (entry - close[end]) - cost


def main():
    ensure()
    info = mt5.symbol_info(SYMBOL)
    cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"H1 | cost(spread)={cost:.3f} | {n} barras {df['time'].iloc[0].date()} -> {df['time'].iloc[-1].date()}")
    X = build_features(df, FEATS)
    df = compute_indicators(df)
    print("clasificando régimen...")
    reg = classify(df, Params())

    close = df["close"].values; high = df["high"].values
    low = df["low"].values; atr = df["atr"].values
    fam = reg["family"].values; sma = df["sma20"].values; year = df["time"].dt.year.values

    d_trend = np.where(fam == "TREND_DOWN", -1, 1)
    d_mean = np.where(close > sma, -1, 1)
    d_vol = np.where(close > np.roll(close, 1), 1, -1); d_vol[0] = 1

    finite = np.isfinite(atr) & np.isfinite(X[FEATS].values).all(axis=1)
    usable = np.where(finite)[0]; usable = usable[usable < n - 1]
    print(f"simulando {len(usable)} señales SL/TP...\n")

    pnl = {"trend": np.full(n, np.nan), "vol": np.full(n, np.nan), "mean": np.full(n, np.nan)}
    for t in usable:
        sl_d, tp_d = atr[t] * SL_MULT, atr[t] * TP_MULT
        pnl["trend"][t] = sim(close[t], int(d_trend[t]), sl_d, tp_d, high, low, close, t, cost)
        pnl["vol"][t] = sim(close[t], int(d_vol[t]), sl_d, tp_d, high, low, close, t, cost)
        pnl["mean"][t] = sim(close[t], int(d_mean[t]), sl_d, tp_d, high, low, close, t, cost)

    def me(p):
        p = p[np.isfinite(p)]
        return (p.mean(), (p > 0).mean() * 100, p.sum(), len(p)) if len(p) else (0, 0, 0, 0)

    years = sorted(set(year[usable]))
    print(f"{'año':<6}{'n':>6}{'oroRet%':>9} | {'trend m$/wr':>16}{'vol m$/wr':>16}{'mean m$':>10}")
    print("-" * 70)
    pos = {"trend": 0, "vol": 0}
    for y in years:
        mask = (year == y) & finite
        idxs = np.where(mask)[0]; idxs = idxs[idxs < n - 1]
        if len(idxs) < 200:
            continue
        c0, c1 = close[idxs[0]], close[idxs[-1]]
        gret = (c1 / c0 - 1) * 100
        tm, tw, _, nn = me(pnl["trend"][idxs])
        vm, vw, _, _ = me(pnl["vol"][idxs])
        mm, _, _, _ = me(pnl["mean"][idxs])
        pos["trend"] += tm > 0; pos["vol"] += vm > 0
        print(f"{y:<6}{nn:>6}{gret:>+9.1f} | {tm:>+8.3f}/{tw:>4.1f}  {vm:>+8.3f}/{vw:>4.1f}  {mm:>+9.3f}")

    print("-" * 70)
    tm, tw, ts, tn = me(pnl["trend"])
    vm, vw, vs, vn = me(pnl["vol"])
    mm, mw, ms, mn = me(pnl["mean"])
    ny = len([y for y in years if np.sum((year == y) & finite) >= 200])
    print(f"{'TOTAL':<6}{tn:>6}{'':>9} | {tm:>+8.3f}/{tw:>4.1f}  {vm:>+8.3f}/{vw:>4.1f}  {mm:>+9.3f}")
    print(f"\nAños positivos:  trend {pos['trend']}/{ny}   volatility {pos['vol']}/{ny}")
    print(f"P&L total (out-of-cost):  trend {ts:+.0f}   volatility {vs:+.0f}   mean {ms:+.0f}")


if __name__ == "__main__":
    main()

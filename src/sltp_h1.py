"""
Backtest DECISIVO en H1: SL/TP por ATR + costos, entrenando y evaluando el
bandit sobre la MISMA recompensa realista (P&L neto). Solo LEE histórico.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure

from paths import load_config
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_master import classify, Params
from context_builder import build_context
from bandit_contextual import LinTSBandit

cfg = load_config()
SYMBOL = cfg["symbol"]
ALL_FEATURES = ["close", "volume", "rsi", "sma20", "returns", "volatility"]
ARM_NAMES = ["trend", "mean", "flat", "momentum", "volatility"]
SL_MULT = cfg["trading"].get("sl_atr_mult", 1.5)
TP_MULT = cfg["trading"].get("tp_atr_mult", 2.0)
N_BARS = 50000
TEST_BARS = 12000
MAXHOLD = 150
N_FEATURES = len(ALL_FEATURES) + 2 + 4 + 10 + 4
LAM = 1.0


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


def stats(p):
    p = np.asarray(p, float)
    sh = p.mean() / p.std() if p.std() > 0 else 0.0
    return p.sum(), p.mean(), (p > 0).mean() * 100, sh


def main():
    ensure()
    info = mt5.symbol_info(SYMBOL)
    spread = info.spread * info.point
    print(f"H1 | spread {info.spread}pts = {spread:.3f}")

    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"{n} barras: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    X = build_features(df, ALL_FEATURES)
    df = compute_indicators(df)
    print("clasificando régimen...")
    reg = classify(df, Params())

    close = df["close"].values; high = df["high"].values
    low = df["low"].values; atr = df["atr"].values
    fam = reg["family"].values; sma = df["sma20"].values

    dir_trend = np.where(fam == "TREND_DOWN", -1, 1)
    dir_mean = np.where(close > sma, -1, 1)
    dir_vol = np.where(close > np.roll(close, 1), 1, -1); dir_vol[0] = 1

    finite = np.isfinite(atr) & np.isfinite(X[ALL_FEATURES].values).all(axis=1)
    usable = np.where(finite)[0]
    usable = usable[usable < n - 1]
    print(f"barras usables: {len(usable)}  (calculando P&L SL/TP, ~1-2 min)...")

    def build_R(cost):
        R = np.full((n, 5), np.nan)
        for t in usable:
            sl_d, tp_d = atr[t] * SL_MULT, atr[t] * TP_MULT
            pt = sim(close[t], int(dir_trend[t]), sl_d, tp_d, high, low, close, t, cost)
            pm = sim(close[t], int(dir_mean[t]), sl_d, tp_d, high, low, close, t, cost)
            pv = sim(close[t], int(dir_vol[t]), sl_d, tp_d, high, low, close, t, cost)
            R[t, 0] = pt; R[t, 3] = pt; R[t, 1] = pm; R[t, 4] = pv; R[t, 2] = 0.0
        return R

    R = build_R(spread)
    valid = np.isfinite(R).all(axis=1) & finite
    idx = np.where(valid)[0]
    split = idx[-TEST_BARS]
    tr = idx[idx < split]; te = idx[idx >= split]
    print(f"train {len(tr)} | test {len(te)}\n")

    # contexto (26 dims) por barra
    ctx = np.full((n, N_FEATURES), np.nan)
    for t in np.concatenate([tr, te]):
        try:
            ctx[t] = build_context(X.iloc[t], reg.iloc[t])
        except Exception:
            pass

    # entrenar bandit sobre P&L realista (info completa)
    mean = ctx[tr].mean(0); std = ctx[tr].std(0); ss = np.where(std < 1e-8, 1.0, std)
    Ztr = (ctx[tr] - mean) / ss; Zte = (ctx[te] - mean) / ss
    Ainv = np.linalg.inv(LAM * np.eye(N_FEATURES) + Ztr.T @ Ztr)
    theta = np.array([Ainv @ (Ztr.T @ R[tr][:, a]) for a in range(5)])
    picks = np.argmax(Zte @ theta.T, axis=1)
    realized = R[te][np.arange(len(te)), picks]

    print(f"===== H1 SL/TP + spread (out-of-sample, {len(te)} señales) =====")
    print(f"  {'estrategia':<12}{'suma$':>10}{'media$':>10}{'wr%':>7}{'sharpe':>9}")
    for a in [0, 1, 3, 4]:
        s, m, w, sh = stats(R[te][:, a])
        print(f"  {ARM_NAMES[a]:<12}{s:>+10.1f}{m:>+10.4f}{w:>7.1f}{sh:>+9.3f}")
    s, m, w, sh = stats(realized)
    print(f"  {'BANDIT':<12}{s:>+10.1f}{m:>+10.4f}{w:>7.1f}{sh:>+9.3f}")
    uniq, cnt = np.unique(picks, return_counts=True)
    print(f"  brazos elegidos: { {ARM_NAMES[u]: int(c) for u, c in zip(uniq, cnt)} }")

    # sensibilidad: costo extra (swaps por hold largo, slippage)
    R2 = build_R(spread + 0.30)
    print(f"\n===== con costo extra (spread + $0.30) =====")
    for a in [0, 1, 3, 4]:
        s, m, w, sh = stats(R2[te][:, a])
        print(f"  {ARM_NAMES[a]:<12}{s:>+10.1f}{m:>+10.4f}{w:>7.1f}{sh:>+9.3f}")


if __name__ == "__main__":
    main()

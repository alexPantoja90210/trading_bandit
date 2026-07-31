"""
Backtest con mecánica REAL: SL/TP por ATR + costo de spread.

Para cada señal (por barra) entra, coloca SL = ATR*sl_mult y TP = ATR*tp_mult
(como el executor), y camina barra a barra viendo qué toca primero (usando
high/low). Resta el spread. Mide P&L por señal — es una estimación de esperanza
por trade, no una simulación de cartera con max_open.

Evalúa cada brazo fijo y el bandit entrenado (greedy). Solo LEE histórico.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure
import os

from paths import load_config, DATA_DIR
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
MAXHOLD = 200   # barras máx que se mantiene una operación si no toca SL/TP


def directions(reg, close, sma20):
    n = len(close)
    trend = np.where(reg["family"].values == "TREND_DOWN", -1, 1)
    meanr = np.where(close.values > sma20.values, -1, 1)
    vol = np.where(close.values > np.roll(close.values, 1), 1, -1)
    vol[0] = 1
    return {0: trend, 1: meanr, 2: np.zeros(n, int), 3: trend, 4: vol}


def sim_trade(entry, d, sl_dist, tp_dist, high, low, close, t, cost):
    """P&L en precio de una operación con SL/TP; barre hasta MAXHOLD."""
    if d == 0:
        return None
    end = min(t + MAXHOLD, len(close) - 1)
    if d == 1:  # BUY
        sl, tp = entry - sl_dist, entry + tp_dist
        for k in range(t + 1, end + 1):
            if low[k] <= sl:
                return -sl_dist - cost
            if high[k] >= tp:
                return tp_dist - cost
        return (close[end] - entry) - cost
    else:       # SELL
        sl, tp = entry + sl_dist, entry - tp_dist
        for k in range(t + 1, end + 1):
            if high[k] >= sl:
                return -sl_dist - cost
            if low[k] <= tp:
                return tp_dist - cost
        return (entry - close[end]) - cost


def evaluate(picks_dir, valid_idx, atr, high, low, close, cost):
    pnls = []
    for t in valid_idx:
        d = picks_dir[t]
        r = sim_trade(close[t], int(d), atr[t] * SL_MULT, atr[t] * TP_MULT,
                      high, low, close, t, cost)
        if r is not None:
            pnls.append(r)
    if not pnls:
        return None
    p = np.array(pnls)
    return {"n": len(p), "sum": p.sum(), "mean": p.mean(),
            "wr": (p > 0).mean() * 100}


def main():
    ensure()
    info = mt5.symbol_info(SYMBOL)
    spread_price = info.spread * info.point
    print(f"Spread actual: {info.spread} pts = {spread_price:.4f} en precio")

    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    X = build_features(df, ALL_FEATURES)
    df = compute_indicators(df)
    print("Clasificando régimen...")
    reg = classify(df, Params())

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    atr = df["atr"].values
    D = directions(reg, df["close"], df["sma20"])

    # test = últimas TEST_BARS barras válidas (atr y features finitos)
    finite = np.isfinite(atr) & np.isfinite(X[ALL_FEATURES].values).all(axis=1)
    idx = np.where(finite)[0]
    test_idx = idx[idx >= n - TEST_BARS]
    test_idx = test_idx[test_idx < n - 1]
    print(f"Operaciones evaluadas por estrategia: ~{len(test_idx)}\n")

    # bandit entrenado → dirección elegida por barra
    bandit = LinTSBandit.load(os.path.join(DATA_DIR, "lints_state.json"))
    bandit_dir = np.zeros(n, int)
    for t in test_idx:
        try:
            ctx = build_context(X.iloc[t], reg.iloc[t])
            arm = int(np.argmax(bandit.expected_scores(ctx)))
            bandit_dir[t] = D[arm][t]
        except Exception:
            bandit_dir[t] = 0

    for cost, label in [(0.0, "SIN costos"), (spread_price, "CON spread"),
                        (spread_price + 0.20, "spread + $0.20 extra")]:
        print(f"===== {label} (cost={cost:.3f}) =====")
        print(f"  {'estrategia':<12}{'n':>7}{'suma$':>10}{'media$':>10}{'wr%':>7}")
        for arm in [0, 1, 3, 4]:
            res = evaluate(D[arm], test_idx, atr, high, low, close, cost)
            if res:
                print(f"  {ARM_NAMES[arm]:<12}{res['n']:>7}{res['sum']:>+10.2f}{res['mean']:>+10.4f}{res['wr']:>7.1f}")
        rb = evaluate(bandit_dir, test_idx, atr, high, low, close, cost)
        if rb:
            print(f"  {'BANDIT':<12}{rb['n']:>7}{rb['sum']:>+10.2f}{rb['mean']:>+10.4f}{rb['wr']:>7.1f}")
        print()


if __name__ == "__main__":
    main()

"""
Barrido multi-timeframe: repite el sweep (horizonte x features) en M15 y H1
para ver si aparece edge en marcos más altos. Solo LEE histórico.
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
ALL_FEATURES = ["close", "volume", "rsi", "sma20", "returns", "volatility"]
N_ARMS = 5
N_BARS = 50000
TEST_BARS = 12000
LAM = 1.0
FAMILY_MAP = {"TREND_UP": 0, "TREND_DOWN": 1, "RANGE": 2, "NO_TRADE": 3}
HORIZONS = [5, 10, 20, 40]
SUBSETS = {
    "todas": ["close", "volume", "rsi", "sma20", "returns", "volatility"],
    "estacionarias": ["returns", "rsi", "volatility"],
    "solo_regimen": [],
}
TIMEFRAMES = [("M15", mt5.TIMEFRAME_M15), ("H1", mt5.TIMEFRAME_H1)]


def regime_block_matrix(reg):
    n = len(reg)
    B = np.zeros((n, 20))
    for t in range(n):
        row = reg.iloc[t]
        rid = float(row["id"]) if row["id"] == row["id"] else -1.0
        fam = np.zeros(4); fam[FAMILY_MAP.get(str(row["family"]), 3)] = 1.0
        p = [float(row[f"p{i}"]) for i in range(10)]
        knn = [float(row["knn_edge"]), float(row["knn_risk"]),
               float(row["knn_fav"]), float(row["bars_in_regime"])]
        B[t] = np.concatenate([[rid, float(row["confidence"])], fam, p, knn])
    return B


def directions(reg, close, sma20):
    n = len(close)
    trend = np.where(reg["family"].values == "TREND_DOWN", -1, 1)
    meanr = np.where(close.values > sma20.values, -1, 1)
    vol = np.where(close.values > np.roll(close.values, 1), 1, -1); vol[0] = 1
    D = np.zeros((n, N_ARMS))
    D[:, 0] = trend; D[:, 1] = meanr; D[:, 3] = trend; D[:, 4] = vol
    return D


def run(ctx, R, split_pos):
    valid = np.isfinite(ctx).all(axis=1) & np.isfinite(R).all(axis=1)
    idx = np.where(valid)[0]
    tr = idx[idx < split_pos]; te = idx[idx >= split_pos]
    if len(tr) < 500 or len(te) < 500:
        return None
    mean = ctx[tr].mean(axis=0); std = ctx[tr].std(axis=0)
    ss = np.where(std < 1e-8, 1.0, std)
    Ztr = (ctx[tr] - mean) / ss; Zte = (ctx[te] - mean) / ss
    d = ctx.shape[1]
    Ainv = np.linalg.inv(LAM * np.eye(d) + Ztr.T @ Ztr)
    theta = np.array([Ainv @ (Ztr.T @ R[tr][:, a]) for a in range(N_ARMS)])
    picks = np.argmax(Zte @ theta.T, axis=1)
    realized = R[te][np.arange(len(te)), picks]
    bf = int(np.argmax(R[tr].mean(axis=0)))
    return {"bandit": realized.mean(), "random": R[te].mean(axis=1).mean(),
            "bestfix": R[te][:, bf].mean(), "bf": bf, "wr": (realized > 0).mean() * 100}


def sweep_tf(tf_name, tf):
    rates = mt5.copy_rates_from_pos(SYMBOL, tf, 0, N_BARS)
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"\n################  {tf_name}  ################")
    print(f"{n} barras: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    X = build_features(df, ALL_FEATURES)
    df = compute_indicators(df)
    print("clasificando régimen...")
    reg = classify(df, Params())
    feat = X[ALL_FEATURES].values
    Breg = regime_block_matrix(reg)
    D = directions(reg, df["close"], df["sma20"])
    close, atr = df["close"], df["atr"]
    split_pos = n - TEST_BARS
    print(f"{'features':<14}{'H':>4} | {'bandit':>9}{'random':>9}{'bestfix':>9}{'edge':>8}{'wr%':>7}")
    print("-" * 66)
    best = None
    for sname, cols in SUBSETS.items():
        fidx = [ALL_FEATURES.index(c) for c in cols]
        base = feat[:, fidx] if fidx else np.zeros((n, 0))
        ctx = np.hstack([base, Breg])
        for H in HORIZONS:
            fwd = ((close.shift(-H) - close) / atr).values
            R = D * fwd[:, None]
            R = np.where(np.isfinite(R), R, np.nan)
            cu = ctx.copy(); cu[n - H:] = np.nan
            res = run(cu, R, split_pos)
            if not res:
                continue
            edge = res["bandit"] - res["random"]
            tag = "  <== +" if res["bandit"] > 0 else ""
            print(f"{sname:<14}{H:>4} | {res['bandit']:>+9.4f}{res['random']:>+9.4f}"
                  f"{res['bestfix']:>+9.4f}{edge:>+8.4f}{res['wr']:>7.1f}{tag}")
            if best is None or res["bandit"] > best[2]["bandit"]:
                best = (sname, H, res)
    b = best[2]
    print("-" * 66)
    print(f"Mejor {tf_name}: {best[0]} H={best[1]}  bandit={b['bandit']:+.4f}  "
          f"random={b['random']:+.4f}  bestfix={b['bestfix']:+.4f}")


def main():
    ensure()
    for name, tf in TIMEFRAMES:
        sweep_tf(name, tf)


if __name__ == "__main__":
    main()

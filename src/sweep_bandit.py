"""
Barrido offline: reward_horizon x subconjunto de features.

Calcula el histórico, features e (una sola vez) el régimen; luego prueba muchas
combinaciones de horizonte y features, entrenando el LinTS con info completa y
midiendo out-of-sample. Busca si ALGUNA config genera edge que generalice.

Solo LEE histórico. No opera.
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
ARM_NAMES = ["trend", "mean", "flat", "momentum", "volatility"]
N_ARMS = 5
N_BARS = 50000
TEST_BARS = 12000
LAM = 1.0

FAMILY_MAP = {"TREND_UP": 0, "TREND_DOWN": 1, "RANGE": 2, "NO_TRADE": 3}

HORIZONS = [10, 20, 40, 60]
SUBSETS = {
    "todas": ["close", "volume", "rsi", "sma20", "returns", "volatility"],
    "estacionarias": ["returns", "rsi", "volatility"],
    "ret+rsi": ["returns", "rsi"],
    "ret+vol": ["returns", "volatility"],
    "solo_regimen": [],   # solo el bloque de régimen (20 dims)
}


def regime_block_matrix(reg):
    n = len(reg)
    B = np.zeros((n, 20))
    for t in range(n):
        row = reg.iloc[t]
        rid = float(row["id"]) if row["id"] == row["id"] else -1.0
        fam = np.zeros(4)
        fam[FAMILY_MAP.get(str(row["family"]), 3)] = 1.0
        p = [float(row[f"p{i}"]) for i in range(10)]
        knn = [float(row["knn_edge"]), float(row["knn_risk"]),
               float(row["knn_fav"]), float(row["bars_in_regime"])]
        B[t] = np.concatenate([[rid, float(row["confidence"])], fam, p, knn])
    return B


def directions(reg, close, sma20):
    n = len(close)
    trend = np.where(reg["family"].values == "TREND_DOWN", -1, 1)
    meanr = np.where(close.values > sma20.values, -1, 1)
    vol = np.where(close.values > np.roll(close.values, 1), 1, -1)
    vol[0] = 1
    D = np.zeros((n, N_ARMS))
    D[:, 0] = trend; D[:, 1] = meanr; D[:, 2] = 0; D[:, 3] = trend; D[:, 4] = vol
    return D


def run(ctx, R, split_pos):
    valid = np.isfinite(ctx).all(axis=1) & np.isfinite(R).all(axis=1)
    idx = np.where(valid)[0]
    tr = idx[idx < split_pos]; te = idx[idx >= split_pos]
    if len(tr) < 500 or len(te) < 500:
        return None
    Ztr_raw, Zte_raw = ctx[tr], ctx[te]
    Rtr, Rte = R[tr], R[te]
    mean = Ztr_raw.mean(axis=0); std = Ztr_raw.std(axis=0)
    std_safe = np.where(std < 1e-8, 1.0, std)
    Ztr = (Ztr_raw - mean) / std_safe
    Zte = (Zte_raw - mean) / std_safe
    d = ctx.shape[1]
    A = LAM * np.eye(d) + Ztr.T @ Ztr
    Ainv = np.linalg.inv(A)
    theta = np.array([Ainv @ (Ztr.T @ Rtr[:, a]) for a in range(N_ARMS)])   # (arms x d)
    scores = Zte @ theta.T                                                   # (n_te x arms)
    picks = np.argmax(scores, axis=1)
    realized = Rte[np.arange(len(te)), picks]
    best_fixed = int(np.argmax(Rtr.mean(axis=0)))
    return {
        "bandit": realized.mean(),
        "random": Rte.mean(axis=1).mean(),
        "bestfix": Rte[:, best_fixed].mean(),
        "wr": (realized > 0).mean() * 100,
        "n_te": len(te),
    }


def main():
    ensure()
    print(f"Bajando {N_BARS} barras M5 de {SYMBOL}...")
    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"  {n} barras: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")

    Xall = build_features(df, ALL_FEATURES)
    df = compute_indicators(df)
    print("Clasificando régimen (una vez)...")
    reg = classify(df, Params())

    feat_mat = Xall[ALL_FEATURES].values          # (n x 6)
    Breg = regime_block_matrix(reg)               # (n x 20)
    D = directions(reg, df["close"], df["sma20"])
    close, atr = df["close"], df["atr"]
    fwd_by_H = {H: ((close.shift(-H) - close) / atr).values for H in HORIZONS}

    split_pos = n - TEST_BARS
    print(f"\n{'features':<14}{'H':>4} | {'bandit':>9}{'random':>9}{'bestfix':>9}{'edge':>8}{'wr%':>7}")
    print("-" * 66)
    results = []
    for sname, cols in SUBSETS.items():
        fidx = [ALL_FEATURES.index(c) for c in cols]
        base = feat_mat[:, fidx] if fidx else np.zeros((n, 0))
        ctx = np.hstack([base, Breg])
        for H in HORIZONS:
            fwd = fwd_by_H[H]
            R = D * fwd[:, None]
            R = np.where(np.isfinite(R), R, np.nan)
            valid_future = np.ones(n, dtype=bool); valid_future[n - H:] = False
            ctx_use = ctx.copy()
            ctx_use[~valid_future] = np.nan
            res = run(ctx_use, R, split_pos)
            if res is None:
                continue
            edge = res["bandit"] - res["random"]
            results.append((sname, H, res, edge))
            print(f"{sname:<14}{H:>4} | {res['bandit']:>+9.4f}{res['random']:>+9.4f}"
                  f"{res['bestfix']:>+9.4f}{edge:>+8.4f}{res['wr']:>7.1f}")

    print("-" * 66)
    best = max(results, key=lambda r: r[3])
    print(f"Mayor edge vs aleatorio: {best[0]} H={best[1]}  edge={best[3]:+.4f}  "
          f"(bandit={best[2]['bandit']:+.4f})")
    pos = [r for r in results if r[2]['bandit'] > 0]
    print(f"Configs con bandit RENTABLE (media>0) out-of-sample: {len(pos)} / {len(results)}")
    for r in pos:
        print(f"  -> {r[0]} H={r[1]}: media={r[2]['bandit']:+.4f} wr={r[2]['wr']:.1f}%")


if __name__ == "__main__":
    main()

"""
Entrenamiento OFFLINE del bandit contextual bayesiano (LinTS).

- Baja histórico M5 del bróker.
- Calcula features + indicadores + régimen sobre todo el histórico.
- Arma el contexto (26 dims) por barra.
- Recompensa FUTURA por brazo: dir * (close[t+H]-close[t]) / atr[t]  (info completa).
- Split train/test (out-of-sample) para validar sin sobreajuste.
- Entrena (A = lam*I + Z^T Z ; b[arm] = Z^T r[:,arm]).
- Backtest greedy en test vs baselines.
- Guarda el estado entrenado en data/lints_state.json.

Solo LEE histórico. No envía órdenes.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure

from paths import load_config, DATA_DIR
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_master import classify, Params
from context_builder import build_context
from bandit_contextual import LinTSBandit

import os

cfg = load_config()
SYMBOL = cfg["symbol"]
FEATURES = cfg["features"]
H = int(cfg.get("reward_horizon", 20))
N_BARS = 50000            # máximo que suele dar el bróker en M5
TEST_BARS = 12000         # out-of-sample (~2 meses)
ARM_NAMES = ["trend", "mean", "flat", "momentum", "volatility"]
N_ARMS = len(ARM_NAMES)
N_FEATURES = len(FEATURES) + 2 + 4 + 10 + 4  # = 26


def implied_direction_vec(family, close, sma20):
    """Direcciones (n x n_arms) por barra, espejo de main_live_v2.implied_direction."""
    n = len(close)
    trend_dir = np.where(family.values == "TREND_DOWN", -1, 1)
    mean_dir = np.where(close.values > sma20.values, -1, 1)
    vol_dir = np.where(close.values > np.roll(close.values, 1), 1, -1)
    vol_dir[0] = 1
    D = np.zeros((n, N_ARMS))
    D[:, 0] = trend_dir      # trend
    D[:, 1] = mean_dir       # mean
    D[:, 2] = 0              # flat
    D[:, 3] = trend_dir      # momentum
    D[:, 4] = vol_dir        # volatility
    return D


def main():
    if not ensure():
        raise RuntimeError("MT5 no inicializó")

    print(f"Bajando {N_BARS} barras M5 de {SYMBOL}...")
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, N_BARS)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"  recibidas {n} barras: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")

    print("Calculando features + indicadores...")
    X = build_features(df, FEATURES)
    df = compute_indicators(df)

    print("Clasificando régimen sobre todo el histórico (puede tardar ~1 min)...")
    reg = classify(df, Params())

    print("Armando contexto por barra...")
    ctx = np.full((n, N_FEATURES), np.nan)
    for t in range(n):
        try:
            ctx[t] = build_context(X.iloc[t], reg.iloc[t])
        except Exception:
            pass  # barras de warmup con NaN → quedan inválidas

    # Recompensa futura por brazo (info completa)
    close = df["close"]
    atr = df["atr"]
    fwd = (close.shift(-H) - close) / atr          # retorno futuro en ATR
    D = implied_direction_vec(reg["family"], close, df["sma20"])
    R = D * fwd.values[:, None]                     # (n x n_arms)

    # Validez: contexto finito, reward finito, y con futuro disponible (t <= n-H-1)
    valid = np.isfinite(ctx).all(axis=1) & np.isfinite(R).all(axis=1)
    valid[n - H:] = False
    idx = np.where(valid)[0]
    print(f"  barras válidas para entrenar/testear: {len(idx)} / {n}")

    # Split temporal: test = últimas TEST_BARS válidas
    split = idx[-TEST_BARS] if len(idx) > TEST_BARS else idx[len(idx) // 2]
    train_idx = idx[idx < split]
    test_idx = idx[idx >= split]
    print(f"  train: {len(train_idx)} barras | test (out-of-sample): {len(test_idx)} barras")

    Ztr_raw = ctx[train_idx]
    Rtr = R[train_idx]
    Zte_raw = ctx[test_idx]
    Rte = R[test_idx]

    # Scaler fijo con estadísticos de TRAIN (sin fuga del test)
    mean = Ztr_raw.mean(axis=0)
    std = Ztr_raw.std(axis=0)

    bandit = LinTSBandit(N_FEATURES, N_ARMS, v=0.2, lam=1.0)
    bandit.set_scaler(mean, std)

    # Entrenamiento vectorizado (info completa): A = lam*I + Z^T Z ; b[a] = Z^T r[:,a]
    Ztr = (Ztr_raw - mean) / np.where(std < 1e-8, 1.0, std)
    A = bandit.lam * np.eye(N_FEATURES) + Ztr.T @ Ztr
    for a in range(N_ARMS):
        bandit.A[a] = A
        bandit.b[a] = Ztr.T @ Rtr[:, a]
    bandit._Ainv = None
    print("Entrenado.")

    # ---- Backtest greedy en test (out-of-sample) ----
    picks = np.array([int(np.argmax(bandit.expected_scores(ctx[t]))) for t in test_idx])
    realized = Rte[np.arange(len(test_idx)), picks]           # reward realizado por la elección

    def stats(r):
        r = np.asarray(r, dtype=float)
        sharpe = r.mean() / r.std() if r.std() > 0 else 0.0
        return r.mean(), (r > 0).mean() * 100, r.sum(), sharpe

    print("\n===== BACKTEST out-of-sample (recompensa en ATR por decisión) =====")
    m, wr, tot, sh = stats(realized)
    print(f"  Bandit contextual : media={m:+.4f}  winrate={wr:.1f}%  suma={tot:+.1f}  sharpe={sh:.3f}")

    # baseline: mejor brazo fijo según TRAIN, aplicado en test
    best_fixed = int(np.argmax(Rtr.mean(axis=0)))
    m2, wr2, tot2, sh2 = stats(Rte[:, best_fixed])
    print(f"  Mejor brazo fijo  : media={m2:+.4f}  winrate={wr2:.1f}%  suma={tot2:+.1f}  sharpe={sh2:.3f}  (brazo={ARM_NAMES[best_fixed]})")

    # baseline: promedio de todos los brazos (elección aleatoria uniforme)
    m3, wr3, tot3, sh3 = stats(Rte.mean(axis=1))
    print(f"  Aleatorio (prom.) : media={m3:+.4f}  winrate={wr3:.1f}%  suma={tot3:+.1f}  sharpe={sh3:.3f}")

    # distribución de brazos elegidos
    uniq, cnt = np.unique(picks, return_counts=True)
    dist = {ARM_NAMES[u]: int(c) for u, c in zip(uniq, cnt)}
    print(f"  Brazos elegidos   : {dist}")

    # ---- Guardar estado entrenado ----
    out_path = os.path.join(DATA_DIR, "lints_state.json")
    bandit.save(out_path)
    print(f"\nEstado guardado en {out_path}")


if __name__ == "__main__":
    main()

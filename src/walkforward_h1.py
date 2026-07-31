"""
Walk-forward ONLINE en H1: el bandit corre barra a barra actualizándose como en
vivo (reward realista SL/TP+costos, maduración causal por cierre de trade), a lo
largo de 8.5 años y todos los regímenes. Compara adaptativo vs estrategias fijas.

Prueba la hipótesis: ¿adaptarse al régimen (incluida la caída de 2026) supera a
una regla fija con sesgo alcista? Solo LEE histórico.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure
from collections import defaultdict

from paths import load_config
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_master import classify, Params
from context_builder import build_context
from bandit_contextual import LinTSBandit
from bandit import ContextualBanditTS

cfg = load_config()
SYMBOL = cfg["symbol"]
FEATS = ["close", "volume", "rsi", "sma20", "returns", "volatility"]
SL_MULT = cfg["trading"].get("sl_atr_mult", 1.5)
TP_MULT = cfg["trading"].get("tp_atr_mult", 2.0)
N_BARS = 50000
MAXHOLD = 150
WARMUP = 3000
NF = len(FEATS) + 2 + 4 + 10 + 4


def sim(entry, d, sl_dist, tp_dist, high, low, close, t, cost):
    """Devuelve (pnl, bar_de_cierre)."""
    if d == 0:
        return 0.0, t + 1
    end = min(t + MAXHOLD, len(close) - 1)
    if d == 1:
        sl, tp = entry - sl_dist, entry + tp_dist
        for k in range(t + 1, end + 1):
            if low[k] <= sl:
                return -sl_dist - cost, k
            if high[k] >= tp:
                return tp_dist - cost, k
        return (close[end] - entry) - cost, end
    else:
        sl, tp = entry + sl_dist, entry - tp_dist
        for k in range(t + 1, end + 1):
            if high[k] >= sl:
                return -sl_dist - cost, k
            if low[k] <= tp:
                return tp_dist - cost, k
        return (entry - close[end]) - cost, end


def maxdd(eq):
    eq = np.asarray(eq); peak = np.maximum.accumulate(eq)
    return (eq - peak).min()


def main():
    np.random.seed(7)
    ensure()
    info = mt5.symbol_info(SYMBOL)
    cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"H1 walk-forward | cost={cost:.3f} | {n} barras "
          f"{df['time'].iloc[0].date()} -> {df['time'].iloc[-1].date()}")
    X = build_features(df, FEATS)
    df = compute_indicators(df)
    print("clasificando régimen...")
    reg = classify(df, Params())

    close = df["close"].values; high = df["high"].values
    low = df["low"].values; atr = df["atr"].values
    fam = reg["family"].values; sma = df["sma20"].values; year = df["time"].dt.year.values

    d_arm = {0: np.where(fam == "TREND_DOWN", -1, 1),
             1: np.where(close > sma, -1, 1),
             2: np.zeros(n, int),
             3: np.where(fam == "TREND_DOWN", -1, 1),
             4: (lambda v: (v.__setitem__(0, 1), v)[1])(np.where(close > np.roll(close, 1), 1, -1))}

    finite = np.isfinite(atr) & np.isfinite(X[FEATS].values).all(axis=1)

    print("simulando P&L SL/TP por brazo (cierre causal)...")
    R = np.full((n, 5), np.nan)
    CB = np.zeros((n, 5), int)   # bar de cierre
    for t in np.where(finite)[0]:
        if t >= n - 1:
            continue
        sl_d, tp_d = atr[t] * SL_MULT, atr[t] * TP_MULT
        for a in range(5):
            p, k = sim(close[t], int(d_arm[a][t]), sl_d, tp_d, high, low, close, t, cost)
            R[t, a] = p; CB[t, a] = k

    valid = finite & np.isfinite(R).all(axis=1)
    valid[n - 1:] = False

    # contexto
    ctx = np.full((n, NF), np.nan)
    for t in np.where(valid)[0]:
        try:
            ctx[t] = build_context(X.iloc[t], reg.iloc[t])
        except Exception:
            valid[t] = False

    # scaler causal desde el warmup
    wm = np.where(valid[:WARMUP])[0]
    mean = ctx[wm].mean(0); std = ctx[wm].std(0)

    lints = LinTSBandit(NF, 5, v=0.3, lam=1.0); lints.set_scaler(mean, std)
    simple = ContextualBanditTS(len(FEATS) + 2 + 4 + 10 + 4, 5)

    schedule = defaultdict(list)   # bar_cierre -> [(open_bar, arm)]
    eq = {k: [0.0] for k in ["lints", "simple", "trend", "vol", "random"]}
    picks_l = []
    print("corriendo walk-forward online...\n")

    for t in range(WARMUP, n):
        # maduración causal: actualizar con trades que cerraron en t
        for (ob, a) in schedule.get(t, []):
            lints.update(a, ctx[ob], R[ob, a])
            simple.update(a, ctx[ob], R[ob, a])

        if not valid[t]:
            continue
        al = lints.select_arm(ctx[t])
        asi = simple.select_arm(ctx[t])
        ar = np.random.randint(5)
        picks_l.append(al)

        eq["lints"].append(eq["lints"][-1] + R[t, al])
        eq["simple"].append(eq["simple"][-1] + R[t, asi])
        eq["trend"].append(eq["trend"][-1] + R[t, 0])
        eq["vol"].append(eq["vol"][-1] + R[t, 4])
        eq["random"].append(eq["random"][-1] + R[t, ar])

        # programar maduración (info completa: todos los brazos del bar t)
        for a in range(5):
            schedule[CB[t, a]].append((t, a))

    print(f"{'estrategia':<12}{'P&L final$':>12}{'media$':>10}{'maxDD$':>11}")
    for k in ["lints", "simple", "trend", "vol", "random"]:
        e = np.array(eq[k]); tot = e[-1]; m = np.diff(e).mean() if len(e) > 1 else 0
        print(f"{k:<12}{tot:>+12.1f}{m:>+10.4f}{maxdd(e):>+11.1f}")

    # P&L por año del adaptativo contextual (lints) vs trend fijo
    print(f"\n{'año':<6}{'lints$':>10}{'trend$':>10}{'oroRet%':>9}")
    tv = np.where(valid)[0]; tv = tv[tv >= WARMUP]
    # reconstruir contribuciones por año
    yl = defaultdict(float); yt = defaultdict(float)
    i = 0
    for t in range(WARMUP, n):
        if not valid[t]:
            continue
        y = year[t]
        yl[y] += R[t, picks_l[i]]; yt[y] += R[t, 0]; i += 1
    for y in sorted(yl):
        mask = (year == y) & valid
        idxs = np.where(mask)[0]
        gret = (close[idxs[-1]] / close[idxs[0]] - 1) * 100 if len(idxs) else 0
        print(f"{y:<6}{yl[y]:>+10.1f}{yt[y]:>+10.1f}{gret:>+9.1f}")

    from collections import Counter
    c = Counter(picks_l)
    names = ["trend", "mean", "flat", "momentum", "volatility"]
    print("\nbrazos elegidos (lints):", {names[k]: c[k] for k in sorted(c)})


if __name__ == "__main__":
    main()

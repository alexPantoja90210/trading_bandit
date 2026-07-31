"""
Walk-forward online con distintos factores de olvido (gamma) para el LinTS.
Prueba si una memoria corta permite al bandit voltear con el régimen (p. ej.
short en la caída de 2026) y mejorar el total. Calcula datos una sola vez.
Solo LEE histórico.
"""
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure
from collections import defaultdict, Counter

from paths import load_config
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_master import classify, Params
from context_builder import build_context
from bandit_contextual import LinTSBandit

cfg = load_config()
SYMBOL = cfg["symbol"]
FEATS = ["close", "volume", "rsi", "sma20", "returns", "volatility"]
SL_MULT = cfg["trading"].get("sl_atr_mult", 1.5)
TP_MULT = cfg["trading"].get("tp_atr_mult", 2.0)
N_BARS = 50000
MAXHOLD = 150
WARMUP = 3000
NF = len(FEATS) + 2 + 4 + 10 + 4
GAMMAS = [1.0, 0.999, 0.995, 0.99, 0.98]
ARM = ["trend", "mean", "flat", "momentum", "volatility"]


def sim(entry, d, sl, tp, high, low, close, t, cost):
    if d == 0:
        return 0.0, t + 1
    end = min(t + MAXHOLD, len(close) - 1)
    if d == 1:
        s, p = entry - sl, entry + tp
        for k in range(t + 1, end + 1):
            if low[k] <= s:
                return -sl - cost, k
            if high[k] >= p:
                return tp - cost, k
        return (close[end] - entry) - cost, end
    else:
        s, p = entry + sl, entry - tp
        for k in range(t + 1, end + 1):
            if high[k] >= s:
                return -sl - cost, k
            if low[k] <= p:
                return tp - cost, k
        return (entry - close[end]) - cost, end


def maxdd(eq):
    eq = np.asarray(eq); return (eq - np.maximum.accumulate(eq)).min()


def build():
    ensure()
    info = mt5.symbol_info(SYMBOL); cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"H1 | cost={cost:.3f} | {n} barras {df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()}")
    X = build_features(df, FEATS); df = compute_indicators(df)
    print("clasificando régimen (una vez)...")
    reg = classify(df, Params())
    close = df["close"].values; high = df["high"].values; low = df["low"].values
    atr = df["atr"].values; fam = reg["family"].values; sma = df["sma20"].values
    year = df["time"].dt.year.values
    vol = np.where(close > np.roll(close, 1), 1, -1); vol[0] = 1
    d_arm = {0: np.where(fam == "TREND_DOWN", -1, 1), 1: np.where(close > sma, -1, 1),
             2: np.zeros(n, int), 3: np.where(fam == "TREND_DOWN", -1, 1), 4: vol}
    finite = np.isfinite(atr) & np.isfinite(X[FEATS].values).all(axis=1)
    print("simulando P&L SL/TP por brazo...")
    R = np.full((n, 5), np.nan); CB = np.zeros((n, 5), int)
    for t in np.where(finite)[0]:
        if t >= n - 1:
            continue
        for a in range(5):
            p, k = sim(close[t], int(d_arm[a][t]), atr[t]*SL_MULT, atr[t]*TP_MULT, high, low, close, t, cost)
            R[t, a] = p; CB[t, a] = k
    valid = finite & np.isfinite(R).all(axis=1); valid[n-1:] = False
    ctx = np.full((n, NF), np.nan)
    for t in np.where(valid)[0]:
        try:
            ctx[t] = build_context(X.iloc[t], reg.iloc[t])
        except Exception:
            valid[t] = False
    wm = np.where(valid[:WARMUP])[0]
    return dict(n=n, R=R, CB=CB, ctx=ctx, valid=valid, year=year, close=close,
               mean=ctx[wm].mean(0), std=ctx[wm].std(0))


def run(dat, gamma):
    n, R, CB, ctx, valid, year = dat["n"], dat["R"], dat["CB"], dat["ctx"], dat["valid"], dat["year"]
    b = LinTSBandit(NF, 5, v=0.3, lam=1.0, gamma=gamma)
    b.set_scaler(dat["mean"], dat["std"])
    sched = defaultdict(list); eq = [0.0]; picks = []; y_pnl = defaultdict(float)
    for t in range(WARMUP, n):
        for (ob, a) in sched.get(t, []):
            b.update(a, ctx[ob], R[ob, a])
        if not valid[t]:
            continue
        a = b.select_arm(ctx[t]); picks.append(a)
        eq.append(eq[-1] + R[t, a]); y_pnl[year[t]] += R[t, a]
        for aa in range(5):
            sched[CB[t, aa]].append((t, aa))
    e = np.array(eq)
    return dict(tot=e[-1], dd=maxdd(e), y=y_pnl, picks=Counter(picks))


def main():
    dat = build()
    print("\ncorriendo walk-forward por gamma...\n")
    print(f"{'gamma':>7}{'P&L final':>11}{'maxDD':>10}{'2025':>9}{'2026':>9}  brazos")
    print("-" * 70)
    rows = []
    for g in GAMMAS:
        r = run(dat, g)
        rows.append((g, r))
        top = ", ".join(f"{ARM[k]}:{r['picks'][k]}" for k in sorted(r['picks'], key=lambda k: -r['picks'][k])[:3])
        print(f"{g:>7.3f}{r['tot']:>+11.1f}{r['dd']:>+10.1f}{r['y'].get(2025,0):>+9.1f}{r['y'].get(2026,0):>+9.1f}  {top}")
    print("-" * 70)
    # detalle por año del mejor gamma (por P&L total)
    best = max(rows, key=lambda x: x[1]['tot'])
    print(f"\nDetalle por año — mejor gamma={best[0]:.3f} (P&L {best[1]['tot']:+.0f}):")
    for y in sorted(best[1]['y']):
        print(f"  {y}: {best[1]['y'][y]:+.1f}")


if __name__ == "__main__":
    main()

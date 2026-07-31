"""
Variante fiel al bot vivo: aplica el filtro should_trade (régimen) sobre las
señales. Compara cada estrategia CON y SIN el filtro para ver si el gate de
régimen crea/mejora edge. H1, reward SL/TP + costos. Solo LEE histórico.
"""
import sys
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
from bandit import ContextualBanditTS
from policy import should_trade

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

cfg = load_config()
SYMBOL = cfg["symbol"]
FEATS = ["close", "volume", "rsi", "sma20", "returns", "volatility"]
SL_MULT = cfg["trading"].get("sl_atr_mult", 1.5)
TP_MULT = cfg["trading"].get("tp_atr_mult", 2.0)
N_BARS = 50000
MAXHOLD = 150
WARMUP = 3000
ARM = ["trend", "mean", "flat", "momentum", "volatility"]
NF = len(FEATS) + 2 + 4 + 10 + 4


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


def per_year(pairs):
    yr = defaultdict(float)
    for y, r in pairs:
        yr[y] += r
    return yr


def main():
    ensure()
    info = mt5.symbol_info(SYMBOL); cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"H1 | cost={cost:.3f} | {n} barras {df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()}")
    X = build_features(df, FEATS); df = compute_indicators(df)
    print("clasificando régimen...")
    reg = classify(df, Params())
    close = df["close"].values; high = df["high"].values; low = df["low"].values
    atr = df["atr"].values; fam = reg["family"].values; sma = df["sma20"].values
    year = df["time"].dt.year.values
    vol = np.where(close > np.roll(close, 1), 1, -1); vol[0] = 1
    d_arm = {0: np.where(fam == "TREND_DOWN", -1, 1), 1: np.where(close > sma, -1, 1),
             2: np.zeros(n, int), 3: np.where(fam == "TREND_DOWN", -1, 1), 4: vol}
    finite = np.isfinite(atr) & np.isfinite(X[FEATS].values).all(axis=1)

    print("simulando P&L SL/TP + gate de régimen...")
    R = np.full((n, 5), np.nan); CB = np.zeros((n, 5), int)
    gate = np.zeros((n, 5), bool)
    for t in np.where(finite)[0]:
        if t >= n - 1:
            continue
        rr = reg.iloc[t]
        for a in range(5):
            p, k = sim(close[t], int(d_arm[a][t]), atr[t]*SL_MULT, atr[t]*TP_MULT, high, low, close, t, cost)
            R[t, a] = p; CB[t, a] = k
            try:
                gate[t, a] = should_trade(rr, ARM[a])
            except Exception:
                gate[t, a] = False

    valid = finite & np.isfinite(R).all(axis=1); valid[n-1:] = False
    tv = np.where(valid)[0]

    # ---- estrategias fijas: sin gate vs con gate ----
    print(f"\n{'estrategia':<14}{'SIN gate ΣR':>13}{'CON gate ΣR':>13}{'trades s/g':>11}{'trades c/g':>11}")
    print("-" * 62)
    for a in [0, 1, 3, 4]:
        rr = R[tv, a]
        sr_all = rr.sum()
        m = gate[tv, a]
        sr_gate = R[tv, a][m].sum()
        print(f"{ARM[a]:<14}{sr_all:>+13.1f}{sr_gate:>+13.1f}{len(tv):>11}{int(m.sum()):>11}")

    # ---- bandit simple (config del bot vivo) online: sin gate vs con gate ----
    ctx = np.full((n, NF), np.nan)
    for t in tv:
        try:
            ctx[t] = build_context(X.iloc[t], reg.iloc[t])
        except Exception:
            pass

    def run_bandit(use_gate):
        np.random.seed(3)
        b = ContextualBanditTS(NF, 5)
        sched = defaultdict(list); pairs = []
        for t in range(WARMUP, n):
            for (ob, a) in sched.get(t, []):
                b.update(a, ctx[ob], R[ob, a])
            if not valid[t]:
                continue
            a = b.select_arm(ctx[t])
            if (not use_gate) or gate[t, a]:
                pairs.append((year[t], R[t, a]))
            for aa in range(5):
                sched[CB[t, aa]].append((t, aa))
        return pairs

    p_no = run_bandit(False); p_yes = run_bandit(True)
    print(f"\n{'BANDIT vivo':<14}{sum(r for _,r in p_no):>+13.1f}{sum(r for _,r in p_yes):>+13.1f}"
          f"{len(p_no):>11}{len(p_yes):>11}")

    print("\n=== por año: BANDIT sin gate vs con gate ===")
    yn = per_year(p_no); yy = per_year(p_yes)
    print(f"  {'año':<6}{'sin gate':>10}{'con gate':>10}")
    for y in sorted(set(list(yn) + list(yy))):
        print(f"  {y:<6}{yn.get(y,0):>+10.1f}{yy.get(y,0):>+10.1f}")


if __name__ == "__main__":
    main()

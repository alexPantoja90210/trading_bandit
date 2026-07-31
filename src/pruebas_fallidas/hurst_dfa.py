"""
Variante DFA (Detrended Fluctuation Analysis) del test de Hurst.

Igual que hurst_analysis.py pero con un estimador más robusto: la DFA quita la
tendencia local en cada escala antes de medir la fluctuación, así no confunde
"drift" con "persistencia". Repite los 3 tests (redundancia, significado,
RSI2+filtro) sobre los mismos activos. Solo LEE histórico.
"""
import os
import sys
# archivado en pruebas_fallidas/ → añadir src/ al path para importar sus módulos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from regime_master import build_features, Params
from rsi2_meanrev import rsi
from hurst_analysis import rsi2_backtest, FWD

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

WIN = 200                                  # ventana rodante (más larga para la DFA)
SCALES = np.array([4, 6, 8, 12, 16, 24, 32, 48])


def dfa_hurst(logp):
    """Exponente DFA (α≈Hurst) de una ventana de log-precios. Detrended lineal
    por caja, vectorizado. α<0.5 reversión, ≈0.5 aleatorio, >0.5 persistente."""
    x = np.diff(logp)
    if x.size < SCALES[-1] * 2:
        return np.nan
    y = np.cumsum(x - x.mean())
    N = y.size
    logn, logF = [], []
    for n in SCALES:
        nb = N // n
        if nb < 2:
            continue
        segs = y[:nb * n].reshape(nb, n).astype(float)
        t = np.arange(n, dtype=float)
        tm = t.mean(); tvar = ((t - tm) ** 2).sum()
        sm = segs.mean(axis=1, keepdims=True)
        slope = ((segs - sm) * (t - tm)).sum(axis=1) / tvar
        intercept = sm[:, 0] - slope * tm
        trend = intercept[:, None] + slope[:, None] * t[None, :]
        rms = np.sqrt(((segs - trend) ** 2).mean(axis=1))
        F = np.sqrt((rms ** 2).mean())
        if F > 0:
            logn.append(np.log(n)); logF.append(np.log(F))
    if len(logn) < 3:
        return np.nan
    return np.polyfit(logn, logF, 1)[0]


def rolling_dfa(close, win=WIN):
    logp = np.log(close)
    n = len(close)
    H = np.full(n, np.nan)
    for t in range(win, n):
        H[t] = dfa_hurst(logp[t - win:t])
    return H


def run(sym, tf, tf_name):
    ensure(); mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    r = mt5.copy_rates_from_pos(sym, tf, 0, 50000)
    if r is None or len(r) < 1500:
        print(f"### {sym}: insuficiente"); return
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    close = df["close"].values
    print(f"\n{'='*70}\n### {sym} · {tf_name} · {len(df)} barras "
          f"({df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()})  [DFA, win={WIN}]")

    feats = build_features(df, Params())
    er = feats["er"].values; r2f = feats["r2"].values
    H = rolling_dfa(close)

    mask = np.isfinite(H) & np.isfinite(er) & np.isfinite(r2f)
    cer = np.corrcoef(H[mask], er[mask])[0, 1]
    cr2 = np.corrcoef(H[mask], r2f[mask])[0, 1]
    print(f"\n[1] REDUNDANCIA  corr(DFA, ER)={cer:+.2f}   corr(DFA, R²)={cr2:+.2f}"
          f"   (media α={np.nanmean(H):.3f})")

    past = np.full(len(close), np.nan); fwd = np.full(len(close), np.nan)
    for t in range(FWD, len(close) - FWD):
        past[t] = (close[t] - close[t - FWD]) / close[t - FWD]
        fwd[t] = (close[t + FWD] - close[t]) / close[t]
    print("[2] SIGNIFICADO  corr(mov pasado, mov futuro) por cubo de α (DFA):")
    print("    (negativo=reversión · positivo=momentum · esperado: sube con α)")
    for lo, hi, lab in [(-9, 0.45, "α<0.45 (anti-persist)"),
                        (0.45, 0.55, "0.45-0.55 (aleatorio)"),
                        (0.55, 9, "α>0.55 (persistente)")]:
        m = mask & (H >= lo) & (H < hi) & np.isfinite(past) & np.isfinite(fwd)
        if m.sum() > 30:
            c = np.corrcoef(past[m], fwd[m])[0, 1]
            print(f"      {lab:<26} n={m.sum():>5}  corr={c:+.3f}")

    if tf == mt5.TIMEFRAME_D1:
        sma200 = pd.Series(close).rolling(200).mean().values
        sma5 = pd.Series(close).rolling(5).mean().values
        r2ind = rsi(close, 2)
        cost = (info.spread * info.point) / np.nanmean(close) if info else 0.0
        base, nb = rsi2_backtest(close, sma200, sma5, r2ind, cost)
        print(f"[3] RSI(2) baseline:            {base}")
        for thr in [0.5, 0.45]:
            filt, nf = rsi2_backtest(close, sma200, sma5, r2ind, cost, entry_mask=(H < thr))
            print(f"    RSI(2) + DFA<{thr}:          {filt}   ({nf}/{nb} trades)")


def main():
    ensure()
    run("XAUUSD", mt5.TIMEFRAME_H4, "H4")
    run("US500", mt5.TIMEFRAME_D1, "D1")
    run("NAS100", mt5.TIMEFRAME_D1, "D1")


if __name__ == "__main__":
    main()

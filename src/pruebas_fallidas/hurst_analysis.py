"""
¿El exponente de Hurst mejora la detección de régimen / brazo?

Metodología del proyecto: no asumir, PROBAR sobre datos reales y varios años.
El regime_master YA tiene ER (efficiency ratio) y R² — ambos miden persistencia,
igual que el Hurst. Este test responde 3 preguntas:

  1) REDUNDANCIA: ¿qué tan correlacionado está el Hurst con ER y R²?
     (si corr alta → no aporta info nueva).
  2) SIGNIFICADO: ¿el Hurst realmente separa mercado persistente (momentum)
     de anti-persistente (reversión)? Se mide la autocorrelación del retorno
     futuro dentro de cada cubo de Hurst. Si H<0.5 → reversión y H>0.5 →
     momentum de verdad, el Hurst tiene poder.
  3) APLICACIÓN: ¿filtrar el RSI(2) por Hurst<umbral mejora PF/DD?
     (RSI2 es reversión → debería preferir H<0.5).

Solo LEE histórico.
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

WIN = 120           # ventana del Hurst rodante
LAGS = np.arange(2, 21)
FWD = 5             # horizonte para el test de significado


def hurst_window(logp):
    """Hurst por función de estructura orden 2: std(x[t+lag]-x[t]) ~ lag^H."""
    tau = []
    for lag in LAGS:
        d = logp[lag:] - logp[:-lag]
        s = np.std(d)
        tau.append(s if s > 1e-12 else 1e-12)
    return np.polyfit(np.log(LAGS), np.log(tau), 1)[0]


def rolling_hurst(close, win=WIN):
    logp = np.log(close)
    n = len(close)
    H = np.full(n, np.nan)
    for t in range(win, n):
        H[t] = hurst_window(logp[t - win:t])
    return H


def rsi2_backtest(close, sma200, sma5, r2ind, cost, entry_mask=None):
    """RSI2 clásico; entry_mask opcional (filtro adicional booleano por barra)."""
    n = len(close); pos = None; trades = []
    for t in range(205, n):
        if not (np.isfinite(sma200[t]) and np.isfinite(sma5[t]) and np.isfinite(r2ind[t])):
            continue
        if pos is None:
            ok = close[t] > sma200[t] and r2ind[t] < 10.0
            if entry_mask is not None:
                ok = ok and bool(entry_mask[t])
            if ok:
                pos = {"entry": close[t], "bar": t}
        else:
            if close[t] > sma5[t] or r2ind[t] > 70.0 or (t - pos["bar"]) >= 10:
                ret = (close[t] - pos["entry"]) / pos["entry"] - cost
                trades.append(ret * 100); pos = None
    R = np.array(trades)
    if len(R) == 0:
        return "sin trades", 0
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; l = R[R < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    return (f"n={len(R):>4}  ret%={R.sum():>+7.1f}  PF={pf:.2f}  "
            f"wr={(R>0).mean()*100:>4.1f}%  DD%={dd:>+6.1f}"), len(R)


def run(sym, tf, tf_name):
    ensure(); mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    r = mt5.copy_rates_from_pos(sym, tf, 0, 50000)
    if r is None or len(r) < 1500:
        print(f"### {sym}: insuficiente"); return
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    close = df["close"].values
    print(f"\n{'='*70}\n### {sym} · {tf_name} · {len(df)} barras "
          f"({df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()})")

    # features del regime_master (ER y R²)
    feats = build_features(df, Params())
    er = feats["er"].values
    r2f = feats["r2"].values
    H = rolling_hurst(close)

    mask = np.isfinite(H) & np.isfinite(er) & np.isfinite(r2f)
    # ---- 1) REDUNDANCIA ----
    cer = np.corrcoef(H[mask], er[mask])[0, 1]
    cr2 = np.corrcoef(H[mask], r2f[mask])[0, 1]
    print(f"\n[1] REDUNDANCIA  corr(Hurst, ER)={cer:+.2f}   corr(Hurst, R²)={cr2:+.2f}"
          f"   (media H={np.nanmean(H):.3f})")

    # ---- 2) SIGNIFICADO: autocorr del retorno futuro por cubo de Hurst ----
    ret1 = np.zeros(len(close)); ret1[1:] = np.diff(close) / close[:-1]
    past = np.full(len(close), np.nan); fwd = np.full(len(close), np.nan)
    for t in range(FWD, len(close) - FWD):
        past[t] = (close[t] - close[t - FWD]) / close[t - FWD]
        fwd[t] = (close[t + FWD] - close[t]) / close[t]
    print("[2] SIGNIFICADO  corr(mov pasado, mov futuro) por cubo de Hurst:")
    print("    (negativo=reversión · positivo=momentum · esperado: sube con H)")
    edges = [(-9, 0.45, "H<0.45 (anti-persist)"), (0.45, 0.55, "0.45-0.55 (aleatorio)"),
             (0.55, 9, "H>0.55 (persistente)")]
    for lo, hi, lab in edges:
        m = mask & (H >= lo) & (H < hi) & np.isfinite(past) & np.isfinite(fwd)
        if m.sum() > 30:
            c = np.corrcoef(past[m], fwd[m])[0, 1]
            print(f"      {lab:<26} n={m.sum():>5}  corr={c:+.3f}")

    # ---- 3) APLICACIÓN: RSI2 con y sin filtro de Hurst (solo D1 índices) ----
    if tf == mt5.TIMEFRAME_D1:
        sma200 = pd.Series(close).rolling(200).mean().values
        sma5 = pd.Series(close).rolling(5).mean().values
        r2ind = rsi(close, 2)
        cost = (info.spread * info.point) / np.nanmean(close) if info else 0.0
        base, nb = rsi2_backtest(close, sma200, sma5, r2ind, cost)
        print(f"[3] RSI(2) baseline:            {base}")
        for thr in [0.5, 0.45]:
            fmask = H < thr
            filt, nf = rsi2_backtest(close, sma200, sma5, r2ind, cost, entry_mask=fmask)
            keep = f"{nf}/{nb} trades" if nb else "—"
            print(f"    RSI(2) + Hurst<{thr}:        {filt}   ({keep})")


def main():
    ensure()
    run("XAUUSD", mt5.TIMEFRAME_H4, "H4")
    run("US500", mt5.TIMEFRAME_D1, "D1")
    run("NAS100", mt5.TIMEFRAME_D1, "D1")


if __name__ == "__main__":
    main()

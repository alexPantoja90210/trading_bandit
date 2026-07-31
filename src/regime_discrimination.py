"""
Test de discriminación del regime_master: ¿los 10 regímenes separan de verdad
el comportamiento FUTURO del mercado? Clasifica todo el histórico y, por régimen,
mide el retorno futuro, el sesgo direccional, la volatilidad futura y si la
'jugada' del régimen (long en alcista, short en bajista) paga. Solo LEE.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from reward_engine import compute_indicators
from regime_master import classify, Params

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

N = 50000
H = 12   # horizonte futuro (= knnHz del clasificador)
NAMES = {0: "ALC_CALMA", 1: "CAOS", 2: "RANGO_OK", 3: "ALC_VOL", 4: "ALC_NORM",
         5: "BAJ_CALMA", 6: "BAJ_NORM", 7: "BAJ_VOL", 8: "RANGO_VOL", 9: "TRANSICION"}
# dirección esperada por régimen (+1 alcista, -1 bajista, 0 rango/caos)
EXP_DIR = {0: 1, 3: 1, 4: 1, 5: -1, 6: -1, 7: -1, 2: 0, 8: 0, 1: 0, 9: 0}


def run(sym, tf, tf_name):
    ensure(); mt5.symbol_select(sym, True)
    rates = mt5.copy_rates_from_pos(sym, tf, 0, N)
    if rates is None or len(rates) < 3000:
        print(f"### {sym}: histórico insuficiente"); return
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    df = compute_indicators(df)
    print(f"\n### {sym} · {tf_name} · {len(df)} barras ({df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()})  H={H}")
    print("clasificando régimen (puede tardar)...")
    reg = classify(df, Params())

    close = df["close"].values; high = df["high"].values; low = df["low"].values
    atr = df["atr"].values
    rid = reg["id"].values
    kedge = reg["knn_edge"].values
    n = len(df)

    # retorno futuro y rango futuro (en ATR)
    fwd = np.full(n, np.nan); frange = np.full(n, np.nan)
    for t in range(n - H):
        if atr[t] and atr[t] > 0 and np.isfinite(atr[t]):
            fwd[t] = (close[t + H] - close[t]) / atr[t]
            frange[t] = (high[t + 1:t + H + 1].max() - low[t + 1:t + H + 1].min()) / atr[t]

    print(f"  {'régimen':<12}{'n':>7}{'%tot':>6}{'ret_ATR':>9}{'|ret|':>8}{'jugada':>8}{'%acierto':>9}{'rangoFut':>9}{'knn_edge':>9}")
    print("  " + "-" * 79)
    total = np.isfinite(fwd).sum()
    rows = []
    for r in range(10):
        m = (rid == r) & np.isfinite(fwd)
        cnt = int(m.sum())
        if cnt < 20:
            continue
        f = fwd[m]
        ed = EXP_DIR[r]
        play = ed * f            # jugada del régimen (0 si rango/caos)
        # % acierto = fracción que fue en la dirección esperada (para trend regs)
        acc = (np.sign(f) == ed).mean() * 100 if ed != 0 else np.nan
        rows.append((r, cnt))
        print(f"  {NAMES[r]:<12}{cnt:>7}{cnt/total*100:>6.1f}{f.mean():>+9.3f}{np.abs(f).mean():>8.3f}"
              f"{(play.mean() if ed != 0 else 0):>+8.3f}{acc if ed != 0 else float('nan'):>9.1f}"
              f"{np.nanmean(frange[m]):>9.2f}{np.nanmean(kedge[m]):>+9.3f}")

    # resumen: ¿los trend regs tienen jugada>0 y los rangos ret≈0?
    def grp(ids):
        m = np.isin(rid, ids) & np.isfinite(fwd)
        return fwd[m], m.sum()
    fb, _ = grp([0, 3, 4]); fbear, _ = grp([5, 6, 7]); frg, _ = grp([2, 8]); fch, _ = grp([1, 9])
    print("  " + "-" * 79)
    print(f"  ALCISTA: ret {fb.mean():+.3f} ATR (jugada long {fb.mean():+.3f}) | "
          f"BAJISTA: ret {fbear.mean():+.3f} (jugada short {-fbear.mean():+.3f})")
    print(f"  RANGO: ret {frg.mean():+.3f} ATR (debe ≈0) | CAOS/TRANS: ret {fch.mean():+.3f}, |ret| {np.abs(fch).mean():.3f}")


def main():
    ensure()
    run("XAUUSD", mt5.TIMEFRAME_H4, "H4")
    run("US500", mt5.TIMEFRAME_D1, "D1")


if __name__ == "__main__":
    main()

"""
cross_asset.py — busca ALPHA intermarket: ¿el retorno de HOY de un activo predice
el retorno de MAÑANA de otro? (lead-lag). La correlación CONTEMPORÁNEA no es operable
(se mueven juntos en tiempo real); la LEAD-LAG sí (A se mueve, B lo sigue después).

Universo: predictores (USDX dólar, VIX miedo, CN50 China) + nuestros activos.
D1, retornos alineados por fecha. Reporta:
  1. lead-lag: corr(A[t], B[t+1]) con t-stat → señales operables.
  2. contemporánea (contexto de riesgo/estructura).
Solo LEE histórico.
"""
import sys
from itertools import product

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UNIVERSE = ["USDX", "VIX", "CN50", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD",
            "US500", "NAS100", "GER40", "EURUSD", "WTOIL-PERP"]
N_BARS = 3000


def load_returns(sym):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, N_BARS)
    if r is None or len(r) < 300:
        return None
    df = pd.DataFrame(r); df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
    ret = df["close"].pct_change()
    return pd.Series(ret.values, index=df["date"], name=sym)


def tstat(c, n):
    if abs(c) >= 1 or n < 5:
        return 0.0
    return c * np.sqrt((n - 2) / (1 - c * c))


def main():
    ensure()
    series = {}
    for s in UNIVERSE:
        r = load_returns(s)
        if r is not None:
            series[s] = r
    R = pd.DataFrame(series).dropna(how="all")
    R = R.loc[:, R.notna().sum() > 500]          # solo activos con suficiente historia común
    syms = list(R.columns)
    print(f"Activos con data: {syms}")
    print(f"Días alineados: {len(R.dropna())} (dropna full)")

    # matriz LEAD-LAG: corr(A[t], B[t+1]) — A predice a B al día siguiente
    ll = []
    for a, b in product(syms, syms):
        if a == b:
            continue
        d = pd.concat([R[a].shift(1), R[b]], axis=1).dropna()   # A[t] vs B[t+1]
        if len(d) < 300:
            continue
        c = d.iloc[:, 0].corr(d.iloc[:, 1])
        if np.isfinite(c):
            ll.append((a, b, c, tstat(c, len(d)), len(d)))
    ll.sort(key=lambda x: -abs(x[2]))

    print("\n[1] LEAD-LAG operable — A(hoy) → B(mañana), top por |corr| (t>2 = significativo):")
    print(f"    {'A predice→B':<22}{'corr':>8}{'t-stat':>8}{'n':>7}")
    for a, b, c, t, n in ll[:15]:
        flag = "  <-- señal" if abs(t) > 2 and abs(c) > 0.04 else ""
        print(f"    {a+'→'+b:<22}{c:>+8.3f}{t:>+8.1f}{n:>7}{flag}")

    # contemporánea (contexto, NO operable)
    print("\n[2] Correlación CONTEMPORÁNEA (contexto de riesgo, no operable):")
    C = R.corr()
    pairs = [("USDX", "XAUUSD"), ("USDX", "EURUSD"), ("VIX", "US500"),
             ("VIX", "NAS100"), ("XAUUSD", "XAGUSD"), ("BTCUSD", "ETHUSD"),
             ("US500", "GER40"), ("WTOIL-PERP", "US500")]
    for a, b in pairs:
        if a in C.columns and b in C.columns:
            print(f"    {a:>10} ~ {b:<12} corr={C.loc[a,b]:+.2f}")


if __name__ == "__main__":
    main()

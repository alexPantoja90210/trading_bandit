"""
vix_term_structure.py — EDGE PROPIO DE FUTUROS (term structure del VIX), hecho BIEN.
Data externa (yfinance): VIX, VIX3M (curva), VIXY (short-vol tradeable), S&P.

Señal = VIX/VIX3M. <1 = CONTANGO (curva al alza, calma) → carry de vol-corto favorable
(VIXY decae). >1 = BACKWARDATION (estrés) → evitar short-vol.

Se compara: (A) short-vol NAIVE (siempre corto VIXY) vs (B) short-vol TIMED por la curva
(corto solo en contango). Foco EXPLÍCITO en el TAIL: peor día, feb-2018 (Volmageddon),
mar-2020 (COVID). El punto: ¿la curva cosecha el carry SIN reventar en el tail? Costos incluidos.
Solo LEE. Cachea la data en data/futures/.
"""
import os
import sys
import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = os.path.join(os.path.dirname(__file__), "data", "futures")
COST_DAY = 0.0003   # ~3 bps/día de costo al mantener corto VIXY (borrow+spread), estresable


def load():
    os.makedirs(CACHE, exist_ok=True)
    import yfinance as yf
    out = {}
    for name, tk, col in [("VIX", "^VIX", "Close"), ("VIX3M", "^VIX3M", "Close"),
                          ("VIXY", "VIXY", "Close"), ("SP500", "^GSPC", "Close")]:
        fp = os.path.join(CACHE, f"{name}.csv")
        d = yf.download(tk, period="max", interval="1d", progress=False, auto_adjust=True)
        s = d[col] if col in d.columns else d.iloc[:, 0]
        s = pd.Series(np.asarray(s).ravel(), index=pd.to_datetime(d.index)).dropna()
        s.to_csv(fp)
        out[name] = s
    return out


def stats(r, cost_mult=1.0):
    r = r.dropna()
    if len(r) < 50:
        return None
    r = r.copy()
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    ann = (1 + r.mean())**252 - 1
    sh = r.mean()/r.std()*np.sqrt(252) if r.std() > 0 else 0
    return dict(ann=ann*100, sharpe=sh, maxDD=dd*100, worst_day=r.min()*100,
                n=len(r), final=eq.iloc[-1])


def window(r, y0, m0, y1, m1):
    sub = r[(r.index >= f"{y0}-{m0:02d}-01") & (r.index <= f"{y1}-{m1:02d}-28")]
    return (1 + sub).prod() - 1 if len(sub) else np.nan


def main():
    d = load()
    df = pd.DataFrame(d).dropna()
    df["TS"] = df["VIX"] / df["VIX3M"]                       # <1 contango, >1 backwardation
    df["vixy_ret"] = df["VIXY"].pct_change()
    df["short_vol"] = -df["vixy_ret"]                        # retorno de estar CORTO VIXY
    df = df.dropna()
    print(f"Rango tradeable (VIXY): {df.index[0].date()} -> {df.index[-1].date()}  ({len(df)} días)")
    print(f"% del tiempo en CONTANGO (TS<1): {(df['TS'] < 1).mean()*100:.0f}%\n")

    sig = (df["TS"].shift(1) < 1).astype(float)              # ayer en contango → hoy corto (sin lookahead)
    naive = df["short_vol"] - COST_DAY                       # siempre corto
    timed = df["short_vol"]*sig - COST_DAY*sig               # corto solo en contango

    print(f"{'Estrategia':<26}{'annual%':>9}{'Sharpe':>8}{'maxDD%':>9}{'peorDía%':>10}{'x inicial':>10}")
    print("-"*72)
    for name, r in [("A) Short-vol SIEMPRE", naive), ("B) Short-vol TIMED (curva)", timed),
                    ("  Buy&hold VIXY (largo)", df["vixy_ret"])]:
        s = stats(r)
        print(f"{name:<26}{s['ann']:>+9.1f}{s['sharpe']:>+8.2f}{s['maxDD']:>+9.1f}{s['worst_day']:>+10.1f}{s['final']:>10.2f}")

    print("\n=== FOCO EN EL TAIL (retorno en los crashes de vol) ===")
    for lbl, (y0, m0, y1, m1) in [("Volmageddon feb-2018", (2018, 2, 2018, 2)),
                                    ("COVID mar-2020", (2020, 3, 2020, 3)),
                                    ("ago-2024 (yen carry)", (2024, 8, 2024, 8))]:
        a = window(naive, y0, m0, y1, m1); b = window(timed, y0, m0, y1, m1)
        print(f"  {lbl:22}: SIEMPRE {a*100:+6.1f}%   TIMED {b*100:+6.1f}%")

    print("\n=== robustez por año (Sharpe) — B) TIMED ===")
    for y, g in timed.groupby(timed.index.year):
        s = stats(g)
        if s:
            print(f"  {y}: Sharpe {s['sharpe']:+.2f}  ret {s['ann']:+.0f}%  peorDía {s['worst_day']:+.1f}%")

    # costos x3
    n3 = df["short_vol"]*sig - COST_DAY*3*sig
    s3 = stats(n3)
    print(f"\nTIMED con costo x3 (9bps/día): Sharpe {s3['sharpe']:+.2f}  annual {s3['ann']:+.1f}%")


if __name__ == "__main__":
    main()

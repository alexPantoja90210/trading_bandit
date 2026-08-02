"""
xauusd_ob_zones.py — ¿en qué ZONA de retroceso es más eficiente el OB para MichaelFX?

Tras un HH (impulso alcista que rompe el máx previo) o un LL (impulso bajista), el precio
retrocede. Para cada nivel de entrada Z (retroceso, estilo Fib) mide:
  - fill%  : cuántos impulsos retroceden al menos a Z (una LIMIT en Z se llena).
  - cont%  : de los que se llenaron, cuántos CONTINÚAN (alcanzan el máx/mín previo = objetivo)
             antes de invalidar (romper el origen del impulso = 100% del retroceso).
  - R:R    : entrando en Z, stop en el origen (100%), objetivo el extremo (0%) → Z/(1-Z).
  - exp(R) : cont%·R:R − (1−cont%)·1  → expectativa en R. La zona con mayor exp = OB más eficiente.
Por TF y dirección. Descriptivo (caracteriza el comportamiento), no un backtest en vivo. Solo LEE.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TFS = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}
ZONES = [0.382, 0.5, 0.618, 0.705, 0.79]
ZLBL = {0.382: "38.2%", 0.5: "50%", 0.618: "61.8%", 0.705: "70.5%", 0.79: "79%"}


def load(sym, tf, n=5000):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 200:
        return None
    return pd.DataFrame(r)


def pivots(h, l, k=3):
    piv = []
    for i in range(k, len(h)-k):
        if h[i] == max(h[i-k:i+k+1]):
            piv.append([i, h[i], "H"])
        elif l[i] == min(l[i-k:i+k+1]):
            piv.append([i, l[i], "L"])
    clean = []
    for p in piv:
        if clean and clean[-1][2] == p[2]:
            if (p[2] == "H" and p[1] > clean[-1][1]) or (p[2] == "L" and p[1] < clean[-1][1]):
                clean[-1] = p
        else:
            clean.append(p)
    return clean


def impulses(df, bullish=True, k=3):
    h, l = df["high"].values, df["low"].values
    piv = pivots(h, l, k)
    res = []                       # (max_retr, cont)
    prevH = prevL = None
    for j in range(1, len(piv)):
        a, b = piv[j-1], piv[j]
        if bullish and a[2] == "L" and b[2] == "H":
            if prevH is not None and b[1] <= prevH:      # exigir HH (rompe máx previo)
                prevH = max(prevH, b[1]); prevL = a[1]; continue
            low0, high1 = a[1], b[1]; rng = high1 - low0
            if rng > 0:
                mr = 0.0; cont = None
                for t in range(b[0]+1, len(h)):
                    mr = max(mr, (high1 - l[t]) / rng)
                    if h[t] >= high1: cont = True; break
                    if l[t] <= low0: cont = False; break
                if cont is not None:
                    res.append((mr, cont))
            prevH = b[1]; prevL = a[1]
        elif (not bullish) and a[2] == "H" and b[2] == "L":
            if prevL is not None and b[1] >= prevL:       # exigir LL
                prevL = min(prevL, b[1]); prevH = a[1]; continue
            high0, low1 = a[1], b[1]; rng = high0 - low1
            if rng > 0:
                mr = 0.0; cont = None
                for t in range(b[0]+1, len(h)):
                    mr = max(mr, (h[t] - low1) / rng)
                    if l[t] <= low1: cont = True; break
                    if h[t] >= high0: cont = False; break
                if cont is not None:
                    res.append((mr, cont))
            prevL = b[1]; prevH = a[1]
        else:
            if b[2] == "H": prevH = b[1]
            if b[2] == "L": prevL = b[1]
    return res


def report(res, title):
    if len(res) < 20:
        print(f"  {title}: muestra chica (n={len(res)})"); return
    mr = np.array([r[0] for r in res]); ct = np.array([r[1] for r in res], float)
    print(f"  {title}  (n={len(res)} impulsos · retroceso mediano {np.median(mr)*100:.0f}%)")
    print(f"    {'zona OB':>9}{'fill%':>8}{'cont%':>8}{'R:R':>7}{'exp(R)':>9}")
    best = None
    for Z in ZONES:
        fill = mr >= Z
        if fill.sum() < 8:
            continue
        cont = ct[fill].mean()
        rr = Z/(1-Z)
        exp = cont*rr - (1-cont)*1
        star = ""
        if best is None or exp > best[1]:
            best = (Z, exp)
        print(f"    {ZLBL[Z]:>9}{fill.mean()*100:>7.0f}%{cont*100:>7.0f}%{rr:>7.2f}{exp:>+9.2f}")
    if best:
        print(f"    → zona más eficiente: OB en ~{ZLBL[best[0]]} (exp {best[1]:+.2f} R)")


def main():
    ensure()
    print("=== XAUUSD — zona de OB más eficiente por retroceso, tras HH / LL ===")
    for tfn in ["M5", "M15", "H1", "H4"]:
        df = load("XAUUSD", TFS[tfn])
        if df is None:
            print(f"\n{tfn}: sin data"); continue
        print(f"\n### {tfn}")
        report(impulses(df, bullish=True), "Tras HH (compras en retroceso)")
        report(impulses(df, bullish=False), "Tras LL (ventas en retroceso)")
    # contraste EURUSD en M15
    print("\n### Contraste EURUSD M15")
    df = load("EURUSD", TFS["M15"])
    if df is not None:
        report(impulses(df, bullish=True), "Tras HH")
        report(impulses(df, bullish=False), "Tras LL")


if __name__ == "__main__":
    main()

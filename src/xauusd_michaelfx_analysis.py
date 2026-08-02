"""
xauusd_michaelfx_analysis.py — analiza si XAUUSD requiere ajustes en MichaelFX:
  [1] Perfil de VOLATILIDAD por TF (ATR en $ y %) vs EURUSD/GBPUSD → ¿escalar SL/targets?
  [2] SPREAD vs rango por TF (cuánto del rango se come el costo) → ¿viable bajar a 1M/5M?
  [3] Profundidad de RETROCESO tras un impulso → ¿el precio retrocede profundo (limits del 80%
      se llenan) o corre (mejor STOP del 40%)?
  [4] Régimen reciente (ATR mensual) → ¿está en volatilidad alta?
Solo LEE. Data del bróker (fin de semana = último histórico).
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

TFS = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
       "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}


def load(sym, tf, n=3000):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 50:
        return None
    return pd.DataFrame(r)


def atr(df, n=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
    return pd.Series(tr).rolling(n).mean().iloc[-1], tr


def swings(df, k=3):
    """pivotes alternados → legs y su retroceso siguiente (% del leg)."""
    h, l = df["high"].values, df["low"].values
    piv = []   # (idx, price, type)
    for i in range(k, len(h)-k):
        if h[i] == max(h[i-k:i+k+1]):
            piv.append((i, h[i], "H"))
        elif l[i] == min(l[i-k:i+k+1]):
            piv.append((i, l[i], "L"))
    # limpiar a alternancia H/L
    clean = []
    for p in piv:
        if clean and clean[-1][2] == p[2]:
            if (p[2] == "H" and p[1] > clean[-1][1]) or (p[2] == "L" and p[1] < clean[-1][1]):
                clean[-1] = p
        else:
            clean.append(p)
    retr = []
    for a, b, cc in zip(clean, clean[1:], clean[2:]):
        leg = abs(b[1]-a[1])
        pull = abs(b[1]-cc[1])
        if leg > 0:
            retr.append(min(pull/leg, 2.0))
    return np.array(retr)


def main():
    ensure()
    print("=== [1] VOLATILIDAD por TF — ATR14 en precio y en % ===")
    print(f"{'sym':8}{'TF':4}{'ATR($/pip)':>12}{'ATR %':>9}{'rango medio %':>14}")
    vol = {}
    for sym in ["XAUUSD", "EURUSD", "GBPUSD"]:
        info = mt5.symbol_info(sym)
        for tfn in ["M1", "M5", "M15", "H1", "H4"]:
            df = load(sym, TFS[tfn])
            if df is None:
                print(f"{sym:8}{tfn:4}   sin data"); continue
            a, tr = atr(df)
            px = df["close"].iloc[-1]
            rng_pct = (tr[-500:].mean())/px*100 if len(tr) > 10 else np.nan
            vol[(sym, tfn)] = (a, a/px*100)
            print(f"{sym:8}{tfn:4}{a:>12.4f}{a/px*100:>8.3f}%{rng_pct:>13.3f}%")
        print()

    print("=== [2] SPREAD vs rango (¿viable bajar de TF?) — spread / ATR de la barra ===")
    print(f"{'sym':8}{'spread':>9}{'punto':>8}   spread/ATR por TF (cuanto se come el costo)")
    for sym in ["XAUUSD", "EURUSD", "GBPUSD"]:
        info = mt5.symbol_info(sym)
        df15 = load(sym, TFS["M15"])
        spr_pts = float(np.median(df15["spread"].values[-500:])) if df15 is not None else info.spread
        spr_price = spr_pts * info.point
        row = f"{sym:8}{spr_pts:>9.0f}{info.point:>8.4f}   "
        for tfn in ["M1", "M5", "M15", "H1"]:
            a = vol.get((sym, tfn), (np.nan,))[0]
            row += f"{tfn}:{spr_price/a*100:>5.1f}%  " if a and np.isfinite(a) else f"{tfn}: n/a  "
        print(row)
    print("  (>25% del ATR = el costo domina; <10% = sano)")

    print("\n=== [3] PROFUNDIDAD DE RETROCESO XAUUSD (¿limits 80% se llenan o el precio corre?) ===")
    for tfn in ["M5", "M15", "H1"]:
        df = load(sym, TFS[tfn]) if False else load("XAUUSD", TFS[tfn])
        r = swings(df)
        if len(r) < 20:
            print(f"  {tfn}: muestra chica"); continue
        p50 = np.median(r)*100
        deep80 = (r >= 0.8).mean()*100
        mid40 = (r >= 0.4).mean()*100
        print(f"  {tfn}: retroceso mediano={p50:.0f}% del leg · "
              f"llega al 40%: {mid40:.0f}% de las veces · llega al 80%: {deep80:.0f}%  (n={len(r)})")
    print("  → si 'llega al 80%' es BAJO, los limits del 80% se llenan poco (mejor STOP del 40%).")

    print("\n=== [4] RÉGIMEN reciente XAUUSD — ATR% D1 por mes (¿vol alta ahora?) ===")
    d = load("XAUUSD", TFS["D1"], 400)
    d["dt"] = pd.to_datetime(d["time"], unit="s")
    h, l, c = d["high"].values, d["low"].values, d["close"].values
    tr = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
    d = d.iloc[1:].copy(); d["trpct"] = tr/c[1:]*100
    d["ym"] = d["dt"].dt.to_period("M")
    g = d.groupby("ym")["trpct"].mean().tail(8)
    for ym, v in g.items():
        bar = "#" * int(v*8)
        print(f"  {ym}  ATR%={v:.2f}  {bar}")


if __name__ == "__main__":
    main()

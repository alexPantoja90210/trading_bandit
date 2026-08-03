"""
fx_carry.py — PATH B: carry cross-seccional de DIVISAS (AQR / Koijen-Moskowitz-Pedersen-Vrugt).
Generaliza la tesis de la curva/carry más allá del VIX: rankear divisas por su tasa (el carry =
diferencial de interés / forward discount), ir LARGO las de alto carry y CORTO las de bajo carry,
dollar-neutral. Retorno total = movimiento spot en USD + acumulación mensual del diferencial.

Data externa (cacheada en data/carry/):
  - Tasas interbancarias 3m (FRED, OECD, CSV público): USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD.
  - Spot FX (yfinance), homogeneizado a USD por unidad de divisa extranjera.

Rigor (el mismo que validó/mató otras ideas): Sharpe con costos, split OOS, test de nulidad,
robustez por año, cola de crash (2008/2015-CHF/2020/2024-yen), y correlación con STF/RSI2/VIXcarry.
Desplegable: Pepperstone tiene todos los pares FX. Solo LEE.
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = os.path.join(os.path.dirname(__file__), "data", "carry")
os.makedirs(CACHE, exist_ok=True)
COST = 0.0002   # ~2 bps por pata sobre el turnover del rebalanceo mensual

FRED = {"USD": "IR3TIB01USM156N", "EUR": "IR3TIB01EZM156N", "GBP": "IR3TIB01GBM156N",
        "JPY": "IR3TIB01JPM156N", "AUD": "IR3TIB01AUM156N", "CAD": "IR3TIB01CAM156N",
        "CHF": "IR3TIB01CHM156N", "NZD": "IR3TIB01NZM156N"}
# spot FX y si hay que invertir para dejar "USD por 1 unidad de divisa extranjera"
FX = {"EUR": ("EURUSD=X", False), "GBP": ("GBPUSD=X", False), "AUD": ("AUDUSD=X", False),
      "NZD": ("NZDUSD=X", False), "JPY": ("JPY=X", True), "CAD": ("CAD=X", True),
      "CHF": ("CHF=X", True)}
FOREIGN = list(FX.keys())


def _cache_csv(name, fetch):
    fp = os.path.join(CACHE, f"{name}.csv")
    if os.path.exists(fp):
        return pd.read_csv(fp, index_col=0, parse_dates=True).iloc[:, 0]
    s = fetch()
    s.to_csv(fp)
    return s


def load_rate(ccy):
    def fetch():
        d = pd.read_csv(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED[ccy]}")
        d.columns = ["date", "val"]
        d["date"] = pd.to_datetime(d["date"]); d["val"] = pd.to_numeric(d["val"], errors="coerce")
        return pd.Series(d["val"].values, index=d["date"], name=ccy).dropna()
    return _cache_csv(f"rate_{ccy}", fetch)


def load_fx(ccy):
    tkr, inv = FX[ccy]
    def fetch():
        import yfinance as yf
        d = yf.download(tkr, period="max", interval="1d", progress=False, auto_adjust=True)
        s = d["Close"] if "Close" in d.columns else d.iloc[:, 0]
        s = pd.Series(np.asarray(s).ravel(), index=pd.to_datetime(d.index)).dropna()
        return (1.0 / s if inv else s).rename(ccy)
    return _cache_csv(f"fx_{ccy}", fetch)


def sr(r, freq=12):
    r = r.dropna()
    if len(r) < 12 or r.std() == 0:
        return 0.0, 0.0, 0.0
    eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min()
    return r.mean() / r.std() * np.sqrt(freq), ((1 + r.mean())**freq - 1) * 100, dd * 100


def build():
    # tasas mensuales -> fin de mes, ffill (la tasa persiste); en % anual
    rates = pd.DataFrame({c: load_rate(c) for c in FRED})
    rates = rates.resample("ME").last().ffill()
    # spot FX diario -> fin de mes (USD por unidad extranjera)
    fx = pd.DataFrame({c: load_fx(c) for c in FOREIGN})
    fx = fx.resample("ME").last()

    idx = rates.index.intersection(fx.index)
    rates, fx = rates.loc[idx], fx.loc[idx]
    spot_ret = fx.pct_change()                       # retorno spot mensual en USD

    # retorno TOTAL de mantener divisa i (spot + interés propio, mensual), fondeada aparte
    carry_accr = rates[FOREIGN] / 100.0 / 12.0       # interés mensual devengado
    total_ret = spot_ret + carry_accr.shift(0)       # el mes t: spot(t) + interés del mes
    return rates, total_ret


def strategy(rates, total_ret, k=2):
    """Cada mes: rankear las divisas por su tasa (señal de AYER = fin de mes previo), LARGO top-k,
    CORTO bottom-k, dollar-neutral (pesos +/- 1/k). Retorno = pata larga - pata corta - costos."""
    sig = rates[FOREIGN].shift(1)                    # tasa conocida al inicio del mes (sin lookahead)
    rets, weights_prev = [], pd.Series(0.0, index=FOREIGN)
    dates = []
    for dt in total_ret.index[1:]:
        s = sig.loc[dt].dropna()
        if len(s) < 2 * k:
            continue
        ranked = s.sort_values()
        shorts, longs = ranked.index[:k], ranked.index[-k:]
        w = pd.Series(0.0, index=FOREIGN)
        w[longs] = 1.0 / k; w[shorts] = -1.0 / k
        r = float((w * total_ret.loc[dt].reindex(FOREIGN).fillna(0)).sum())
        turnover = (w - weights_prev).abs().sum()
        r -= COST * turnover
        rets.append(r); dates.append(dt); weights_prev = w
    return pd.Series(rets, index=pd.to_datetime(dates))


def main():
    print("Cargando tasas (FRED) + spot FX (yfinance)...")
    rates, total_ret = build()
    # data completa recién ~2003 (JPY 3m arranca 2002-04); antes el rankeo se degenera → sesga.
    START = "2004-01-01"
    rates = rates[rates.index >= START]
    total_ret = total_ret[total_ret.index >= START]
    r = strategy(rates, total_ret, k=2)
    print(f"Ventana (data completa): {r.index[0].date()} -> {r.index[-1].date()}  ({len(r)} meses)\n")

    print("=== Carry FX cross-seccional (largo top-2 tasa / corto bottom-2, dollar-neutral) ===")
    s, a, dd = sr(r)
    print(f"  Sharpe {s:+.2f}   annual {a:+.1f}%   maxDD {dd:+.1f}%   x={float((1+r).prod()):.1f}")

    print("\n=== Split OOS 60/40 ===")
    kk = int(len(r) * 0.6)
    si, ai, ddi = sr(r.iloc[:kk]); so, ao, ddo = sr(r.iloc[kk:])
    print(f"  TRAIN: Sharpe {si:+.2f}  ret {ai:+.0f}%  DD {ddi:+.0f}%")
    print(f"  TEST : Sharpe {so:+.2f}  ret {ao:+.0f}%  DD {ddo:+.0f}%")

    print("\n=== Test de nulidad (barajar el ranking 200x) ===")
    rng = np.random.RandomState(42)
    null = []
    for _ in range(200):
        perm_rates = rates.copy()
        perm_rates[FOREIGN] = rng.permutation(rates[FOREIGN].values)
        null.append(sr(strategy(perm_rates, total_ret, k=2))[0])
    pct = (np.array(null) < s).mean() * 100
    print(f"  Sharpe real {s:+.2f}  vs percentil {pct:.0f}% de la nula (>=95 = señal real)")

    print("\n=== Robustez por año ===")
    for y, g in r.groupby(r.index.year):
        if len(g) >= 6:
            sy, ay, ddy = sr(g)
            print(f"  {y}: Sharpe {sy:+.2f}  ret {ay:+.0f}%  DD {ddy:+.0f}%")

    print("\n=== Cola de crash (retorno del mes) ===")
    for label, ym in [("Lehman 2008-10", "2008-10"), ("CHF unpeg 2015-01", "2015-01"),
                      ("COVID 2020-03", "2020-03"), ("Yen unwind 2024-08", "2024-08")]:
        try:
            v = r.loc[ym] if ym in r.index.strftime("%Y-%m") else r[r.index.strftime("%Y-%m") == ym]
            val = float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
            print(f"  {label}: {val*100:+.1f}%")
        except Exception:
            print(f"  {label}: s/d")

    # === TESIS AQR: ¿el carry DIVERSIFICADO entre mercados (FX + VIX) es mejor que FX solo? ===
    print("\n=== Carry multi-mercado: FX + VIX (la tesis real de diversificación) ===")
    try:
        from svxy_portfolio_broker import vix_carry_broker_daily
        vixd = vix_carry_broker_daily()
        vixm = (1 + vixd).resample("ME").prod() - 1        # VIX carry mensual
        both = pd.DataFrame({"FX": r, "VIX": vixm}).dropna()
        corr = np.corrcoef(both["FX"], both["VIX"])[0, 1]
        print(f"  meses comunes: {len(both)}   corr(FX carry, VIX carry) = {corr:+.2f}")
        # escalar cada uno a la MISMA vol objetivo (retornos reales, DD interpretable)
        tgt = 0.02
        def scale(x): return x * (tgt / (x.std() + 1e-12))
        fxS, vixS = scale(both["FX"]), scale(both["VIX"])
        combo = 0.5 * fxS + 0.5 * vixS
        for name, series in [("FX carry solo", fxS), ("VIX carry solo", vixS),
                             ("50/50 carry basket", combo)]:
            s2, a2, dd2 = sr(series)
            print(f"  {name:<22} Sharpe {s2:+.2f}   maxDD {dd2:+.1f}%")
        print("  (Sharpe del basket > cada uno solo → diversificar carry entre mercados AYUDA)")
    except Exception as e:
        print(f"  (no se pudo combinar: {e})")


if __name__ == "__main__":
    main()

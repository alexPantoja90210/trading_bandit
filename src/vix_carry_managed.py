"""
vix_carry_managed.py — DOMAR el carry de vol-corto (VIX term structure) con gestion de riesgo.
Objetivo: bajar el maxDD de -66% a algo sobrevivible SIN matar el Sharpe. Lee cache data/futures/.

Overlays probados (todos sin lookahead — usan VIX/TS/vol de AYER):
  base     : corto VIXY 100% en contango (TS<1).
  frac30   : mismo, pero tamano fijo 30% (el Sharpe es invariante a escala; capa el DD absoluto).
  vixfilt  : corto solo si TS<1 Y VIX<umbral (evita regimenes ya estresados).
  voltgt   : tamano ~ objetivo_vol / VIX (dimensiona chico cuando VIX alto).
  combo    : voltgt + filtro VIX + solo contango.
Metricas + Calmar (ret/|DD|) + tail (2018/2020). El "domado" = Sharpe alto Y DD sobrevivible.
Solo LEE.
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
COST = 0.0003


def load1(name):
    fp = os.path.join(CACHE, f"{name}.csv")
    s = pd.read_csv(fp, index_col=0, parse_dates=True).iloc[:, 0]
    return s


def stats(r):
    r = r.dropna()
    if len(r) < 50:
        return None
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    ann = (1 + r.mean())**252 - 1
    sh = r.mean()/r.std()*np.sqrt(252) if r.std() > 0 else 0
    calmar = (ann) / abs(dd) if dd < 0 else np.nan
    return dict(ann=ann*100, sharpe=sh, maxDD=dd*100, worst=r.min()*100,
                calmar=calmar, final=eq.iloc[-1])


def win(r, y, m):
    sub = r[(r.index >= f"{y}-{m:02d}-01") & (r.index <= f"{y}-{m:02d}-28")]
    return ((1 + sub).prod() - 1)*100 if len(sub) else np.nan


def main():
    vix, vix3m, vixy = load1("VIX"), load1("VIX3M"), load1("VIXY")
    df = pd.DataFrame({"VIX": vix, "VIX3M": vix3m, "VIXY": vixy}).dropna()
    df["TS"] = df["VIX"]/df["VIX3M"]
    df["sv"] = -df["VIXY"].pct_change()                      # retorno de estar corto VIXY
    df = df.dropna()
    contango = (df["TS"].shift(1) < 1).astype(float)         # ayer contango (sin lookahead)
    vix_y = df["VIX"].shift(1)                               # VIX de ayer

    def apply(pos):                                          # pos = tamano (0..1) por dia
        return df["sv"]*pos - COST*pos.abs()

    overlays = {}
    overlays["base (100%)"] = apply(contango)
    overlays["frac30 (30%)"] = apply(contango*0.30)
    overlays["vixfilt <25"] = apply(contango*(vix_y < 25))
    overlays["vixfilt <22"] = apply(contango*(vix_y < 22))
    # vol-target: tamano = clip(objetivo/ (VIX/100), 0, 1). objetivo 18% anual.
    vt = np.clip(0.18/(vix_y/100.0), 0, 1)
    overlays["voltgt(18%)"] = apply(contango*vt)
    overlays["combo (voltgt+VIX<25)"] = apply(contango*vt*(vix_y < 25))
    overlays["combo x0.6 (menos riesgo)"] = apply(contango*vt*(vix_y < 25)*0.6)

    print(f"Rango: {df.index[0].date()} -> {df.index[-1].date()}  ({len(df)} días)\n")
    print(f"{'Overlay':<26}{'annual%':>9}{'Sharpe':>8}{'maxDD%':>9}{'peorDía%':>10}{'Calmar':>8}{'x':>8}")
    print("-"*78)
    for name, r in overlays.items():
        s = stats(r)
        print(f"{name:<26}{s['ann']:>+9.1f}{s['sharpe']:>+8.2f}{s['maxDD']:>+9.1f}"
              f"{s['worst']:>+10.1f}{s['calmar']:>8.2f}{s['final']:>8.1f}")

    print("\n=== TAIL (retorno en el mes del crash) ===")
    print(f"{'Overlay':<26}{'feb-2018':>10}{'mar-2020':>10}{'ago-2024':>10}")
    for name, r in overlays.items():
        print(f"{name:<26}{win(r,2018,2):>+10.1f}{win(r,2020,3):>+10.1f}{win(r,2024,8):>+10.1f}")

    print("\n=== robustez por año (Sharpe) del mejor candidato: combo x0.6 ===")
    best = overlays["combo x0.6 (menos riesgo)"]
    for y, g in best.groupby(best.index.year):
        s = stats(g)
        if s: print(f"  {y}: Sharpe {s['sharpe']:+.2f}  ret {s['ann']:+.0f}%  DD {s['maxDD']:+.0f}%")


if __name__ == "__main__":
    main()

"""
analyze_vix.py — ¿el VIX (miedo/vol implícita) ayuda a construir EDGE o reducir DD?

Usa el meta_dataset (recompensas por-apuesta fechadas, en ATR, scale-free) de los 3 edges
validados (STF, RSI2, Zarattini) y le une el VIX del DÍA PREVIO (conocido al entrar, sin
lookahead). Pregunta:
  [1] ¿la recompensa del edge CONDICIONA con el régimen de VIX? (bucket bajo/medio/alto)
  [2] ¿las PEORES pérdidas (cola que hace el DD) se concentran en algún régimen de VIX?
      -> si sí, un tope de VIX recorta la cola (reduce DD).
  [3] ¿un FILTRO por VIX (operar solo en cierto régimen) mejora reward/Sharpe OOS?
Solo LEE. No cambia estrategias; es diagnóstico.
"""
import os
import sys

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

META = os.path.join(DATA_DIR, "meta_dataset.csv")


def vix_daily():
    mt5.symbol_select("VIX", True)
    r = mt5.copy_rates_from_pos("VIX", mt5.TIMEFRAME_D1, 0, 6000)
    df = pd.DataFrame(r); df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
    lvl = pd.Series(df["close"].values, index=df["date"])
    chg = lvl.pct_change()
    return pd.DataFrame({"vix": lvl.shift(1), "vix_chg": chg.shift(1)})   # día previo = conocido al entrar


def sharpe(r):
    r = np.asarray(r, float)
    return r.mean() / r.std() if len(r) > 1 and r.std() > 0 else 0.0


def bucket_report(d, col, label):
    """reward por tercil de `col`, por edge."""
    print(f"\n[{label}] recompensa media (ATR) por tercil de {col}:")
    q = d[col].quantile([1/3, 2/3]).values
    d = d.copy()
    d["bk"] = np.where(d[col] <= q[0], "bajo", np.where(d[col] <= q[1], "medio", "alto"))
    print(f"    {'edge':<10}{'bajo':>16}{'medio':>16}{'alto':>16}")
    for edge in sorted(d["edge"].unique()):
        row = f"    {edge:<10}"
        for bk in ["bajo", "medio", "alto"]:
            s = d[(d["edge"] == edge) & (d["bk"] == bk)]["reward"]
            row += f"{s.mean():>+9.3f}(n{len(s):>4})"[:16].rjust(16)
        print(row)
    print(f"    rangos: bajo≤{q[0]:.2f} · medio≤{q[1]:.2f} · alto>{q[1]:.2f}")


def main():
    ensure()
    d = pd.read_csv(META)
    d["date"] = pd.to_datetime(d["time"]).dt.date
    vx = vix_daily()
    uni = pd.Index(sorted(set(vx.index) | set(d["date"])))
    vxf = vx.reindex(uni).ffill()
    d["vix"] = d["date"].map(vxf["vix"])
    d["vix_chg"] = d["date"].map(vxf["vix_chg"])
    d = d.dropna(subset=["vix"])
    print(f"meta_dataset con VIX: {len(d)} filas · edges {sorted(d['edge'].unique())}")
    print(f"VIX rango {d['vix'].min():.1f}–{d['vix'].max():.1f}  (mediana {d['vix'].median():.1f})")

    # [1] EDGE: ¿reward condiciona con nivel de VIX y con su cambio?
    bucket_report(d, "vix", "1a  NIVEL de VIX")
    bucket_report(d, "vix_chg", "1b  CAMBIO de VIX (día previo)")

    # correlación reward~vix por edge (señal continua)
    print("\n[1c] corr(reward, VIX) por edge (¿pendiente monótona?):")
    for edge in sorted(d["edge"].unique()):
        s = d[d["edge"] == edge]
        c = np.corrcoef(s["reward"], s["vix"])[0, 1]
        cc = np.corrcoef(s["reward"], s["vix_chg"].fillna(0))[0, 1]
        print(f"    {edge:<10} corr(reward,VIX)={c:+.3f}  corr(reward,ΔVIX)={cc:+.3f}")

    # [2] DD: ¿las peores pérdidas (peor 5%) se concentran en VIX alto?
    print("\n[2] Cola de pérdidas (media del peor 5% de rewards) por régimen de VIX:")
    q = d["vix"].quantile([1/3, 2/3]).values
    d["bk"] = np.where(d["vix"] <= q[0], "bajo", np.where(d["vix"] <= q[1], "medio", "alto"))
    for edge in sorted(d["edge"].unique()):
        row = f"    {edge:<10}"
        for bk in ["bajo", "medio", "alto"]:
            s = d[(d["edge"] == edge) & (d["bk"] == bk)]["reward"].values
            if len(s) > 20:
                tail = np.mean(np.sort(s)[:max(1, len(s)//20)])
                row += f"  {bk}:{tail:+.2f}"
            else:
                row += f"  {bk}:  n/a"
        print(row)

    # [3] FILTRO por VIX: comparar reward/Sharpe operando solo en cada régimen vs todos
    print("\n[3] ¿Filtrar por VIX mejora? (reward medio y Sharpe por régimen vs TODOS):")
    for edge in sorted(d["edge"].unique()):
        s_all = d[d["edge"] == edge]["reward"]
        print(f"    {edge:<10} TODOS: mean={s_all.mean():+.3f} sh={sharpe(s_all):+.2f}"
              f"  |  " + "  ".join(
              f"{bk}: mean={d[(d['edge']==edge)&(d['bk']==bk)]['reward'].mean():+.3f} "
              f"sh={sharpe(d[(d['edge']==edge)&(d['bk']==bk)]['reward']):+.2f}"
              for bk in ["bajo", "medio", "alto"]))


if __name__ == "__main__":
    main()

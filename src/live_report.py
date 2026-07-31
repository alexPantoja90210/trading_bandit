"""
Reporte del forward-test en vivo (demo) — agrega los CSV de trades de cada
estrategia y compara lo REALIZADO contra la expectativa del backtest.

Es la base para "ir sacando conclusiones": cada corrida escribe una foto a
data/live_report.json. Correr periódicamente (o desde un monitor) para ver si el
vivo converge a lo validado. Cuenta solo trades REALES (dry=False).

Uso:  python live_report.py
"""
import os
import sys
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# (etiqueta, csv, columna de retorno, unidad, referencia backtest)
STRATS = [
    ("RSI(2) · índices D1", "rsi2_live_trades.csv", "ret_pct", "%", "PF 2-3, wr 70-82%"),
    ("Intradía Zarattini · M30", "intraday_live_trades.csv", "ret_pct", "%", "PF ~1.2, Sharpe 0.81 OOS"),
    ("STF · oro/BTC H4", "stf_live_trades.csv", "ret_R", "R", "PF ~1.25, wr ~40%"),
]
CLOSED = {"EXIT", "EOD_FLAT", "STOP"}


def summarize(csv, retcol):
    path = os.path.join(DATA_DIR, csv)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if "dry" in df.columns:
        df = df[df["dry"].astype(str).str.lower().isin(["false", "0"])]
    df = df[df["event"].isin(CLOSED)]
    R = pd.to_numeric(df[retcol], errors="coerce").dropna().values if retcol in df.columns else np.array([])
    if len(R) == 0:
        return {"n": 0}
    w = R[R > 0]; l = R[R < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else (9.99 if w.sum() > 0 else 0.0)
    return {"n": int(len(R)), "sum": float(R.sum()), "avg": float(R.mean()),
            "wr": float((R > 0).mean() * 100), "pf": float(pf),
            "avg_win": float(w.mean()) if len(w) else 0.0,
            "avg_loss": float(l.mean()) if len(l) else 0.0,
            "best": float(R.max()), "worst": float(R.min())}


def main():
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    print(f"\n{'='*74}\nFORWARD-TEST EN VIVO (demo) — {now}")
    print(f"{'='*74}")
    print(f"{'estrategia':<26}{'trades':>7}{'wr%':>7}{'PF':>7}{'Σ':>9}{'prom':>8}   backtest")
    print("-" * 74)
    snap = {"updated": now, "strategies": {}}
    for label, csv, retcol, unit, ref in STRATS:
        s = summarize(csv, retcol)
        snap["strategies"][label] = s
        if s is None:
            print(f"{label:<26}{'—':>7}   (sin archivo)")
        elif s["n"] == 0:
            print(f"{label:<26}{0:>7}   (sin trades cerrados aún)   ← esperando · ref: {ref}")
        else:
            print(f"{label:<26}{s['n']:>7}{s['wr']:>7.0f}{s['pf']:>7.2f}"
                  f"{s['sum']:>+8.1f}{unit}{s['avg']:>+7.2f}{unit}   ref: {ref}")
    with open(os.path.join(DATA_DIR, "live_report.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    print(f"\nfoto guardada → data/live_report.json")
    print("Nota: solo trades REALES (dry=False). Baja frecuencia → toma semanas acumular muestra.")


if __name__ == "__main__":
    main()

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
    ("VIX carry · SVXY (posición)", "svxy_live_trades.csv", "ret_pct", "%", "Sharpe ~0.38, POCOS trades (hold)"),
]
CLOSED = {"EXIT", "EOD_FLAT", "STOP", "STOP_HIT"}


def _dry_col(df):
    for c in ("dry", "dry_run"):
        if c in df.columns:
            return c
    return None


def _stats(R):
    w = R[R > 0]; l = R[R < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else (9.99 if w.sum() > 0 else 0.0)
    return {"n": int(len(R)), "sum": float(R.sum()), "avg": float(R.mean()),
            "wr": float((R > 0).mean() * 100), "pf": float(pf),
            "best": float(R.max()), "worst": float(R.min())}


def summarize(csv, retcol):
    path = os.path.join(DATA_DIR, csv)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    df = df[df["event"].isin(CLOSED)]
    if retcol not in df.columns:
        return {"n": 0, "modo": "vivo"}
    dc = _dry_col(df)
    is_dry = df[dc].astype(str).str.lower().isin(["true", "1"]) if dc else pd.Series(False, index=df.index)
    real = pd.to_numeric(df[retcol][~is_dry], errors="coerce").dropna().values
    paper = pd.to_numeric(df[retcol][is_dry], errors="coerce").dropna().values
    # Headline = trades REALES. Si aún no hay reales pero sí papel (p.ej. VIX carry en dry-run),
    # se muestra el track de PAPEL, etiquetado como tal (honesto: es simulado).
    if len(real) > 0:
        out = _stats(real); out["modo"] = "vivo"; out["n_papel"] = int(len(paper))
    elif len(paper) > 0:
        out = _stats(paper); out["modo"] = "papel"
    else:
        out = {"n": 0, "modo": "papel" if dc else "vivo"}
    return out


def main():
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    print(f"\n{'='*74}\nFORWARD-TEST EN VIVO (demo) — {now}")
    print(f"{'='*74}")
    print(f"{'estrategia':<28}{'modo':>6}{'trades':>7}{'wr%':>6}{'PF':>6}{'Σ':>8}{'prom':>8}   backtest")
    print("-" * 82)
    snap = {"updated": now, "strategies": {}}
    for label, csv, retcol, unit, ref in STRATS:
        s = summarize(csv, retcol)
        snap["strategies"][label] = s
        if s is None:
            print(f"{label:<28}{'—':>6}{'—':>7}   (sin archivo)")
        elif s["n"] == 0:
            print(f"{label:<28}{s['modo']:>6}{0:>7}   (sin trades cerrados aún)  ← ref: {ref}")
        else:
            print(f"{label:<28}{s['modo']:>6}{s['n']:>7}{s['wr']:>6.0f}{s['pf']:>6.2f}"
                  f"{s['sum']:>+7.1f}{unit}{s['avg']:>+7.2f}{unit}   ref: {ref}")
    with open(os.path.join(DATA_DIR, "live_report.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    print(f"\nfoto guardada → data/live_report.json")
    print("Nota: 'vivo' = trades REALES (dry=False); 'papel' = simulado (dry-run, aún no coloca órdenes).")
    print("El VIX carry es de POSICIÓN (hold): hará pocos trades cerrados → se mide mejor por curva de retorno.")


if __name__ == "__main__":
    main()

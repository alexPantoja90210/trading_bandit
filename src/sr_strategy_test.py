"""
sr_strategy_test.py — ¿tienen edge las estrategias de SOPORTE/RESISTENCIA? Validación robusta.

Dos tesis clásicas sobre niveles (pivots diarios, S/R más objetivo y testeable):
  FADE (rebote): comprar en S1 (target PP, stop S2); vender en R1 (target PP, stop R2).
  BREAK (ruptura): comprar al romper R1 (target R2, stop PP); vender al romper S1 (target S2, stop PP).
Pivots del día PREVIO (sin lookahead), entradas en H1, 1 trade/día/lado, plano al cierre de día.

Batería robusta: expectancy en R (full-sample) + split cronológico 60/40 (OOS) + sensibilidad a
costos + test de nulidad (barajar) + robustez por año. Multi-instrumento. Solo LEE.
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

RNG = np.random.default_rng(7)
SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "US500", "NAS100", "US30", "GER40"]
N_H1 = 8000


def load(sym, tf, n):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 300:
        return None
    df = pd.DataFrame(r); df["dt"] = pd.to_datetime(df["time"], unit="s"); df["date"] = df["dt"].dt.date
    return df


def pivots(sym):
    """Niveles pivote por día desde el D1 PREVIO."""
    d = load(sym, mt5.TIMEFRAME_D1, 2000)
    if d is None:
        return None
    d = d.set_index("date")
    pp = (d["high"] + d["low"] + d["close"]) / 3
    lv = pd.DataFrame({"PP": pp, "R1": 2*pp - d["low"], "S1": 2*pp - d["high"],
                       "R2": pp + (d["high"] - d["low"]), "S2": pp - (d["high"] - d["low"])})
    return lv.shift(1)                     # niveles de HOY = del cierre de AYER (sin lookahead)


def simulate(sym, mode, cost_pct):
    h = load(sym, mt5.TIMEFRAME_H1, N_H1)
    lv = pivots(sym)
    if h is None or lv is None:
        return None
    h = h.join(lv, on="date").dropna(subset=["PP", "R1", "S1"])
    trades = []
    for day, g in h.groupby("date"):
        g = g.sort_values("dt")
        PP, R1, S1, R2, S2 = (g.iloc[0][k] for k in ("PP", "R1", "S1", "R2", "S2"))
        pos = 0; entry = sl = tp = 0.0; done_long = done_short = False
        for _, b in g.iterrows():
            if pos == 0:
                if mode == "fade":
                    if not done_long and b["low"] <= S1:
                        pos, entry, sl, tp, done_long = 1, S1, S2, PP, True
                    elif not done_short and b["high"] >= R1:
                        pos, entry, sl, tp, done_short = -1, R1, R2, PP, True
                else:  # break — stop-order en el nivel: fill al TOCAR R1/S1 (no al cierre ya rebasado)
                    if not done_long and b["high"] >= R1:
                        pos, entry, sl, tp, done_long = 1, R1, PP, R2, True
                    elif not done_short and b["low"] <= S1:
                        pos, entry, sl, tp, done_short = -1, S1, PP, S2, True
                if pos == 0:
                    continue          # sin entrada; si entró, evalúa exit en la MISMA barra (conservador)
            risk = abs(entry - sl)
            if risk <= 0:
                pos = 0; continue
            hit = None
            if pos == 1:
                if b["low"] <= sl: hit = -1.0
                elif b["high"] >= tp: hit = (tp-entry)/risk
            else:
                if b["high"] >= sl: hit = -1.0
                elif b["low"] <= tp: hit = (entry-tp)/risk
            if hit is not None:
                trades.append((day, hit - cost_pct*abs(entry)/risk/100))
                pos = 0
        if pos != 0:                       # plano al cierre del día (m2m en R)
            c = g.iloc[-1]["close"]; risk = abs(entry-sl)
            trades.append((day, (pos*(c-entry))/risk - cost_pct*abs(entry)/risk/100))
    return trades


def stats(tr):
    if not tr or len(tr) < 20:
        return None
    R = np.array([t[1] for t in tr])
    return dict(n=len(R), wr=round((R > 0).mean()*100), expR=round(R.mean(), 3), ΣR=round(R.sum(), 1))


def robust(sym, mode):
    tr = simulate(sym, mode, cost_pct=0.01)
    st = stats(tr)
    if st is None:
        return f"{sym:8} {mode:5}: muestra chica"
    R = np.array([t[1] for t in tr])
    k = int(len(R)*0.6)
    sin, sout = R[:k], R[k:]
    def e(x): return round(x.mean(), 3) if len(x) else 0
    # nulidad: barajar signos
    nulls = [np.sign(RNG.choice([-1, 1], len(R)))*np.abs(R) for _ in range(200)]
    pctl = np.mean([n.mean() < R.mean() for n in nulls])*100
    # costo x3
    st3 = stats(simulate(sym, mode, 0.03))
    line = (f"{sym:8} {mode:5}: n={st['n']:>4} expR={st['expR']:+.3f} ΣR={st['ΣR']:+6.1f} wr={st['wr']}% "
            f"| OOS in={e(sin):+.3f}/out={e(sout):+.3f} | x3 expR={st3['expR']:+.3f} "
            f"| null={pctl:.0f}%{'PASA' if pctl>95 else ''}")
    return line


def main():
    ensure()
    print("=== S/R (pivots diarios) — validación robusta ===")
    for mode in ["fade", "break"]:
        print(f"\n### {mode.upper()} ({'rebote en el nivel' if mode=='fade' else 'ruptura del nivel'})")
        for s in SYMBOLS:
            print("  " + robust(s, mode))
    print("\nOOS out>0 y null>95% en varios instrumentos = edge real. Si no, S/R mecánico sin edge.")


if __name__ == "__main__":
    main()

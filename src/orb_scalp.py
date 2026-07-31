"""
orb_scalp.py — Opening Range Breakout (ORB) en M15, la versión con base sólida y TESTEABLE
del "momentum scalping" para nuestra infra (CFD sin L2; scalping sub-minuto no es viable).

Base documentada: Zarattini & Grossman (2023) "Can Day Trading Really Be Profitable?
Evidence of Sustainable Long-term Profits from ORB" (5-min ORB, QQQ/TQQQ, 20 años, neto de
comisiones) + Crabel (1990). Adaptación fiel a M15 (dato más fino usable, ~2.2a):
  - Rango de apertura (OR) = primera barra M15 de la sesión (US: 16:30 bróker = 9:30 ET).
  - Dirección = signo de esa barra (verde→solo largos; roja→solo cortos)  [filtro Zarattini].
  - Entrada: al romper el extremo del OR en esa dirección.
  - Stop: extremo opuesto del OR.  Target: cierre de sesión (flat, sin overnight).
  - 1 trade/día. P&L en R (múltiplos de riesgo) y en %. Costo estresado (el juez del scalping).

CAVEAT honesto: M15 ~2.2a = muestra corta (sin robustez multi-régimen). Solo LEE.
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

# Sesión en hora BRÓKER (server = ET+7). US cash 9:30-16:00 ET = 16:30-23:00 bróker.
SESSIONS = {
    "US": {"open": (16, 30), "close": (23, 0), "symbols": ["US500", "NAS100", "US30"]},
    "EU": {"open": (10, 0), "close": (18, 30), "symbols": ["GER40"]},
}
N_BARS = 50000


def hm(ts):
    return ts.hour * 60 + ts.minute


def load_m15(sym):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, N_BARS)
    if r is None or len(r) < 2000:
        return None
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    df["date"] = df["time"].dt.date; df["hm"] = df["time"].apply(hm)
    return df


def backtest(sym, sess, cost_pct, both_ways=False, random_dir=False, rng=None):
    df = load_m15(sym)
    if df is None:
        return None
    o0 = sess["open"][0]*60 + sess["open"][1]; c0 = sess["close"][0]*60 + sess["close"][1]
    d = df[(df["hm"] >= o0) & (df["hm"] < c0)].copy()
    trades = []
    for day, g in d.groupby("date"):
        g = g.sort_values("time")
        if len(g) < 4:
            continue
        orb = g.iloc[0]
        or_hi, or_lo = orb["high"], orb["low"]
        rng_sz = or_hi - or_lo
        if rng_sz <= 0:
            continue
        d_dir = 1 if orb["close"] >= orb["open"] else -1
        if random_dir:
            d_dir = rng.choice([-1, 1])
        rest = g.iloc[1:]
        entry = None; side = 0
        for _, b in rest.iterrows():
            if (both_ways or d_dir == 1) and entry is None and b["high"] > or_hi:
                entry = or_hi; side = 1; break
            if (both_ways or d_dir == -1) and entry is None and b["low"] < or_lo:
                entry = or_lo; side = -1; break
        if entry is None:
            continue
        stop = or_lo if side == 1 else or_hi
        risk = abs(entry - stop)
        # recorrer hasta cierre de sesión: stop o EOD
        after = rest[rest["time"] >= b["time"]]
        exit_p = g.iloc[-1]["close"]
        for _, bb in after.iterrows():
            if side == 1 and bb["low"] <= stop:
                exit_p = stop; break
            if side == -1 and bb["high"] >= stop:
                exit_p = stop; break
        pnl_pct = side * (exit_p / entry - 1) * 100 - cost_pct
        R = (side * (exit_p - entry) - abs(entry)*cost_pct/100) / risk if risk > 0 else 0
        trades.append((day, pnl_pct, R))
    return trades


def stats(trades):
    if not trades or len(trades) < 20:
        return None
    pnl = np.array([t[1] for t in trades]); R = np.array([t[2] for t in trades])
    eq = np.cumsum(pnl); dd = (eq - np.maximum.accumulate(eq)).min()
    wr = (pnl > 0).mean()*100
    w = pnl[pnl > 0].sum(); l = -pnl[pnl < 0].sum()
    sh = pnl.mean()/pnl.std()*np.sqrt(252) if pnl.std() > 0 else 0
    return dict(n=len(trades), ret=eq[-1], dd=dd, wr=wr, pf=(w/l if l > 0 else 9.99),
                sharpe=sh, avgR=R.mean(), expectancy=pnl.mean())


def main():
    ensure()
    rng = np.random.default_rng(9)
    print("=== ORB (Opening Range Breakout) M15 — momentum intradía testeable ===")
    for sname, sess in SESSIONS.items():
        for sym in sess["symbols"]:
            tr = backtest(sym, sess, cost_pct=0.01)      # ~0.01% ida y vuelta (spread índice fino)
            st = stats(tr)
            if st is None:
                print(f"\n{sym} ({sname}): muestra insuficiente"); continue
            print(f"\n### {sym} ({sname}) · M15 ORB direccional (Zarattini) · {st['n']} trades")
            print(f"    ret={st['ret']:+.1f}%  Sharpe={st['sharpe']:+.2f}  DD={st['dd']:+.1f}%  "
                  f"wr={st['wr']:.0f}%  PF={st['pf']:.2f}  avgR={st['avgR']:+.2f}  exp={st['expectancy']:+.3f}%/tr")
            # robustez por trimestre (2.2a -> pocos años, uso trimestres)
            q = {}
            for day, pnl, _ in tr:
                k = f"{day.year}Q{(day.month-1)//3+1}"; q[k] = q.get(k, 0)+pnl
            pos = sum(1 for v in q.values() if v > 0)
            print(f"    trimestres+ {pos}/{len(q)}  |  " + "  ".join(f"{k}:{v:+.0f}" for k, v in sorted(q.items())))
            # sensibilidad a costos (juez del scalping)
            print("    costos: " + "  ".join(
                f"x{m}({0.01*m:.3f}%):PF={stats(backtest(sym,sess,0.01*m))['pf']:.2f}" for m in [1, 2, 3, 5]))
            # baselines: ambos lados (sin filtro dir) y dirección aleatoria
            bw = stats(backtest(sym, sess, 0.01, both_ways=True))
            rd = [stats(backtest(sym, sess, 0.01, random_dir=True, rng=rng))["sharpe"] for _ in range(30)]
            pctl = (np.array(rd) < st["sharpe"]).mean()*100
            print(f"    vs ambos-lados PF={bw['pf']:.2f} Sh={bw['sharpe']:+.2f}  |  "
                  f"vs dir-aleatoria: percentil {pctl:.0f}% ({'PASA' if pctl>95 else 'azar'})")


if __name__ == "__main__":
    main()

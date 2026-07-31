"""
Smart Money — Order Blocks + Break of Structure, filtrado por régimen.

Prueba mecánica y SIN look-ahead (el pecado capital de los backtests SMC):
- Swings por fractal confirmado sw barras DESPUÉS (no repinta).
- BOS alcista = close rompe el último swing high CONFIRMADO. OB alcista = última
  vela bajista antes del impulso que rompió. Entrada en la mitigación (precio
  regresa al OB). Stop bajo el OB; target R-múltiplo fijo. Simétrico para cortos.
- Filtro de régimen: solo largos si familia=TREND_UP, cortos si TREND_DOWN.
  Se compara CON y SIN filtro para aislar el aporte del régimen.

Metodología del proyecto: si no supera al azar / no es robusto por año / no pasa
walk-forward → a pruebas_fallidas. Solo LEE histórico.
"""
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from reward_engine import compute_indicators
from regime_master import classify, Params

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

N = 50000
SW = 3          # ventana del fractal (confirma sw barras después)
BUF = 0.10      # colchón del stop en ATR
TP_R = 2.0      # target en múltiplos de R
MAXWAIT = 12    # barras máximas esperando la mitigación
OBSCAN = 20     # cuántas barras atrás buscar la vela del OB


def atr_series(high, low, close, length=14):
    prev = np.roll(close, 1); prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).rolling(length).mean().values


def swings(high, low, sw):
    """Fractales estrictos: swing high/low con sw barras a cada lado."""
    n = len(high)
    sh = np.zeros(n, bool); sl = np.zeros(n, bool)
    for i in range(sw, n - sw):
        if high[i] == high[i - sw:i + sw + 1].max() and (high[i] > high[i - sw:i]).all() and (high[i] > high[i + 1:i + sw + 1]).all():
            sh[i] = True
        if low[i] == low[i - sw:i + sw + 1].min() and (low[i] < low[i - sw:i]).all() and (low[i] < low[i + 1:i + sw + 1]).all():
            sl[i] = True
    return sh, sl


def backtest(o, h, l, c, atr, fam, year, cost, sw=SW, tp_r=TP_R, buf=BUF,
             require_family=True):
    n = len(c)
    sh, sl = swings(h, l, sw)
    lsh = None                # (valor, idx) último swing high confirmado
    lsl = None
    pend = None               # OB armado: dict(side, top, bot, expiry)
    pos = None
    trades = []
    warm = 210

    for t in range(warm, n):
        if not (np.isfinite(atr[t]) and atr[t] > 0):
            continue
        # 1) confirmar swings cuya ventana derecha termina en t (idx = t-sw)
        j = t - sw
        if j >= 0:
            if sh[j]:
                lsh = (h[j], j)
            if sl[j]:
                lsl = (l[j], j)

        # 2) gestionar posición abierta (stop primero = conservador)
        if pos is not None:
            hit_stop = (l[t] <= pos["stop"]) if pos["side"] == 1 else (h[t] >= pos["stop"])
            hit_tp = (h[t] >= pos["tp"]) if pos["side"] == 1 else (l[t] <= pos["tp"])
            exitp = None
            if hit_stop:
                exitp = pos["stop"]
            elif hit_tp:
                exitp = pos["tp"]
            if hit_stop or hit_tp:
                R = pos["side"] * (exitp - pos["entry"]) / pos["risk"] - cost / pos["risk"]
                trades.append((t, R, year[pos["ebar"]], pos["fam"]))
                pos = None
            else:
                continue      # sigue en posición, no busca nueva

        if pos is not None:
            continue

        # 3) mitigación del OB armado → entrada
        if pend is not None:
            if t > pend["expiry"]:
                pend = None
            else:
                if pend["side"] == 1:
                    invalid = c[t] < pend["bot"]
                    touch = l[t] <= pend["top"] and h[t] >= pend["bot"]
                else:
                    invalid = c[t] > pend["top"]
                    touch = h[t] >= pend["bot"] and l[t] <= pend["top"]
                if invalid:
                    pend = None
                elif touch:
                    fam_ok = (not require_family) or (
                        fam[t] == ("TREND_UP" if pend["side"] == 1 else "TREND_DOWN"))
                    if fam_ok:
                        entry = pend["top"] if pend["side"] == 1 else pend["bot"]
                        stop = (pend["bot"] - buf * atr[t]) if pend["side"] == 1 else (pend["top"] + buf * atr[t])
                        risk = abs(entry - stop)
                        if risk > 0:
                            tp = entry + pend["side"] * tp_r * risk
                            pos = {"side": pend["side"], "entry": entry, "stop": stop,
                                   "tp": tp, "risk": risk, "ebar": t, "fam": fam[t]}
                    pend = None

        # 4) detectar BOS y armar OB (solo si no hay OB armado ni posición)
        if pend is None and pos is None:
            if lsh is not None and c[t] > lsh[0]:
                k = None
                for b in range(t - 1, max(t - OBSCAN, lsh[1]) - 1, -1):
                    if c[b] < o[b]:
                        k = b; break
                if k is not None:
                    pend = {"side": 1, "top": h[k], "bot": l[k], "expiry": t + MAXWAIT}
            elif lsl is not None and c[t] < lsl[0]:
                k = None
                for b in range(t - 1, max(t - OBSCAN, lsl[1]) - 1, -1):
                    if c[b] > o[b]:
                        k = b; break
                if k is not None:
                    pend = {"side": -1, "top": h[k], "bot": l[k], "expiry": t + MAXWAIT}

    return trades


def stats(trades):
    if not trades:
        return None
    R = np.array([r for _, r, _, _ in trades])
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; ls = R[R < 0]
    pf = w.sum() / -ls.sum() if ls.sum() < 0 else 9.99
    return dict(n=len(R), sumR=R.sum(), pf=pf, wr=(R > 0).mean() * 100,
                dd=dd, avg=R.mean(), sharpe=R.mean() / R.std() if R.std() > 0 else 0)


def line(label, s):
    if s is None:
        print(f"  {label:<26} sin trades"); return
    print(f"  {label:<26} n={s['n']:>4}  ΣR={s['sumR']:>+7.1f}  PF={s['pf']:.2f}  "
          f"wr={s['wr']:>4.1f}%  maxDD={s['dd']:>+6.1f}R  sh/tr={s['sharpe']:+.2f}")


def run(sym, tf, tf_name):
    ensure(); mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    r = mt5.copy_rates_from_pos(sym, tf, 0, N)
    if r is None or len(r) < 2000:
        print(f"### {sym}: insuficiente"); return
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    df = compute_indicators(df)
    print(f"\n{'='*72}\n### {sym} · {tf_name} · {len(df)} barras "
          f"({df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()})")
    print("clasificando régimen...")
    reg = classify(df, Params())
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    year = df["time"].dt.year.values
    atr = atr_series(h, l, c, 14)
    fam = reg["family"].fillna("NO_TRADE").values
    cost = info.spread * info.point if info else 0.0

    # SIN filtro vs CON filtro de régimen
    print("\n[A] OB+BOS — sin filtro de régimen vs con filtro:")
    for tp in [1.5, 2.0, 3.0]:
        s_no = stats(backtest(o, h, l, c, atr, fam, year, cost, tp_r=tp, require_family=False))
        s_yes = stats(backtest(o, h, l, c, atr, fam, year, cost, tp_r=tp, require_family=True))
        line(f"TP={tp}R  sin filtro", s_no)
        line(f"TP={tp}R  con régimen", s_yes)

    # robustez por año (config base TP=2, con régimen)
    tr = backtest(o, h, l, c, atr, fam, year, cost, tp_r=2.0, require_family=True)
    yr = defaultdict(list)
    for _, R, y, _ in tr:
        yr[y].append(R)
    pos = sum(1 for v in yr.values() if np.sum(v) > 0)
    print(f"\n[B] Robustez por año (TP=2R, con régimen): {pos}/{len(yr)} años positivos")
    print("    " + "  ".join(f"{y}:{np.sum(v):+.0f}" for y, v in sorted(yr.items()) if len(v) >= 2))


def main():
    ensure()
    run("XAUUSD", mt5.TIMEFRAME_H4, "H4")
    run("NAS100", mt5.TIMEFRAME_D1, "D1")
    run("US500", mt5.TIMEFRAME_D1, "D1")


if __name__ == "__main__":
    main()

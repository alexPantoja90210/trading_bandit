"""
michaelfx_backtest.py — mecaniza una APROXIMACIÓN de MichaelFX y compara BRANCHES (variantes
de reglas) por reward, estilo bandit/AlphaZero. Baseline medible de la estrategia discrecional.

Mecanización (branch = dict de params):
  - Sesgo en HTF (H1/H4, EMA20 vs EMA50) → solo operar a favor del sesgo (o ignorarlo).
  - OB (M15): última vela opuesta antes de un BOS (cierre rompe máx/mín de `ob_str` velas).
  - Entrada: cuando el precio MITIGA un OB (vuelve a la zona) en dirección del sesgo → entra en
    el borde proximal, SL al borde opuesto + buffer, TP a R:R.
  - Filtro de sesión (UTC-5), máx 2 ops/día, 1 activa a la vez.
Métricas por par y total: trades, winrate, avgR, expectancy(R), totalR. Ventana: este mes.
Honesto: aproxima lo discrecional; probablemente rinde menos que la discreción (SMC mecánico
falló antes). Es el branch #1 y el marco para iterar branches. Solo LEE.
"""
import sys
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
import michaelfx_engine as E

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

N_M15 = 6000          # ~2 meses de M15 (warmup + mes)
SESS_UTC5 = E.SESSIONS_UTC5


def _load(sym, tf, n):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 500:
        return None
    df = pd.DataFrame(r); df["dt"] = pd.to_datetime(df["time"], unit="s")
    return df


def _bias_series(sym, bias_tf, emaF=20, emaS=50):
    df = _load(sym, {"H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}[bias_tf], 2000)
    if df is None:
        return None
    c = df["close"].values
    ef = pd.Series(c).ewm(span=emaF, adjust=False).mean().values
    es = pd.Series(c).ewm(span=emaS, adjust=False).mean().values
    b = np.where((c > ef) & (ef > es), 1, np.where((c < ef) & (ef < es), -1, 0))
    return df["dt"].values, b


def _in_session(dt_utc):
    m = (dt_utc - np.timedelta64(5, "h"))
    ts = pd.Timestamp(m); mins = ts.hour*60 + ts.minute
    return any(a <= mins <= z for a, z in SESS_UTC5.values())


def _confluence_maps(df):
    """PDH/PDL por barra (día previo) + banda Fib de descuento/premium (61.8-75% del swing rodante)."""
    d = df.copy()
    d["date"] = d["dt"].dt.date
    dhl = d.groupby("date").agg(dh=("high", "max"), dl=("low", "min"))
    dhl["pdh"] = dhl["dh"].shift(1); dhl["pdl"] = dhl["dl"].shift(1)
    pdh = d["date"].map(dhl["pdh"]).values
    pdl = d["date"].map(dhl["pdl"]).values
    W = 160
    hi = pd.Series(df["high"]).rolling(W).max().shift(1).values
    lo = pd.Series(df["low"]).rolling(W).min().shift(1).values
    rng = hi - lo
    return {"pdh": pdh, "pdl": pdl,
            "disc_hi": hi - 0.618*rng, "disc_lo": hi - 0.75*rng,   # zona descuento (longs)
            "prem_lo": lo + 0.618*rng, "prem_hi": lo + 0.75*rng}   # zona premium (shorts)


def _has_confluence(kind, side, top, bot, t, cm, price):
    if kind is None:
        return True
    tol = 0.0025 * price
    if kind == "pdhl":
        lvl = cm["pdl"][t] if side == 1 else cm["pdh"][t]
        return np.isfinite(lvl) and (bot - tol) <= lvl <= (top + tol)
    if kind == "fib":
        if side == 1:
            dl, dh = cm["disc_lo"][t], cm["disc_hi"][t]
            return np.isfinite(dl) and not (top < dl or bot > dh)   # solapa banda descuento
        pl, ph = cm["prem_lo"][t], cm["prem_hi"][t]
        return np.isfinite(pl) and not (top < pl or bot > ph)
    return True


def run_branch(sym, p, month_start):
    df = _load(sym, mt5.TIMEFRAME_M15, N_M15)
    if df is None:
        return []
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    dts = df["dt"].values
    n = len(c)
    cm = _confluence_maps(df)
    conf = p.get("confluence")
    # sesgo alineado (última H1/H4 cerrada antes de cada barra M15)
    bias_at = np.zeros(n)
    if p["use_bias"]:
        bt = _bias_series(sym, p["bias_tf"])
        if bt is not None:
            btimes, bvals = bt
            idx = np.searchsorted(btimes, dts, side="right") - 1
            bias_at = np.where(idx >= 0, bvals[np.clip(idx, 0, len(bvals)-1)], 0)
    K = p["ob_str"]; scan = 15
    bull, bear = [], []               # OBs activos: (top, bottom)
    trades = []; open_tr = None; warm = 60
    for t in range(warm, n):
        # detectar BOS y crear OB
        if t > K+1:
            if c[t] > np.max(h[t-K:t]) and c[t-1] <= np.max(h[t-K-1:t-1]):
                for i in range(1, scan):
                    if t-i >= 0 and c[t-i] < o[t-i]:
                        bull.append((h[t-i], l[t-i])); break
            if c[t] < np.min(l[t-K:t]) and c[t-1] >= np.min(l[t-K-1:t-1]):
                for i in range(1, scan):
                    if t-i >= 0 and c[t-i] > o[t-i]:
                        bear.append((h[t-i], l[t-i])); break
            bull = bull[-6:]; bear = bear[-6:]
        # gestionar trade abierto
        if open_tr:
            side, entry, sl, tp = open_tr["side"], open_tr["entry"], open_tr["sl"], open_tr["tp"]
            risk = abs(entry - sl); hit = None
            if side == 1:
                if l[t] <= sl: hit = -1.0
                elif h[t] >= tp: hit = p["rr"]
            else:
                if h[t] >= sl: hit = -1.0
                elif l[t] <= tp: hit = p["rr"]
            open_tr["bars"] += 1
            if hit is None and open_tr["bars"] >= p["max_hold"]:
                hit = (side*(c[t]-entry))/risk if risk > 0 else 0.0     # cierre por tiempo (m2m en R)
            if hit is not None:
                trades.append({"dt": dts[t], "sym": sym, "side": side, "R": round(hit, 2)})
                open_tr = None
            continue
        # buscar entrada (a favor del sesgo, en sesión, con confluencia). El cap 2/día TOTAL
        # se aplica global en main(); aquí solo 1 abierta por par.
        if p["sessions"] and not _in_session(dts[t]):
            continue
        b = bias_at[t] if p["use_bias"] else 0
        px = c[t]
        # LONG: mitiga OB alcista (demanda) y sesgo alcista/neutral + confluencia
        if (not p["use_bias"] or b == 1):
            for j, (top, bot) in enumerate(bull):
                if l[t] <= top and c[t] >= bot and _has_confluence(conf, 1, top, bot, t, cm, px):
                    entry = top; sl = bot - p["buf"]*(top-bot); risk = entry-sl
                    if risk <= 0: continue
                    open_tr = {"side": 1, "entry": entry, "sl": sl, "tp": entry + p["rr"]*risk, "bars": 0}
                    bull.pop(j); break
        if open_tr: continue
        if (not p["use_bias"] or b == -1):
            for j, (top, bot) in enumerate(bear):
                if h[t] >= bot and c[t] <= top and _has_confluence(conf, -1, top, bot, t, cm, px):
                    entry = bot; sl = top + p["buf"]*(top-bot); risk = sl-entry
                    if risk <= 0: continue
                    open_tr = {"side": -1, "entry": entry, "sl": sl, "tp": entry - p["rr"]*risk, "bars": 0}
                    bear.pop(j); break
    # filtrar al mes objetivo
    return [t for t in trades if pd.Timestamp(t["dt"]) >= month_start]


def summarize(trades):
    if not trades:
        return None
    R = np.array([t["R"] for t in trades])
    return dict(n=len(R), wr=round((R > 0).mean()*100), avgR=round(R.mean(), 2),
                expectancy=round(R.mean(), 2), totalR=round(R.sum(), 1))


# Todas con cap 2/día TOTAL (aplicado en main). confluence: None | "pdhl" | "fib" (mejora #1).
BRANCHES = {
    "A_baseline":   dict(use_bias=True,  bias_tf="H1", rr=2.5, sessions=True,  ob_str=5, buf=0.1, max_hold=48, confluence=None),
    "C_no_session": dict(use_bias=True,  bias_tf="H1", rr=2.5, sessions=False, ob_str=5, buf=0.1, max_hold=48, confluence=None),
    "F_conf_pdhl":  dict(use_bias=True,  bias_tf="H1", rr=2.5, sessions=True,  ob_str=5, buf=0.1, max_hold=48, confluence="pdhl"),
    "G_conf_fib":   dict(use_bias=True,  bias_tf="H1", rr=2.5, sessions=True,  ob_str=5, buf=0.1, max_hold=48, confluence="fib"),
    "H_conf_both":  dict(use_bias=True,  bias_tf="H1", rr=5.0, sessions=True,  ob_str=5, buf=0.1, max_hold=48, confluence="pdhl"),
}


def apply_global_cap(trades, cap=2):
    """Máx `cap` entradas por día TOTAL (across pares), por orden de tiempo de entrada."""
    trades = sorted(trades, key=lambda t: t["dt"])
    kept = []; count = {}
    for t in trades:
        d = pd.Timestamp(t["dt"]).date()
        if count.get(d, 0) < cap:
            kept.append(t); count[d] = count.get(d, 0) + 1
    return kept


def main():
    ensure()
    watch = E.load_watchlist()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = pd.Timestamp(now.year, now.month, 1)
    print(f"=== Backtest MichaelFX (mecánico) — {month_start:%Y-%m} · pares {watch} ===")
    results = {}
    for bname, p in BRANCHES.items():
        allt = []
        for s in watch:
            allt += run_branch(s, p, month_start)
        capped = apply_global_cap(allt, cap=2)              # mejora #1: máx 2/día TOTAL
        per = {}
        for s in watch:
            st = summarize([t for t in capped if t["sym"] == s])
            if st:
                per[s] = st
        tot = summarize(capped)
        results[bname] = (tot, per)
        conf = p.get("confluence") or "—"
        if tot:
            print(f"\n[{bname}] (conf={conf}) TOTAL: n={tot['n']} wr={tot['wr']}% "
                  f"expR={tot['expectancy']:+.2f} ΣR={tot['totalR']:+.1f}  (pre-cap {len(allt)} → cap {len(capped)})")
            print("   por par: " + " · ".join(f"{s}(n{v['n']} {v['expectancy']:+.2f}R)" for s, v in per.items()))
        else:
            print(f"\n[{bname}] (conf={conf}) sin trades este mes")
    # ranking por expectancy total
    rank = sorted([(b, r[0]) for b, r in results.items() if r[0]], key=lambda x: -x[1]["expectancy"])
    print("\n=== RANKING de branches (por expectancy R, este mes) ===")
    for b, s in rank:
        print(f"  {b:14} expR={s['expectancy']:+.2f}  ΣR={s['totalR']:+.1f}  n={s['n']}  wr={s['wr']}%")
    print("\nNOTA: muestra de 1 mes = ruido alto. Es el arranque del ciclo de branches; se acumula "
          "mes a mes en MICHAELFX_BRANCHES.md y se compara contra el journal discrecional.")


if __name__ == "__main__":
    main()

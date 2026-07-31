"""
Réplica de "Market Intraday Momentum" (Gao, Han, Li & Zhou, 2018, JFE) sobre
índices CFD (US500, NAS100) en M30.

Hallazgo del paper: el retorno de la PRIMERA media hora del día predice el de la
ÚLTIMA media hora. Estrategia: al empezar la última media hora, tomar posición
en el sentido del signo del retorno de la primera (o de la 12ª) media hora, y
cerrar al cierre. ~1 trade/día, intradía (sin swap).

Sesión cash de EEUU 9:30–16:00 ET = 13 barras M30. Validado empíricamente que
el servidor del bróker = ET + 7h (pico de vol/volumen en 16:30 bróker = 9:30 ET).
  - primera media hora  = barra que ABRE 09:30 ET
  - 12ª media hora      = barra que abre 15:00 ET
  - última media hora   = barra que abre 15:30 ET

Cada corrida cachea la data a data/intraday/<sym>_M30.csv (dedup+append) para ir
acumulando historia más allá de la ventana rodante del bróker. Solo LEE mercado.
"""
import os
import sys
# archivado en pruebas_fallidas/ → añadir src/ al path para importar sus módulos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import defaultdict

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SYMBOLS = ["US500", "NAS100"]
ET_SHIFT_H = -7            # servidor → ET (validado empíricamente)
FIRST_ET = "09:30"        # primera media hora (open)
TW12_ET = "15:00"         # 12ª media hora
LAST_ET = "15:30"         # última media hora
CACHE_DIR = os.path.join(DATA_DIR, "intraday")


def load_m30(sym, n=50000):
    """Pull M30 y cachea (merge+dedup) a CSV. Devuelve DataFrame con time/OHLC."""
    ensure(); mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M30, 0, n)
    live = pd.DataFrame(r) if r is not None else pd.DataFrame()
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{sym}_M30.csv")
    cols = ["time", "open", "high", "low", "close", "tick_volume"]
    if os.path.exists(path):
        old = pd.read_csv(path)
        both = pd.concat([old[cols] if set(cols) <= set(old.columns) else old,
                          live[cols] if len(live) else live], ignore_index=True)
    else:
        both = live[cols] if len(live) else live
    if len(both) == 0:
        return None, path, 0
    both = both.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    both.to_csv(path, index=False)
    return both, path, len(both)


def to_sessions(df):
    """Devuelve DataFrame por día ET con r_first, r_12, r_last (retornos %)."""
    d = df.copy()
    d["dt"] = pd.to_datetime(d["time"], unit="s") + pd.Timedelta(hours=ET_SHIFT_H)  # ET
    d["date"] = d["dt"].dt.date
    d["hm"] = d["dt"].dt.strftime("%H:%M")
    rows = {}
    for tag, hm in [("first", FIRST_ET), ("tw12", TW12_ET), ("last", LAST_ET)]:
        sel = d[d["hm"] == hm].set_index("date")
        rows[tag] = (sel["close"] / sel["open"] - 1.0) * 100.0  # retorno de esa media hora
    out = pd.DataFrame(rows).dropna()
    # precio de apertura de la última barra (costos) + volumen de la primera (condicional)
    out["last_open"] = d[d["hm"] == LAST_ET].set_index("date")["open"]
    out["fvol"] = d[d["hm"] == FIRST_ET].set_index("date")["tick_volume"]
    return out.dropna()


def ols_t(x, y):
    x = np.asarray(x, float); y = np.asarray(y, float)
    n = len(x); xb = x.mean(); yb = y.mean()
    sxx = ((x - xb) ** 2).sum()
    beta = ((x - xb) * (y - yb)).sum() / sxx
    alpha = yb - beta * xb
    resid = y - (alpha + beta * x)
    se = np.sqrt((resid ** 2).sum() / (n - 2) / sxx)
    t = beta / se if se > 0 else 0.0
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    return beta, t, r2


def stats(daily_ret, year):
    R = np.asarray(daily_ret, float)
    if len(R) == 0:
        return None
    eq = np.cumsum(R); dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; l = R[R < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    sharpe = (R.mean() / R.std() * np.sqrt(252)) if R.std() > 0 else 0.0
    return dict(ret=R.sum(), sharpe=sharpe, dd=dd, wr=(R > 0).mean() * 100,
                pf=pf, n=len(R), avg=R.mean())


def run(sym):
    df, path, ntot = load_m30(sym)
    if df is None or ntot < 2000:
        print(f"### {sym}: data insuficiente"); return
    S = to_sessions(df)
    span = f"{S.index.min()} -> {S.index.max()}"
    print(f"\n{'='*72}\n### {sym} · M30 · {len(S)} días de sesión completos ({span})")
    print(f"    cache: {path}  ({ntot} barras M30)")

    # costo por trade (round-trip cruzando el spread una vez), en %
    info = mt5.symbol_info(sym)
    px = float(S["last_open"].iloc[-1])
    cost = (info.spread * info.point) / px * 100.0 if info else 0.0
    print(f"    spread≈{info.spread} pts → costo≈{cost:.4f}%/trade")

    rf, r12, rl = S["first"].values, S["tw12"].values, S["last"].values

    # [A] PREDICTIVIDAD (regresión última ~ primera, y ~ 12ª)
    b1, t1, q1 = ols_t(rf, rl)
    b2, t2, q2 = ols_t(r12, rl)
    print(f"\n[A] Predictividad de la ÚLTIMA media hora:")
    print(f"    r_last ~ r_first :  beta={b1:+.3f}  t={t1:+.2f}  R²={q1*100:.2f}%")
    print(f"    r_last ~ r_12    :  beta={b2:+.3f}  t={t2:+.2f}  R²={q2*100:.2f}%")

    # [B] ESTRATEGIAS de timing (posición en la última media hora)
    years = np.array([d.year for d in S.index])
    sig_first = np.sign(rf)
    sig_12 = np.sign(r12)
    sig_both = np.where((rf > 0) & (r12 > 0), 1.0,
                        np.where((rf < 0) & (r12 < 0), -1.0, 0.0))
    strategies = {
        "sign(first)": sig_first,
        "sign(12th)": sig_12,
        "both-agree": sig_both,
        "always-long": np.ones_like(rf),
    }
    print(f"\n[B] Estrategias en la última media hora (con costo {cost:.3f}%/trade):")
    print(f"    {'estrategia':<14}{'ret%':>8}{'Sharpe':>8}{'DD%':>8}{'wr%':>7}{'PF':>6}{'trades':>8}")
    print("    " + "-" * 59)
    results = {}
    for name, sig in strategies.items():
        traded = sig != 0
        pnl = sig * rl - np.where(traded, cost, 0.0)
        st = stats(pnl[traded] if name != "always-long" else pnl, years)
        results[name] = (sig, pnl)
        print(f"    {name:<14}{st['ret']:>+8.1f}{st['sharpe']:>8.2f}{st['dd']:>+8.1f}"
              f"{st['wr']:>7.1f}{st['pf']:>6.2f}{st['n']:>8}")

    # [C] ROBUSTEZ POR AÑO (mejor señal: la que tenga más Sharpe entre first/12/both)
    best = max(["sign(first)", "sign(12th)", "both-agree"],
               key=lambda k: stats(results[k][1][results[k][0] != 0], years)["sharpe"])
    sig, pnl = results[best]
    print(f"\n[C] Robustez por año — {best}:")
    yr = defaultdict(list)
    for i, y in enumerate(years):
        if sig[i] != 0:
            yr[y].append(pnl[i])
    pos = sum(1 for y, v in yr.items() if np.sum(v) > 0)
    print(f"    años positivos: {pos}/{len(yr)}")
    print("    " + "  ".join(f"{y}:{np.sum(v):+.1f}" for y, v in sorted(yr.items())))

    # [D] CONDICIONAL — el paper dice que el efecto se concentra en días de
    #     PRIMER MOVIMIENTO GRANDE y ALTO VOLUMEN. Test en el tercil superior.
    fvol = S["fvol"].values
    absf = np.abs(rf)
    print(f"\n[D] Condicional (afirmación fuerte del paper):")
    for label, cond in [("|r_first| top-33%", absf >= np.quantile(absf, 0.67)),
                        ("volumen 1ª top-33%", fvol >= np.quantile(fvol, 0.67)),
                        ("ambos", (absf >= np.quantile(absf, 0.67)) & (fvol >= np.quantile(fvol, 0.67)))]:
        x, y = rf[cond], rl[cond]
        if len(x) < 30:
            continue
        b, t, q = ols_t(x, y)
        pnl_c = np.sign(x) * y - cost
        stc = stats(pnl_c, None)
        print(f"    {label:<20} n={len(x):>4}  beta={b:+.3f} t={t:+.2f}  "
              f"| sign(first): ret={stc['ret']:+.1f}% Sharpe={stc['sharpe']:+.2f} wr={stc['wr']:.0f}%")


def main():
    ensure()
    for s in SYMBOLS:
        run(s)


if __name__ == "__main__":
    main()

"""
stat_arb_tests.py — las dos ramas que abre el resultado de eigen_trading.py:

[A] MOMENTUM CROSS-SECCIONAL: como los eigen-residuos DRIFTEAN (no revierten), probar lo
    contrario — comprar los ganadores relativos recientes y shortear los rezagados
    (dollar-neutral = demean del score). Factor conocido (Jegadeesh-Titman cross-sectional).
[B] PARES COINTEGRADOS: el "eigen" de 2 activos = un spread. Probar oro-plata, BTC-ETH,
    US500-NAS100: hedge ratio rodante, spread, z-score, mean-reversion (half-life + trade).

Rigor en ambas: ventana rodante sin lookahead, costos (por vuelta / por pata), robustez por
año, test de nulidad con la SEÑAL aleatorizada (¿bate a pesos/entradas al azar?). Solo LEE.
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

RNG = np.random.default_rng(5)
N_BARS = 3500


def load(syms, what="ret"):
    out = {}
    for s in syms:
        mt5.symbol_select(s, True)
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_D1, 0, N_BARS)
        if r is None or len(r) < 500:
            continue
        df = pd.DataFrame(r); df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
        col = df["close"].pct_change() if what == "ret" else np.log(df["close"])
        out[s] = pd.Series(col.values, index=df["date"])
    return pd.DataFrame(out).dropna()


def stats(r, ann=252):
    r = np.asarray(r, float); r = r[np.isfinite(r)]
    if len(r) < 30 or r.std() == 0:
        return dict(ret=0, sharpe=0, dd=0, pf=0, n=len(r))
    eq = np.cumsum(r); dd = (eq - np.maximum.accumulate(eq)).min()
    w = r[r > 0].sum(); l = -r[r < 0].sum()
    return dict(ret=eq[-1]*100, sharpe=r.mean()/r.std()*np.sqrt(ann),
                dd=dd*100, pf=(w/l if l > 0 else 9.99), n=len(r))


def by_year(daily, dates):
    yrs = np.array([d.year for d in dates]); pos = 0; tot = 0; line = []
    for y in sorted(set(yrs)):
        s = daily[yrs == y].sum()*100; line.append(f"{y}:{s:+.1f}"); pos += s > 0; tot += 1
    return pos, tot, "  ".join(line)


# ---------- [A] MOMENTUM CROSS-SECCIONAL ----------
def xsec_weights(R, L, skip):
    """peso_i(t) = score demeaned (dollar-neutral), score = retorno medio en [t-L-skip, t-skip]."""
    A = R.values; n, k = A.shape
    W = np.zeros((n, k))
    for t in range(L + skip, n):
        sc = A[t - L - skip:t - skip].mean(axis=0)
        sc = sc - sc.mean()                       # dollar-neutral
        s = np.abs(sc).sum()
        if s > 0:
            W[t] = sc / s                         # gross = 1
    return W


def xsec_backtest(R, L, skip, cost, weights=None):
    A = R.values; n = len(R)
    W = weights if weights is not None else xsec_weights(R, L, skip)
    daily = np.zeros(n)
    for t in range(L + skip, n - 1):
        turn = np.abs(W[t] - W[t - 1]).sum()
        daily[t + 1] = W[t] @ A[t + 1] - turn * cost
    return daily[L + skip + 1:]


def run_xsec(name, syms):
    R = load(syms, "ret")
    if R.shape[1] < 3:
        print(f"{name}: insuficiente"); return
    cost = 0.00005
    print(f"\n### [A] Momentum cross-seccional · {name}: {list(R.columns)} ({len(R)} días)")
    best = None
    for L in [20, 60, 120]:
        skip = 5
        daily = xsec_backtest(R, L, skip, cost)
        st = stats(daily)
        print(f"    L={L:>3} skip={skip}: ret={st['ret']:+7.1f}%  Sharpe={st['sharpe']:+.2f}  "
              f"DD={st['dd']:+6.1f}%  PF={st['pf']:.2f}")
        if best is None or st["sharpe"] > best[1]:
            best = (L, st["sharpe"], daily)
    L, sh, daily = best
    dates = R.index[L + 5 + 1:]
    p, tot, line = by_year(daily, dates)
    print(f"    mejor L={L}: años+ {p}/{tot}  |  {line}")
    print("    costos: " + "  ".join(f"x{k}:PF={stats(xsec_backtest(R,L,5,cost*k))['pf']:.2f}" for k in [1,2,3]))
    # null: pesos aleatorios dollar-neutral (misma gross) — ¿el momentum bate al azar?
    nulls = []
    for _ in range(150):
        Wr = np.zeros((len(R), R.shape[1]))
        for t in range(L + 5, len(R)):
            z = RNG.standard_normal(R.shape[1]); z -= z.mean()
            Wr[t] = z / np.abs(z).sum()
        nulls.append(stats(xsec_backtest(R, L, 5, cost, weights=Wr))["sharpe"])
    pctl = (np.array(nulls) < sh).mean()*100
    print(f"    nulidad (150 pesos aleatorios): percentil {pctl:.0f}%  ({'PASA' if pctl>95 else 'azar'})")


# ---------- [B] PARES COINTEGRADOS ----------
def half_life(spread):
    s = spread[np.isfinite(spread)]
    ds = np.diff(s); s0 = s[:-1]
    if len(s0) < 30 or np.var(s0) == 0:
        return np.nan
    b = np.cov(s0, ds)[0, 1] / np.var(s0)
    return -np.log(2) / b if b < 0 else np.nan


def pair_backtest(P, a, b, W, z_enter, z_exit, cost, rand=False):
    """P=log-precios. hedge ratio rodante beta (a~b), spread=a-beta*b, z rodante. Opera reversión."""
    ya, yb = P[a].values, P[b].values
    ra = np.diff(ya, prepend=ya[0]); rb = np.diff(yb, prepend=yb[0])
    n = len(ya); pos = 0; daily = np.zeros(n); beta = 1.0
    for t in range(W, n):
        xa, xb = ya[t-W:t], yb[t-W:t]
        vb = np.var(xb)
        beta = np.cov(xa, xb)[0, 1]/vb if vb > 0 else 1.0
        sp = xa - beta*xb
        mu, sd = sp.mean(), sp.std()
        z = (ya[t-1] - beta*yb[t-1] - mu)/sd if sd > 0 else 0
        # P&L de posición abierta con retorno de hoy
        daily[t] = pos * (ra[t] - beta*rb[t])
        # señal
        if rand:
            znew = RNG.choice([-2, 0, 2])
            new = -np.sign(znew) if abs(znew) > z_enter else (0 if abs(znew) < z_exit else pos)
        else:
            new = -1 if z > z_enter else (1 if z < -z_enter else (0 if abs(z) < z_exit else pos))
        if new != pos:
            daily[t] -= 2*cost
        pos = new
    return daily[W:]


def run_pairs():
    print(f"\n### [B] Pares cointegrados (spread, z-score reversión):")
    pairs = [("XAUUSD", "XAGUSD"), ("BTCUSD", "ETHUSD"), ("US500", "NAS100")]
    for a, b in pairs:
        P = load([a, b], "logp")
        if P.shape[1] < 2 or len(P) < 400:
            print(f"    {a}-{b}: sin data"); continue
        hl = half_life((P[a] - P[b]).values)
        cost = 0.00005
        daily = pair_backtest(P, a, b, 60, 1.5, 0.5, cost)
        st = stats(daily); dates = P.index[60:]
        p, tot, line = by_year(daily, dates)
        # null: entradas aleatorias, misma mecánica
        nulls = [stats(pair_backtest(P, a, b, 60, 1.5, 0.5, cost, rand=True))["sharpe"] for _ in range(100)]
        pctl = (np.array(nulls) < st["sharpe"]).mean()*100
        print(f"    {a}-{b}: half-life={hl:.0f}d  ret={st['ret']:+.1f}%  Sharpe={st['sharpe']:+.2f}  "
              f"DD={st['dd']:+.1f}%  PF={st['pf']:.2f}  nulidad={pctl:.0f}%({'PASA' if pctl>95 else 'azar'})")
        print(f"        años+ {p}/{tot}  |  {line}")
        print("        costos: " + "  ".join(f"x{k}:PF={stats(pair_backtest(P,a,b,60,1.5,0.5,cost*k))['pf']:.2f}" for k in [1,2,3]))


def main():
    ensure()
    run_xsec("EQUITY", ["US500", "NAS100", "US30", "US2000", "GER40"])
    run_xsec("DIVERSO", ["US500", "NAS100", "XAUUSD", "BTCUSD", "EURUSD", "WTOIL-PERP"])
    run_pairs()


if __name__ == "__main__":
    main()

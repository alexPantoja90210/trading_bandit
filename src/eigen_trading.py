"""
eigen_trading.py — ¿el stat-arb de EIGEN-RESIDUOS (Avellaneda-Lee) da una señal NUEVA?

Idea: PCA de los retornos del cesto -> el 1er autovector es el FACTOR (mercado). El residuo
de cada activo (ret - beta*factor) mean-revierte mejor que el precio crudo (valor RELATIVO, no
absoluto). Se opera el residuo cuando se aleja de su equilibrio (s-score OU) y se cierra al
revertir. Es market-neutral (activo vs factor) -> señal distinta al precio propio.

Rigor: ventana rodante (sin lookahead), s-score AR(1), costo por PIERNA (2 patas), robustez
por año, test de nulidad (signos aleatorios). Cesto homogéneo (índices equity) = factor real.
[B] Absorption ratio (top autovalor / total) como indicador de fragilidad -> ¿reduce DD?
Solo LEE.
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

RNG = np.random.default_rng(3)
EQUITY = ["US500", "NAS100", "US30", "US2000", "GER40"]
DIVERSE = ["US500", "NAS100", "XAUUSD", "BTCUSD", "EURUSD", "WTOIL-PERP"]
W = 60          # ventana rodante
S_ENTER, S_EXIT = 1.25, 0.50
N_BARS = 3500


def load_returns(syms):
    ser = {}
    for s in syms:
        mt5.symbol_select(s, True)
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_D1, 0, N_BARS)
        if r is None or len(r) < 500:
            continue
        df = pd.DataFrame(r); df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
        ser[s] = pd.Series(df["close"].pct_change().values, index=df["date"])
    return pd.DataFrame(ser).dropna()


def s_score(X):
    """AR(1) sobre el residuo acumulado X: X_t = a + b X_{t-1}. Devuelve s-score de equilibrio.
    b<1 = mean-reverting. Si no revierte -> None."""
    x0, x1 = X[:-1], X[1:]
    if len(x0) < 20 or x0.std() == 0:
        return None
    b = np.cov(x0, x1)[0, 1] / np.var(x0)
    a = x1.mean() - b * x0.mean()
    if not (0 < b < 0.9999):
        return None
    m = a / (1 - b)                              # equilibrio
    resid = x1 - (a + b * x0)
    seq = resid.std() / np.sqrt(1 - b * b)       # sigma de equilibrio
    if seq == 0:
        return None
    return (X[-1] - m) / seq


def backtest(R, cost_leg):
    syms = list(R.columns); A = R.values; n = len(R)
    pos = {s: 0 for s in syms}                   # -1/0/+1 sobre el residuo
    beta = {s: 0.0 for s in syms}; wvec = None
    daily = np.zeros(n); absorption = np.zeros(n)
    turn = 0.0
    for t in range(W, n):
        win = A[t - W:t]                         # retornos hasta t-1 (sin lookahead)
        C = np.cov(win.T)
        ev, evec = np.linalg.eigh(C)
        absorption[t] = ev[-1] / ev.sum()
        w1 = evec[:, -1]
        if w1.sum() < 0:
            w1 = -w1                             # 1er factor con signo de mercado
        factor_win = win @ w1
        vf = np.var(factor_win)
        # realizar P&L de las posiciones abiertas con el retorno de HOY (t)
        rt = A[t]
        ft = rt @ w1
        step = 0.0
        for i, s in enumerate(syms):
            if pos[s] != 0:
                resid_ret = rt[i] - beta[s] * ft
                step += pos[s] * resid_ret
        daily[t] = step - turn * cost_leg
        turn = 0.0
        # recomputar señales para MAÑANA
        for i, s in enumerate(syms):
            b_i = np.cov(win[:, i], factor_win)[0, 1] / vf if vf > 0 else 0.0
            eps = win[:, i] - b_i * factor_win
            X = np.cumsum(eps - eps.mean())
            s_sc = s_score(X)
            old = pos[s]
            if s_sc is None:
                new = old
            elif old == 0 and s_sc < -S_ENTER:
                new = 1                          # residuo barato -> largo
            elif old == 0 and s_sc > S_ENTER:
                new = -1
            elif old != 0 and abs(s_sc) < S_EXIT:
                new = 0                          # revirtió -> cerrar
            else:
                new = old
            if new != old:
                turn += 2                        # 2 patas (activo + hedge factor)
            pos[s] = new; beta[s] = b_i
    return daily[W:], absorption[W:]


def stats(r, ann=252):
    r = np.asarray(r, float)
    eq = np.cumsum(r); dd = (eq - np.maximum.accumulate(eq)).min()
    w = r[r > 0].sum(); l = -r[r < 0].sum()
    return dict(ret=eq[-1] * 100, sharpe=(r.mean()/r.std()*np.sqrt(ann) if r.std() > 0 else 0),
                dd=dd*100, pf=(w/l if l > 0 else 9.99), n=int((r != 0).sum()))


def main():
    ensure()
    for name, syms in [("EQUITY (factor fuerte)", EQUITY), ("DIVERSO (cross-clase)", DIVERSE)]:
        R = load_returns(syms)
        if R.shape[1] < 3:
            print(f"{name}: activos insuficientes"); continue
        cost_leg = 0.00005                       # ~0.5 pip/pata por vuelta (índices, spread fino)
        daily, absorp = backtest(R, cost_leg)
        dates = R.index[W:]
        st = stats(daily)
        print(f"\n{'='*70}\n### {name}: {list(R.columns)}  ({len(R)} días)")
        print(f"    stat-arb residuos: ret={st['ret']:+.1f}%  Sharpe={st['sharpe']:+.2f}  "
              f"DD={st['dd']:+.1f}%  PF={st['pf']:.2f}  días-activo={st['n']}")
        # por año
        yrs = np.array([d.year for d in dates])
        posy = 0; tot = 0
        line = []
        for y in sorted(set(yrs)):
            s = daily[yrs == y].sum() * 100
            line.append(f"{y}:{s:+.1f}"); posy += s > 0; tot += 1
        print(f"    años+: {posy}/{tot}  |  " + "  ".join(line))
        # sensibilidad a costos
        print("    costos: " + "  ".join(
            f"x{k}:PF={stats(backtest(R, cost_leg*k)[0])['pf']:.2f}" for k in [1, 2, 3]))
        # null: signos aleatorios (misma frecuencia de trades)
        nulls = []
        base_sh = st["sharpe"]
        sgn = np.sign(daily)
        for _ in range(200):
            perm = daily * RNG.choice([-1, 1], size=len(daily))
            nulls.append(stats(perm)["sharpe"])
        pctl = (np.array(nulls) < base_sh).mean() * 100
        print(f"    nulidad (200 signos aleatorios): Sharpe real percentil {pctl:.0f}%  "
              f"({'PASA' if pctl > 95 else 'azar'})")

    # [B] absorption ratio: ¿la fragilidad (todo correlacionado) predice días malos del equity?
    print(f"\n{'='*70}\n[B] Absorption ratio (fragilidad) sobre EQUITY:")
    R = load_returns(EQUITY)
    _, absorp = backtest(R, 0)
    dates = R.index[W:]
    ew = R.mean(axis=1).values[W:]               # retorno cesto equal-weight
    hi = absorp > np.quantile(absorp, 0.8)       # top 20% fragilidad
    print(f"    vol cesto en fragilidad ALTA: {ew[hi].std()*100:.2f}%  vs "
          f"resto: {ew[~hi].std()*100:.2f}%  (¿mayor riesgo?)")
    print(f"    retorno medio cesto: fragilidad alta {ew[hi].mean()*100:+.3f}%  "
          f"resto {ew[~hi].mean()*100:+.3f}%")


if __name__ == "__main__":
    main()

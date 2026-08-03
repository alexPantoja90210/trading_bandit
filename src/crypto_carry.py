"""
Carry cripto cross-seccional (funding harvest) — extiende la tesis del carry (VIX) a cripto,
con data del bróker-tipo (Binance perp) y desplegable en Pepperstone (BTC/ETH/SOL/XRP/ADA/DOGE/LTC).

Tesis: el funding del perpetuo = el carry. Funding ALTO = largos sobre-apretados (pagan) →
CORTO cobra funding y se beneficia si revierten. Funding BAJO/negativo → LARGO cobra.
Cross-seccional market-neutral: largo bottom-k funding, corto top-k. pnl_i = pos_i*(ret_i - funding_i).
Aísla el premio de carry con beta cripto ~0 (coins muy correlacionados → largo-corto cancela).

Rigor: Sharpe con costos, split OOS, nulidad, por año, y correlación con la cartera (STF/RSI2/VIXcarry).
Solo LEE.
"""
import os
import sys

import numpy as np
import pandas as pd

from crypto_carry_data import build, COINS

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COST = 0.0004   # ~4 bps por pata de rebalanceo (spread CFD cripto)


def sr(r, f=365):
    r = r.dropna()
    if len(r) < 30 or r.std() == 0:
        return 0.0, 0.0, 0.0
    eq = (1 + r).cumprod(); dd = (eq / eq.cummax() - 1).min()
    return r.mean() / r.std() * np.sqrt(f), ((1 + r.mean())**f - 1) * 100, dd * 100


def assemble():
    fund, price = build()
    F = pd.DataFrame({c: fund[c] for c in COINS}).sort_index()
    P = pd.DataFrame({c: price[c] for c in COINS}).sort_index()
    F.index = pd.to_datetime(F.index).normalize(); P.index = pd.to_datetime(P.index).normalize()
    idx = F.index.intersection(P.index)
    F, P = F.loc[idx], P.loc[idx]
    ret = P.pct_change()
    return F, ret


def strategy(F, ret, k=2, cost=COST):
    """Largo bottom-k funding / corto top-k, market-neutral. Señal = funding de AYER (sin lookahead)."""
    sig = F.shift(1)
    rets, wprev, dates = [], pd.Series(0.0, index=COINS), []
    for dt in ret.index[1:]:
        s = sig.loc[dt].dropna()
        if len(s) < 2 * k:
            continue
        r = ret.loc[dt]; fnd = F.loc[dt]
        ranked = s.sort_values()
        longs, shorts = ranked.index[:k], ranked.index[-k:]      # bottom-k funding largo
        w = pd.Series(0.0, index=COINS)
        w[longs] = 1.0 / k; w[shorts] = -1.0 / k
        # pnl = pos*(retorno - funding pagado). funding: largo paga +f, corto recibe.
        pnl = float((w * (r.reindex(COINS).fillna(0) - fnd.reindex(COINS).fillna(0))).sum())
        turn = (w - wprev).abs().sum()
        pnl -= cost * turn
        rets.append(pnl); dates.append(dt); wprev = w
    return pd.Series(rets, index=pd.to_datetime(dates))


def main():
    print("Ensamblando carry cripto...")
    F, ret = assemble()
    r = strategy(F, ret, k=2)
    print(f"Ventana: {r.index[0].date()} → {r.index[-1].date()}  ({len(r)} días)\n")

    print("=== Carry cripto cross-seccional (largo bottom-2 / corto top-2 funding) ===")
    for k in [2, 3]:
        rr = strategy(F, ret, k=k)
        s, a, dd = sr(rr)
        print(f"  k={k}: Sharpe {s:+.2f}  annual {a:+.1f}%  maxDD {dd:+.1f}%  x={float((1+rr).prod()):.2f}")

    print("\n=== Split OOS 60/40 (k=2) ===")
    kk = int(len(r) * 0.6)
    si, ai, ddi = sr(r.iloc[:kk]); so, ao, ddo = sr(r.iloc[kk:])
    print(f"  TRAIN: Sharpe {si:+.2f}  ret {ai:+.0f}%  DD {ddi:+.0f}%")
    print(f"  TEST : Sharpe {so:+.2f}  ret {ao:+.0f}%  DD {ddo:+.0f}%")

    print("\n=== Test de nulidad (barajar el ranking de funding 300x) ===")
    rng = np.random.RandomState(7); null = []
    real = sr(r)[0]
    for _ in range(300):
        Fp = F.copy(); Fp[COINS] = rng.permutation(F[COINS].values)
        null.append(sr(strategy(Fp, ret, k=2))[0])
    pct = (np.array(null) < real).mean() * 100
    print(f"  Sharpe real {real:+.2f}  vs percentil {pct:.0f}% de la nula (>=95 = señal real)")

    print("\n=== Sensibilidad a costos ===")
    for k in [1, 2, 3, 5]:
        s, a, dd = sr(strategy(F, ret, k=2, cost=0.0004 * k))
        print(f"  costo x{k} ({0.04*k:.2f}%/pata): Sharpe {s:+.2f}  annual {a:+.0f}%")

    print("\n=== Robustez por año ===")
    for y, g in r.groupby(r.index.year):
        if len(g) >= 30:
            s, a, dd = sr(g)
            print(f"  {y}: Sharpe {s:+.2f}  ret {a:+.0f}%  DD {dd:+.0f}%")


if __name__ == "__main__":
    main()

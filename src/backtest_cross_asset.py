"""
backtest_cross_asset.py — ¿la señal lead-lag equity(hoy)->EURUSD(manana) es alpha
operable o solo una correlacion bonita?

Senal: si el complejo de equity (US500/NAS100/GER40) subio HOY, ir LARGO EURUSD MANANA
(y corto si bajo). corr contemporanea EURUSD~-USDX, y equity(hoy)->USDX(manana)<0 =>
equity(hoy)->EURUSD(manana)>0 (risk-on -> dolar debil al dia siguiente).

Rigor:
  - costo por vuelta (spread EURUSD ~0.6 pip) aplicado en cada cambio de posicion.
  - metricas por ano (robustez) + Sharpe anualizado + PF + hit-rate.
  - walk-forward: umbral z ajustado en train, evaluado OOS.
  - NULL: 200 barajados de la senal -> percentil del Sharpe real (no casualidad?).
Solo LEE historico.
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

N_BARS = 3000
EQUITY = ["US500", "NAS100", "GER40"]
TARGET = "EURUSD"
COST_PIP = 0.00006          # ~0.6 pip vuelta redonda EURUSD (spread demo tipico)
RNG = np.random.default_rng(7)


def load_close(sym):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, N_BARS)
    if r is None or len(r) < 300:
        return None
    df = pd.DataFrame(r); df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
    return pd.Series(df["close"].values, index=df["date"], name=sym)


def metrics(daily):
    daily = daily[np.isfinite(daily)]
    if len(daily) < 30 or daily.std() == 0:
        return dict(sharpe=0, ret=0, pf=0, wr=0, n=len(daily))
    sharpe = daily.mean() / daily.std() * np.sqrt(252)
    gains = daily[daily > 0].sum(); losses = -daily[daily < 0].sum()
    pf = gains / losses if losses > 0 else float("inf")
    return dict(sharpe=sharpe, ret=daily.sum(), pf=pf,
                wr=(daily > 0).mean(), n=len(daily))


def main():
    ensure()
    cl = {}
    for s in EQUITY + [TARGET]:
        c = load_close(s)
        if c is not None:
            cl[s] = c
    df = pd.DataFrame(cl).dropna()
    rets = df.pct_change().dropna()
    print(f"Dias alineados: {len(rets)}  ({rets.index[0]} -> {rets.index[-1]})")

    # complejo de equity: media de retornos estandarizados (z) de los 3 indices
    eq_z = ((rets[EQUITY] - rets[EQUITY].mean()) / rets[EQUITY].std()).mean(axis=1)
    tgt = rets[TARGET]
    price = df[TARGET].mean()
    cost_ret = COST_PIP / price              # 0.6 pip / precio EURUSD ~1.08 = costo fraccional por vuelta

    def backtest(signal):
        pos = np.sign(signal)
        gross = pos.shift(1) * tgt
        turn = pos.diff().abs().fillna(pos.abs())
        net = (gross - turn.shift(1) * cost_ret).dropna()
        return net

    # --- 1) señal = sign(equity hoy), largo/corto EURUSD mañana ---
    net = backtest(eq_z)
    m = metrics(net)
    gross = (np.sign(eq_z).shift(1) * tgt).dropna()
    mg = metrics(gross)
    print(f"\n    (costo por vuelta = {cost_ret*100:.4f}% del precio)")
    print(f"[0] BRUTO (sin costos): Sharpe {mg['sharpe']:+.2f}  ret {mg['ret']*100:+.1f}%  "
          f"PF {mg['pf']:.2f}  wr {mg['wr']*100:.1f}%")
    print("\n[1] sign(equity hoy) -> EURUSD manana (con costos):")
    print(f"    Sharpe {m['sharpe']:+.2f}  ret {m['ret']*100:+.1f}%  PF {m['pf']:.2f}  "
          f"wr {m['wr']*100:.1f}%  n={m['n']}")

    print("\n    Por ano (robustez):")
    yrs = pd.Series(net.index).apply(lambda d: d.year).values
    for y in sorted(set(yrs)):
        my = metrics(net[yrs == y])
        print(f"      {y}: Sharpe {my['sharpe']:+.2f}  ret {my['ret']*100:+5.1f}%  "
              f"PF {my['pf']:.2f}  n={my['n']}")

    # --- 2) walk-forward con umbral z (solo operar señal fuerte) ---
    print("\n[2] Walk-forward umbral-z (train 504d -> test 126d):")
    oos = []
    idx = eq_z.index
    i = 504
    while i + 126 <= len(eq_z):
        tr = eq_z.iloc[i-504:i]
        best_thr, best_s = 0.0, -9
        for thr in [0, 0.25, 0.5, 0.75, 1.0]:
            sig = eq_z.iloc[i-504:i].where(eq_z.iloc[i-504:i].abs() > thr, 0)
            n = backtest(sig)
            n = n.iloc[-len(tr):]
            s = metrics(n)["sharpe"]
            if s > best_s:
                best_s, best_thr = s, thr
        te = eq_z.iloc[i:i+126]
        sig_te = eq_z.where(eq_z.abs() > best_thr, 0)
        n_te = backtest(sig_te)
        n_te = n_te.loc[te.index[0]:te.index[-1]]
        oos.append(n_te)
        i += 126
    if oos:
        oos_all = pd.concat(oos)
        mo = metrics(oos_all)
        print(f"    OOS combinado: Sharpe {mo['sharpe']:+.2f}  ret {mo['ret']*100:+.1f}%  "
              f"PF {mo['pf']:.2f}  wr {mo['wr']*100:.1f}%  n={mo['n']}")

    # --- 3) NULL: barajar la señal 200 veces -> percentil del Sharpe real ---
    real_s = m["sharpe"]
    null_s = []
    base = eq_z.copy()
    for _ in range(200):
        shuf = pd.Series(RNG.permutation(base.values), index=base.index)
        null_s.append(metrics(backtest(shuf))["sharpe"])
    null_s = np.array(null_s)
    pct = (null_s < real_s).mean() * 100
    print(f"\n[3] Test de nulidad (200 barajados): Sharpe real {real_s:+.2f} "
          f"vs null media {null_s.mean():+.2f} (sd {null_s.std():.2f})")
    print(f"    percentil del real = {pct:.0f}%  "
          f"({'PASA >95%' if pct > 95 else 'no supera el azar' if pct < 90 else 'marginal'})")


if __name__ == "__main__":
    main()

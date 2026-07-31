"""
orb_validate.py — validación de parámetros del candidato NAS100 M15 ORB (paso previo a
cualquier forward-test). Sobre orb_scalp.py añade:
  - VENTANA del OR: 1/2/3 barras (15/30/45 min de rango de apertura).
  - TARGET tipo Zarattini & Grossman: salida a T×riesgo (3R/5R/10R) o al cierre (EOD).
  - Split TRAIN/TEST cronológico (60/40): ¿el edge aguanta fuera de muestra?
  - Cross-check en M5 (~0.7a, muestra chica — solo sanidad).
  - Nulidad (dirección aleatoria) sobre la mejor config.
Solo LEE. Muestra corta (M15 ~2.2a) → esto sube/baja confianza, no "confirma".
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

US = {"open": (16, 30), "close": (23, 0)}
N_BARS = 50000
INF = float("inf")


def load(sym, tf):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, N_BARS)
    if r is None or len(r) < 1500:
        return None
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    df["date"] = df["time"].dt.date
    df["hm"] = df["time"].dt.hour * 60 + df["time"].dt.minute
    return df


def trades_orb(df, sess, or_bars, target_R, cost_pct, random_dir=False, rng=None):
    o0 = sess["open"][0]*60 + sess["open"][1]; c0 = sess["close"][0]*60 + sess["close"][1]
    d = df[(df["hm"] >= o0) & (df["hm"] < c0)]
    out = []
    for day, g in d.groupby("date"):
        g = g.sort_values("time")
        if len(g) < or_bars + 3:
            continue
        orb = g.iloc[:or_bars]
        or_hi, or_lo = orb["high"].max(), orb["low"].min()
        rng_sz = or_hi - or_lo
        if rng_sz <= 0:
            continue
        d_dir = 1 if orb.iloc[-1]["close"] >= orb.iloc[0]["open"] else -1
        if random_dir:
            d_dir = rng.choice([-1, 1])
        rest = g.iloc[or_bars:]
        entry = None; side = 0; ei = None
        for i, (_, b) in enumerate(rest.iterrows()):
            if d_dir == 1 and b["high"] > or_hi:
                entry, side, ei = or_hi, 1, i; break
            if d_dir == -1 and b["low"] < or_lo:
                entry, side, ei = or_lo, -1, i; break
        if entry is None:
            continue
        stop = or_lo if side == 1 else or_hi
        risk = abs(entry - stop)
        target = entry + side * target_R * risk if np.isfinite(target_R) else None
        exit_p = g.iloc[-1]["close"]
        for _, bb in rest.iloc[ei:].iterrows():
            hit_stop = (side == 1 and bb["low"] <= stop) or (side == -1 and bb["high"] >= stop)
            hit_tgt = target is not None and ((side == 1 and bb["high"] >= target) or
                                              (side == -1 and bb["low"] <= target))
            if hit_stop:                    # conservador: si stop y target en la misma barra, stop primero
                exit_p = stop; break
            if hit_tgt:
                exit_p = target; break
        pnl = side * (exit_p / entry - 1) * 100 - cost_pct
        out.append((day, pnl, (side*(exit_p-entry) - abs(entry)*cost_pct/100)/risk))
    return out


def stats(tr):
    if not tr or len(tr) < 15:
        return None
    p = np.array([t[1] for t in tr])
    eq = np.cumsum(p); dd = (eq - np.maximum.accumulate(eq)).min()
    w = p[p > 0].sum(); l = -p[p < 0].sum()
    return dict(n=len(tr), ret=eq[-1], dd=dd, wr=(p > 0).mean()*100,
                pf=(w/l if l > 0 else 9.99), sharpe=p.mean()/p.std()*np.sqrt(252) if p.std() > 0 else 0)


def main():
    ensure()
    sym = "NAS100"; cost = 0.01
    df = load(sym, mt5.TIMEFRAME_M15)
    print(f"=== Validación NAS100 M15 ORB ({len(df)} barras) ===")

    print("\n[1] Grid ventana-OR × target (Sharpe / PF):")
    print(f"    {'OR barras':<12}" + "".join(f"{'T='+str(t):>16}" for t in ['EOD', 3, 5, 10]))
    grids = {}
    for ob in [1, 2, 3]:
        row = f"    {str(ob)+' ('+str(ob*15)+'min)':<12}"
        for tR in [INF, 3, 5, 10]:
            st = stats(trades_orb(df, US, ob, tR, cost))
            grids[(ob, tR)] = st
            row += f"{st['sharpe']:+.2f}/{st['pf']:.2f}".rjust(16) if st else "n/a".rjust(16)
        print(row)

    # mejor config por Sharpe
    best = max(grids.items(), key=lambda kv: kv[1]["sharpe"] if kv[1] else -9)
    (ob, tR), bst = best
    tlbl = "EOD" if not np.isfinite(tR) else f"{int(tR)}R"
    print(f"\n[2] Mejor config: OR={ob} barra(s), target={tlbl}  "
          f"→ Sharpe {bst['sharpe']:+.2f}, PF {bst['pf']:.2f}, n={bst['n']}, wr {bst['wr']:.0f}%")

    # split train/test cronológico 60/40
    tr = trades_orb(df, US, ob, tR, cost)
    k = int(len(tr)*0.6)
    sin, sout = stats(tr[:k]), stats(tr[k:])
    print("\n[3] Split cronológico (¿aguanta fuera de muestra?):")
    print(f"    TRAIN (60%): Sharpe {sin['sharpe']:+.2f}  PF {sin['pf']:.2f}  ret {sin['ret']:+.1f}%  n={sin['n']}")
    print(f"    TEST  (40%): Sharpe {sout['sharpe']:+.2f}  PF {sout['pf']:.2f}  ret {sout['ret']:+.1f}%  n={sout['n']}")

    # nulidad direccion aleatoria sobre la mejor config
    rng = np.random.default_rng(4)
    rd = [stats(trades_orb(df, US, ob, tR, cost, random_dir=True, rng=rng))["sharpe"] for _ in range(50)]
    pctl = (np.array(rd) < bst["sharpe"]).mean()*100
    print(f"\n[4] Nulidad (50 dir-aleatorias): percentil {pctl:.0f}%  ({'PASA' if pctl>95 else 'no supera'})")

    # cross-check M5 (muestra chica)
    df5 = load(sym, mt5.TIMEFRAME_M5)
    if df5 is not None:
        st5 = stats(trades_orb(df5, US, ob*3, tR, cost))   # OR equivalente en minutos (M5×3 ≈ M15×1)
        if st5:
            print(f"\n[5] Cross-check M5 (~0.7a, OR={ob*3} barras≈mismos min, solo sanidad): "
                  f"Sharpe {st5['sharpe']:+.2f}  PF {st5['pf']:.2f}  n={st5['n']}")


if __name__ == "__main__":
    main()

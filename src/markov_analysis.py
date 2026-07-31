"""
markov_analysis.py — ¿una cadena de Markov sobre la señal aporta edge / reduce DD?

Una cadena de Markov de 1er orden modela P(estado_siguiente | estado_actual): pura
DEPENDENCIA SERIAL. Dos preguntas:

[A] DIRECCIÓN: discretizar retornos en estados (terciles: baja/plano/sube), estimar la
    matriz de transición en TRAIN y operar en TEST el signo de E[ret_siguiente | estado].
    Walk-forward + costos + test de nulidad (barajar). ¿Bate al azar? ¿Bate a b&h?
    Si el activo es ~random-walk → transiciones simétricas → sin edge (confirma Hurst≈0.5).
    OJO: donde nuestras reglas (RSI2 reversión / STF momentum) ya viven, Markov re-descubrirá
    esa misma dependencia fina → comparar si aporta MÁS que la regla hecha a mano.

[B] PERSISTENCIA DE RÉGIMEN (el ángulo útil): matriz de transición sobre los estados
    up/down → ¿los estados son PERSISTENTES (diagonal alta) o se revierten? La persistencia
    es lo que un overlay de Markov podría usar para asignar/tamaño (no dirección).
Solo LEE. Diagnóstico.
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

RNG = np.random.default_rng(11)
TFS = {"D1": mt5.TIMEFRAME_D1, "H4": mt5.TIMEFRAME_H4, "M30": mt5.TIMEFRAME_M30}


def load(sym, tf, n=8000):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 800:
        return None
    return pd.DataFrame(r)["close"].pct_change().dropna().values


def states_terciles(ret, q):
    """0=baja, 1=plano, 2=sube según terciles fijados en train."""
    return np.where(ret <= q[0], 0, np.where(ret <= q[1], 2, 1))


def markov_wf(ret, cost, k_states=3):
    """Walk-forward: E[ret_sig | estado] estimado en train, opera signo en test. Devuelve
    (ret_estrategia_por_barra alineado a test, hit-rate)."""
    n = len(ret)
    TRAIN = max(500, n // 4)
    out = np.full(n, np.nan)
    s = TRAIN
    STEP = 250
    while s < n:
        e = min(s + STEP, n)
        tr = ret[:s]
        q = np.quantile(tr, [1/3, 2/3])
        st = states_terciles(tr, q)
        # E[ret siguiente | estado actual]
        exp = np.zeros(k_states)
        for k in range(k_states):
            nxt = tr[1:][st[:-1] == k]
            exp[k] = nxt.mean() if len(nxt) > 5 else 0.0
        # test: estado en s-1..e-1 decide la posición para s..e
        st_te = states_terciles(ret[s-1:e-1], q)
        pos = np.sign(exp[st_te])
        out[s:e] = pos * ret[s:e] - np.abs(np.sign(pos)) * cost * (pos != 0)
        s = e
    m = np.isfinite(out)
    hit = np.mean(np.sign(out[m]) == 1) if m.sum() else 0
    return out[m], hit


def sharpe(r, ann):
    r = np.asarray(r, float)
    return r.mean() / r.std() * np.sqrt(ann) if len(r) > 1 and r.std() > 0 else 0.0


def transition_matrix(ret):
    """Matriz 2x2 up/down: P(sig | actual). Diagonal alta = persistencia (momentum);
    anti-diagonal alta = reversión."""
    s = (ret > 0).astype(int)
    T = np.zeros((2, 2))
    for a, b in zip(s[:-1], s[1:]):
        T[a, b] += 1
    T = T / np.maximum(T.sum(1, keepdims=True), 1)
    return T


def main():
    ensure()
    ann = {"D1": 252, "H4": 252*6, "M30": 252*13}
    universe = [("US500", "D1"), ("NAS100", "D1"), ("XAUUSD", "H4"),
                ("BTCUSD", "H4"), ("US500", "M30"), ("EURUSD", "D1")]
    print(f"{'activo':<16}{'n':>7}{'hit%':>7}{'Sharpe_mk':>11}{'Sharpe_bh':>11}"
          f"{'null_pctl':>11}{'veredicto':>14}")
    print("-" * 78)
    for sym, tfn in universe:
        ret = load(sym, TFS[tfn])
        if ret is None:
            print(f"{sym+'·'+tfn:<16} sin data"); continue
        info = mt5.symbol_info(sym)
        cost = (info.spread * info.point) / info.ask if info and info.ask else 0.0001
        mk, hit = markov_wf(ret, cost)
        sh = sharpe(mk, ann[tfn])
        # buy&hold sobre el mismo tramo test
        bh = ret[len(ret)-len(mk):]
        shbh = sharpe(bh, ann[tfn])
        # null: barajar retornos → romper cualquier estructura de Markov
        nulls = []
        for _ in range(50):
            rr = RNG.permutation(ret)
            mkn, _ = markov_wf(rr, cost)
            nulls.append(sharpe(mkn, ann[tfn]))
        pctl = (np.array(nulls) < sh).mean() * 100
        verd = "edge" if pctl > 95 and sh > 0 else ("azar" if pctl < 90 else "marginal")
        print(f"{sym+'·'+tfn:<16}{len(mk):>7}{hit*100:>7.1f}{sh:>+11.2f}{shbh:>+11.2f}"
              f"{pctl:>10.0f}%{verd:>14}", flush=True)

    print("\n[B] Matriz de transición up/down (persistencia vs reversión):")
    print(f"    {'activo':<16}{'P(up|up)':>10}{'P(up|dn)':>10}{'lectura':>26}")
    for sym, tfn in universe:
        ret = load(sym, TFS[tfn])
        if ret is None:
            continue
        T = transition_matrix(ret)
        puu, pud = T[1, 1], T[0, 1]
        gap = puu - pud
        rd = ("momentum (persiste)" if gap > 0.03 else
              "reversión (revierte)" if gap < -0.03 else "sin memoria (~random walk)")
        print(f"    {sym+'·'+tfn:<16}{puu:>10.3f}{pud:>10.3f}{rd:>26}")


if __name__ == "__main__":
    main()

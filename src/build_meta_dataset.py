"""
build_meta_dataset.py — dataset HISTÓRICO para el META-MODELO (estilo AlphaZero:
aprender de RESULTADOS qué edge validado paga en cada régimen).

A diferencia del aprendizaje vivo del bandit (que debe ir hacia adelante), esto se
construye del histórico sin lookahead por barra → miles de ejemplos de inmediato.

Por cada barra en que un edge validado EMITE su señal de entrada, registra:
   contexto (features + régimen) + edge + señal(-1/0/1)  →  recompensa contrafactual
donde reward = señal × (close[t+H] − close[t]) / ATR[t]  (en ATR = scale-free, comparable).

Edges (en su TF y símbolos nativos, con sus reglas validadas):
  - STF    : H4, oro/BTC/ETH   — Donchian55 + EMA200        (H=30 barras ≈ 5 días)
  - RSI(2) : D1, índices US/EU — RSI2<10 & close>SMA200      (H=5 barras)
(Zarattini/M30 se añade después: necesita la maquinaria de sesión.)

Salida: data/meta_dataset.csv. Solo LEE histórico.
"""
import os
import sys

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_master import classify, Params
from context_builder import build_context
from rsi2_meanrev import rsi
from intraday_cache import add_et, RTH_SLOTS
from paths import DATA_DIR, load_config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

META_CSV = os.path.join(DATA_DIR, "meta_dataset.csv")
N_BARS = 50000

EDGES = [
    {"edge": "STF", "tf_name": "H4", "tf": mt5.TIMEFRAME_H4, "H": 30,
     "symbols": ["XAUUSD", "BTCUSD", "ETHUSD"]},
    {"edge": "RSI2", "tf_name": "D1", "tf": mt5.TIMEFRAME_D1, "H": 5,
     "symbols": ["NAS100", "US500", "US30", "US2000", "FRA40"]},
]

# --- contexto CROSS-ASSET (intermarket): retorno del día PREVIO del complejo de equity
# (US500/NAS100/GER40, z-compuesto) y del dólar (USDX). Es la señal lead-lag validada en
# cross_asset.py/backtest_cross_asset.py: fina y no-estacionaria sola, pero como CONTEXTO
# deja que el meta-modelo decida cuándo pesa. Shift(1) = sin lookahead (valor ya cerrado).
_XA_EQUITY = ["US500", "NAS100", "GER40"]
_XA_CACHE = None


def cross_asset_daily():
    """DataFrame indexado por fecha con [eq, usdx] = retorno del complejo equity (z) y del
    USDX, ya desplazado 1 día (el valor conocido ANTES de la barra → sin lookahead)."""
    global _XA_CACHE
    if _XA_CACHE is not None:
        return _XA_CACHE
    ser = {}
    for s in _XA_EQUITY + ["USDX"]:
        mt5.symbol_select(s, True)
        r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_D1, 0, 6000)
        if r is None or len(r) < 300:
            continue
        df = pd.DataFrame(r); df["date"] = pd.to_datetime(df["time"], unit="s").dt.date
        ser[s] = pd.Series(df["close"].pct_change().values, index=df["date"])
    R = pd.DataFrame(ser).dropna(how="all")
    eq = R[_XA_EQUITY]
    eq_z = ((eq - eq.mean()) / eq.std()).mean(axis=1)          # complejo equity estandarizado
    xa = pd.DataFrame({"eq": eq_z, "usdx": R["USDX"]}).shift(1)  # día previo (conocido)
    _XA_CACHE = xa
    return xa


def align_xa(df):
    """Alinea [eq, usdx] cross-asset a las barras de df por fecha (ffill entre días)."""
    xa = cross_asset_daily()
    bd = pd.to_datetime(df["time"]).dt.date
    uni = pd.Index(sorted(set(xa.index) | set(bd)))
    xaf = xa.reindex(uni).ffill()
    return bd.map(xaf["eq"]).values, bd.map(xaf["usdx"]).values


# --- features de MARKOV sobre RÉGIMEN (persistencia + duración de racha, causal/sin lookahead).
# Idea: no predecir dirección (near-random-walk) sino dar al meta-modelo cuán PERSISTENTE/maduro
# es el régimen actual del regime_master → contexto de ASIGNACIÓN (pesar STF si el trend persiste).
# Nº de features cross-asset + markov que se anexan al ctx (para nombrar columnas y ablación).
EXTRA_COLS = ["xa_eq", "xa_usdx", "mk_runlen", "mk_persist"]


def markov_regime_features(fam):
    """Sobre la secuencia de familias de régimen: log(runlen) = barras seguidas en el mismo
    régimen; persist = P(seguir en el régimen actual) estimada SOLO con transiciones pasadas
    (matriz de transición online). Ambas causales (usables al entrar en t)."""
    fam = np.asarray(fam, dtype=object)
    labels = pd.unique(fam[pd.notna(fam)])
    uniq = {f: i for i, f in enumerate(labels)}          # solo etiqueta estados (no filtra valores futuros)
    K = max(len(uniq), 1)
    s = np.array([uniq.get(f, -1) for f in fam])
    n = len(s)
    runlen = np.zeros(n); persist = np.full(n, 0.5)
    cnt = np.zeros((K, K))
    for t in range(n):
        st = s[t]
        if t > 0 and s[t - 1] >= 0 and st >= 0:
            cnt[s[t - 1], st] += 1                        # transición realizada hasta t (conocida)
        if st >= 0:
            runlen[t] = runlen[t - 1] + 1 if (t > 0 and s[t - 1] == st) else 1
            tot = cnt[st].sum()
            persist[t] = cnt[st, st] / tot if tot > 0 else 0.5
    return np.log1p(runlen), persist


def stf_signal(o, h, l, c):
    """Señal STF por barra: +1 largo si close>EMA200 y rompe Donchian55 alto;
    -1 corto si close<EMA200 y rompe Donchian55 bajo; 0 si no hay ruptura."""
    ema = pd.Series(c).ewm(span=200, adjust=False).mean().values
    dhi = pd.Series(h).rolling(55).max().shift(1).values
    dlo = pd.Series(l).rolling(55).min().shift(1).values
    sig = np.zeros(len(c))
    sig[(c > ema) & (c > dhi)] = 1
    sig[(c < ema) & (c < dlo)] = -1
    return sig


def rsi2_signal(c):
    """Señal RSI(2) por barra: +1 (solo largos) si close>SMA200 y RSI2<10; 0 si no."""
    sma = pd.Series(c).rolling(200).mean().values
    r2 = rsi(c, 2)
    sig = np.zeros(len(c))
    sig[(c > sma) & (r2 < 10.0)] = 1
    return sig


def collect_zarattini(sym, N, features, rows, H=6, lookback=14):
    """Edge Zarattini (M30): banda de ruido por slot de sesión; señal al romperla.
    reward = señal × (close[t+H]−close[t])/ATR (H≈6 barras M30 ≈ 3h, intradía)."""
    ensure(); mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M30, 0, N_BARS)
    if r is None or len(r) < 700:
        return 0
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    c = df["close"].values
    X = build_features(df.copy(), features)
    dfi = compute_indicators(df.copy())
    reg = classify(dfi, Params())
    atr = dfi["atr"].values

    d = add_et(df)
    smap = {s: i for i, s in enumerate(RTH_SLOTS)}
    d["slot"] = d["hm"].map(smap)
    open0 = d[d["slot"] == 0].groupby("date")["open"].first()
    d["day_open"] = d["date"].map(open0)
    d["cum"] = d["close"] / d["day_open"] - 1.0
    d["abscum"] = d["cum"].abs()
    move = np.full(len(d), np.nan)
    for sl in range(len(RTH_SLOTS)):                      # Move(t)=media 14 días del |cum| en ese slot
        m = (d["slot"] == sl).values
        move[m] = pd.Series(d["abscum"].values[m]).rolling(lookback).mean().shift(1).values
    cum = d["cum"].values; slot = d["slot"].values
    xa_e, xa_u = align_xa(df)
    mk_run, mk_per = markov_regime_features(reg["family"].values)

    Xv = X.values; n = len(c); warm = 260; added = 0
    for t in range(warm, n - H):
        sl = slot[t]
        if not (np.isfinite(sl) and 1 <= sl <= 11):        # entradas en slots 1..11
            continue
        mv = move[t]
        if not (np.isfinite(mv) and mv > 0 and np.isfinite(atr[t]) and atr[t] > 0):
            continue
        ub = N * mv; cu = cum[t]
        sig = 1 if cu > ub else (-1 if cu < -ub else 0)
        if sig == 0:
            continue
        rr = reg.iloc[t]
        if not np.isfinite(rr["id"]) or not np.isfinite(Xv[t]).all():
            continue
        try:
            ctx = build_context(X.iloc[t], rr)
        except Exception:
            continue
        if not np.isfinite(np.asarray(ctx, float)).all():
            continue
        reward = float(sig) * (c[t + H] - c[t]) / (atr[t] * np.sqrt(H))   # ÷√H: comparable por unidad de tiempo
        rows.append([
            df["time"].iloc[t].isoformat(), sym, "M30", "Zarattini", int(sig), round(reward, 4),
            int(rr["id"]), str(rr["family"]), round(float(rr["knn_edge"]), 3),
            round(float(rr["confidence"]), 3),
        ] + [round(float(x), 5) for x in np.asarray(ctx, float)]
          + [round(float(xa_e[t]) if np.isfinite(xa_e[t]) else 0.0, 5),
             round(float(xa_u[t]) if np.isfinite(xa_u[t]) else 0.0, 5),
             round(float(mk_run[t]), 5), round(float(mk_per[t]), 5)])
        added += 1
    return added


def collect(edge_cfg, sym, features, rows):
    ensure(); mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, edge_cfg["tf"], 0, N_BARS)
    if r is None or len(r) < 700:
        return 0
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    o, h, l, c = (df["open"].values, df["high"].values,
                  df["low"].values, df["close"].values)

    X = build_features(df.copy(), features)                 # contexto (features)
    dfi = compute_indicators(df.copy())
    reg = classify(dfi, Params())                           # régimen por barra
    atr = dfi["atr"].values

    sig = stf_signal(o, h, l, c) if edge_cfg["edge"] == "STF" else rsi2_signal(c)
    H = edge_cfg["H"]; n = len(c); warm = 260; added = 0
    xa_e, xa_u = align_xa(df)
    mk_run, mk_per = markov_regime_features(reg["family"].values)

    Xv = X.values
    for t in range(warm, n - H):
        if sig[t] == 0 or not (np.isfinite(atr[t]) and atr[t] > 0):
            continue
        rr = reg.iloc[t]
        if not np.isfinite(rr["id"]) or not np.isfinite(Xv[t]).all():
            continue
        try:
            ctx = build_context(X.iloc[t], rr)
        except Exception:
            continue
        if not np.isfinite(np.asarray(ctx, float)).all():
            continue
        reward = float(sig[t]) * (c[t + H] - c[t]) / (atr[t] * np.sqrt(H))   # ÷√H: comparable por unidad de tiempo
        rows.append([
            df["time"].iloc[t].isoformat(), sym, edge_cfg["tf_name"], edge_cfg["edge"],
            int(sig[t]), round(reward, 4),
            int(rr["id"]), str(rr["family"]), round(float(rr["knn_edge"]), 3),
            round(float(rr["confidence"]), 3),
        ] + [round(float(x), 5) for x in np.asarray(ctx, float)]
          + [round(float(xa_e[t]) if np.isfinite(xa_e[t]) else 0.0, 5),
             round(float(xa_u[t]) if np.isfinite(xa_u[t]) else 0.0, 5),
             round(float(mk_run[t]), 5), round(float(mk_per[t]), 5)])
        added += 1
    return added


def main():
    ensure()
    features = load_config()["features"]
    rows = []
    ncols = None
    print("Construyendo meta_dataset (histórico, sin lookahead)…")
    for e in EDGES:
        for sym in e["symbols"]:
            a = collect(e, sym, features, rows)
            print(f"  {e['edge']:5} {sym:8} {e['tf_name']}: +{a} filas")
    # Zarattini (M30, N validado por símbolo del bloque intraday)
    zc = load_config().get("intraday", {}).get("symbols", {})
    for sym in ["US500", "NAS100", "US30"]:
        a = collect_zarattini(sym, float(zc.get(sym, 1.0)), features, rows)
        print(f"  Zarat {sym:8} M30: +{a} filas")
    if not rows:
        print("sin filas"); return
    nctx_total = len(rows[0]) - 10
    n_bc = nctx_total - len(EXTRA_COLS)          # ctx de build_context (lo que meta_observer SÍ computa en vivo)
    header = (["time", "symbol", "timeframe", "edge", "signal", "reward",
               "regime_id", "family", "knn_edge", "confidence"]
              + [f"ctx_{i}" for i in range(n_bc)] + EXTRA_COLS)
    out = pd.DataFrame(rows, columns=header)
    out.to_csv(META_CSV, index=False)
    print(f"\n{len(out)} filas → {META_CSV}")
    print("\nResumen por edge (recompensa media de sus apuestas, en ATR):")
    g = out.groupby("edge")["reward"].agg(["count", "mean"])
    for edge, row in g.iterrows():
        print(f"  {edge:6} n={int(row['count']):>5}  reward_medio={row['mean']:+.3f} ATR")


if __name__ == "__main__":
    main()

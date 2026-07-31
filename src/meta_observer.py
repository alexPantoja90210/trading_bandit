"""
meta_observer.py — forward-test EN VIVO del meta-modelo (paso c).

NO manda órdenes: por cada señal de un edge (STF/RSI2) en su barra cerrada, aplica
el meta-modelo (`data/meta_model.json`) para decidir tomar/saltar, y encola el
resultado diferido. Al madurar registra la recompensa a `data/meta_forward.csv`
con la decisión, para comparar EN VIVO "meta (pred>0)" vs "1/N (tomar todo)".

Edges: STF (H4, oro/BTC/ETH) y RSI(2) (D1, índices). Zarattini se añade después.
Baja frecuencia → acumula lento (STF/RSI2 maduran en días). Kill: data/meta_command.json.
"""
import os
import sys
import json
import time
from collections import deque

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from paths import DATA_DIR, load_config
from mt5_connect import ensure
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_master import classify, Params
from context_builder import build_context
from build_meta_dataset import stf_signal, rsi2_signal

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MODEL_FILE = os.path.join(DATA_DIR, "meta_model.json")
FWD_CSV = os.path.join(DATA_DIR, "meta_forward.csv")
PEND_FILE = os.path.join(DATA_DIR, "meta_obs_pending.json")
CMD_FILE = os.path.join(DATA_DIR, "meta_command.json")

EDGES = [
    {"edge": "STF", "tf": mt5.TIMEFRAME_H4, "H": 30, "sig": "stf",
     "symbols": ["XAUUSD", "BTCUSD", "ETHUSD"]},
    {"edge": "RSI2", "tf": mt5.TIMEFRAME_D1, "H": 5, "sig": "rsi2",
     "symbols": ["NAS100", "US500", "US30", "US2000", "FRA40"]},
]


def load_model():
    with open(MODEL_FILE, encoding="utf-8") as f:
        return json.load(f)


def meta_predict(model, ctx_vec, edge):
    idx = [int(c.split("_")[1]) for c in model["ctx_cols"]]
    feats = [float(ctx_vec[i]) for i in idx]
    edge_oh = [1.0 if edge == e else 0.0 for e in model["edges"]]
    x = np.array(feats + edge_oh, dtype=float)
    xs = (x - np.array(model["mu"])) / np.array(model["sd"])
    return float(np.dot(model["weights"], np.concatenate([[1.0], xs])))


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, default=str)
    except Exception:
        pass


def append_row(row):
    new = not os.path.exists(FWD_CSV)
    with open(FWD_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write("ts,edge,symbol,signal,meta_pred,took,H,reward\n")
        f.write("{ts},{edge},{symbol},{signal},{meta_pred},{took},{H},{reward}\n".format(**row))


def process_edge(e, sym, model, pending, features):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, e["tf"], 0, 2500)
    if r is None or len(r) < 700:
        return 0
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    c = df["close"].values; H = e["H"]
    X = build_features(df.copy(), features)
    dfi = compute_indicators(df.copy())
    atr = dfi["atr"].values
    sig = stf_signal(df["open"].values, df["high"].values, df["low"].values, c) \
        if e["sig"] == "stf" else rsi2_signal(c)

    key = f"{e['edge']}:{sym}"
    dq = pending.setdefault(key, deque())
    n = len(c); i = n - 2                      # última barra CERRADA
    written = 0

    # madurar (barras suficientes después del bar encolado)
    times = df["time"].values
    while dq and (df["time"] > pd.Timestamp(dq[0]["bar_time"])).sum() >= H:
        d = dq.popleft()
        m = df.index[df["time"] == pd.Timestamp(d["bar_time"])]
        if len(m) == 0:
            continue
        pos = df.index.get_loc(m[0]); tgt = pos + H
        if tgt >= n:
            continue
        reward = d["signal"] * (c[tgt] - d["entry_close"]) / (d["entry_atr"] * np.sqrt(H))
        append_row({"ts": pd.Timestamp.utcnow().isoformat(), "edge": e["edge"], "symbol": sym,
                    "signal": int(d["signal"]), "meta_pred": round(d["meta_pred"], 4),
                    "took": int(d["meta_pred"] > 0), "H": H, "reward": round(float(reward), 4)})
        written += 1

    # encolar la señal de la barra cerrada (una por barra)
    bar_time = str(df["time"].iloc[i])
    if sig[i] != 0 and np.isfinite(atr[i]) and atr[i] > 0 and bar_time != dq_last_bar(dq, key, pending):
        reg = classify(dfi, Params()).iloc[i]
        if np.isfinite(reg["id"]) and np.isfinite(X.iloc[i].values).all():
            try:
                ctx = build_context(X.iloc[i], reg)
                pred = meta_predict(model, np.asarray(ctx, float), e["edge"])
                dq.append({"bar_time": bar_time, "signal": int(sig[i]),
                           "entry_close": float(c[i]), "entry_atr": float(atr[i]),
                           "meta_pred": pred})
                pending[key + "_last"] = bar_time
            except Exception:
                pass
    return written


def dq_last_bar(dq, key, pending):
    return pending.get(key + "_last")


def main():
    cfg = load_config()
    if not ensure(cfg):
        print("sin conexión demo"); return
    if not os.path.exists(MODEL_FILE):
        print("falta meta_model.json — corre train_meta_model.py"); return
    model = load_model()
    features = cfg["features"]
    print(f"=== META-OBSERVER (forward-test) — edges STF/RSI2, {len(model['ctx_cols'])} ctx ===")
    once = "--once" in sys.argv
    pending = _load(PEND_FILE, {})
    pending = {k: (deque(v) if isinstance(v, list) else v) for k, v in pending.items()}
    while True:
        if _load(CMD_FILE, {}).get("stop"):
            print("STOP"); break
        if not ensure(cfg):
            time.sleep(120); continue
        try:                              # recarga el modelo (toma el reentrenado, bucle b)
            model = load_model()
        except Exception:
            pass
        total = 0
        for e in EDGES:
            for sym in e["symbols"]:
                try:
                    total += process_edge(e, sym, model, pending, features)
                except Exception as ex:
                    print(f"  {e['edge']}:{sym} err {ex}")
        _save(PEND_FILE, {k: (list(v) if isinstance(v, deque) else v) for k, v in pending.items()})
        if total:
            print(f"[{pd.Timestamp.utcnow():%H:%M:%S}] +{total} resultados madurados → meta_forward.csv")
        if once:
            break
        time.sleep(cfg.get("collector", {}).get("sleep", 300))


if __name__ == "__main__":
    main()

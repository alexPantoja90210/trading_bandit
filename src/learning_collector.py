"""
Colector de datos de aprendizaje MULTI-SÍMBOLO con recompensa contrafactual.

Genera el dataset de entrenamiento (`data/learning_dataset.csv`) de forma
DESACOPLADA del trading: no manda órdenes, solo LEE mercado y registra ejemplos.

Por cada barra nueva de cada símbolo:
  1. calcula el contexto (features + régimen codificado) = input del modelo,
  2. calcula la dirección implícita de los 5 brazos,
  3. encola la decisión (recompensa diferida).
Al madurar (H barras después), conociendo el precio futuro, calcula la recompensa
CONTRAFACTUAL de CADA brazo (no solo el elegido) → información completa, ideal para
entrenar "qué brazo gana en qué condición". Multi-símbolo → comparable entre pares
(la recompensa está en unidades de ATR, scale-free).

Config: bloque `collector` (symbols, timeframe, reward_horizon, sleep). Kill switch:
`{"stop":true}` en data/collector_command.json.
"""
import os
import sys
import json
import time
from collections import deque

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from paths import load_config, DATA_DIR
from mt5_connect import ensure
from feature_engineering import build_features
from reward_engine import compute_indicators
from regime_engine import compute_regime
from context_builder import build_context
from recorder import record_learning_row

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ARM_NAMES = ["trend", "mean", "flat", "momentum", "volatility"]
N_ARMS = 5
PENDING_FILE = os.path.join(DATA_DIR, "collector_pending.json")
COMMAND_FILE = os.path.join(DATA_DIR, "collector_command.json")
STATUS_FILE = os.path.join(DATA_DIR, "collector_status.json")
_TF = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
       "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
       "D1": mt5.TIMEFRAME_D1}


def implied_direction(arm, df, regime_row):
    """Misma lógica que el bandit (main_live_v2): +1 compra, -1 vende, 0 flat."""
    family = str(regime_row["family"])
    if arm in (0, 3):        # trend / momentum → a favor de la tendencia
        return -1 if family == "TREND_DOWN" else 1
    if arm == 1:             # mean → reversión contra el desvío de la sma20
        return -1 if df["close"].iloc[-1] > df["sma20"].iloc[-1] else 1
    if arm == 4:             # volatility → dirección de la última barra
        return 1 if df["close"].iloc[-1] > df["close"].iloc[-2] else -1
    return 0                 # flat


def bars_after(df, bar_time):
    return int((df["time"] > bar_time).sum())


def close_after_horizon(df, bar_time, horizon):
    matches = df.index[df["time"] == bar_time]
    if len(matches) == 0:
        return None
    pos = df.index.get_loc(matches[0])
    target = pos + horizon
    return float(df["close"].iloc[target]) if target < len(df) else None


def _save_pending(pending):
    try:
        data = {}
        for sym, dq in pending.items():
            data[sym] = [{
                "bar_time": str(d["bar_time"]),
                "context": np.asarray(d["context"]).tolist(),
                "dirs": [int(x) for x in d["dirs"]],
                "entry_close": float(d["entry_close"]),
                "entry_atr": float(d["entry_atr"]),
                "regime_id": d["regime_id"], "family": d["family"],
                "knn_edge": d["knn_edge"], "confidence": d["confidence"],
            } for d in dq]
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_pending():
    try:
        with open(PENDING_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        out = {}
        for sym, lst in raw.items():
            dq = deque()
            for d in lst:
                dq.append({
                    "bar_time": pd.Timestamp(d["bar_time"]),
                    "context": np.array(d["context"], dtype=float),
                    "dirs": d["dirs"], "entry_close": d["entry_close"],
                    "entry_atr": d["entry_atr"], "regime_id": d["regime_id"],
                    "family": d["family"], "knn_edge": d["knn_edge"],
                    "confidence": d["confidence"],
                })
            out[sym] = dq
        return out
    except Exception:
        return {}


def load_ohlc(sym, tf, n=2500):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 700:
        return None
    df = pd.DataFrame(r)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def process_symbol(sym, tf_name, cfg, pending, last_bar):
    """Madura decisiones (registra los 5 brazos) y encola una nueva. Devuelve last_bar."""
    features = cfg["features"]
    H = int(cfg["collector"].get("reward_horizon", cfg.get("reward_horizon", 20)))
    df = load_ohlc(sym, _TF[tf_name])
    if df is None:
        return last_bar, 0
    X = build_features(df, features)
    feats_row = X.iloc[-1]
    df = compute_indicators(df)
    if df["atr"].iloc[-1] != df["atr"].iloc[-1] or df["atr"].iloc[-1] <= 0:
        return last_bar, 0
    regime_row = compute_regime(df)
    context = build_context(feats_row, regime_row)
    if not np.isfinite(np.asarray(context, dtype=float)).all():
        return last_bar, 0

    dq = pending.setdefault(sym, deque())
    written = 0

    # 1) madurar: recompensa contrafactual de los 5 brazos
    while dq and bars_after(df, dq[0]["bar_time"]) >= H:
        d = dq.popleft()
        exit_close = close_after_horizon(df, d["bar_time"], H)
        if exit_close is None:
            continue
        move = (exit_close - d["entry_close"]) / d["entry_atr"]   # en ATR (scale-free)
        for a in range(N_ARMS):
            reward = d["dirs"][a] * move                          # dir=0 (flat) → reward 0
            row = {"context": d["context"], "arm": a, "arm_name": ARM_NAMES[a],
                   "direction": d["dirs"][a], "regime_id": d["regime_id"],
                   "family": d["family"], "knn_edge": d["knn_edge"],
                   "confidence": d["confidence"]}
            record_learning_row(sym, row, reward, tf=tf_name)
            written += 1

    # 2) encolar una decisión por barra nueva
    bar_time = df["time"].iloc[-1]
    if bar_time != last_bar:
        last_bar = bar_time
        dirs = [implied_direction(a, df, regime_row) for a in range(N_ARMS)]
        rid = regime_row["id"]
        dq.append({
            "bar_time": bar_time, "context": np.asarray(context, dtype=float),
            "dirs": dirs, "entry_close": float(df["close"].iloc[-1]),
            "entry_atr": float(df["atr"].iloc[-1]),
            "regime_id": int(rid) if rid == rid else -1,
            "family": str(regime_row["family"]),
            "knn_edge": round(float(regime_row["knn_edge"]), 3),
            "confidence": round(float(regime_row["confidence"]), 3),
        })
    return last_bar, written


def main():
    cfg = load_config()
    if not cfg.get("collector", {}).get("enabled", False):
        print("collector.enabled=false"); return
    if not ensure(cfg):
        print("sin conexión demo"); return
    symbols = cfg["collector"]["symbols"]
    tf_name = cfg["collector"].get("timeframe", "M5")
    print(f"=== LEARNING COLLECTOR — {symbols} {tf_name}, recompensa contrafactual de "
          f"{N_ARMS} brazos ===")
    pending = _load_pending()
    last_bar = {s: None for s in symbols}
    once = "--once" in sys.argv
    while True:
        cmd = {}
        try:
            with open(COMMAND_FILE, encoding="utf-8") as f:
                cmd = json.load(f)
        except Exception:
            pass
        if cmd.get("stop"):
            print("STOP — saliendo"); break
        cfg = load_config()
        if not ensure(cfg):
            time.sleep(cfg["collector"].get("sleep", 60)); continue
        total = 0
        for s in cfg["collector"]["symbols"]:
            try:
                last_bar[s], w = process_symbol(s, tf_name, cfg, pending, last_bar.get(s))
                total += w
            except Exception as e:
                print(f"  {s}: error {e}")
        _save_pending(pending)
        try:
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump({"symbols": list(pending.keys()),
                           "pending": {s: len(dq) for s, dq in pending.items()},
                           "rows_last_pass": total}, f, indent=2, default=str)
        except Exception:
            pass
        if total:
            print(f"[{pd.Timestamp.utcnow():%H:%M:%S}] +{total} filas al dataset")
        if once:
            break
        time.sleep(cfg["collector"].get("sleep", 60))


if __name__ == "__main__":
    main()

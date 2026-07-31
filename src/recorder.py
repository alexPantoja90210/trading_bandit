"""
Persistencia de datos para el dashboard.

El loop de trading llama estas funciones para escribir equity.csv, rewards.csv
y bandit_state.json en DATA_DIR. El dashboard lee esos mismos archivos.
"""
import os
import csv
import json
import time
from collections import deque

import numpy as np

from paths import (EQUITY_CSV, REWARDS_CSV, BANDIT_STATE, STATUS_FILE,
                   COMMAND_FILE, PENDING_FILE, LEARNING_CSV)

EQUITY_HEADER = ["time", "equity"]
REWARDS_HEADER = ["time", "arm", "arm_name", "reward"]


def _append_row(path, header, row):
    """Agrega una fila al CSV, escribiendo la cabecera si el archivo es nuevo."""
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(header)
        w.writerow(row)


def record_equity(equity, ts=None):
    ts = ts if ts is not None else time.time()
    _append_row(EQUITY_CSV, EQUITY_HEADER, [ts, float(equity)])


def record_reward(arm, arm_name, reward, ts=None):
    ts = ts if ts is not None else time.time()
    _append_row(REWARDS_CSV, REWARDS_HEADER, [ts, int(arm), arm_name, float(reward)])


def record_learning_row(symbol, d, reward, tf="", ts=None):
    """Escribe una fila del DATASET DE ENTRENAMIENTO.

    Cada ejemplo = condiciones (vector de contexto = features + régimen codificado,
    el input exacto del modelo) + régimen interpretable + brazo/dirección → RECOMPENSA.
    Lo genera learning_collector.py: multi-símbolo + recompensa contrafactual de los 5
    brazos = información completa. Sirve para regresión de recompensa, clasificación del
    mejor brazo u offline-RL.
    """
    ts = ts if ts is not None else time.time()
    ctx = list(np.asarray(d.get("context", []), dtype=float).ravel())
    header = (["time", "symbol", "timeframe", "arm", "arm_name", "direction", "reward",
               "regime_id", "family", "knn_edge", "confidence"]
              + [f"ctx_{i}" for i in range(len(ctx))])
    row = ([ts, symbol, tf, int(d["arm"]), d["arm_name"], int(d["direction"]), float(reward),
            d.get("regime_id", ""), d.get("family", ""), d.get("knn_edge", ""),
            d.get("confidence", "")] + ctx)
    _append_row(LEARNING_CSV, header, row)


def save_bandit_state(bandit):
    """Guarda mu, sigma y theta del bandit como JSON."""
    state = {
        "mu": np.asarray(bandit.mu).tolist(),
        "sigma": np.asarray(bandit.sigma).tolist(),
        "theta": np.asarray(bandit.theta).tolist(),
    }
    with open(BANDIT_STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def save_status(status: dict):
    """Guarda el estado en vivo del bot (contadores) para el dashboard."""
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f)
    except Exception:
        pass


def load_status():
    """Lee el status.json (o {} si no existe / está corrupto)."""
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_command(cmd: dict):
    """El dashboard escribe comandos para el bot (fusiona con los existentes)."""
    try:
        existing = load_command()
        existing.update(cmd)
        with open(COMMAND_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f)
    except Exception:
        pass


def load_command():
    """El bot lee los comandos pendientes del dashboard."""
    try:
        with open(COMMAND_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_pending(pending):
    """Persiste la cola de decisiones diferidas (sobrevive a paros/reinicios)."""
    try:
        data = []
        for d in pending:
            bt = d["bar_time"]
            data.append({
                "bar_time": bt.isoformat() if hasattr(bt, "isoformat") else str(bt),
                "context": np.asarray(d["context"]).tolist(),
                "arm": int(d["arm"]),
                "arm_name": d["arm_name"],
                "direction": int(d["direction"]),
                "entry_close": float(d["entry_close"]),
                "entry_atr": float(d["entry_atr"]),
                "regime_id": d.get("regime_id", -1),
                "family": d.get("family", ""),
                "knn_edge": d.get("knn_edge", 0.0),
                "confidence": d.get("confidence", 0.0),
            })
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_pending():
    """Carga la cola de decisiones diferidas guardada (o vacía si no hay)."""
    try:
        import pandas as pd
        with open(PENDING_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        out = deque()
        for d in raw:
            out.append({
                "bar_time": pd.Timestamp(d["bar_time"]),
                "context": np.array(d["context"], dtype=float),
                "arm": int(d["arm"]),
                "arm_name": d["arm_name"],
                "direction": int(d["direction"]),
                "entry_close": float(d["entry_close"]),
                "entry_atr": float(d["entry_atr"]),
                "regime_id": d.get("regime_id", -1),
                "family": d.get("family", ""),
                "knn_edge": d.get("knn_edge", 0.0),
                "confidence": d.get("confidence", 0.0),
            })
        return out
    except Exception:
        return deque()

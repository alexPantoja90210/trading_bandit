"""
Cache y utilidades de datos intradía (M30) para el laboratorio intradía.

MT5 solo guarda una ventana rodante (~50k barras M30 ≈ 4.2 años). Este módulo
baja M30 y lo apila (dedup) a data/intraday/<sym>_M30.csv, para ir acumulando
historia más allá de lo que da el bróker. Reutilizable por los tests intradía.

Sesión cash EEUU 9:30–16:00 ET = 13 barras M30. Validado empíricamente que el
servidor del bróker = ET + 7h (pico de vol/volumen en 16:30 bróker = 9:30 ET).
"""
import os
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from paths import DATA_DIR

ET_SHIFT_H = -7                    # servidor → ET (validado)
CACHE_DIR = os.path.join(DATA_DIR, "intraday")
_COLS = ["time", "open", "high", "low", "close", "tick_volume"]


def load_m30(sym, n=50000):
    """Pull M30 + merge/dedup a CSV. Devuelve (df, path, n_barras)."""
    ensure(); mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M30, 0, n)
    live = pd.DataFrame(r) if r is not None else pd.DataFrame()
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{sym}_M30.csv")
    if os.path.exists(path):
        old = pd.read_csv(path)
        parts = [old[_COLS]] + ([live[_COLS]] if len(live) else [])
        both = pd.concat(parts, ignore_index=True)
    else:
        both = live[_COLS] if len(live) else live
    if len(both) == 0:
        return None, path, 0
    both = both.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    both.to_csv(path, index=False)
    return both, path, len(both)


def add_et(df):
    """Añade columnas ET: dt (hora ET), date (fecha ET), hm ('HH:MM')."""
    d = df.copy()
    d["dt"] = pd.to_datetime(d["time"], unit="s") + pd.Timedelta(hours=ET_SHIFT_H)
    d["date"] = d["dt"].dt.date
    d["hm"] = d["dt"].dt.strftime("%H:%M")
    return d


# 13 slots RTH (open time de cada barra M30, en ET)
RTH_SLOTS = ["09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
             "13:00", "13:30", "14:00", "14:30", "15:00", "15:30"]

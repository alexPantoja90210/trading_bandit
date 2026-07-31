"""
Observador PAPEL del Smart Trend Follower (oro + BTC, H4).

Replica la mecánica exacta (Donchian 55 + EMA200 + stop 2.5xATR + trailing
Chandelier 3.0xATR + flip) SIN órdenes reales. Procesa cada barra H4 nueva que
cierra (el trailing es stateful). Forward-test en vivo, arranca en plano.
Solo LEE datos e imprime eventos.
"""
import sys
import os
import json
from datetime import datetime

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from smart_trend_follower import (atr_series, EMA_LEN, DONCHIAN, ATR_LEN,
                                  INIT_STOP, CHANDELIER, FLIP)
from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE = os.path.join(DATA_DIR, "stf_paper.json")
TRADES = os.path.join(DATA_DIR, "stf_paper_trades.csv")
STATUS = os.path.join(DATA_DIR, "stf_paper_status.json")
SYMBOLS = ["XAUUSD", "BTCUSD"]
NB = 800


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _log_trade(sym, st, exit_price, exit_date, reason, R):
    new = not os.path.exists(TRADES)
    with open(TRADES, "a", encoding="utf-8") as f:
        if new:
            f.write("symbol,side,entry_date,exit_date,entry,exit,R,reason\n")
        side = "LONG" if st["side"] == 1 else "SHORT"
        f.write(f"{sym},{side},{st['entry_date']},{exit_date},{st['entry']:.2f},"
                f"{exit_price:.2f},{R:.2f},{reason}\n")


def process_symbol(sym, state, status):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, NB)
    if r is None or len(r) < EMA_LEN + DONCHIAN + 5:
        return
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    ema = pd.Series(close).ewm(span=EMA_LEN, adjust=False).mean().values
    atr = atr_series(high, low, close, ATR_LEN)
    dhi = pd.Series(high).rolling(DONCHIAN).max().shift(1).values
    dlo = pd.Series(low).rolling(DONCHIAN).min().shift(1).values
    t = df["time"].astype("int64").values // 10**9   # epoch s
    n = len(df)
    closed_last = n - 2   # última barra CERRADA (la n-1 está formándose)

    st = state.get(sym)
    if st is None:
        # primera vez: arrancar en plano desde la última barra cerrada
        st = {"last_bar": int(t[closed_last]), "in_pos": False}
        state[sym] = st

    def enter(i, side):
        oneR = INIT_STOP * atr[i]
        st.update({"in_pos": True, "side": side, "entry": float(close[i]),
                   "entry_date": str(df["time"].iloc[i]), "oneR": float(oneR),
                   "ext": float(high[i] if side == 1 else low[i]),
                   "stop": float(close[i] - oneR if side == 1 else close[i] + oneR)})
        print(f"[{df['time'].iloc[i]}] ENTRY {sym} {'LONG' if side==1 else 'SHORT'} "
              f"@ {close[i]:.2f}  (1R={oneR:.2f})")

    def close_pos(i, exit_price, reason):
        R = ((exit_price - st["entry"]) if st["side"] == 1 else (st["entry"] - exit_price)) / st["oneR"]
        print(f"[{df['time'].iloc[i]}] EXIT  {sym} @ {exit_price:.2f}  R={R:+.2f}  ({reason})")
        _log_trade(sym, st, exit_price, str(df["time"].iloc[i]), reason, R)
        st.update({"in_pos": False})

    # procesar barras cerradas nuevas en orden
    for i in range(0, closed_last + 1):
        if t[i] <= st["last_bar"]:
            continue
        if not (np.isfinite(ema[i]) and np.isfinite(atr[i]) and np.isfinite(dhi[i]) and np.isfinite(dlo[i])):
            st["last_bar"] = int(t[i]); continue
        # 1) gestionar posición: trailing + salida por stop
        if st["in_pos"]:
            if st["side"] == 1:
                st["ext"] = max(st["ext"], float(high[i]))
                st["stop"] = max(st["stop"], st["ext"] - CHANDELIER * atr[i])
                if low[i] <= st["stop"]:
                    close_pos(i, st["stop"], "trailing")
            else:
                st["ext"] = min(st["ext"], float(low[i]))
                st["stop"] = min(st["stop"], st["ext"] + CHANDELIER * atr[i])
                if high[i] >= st["stop"]:
                    close_pos(i, st["stop"], "trailing")
        # 2) señales (vela cerrada)
        long_sig = close[i] > dhi[i] and close[i] > ema[i]
        short_sig = close[i] < dlo[i] and close[i] < ema[i]
        if not st["in_pos"]:
            if long_sig:
                enter(i, 1)
            elif short_sig:
                enter(i, -1)
        elif FLIP:
            if st["side"] == 1 and short_sig:
                close_pos(i, float(close[i]), "flip"); enter(i, -1)
            elif st["side"] == -1 and long_sig:
                close_pos(i, float(close[i]), "flip"); enter(i, 1)
        st["last_bar"] = int(t[i])

    # status legible
    c = float(close[closed_last])
    if st["in_pos"]:
        unreal = ((c - st["entry"]) if st["side"] == 1 else (st["entry"] - c)) / st["oneR"]
        status[sym] = {"in_position": True, "side": "LONG" if st["side"] == 1 else "SHORT",
                       "entry": round(st["entry"], 2), "stop": round(st["stop"], 2),
                       "price": round(c, 2), "unrealized_R": round(unreal, 2)}
    else:
        status[sym] = {"in_position": False, "price": round(c, 2),
                       "above_ema200": bool(c > ema[closed_last])}


def main():
    ensure()
    state = _load(STATE, {})
    status = {}
    for sym in SYMBOLS:
        process_symbol(sym, state, status)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now().isoformat(), "symbols": status}, f, indent=2)


if __name__ == "__main__":
    main()

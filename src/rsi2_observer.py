"""
Observador PAPEL del RSI(2) en índices US (NAS100, US500).

Registra las señales de entrada/salida de la estrategia validada — SIN enviar
órdenes reales. Sirve para seguir la corrección actual como test out-of-sample
en vivo. Fiel al backtest: entrada RSI2<10 y close>SMA200; salida close>SMA5,
RSI2>70 o max_hold. Solo LEE datos e imprime eventos.
"""
import sys
import os
import json
from datetime import datetime

import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from rsi2_meanrev import rsi
from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE = os.path.join(DATA_DIR, "rsi2_paper.json")
TRADES = os.path.join(DATA_DIR, "rsi2_paper_trades.csv")
STATUS = os.path.join(DATA_DIR, "rsi2_paper_status.json")
SYMBOLS = ["NAS100", "US500"]
ENTRY_TH = 10.0
EXIT_TH = 70.0
MAX_HOLD = 10


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    ensure()
    state = _load(STATE, {})
    status = {}
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 300)
        if r is None or len(r) < 210:
            continue
        df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
        close = df["close"].values
        sma200 = pd.Series(close).rolling(200).mean().values[-1]
        sma5 = pd.Series(close).rolling(5).mean().values[-1]
        r2 = rsi(close, 2)[-1]
        c = float(close[-1]); date = str(df["time"].iloc[-1].date())

        st = state.get(sym, {"in_pos": False})
        if not st.get("in_pos"):
            if c > sma200 and r2 < ENTRY_TH:
                st = {"in_pos": True, "entry": c, "entry_date": date, "bars": 0}
                print(f"[{date}] ENTRY {sym} @ {c:,.0f}  RSI2={r2:.1f}  "
                      f"(+{(c/sma200-1)*100:.1f}% sobre SMA200) → dip-buy en la corrección")
        else:
            st["bars"] = st.get("bars", 0) + 1
            reason = None
            if c > sma5:
                reason = "close>SMA5"
            elif r2 > EXIT_TH:
                reason = "RSI2>70"
            elif st["bars"] >= MAX_HOLD:
                reason = "max_hold"
            if reason:
                ret = (c - st["entry"]) / st["entry"] * 100
                print(f"[{date}] EXIT  {sym} @ {c:,.0f}  ret={ret:+.2f}%  ({reason})  "
                      f"entrada {st['entry_date']}")
                new = not os.path.exists(TRADES)
                with open(TRADES, "a", encoding="utf-8") as f:
                    if new:
                        f.write("symbol,entry_date,exit_date,entry,exit,ret_pct,reason\n")
                    f.write(f"{sym},{st['entry_date']},{date},{st['entry']:.1f},{c:.1f},{ret:.2f},{reason}\n")
                st = {"in_pos": False}

        state[sym] = st
        unreal = ((c - st["entry"]) / st["entry"] * 100) if st.get("in_pos") else 0.0
        status[sym] = {"date": date, "price": round(c, 1), "rsi2": round(r2, 1),
                       "vs_sma200_pct": round((c / sma200 - 1) * 100, 2),
                       "in_position": st.get("in_pos", False),
                       "unrealized_pct": round(unreal, 2)}

    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now().isoformat(), "symbols": status}, f, indent=2)


if __name__ == "__main__":
    main()

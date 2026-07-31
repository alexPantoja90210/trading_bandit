"""
Smart Trend Follower — backtest event-driven en H1.

Mecánica exacta de la doc:
- Filtro régimen: largos solo si close > EMA200; cortos solo si close < EMA200.
- Entrada: ruptura Donchian 55 (close rompe máx/mín de las 55 barras previas).
- Stop inicial (1R) = 2.5*ATR(14). Trailing Chandelier = 3.0*ATR desde el extremo,
  con trinquete (nunca retrocede). Sin TP fijo. Flip en señal opuesta.
- Riesgo 0.5%/trade → P&L en múltiplos de R (independiente del sizing).

Simulación barra a barra, una posición a la vez. Solo LEE histórico.
"""
import sys
import numpy as np
import pandas as pd
import MetaTrader5 as mt5
from mt5_connect import ensure

from paths import load_config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

cfg = load_config()
SYMBOL = cfg["symbol"]
N_BARS = 50000
EMA_LEN = 200
DONCHIAN = 55
ATR_LEN = 14
INIT_STOP = 2.5     # 1R
CHANDELIER = 3.0
RISK_PCT = 0.005
BALANCE = 10000.0
FLIP = True


def atr_series(high, low, close, length):
    prev = np.roll(close, 1); prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).rolling(length).mean().values


def backtest(high, low, close, ema, atr, dch_hi, dch_lo, year, cost,
             el=DONCHIAN, ch=CHANDELIER, init=INIT_STOP, gate=None):
    n = len(close)
    pos = None            # dict: side, entry, stop, oneR, ext (extremo)
    trades = []           # (exit_bar, R, year_entry)
    warm = max(EMA_LEN, el, ATR_LEN) + 2

    def close_pos(exit_price, t):
        pnl = (exit_price - pos["entry"]) if pos["side"] == 1 else (pos["entry"] - exit_price)
        R = (pnl - cost) / pos["oneR"]
        trades.append((t, R, pos["year"]))

    for t in range(warm, n):
        if not (np.isfinite(ema[t]) and np.isfinite(atr[t]) and
                np.isfinite(dch_hi[t]) and np.isfinite(dch_lo[t]) and atr[t] > 0):
            continue

        # 1) gestionar posición abierta: trailing + salida por stop
        if pos is not None:
            if pos["side"] == 1:
                pos["ext"] = max(pos["ext"], high[t])
                pos["stop"] = max(pos["stop"], pos["ext"] - ch * atr[t])
                if low[t] <= pos["stop"]:
                    close_pos(pos["stop"], t); pos = None
            else:
                pos["ext"] = min(pos["ext"], low[t])
                pos["stop"] = min(pos["stop"], pos["ext"] + ch * atr[t])
                if high[t] >= pos["stop"]:
                    close_pos(pos["stop"], t); pos = None

        # 2) señales (vela cerrada), con filtro de tendencia opcional (gate)
        g = True if gate is None else bool(gate[t])
        long_sig = g and close[t] > dch_hi[t] and close[t] > ema[t]
        short_sig = g and close[t] < dch_lo[t] and close[t] < ema[t]

        def open_pos(side):
            oneR = init * atr[t]
            return {"side": side, "entry": close[t],
                    "stop": close[t] - oneR if side == 1 else close[t] + oneR,
                    "oneR": oneR, "ext": high[t] if side == 1 else low[t],
                    "year": year[t]}

        if pos is None:
            if long_sig:
                pos = open_pos(1)
            elif short_sig:
                pos = open_pos(-1)
        elif FLIP:
            if pos["side"] == 1 and short_sig:
                close_pos(close[t], t); pos = open_pos(-1)
            elif pos["side"] == -1 and long_sig:
                close_pos(close[t], t); pos = open_pos(1)

    return trades


def report(trades, label):
    if not trades:
        print(f"{label}: sin trades"); return
    R = np.array([r for _, r, _ in trades])
    risk_d = BALANCE * RISK_PCT
    eq = np.cumsum(R * risk_d)
    dd = (eq - np.maximum.accumulate(eq)).min()
    wins = R[R > 0]; losses = R[R < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    sharpe = R.mean() / R.std() if R.std() > 0 else 0.0
    print(f"\n===== {label} =====")
    print(f"  trades={len(R)}  ΣR={R.sum():+.1f}  net=${R.sum()*risk_d:+.0f}  PF={pf:.2f}")
    print(f"  winrate={ (R>0).mean()*100:.1f}%  ganProm={wins.mean() if len(wins) else 0:+.2f}R  "
          f"perdProm={losses.mean() if len(losses) else 0:+.2f}R")
    print(f"  maxDD=${dd:+.0f} ({dd/BALANCE*100:+.1f}%)  sharpe/trade={sharpe:+.2f}  "
          f"mejorTrade={R.max():+.1f}R")


def by_year(trades):
    from collections import defaultdict
    yr = defaultdict(list)
    for _, r, y in trades:
        yr[y].append(r)
    print(f"\n  {'año':<6}{'trades':>7}{'ΣR':>8}{'PF':>7}{'wr%':>7}")
    for y in sorted(yr):
        R = np.array(yr[y]); w = R[R > 0]; l = R[R < 0]
        pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
        print(f"  {y:<6}{len(R):>7}{R.sum():>+8.1f}{pf:>7.2f}{(R>0).mean()*100:>7.1f}")


def main():
    ensure()
    tf_name = sys.argv[1] if len(sys.argv) > 1 else "H1"
    tf = {"H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
          "M15": mt5.TIMEFRAME_M15, "D1": mt5.TIMEFRAME_D1}[tf_name]
    info = mt5.symbol_info(SYMBOL); cost = info.spread * info.point
    df = pd.DataFrame(mt5.copy_rates_from_pos(SYMBOL, tf, 0, N_BARS))
    df["time"] = pd.to_datetime(df["time"], unit="s")
    n = len(df)
    print(f"Smart Trend Follower | {tf_name} | cost(spread)={cost:.3f}")
    print(f"{n} barras: {df['time'].iloc[0].date()} -> {df['time'].iloc[-1].date()}")
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    year = df["time"].dt.year.values
    ema = pd.Series(close).ewm(span=EMA_LEN, adjust=False).mean().values
    atr = atr_series(high, low, close, ATR_LEN)
    dch_hi = pd.Series(high).rolling(DONCHIAN).max().shift(1).values
    dch_lo = pd.Series(low).rolling(DONCHIAN).min().shift(1).values

    # base
    trades = backtest(high, low, close, ema, atr, dch_hi, dch_lo, year, cost)
    report(trades, "BASE  EL55 CH30  (todo 2018-2026)")
    by_year(trades)

    # sub-período comparable a la doc (2024-2026)
    tr_doc = [tr for tr in trades if tr[2] >= 2024]
    report(tr_doc, "Sub-período 2024-2026 (comparable a la doc)")

    # out-of-sample hacia atrás (2018-2021, que la doc NO cubre)
    tr_oos = [tr for tr in trades if tr[2] <= 2021]
    report(tr_oos, "Out-of-sample 2018-2021 (fuera de la validación previa)")

    # robustez de parámetros
    print("\n===== robustez de parámetros (ΣR / PF / maxDD%) =====")
    for el, ch in [(40, 3.0), (55, 3.0), (80, 3.0), (55, 2.5), (55, 4.0)]:
        dhi = pd.Series(high).rolling(el).max().shift(1).values
        dlo = pd.Series(low).rolling(el).min().shift(1).values
        tr = backtest(high, low, close, ema, atr, dhi, dlo, year, cost, el=el, ch=ch)
        R = np.array([r for _, r, _ in tr])
        risk_d = BALANCE * RISK_PCT; eq = np.cumsum(R * risk_d)
        dd = (eq - np.maximum.accumulate(eq)).min()
        w = R[R > 0]; l = R[R < 0]; pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
        print(f"  EL{el} CH{ch}: ΣR={R.sum():+7.1f}  PF={pf:.2f}  maxDD={dd/BALANCE*100:+.1f}%  trades={len(R)}")


if __name__ == "__main__":
    main()

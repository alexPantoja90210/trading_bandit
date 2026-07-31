"""
RSI(2) Mean-Reversion (Connors) — backtest event-driven en índices.

Reglas (clásicas):
- Filtro de tendencia mayor: solo largos si close > SMA(200).
- Entrada: RSI(2) < entry_th (debilidad de corto plazo dentro del uptrend).
- Salida: close > SMA(5)  (o RSI(2) > exit_th, o max_hold barras).
- Opcional: stop de ATR para acotar el "cuchillo que cae".
- P&L medido en % (sin apalancar). Solo LEE histórico.
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

SYMBOLS = ["US30", "NAS100", "US500", "GER40"]
N_BARS = 50000
SMA_TREND = 200
SMA_EXIT = 5
RSI_LEN = 2
ENTRY_TH = 10.0
EXIT_TH = 70.0
MAX_HOLD = 10
USE_STOP = False
STOP_ATR = 3.0


def rsi(close, period):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    ag = pd.Series(gain).ewm(alpha=1 / period, adjust=False).mean()
    al = pd.Series(loss).ewm(alpha=1 / period, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).values


def atr_series(high, low, close, length=14):
    prev = np.roll(close, 1); prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).rolling(length).mean().values


def backtest(high, low, close, sma200, sma5, r2, atr, year, cost_pct,
             entry_th=ENTRY_TH, use_stop=USE_STOP):
    n = len(close)
    warm = SMA_TREND + 2
    pos = None
    trades = []
    for t in range(warm, n):
        if not (np.isfinite(sma200[t]) and np.isfinite(r2[t]) and np.isfinite(sma5[t])):
            continue
        if pos is None:
            if close[t] > sma200[t] and r2[t] < entry_th:
                pos = {"entry": close[t], "bar": t,
                       "stop": (close[t] - STOP_ATR * atr[t]) if use_stop else None}
        else:
            exit_price = None
            if pos["stop"] is not None and low[t] <= pos["stop"]:
                exit_price = pos["stop"]
            elif close[t] > sma5[t] or r2[t] > EXIT_TH:
                exit_price = close[t]
            elif t - pos["bar"] >= MAX_HOLD:
                exit_price = close[t]
            if exit_price is not None:
                ret = (exit_price - pos["entry"]) / pos["entry"] - cost_pct
                trades.append((t, ret, year[t]))
                pos = None
    return trades


def stats(trades, bh_ret, years_span):
    if not trades:
        return "sin trades"
    R = np.array([r for _, r, _ in trades]) * 100  # en %
    eq = np.cumsum(R)
    dd = (eq - np.maximum.accumulate(eq)).min()
    w = R[R > 0]; l = R[R < 0]
    pf = w.sum() / -l.sum() if l.sum() < 0 else 9.99
    return (f"n={len(R):>4}  ret%={R.sum():>+7.1f}  PF={pf:.2f}  wr={ (R>0).mean()*100:>4.1f}%  "
            f"maxDD%={dd:>+6.1f}  gan={w.mean() if len(w) else 0:>+.2f}%  perd={l.mean() if len(l) else 0:>+.2f}%  "
            f"| buy&hold%={bh_ret:>+7.1f}")


def run_symbol(sym):
    ensure()
    mt5.symbol_select(sym, True)
    info = mt5.symbol_info(sym)
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, N_BARS)
    if rates is None or len(rates) < SMA_TREND + 50:
        print(f"\n### {sym}: histórico insuficiente")
        return
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    close = df["close"].values; high = df["high"].values; low = df["low"].values
    year = df["time"].dt.year.values
    sma200 = pd.Series(close).rolling(SMA_TREND).mean().values
    sma5 = pd.Series(close).rolling(SMA_EXIT).mean().values
    r2 = rsi(close, RSI_LEN)
    atr = atr_series(high, low, close, 14)
    cost_pct = (info.spread * info.point) / np.nanmean(close) if info else 0.0
    bh = (close[-1] / close[SMA_TREND] - 1) * 100
    span = f"{df['time'].iloc[0].date()}->{df['time'].iloc[-1].date()}"

    print(f"\n### {sym} | D1 | {len(df)} barras {span} | spread%={cost_pct*100:.4f}")
    for eth in [10.0, 5.0]:
        tr = backtest(high, low, close, sma200, sma5, r2, atr, year, cost_pct, entry_th=eth)
        print(f"  RSI2<{eth:>4}: {stats(tr, bh, span)}")

    # robustez por año (config base entry<10)
    tr = backtest(high, low, close, sma200, sma5, r2, atr, year, cost_pct, entry_th=10.0)
    from collections import defaultdict
    yr = defaultdict(list)
    for _, r, y in tr:
        yr[y].append(r * 100)
    line = "  por año: " + "  ".join(f"{y}:{np.sum(v):+.1f}" for y, v in sorted(yr.items()) if len(v) >= 2)
    print(line)


def main():
    ensure()
    for s in SYMBOLS:
        run_symbol(s)


if __name__ == "__main__":
    main()

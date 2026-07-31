import MetaTrader5 as mt5
import pandas as pd

from paths import load_config
from mt5_connect import ensure

def load_ohlc():
    cfg = load_config()
    symbol = cfg["symbol"]
    timeframe = getattr(mt5, "TIMEFRAME_" + cfg["timeframe"])
    n = 2000

    if not ensure(cfg):
        raise RuntimeError("MT5 no pudo inicializarse")

    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

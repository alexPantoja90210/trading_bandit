import numpy as np


def compute_indicators(df):
    """Agrega indicadores de mercado que usan el loop y el contexto.

    Ya NO calcula recompensas contemporáneas por brazo. La recompensa ahora es
    FUTURA y direccional: se computa en el loop (main_live_v2) cuando la decisión
    madura tras `reward_horizon` barras, como P&L real en la dirección operada.
    Aquí solo se derivan columnas de apoyo (returns, sma20, atr).
    """
    df["returns"] = df["close"].pct_change()
    df["sma20"] = df["close"].rolling(20).mean()

    atr = (df["high"] - df["low"]).rolling(14).mean()
    atr = atr.replace(0, np.nan).bfill()
    df["atr"] = atr

    return df

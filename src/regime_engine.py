import pandas as pd
from regime_master import classify, Params

def compute_regime(df: pd.DataFrame):
    """
    df: DataFrame con columnas: open, high, low, close
    return: última fila del clasificador (regime, family, p0..p9, knn_edge, etc.)
    """
    p = Params()
    out = classify(df, p)
    last = out.iloc[-1]
    return last

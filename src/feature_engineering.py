import pandas as pd

def build_features(df, features):
    X = pd.DataFrame()

    for f in features:

        if f == "close":
            X["close"] = df["close"]

        elif f == "volume":
            X["volume"] = df["tick_volume"]

        elif f == "rsi":
            X["rsi"] = compute_rsi(df["close"], 14)

        elif f == "sma20":
            X["sma20"] = df["close"].rolling(20).mean()

        elif f == "returns":
            X["returns"] = df["close"].pct_change()

        elif f == "volatility":
            X["volatility"] = (df["high"] - df["low"])

        else:
            raise ValueError(f"Feature desconocida: {f}")

    return X


def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi

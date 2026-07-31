def should_trade(regime_row, arm_name: str):
    """
    regime_row: última fila de classify()
    arm_name: "trend", "mean", "flat", "momentum", "volatility"
    """
    family = str(regime_row["family"])
    conf = float(regime_row["confidence"])

    # si la confianza es baja, no operar
    if conf < 0.4:
        return False

    if family == "NO_TRADE":
        return False

    if family == "TREND_UP":
        return arm_name in ["trend", "momentum"]

    if family == "TREND_DOWN":
        return arm_name in ["trend", "momentum"]

    if family == "RANGE":
        return arm_name in ["mean", "flat", "volatility"]

    return False

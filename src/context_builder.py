import numpy as np

FAMILY_MAP = {
    "TREND_UP": 0,
    "TREND_DOWN": 1,
    "RANGE": 2,
    "NO_TRADE": 3,
}

def family_one_hot(family: str):
    idx = FAMILY_MAP.get(family, 3)
    vec = np.zeros(len(FAMILY_MAP))
    vec[idx] = 1.0
    return vec

def build_context(features_row, regime_row):
    """
    features_row: última fila de tu X (close, rsi, etc.)
    regime_row: última fila de classify()
    return: vector numpy 1D
    """
    base = features_row.values.astype(float)

    regime_id = float(regime_row["id"]) if regime_row["id"] == regime_row["id"] else -1.0
    confidence = float(regime_row["confidence"])
    family_vec = family_one_hot(str(regime_row["family"]))

    p_scores = np.array([float(regime_row[f"p{i}"]) for i in range(10)], dtype=float)

    knn_edge = float(regime_row["knn_edge"])
    knn_risk = float(regime_row["knn_risk"])
    knn_fav = float(regime_row["knn_fav"])
    bars_in = float(regime_row["bars_in_regime"])

    ctx = np.concatenate([
        base,
        np.array([regime_id, confidence], dtype=float),
        family_vec,
        p_scores,
        np.array([knn_edge, knn_risk, knn_fav, bars_in], dtype=float),
    ])

    return ctx

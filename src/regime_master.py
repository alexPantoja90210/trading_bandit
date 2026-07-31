"""
==============================================================================
 REGIME MASTER - IMPLEMENTACION DE REFERENCIA
 Clasificador de regimenes de mercado: motor difuso + capa k-NN
==============================================================================

Esta es la VERDAD DE REFERENCIA del algoritmo. Cualquier port a otro lenguaje
(MQL5, Pine, C++, Rust, JS...) debe reproducir estas salidas bit a bit sobre
los mismos datos de entrada.

Entrada : OHLC (open, high, low, close) ordenado cronologicamente.
Salida  : DataFrame con id, code, confidence, scores[10], knn_edge, ...

Sin dependencias mas alla de numpy y pandas.
Sin look-ahead: toda barra t usa exclusivamente informacion de barras <= t.

Uso:
    df = pd.read_csv("ohlc.csv")            # columnas: open, high, low, close
    out = classify(df)
    print(out.tail())
==============================================================================
"""

from dataclasses import dataclass, field
import numpy as np
import pandas as pd

# =============================================================================
# 0. TAXONOMIA Y CODIFICACION
# =============================================================================
REGIME_NAMES = {
    0: "ALCISTA_CALMADA",
    1: "CAOS_VOLATIL",
    2: "RANGO_TRANQUILO",
    3: "ALCISTA_VOLATIL",
    4: "ALCISTA_NORMAL",
    5: "BAJISTA_CALMADA",
    6: "BAJISTA_NORMAL",
    7: "BAJISTA_VOLATIL",
    8: "RANGO_VOLATIL",
    9: "FASE_TRANSICION",
}
N_REGIMES = 10
N_FEATURES = 10

# Familia operativa de cada regimen
FAMILY = {
    0: "TREND_UP", 3: "TREND_UP", 4: "TREND_UP",
    5: "TREND_DOWN", 6: "TREND_DOWN", 7: "TREND_DOWN",
    2: "RANGE", 8: "RANGE",
    1: "NO_TRADE", 9: "NO_TRADE",
}


def encode(regime_id: int, confidence: float) -> int:
    """codigo = id*10 + paso_de_confianza(1..10).  -1 = sin clasificar."""
    if regime_id is None or regime_id < 0:
        return -1
    step = int(np.ceil(min(max(confidence, 0.0), 1.0) * 10))
    step = min(max(step, 1), 10)
    return regime_id * 10 + step


def decode(code: int):
    """Inverso exacto de encode(). OJO: no es floor(code/10)."""
    if code is None or code < 0:
        return (-1, 0.0, 0.0)
    regime_id = (code - 1) // 10          # <-- clave: (code-1)//10, NO code//10
    step = code - regime_id * 10          # 1..10
    conf_lo = (step - 1) / 10.0
    conf_hi = step / 10.0
    return (regime_id, conf_lo, conf_hi)


# =============================================================================
# 1. PARAMETROS
# =============================================================================
@dataclass
class Params:
    # --- indicadores
    ma_fast: int = 21
    ma_slow: int = 55
    ma_trend: int = 200
    ma_type: str = "EMA"          # EMA | SMA | WMA | RMA
    adx_len: int = 14
    atr_len: int = 14
    rsi_len: int = 14
    bb_len: int = 20
    bb_mult: float = 2.0
    # --- normalizacion auto-adaptativa
    vol_lookback: int = 250
    slope_bars: int = 5
    er_len: int = 14
    r2_len: int = 20
    # --- umbrales del motor difuso
    adx_weak: float = 18.0
    adx_strong: float = 27.0
    vol_calm_pct: float = 0.35
    vol_high_pct: float = 0.70
    er_trend: float = 0.35
    r2_trend: float = 0.45
    slope_min: float = 0.05
    # --- estabilidad
    confirm_bars: int = 2
    min_conf_change: float = 0.30
    no_repaint: bool = True        # clasifica sobre la barra cerrada (shift=1)
    # --- capa k-NN
    use_knn: bool = True
    knn_max_cases: int = 400
    knn_horizon: int = 12
    knn_radius: float = 0.55
    knn_min_neighbors: int = 5
    weight_rules: float = 0.60
    feature_weights: tuple = (1.4, 1.2, 1.5, 1.3, 1.0, 0.8, 1.2, 1.3, 1.1, 0.7)


# =============================================================================
# 2. FUNCIONES DIFUSAS
# =============================================================================
def clamp(x, lo=0.0, hi=1.0):
    return np.minimum(np.maximum(x, lo), hi)


def f_up(x, a, b):
    """Rampa ascendente: 0 debajo de a, 1 encima de b."""
    if b <= a:
        return np.where(x >= b, 1.0, 0.0)
    return clamp((x - a) / (b - a))


def f_dn(x, a, b):
    return 1.0 - f_up(x, a, b)


def f_trap(x, a, b, c, d):
    """Trapecio: 0 antes de a, 1 entre b y c, 0 despues de d."""
    x = np.asarray(x, dtype=float)
    up = (x - a) / max(b - a, 1e-9)
    dn = (d - x) / max(d - c, 1e-9)
    y = np.where(x < b, up, np.where(x > c, dn, 1.0))
    return clamp(np.where((x <= a) | (x >= d), 0.0, y))


# =============================================================================
# 3. INDICADORES BASE
# =============================================================================
def _ma(series: pd.Series, length: int, kind: str) -> pd.Series:
    if kind == "SMA":
        return series.rolling(length, min_periods=length).mean()
    if kind == "WMA":
        w = np.arange(1, length + 1, dtype=float)
        return series.rolling(length, min_periods=length).apply(
            lambda v: np.dot(v, w) / w.sum(), raw=True)
    if kind == "RMA":
        return series.ewm(alpha=1.0 / length, adjust=False).mean()
    return series.ewm(span=length, adjust=False).mean()          # EMA


def _rma(series: pd.Series, length: int) -> pd.Series:
    """Media de Wilder: la usan ATR, RSI y ADX."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


def _true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def _atr(h, l, c, length):
    return _rma(_true_range(h, l, c), length)


def _rsi(c, length):
    d = c.diff()
    gain = _rma(d.clip(lower=0.0), length)
    loss = _rma((-d).clip(lower=0.0), length)
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(100.0).where(loss != 0, 100.0).where(gain != 0, out.fillna(0.0))


def _dmi(h, l, c, length):
    """Devuelve (+DI, -DI, ADX) con el suavizado de Wilder."""
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = _true_range(h, l, c)
    atr = _rma(tr, length)
    plus_di = 100.0 * _rma(pd.Series(plus_dm, index=h.index), length) / atr.replace(0.0, np.nan)
    minus_di = 100.0 * _rma(pd.Series(minus_dm, index=h.index), length) / atr.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = _rma(dx.fillna(0.0), length)
    return plus_di.fillna(0.0), minus_di.fillna(0.0), adx.fillna(0.0)


def _percent_rank(series: pd.Series, length: int) -> pd.Series:
    """
    Porcentaje (0..1) de las `length` barras ANTERIORES cuyo valor es <= el actual.
    Definicion canonica: no incluye la barra actual en la poblacion comparada.
    """
    arr = series.to_numpy(dtype=float)
    n = arr.size
    out = np.full(n, 0.5)
    for t in range(n):
        lo = t - length
        if lo < 0 or not np.isfinite(arr[t]):
            continue
        win = arr[lo:t]
        win = win[np.isfinite(win)]
        if win.size < 5:
            continue
        out[t] = np.count_nonzero(win <= arr[t]) / win.size
    return pd.Series(out, index=series.index)


def _efficiency_ratio(c: pd.Series, length: int) -> pd.Series:
    direction = (c - c.shift(length)).abs()
    noise = c.diff().abs().rolling(length, min_periods=length).sum()
    return clamp((direction / noise.replace(0.0, np.nan)).fillna(0.0))


def _r2(c: pd.Series, length: int) -> pd.Series:
    t = pd.Series(np.arange(len(c), dtype=float), index=c.index)
    r = c.rolling(length, min_periods=length).corr(t)
    return (r ** 2).fillna(0.0)


# =============================================================================
# 4. VECTOR DE FEATURES  (10 dimensiones, todas 0..1)
# =============================================================================
def build_features(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]

    ma_f = _ma(c, p.ma_fast, p.ma_type)
    ma_s = _ma(c, p.ma_slow, p.ma_type)
    ma_t = _ma(c, p.ma_trend, p.ma_type)
    plus_di, minus_di, adx = _dmi(h, l, c, p.adx_len)
    atr = _atr(h, l, c, p.atr_len)
    rsi = _rsi(c, p.rsi_len)

    bb_mid = c.rolling(p.bb_len, min_periods=p.bb_len).mean()
    bb_sd = c.rolling(p.bb_len, min_periods=p.bb_len).std(ddof=0)
    bb_up = bb_mid + p.bb_mult * bb_sd
    bb_lo = bb_mid - p.bb_mult * bb_sd

    atr_safe = atr.replace(0.0, np.nan).ffill().fillna(1e-9)
    bbw = (bb_up - bb_lo) / bb_mid.abs().replace(0.0, np.nan)

    atr_pct = _percent_rank(atr_safe, p.vol_lookback)
    bbw_pct = _percent_rank(bbw.ffill().fillna(0.0), p.vol_lookback)

    er = _efficiency_ratio(c, p.er_len)
    r2 = _r2(c, p.r2_len)
    slope = (ma_f - ma_f.shift(p.slope_bars)) / (atr_safe * p.slope_bars)

    di_sum = plus_di + minus_di
    di_bal = np.where(di_sum > 1e-9, (plus_di - minus_di) / di_sum.replace(0.0, np.nan), 0.0)
    di_bal = np.nan_to_num(di_bal)

    f = pd.DataFrame(index=df.index)
    f["f0_adx"]     = clamp(adx / 50.0)
    f["f1_di"]      = clamp((di_bal + 1.0) * 0.5)
    f["f2_atr_pct"] = clamp(atr_pct)
    f["f3_slope"]   = clamp((clamp(slope, -1.0, 1.0) + 1.0) * 0.5)
    f["f4_maspread"] = clamp((clamp((ma_f - ma_s) / atr_safe, -3.0, 3.0) / 3.0 + 1.0) * 0.5)
    f["f5_rsi"]     = clamp(rsi / 100.0)
    f["f6_bbw_pct"] = clamp(bbw_pct)
    f["f7_er"]      = clamp(er)
    f["f8_r2"]      = clamp(r2)
    band = (bb_up - bb_lo)
    f["f9_pctb"]    = np.where(band > 1e-12, clamp((c - bb_lo) / band.replace(0.0, np.nan)), 0.5)
    f["f9_pctb"]    = f["f9_pctb"].fillna(0.5)

    # valores crudos que necesita el motor de reglas
    f["adx"] = adx
    f["atr"] = atr_safe
    f["rsi"] = rsi
    f["er"] = er
    f["r2"] = r2
    f["slope"] = slope.fillna(0.0)
    f["close"] = c
    f["ma_trend"] = ma_t
    f["ma_fast"] = ma_f
    f["ma_slow"] = ma_s
    f["bb_up"] = bb_up
    f["bb_mid"] = bb_mid
    f["bb_lo"] = bb_lo
    f["plus_di"] = plus_di
    f["minus_di"] = minus_di
    return f


FEATURE_COLS = ["f0_adx", "f1_di", "f2_atr_pct", "f3_slope", "f4_maspread",
                "f5_rsi", "f6_bbw_pct", "f7_er", "f8_r2", "f9_pctb"]


# =============================================================================
# 5. MOTOR DE REGLAS DIFUSAS  ->  10 scores sin normalizar
# =============================================================================
def rule_scores(row, prev_slope: float, prev_adx: float, p: Params) -> np.ndarray:
    f = [row[c] for c in FEATURE_COLS]
    adx, atr, rsi = row["adx"], row["atr"], row["rsi"]
    er, r2, slope = row["er"], row["r2"], row["slope"]
    close, ma_t = row["close"], row["ma_trend"]

    # ---- 1) DIRECCION -------------------------------------------------------
    di_bal = (f[1] - 0.5) * 2.0
    slp = float(clamp(slope, -1.0, 1.0))
    ma_sprd = (f[4] - 0.5) * 2.0
    sign = 1.0 if close > ma_t else -1.0
    pos_ma = sign * float(f_up(abs(close - ma_t) / max(atr, 1e-9), 0.10, 1.20))

    dir_c = float(clamp(
        0.30 * di_bal
        + 0.30 * (slp / max(p.slope_min * 4.0, 1e-9))
        + 0.25 * ma_sprd
        + 0.15 * pos_ma, -1.0, 1.0))

    mu_bull = float(f_up(dir_c, 0.08, 0.55))
    mu_bear = float(f_up(-dir_c, 0.08, 0.55))
    mu_flat = max(0.0, 1.0 - max(mu_bull, mu_bear))

    # ---- 2) FUERZA / ESTRUCTURA DE TENDENCIA --------------------------------
    mu_trend = float(min(1.0,
        0.45 * f_up(adx, p.adx_weak, p.adx_strong)
        + 0.30 * f_up(r2, p.r2_trend * 0.6, p.r2_trend * 1.6)
        + 0.25 * f_up(er, p.er_trend * 0.5, p.er_trend * 1.8)))
    mu_range = max(0.0, 1.0 - mu_trend)

    # ---- 3) VOLATILIDAD -----------------------------------------------------
    vol_comp = 0.60 * f[2] + 0.40 * f[6]
    c_calm = float(f_trap(vol_comp, -0.10, 0.0, p.vol_calm_pct, p.vol_calm_pct + 0.15))
    c_norm = float(f_trap(vol_comp, p.vol_calm_pct - 0.10, p.vol_calm_pct + 0.05,
                          p.vol_high_pct - 0.05, p.vol_high_pct + 0.10))
    c_high = float(f_up(vol_comp, p.vol_high_pct - 0.10, p.vol_high_pct + 0.15))
    v_sum = c_calm + c_norm + c_high
    if v_sum > 1e-9:
        mu_calm, mu_norm, mu_high = c_calm / v_sum, c_norm / v_sum, c_high / v_sum
    else:
        mu_calm, mu_norm, mu_high = 0.0, 1.0, 0.0

    # ---- 4) TRANSICION ------------------------------------------------------
    slope_flip = 1.0 if (slope * prev_slope) < 0.0 else 0.0
    mu_trans = float(clamp(
        0.35 * slope_flip
        + 0.25 * f_up(adx - prev_adx, 0.5, 4.0)
        + 0.25 * f_trap(adx, p.adx_weak - 6.0, p.adx_weak - 1.0,
                        p.adx_strong - 1.0, p.adx_strong + 4.0)
        + 0.15 * f_up(f[6], 0.55, 0.85) * f_dn(f[2], 0.30, 0.65)))

    # ---- 5) CAOS ------------------------------------------------------------
    mu_chaos = (mu_high
                * float(f_dn(er, 0.12, 0.32))
                * max(mu_flat, 0.35)
                * (0.5 + 0.5 * float(f_dn(r2, 0.10, 0.40)))
                * float(f_dn(adx, p.adx_strong, p.adx_strong + 12.0)))

    # ---- 6) SCORES ----------------------------------------------------------
    inside_bb = 1.0 - abs(f[9] - 0.5) * 2.0
    range_core = mu_range * (0.55 + 0.45 * inside_bb) * max(mu_flat, 0.30)

    s = np.zeros(N_REGIMES)
    s[0] = mu_bull * mu_trend * mu_calm
    s[1] = mu_chaos
    s[2] = range_core * (mu_calm + 0.5 * mu_norm)
    s[3] = mu_bull * mu_trend * mu_high
    s[4] = mu_bull * mu_trend * mu_norm
    s[5] = mu_bear * mu_trend * mu_calm
    s[6] = mu_bear * mu_trend * mu_norm
    s[7] = mu_bear * mu_trend * mu_high
    s[8] = range_core * mu_high * (0.6 + 0.4 * float(f_up(er, 0.05, 0.25)))
    s[9] = mu_trans * (0.35 + 0.65 * mu_range)

    # penalizacion por RSI extremo contradictorio
    if f[5] > 0.72:
        s[5] *= 0.75
    if f[5] < 0.28:
        s[0] *= 0.75

    return np.clip(s, 0.0, 1.0) + 0.0005


# =============================================================================
# 6. CONFIANZA
# =============================================================================
def confidence_from(scores_norm: np.ndarray):
    order = np.argsort(scores_norm)[::-1]
    best, second = order[0], order[1]
    bv, sv = scores_norm[best], scores_norm[second]
    share = bv
    margin = (bv - max(sv, 0.0)) / bv if bv > 1e-9 else 0.0
    conf = 0.55 * float(clamp((share - 0.10) / 0.45)) + 0.45 * margin
    return int(best), float(clamp(conf))


# =============================================================================
# 7. CAPA k-NN
# =============================================================================
class KnnMemory:
    """Buffer circular: features + etiqueta + resultado futuro real."""

    def __init__(self, p: Params):
        self.p = p
        self.feat = np.zeros((p.knn_max_cases, N_FEATURES))
        self.label = np.full(p.knn_max_cases, -1, dtype=int)
        self.fwd = np.zeros(p.knn_max_cases)     # retorno futuro en ATR
        self.risk = np.zeros(p.knn_max_cases)    # excursion adversa en ATR
        self.fav = np.zeros(p.knn_max_cases)     # excursion favorable en ATR
        self.ptr = 0
        self.count = 0
        self.w = np.asarray(p.feature_weights, dtype=float)

    def add(self, feat, label, fwd, risk, fav):
        i = self.ptr
        self.feat[i] = feat
        self.label[i] = label
        self.fwd[i] = fwd
        self.risk[i] = risk
        self.fav[i] = fav
        self.ptr = (self.ptr + 1) % self.p.knn_max_cases
        self.count = min(self.count + 1, self.p.knn_max_cases)

    def query(self, feat):
        """Kernel de Parzen. Devuelve (votos[10], edge, risk, fav, n_vecinos)."""
        votes = np.zeros(N_REGIMES)
        if self.count < self.p.knn_min_neighbors:
            return votes, 0.0, 0.0, 0.0, 0
        lib = self.feat[:self.count]
        d = np.sqrt(((lib - feat) ** 2 * self.w).sum(axis=1) / self.w.sum())
        m = d < self.p.knn_radius
        n = int(m.sum())
        if n < self.p.knn_min_neighbors:
            return votes, 0.0, 0.0, 0.0, 0
        w = 1.0 / (d[m] + 0.02)
        ws = w.sum()
        for lab, wi in zip(self.label[:self.count][m], w):
            if 0 <= lab < N_REGIMES:
                votes[lab] += wi
        votes /= ws
        edge = float((w * self.fwd[:self.count][m]).sum() / ws)
        risk = float((w * self.risk[:self.count][m]).sum() / ws)
        fav = float((w * self.fav[:self.count][m]).sum() / ws)
        return votes, edge, risk, fav, n


# =============================================================================
# 8. CLASIFICADOR COMPLETO
# =============================================================================
def classify(df: pd.DataFrame, p: Params = None) -> pd.DataFrame:
    p = p or Params()
    feats = build_features(df, p)
    n = len(feats)
    shift = 1 if p.no_repaint else 0
    H = p.knn_horizon

    warmup = max(p.ma_trend, p.vol_lookback) + p.r2_len + p.slope_bars + 10

    mem = KnnMemory(p)
    fcols = feats[FEATURE_COLS].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    atr_arr = feats["atr"].to_numpy(dtype=float)
    slope_arr = feats["slope"].to_numpy(dtype=float)
    adx_arr = feats["adx"].to_numpy(dtype=float)

    rule_label = np.full(n, -1, dtype=int)
    out = {k: np.full(n, np.nan) for k in
           ["id", "code", "confidence", "knn_edge", "knn_risk", "knn_fav",
            "knn_n", "bars_in_regime", "rule_best"]}
    score_mat = np.full((n, N_REGIMES), np.nan)

    cur_id, pend_id, pend_cnt, bars_in = -1, -1, 0, 0

    for t in range(n):
        # ---- etiqueta de reglas de ESTA barra (para archivar en el k-NN) ----
        if t >= 1 and np.isfinite(fcols[t]).all():
            s_raw = rule_scores(feats.iloc[t], slope_arr[t - 1], adx_arr[t - 1], p)
            rule_label[t] = int(np.argmax(s_raw))

        # ---- archivado del caso que ya ha madurado (sin look-ahead) ---------
        if p.use_knn and t > warmup + H:
            j = t - H                     # barra cuyo resultado ya es pasado
            atr_j = atr_arr[j]
            if atr_j > 0 and rule_label[j] >= 0:
                fwd = (close[t] - close[j]) / atr_j
                hi = high[j + 1:t + 1].max()
                lo = low[j + 1:t + 1].min()
                if fwd >= 0:
                    risk = (close[j] - lo) / atr_j
                    fav = (hi - close[j]) / atr_j
                else:
                    risk = (hi - close[j]) / atr_j
                    fav = (close[j] - lo) / atr_j
                mem.add(fcols[j], rule_label[j], fwd, max(0.0, risk), max(0.0, fav))

        # ---- clasificacion de la barra objetivo (t - shift) -----------------
        k = t - shift
        if k < 1 or not np.isfinite(fcols[k]).all() or t < warmup:
            continue

        s_raw = rule_scores(feats.iloc[k], slope_arr[k - 1], adx_arr[k - 1], p)
        rn = s_raw / s_raw.sum()

        votes, edge, risk, fav, nn = (np.zeros(N_REGIMES), 0.0, 0.0, 0.0, 0)
        if p.use_knn:
            votes, edge, risk, fav, nn = mem.query(fcols[k])
        knn_ok = nn >= p.knn_min_neighbors

        wr = p.weight_rules if knn_ok else 1.0
        fused = wr * rn + (1.0 - wr) * votes
        fused = fused / fused.sum()

        best, conf = confidence_from(fused)

        # ---- histeresis -----------------------------------------------------
        if cur_id < 0:
            cur_id, bars_in, pend_id, pend_cnt = best, 1, -1, 0
            reported = cur_id
        elif best == cur_id:
            bars_in += 1
            pend_id, pend_cnt = -1, 0
            reported = cur_id
        else:
            if best == pend_id:
                pend_cnt += 1
            else:
                pend_id, pend_cnt = best, 1
            if pend_cnt >= p.confirm_bars and conf >= p.min_conf_change:
                cur_id, bars_in, pend_id, pend_cnt = best, 1, -1, 0
                reported = cur_id
            else:
                bars_in += 1
                reported = 9 if pend_cnt >= p.confirm_bars else cur_id

        out["id"][t] = reported
        out["code"][t] = encode(reported, conf)
        out["confidence"][t] = conf
        out["knn_edge"][t] = edge if knn_ok else 0.0
        out["knn_risk"][t] = risk if knn_ok else 0.0
        out["knn_fav"][t] = fav if knn_ok else 0.0
        out["knn_n"][t] = nn
        out["bars_in_regime"][t] = bars_in
        out["rule_best"][t] = int(np.argmax(rn))
        score_mat[t] = fused

    res = pd.DataFrame(out, index=df.index)
    for i in range(N_REGIMES):
        res[f"p{i}"] = score_mat[:, i]
    res["regime"] = res["id"].map(lambda v: REGIME_NAMES.get(int(v), "SIN_CLASIFICAR")
                                  if np.isfinite(v) else "SIN_CLASIFICAR")
    res["family"] = res["id"].map(lambda v: FAMILY.get(int(v), "NO_TRADE")
                                  if np.isfinite(v) else "NO_TRADE")
    return res


# =============================================================================
# 9. AUTOTEST
# =============================================================================
if __name__ == "__main__":
    rng = np.random.default_rng(7)
    n = 3000
    # serie sintetica con tramos de tendencia, rango y alta volatilidad
    drift = np.concatenate([
        np.full(600, 0.0008), np.full(600, 0.0), np.full(400, -0.0012),
        np.full(500, 0.0), np.full(500, 0.0006), np.full(400, -0.0004)])
    vol = np.concatenate([
        np.full(600, 0.004), np.full(600, 0.002), np.full(400, 0.011),
        np.full(500, 0.003), np.full(500, 0.005), np.full(400, 0.009)])
    ret = drift + vol * rng.standard_normal(n)
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.standard_normal(n)) * vol * 0.6)
    low = close * (1 - np.abs(rng.standard_normal(n)) * vol * 0.6)
    df = pd.DataFrame({"open": np.r_[close[0], close[:-1]],
                       "high": high, "low": low, "close": close})

    out = classify(df)
    valid = out.dropna(subset=["id"])

    print(f"Barras clasificadas: {len(valid)} / {n}")
    print("\nDistribucion de regimenes:")
    print(valid["regime"].value_counts().to_string())

    # --- invariantes ---
    pcols = [f"p{i}" for i in range(N_REGIMES)]
    sums = valid[pcols].sum(axis=1)
    assert np.allclose(sums, 1.0, atol=1e-9), "los scores deben sumar 1"
    assert valid["confidence"].between(0, 1).all(), "confianza fuera de [0,1]"
    codes = valid["code"].astype(int)
    assert codes.between(1, 100).all(), "codigo fuera de rango"
    for c in codes.unique():
        rid, lo, hi = decode(int(c))
        assert 0 <= rid <= 9, f"decode fallo en {c}"
    rt = codes.map(lambda c: decode(int(c))[0])
    assert (rt.to_numpy() == valid["id"].astype(int).to_numpy()).all(), \
        "round-trip encode/decode roto"
    print("\nInvariantes: OK (scores suman 1, confianza en [0,1], "
          "codigos 1-100, decode reversible)")
    print(f"\nMemoria k-NN final: hasta {Params().knn_max_cases} casos")
    print(valid[["code", "regime", "confidence", "knn_edge", "knn_n"]].tail(8).to_string())
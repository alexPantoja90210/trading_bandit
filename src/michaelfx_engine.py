"""
michaelfx_engine.py — capa de cálculo y BITÁCORA para la estrategia discrecional MichaelFX.

NO opera. Provee el CONTEXTO que el método necesita (sesgo D/4H/1H, PDH/PDL, sesión UTC-5,
niveles/liquidez, Fibonacci, OB aproximados, noticias) y guarda/analiza la bitácora manual.
El OB "exacto" lo marca el trader en su gráfico; aquí se dan candidatos aproximados + contexto.
Solo LEE mercado. La bitácora vive en data/michaelfx_journal.csv.
"""
import os
import csv
import json
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from mt5_connect import ensure
from paths import DATA_DIR

JOURNAL = os.path.join(DATA_DIR, "michaelfx_journal.csv")
WATCHLIST_FILE = os.path.join(DATA_DIR, "michaelfx_watchlist.json")
DEFAULT_WATCHLIST = ["XAUUSD", "EURUSD", "GBPUSD", "US500", "NAS100"]


def load_watchlist():
    """Watchlist editable, persistida en data/michaelfx_watchlist.json (fallback al default)."""
    try:
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            wl = json.load(f)
        if isinstance(wl, list) and wl:
            return wl
    except Exception:
        pass
    return list(DEFAULT_WATCHLIST)


def save_watchlist(wl):
    seen = []
    for s in wl:
        s = str(s).strip().upper()
        if s and s not in seen:
            seen.append(s)
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f)
    return seen


def add_symbol(sym):
    """Agrega si existe en el bróker. Devuelve (ok, mensaje)."""
    sym = str(sym or "").strip().upper()
    if not sym:
        return False, "escribe un símbolo"
    mt5.symbol_select(sym, True)
    if mt5.symbol_info(sym) is None:
        return False, f"'{sym}' no existe en el bróker"
    wl = load_watchlist()
    if sym in wl:
        return False, f"{sym} ya está en la watchlist"
    wl.append(sym); save_watchlist(wl)
    return True, f"✔ {sym} agregado"


def remove_symbol(sym):
    sym = str(sym or "").strip().upper()
    wl = load_watchlist()
    if sym not in wl:
        return False, f"{sym} no está en la watchlist"
    if len(wl) <= 1:
        return False, "debe quedar al menos 1 símbolo"
    wl.remove(sym); save_watchlist(wl)
    return True, f"✔ {sym} quitado"

# Sesiones en hora UTC-5 (Perú/Ecuador, sin DST) — (inicio_min, fin_min) desde medianoche
SESSIONS_UTC5 = {"London": (90, 270), "New York": (450, 630), "Tokio": (1110, 1290)}

JOURNAL_FIELDS = [
    "id", "fecha", "hora", "simbolo", "sesion", "direccion", "escenario", "tipo_orden",
    "ob_tf", "confluencias", "entrada", "sl", "tp", "riesgo_pct", "rr_plan",
    "resultado", "r_obtenido", "pnl", "respeto_reglas", "errores", "conclusion", "screenshot",
]

TF = {"D1": mt5.TIMEFRAME_D1, "H4": mt5.TIMEFRAME_H4, "H1": mt5.TIMEFRAME_H1,
      "M15": mt5.TIMEFRAME_M15, "M5": mt5.TIMEFRAME_M5}


# ---------------- contexto de mercado ----------------
def _rates(sym, tf, n=300):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, tf, 0, n)
    if r is None or len(r) < 2:
        return None
    return pd.DataFrame(r)


def bias_tf(df):
    """Sesgo de una temporalidad: EMA20/50 + estructura por pivotes (HH/HL vs LH/LL)."""
    c = df["close"].values
    ema20 = pd.Series(c).ewm(span=20, adjust=False).mean().values
    ema50 = pd.Series(c).ewm(span=50, adjust=False).mean().values
    px = c[-1]
    if px > ema20[-1] > ema50[-1]:
        trend = "alcista"
    elif px < ema20[-1] < ema50[-1]:
        trend = "bajista"
    else:
        trend = "rango"
    # estructura: últimos pivotes (ventana 3)
    h, l = df["high"].values, df["low"].values
    k = 3
    ph = [i for i in range(k, len(h)-k) if h[i] == max(h[i-k:i+k+1])]
    pl = [i for i in range(k, len(l)-k) if l[i] == min(l[i-k:i+k+1])]
    est = "—"
    if len(ph) >= 2 and len(pl) >= 2:
        hh = h[ph[-1]] > h[ph[-2]]; hl = l[pl[-1]] > l[pl[-2]]
        if hh and hl:
            est = "HH/HL (alcista)"
        elif not hh and not hl:
            est = "LH/LL (bajista)"
        else:
            est = "mixta (rango)"
    return {"trend": trend, "estructura": est, "ema20": ema20[-1], "ema50": ema50[-1]}


def prev_day_levels(sym):
    """PDH/PDL (día anterior) + H/L de hoy, desde D1."""
    df = _rates(sym, TF["D1"], 5)
    if df is None or len(df) < 2:
        return {}
    df["t"] = pd.to_datetime(df["time"], unit="s")
    return {"PDH": float(df["high"].iloc[-2]), "PDL": float(df["low"].iloc[-2]),
            "HOY_H": float(df["high"].iloc[-1]), "HOY_L": float(df["low"].iloc[-1])}


def fib_levels(sym):
    """Fibonacci 61.8% y 75% del último swing relevante en 1H."""
    df = _rates(sym, TF["H1"], 120)
    if df is None:
        return {}
    h, l = df["high"].values, df["low"].values
    hi_i, lo_i = int(np.argmax(h[-60:])) + len(h)-60, int(np.argmin(l[-60:])) + len(l)-60
    hi, lo = h[hi_i], l[lo_i]
    rng = hi - lo
    if rng <= 0:
        return {}
    up = hi_i > lo_i   # swing alcista (low antes que high) → retrocesos hacia abajo
    if up:
        return {"dir": "retroceso de alza", "61.8%": hi - 0.618*rng, "75%": hi - 0.75*rng}
    return {"dir": "retroceso de baja", "61.8%": lo + 0.618*rng, "75%": lo + 0.75*rng}


def retracement_zone(sym, tf_name="H1", W=90):
    """Zona de retroceso actual del precio dentro del último swing (dónde poner el OB).
    Profundo 61.8-79% = zona más eficiente (ver MICHAELFX_ZONAS_OB.md)."""
    df = _rates(sym, TF[tf_name], W + 15)
    if df is None or len(df) < 30:
        return {}
    h = df["high"].values[-W:]; l = df["low"].values[-W:]
    hi = float(h.max()); lo = float(l.min()); rng = hi - lo
    if rng <= 0:
        return {}
    info = mt5.symbol_info(sym)
    px = float(info.bid) if info and info.bid else float(df["close"].iloc[-1])
    if int(np.argmax(h)) > int(np.argmin(l)):
        d = (hi - px) / rng; side = "compra (descuento)"   # swing alcista, retroceso a la baja
    else:
        d = (px - lo) / rng; side = "venta (premium)"       # swing bajista, retroceso al alza
    d = max(0.0, min(d, 1.3))
    if d < 0.382:
        z = "somera"
    elif d < 0.618:
        z = "media"
    elif d <= 0.79:
        z = "PROFUNDA · OB eficiente"
    else:
        z = "muy profunda · cerca de invalidar"
    return {"side": side, "depth": d, "zone": z, "efficient": 0.618 <= d <= 0.79,
            "hi": hi, "lo": lo}


def approx_obs(sym, tf_name, price, n=200):
    """OB aproximados (SOPORTE, no exactos): última vela opuesta antes de una ruptura de
    estructura. Devuelve el OB alcista más cercano bajo el precio y el bajista sobre el precio."""
    df = _rates(sym, TF[tf_name], n)
    if df is None:
        return {}
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    k = 3
    bull_ob = bear_ob = None
    for i in range(k, len(c)-k-1):
        # ruptura alcista: cierre rompe el max previo local → OB = última vela bajista antes
        if c[i] > max(h[i-k:i]) and l[i+1] >= l[i]:
            j = i
            while j > 0 and c[j] >= o[j]:
                j -= 1
            zlo, zhi = l[j], h[j]
            if zhi < price:                      # OB alcista bajo el precio (demanda)
                bull_ob = (zlo, zhi)
        if c[i] < min(l[i-k:i]):                 # ruptura bajista → OB = última vela alcista
            j = i
            while j > 0 and c[j] <= o[j]:
                j -= 1
            zlo, zhi = l[j], h[j]
            if zlo > price:                      # OB bajista sobre el precio (oferta)
                bear_ob = (zlo, zhi)
    return {"bull": bull_ob, "bear": bear_ob}


def current_session():
    """Sesión activa en UTC-5 + si estamos en horario operativo (máx 3h/sesión)."""
    now_utc5 = datetime.now(timezone.utc) - timedelta(hours=5)
    m = now_utc5.hour*60 + now_utc5.minute
    for name, (a, b) in SESSIONS_UTC5.items():
        if a <= m <= b:
            return {"activa": name, "en_horario": True, "cierra_en_min": b - m,
                    "hora_utc5": now_utc5.strftime("%H:%M")}
    nxt = min(SESSIONS_UTC5.items(), key=lambda kv: ((kv[1][0]-m) % 1440))
    return {"activa": None, "en_horario": False, "proxima": nxt[0],
            "faltan_min": (nxt[1][0]-m) % 1440, "hora_utc5": now_utc5.strftime("%H:%M")}


def symbol_context(sym):
    """Contexto completo de un símbolo para el cockpit."""
    ctx = {"symbol": sym}
    for tfn in ["D1", "H4", "H1"]:
        df = _rates(sym, TF[tfn], 200)
        ctx[tfn] = bias_tf(df) if df is not None else None
    info = mt5.symbol_info(sym)
    px = info.bid if info else np.nan
    ctx["price"] = px
    ctx["levels"] = prev_day_levels(sym)
    ctx["fib"] = fib_levels(sym)
    ctx["zona"] = retracement_zone(sym)
    ctx["ob_H1"] = approx_obs(sym, "H1", px)
    ctx["ob_M15"] = approx_obs(sym, "M15", px)
    return ctx


# ---------------- bitácora ----------------
def _next_id():
    if not os.path.exists(JOURNAL):
        return 1
    try:
        with open(JOURNAL, encoding="utf-8") as f:
            return sum(1 for _ in f)          # header + filas → siguiente id
    except Exception:
        return 1


def add_trade(d):
    """Agrega un trade a la bitácora. `d` = dict con campos de JOURNAL_FIELDS (los que falten van vacíos)."""
    new = not os.path.exists(JOURNAL)
    d = dict(d)
    d.setdefault("id", _next_id())
    now = datetime.now()
    d.setdefault("fecha", now.strftime("%Y-%m-%d"))
    d.setdefault("hora", now.strftime("%H:%M"))
    # rr_plan calculado si hay entrada/sl/tp
    try:
        e, s, t = float(d.get("entrada")), float(d.get("sl")), float(d.get("tp"))
        risk = abs(e - s)
        d.setdefault("rr_plan", round(abs(t - e)/risk, 2) if risk > 0 else "")
    except Exception:
        d.setdefault("rr_plan", "")
    with open(JOURNAL, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(d)
    return d["id"]


def load_trades():
    if not os.path.exists(JOURNAL):
        return pd.DataFrame(columns=JOURNAL_FIELDS)
    return pd.read_csv(JOURNAL)


def stats():
    """Expectancy y desglose: global, por escenario, por sesión, por cumplimiento de reglas."""
    d = load_trades()
    d = d[pd.to_numeric(d.get("r_obtenido"), errors="coerce").notna()] if len(d) else d
    if len(d) == 0:
        return {"n": 0, "msg": "sin trades cerrados aún"}
    r = pd.to_numeric(d["r_obtenido"], errors="coerce")
    def blk(sub):
        rr = pd.to_numeric(sub["r_obtenido"], errors="coerce").dropna()
        if len(rr) == 0:
            return None
        return {"n": len(rr), "winrate": round((rr > 0).mean()*100, 0),
                "avgR": round(rr.mean(), 2), "expectancy_R": round(rr.mean(), 2),
                "total_R": round(rr.sum(), 1)}
    out = {"global": blk(d), "por_escenario": {}, "por_sesion": {}, "por_reglas": {}}
    for k, g in d.groupby(d.get("escenario").astype(str)):
        out["por_escenario"][f"Esc {k}"] = blk(g)
    for k, g in d.groupby(d.get("sesion").astype(str)):
        out["por_sesion"][str(k)] = blk(g)
    for k, g in d.groupby(d.get("respeto_reglas").astype(str)):
        out["por_reglas"][f"reglas={k}"] = blk(g)
    return out


if __name__ == "__main__":
    ensure()
    print("=== Contexto MichaelFX (muestra) ===")
    for s in load_watchlist()[:2]:
        c = symbol_context(s)
        print(f"\n{s}  px={c['price']}")
        for tfn in ["D1", "H4", "H1"]:
            b = c[tfn]
            if b:
                print(f"  {tfn}: {b['trend']:8} · {b['estructura']}")
        print(f"  niveles: {c['levels']}")
        print(f"  fib: {c['fib']}")
    print(f"\nSesión: {current_session()}")
    print(f"\nBitácora: {stats()}")

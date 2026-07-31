"""
news_calendar.py — calendario económico (ForexFactory feed gratis) para el filtro
de noticias. Paso 1: traer eventos + mapear divisa→símbolos + ventana de blackout.

Feed FF (sin auth): ff_calendar_thisweek.json / _nextweek.json / _lastweek.json.
Campos por evento: title, country (divisa ISO), date (ISO8601 con TZ), impact (High/Medium/Low),
forecast, previous. Para el BACKTEST se usará histórico (scrapers FF, CSV por año) — paso 2.

Uso:  python news_calendar.py            # próximos eventos High que nos afectan
"""
import os
import sys
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta

from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "https://nfs.faireconomy.media/ff_calendar_{}.json"
CACHE_MIN = 60          # el calendario cambia poco → refrescar como mucho cada hora (evita 429)

# Divisa/país del evento → símbolos NUESTROS que reacciona (mapa de sensibilidad).
CCY_TO_SYMBOLS = {
    "USD": ["US500", "NAS100", "US30", "XAUUSD", "XAGUSD", "EURUSD", "WTOIL-PERP", "BTCUSD", "ETHUSD"],
    "EUR": ["EURUSD", "GER40"],
    "GBP": ["EURUSD"],           # spill parcial
    "JPY": [],                    # (no operamos JPY aún)
    "CNY": ["HK50"],             # (si se agrega Asia)
    "ALL": ["XAUUSD"],           # geopolítica → oro refugio
}


def fetch(period="thisweek"):
    """Trae el feed con CACHÉ (60 min) + fallback al caché viejo si el feed falla (429/red)."""
    cache = os.path.join(DATA_DIR, f"news_{period}.json")
    if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) < CACHE_MIN * 60:
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    try:
        req = urllib.request.Request(BASE.format(period),
                                     headers={"User-Agent": "Mozilla/5.0 (trading-bandit)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return data
    except Exception:
        if os.path.exists(cache):                 # feed caído → usar lo último bueno
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        raise


def events(periods=("thisweek",), min_impact="High"):   # el feed FF solo expone la semana actual
    order = {"Low": 0, "Medium": 1, "High": 2}
    lo = order.get(min_impact, 2)
    out = []
    for p in periods:
        try:
            for e in fetch(p):
                if order.get(e.get("impact", "Low"), 0) < lo:
                    continue
                try:
                    dt = datetime.fromisoformat(e["date"]).astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    continue
                out.append({"time": dt, "ccy": e.get("country", ""),
                            "impact": e.get("impact"), "title": e.get("title", ""),
                            "symbols": CCY_TO_SYMBOLS.get(e.get("country", ""), [])})
        except Exception as ex:
            print(f"[news] no se pudo traer {p}: {ex}")
    return sorted(out, key=lambda x: x["time"])


def in_blackout(symbol, ts, evs, before_min=30, after_min=15):
    """True si `ts` (UTC naive) cae en la ventana de una noticia High que afecta a `symbol`."""
    for e in evs:
        if symbol not in e["symbols"]:
            continue
        if e["time"] - timedelta(minutes=before_min) <= ts <= e["time"] + timedelta(minutes=after_min):
            return True, e["title"]
    return False, None


def main():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    evs = [e for e in events(min_impact="High") if e["time"] >= now - timedelta(hours=2)]
    print(f"Eventos HIGH próximos (UTC) — {len(evs)} en esta+próxima semana:")
    for e in evs[:25]:
        mx = e["time"] - timedelta(hours=6)
        syms = ",".join(e["symbols"][:4]) + ("…" if len(e["symbols"]) > 4 else "") or "-"
        print(f"  {e['time']:%m-%d %H:%M}UTC ({mx:%H:%M}mx) [{e['ccy']}] {e['title'][:40]:40} → {syms}")


if __name__ == "__main__":
    main()

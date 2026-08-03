"""
Descarga y cachea la data del carry cripto (Binance perp, público, gratis):
  - funding rate (cada 8h → agregado a diario) = la señal de carry.
  - klines diarias del perp = retorno de precio.
Canasto = coins que Pepperstone también ofrece (desplegable): BTC ETH SOL XRP ADA DOGE LTC.
Cache en data/carry/. Solo LEE de la red.
"""
import os
import sys
import time
import json
import urllib.request

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = os.path.join(os.path.dirname(__file__), "data", "carry")
os.makedirs(CACHE, exist_ok=True)
COINS = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC"]
BASE = "https://fapi.binance.com"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def fetch_funding(sym):
    """Toda la historia de funding (paginada, ascendente). Devuelve Series diaria (suma del día)."""
    rows = []
    start = 1568000000000  # ~sep-2019
    while True:
        url = f"{BASE}/fapi/v1/fundingRate?symbol={sym}&startTime={start}&limit=1000"
        batch = _get(url)
        if not batch:
            break
        rows += batch
        last = batch[-1]["fundingTime"]
        if len(batch) < 1000:
            break
        start = last + 1
        time.sleep(0.25)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["r"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    daily = df.set_index("t")["r"].resample("D").sum()   # 3 pagos/día → funding diario
    return daily


def fetch_price(sym):
    """Klines diarias (close). Hasta 1500 barras."""
    url = f"{BASE}/fapi/v1/klines?symbol={sym}&interval=1d&limit=1500"
    k = _get(url)
    idx = pd.to_datetime([x[0] for x in k], unit="ms")
    return pd.Series([float(x[4]) for x in k], index=idx, name="close")


def build():
    fund, price = {}, {}
    for c in COINS:
        sym = f"{c}USDT"
        ff = os.path.join(CACHE, f"fund_{c}.csv"); pf = os.path.join(CACHE, f"px_{c}.csv")
        if os.path.exists(ff) and os.path.exists(pf):
            fund[c] = pd.read_csv(ff, index_col=0, parse_dates=True).iloc[:, 0]
            price[c] = pd.read_csv(pf, index_col=0, parse_dates=True).iloc[:, 0]
            print(f"  {c}: cache ({len(fund[c])} funding, {len(price[c])} px)")
            continue
        try:
            f = fetch_funding(sym); p = fetch_price(sym)
            f.to_csv(ff); p.to_csv(pf)
            fund[c] = f; price[c] = p
            print(f"  {c}: {len(f)} funding ({f.index[0].date()}→{f.index[-1].date()}), {len(p)} px")
        except Exception as e:
            print(f"  {c}: FALLO {str(e)[:60]}")
    return fund, price


if __name__ == "__main__":
    print("Descargando carry cripto (Binance)...")
    build()
    print("listo → data/carry/")

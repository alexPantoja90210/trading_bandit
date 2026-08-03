"""
Ejecutor EN VIVO del VIX carry (term structure / prima de volatilidad) — DEMO.

Convierte el edge VALIDADO ([[research-conclusion-no-edge]] ★★, VIX_TERM_STRUCTURE.md)
en un bot que COLOCA ÓRDENES REALES en la cuenta demo, reutilizando la
infraestructura de seguridad del proyecto (mismo molde que rsi2_live/stf_live):
  - candado de cuenta (ensure + account_status): jamás opera fuera de la demo fijada;
  - kill switch por archivo de comando (data/svxy_command.json);
  - estado para el dashboard (data/svxy_live_status.json) y CSV de trades.

Estrategia (fiel al backtest domado + validado con rigor):
  - Instrumento: **LARGO SVXY.US** (ETF INVERSO del VIX → largo = short-vol). VIXY es
    LONGONLY en Pepperstone, así que la vía es SVXY (cross-check validado, Sharpe +0.64).
  - Señal: TS = VIX / VIX3M de la ÚLTIMA sesión CERRADA (yfinance; el bróker no da VIX3M).
    TS < 1 = CONTANGO → mantener LARGO SVXY (cosechar el roll). TS >= 1 = BACKWARDATION →
    PLANO (salir; la curva invertida avisa del estrés antes del desastre sostenido).
  - Una decisión por día (cuando aparece una nueva fecha de señal VIX/VIX3M).
  - Dimensionamiento FIJO por fracción de exposición (lo único que doma el DD: el Sharpe es
    invariante a escala). exposure_pct del balance como nocional.
  - Stop de desastre ancho (stop_pct) SOLO como red ante un gap tipo Volmageddon entre
    revisiones; la salida primaria es la señal de curva, no el stop.

Arranca en dry_run=true: registra lo que HARÍA sin enviar órdenes. Poner dry_run=false
para operar de verdad en la demo.
"""
import os
import sys
import json
import time
from datetime import datetime, timezone, date

import pandas as pd
import MetaTrader5 as mt5

from paths import load_config, DATA_DIR
from mt5_connect import ensure, account_status

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CACHE = os.path.join(os.path.dirname(__file__), "data", "futures")
STATE_FILE = os.path.join(DATA_DIR, "svxy_live_state.json")
STATUS_FILE = os.path.join(DATA_DIR, "svxy_live_status.json")
TRADES_CSV = os.path.join(DATA_DIR, "svxy_live_trades.csv")
COMMAND_FILE = os.path.join(DATA_DIR, "svxy_command.json")


# ----------------------------------------------------------------------------
# util de estado / log (idéntico patrón robusto a rsi2_live)
# ----------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)
        for _ in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.15)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, default=str)
    except Exception:
        pass


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def log_trade(row):
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write("ts,event,symbol,price,lot,ret_pct,ts_ratio,reason,ticket,dry_run\n")
        f.write("{ts},{event},{symbol},{price},{lot},{ret_pct},{ts_ratio},{reason},{ticket},{dry}\n".format(**row))


# ----------------------------------------------------------------------------
# SEÑAL: TS = VIX / VIX3M de la última sesión cerrada (yfinance, cache diario)
# ----------------------------------------------------------------------------
def _read_cache(name):
    fp = os.path.join(CACHE, f"{name}.csv")
    if not os.path.exists(fp):
        return None
    try:
        return pd.read_csv(fp, index_col=0, parse_dates=True).iloc[:, 0]
    except Exception:
        return None


# Fuentes de la señal, en orden de preferencia. yfinance ^VIX3M va SEMANAS retrasado
# (comprobado: se atascó en 2026-07-17); el CSV oficial de CBOE está al día → primario.
CBOE = {
    "VIX": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "VIX3M": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
}
YF = {"VIX": "^VIX", "VIX3M": "^VIX3M"}


def _from_cboe(name):
    url = CBOE.get(name)
    if not url:
        return None
    d = pd.read_csv(url)
    d.columns = [c.strip().upper() for c in d.columns]
    dcol = [c for c in d.columns if "DATE" in c][0]
    ccol = [c for c in d.columns if "CLOSE" in c][-1]
    s = pd.Series(d[ccol].values, index=pd.to_datetime(d[dcol])).dropna()
    s.name = name
    return s


def _from_yf(name):
    import yfinance as yf
    import numpy as np
    d = yf.download(YF[name], period="max", interval="1d", progress=False, auto_adjust=True)
    if d is None or len(d) == 0:
        return None
    s = d["Close"] if "Close" in d.columns else d.iloc[:, 0]
    return pd.Series(np.asarray(s).ravel(), index=pd.to_datetime(d.index)).dropna()


def _refresh(name):
    """CBOE oficial → yfinance → None. Cachea si obtiene algo (sin tumbar el bot)."""
    for src, fn in (("CBOE", _from_cboe), ("yfinance", _from_yf)):
        try:
            s = fn(name)
            if s is not None and len(s) > 0:
                os.makedirs(CACHE, exist_ok=True)
                s.to_csv(os.path.join(CACHE, f"{name}.csv"))
                return s
        except Exception as e:
            print(f"[{_now():%H:%M:%S}] {src} {name} falló ({e})")
    return None


def get_signal(cfg_s, state):
    """TS de la última sesión cerrada. Refresca yfinance a lo sumo 1×/día; cae a cache.
    Devuelve dict(date, vix, vix3m, ts, contango) o None."""
    thr = cfg_s.get("contango_thr", 1.0)
    max_stale = int(cfg_s.get("max_stale_days", 5))   # señal más vieja que esto = no operar
    today = _now().date().isoformat()
    need = state.get("data_date") != today            # refrescar solo 1 vez por día natural
    vix = _refresh("VIX") if need else None
    v3 = _refresh("VIX3M") if need else None
    if vix is None:
        vix = _read_cache("VIX")
    if v3 is None:
        v3 = _read_cache("VIX3M")
    if vix is None or v3 is None:
        return None
    if need and vix is not None and v3 is not None:
        state["data_date"] = today
    df = pd.DataFrame({"VIX": vix, "VIX3M": v3}).dropna()
    if len(df) == 0:
        return None
    row = df.iloc[-1]
    sig_date = df.index[-1].date()
    stale = (_now().date() - sig_date).days
    ts = float(row["VIX"] / row["VIX3M"])
    return {
        "date": str(sig_date), "stale_days": stale,
        "vix": float(row["VIX"]), "vix3m": float(row["VIX3M"]),
        "ts": ts, "contango": ts < thr,
        "fresh": stale <= max_stale,
    }


# ----------------------------------------------------------------------------
# broker: posición / apertura / cierre (LARGO SVXY)
# ----------------------------------------------------------------------------
def get_position(symbol, magic):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    for p in positions:
        if p.magic == magic and p.type == mt5.ORDER_TYPE_BUY:
            return p
    return None


def size_lot(info, balance, exposure_pct, price):
    """Nocional = exposure_pct × balance; lote = nocional / (precio × contrato). Fracción fija."""
    contract = info.trade_contract_size or 1.0
    lot = info.volume_min
    if balance and price > 0 and contract > 0:
        lot = (balance * exposure_pct) / (price * contract)
    step = info.volume_step or 0.1
    lot = round(lot / step) * step
    lot = max(info.volume_min, min(info.volume_max, lot))
    return round(lot, 2)


def open_long(symbol, cfg_s, dry_run):
    """Abre LARGO de mercado con stop de desastre ancho. Devuelve (ok, price, lot, sl, ticket, msg)."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return False, None, None, None, None, "symbol_not_found"
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.ask <= 0:
        return False, None, None, None, None, "no_tick"

    digits = info.digits
    price = tick.ask
    acc = mt5.account_info()
    balance = acc.balance if acc else 0.0
    lot = size_lot(info, balance, cfg_s.get("exposure_pct", 0.30), price)
    sl = round(price * (1.0 - cfg_s.get("stop_pct", 0.30)), digits)

    if dry_run:
        return True, round(price, digits), lot, sl, None, "dry_run"

    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
        "type": mt5.ORDER_TYPE_BUY, "price": round(price, digits), "sl": sl,
        "deviation": 15, "magic": cfg_s["magic"], "comment": "svxy_live",
        "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        return False, round(price, digits), lot, sl, None, f"retcode={getattr(res,'retcode',None)}"
    return True, res.price, lot, sl, res.order, "ok"


def close_long(position, dry_run):
    info = mt5.symbol_info(position.symbol)
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None or tick.bid <= 0:
        return False, None, "no_tick"
    price = tick.bid
    if dry_run:
        return True, round(price, info.digits), "dry_run"
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol,
        "volume": position.volume, "type": mt5.ORDER_TYPE_SELL,
        "position": position.ticket, "price": round(price, info.digits),
        "deviation": 15, "magic": position.magic, "comment": "svxy_exit",
        "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        return False, round(price, info.digits), f"retcode={getattr(res,'retcode',None)}"
    return True, res.price, "ok"


# ----------------------------------------------------------------------------
# lógica: una decisión por día según la curva
# ----------------------------------------------------------------------------
def process(cfg, state):
    cfg_s = cfg["svxy"]
    dry = bool(cfg_s.get("dry_run", True))
    symbol = cfg_s.get("symbol", "SVXY.US")
    magic = cfg_s["magic"]

    ok, acc = account_status(cfg)
    status = {"updated": _now().isoformat(), "account_ok": ok, "account": acc,
              "dry_run": dry, "running": True, "symbol": symbol}
    if not ok:
        print(f"[{_now():%H:%M:%S}] CUENTA NO VERIFICADA {acc.get('reasons')} — no se opera")
        return state, status

    sig = get_signal(cfg_s, state)
    if sig is None:
        print(f"[{_now():%H:%M:%S}] sin señal (VIX/VIX3M no disponible)")
        status["signal"] = None
        return state, status
    if not sig["fresh"]:
        # data rancia (p.ej. feed caído): NO operar, mantener posición tal cual y avisar.
        print(f"[{_now():%H:%M:%S}] señal RANCIA ({sig['stale_days']}d, {sig['date']}) — no se opera, mantengo posición")
        status["signal"] = sig
        pos = get_position(symbol, magic)
        status["position"] = {"in_position": pos is not None,
                              "entry": pos.price_open if pos else None,
                              "lot": pos.volume if pos else None, "unrealized_pct": 0.0}
        return state, status

    pos = get_position(symbol, magic)
    # En vivo el bróker manda; el stop de desastre pudo cerrar entre corridas.
    if not dry and state.get("in_pos") and pos is None:
        print(f"[{_now():%H:%M:%S}] {symbol}: posición cerrada por el bróker (stop de desastre)")
        log_trade({"ts": _now().isoformat(), "event": "STOP_HIT", "symbol": symbol,
                   "price": "", "lot": state.get("lot", ""), "ret_pct": "",
                   "ts_ratio": round(sig["ts"], 4), "reason": "broker_stop",
                   "ticket": state.get("ticket", ""), "dry": dry})
        state["in_pos"] = False
    held = state.get("in_pos", False) if dry else (pos is not None)

    new_day = sig["date"] != state.get("last_signal_date")
    if new_day:
        if sig["contango"] and not held:
            good, price, lot, sl, ticket, msg = open_long(symbol, cfg_s, dry)
            tag = "DRY" if dry else "LIVE"
            if good:
                state.update({"in_pos": True, "entry": price, "lot": lot, "sl": sl,
                              "ticket": ticket, "entry_date": sig["date"]})
                print(f"[{_now():%H:%M:%S}] {tag} ENTRY {symbol} @ {price:.2f}  lot={lot}  "
                      f"SL={sl:.2f}  TS={sig['ts']:.3f} (contango)")
                log_trade({"ts": _now().isoformat(), "event": "ENTRY", "symbol": symbol,
                           "price": price, "lot": lot, "ret_pct": "", "ts_ratio": round(sig["ts"], 4),
                           "reason": "contango", "ticket": ticket or "", "dry": dry})
            else:
                print(f"[{_now():%H:%M:%S}] {symbol}: apertura fallida ({msg})")
        elif (not sig["contango"]) and held:
            entry_ref = pos.price_open if pos else (state.get("entry") or 0)
            if dry:
                tick = mt5.symbol_info_tick(symbol)
                price = tick.bid if tick and tick.bid > 0 else (state.get("entry") or 0)
                good, tkt, lot_c = True, state.get("ticket", ""), state.get("lot", "")
            else:
                good, price, msg = close_long(pos, dry)
                lot_c, tkt = pos.volume, pos.ticket
            if good:
                ret = ((price - entry_ref) / entry_ref * 100) if entry_ref else 0.0
                tag = "DRY" if dry else "LIVE"
                print(f"[{_now():%H:%M:%S}] {tag} EXIT  {symbol} @ {price:.2f}  ret={ret:+.2f}%  "
                      f"TS={sig['ts']:.3f} (backwardation)")
                log_trade({"ts": _now().isoformat(), "event": "EXIT", "symbol": symbol,
                           "price": round(price, 2), "lot": lot_c, "ret_pct": round(ret, 2),
                           "ts_ratio": round(sig["ts"], 4), "reason": "backwardation",
                           "ticket": tkt or "", "dry": dry})
                state.update({"in_pos": False, "entry": None, "lot": None, "sl": None, "ticket": None})
            else:
                print(f"[{_now():%H:%M:%S}] {symbol}: cierre fallido ({msg})")
        else:
            estado = "en posición (contango)" if held else "plano (backwardation)"
            print(f"[{_now():%H:%M:%S}] {symbol}: sin cambio — {estado}, TS={sig['ts']:.3f}")
        state["last_signal_date"] = sig["date"]

    # snapshot para el dashboard
    pos = get_position(symbol, magic)
    if pos:
        entry_p, lot_p, in_p = pos.price_open, pos.volume, True
        cur = pos.price_current
    elif dry and state.get("in_pos"):
        entry_p, lot_p, in_p = state.get("entry"), state.get("lot"), True
        tick = mt5.symbol_info_tick(symbol)
        cur = tick.bid if tick and tick.bid > 0 else entry_p
    else:
        entry_p, lot_p, in_p, cur = None, None, False, None
    unreal = ((cur - entry_p) / entry_p * 100) if in_p and entry_p and cur else 0.0
    status["signal"] = sig
    status["position"] = {"in_position": in_p, "entry": entry_p, "lot": lot_p,
                          "unrealized_pct": round(unreal, 2)}
    return state, status


# ----------------------------------------------------------------------------
# loop principal + kill switch
# ----------------------------------------------------------------------------
def check_stop():
    cmd = _load(COMMAND_FILE, {})
    return bool(cmd.get("stop"))


def main():
    cfg = load_config()
    if not cfg.get("svxy", {}).get("enabled", False):
        print("svxy.enabled=false — nada que hacer"); return
    if not ensure(cfg):
        print("No se pudo conectar al terminal demo"); return

    dry = cfg["svxy"].get("dry_run", True)
    print(f"=== VIX carry LIVE ({'DRY-RUN' if dry else 'ÓRDENES REALES'}) — "
          f"LARGO {cfg['svxy'].get('symbol','SVXY.US')} en contango, magic {cfg['svxy']['magic']} ===")

    once = "--once" in sys.argv
    state = _load(STATE_FILE, {})
    while True:
        if check_stop():
            print(f"[{_now():%H:%M:%S}] STOP recibido — saliendo")
            st = _load(STATUS_FILE, {}); st["running"] = False
            _save(STATUS_FILE, st)
            _save(COMMAND_FILE, {})
            break
        cfg = load_config()
        if not ensure(cfg):
            print(f"[{_now():%H:%M:%S}] sin conexión demo — reintento")
            time.sleep(cfg["svxy"].get("sleep", 300)); continue
        state, status = process(cfg, state)
        _save(STATE_FILE, state)
        _save(STATUS_FILE, status)
        if once:
            break
        time.sleep(cfg["svxy"].get("sleep", 300))


if __name__ == "__main__":
    main()

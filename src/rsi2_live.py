"""
Ejecutor EN VIVO del RSI(2) mean-reversion (Connors) sobre índices US — DEMO.

Es el paso "demo-live": convierte el observador de papel ([[rsi2_observer]]) en un
bot que COLOCA ÓRDENES REALES en la cuenta demo, reutilizando toda la
infraestructura de seguridad del proyecto:
  - candado de cuenta (ensure + account_status): jamás opera fuera de la demo fijada;
  - dimensionamiento por riesgo (risk_per_trade del balance) con stop de desastre ATR;
  - kill switch por archivo de comando (data/rsi2_command.json);
  - estado para el dashboard (data/rsi2_live_status.json) y CSV de trades.

Fiel al backtest validado (rsi2_meanrev):
  - solo LARGOS; entrada RSI(2)<entry y close>SMA200; salida close>SMA5 / RSI2>exit / max_hold.
  - Decide sobre la ÚLTIMA BARRA D1 CERRADA (no la barra en formación) → una acción por día.
  - Un stop de desastre (stop_atr×ATR) protege ante gaps entre revisiones y fija el lote.

Arranca en dry_run=true (config): registra lo que HARÍA sin enviar órdenes. Poner
dry_run=false para operar de verdad en la demo.
"""
import os
import sys
import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from paths import load_config, DATA_DIR
from mt5_connect import ensure, account_status
from rsi2_meanrev import rsi, atr_series

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE_FILE = os.path.join(DATA_DIR, "rsi2_live_state.json")
STATUS_FILE = os.path.join(DATA_DIR, "rsi2_live_status.json")
TRADES_CSV = os.path.join(DATA_DIR, "rsi2_live_trades.csv")
COMMAND_FILE = os.path.join(DATA_DIR, "rsi2_command.json")


# ----------------------------------------------------------------------------
# util de estado / log
# ----------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    # robusto ante la carrera de archivos de Windows (os.replace → PermissionError
    # si otro proceso tiene el JSON abierto): reintenta y cae a escritura directa.
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
            f.write("ts,event,symbol,price,lot,ret_pct,reason,ticket,dry_run\n")
        f.write("{ts},{event},{symbol},{price},{lot},{ret_pct},{reason},{ticket},{dry}\n".format(**row))


# ----------------------------------------------------------------------------
# broker: posición / apertura / cierre
# ----------------------------------------------------------------------------
def get_position(symbol, magic):
    """Posición LARGA abierta del bot en el símbolo (o None). El bróker es la
    fuente de verdad: si el stop de desastre saltó entre corridas, aquí ya no está."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    for p in positions:
        if p.magic == magic and p.type == mt5.ORDER_TYPE_BUY:
            return p
    return None


def size_lot(info, acc, risk_pct, sl_dist):
    """Lote tal que perder el stop de desastre ≈ risk_pct del balance."""
    tick_size = info.trade_tick_size or info.point
    tick_value = info.trade_tick_value
    lot = info.volume_min
    if acc is not None and tick_size > 0 and tick_value > 0 and sl_dist > 0:
        loss_per_lot = (sl_dist / tick_size) * tick_value
        if loss_per_lot > 0:
            lot = (acc.balance * risk_pct) / loss_per_lot
    step = info.volume_step or 0.01
    lot = round(lot / step) * step
    lot = max(info.volume_min, min(info.volume_max, lot))
    return round(lot, 2)


def open_long(symbol, cfg_r, atr_val, dry_run):
    """Abre un LARGO de mercado con stop de desastre. Devuelve (ok, price, lot, sl, ticket, msg)."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return False, None, None, None, None, "symbol_not_found"
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, None, None, None, None, "no_tick"

    digits = info.digits
    point = info.point
    spread = tick.ask - tick.bid
    min_dist = max(info.trade_stops_level * point, spread * 2, 10 * point)
    sl_dist = max(cfg_r["stop_atr"] * atr_val, min_dist) if atr_val and atr_val > 0 else min_dist

    acc = mt5.account_info()
    lot = size_lot(info, acc, cfg_r["risk_per_trade"], sl_dist)
    price = tick.ask
    sl = round(price - sl_dist, digits)

    if dry_run:
        return True, round(price, digits), lot, sl, None, "dry_run"

    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
        "type": mt5.ORDER_TYPE_BUY, "price": round(price, digits), "sl": sl,
        "deviation": 10, "magic": cfg_r["magic"], "comment": "rsi2_live",
        "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        return False, round(price, digits), lot, sl, None, f"retcode={getattr(res,'retcode',None)}"
    return True, res.price, lot, sl, res.order, "ok"


def close_long(position, dry_run):
    """Cierra el LARGO al mercado. Devuelve (ok, price, msg)."""
    info = mt5.symbol_info(position.symbol)
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        return False, None, "no_tick"
    price = tick.bid
    if dry_run:
        return True, round(price, info.digits), "dry_run"
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol,
        "volume": position.volume, "type": mt5.ORDER_TYPE_SELL,
        "position": position.ticket, "price": round(price, info.digits),
        "deviation": 10, "magic": position.magic, "comment": "rsi2_exit",
        "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        return False, round(price, info.digits), f"retcode={getattr(res,'retcode',None)}"
    return True, res.price, "ok"


# ----------------------------------------------------------------------------
# lógica RSI(2) sobre la última barra D1 CERRADA
# ----------------------------------------------------------------------------
def eval_symbol(sym, cfg_r):
    """Devuelve dict con señal calculada sobre la última barra cerrada, o None."""
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 400)
    if r is None or len(r) < cfg_r["sma_trend"] + 5:
        return None
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
    close = df["close"].values; high = df["high"].values; low = df["low"].values

    sma200 = pd.Series(close).rolling(cfg_r["sma_trend"]).mean().values
    sma5 = pd.Series(close).rolling(cfg_r["sma_exit"]).mean().values
    r2 = rsi(close, cfg_r["rsi_len"])
    atr = atr_series(high, low, close, cfg_r["atr_len"])

    i = -2  # última barra CERRADA (‑1 es la que aún se forma)
    if not (np.isfinite(sma200[i]) and np.isfinite(sma5[i]) and np.isfinite(r2[i]) and np.isfinite(atr[i])):
        return None
    return {
        "bar_time": str(df["time"].iloc[i]),
        "close": float(close[i]), "sma200": float(sma200[i]),
        "sma5": float(sma5[i]), "rsi2": float(r2[i]), "atr": float(atr[i]),
        "entry_signal": bool(close[i] > sma200[i] and r2[i] < cfg_r["entry_rsi"]),
        "exit_signal": bool(close[i] > sma5[i] or r2[i] > cfg_r["exit_rsi"]),
    }


def process(cfg, state):
    """Una pasada por todos los símbolos. Muta y devuelve state + status para el dashboard."""
    cfg_r = cfg["rsi2"]
    dry = bool(cfg_r.get("dry_run", True))
    magic = cfg_r["magic"]

    ok, acc = account_status(cfg)
    status = {"updated": _now().isoformat(), "account_ok": ok, "account": acc,
              "dry_run": dry, "running": True, "symbols": {}}
    if not ok:
        print(f"[{_now():%H:%M:%S}] CUENTA NO VERIFICADA {acc.get('reasons')} — no se opera")
        status["running"] = True
        return state, status

    n_open = sum(1 for s in cfg_r["symbols"] if get_position(s, magic))

    for sym in cfg_r["symbols"]:
        sig = eval_symbol(sym, cfg_r)
        if sig is None:
            continue
        st = state.get(sym, {})
        pos = get_position(sym, magic)
        new_bar = sig["bar_time"] != st.get("last_bar")

        # En vivo el bróker es la verdad; en dry-run el estado local lo es.
        if not dry and st.get("in_pos") and pos is None:
            # la posición desapareció → el stop de desastre saltó entre corridas
            print(f"[{_now():%H:%M:%S}] {sym}: posición cerrada por el bróker (stop de desastre)")
            log_trade({"ts": _now().isoformat(), "event": "STOP_HIT", "symbol": sym,
                       "price": sig["close"], "lot": st.get("lot", ""), "ret_pct": "",
                       "reason": "broker_stop", "ticket": st.get("ticket", ""), "dry": dry})
            st = {}
        held = st.get("in_pos", False) if dry else (pos is not None)

        # Solo actuamos en una barra D1 recién cerrada (una decisión por día)
        if new_bar:
            if not held:
                if sig["entry_signal"] and n_open < cfg_r.get("max_positions", 99):
                    good, price, lot, sl, ticket, msg = open_long(sym, cfg_r, sig["atr"], dry)
                    tag = "DRY" if dry else "LIVE"
                    if good:
                        n_open += 1
                        st = {"in_pos": True, "entry": price, "lot": lot, "sl": sl,
                              "ticket": ticket, "entry_bar": sig["bar_time"], "bars_held": 0}
                        print(f"[{_now():%H:%M:%S}] {tag} ENTRY {sym} @ {price:,.2f}  lot={lot}  "
                              f"SL={sl:,.2f}  RSI2={sig['rsi2']:.1f}")
                        log_trade({"ts": _now().isoformat(), "event": "ENTRY", "symbol": sym,
                                   "price": price, "lot": lot, "ret_pct": "", "reason": "rsi2<entry",
                                   "ticket": ticket or "", "dry": dry})
                    else:
                        print(f"[{_now():%H:%M:%S}] {sym}: apertura fallida ({msg})")
            elif held:
                st["bars_held"] = st.get("bars_held", 0) + 1
                reason = None
                if sig["exit_signal"]:
                    reason = "close>SMA5/RSI2>exit"
                elif st["bars_held"] >= cfg_r["max_hold_days"]:
                    reason = "max_hold"
                if reason:
                    tag = "DRY" if dry else "LIVE"
                    entry_ref = pos.price_open if pos else st.get("entry", sig["close"])
                    if dry:
                        good, price, lot_c, tkt = True, sig["close"], st.get("lot", ""), st.get("ticket", "")
                    else:
                        good, price, msg = close_long(pos, dry)
                        lot_c, tkt = pos.volume, pos.ticket
                    if good:
                        n_open = max(0, n_open - 1)
                        ret = (price - entry_ref) / entry_ref * 100
                        print(f"[{_now():%H:%M:%S}] {tag} EXIT  {sym} @ {price:,.2f}  "
                              f"ret={ret:+.2f}%  ({reason})")
                        log_trade({"ts": _now().isoformat(), "event": "EXIT", "symbol": sym,
                                   "price": price, "lot": lot_c, "ret_pct": round(ret, 2),
                                   "reason": reason, "ticket": tkt or "", "dry": dry})
                        st = {}
                    else:
                        print(f"[{_now():%H:%M:%S}] {sym}: cierre fallido ({msg})")
            st["last_bar"] = sig["bar_time"]

        # snapshot para el dashboard (en dry-run refleja la posición simulada)
        pos = get_position(sym, magic)
        if pos:
            entry_p, lot_p, sl_p, in_p = pos.price_open, pos.volume, round(pos.sl, 2), True
        elif dry and st.get("in_pos"):
            entry_p, lot_p, sl_p, in_p = st.get("entry"), st.get("lot"), st.get("sl"), True
        else:
            entry_p, lot_p, sl_p, in_p = None, None, None, False
        # no-realizado con el PRECIO ACTUAL del bróker (no el cierre D1, que queda viejo intradía)
        cur_price = pos.price_current if pos else sig["close"]
        unreal = ((cur_price - entry_p) / entry_p * 100) if in_p and entry_p else 0.0
        status["symbols"][sym] = {
            "bar": sig["bar_time"], "price": round(sig["close"], 2),
            "rsi2": round(sig["rsi2"], 1),
            "vs_sma200_pct": round((sig["close"] / sig["sma200"] - 1) * 100, 2),
            "in_position": in_p, "unrealized_pct": round(unreal, 2),
            "lot": lot_p, "sl": sl_p,
            "entry_signal": sig["entry_signal"], "exit_signal": sig["exit_signal"],
        }
        state[sym] = st

    return state, status


# ----------------------------------------------------------------------------
# loop principal + kill switch
# ----------------------------------------------------------------------------
def check_stop():
    cmd = _load(COMMAND_FILE, {})
    return bool(cmd.get("stop"))


def main():
    cfg = load_config()
    if not cfg.get("rsi2", {}).get("enabled", False):
        print("rsi2.enabled=false — nada que hacer"); return
    if not ensure(cfg):
        print("No se pudo conectar al terminal demo"); return

    dry = cfg["rsi2"].get("dry_run", True)
    print(f"=== RSI(2) LIVE ({'DRY-RUN' if dry else 'ÓRDENES REALES'}) — símbolos "
          f"{cfg['rsi2']['symbols']}, magic {cfg['rsi2']['magic']} ===")

    once = "--once" in sys.argv
    state = _load(STATE_FILE, {})
    while True:
        if check_stop():
            print(f"[{_now():%H:%M:%S}] STOP recibido — saliendo")
            st = _load(STATUS_FILE, {}); st["running"] = False
            _save(STATUS_FILE, st)
            _save(COMMAND_FILE, {})  # consumir el comando
            break
        cfg = load_config()  # recarga en caliente (permite editar dry_run/params)
        if not ensure(cfg):
            print(f"[{_now():%H:%M:%S}] sin conexión demo — reintento")
            time.sleep(cfg["rsi2"].get("sleep", 60)); continue
        state, status = process(cfg, state)
        _save(STATE_FILE, state)
        _save(STATUS_FILE, status)
        if once:
            break
        time.sleep(cfg["rsi2"].get("sleep", 60))


if __name__ == "__main__":
    main()

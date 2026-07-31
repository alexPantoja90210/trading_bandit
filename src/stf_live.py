"""
Ejecutor DEMO-LIVE del Smart Trend Follower (Familia B, validado en H4).

Coloca órdenes reales en la demo siguiendo la mecánica validada:
- Timeframe H4, cesta oro (XAUUSD) + BTC (BTCUSD).
- Filtro tendencia: largos solo si close>EMA200; cortos si close<EMA200.
- Entrada: ruptura Donchian 55 (close rompe el máx/mín de las 55 barras previas).
- Stop inicial = 2.5*ATR(14). Trailing Chandelier 3*ATR desde el extremo, con
  trinquete (nunca retrocede) → se modifica el SL en el bróker en cada barra.
- Sin TP. Flip en señal opuesta. Riesgo 0.5%/trade sobre el stop inicial.

Swing (holds días-semanas): decide sobre la última barra H4 CERRADA (sin repaint).
Reutiliza candado de cuenta, kill switch (data/stf_command.json), sizing por riesgo,
magic propio (220004, aislado del resto). Arranca en dry_run.
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE_FILE = os.path.join(DATA_DIR, "stf_live_state.json")
STATUS_FILE = os.path.join(DATA_DIR, "stf_live_status.json")
TRADES_CSV = os.path.join(DATA_DIR, "stf_live_trades.csv")
COMMAND_FILE = os.path.join(DATA_DIR, "stf_command.json")


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
            f.write("ts,event,symbol,side,price,lot,ret_R,reason,ticket,dry\n")
        f.write("{ts},{event},{symbol},{side},{price},{lot},{ret_R},{reason},{ticket},{dry}\n".format(**row))


def atr_series(high, low, close, length):
    prev = np.roll(close, 1); prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).rolling(length).mean().values


# ---------------------------------------------------------------------------
# broker
# ---------------------------------------------------------------------------
def get_position(symbol, magic):
    ps = mt5.positions_get(symbol=symbol)
    if not ps:
        return None
    for p in ps:
        if p.magic == magic:
            return p
    return None


def size_lot(info, risk_pct, sl_dist):
    acc = mt5.account_info()
    tick_size = info.trade_tick_size or info.point
    tick_value = info.trade_tick_value
    lot = info.volume_min
    if acc is not None and tick_size > 0 and tick_value > 0 and sl_dist > 0:
        loss_per_lot = (sl_dist / tick_size) * tick_value
        if loss_per_lot > 0:
            lot = (acc.balance * risk_pct) / loss_per_lot
    step = info.volume_step or 0.01
    lot = round(lot / step) * step
    return round(max(info.volume_min, min(info.volume_max, lot)), 2)


def open_pos(symbol, side, oneR, cfg_s, dry):
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    if not info.visible:
        mt5.symbol_select(symbol, True); info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    digits = info.digits
    price = tick.ask if side == 1 else tick.bid
    sl = round(price - side * oneR, digits)
    lot = size_lot(info, cfg_s["risk_per_trade"], oneR)
    if dry:
        return {"price": round(price, digits), "lot": lot, "sl": sl, "ticket": None}
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
           "type": mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL,
           "price": round(price, digits), "sl": sl, "deviation": 20,
           "magic": cfg_s["magic"], "comment": "stf_live",
           "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC}
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[{_now():%H:%M:%S}] {symbol}: apertura fallida retcode={getattr(res,'retcode',None)}")
        return None
    return {"price": res.price, "lot": lot, "sl": sl, "ticket": res.order}


def modify_sl(pos, new_sl, dry):
    if dry or pos is None:
        return
    info = mt5.symbol_info(pos.symbol)
    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
           "position": pos.ticket, "sl": round(new_sl, info.digits), "tp": 0.0}
    mt5.order_send(req)


def close_pos(pos, dry):
    info = mt5.symbol_info(pos.symbol)
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return None
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
    if dry:
        return round(price, info.digits)
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
           "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
           "position": pos.ticket, "price": round(price, info.digits), "deviation": 20,
           "magic": pos.magic, "comment": "stf_exit",
           "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC}
    res = mt5.order_send(req)
    return res.price if (res and res.retcode == mt5.TRADE_RETCODE_DONE) else None


# ---------------------------------------------------------------------------
# lógica STF por símbolo
# ---------------------------------------------------------------------------
def manage_symbol(sym, cfg_s, dry, st):
    magic = cfg_s["magic"]
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H4, 0, 800)
    if r is None or len(r) < cfg_s["ema_len"] + cfg_s["donchian"] + 5:
        return st, {"error": "sin_datos", "symbol": sym}
    df = pd.DataFrame(r)
    times = df["time"].values
    high = df["high"].values; low = df["low"].values; close = df["close"].values
    ema = pd.Series(close).ewm(span=cfg_s["ema_len"], adjust=False).mean().values
    atr = atr_series(high, low, close, cfg_s["atr_len"])
    dch_hi = pd.Series(high).rolling(cfg_s["donchian"]).max().shift(1).values
    dch_lo = pd.Series(low).rolling(cfg_s["donchian"]).min().shift(1).values

    i = len(close) - 2                       # última barra CERRADA (sin repaint)
    if not (np.isfinite(ema[i]) and np.isfinite(atr[i]) and atr[i] > 0
            and np.isfinite(dch_hi[i]) and np.isfinite(dch_lo[i])):
        return st, {"error": "warmup", "symbol": sym}
    close_i = float(close[i]); atr_i = float(atr[i]); bar_time = int(times[i])
    long_sig = close_i > dch_hi[i] and close_i > ema[i]
    short_sig = close_i < dch_lo[i] and close_i < ema[i]
    tag = "DRY" if dry else "LIVE"
    ch = cfg_s["chandelier_atr"]; init = cfg_s["init_stop_atr"]

    pos = get_position(sym, magic)
    # ¿stop-out entre barras? (estado dice en-pos pero el bróker ya no la tiene)
    if not dry and st.get("in_pos") and pos is None:
        print(f"[{_now():%H:%M:%S}] {sym}: cerrada por el bróker (stop/trailing)")
        log_trade({"ts": _now().isoformat(), "event": "STOP", "symbol": sym,
                   "side": "long" if st.get("side") == 1 else "short", "price": close_i,
                   "lot": st.get("lot", ""), "ret_R": "", "reason": "broker_stop",
                   "ticket": st.get("ticket", ""), "dry": dry})
        st = {}
    held = st.get("in_pos", False) if dry else (pos is not None)
    side = st.get("side") if dry else ((1 if pos.type == 0 else -1) if pos else None)
    new_bar = bar_time != st.get("last_bar")

    def do_open(s):
        oneR = init * atr_i
        o = open_pos(sym, s, oneR, cfg_s, dry)
        if not o:
            return st
        print(f"[{_now():%H:%M:%S}] {tag} ENTRY {sym} {'LARGO' if s==1 else 'CORTO'} "
              f"@ {o['price']:,.2f} lot={o['lot']} SL={o['sl']:,.2f} (Donchian break)")
        log_trade({"ts": _now().isoformat(), "event": "ENTRY", "symbol": sym,
                   "side": "long" if s == 1 else "short", "price": o["price"], "lot": o["lot"],
                   "ret_R": "", "reason": "donchian_break", "ticket": o["ticket"] or "", "dry": dry})
        return {"in_pos": True, "side": s, "entry": o["price"], "oneR": oneR, "lot": o["lot"],
                "sl": o["sl"], "ticket": o["ticket"], "entry_bar": bar_time,
                "ext": (high[i] if s == 1 else low[i])}

    def do_close(reason):
        if not dry and pos is not None:
            price = close_pos(pos, dry); entry = pos.price_open
        else:
            price = close_i; entry = st.get("entry", close_i)
        if price is None:
            return st
        R = ((price - entry) * (st.get("side", 1)) / st.get("oneR", atr_i))
        print(f"[{_now():%H:%M:%S}] {tag} EXIT {sym} @ {price:,.2f} R={R:+.2f} ({reason})")
        log_trade({"ts": _now().isoformat(), "event": "EXIT", "symbol": sym,
                   "side": "long" if st.get("side") == 1 else "short", "price": round(price, 2),
                   "lot": st.get("lot", ""), "ret_R": round(R, 2), "reason": reason,
                   "ticket": st.get("ticket", ""), "dry": dry})
        return {}

    if new_bar:
        if held:
            # trailing Chandelier con trinquete → modificar SL en el bróker
            entry_bt = st.get("entry_bar", bar_time)
            idx0 = int(np.searchsorted(times, entry_bt))
            idx0 = max(0, min(idx0, i))
            if side == 1:
                ext = float(np.max(high[idx0:i + 1]))
                new_sl = max(st.get("sl", -1e18), ext - ch * atr_i)
            else:
                ext = float(np.min(low[idx0:i + 1]))
                new_sl = min(st.get("sl", 1e18), ext + ch * atr_i)
            if pos is not None and abs(new_sl - pos.sl) > mt5.symbol_info(sym).point:
                modify_sl(pos, new_sl, dry)
            st["ext"] = ext; st["sl"] = new_sl
            # flip en señal opuesta
            if (side == 1 and short_sig) or (side == -1 and long_sig):
                st = do_close("flip")
                st = do_open(-side)
        elif long_sig:
            st = do_open(1)
        elif short_sig:
            st = do_open(-1)
        st["last_bar"] = bar_time

    # snapshot
    pos = get_position(sym, magic)
    in_p = st.get("in_pos", False) if dry else (pos is not None)
    entry = (pos.price_open if pos else st.get("entry"))
    unreal_R = 0.0
    if in_p and entry and st.get("oneR"):
        cur = pos.price_current if pos else close_i   # precio ACTUAL del bróker
        unreal_R = (cur - entry) * st.get("side", 1) / st["oneR"]
    snap = {"bar": str(pd.to_datetime(bar_time, unit="s")), "close": round(close_i, 2),
            "ema200": round(float(ema[i]), 2), "vs_ema": "sobre" if close_i > ema[i] else "bajo",
            "dch_hi": round(float(dch_hi[i]), 2), "dch_lo": round(float(dch_lo[i]), 2),
            "in_position": in_p, "side": ("long" if st.get("side") == 1 else ("short" if in_p else None)),
            "sl": (round(pos.sl, 2) if pos else st.get("sl")),
            "unrealized_R": round(unreal_R, 2), "lot": (pos.volume if pos else st.get("lot"))}
    return st, snap


def process(cfg, state):
    cfg_s = cfg["stf"]
    dry = bool(cfg_s.get("dry_run", True))
    ok, acc = account_status(cfg)
    status = {"updated": _now().isoformat(), "account_ok": ok, "account": acc,
              "dry_run": dry, "running": True, "symbols": {}}
    if not ok:
        print(f"[{_now():%H:%M:%S}] CUENTA NO VERIFICADA {acc.get('reasons')} — no se opera")
        return state, status
    for sym in cfg_s["symbols"]:
        st, snap = manage_symbol(sym, cfg_s, dry, state.get(sym, {}))
        state[sym] = st
        status["symbols"][sym] = snap
    return state, status


def main():
    cfg = load_config()
    if not cfg.get("stf", {}).get("enabled", False):
        print("stf.enabled=false"); return
    if not ensure(cfg):
        print("sin conexión demo"); return
    dry = cfg["stf"].get("dry_run", True)
    print(f"=== STF LIVE ({'DRY-RUN' if dry else 'ÓRDENES REALES'}) — "
          f"{cfg['stf']['symbols']}, magic {cfg['stf']['magic']} ===")
    once = "--once" in sys.argv
    state = _load(STATE_FILE, {})
    while True:
        if _load(COMMAND_FILE, {}).get("stop"):
            print(f"[{_now():%H:%M:%S}] STOP — saliendo")
            s = _load(STATUS_FILE, {}); s["running"] = False; _save(STATUS_FILE, s)
            _save(COMMAND_FILE, {}); break
        cfg = load_config()
        if not ensure(cfg):
            time.sleep(cfg["stf"].get("sleep", 60)); continue
        state, status = process(cfg, state)
        _save(STATE_FILE, state); _save(STATUS_FILE, status)
        if once:
            break
        time.sleep(cfg["stf"].get("sleep", 60))


if __name__ == "__main__":
    main()

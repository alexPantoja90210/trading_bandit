"""
Ejecutor DEMO-LIVE del breakout intradía de Zarattini (#2, validado en walk-forward).

Coloca órdenes reales en la demo siguiendo la estrategia:
- Banda de ruido alrededor del open del día: UB=Open*(1+N*Move(t)), LB=Open*(1-N*Move(t)),
  Move(t)=media (14 días previos) de |precio_slot/open-1| en ese slot intradía.
- Entrada en cada HH:00/HH:30 (barra M30 cerrada) al romper banda: UB→largo, LB→corto.
- Salida: trailing por VWAP de sesión (largo cierra si cae bajo VWAP; corto si sube sobre VWAP),
  flip si rompe la banda opuesta, y SIEMPRE plano al cierre de sesión (sin overnight → sin swap).

Seguridad reutilizada: candado de cuenta (ensure+account_status), kill switch
(data/intraday_command.json), sizing por riesgo con SL de desastre = banda opuesta,
magic propio (aislado de bandit y RSI2), estado a data/intraday_live_status.json.

Sesión cash EEUU = 13 barras M30 (servidor bróker = ET+7h, validado). Entra en slots
1..11, gestiona en todos, fuerza plano en el slot 12 (15:30 ET). Arranca en dry_run.
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
from intraday_cache import add_et, RTH_SLOTS

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE_FILE = os.path.join(DATA_DIR, "intraday_live_state.json")
STATUS_FILE = os.path.join(DATA_DIR, "intraday_live_status.json")
TRADES_CSV = os.path.join(DATA_DIR, "intraday_live_trades.csv")
COMMAND_FILE = os.path.join(DATA_DIR, "intraday_command.json")
LAST_SLOT = len(RTH_SLOTS) - 1        # 12 = 15:30 ET (última barra, flat)


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    # robusto ante la carrera de archivos de Windows: os.replace falla con
    # PermissionError si otro proceso (dashboard/lectura) tiene el JSON abierto.
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
        with open(path, "w", encoding="utf-8") as f:   # fallback no-atómico
            json.dump(obj, f, indent=2, default=str)
    except Exception:
        pass


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def log_trade(row):
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write("ts,event,symbol,side,price,lot,ret_pct,reason,ticket,dry\n")
        f.write("{ts},{event},{symbol},{side},{price},{lot},{ret_pct},{reason},{ticket},{dry}\n".format(**row))


# ---------------------------------------------------------------------------
# señales del breakout sobre la última barra M30 CERRADA
# ---------------------------------------------------------------------------
def compute_signals(sym, N, lookback):
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M30, 0, 4000)
    if r is None or len(r) < 500:
        return {"in_session": False, "reason": "sin_datos"}
    df = pd.DataFrame(r)
    forming = df["time"].iloc[-1]                 # barra en formación
    d = add_et(df)
    d = d[d["hm"].isin(RTH_SLOTS)].copy()
    d["slot"] = d["hm"].map({s: i for i, s in enumerate(RTH_SLOTS)})
    if len(d) == 0:
        return {"in_session": False, "reason": "fuera_rth"}

    today = d["date"].max()
    today_rows = d[d["date"] == today]
    closed = today_rows[today_rows["time"] < forming]     # solo barras cerradas
    if len(closed) == 0 or (today_rows["slot"] == 0).sum() == 0:
        return {"in_session": False, "reason": "sesion_no_iniciada", "date": str(today)}

    cur = closed.sort_values("slot").iloc[-1]
    slot = int(cur["slot"])
    today_open = float(today_rows[today_rows["slot"] == 0]["open"].iloc[0])
    close_now = float(cur["close"])
    cum = close_now / today_open - 1.0

    # Move(t): media de |cum| en este slot sobre los `lookback` días previos
    piv_c = d.pivot_table(index="date", columns="slot", values="close")
    piv_o = d.pivot_table(index="date", columns="slot", values="open")
    oday = piv_o[0]
    cum_col = (piv_c[slot] / oday - 1.0).dropna()
    prev = cum_col[cum_col.index < today]
    if len(prev) < lookback:
        return {"in_session": True, "actionable": False, "reason": "poca_historia",
                "slot": slot, "date": str(today), "cum": cum}
    move = float(np.abs(prev.tail(lookback)).mean())

    # VWAP de sesión hasta el slot actual
    upto = today_rows[today_rows["slot"] <= slot]
    typ = (upto["high"] + upto["low"] + upto["close"]) / 3.0
    vwap = float((typ * upto["tick_volume"]).sum() / max(upto["tick_volume"].sum(), 1e-9))

    ub = N * move
    return {
        "in_session": True, "actionable": True, "date": str(today),
        "slot": slot, "bar_time": int(cur["time"]), "close": close_now,
        "today_open": today_open, "cum": cum, "move": move, "ub": ub, "vwap": vwap,
        "is_last": slot >= LAST_SLOT,
        "long_break": cum > ub, "short_break": cum < -ub,
        "sl_long": today_open * (1 - ub), "sl_short": today_open * (1 + ub),
    }


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


def open_pos(symbol, side, sl_price, cfg_i, dry):
    """side: +1 largo, -1 corto. SL de desastre = banda opuesta. Sin TP (salida por VWAP)."""
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
    sl = round(sl_price, digits)
    sl_dist = abs(price - sl)
    if sl_dist < info.point * 10:          # SL demasiado cerca → piso mínimo
        sl_dist = info.point * 10
        sl = round(price - side * sl_dist, digits)
    lot = size_lot(info, cfg_i["risk_per_trade"], sl_dist)
    if dry:
        return {"price": round(price, digits), "lot": lot, "sl": sl, "ticket": None}
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL,
        "price": round(price, digits), "sl": sl, "deviation": 15,
        "magic": cfg_i["magic"], "comment": "intraday_zar",
        "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[{_now():%H:%M:%S}] {symbol}: apertura fallida retcode={getattr(res,'retcode',None)}")
        return None
    return {"price": res.price, "lot": lot, "sl": sl, "ticket": res.order}


def close_pos(pos, dry):
    info = mt5.symbol_info(pos.symbol)
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return None
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
    if dry:
        return round(price, info.digits)
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "position": pos.ticket, "price": round(price, info.digits), "deviation": 15,
        "magic": pos.magic, "comment": "intraday_exit",
        "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC,
    }
    res = mt5.order_send(req)
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"[{_now():%H:%M:%S}] {pos.symbol}: cierre fallido retcode={getattr(res,'retcode',None)}")
        return None
    return res.price


# ---------------------------------------------------------------------------
# lógica principal por símbolo
# ---------------------------------------------------------------------------
def manage_symbol(sym, N, cfg_i, dry, st):
    """Devuelve (nuevo_estado, snapshot). Muta órdenes según la estrategia."""
    magic = cfg_i["magic"]
    sig = compute_signals(sym, N, cfg_i["lookback"])
    pos = get_position(sym, magic)
    tag = "DRY" if dry else "LIVE"

    # --- stop-out del bróker entre iteraciones (SL de desastre): registrar el cierre ---
    if not dry and st.get("in_pos") and pos is None:
        entry = st.get("entry"); slp = st.get("sl"); s = st.get("side", 1)
        ret = (s * (slp - entry) / entry * 100) if (entry and slp) else 0.0
        print(f"[{_now():%H:%M:%S}] {tag} STOP {sym} @ {slp:,.2f} ret={ret:+.2f}% (SL de desastre)")
        log_trade({"ts": _now().isoformat(), "event": "STOP", "symbol": sym,
                   "side": "long" if s == 1 else "short", "price": slp, "lot": st.get("lot", ""),
                   "ret_pct": round(ret, 2), "reason": "broker_sl",
                   "ticket": st.get("ticket", ""), "dry": dry})
        st = {}

    # --- fuera de sesión: forzar plano (seguridad EOD / overnight) ---
    if not sig.get("in_session") or not sig.get("actionable", False):
        if pos is not None:
            price = close_pos(pos, dry)
            if price:
                ret = (price - pos.price_open) / pos.price_open * 100 * (1 if pos.type == 0 else -1)
                print(f"[{_now():%H:%M:%S}] {tag} FLAT {sym} @ {price:,.2f} ret={ret:+.2f}% (fuera de sesión)")
                log_trade({"ts": _now().isoformat(), "event": "EOD_FLAT", "symbol": sym,
                           "side": "long" if pos.type == 0 else "short", "price": price,
                           "lot": pos.volume, "ret_pct": round(ret, 2), "reason": "out_of_session",
                           "ticket": pos.ticket, "dry": dry})
        return {}, {"in_session": False, "reason": sig.get("reason", ""),
                    "slot": sig.get("slot"), "date": sig.get("date")}

    new_bar = sig["bar_time"] != st.get("last_bar")
    held = (pos is not None) if not dry else st.get("in_pos", False)
    side = None
    if held:
        side = (1 if pos.type == 0 else -1) if pos else st.get("side")

    if new_bar:
        if sig["is_last"]:
            # última barra: plano, sin nuevas entradas
            if held:
                _do_exit(sym, pos, side, sig, dry, st, "eod_close", tag)
            st = {"in_pos": False}
        elif not held:
            # ¿entrada por ruptura?
            if sig["long_break"] or sig["short_break"]:
                s = 1 if sig["long_break"] else -1
                slp = sig["sl_long"] if s == 1 else sig["sl_short"]
                o = open_pos(sym, s, slp, cfg_i, dry)
                if o:
                    st = {"in_pos": True, "side": s, "entry": o["price"], "lot": o["lot"],
                          "sl": o["sl"], "ticket": o["ticket"], "entry_bar": sig["bar_time"]}
                    print(f"[{_now():%H:%M:%S}] {tag} ENTRY {sym} {'LARGO' if s==1 else 'CORTO'} "
                          f"@ {o['price']:,.2f} lot={o['lot']} SL={o['sl']:,.2f} "
                          f"(cum={sig['cum']*100:+.2f}% band=±{sig['ub']*100:.2f}%)")
                    log_trade({"ts": _now().isoformat(), "event": "ENTRY", "symbol": sym,
                               "side": "long" if s == 1 else "short", "price": o["price"],
                               "lot": o["lot"], "ret_pct": "", "reason": "band_break",
                               "ticket": o["ticket"] or "", "dry": dry})
        else:
            # gestionar posición: VWAP trailing + flip por banda opuesta
            vwap_exit = (side == 1 and sig["close"] < sig["vwap"]) or \
                        (side == -1 and sig["close"] > sig["vwap"])
            flip = (side == 1 and sig["short_break"]) or (side == -1 and sig["long_break"])
            if vwap_exit or flip:
                _do_exit(sym, pos, side, sig, dry, st, "flip" if flip else "vwap", tag)
                st = {"in_pos": False}
                if flip:                       # abrir opuesto inmediatamente
                    s = -side
                    slp = sig["sl_long"] if s == 1 else sig["sl_short"]
                    o = open_pos(sym, s, slp, cfg_i, dry)
                    if o:
                        st = {"in_pos": True, "side": s, "entry": o["price"], "lot": o["lot"],
                              "sl": o["sl"], "ticket": o["ticket"], "entry_bar": sig["bar_time"]}
                        print(f"[{_now():%H:%M:%S}] {tag} FLIP→{'LARGO' if s==1 else 'CORTO'} "
                              f"{sym} @ {o['price']:,.2f} lot={o['lot']}")
                        log_trade({"ts": _now().isoformat(), "event": "FLIP", "symbol": sym,
                                   "side": "long" if s == 1 else "short", "price": o["price"],
                                   "lot": o["lot"], "ret_pct": "", "reason": "opposite_break",
                                   "ticket": o["ticket"] or "", "dry": dry})
        st["last_bar"] = sig["bar_time"]

    # snapshot
    pos = get_position(sym, magic)
    in_p = (pos is not None) if not dry else st.get("in_pos", False)
    entry = pos.price_open if pos else st.get("entry")
    unreal = 0.0
    if in_p and entry:
        sd = (1 if (pos.type == 0 if pos else st.get("side") == 1) else -1)
        cur = pos.price_current if pos else sig["close"]   # precio ACTUAL del bróker
        unreal = (cur - entry) / entry * 100 * sd
    snap = {"in_session": True, "slot": sig["slot"], "date": sig["date"],
            "close": round(sig["close"], 2), "cum_pct": round(sig["cum"] * 100, 2),
            "band_pct": round(sig["ub"] * 100, 2), "vwap": round(sig["vwap"], 2),
            "in_position": in_p, "side": ("long" if (st.get("side") == 1 or (pos and pos.type == 0)) else
                                          ("short" if in_p else None)),
            "unrealized_pct": round(unreal, 2),
            "lot": (pos.volume if pos else st.get("lot")), "N": N}
    return st, snap


def _do_exit(sym, pos, side, sig, dry, st, reason, tag):
    if not dry and pos is not None:
        price = close_pos(pos, dry)
        entry = pos.price_open
    else:
        price = sig["close"]; entry = st.get("entry", sig["close"])
    if price is None:
        return
    ret = (price - entry) / entry * 100 * side
    print(f"[{_now():%H:%M:%S}] {tag} EXIT {sym} @ {price:,.2f} ret={ret:+.2f}% ({reason})")
    log_trade({"ts": _now().isoformat(), "event": "EXIT", "symbol": sym,
               "side": "long" if side == 1 else "short", "price": round(price, 2),
               "lot": (pos.volume if pos else st.get("lot", "")), "ret_pct": round(ret, 2),
               "reason": reason, "ticket": (pos.ticket if pos else st.get("ticket", "")), "dry": dry})


# ---------------------------------------------------------------------------
# loop
# ---------------------------------------------------------------------------
def process(cfg, state):
    cfg_i = cfg["intraday"]
    dry = bool(cfg_i.get("dry_run", True))
    ok, acc = account_status(cfg)
    status = {"updated": _now().isoformat(), "account_ok": ok, "account": acc,
              "dry_run": dry, "running": True, "symbols": {}}
    if not ok:
        print(f"[{_now():%H:%M:%S}] CUENTA NO VERIFICADA {acc.get('reasons')} — no se opera")
        return state, status
    for sym, N in cfg_i["symbols"].items():
        st, snap = manage_symbol(sym, float(N), cfg_i, dry, state.get(sym, {}))
        state[sym] = st
        status["symbols"][sym] = snap
    return state, status


def main():
    cfg = load_config()
    if not cfg.get("intraday", {}).get("enabled", False):
        print("intraday.enabled=false"); return
    if not ensure(cfg):
        print("sin conexión demo"); return
    dry = cfg["intraday"].get("dry_run", True)
    print(f"=== INTRADAY LIVE Zarattini ({'DRY-RUN' if dry else 'ÓRDENES REALES'}) — "
          f"{list(cfg['intraday']['symbols'])}, magic {cfg['intraday']['magic']} ===")
    once = "--once" in sys.argv
    state = _load(STATE_FILE, {})
    while True:
        cmd = _load(COMMAND_FILE, {})
        if cmd.get("stop"):
            print(f"[{_now():%H:%M:%S}] STOP — saliendo")
            s = _load(STATUS_FILE, {}); s["running"] = False; _save(STATUS_FILE, s)
            _save(COMMAND_FILE, {}); break
        cfg = load_config()
        if not ensure(cfg):
            time.sleep(cfg["intraday"].get("sleep", 30)); continue
        state, status = process(cfg, state)
        _save(STATE_FILE, state); _save(STATUS_FILE, status)
        if once:
            break
        time.sleep(cfg["intraday"].get("sleep", 30))


if __name__ == "__main__":
    main()

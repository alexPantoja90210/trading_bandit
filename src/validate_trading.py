"""
validate_trading.py — valida el pipeline de ejecucion en MT5 tras la recuperacion:
ABRIR -> MODIFICAR (SL/TP) -> CERRAR, para COMPRA y VENTA. Replica el patron exacto de los
ejecutores (order_send, TRADE_ACTION_DEAL/SLTP, filling IOC). Demo, lote minimo, magic 999999
(no toca a los bots). Verifica cuenta DEMO antes de nada y limpia cualquier posicion de prueba.
"""
import sys
import time

import MetaTrader5 as mt5
from mt5_connect import ensure

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MAGIC = 999999
CANDIDATES = ["EURUSD", "XAUUSD", "GBPUSD", "US500"]
OK = mt5.TRADE_RETCODE_DONE


def pick_symbol():
    for s in CANDIDATES:
        mt5.symbol_select(s, True)
        info = mt5.symbol_info(s)
        tick = mt5.symbol_info_tick(s)
        if info and tick and info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL and tick.ask > 0 and tick.bid > 0:
            return s, info
    return None, None


def openpos(sym, info, is_buy):
    tick = mt5.symbol_info_tick(sym)
    price = tick.ask if is_buy else tick.bid
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": info.volume_min,
           "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
           "price": price, "deviation": 20, "magic": MAGIC, "comment": "validate",
           "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC}
    return mt5.order_send(req)


def modify(sym, info, pos, is_buy):
    stops = max(info.trade_stops_level, 10) * info.point
    dist = max(stops * 3, 50 * info.point)
    e = pos.price_open
    sl = round(e - dist, info.digits) if is_buy else round(e + dist, info.digits)
    tp = round(e + dist, info.digits) if is_buy else round(e - dist, info.digits)
    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": sym, "position": pos.ticket, "sl": sl, "tp": tp}
    return mt5.order_send(req), sl, tp


def closepos(sym, info, pos, is_buy):
    tick = mt5.symbol_info_tick(sym)
    price = tick.bid if is_buy else tick.ask
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": pos.volume,
           "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
           "position": pos.ticket, "price": price, "deviation": 20, "magic": MAGIC,
           "comment": "validate_close", "type_filling": mt5.ORDER_FILLING_IOC, "type_time": mt5.ORDER_TIME_GTC}
    return mt5.order_send(req)


def get_pos(ticket):
    for p in mt5.positions_get() or []:
        if p.ticket == ticket:
            return p
    return None


def run_side(sym, info, is_buy):
    side = "COMPRA (BUY)" if is_buy else "VENTA (SELL)"
    print(f"\n### {side} en {sym} ({info.volume_min} lote)")
    r = openpos(sym, info, is_buy)
    ok = r and r.retcode == OK
    print(f"  1) ABRIR    -> retcode {r.retcode if r else 'None'} "
          f"{'OK' if ok else 'FALLO: '+ (r.comment if r else '')}")
    if not ok:
        return False
    ticket = r.order if not r.deal else None
    # localizar la posicion por magic
    pos = None
    for p in mt5.positions_get(symbol=sym) or []:
        if p.magic == MAGIC:
            pos = p; break
    if not pos:
        print("     no se encontro la posicion abierta"); return False
    print(f"     posicion #{pos.ticket} @ {pos.price_open}")
    time.sleep(0.5)
    rm, sl, tp = modify(sym, info, pos, is_buy)
    okm = rm and rm.retcode == OK
    print(f"  2) MODIFICAR-> retcode {rm.retcode if rm else 'None'} "
          f"{'OK (SL '+str(sl)+' / TP '+str(tp)+')' if okm else 'FALLO: '+(rm.comment if rm else '')}")
    time.sleep(0.5)
    pos = get_pos(pos.ticket) or pos
    rc = closepos(sym, info, pos, is_buy)
    okc = rc and rc.retcode == OK
    print(f"  3) CERRAR   -> retcode {rc.retcode if rc else 'None'} "
          f"{'OK' if okc else 'FALLO: '+(rc.comment if rc else '')}")
    return ok and okm and okc


def cleanup():
    for p in mt5.positions_get() or []:
        if p.magic == MAGIC:
            info = mt5.symbol_info(p.symbol)
            closepos(p.symbol, info, p, p.type == 0)


def main():
    if not ensure():
        print("No se pudo conectar / candado de cuenta rechazo"); return
    acc = mt5.account_info()
    demo = acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    print(f"Cuenta {acc.login} ({acc.server}) — {'DEMO OK' if demo else 'NO ES DEMO'}  "
          f"trade_allowed={mt5.terminal_info().trade_allowed}")
    if not demo:
        print("ABORTA: no es demo, no ejecuto pruebas."); return
    if not mt5.terminal_info().trade_allowed:
        print("ABORTA: AutoTrading DESACTIVADO en el terminal (boton AlgoTrading)."); return
    sym, info = pick_symbol()
    if not sym:
        print("ABORTA: ningun simbolo con mercado abierto ahora."); return
    print(f"Simbolo de prueba: {sym}  (spread {info.spread}, stops_level {info.trade_stops_level})")
    try:
        b = run_side(sym, info, True)
        c = run_side(sym, info, False)
    finally:
        cleanup()
    print("\n=== VEREDICTO ===")
    print(f"  Compra (abrir/modificar/cerrar): {'✔ OK' if b else '✗ FALLO'}")
    print(f"  Venta  (abrir/modificar/cerrar): {'✔ OK' if c else '✗ FALLO'}")
    rem = [p for p in (mt5.positions_get() or []) if p.magic == MAGIC]
    print(f"  Posiciones de prueba restantes: {len(rem)} (debe ser 0)")
    print("  -> LISTO PARA OPERAR" if (b and c and not rem) else "  -> revisar fallos arriba")


if __name__ == "__main__":
    main()

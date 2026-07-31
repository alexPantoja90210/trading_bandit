import MetaTrader5 as mt5

from paths import load_config
from mt5_connect import ensure, account_status


def count_open_positions(symbol, magic):
    """Nº de posiciones abiertas del bot (por magic number) en el símbolo."""
    if not ensure():
        return 0
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return 0
    return sum(1 for p in positions if p.magic == magic)


def open_position_sides(symbol, magic):
    """Lista de tipos de posiciones abiertas del bot (0=BUY, 1=SELL)."""
    if not ensure():
        return []
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return []
    return [p.type for p in positions if p.magic == magic]


def execute_trade(symbol, order_type, atr_price=None):
    """Envía una orden de mercado con SL/TP válidos.

    atr_price: distancia base (en precio) para los stops, típicamente el ATR
    real del activo. Si es None o inválido se usa la distancia mínima del bróker.
    Los stops se calculan como max(ATR*mult, mínimo del bróker) y se redondean a
    los dígitos del símbolo → evita retcode 10016 (INVALID_STOPS).
    """
    cfg = load_config()
    slippage = cfg["trading"]["slippage"]
    sl_mult = cfg["trading"].get("sl_atr_mult", 1.5)
    tp_mult = cfg["trading"].get("tp_atr_mult", 2.0)
    risk_pct = cfg["trading"].get("risk_per_trade", 0.0)  # 0 = usar lot_size fijo

    # Conectar al terminal DEMO fijado
    if not ensure(cfg):
        return {"retcode": "mt5_init_failed"}

    # ==========================================================
    # CANDADO DE SEGURIDAD: no operar si la cuenta no es la demo esperada.
    # Con varios terminales (demo/real/fondeo) la conexión puede derivar.
    # ==========================================================
    ok, acc = account_status(cfg)
    if not ok:
        return {"retcode": "wrong_account", "account": acc}

    info = mt5.symbol_info(symbol)
    if info is None:
        return {"retcode": "symbol_not_found"}

    if info.trade_mode != mt5.SYMBOL_TRADE_MODE_FULL:
        return {"retcode": "symbol_not_tradable"}

    # Asegurar que el símbolo esté visible en Market Watch
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"retcode": "no_tick_data"}

    point = info.point
    digits = info.digits

    # Distancia mínima exigida por el bróker (con un buffer de seguridad).
    # Si trade_stops_level es 0, usamos un piso basado en el spread actual.
    spread_price = (tick.ask - tick.bid)
    min_dist = max(info.trade_stops_level * point, spread_price * 2, 10 * point)

    # Distancia base: ATR real si se pasó; si no, la mínima del bróker.
    base = atr_price if (atr_price is not None and atr_price > 0) else min_dist

    sl_dist = max(base * sl_mult, min_dist)
    tp_dist = max(base * tp_mult, min_dist)

    # ============================================================
    # DIMENSIONAMIENTO POR RIESGO: lote tal que si salta el SL, la
    # pérdida ≈ risk_pct del balance. Si risk_pct <= 0 usa lot_size fijo.
    # ============================================================
    lot = cfg.get("lot_size", 0.01)
    if risk_pct and risk_pct > 0:
        acc = mt5.account_info()
        tick_size = info.trade_tick_size or point
        tick_value = info.trade_tick_value
        if acc is not None and tick_size > 0 and tick_value > 0 and sl_dist > 0:
            risk_amount = acc.balance * risk_pct
            loss_per_lot = (sl_dist / tick_size) * tick_value  # pérdida al SL por 1.0 lote
            if loss_per_lot > 0:
                lot = risk_amount / loss_per_lot

        # Ajustar al step de volumen y limitar a [min, max]
        step = info.volume_step or 0.01
        lot = round(lot / step) * step
        lot = max(info.volume_min, min(info.volume_max, lot))
        lot = round(lot, 2)

    # Precio y stops según tipo de orden, redondeados a los dígitos del símbolo
    if order_type == mt5.ORDER_TYPE_BUY:
        price = tick.ask
        sl = round(price - sl_dist, digits)
        tp = round(price + tp_dist, digits)
    elif order_type == mt5.ORDER_TYPE_SELL:
        price = tick.bid
        sl = round(price + sl_dist, digits)
        tp = round(price - tp_dist, digits)
    else:
        return {"retcode": "invalid_order_type"}

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": round(price, digits),
        "sl": sl,
        "tp": tp,
        "deviation": slippage,
        "magic": cfg["trading"]["magic_number"],
        "comment": "bandit_trade",
        "type_filling": mt5.ORDER_FILLING_IOC,
        "type_time": mt5.ORDER_TIME_GTC
    }

    result = mt5.order_send(request)
    return result

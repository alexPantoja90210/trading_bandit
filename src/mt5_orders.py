import MetaTrader5 as mt5
from datetime import datetime, timedelta
from mt5_connect import ensure, strategy_label


def get_order_history(days=7, limit=50):
    """Historial de operaciones (deals) de los últimos `days` días.

    Usa history_deals_get (no history_orders_get): los deals son las
    transacciones ejecutadas y llevan el `profit` de los trades cerrados.
    Se usa un rango de fechas amplio con buffer para cubrir la diferencia de
    zona horaria entre el servidor del bróker y la hora local.
    """
    if not ensure():
        return []

    # Rango con buffer de ±1 día para absorber el desfase de zona horaria del bróker
    date_to = datetime.now() + timedelta(days=1)
    date_from = date_to - timedelta(days=days + 2)

    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        return []

    result = []
    for d in deals:
        # Ignorar movimientos que no son de mercado (balance, crédito, etc.)
        if d.symbol == "":
            continue

        if d.type == mt5.DEAL_TYPE_BUY:
            dtype = "BUY"
        elif d.type == mt5.DEAL_TYPE_SELL:
            dtype = "SELL"
        else:
            dtype = str(d.type)

        result.append({
            "ticket": d.ticket,
            "symbol": d.symbol,
            "type": dtype,
            "volume": d.volume,
            "price": d.price,
            "profit": d.profit,
            "strategy": strategy_label(d.magic, d.comment),
            "time": datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
        })

    # Más recientes primero, limitado a `limit`
    result.reverse()
    return result[:limit]

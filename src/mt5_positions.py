import MetaTrader5 as mt5
from mt5_connect import ensure, strategy_label

def get_positions():
    if not ensure():
        return []

    positions = mt5.positions_get()
    if positions is None:
        return []

    result = []
    for p in positions:
        result.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "profit": p.profit,
            "strategy": strategy_label(p.magic, p.comment),
            "time": p.time
        })

    return result

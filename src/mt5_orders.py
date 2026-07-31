import MetaTrader5 as mt5
from datetime import datetime, timedelta
from collections import defaultdict
from mt5_connect import ensure, strategy_label


def get_order_history(days=7, limit=50):
    """Historial de OPERACIONES (posiciones cerradas), igual que la vista de Posiciones de MT5.

    MT5 registra cada operación como 2 deals: apertura (entry=IN, profit 0) y cierre
    (entry=OUT, con el P&L realizado). Aquí se AGRUPAN por `position_id` para mostrar una
    sola fila por operación con el profit NETO (incluye swap y comisión), el precio de
    apertura y de cierre — como la pestaña 'Posiciones' del historial de MT5.
    """
    if not ensure():
        return []

    # Rango con buffer de ±1 día para absorber el desfase de zona horaria del bróker
    date_to = datetime.now() + timedelta(days=1)
    date_from = date_to - timedelta(days=days + 2)

    deals = mt5.history_deals_get(date_from, date_to)
    if deals is None:
        return []

    # Agrupar deals por posición
    groups = defaultdict(list)
    for d in deals:
        if d.symbol == "":            # ignora balance/crédito (no son operaciones)
            continue
        groups[d.position_id].append(d)

    result = []
    for pid, ds in groups.items():
        ds.sort(key=lambda x: x.time)
        ins = [d for d in ds if d.entry == mt5.DEAL_ENTRY_IN]
        outs = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not outs:                  # posición aún ABIERTA -> va al panel de posiciones, no al historial
            continue
        first_in = ins[0] if ins else ds[0]
        last_out = outs[-1]
        # dirección de la POSICIÓN = tipo del deal de apertura (BUY=largo, SELL=corto), como MT5
        direction = "BUY" if first_in.type == mt5.DEAL_TYPE_BUY else "SELL"
        vol = sum(d.volume for d in ins) if ins else last_out.volume
        net = sum(d.profit + d.swap + d.commission for d in ds)   # P&L neto de la operación
        result.append({
            "ticket": pid,
            "symbol": last_out.symbol,
            "strategy": strategy_label(first_in.magic, first_in.comment),
            "type": direction,
            "volume": round(vol, 2),
            "open_price": first_in.price if ins else None,
            "close_price": last_out.price,
            "profit": round(net, 2),
            "time": datetime.fromtimestamp(last_out.time).strftime("%Y-%m-%d %H:%M:%S"),
        })

    # Más recientes primero (por hora de cierre), limitado a `limit`
    result.sort(key=lambda r: r["time"], reverse=True)
    return result[:limit]

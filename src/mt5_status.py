import MetaTrader5 as mt5
from mt5_connect import ensure

def get_mt5_status():
    if not ensure():
        return {
            "connected": False,
            "error": "MT5 no pudo inicializarse"
        }

    info = mt5.account_info()
    if info is None:
        return {
            "connected": False,
            "error": "No hay cuenta activa en MT5"
        }

    return {
        "connected": True,
        "login": info.login,
        "balance": info.balance,
        "equity": info.equity,
        "margin": info.margin,
        "free_margin": info.margin_free,
        "server": info.server,
        "account_type": "Demo" if "demo" in info.server.lower() else "Real"
    }

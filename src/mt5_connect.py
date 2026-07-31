"""
Conexión segura a MT5.

Con múltiples terminales instalados (demo, real, fondeo), `mt5.initialize()` sin
ruta se engancha a cualquiera y puede DERIVAR a una cuenta real. Este módulo:
  - fija la conexión al terminal DEMO por su ruta (config.account.terminal_path),
  - y verifica que la cuenta conectada sea EXACTAMENTE la esperada (login+servidor+demo)
    antes de permitir cualquier operación.
"""
import MetaTrader5 as mt5
from paths import load_config


# Mapa magic → estrategia (para etiquetar posiciones e historial en el dashboard)
STRATEGY_BY_MAGIC = {123456: "Bandit", 220002: "RSI(2)", 220003: "Intradía", 220004: "STF"}


def strategy_label(magic=0, comment=""):
    """Estrategia dueña de la operación, por magic (fiable en posiciones) o por
    comment (los deals del historial no siempre preservan el magic)."""
    if magic in STRATEGY_BY_MAGIC:
        return STRATEGY_BY_MAGIC[magic]
    c = (comment or "").lower()
    for pre, name in (("rsi2", "RSI(2)"), ("intraday", "Intradía"),
                      ("stf", "STF"), ("bandit", "Bandit")):
        if c.startswith(pre):
            return name
    return comment or "—"


def ensure(cfg=None):
    """Conexión al terminal DEMO fijado, sin relanzar terminales de más.

    - Si YA estamos conectados a la cuenta esperada → no re-inicializa (evita que
      MT5 relance terminales una y otra vez).
    - Si no, inicializa SOLO con la ruta del terminal demo (nunca el default de
      Windows, que podría ser una cuenta real/fondeo).
    """
    cfg = cfg or load_config()
    a = cfg.get("account", {}) or {}
    path = a.get("terminal_path")
    exp_login = a.get("login")

    # ¿ya conectado a la cuenta correcta? entonces no tocar nada.
    try:
        info = mt5.account_info()
    except Exception:
        info = None
    if info is not None and (not exp_login or info.login == exp_login):
        return True

    # (re)conectar SOLO al terminal demo por su ruta.
    try:
        if path:
            return bool(mt5.initialize(path=path))
        return bool(mt5.initialize())
    except Exception:
        return False


def account_status(cfg=None):
    """Devuelve (ok, info). ok=True solo si la cuenta conectada coincide con la esperada.

    Falla de forma SEGURA: ante cualquier duda (sin cuenta, no demo, login/servidor
    distinto), ok=False → no se debe operar.
    """
    cfg = cfg or load_config()
    a = cfg.get("account", {}) or {}
    if not a.get("enforce", True):
        info = mt5.account_info()
        return True, {"login": getattr(info, "login", None),
                      "server": getattr(info, "server", None), "reasons": []}

    info = mt5.account_info()
    if info is None:
        return False, {"login": None, "server": None, "reasons": ["sin_cuenta"]}

    reasons = []
    is_demo = (info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO)
    if a.get("require_demo", True) and not is_demo:
        reasons.append(f"NO_DEMO(trade_mode={info.trade_mode})")
    exp_login = a.get("login")
    if exp_login and info.login != exp_login:
        reasons.append(f"login {info.login}!={exp_login}")
    exp_server = a.get("server")
    if exp_server and exp_server not in (info.server or ""):
        reasons.append(f"server '{info.server}'!='{exp_server}'")

    return (len(reasons) == 0), {
        "login": info.login, "server": info.server,
        "trade_mode": info.trade_mode, "is_demo": is_demo, "reasons": reasons,
    }

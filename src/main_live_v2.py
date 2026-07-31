import os
import sys
import time
import subprocess
from collections import deque
from datetime import date, datetime, timedelta, timezone

import numpy as np
import MetaTrader5 as mt5

from paths import load_config, BASE_DIR
from data_loader_mt5 import load_ohlc
from feature_engineering import build_features
from reward_engine import compute_indicators
from bandit import ContextualBanditTS
from executor_mt5 import execute_trade, count_open_positions, open_position_sides
from mt5_connect import ensure, account_status
from logger import log_event
from recorder import (record_equity, record_reward, record_learning_row,
                      save_bandit_state, save_status, load_status, load_command,
                      save_pending, load_pending)

from regime_engine import compute_regime
from context_builder import build_context
from policy import should_trade


cfg = load_config()

symbol = cfg["symbol"]
features = cfg["features"]
arms = cfg["arms"]
sleep_time = cfg["sleep_time"]

bandit = ContextualBanditTS(
    n_features=len(features) + 2 + 4 + 10 + 4,  # base + regime + family + p0..p9 + knn/bars
    n_arms=len(arms)
)

ARM_NAMES = ["trend", "mean", "flat", "momentum", "volatility"]

# Límites de riesgo (guardarraíles)
MAGIC = cfg["trading"]["magic_number"]
MAX_OPEN = cfg["trading"].get("max_open_positions", 1)
MAX_DAILY_TRADES = cfg.get("max_daily_trades", 15)
MAX_DAILY_LOSS = cfg.get("max_daily_loss", -100.0)  # negativo; se aplica solo si < 0
EXECUTE_TRADES = cfg["trading"].get("execute", True)  # False = solo-aprende (no coloca órdenes)

# Horizonte de la recompensa futura (en barras)
H = int(cfg.get("reward_horizon", 20))

# Sesiones de trading (horario alineado a México vía utc_offset)
SESSIONS = cfg.get("sessions", {})
UTC_OFFSET = SESSIONS.get("utc_offset", -6)


def now_local(offset=UTC_OFFSET):
    """Hora local del usuario (México por defecto, UTC-6). Robusto al TZ de la máquina."""
    if offset is None:
        return datetime.now()
    # UTC now (moderno, sin deprecación) → naive → aplicar offset
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=offset)


def daily_realized_pnl(magic, now_dt, offset):
    """P&L realizado (profit+swap+comisión) del día para el `magic` dado.

    Se deriva del HISTORIAL del bróker, no de un contador en memoria → resetea
    solo al cambiar de día y sobrevive paros/reinicios. El inicio del día es la
    medianoche local (mismo criterio que el contador de operaciones).
    """
    try:
        day0_local = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        # epoch UTC del inicio del día local (UTC = local - offset)
        day0_epoch = (day0_local - timedelta(hours=offset)).replace(
            tzinfo=timezone.utc).timestamp()
        # ventana amplia para el historial; el corte fino es por d.time (epoch)
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        deals = mt5.history_deals_get(utc_now - timedelta(days=2), utc_now + timedelta(days=1))
        if not deals:
            return 0.0
        total = 0.0
        for d in deals:
            if d.magic == magic and d.entry == mt5.DEAL_ENTRY_OUT and d.time >= day0_epoch:
                total += d.profit + d.swap + d.commission
        return round(total, 2)
    except Exception:
        return 0.0


def restart_self():
    """Relanza el bot re-leyendo el config (para aplicar cambios que se leen al arrancar)."""
    log_event("bot_restart", {})
    script = os.path.join(BASE_DIR, "main_live_v2.py")
    flags = 0
    if os.name == "nt":
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP → sin ventana, independiente
        flags = 0x08000000 | 0x00000200
    try:
        subprocess.Popen(
            [sys.executable, script], cwd=BASE_DIR, creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as e:
        log_event("error", {"message": f"restart failed: {e}"})
        return
    sys.exit(0)


def _hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def in_session(now, sess):
    """(permitido, nombre_sesion). Si el filtro está off → siempre permitido."""
    if not sess or not sess.get("enabled", True):
        return True, "filtro_off"
    t = now.hour * 60 + now.minute
    for name in ("tokyo", "london", "newyork"):
        w = sess.get(name)
        if not w or not w.get("enabled", True):
            continue
        s, e = _hm(w["start"]), _hm(w["end"])
        inside = (s <= t < e) if s <= e else (t >= s or t < e)
        if inside:
            return True, name
    return False, None


# Estado — restaurar el contador diario si el status.json es de hoy
current_day = now_local().date()
_st = load_status()
trades_today = int(_st.get("trades_today", 0)) if _st.get("date") == current_day.isoformat() else 0
last_reset_ts = float(_st.get("last_reset_ts", 0) or 0)
last_restart_ts = float(_st.get("last_restart_ts", 0) or 0)
last_stop_ts = float(_st.get("last_stop_ts", 0) or 0)
pending = load_pending()   # cola persistida (sobrevive paros/reinicios)
last_decision_bar = None   # para encolar UNA decisión por barra nueva
last_trade_bar = None      # última barra en la que se ABRIÓ una operación

# Anti-cobertura y ritmo de operación
ALLOW_HEDGE = cfg["trading"].get("allow_hedge", False)
ONE_PER_BAR = cfg["trading"].get("one_trade_per_bar", True)

# Filtro de edge k-NN del régimen (como el useEdgeFlt del Pine)
USE_KNN_FILTER = cfg["trading"].get("use_knn_edge_filter", False)
KNN_EDGE_MIN = cfg["trading"].get("knn_edge_min", 0.15)


def implied_direction(arm, df, regime_row):
    """Dirección implícita del brazo: +1 compra, -1 venta, 0 sin operar.

    Debe coincidir con la dirección real de ejecución para que la recompensa
    diferida refleje el P&L del trade que se abriría.
    """
    family = str(regime_row["family"])
    if arm in (0, 3):        # trend / momentum → a favor de la tendencia
        return -1 if family == "TREND_DOWN" else 1
    if arm == 1:             # mean → reversión contra el desvío de la sma20
        return -1 if df["close"].iloc[-1] > df["sma20"].iloc[-1] else 1
    if arm == 4:             # volatility → dirección de la última barra
        return 1 if df["close"].iloc[-1] > df["close"].iloc[-2] else -1
    return 0                 # flat


def bars_after(df, bar_time):
    """Cuántas barras hay en df posteriores a bar_time."""
    return int((df["time"] > bar_time).sum())


def close_after_horizon(df, bar_time, horizon):
    """Close de la barra `horizon` posiciones después de la de bar_time (o None)."""
    matches = df.index[df["time"] == bar_time]
    if len(matches) == 0:
        return None
    pos = df.index.get_loc(matches[0])
    target = pos + horizon
    if target < len(df):
        return float(df["close"].iloc[target])
    return None


while True:
    try:
        # ---- gestión de día, comandos y sesión (arriba del todo) ----
        # config editable en vivo: se relee cada iteración (sesiones + límites)
        try:
            live_cfg = load_config()
            sess_cfg = live_cfg.get("sessions", SESSIONS)
            MAX_OPEN = live_cfg["trading"].get("max_open_positions", MAX_OPEN)
            MAX_DAILY_TRADES = live_cfg.get("max_daily_trades", MAX_DAILY_TRADES)
            MAX_DAILY_LOSS = live_cfg.get("max_daily_loss", MAX_DAILY_LOSS)
            EXECUTE_TRADES = live_cfg["trading"].get("execute", EXECUTE_TRADES)
            sleep_time = live_cfg.get("sleep_time", sleep_time)
            ALLOW_HEDGE = live_cfg["trading"].get("allow_hedge", ALLOW_HEDGE)
            ONE_PER_BAR = live_cfg["trading"].get("one_trade_per_bar", ONE_PER_BAR)
            USE_KNN_FILTER = live_cfg["trading"].get("use_knn_edge_filter", USE_KNN_FILTER)
            KNN_EDGE_MIN = live_cfg["trading"].get("knn_edge_min", KNN_EDGE_MIN)
        except Exception:
            sess_cfg = SESSIONS
        now = now_local(sess_cfg.get("utc_offset", UTC_OFFSET))

        # ---- CANDADO DE CUENTA: fijar terminal demo y verificar ----
        ensure()
        account_ok, account_info = account_status()
        if not account_ok:
            log_event("account_mismatch", account_info)  # crítico: cuenta equivocada

        # reset automático al cambiar de día
        if now.date() != current_day:
            current_day = now.date()
            trades_today = 0
            log_event("daily_reset", {"date": current_day.isoformat()})
        # comandos desde el dashboard (con timestamp)
        cmd = load_command()
        rts = float(cmd.get("reset_trades_ts", 0) or 0)
        if rts > last_reset_ts:
            last_reset_ts = rts
            trades_today = 0
            log_event("manual_reset", {"trades_today": 0})
        # PARO del bot (kill switch desde el dashboard)
        sbt = float(cmd.get("stop_bot_ts", 0) or 0)
        if sbt > last_stop_ts:
            last_stop_ts = sbt
            save_status({
                "date": current_day.isoformat(), "trades_today": trades_today,
                "max_daily_trades": MAX_DAILY_TRADES, "max_open_positions": MAX_OPEN,
                "last_reset_ts": last_reset_ts, "last_restart_ts": last_restart_ts,
                "last_stop_ts": last_stop_ts, "running": False,
            })
            log_event("bot_stopped", {})
            sys.exit(0)
        # reinicio del bot (para aplicar cambios de config que se leen al arrancar)
        rbt = float(cmd.get("restart_bot_ts", 0) or 0)
        if rbt > last_restart_ts:
            last_restart_ts = rbt
            save_status({
                "date": current_day.isoformat(), "trades_today": trades_today,
                "max_daily_trades": MAX_DAILY_TRADES, "max_open_positions": MAX_OPEN,
                "last_reset_ts": last_reset_ts, "last_restart_ts": last_restart_ts,
                "last_stop_ts": last_stop_ts,   # ← persistir para no re-procesar un paro viejo
            })
            restart_self()   # relanza y termina este proceso
        # ¿estamos dentro de una sesión habilitada?
        session_ok, session_name = in_session(now, sess_cfg)

        df = load_ohlc()
        # guardia: si MT5 devolvió datos vacíos, saltar esta iteración
        if df is None or len(df) == 0 or "time" not in df.columns:
            log_event("no_data", {"local_time": now.strftime("%H:%M:%S")})
            time.sleep(sleep_time)
            continue

        X = build_features(df, features)
        feats_row = X.iloc[-1]

        df = compute_indicators(df)
        regime_row = compute_regime(df)

        context = build_context(feats_row, regime_row)

        bar_time = df["time"].iloc[-1]

        # ============================================================
        # 1) MADURAR decisiones pendientes → actualizar el bandit con
        #    la recompensa FUTURA real (P&L en la dirección operada).
        # ============================================================
        while pending and bars_after(df, pending[0]["bar_time"]) >= H:
            d = pending.popleft()
            exit_close = close_after_horizon(df, d["bar_time"], H)
            if exit_close is None:
                continue  # la barra de entrada quedó fuera de rango → descartar

            reward = d["direction"] * (exit_close - d["entry_close"]) / d["entry_atr"]

            bandit.update(d["arm"], d["context"], reward)
            record_reward(d["arm"], d["arm_name"], reward)
            # (el dataset de entrenamiento lo genera learning_collector.py: multi-símbolo
            #  + recompensa contrafactual de los 5 brazos = información completa)
            save_bandit_state(bandit)
            log_event("reward_matured", {
                "arm": d["arm"], "arm_name": d["arm_name"],
                "direction": d["direction"], "reward": float(reward)
            })

        # ============================================================
        # 2) SELECCIÓN del brazo y su dirección implícita
        # ============================================================
        arm = bandit.select_arm(context)
        arm_name = ARM_NAMES[arm]
        direction = implied_direction(arm, df, regime_row)

        # ============================================================
        # 3) ENCOLAR una decisión por barra nueva (recompensa diferida).
        #    El bandit aprende SIEMPRE, aunque el régimen no permita operar.
        # ============================================================
        if bar_time != last_decision_bar:
            last_decision_bar = bar_time
            if direction != 0:
                pending.append({
                    "bar_time": bar_time,
                    "context": context,
                    "arm": arm,
                    "arm_name": arm_name,
                    "direction": direction,
                    "entry_close": float(df["close"].iloc[-1]),
                    "entry_atr": float(df["atr"].iloc[-1]),
                    # condiciones de régimen (interpretables) para el dataset de entrenamiento
                    "regime_id": int(regime_row["id"]) if regime_row["id"] == regime_row["id"] else -1,
                    "family": str(regime_row["family"]),
                    "knn_edge": round(float(regime_row["knn_edge"]), 3),
                    "confidence": round(float(regime_row["confidence"]), 3),
                })

        # persistir la cola (refleja maduración + encolado de esta iteración)
        save_pending(pending)

        # ---- equity + status para el dashboard (cada tick) ----
        acc = mt5.account_info()
        if acc is not None and acc.equity and acc.equity > 0:
            record_equity(acc.equity)
        # P&L realizado del día (para la guarda de pérdida máxima diaria)
        daily_pnl = daily_realized_pnl(MAGIC, now, sess_cfg.get("utc_offset", UTC_OFFSET))
        save_status({
            "date": current_day.isoformat(),
            "trades_today": trades_today,
            "max_daily_trades": MAX_DAILY_TRADES,
            "daily_pnl": daily_pnl,
            "max_daily_loss": MAX_DAILY_LOSS,
            "max_open_positions": MAX_OPEN,
            "open_positions": count_open_positions(symbol, MAGIC),
            "last_reset_ts": last_reset_ts,
            "last_restart_ts": last_restart_ts,
            "last_stop_ts": last_stop_ts,
            "running": True,
            "session_active": session_ok,
            "session": session_name,
            "local_time": now.strftime("%Y-%m-%d %H:%M"),
            "account_ok": account_ok,
            "account_login": account_info.get("login"),
            "account_server": account_info.get("server"),
            "account_reasons": account_info.get("reasons", []),
            # --- régimen de mercado (regime_master) ---
            "regime_id": int(regime_row["id"]) if regime_row["id"] == regime_row["id"] else -1,
            "regime_code": int(regime_row["code"]) if regime_row["code"] == regime_row["code"] else -1,
            "regime_name": str(regime_row["regime"]),
            "regime_family": str(regime_row["family"]),
            "regime_conf": round(float(regime_row["confidence"]), 3),
            "regime_bars": int(regime_row["bars_in_regime"]) if regime_row["bars_in_regime"] == regime_row["bars_in_regime"] else 0,
            "knn_edge": round(float(regime_row["knn_edge"]), 3),
        })

        # ============================================================
        # 4) EJECUCIÓN: sesión + política de régimen + guardarraíles
        # ============================================================
        if not EXECUTE_TRADES:
            # MODO SOLO-APRENDE: sigue seleccionando brazo, encolando y actualizando el
            # bandit con recompensas contrafactuales (arriba), pero NO coloca órdenes.
            log_event("learn_only", {
                "symbol": symbol, "arm": arm, "arm_name": arm_name, "direction": direction
            })
        elif not account_ok:
            log_event("no_trade_account", {
                "symbol": symbol, "login": account_info.get("login"),
                "server": account_info.get("server"), "reasons": account_info.get("reasons")
            })
        elif not session_ok:
            log_event("no_trade_session", {
                "symbol": symbol, "arm": arm, "arm_name": arm_name,
                "local_time": now.strftime("%H:%M")
            })
        elif not should_trade(regime_row, arm_name):
            log_event("no_trade_regime", {
                "symbol": symbol, "arm": arm, "arm_name": arm_name,
                "regime": str(regime_row["regime"]),
                "family": str(regime_row["family"]),
                "confidence": float(regime_row["confidence"])
            })
        else:
            n_open = count_open_positions(symbol, MAGIC)

            if n_open >= MAX_OPEN:
                log_event("no_trade_max_positions", {
                    "symbol": symbol, "arm": arm, "arm_name": arm_name,
                    "open_positions": n_open, "max_open": MAX_OPEN
                })
            elif trades_today >= MAX_DAILY_TRADES:
                log_event("no_trade_daily_cap", {
                    "symbol": symbol, "arm": arm, "arm_name": arm_name,
                    "trades_today": trades_today, "max_daily": MAX_DAILY_TRADES
                })
            elif MAX_DAILY_LOSS < 0 and daily_pnl <= MAX_DAILY_LOSS:
                log_event("no_trade_daily_loss", {
                    "symbol": symbol, "arm": arm, "arm_name": arm_name,
                    "daily_pnl": daily_pnl, "max_daily_loss": MAX_DAILY_LOSS
                })
            elif direction == 0:
                pass  # brazo flat → no se opera
            elif ONE_PER_BAR and last_trade_bar == bar_time:
                # ya se abrió una operación en esta barra → no disparar otra
                log_event("no_trade_same_bar", {
                    "symbol": symbol, "arm": arm, "arm_name": arm_name
                })
            elif USE_KNN_FILTER and not (
                (direction == 1 and float(regime_row["knn_edge"]) >= KNN_EDGE_MIN) or
                (direction == -1 and float(regime_row["knn_edge"]) <= -KNN_EDGE_MIN)):
                # filtro de edge k-NN: el histórico parecido debe respaldar la dirección
                log_event("no_trade_knn_edge", {
                    "symbol": symbol, "arm": arm, "arm_name": arm_name,
                    "direction": direction, "knn_edge": round(float(regime_row["knn_edge"]), 3),
                    "min": KNN_EDGE_MIN
                })
            else:
                order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL
                # anti-cobertura: no abrir en dirección opuesta a lo ya abierto
                opp_side = 1 - int(order_type)   # BUY(0)->1(SELL) ; SELL(1)->0(BUY)
                sides = open_position_sides(symbol, MAGIC)

                if (not ALLOW_HEDGE) and opp_side in sides:
                    log_event("no_trade_hedge", {
                        "symbol": symbol, "arm": arm, "arm_name": arm_name,
                        "direction": direction, "open_sides": sides
                    })
                    result = None
                else:
                    atr_price = float(df["atr"].iloc[-1])
                    result = execute_trade(symbol, order_type, atr_price=atr_price)

                if result is not None and hasattr(result, "retcode") and result.retcode == mt5.TRADE_RETCODE_DONE:
                    trades_today += 1
                    last_trade_bar = bar_time
                    log_event("trade_executed", {
                        "symbol": symbol, "arm": arm, "arm_name": arm_name,
                        "direction": direction,
                        "regime": str(regime_row["regime"]),
                        "family": str(regime_row["family"]),
                        "confidence": float(regime_row["confidence"]),
                        "retcode": int(result.retcode),
                        "trades_today": trades_today
                    })
                elif result is not None:
                    log_event("trade_failed", {
                        "symbol": symbol, "arm": arm, "arm_name": arm_name,
                        "direction": direction,
                        "regime": str(regime_row["regime"]),
                        "family": str(regime_row["family"]),
                        "confidence": float(regime_row["confidence"]),
                        "retcode": getattr(result, "retcode", "no_response")
                    })

        log_event("bandit_update", {
            "mu": np.array(bandit.mu).tolist(),
            "sigma": np.array(bandit.sigma).tolist(),
            "pending": len(pending)
        })

        time.sleep(sleep_time)

    except Exception as e:
        log_event("error", {"message": str(e)})
        time.sleep(5)

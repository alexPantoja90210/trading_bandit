import dash
from dash import dcc, html, Input, Output, State, dash_table
from dash.dash_table.Format import Format, Scheme
import plotly.graph_objs as go
import pandas as pd
import numpy as np
import json
import os
import sys
import time
import subprocess
from datetime import datetime

from paths import load_config, EQUITY_CSV, REWARDS_CSV, BANDIT_STATE, STATUS_FILE, CONFIG_FILE, BASE_DIR, DATA_DIR
from recorder import write_command
from metrics import compute_sharpe, compute_drawdown, compute_winrate, compute_expectancy
from mt5_status import get_mt5_status
from mt5_positions import get_positions
from mt5_orders import get_order_history


cfg = load_config()
SYMBOL = cfg["symbol"]


def load_equity():
    if not os.path.exists(EQUITY_CSV):
        return pd.DataFrame([])
    return pd.read_csv(EQUITY_CSV)


def load_rewards():
    if not os.path.exists(REWARDS_CSV):
        return pd.DataFrame([])
    return pd.read_csv(REWARDS_CSV)


def load_bandit_state():
    if not os.path.exists(BANDIT_STATE):
        return {}
    with open(BANDIT_STATE, encoding="utf-8") as f:
        return json.load(f)


def read_status():
    """Estado en vivo del bot (contadores) escrito por main_live_v2."""
    if not os.path.exists(STATUS_FILE):
        return {}
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ============================
# EDITOR DE CONFIG
# (etiqueta, id, ruta en el JSON, step, entero?)
# ============================
CONFIG_FIELDS = [
    ("Riesgo por operación (fracción, 0.005 = 0.5%)", "cfg-risk", ["trading", "risk_per_trade"], 0.001, False),
    ("Máx. posiciones abiertas", "cfg-maxopen", ["trading", "max_open_positions"], 1, True),
    ("Máx. operaciones / día", "cfg-maxdaily", ["max_daily_trades"], 1, True),
    ("Pérdida máx. diaria", "cfg-maxloss", ["max_daily_loss"], 10, False),
    ("SL × ATR", "cfg-slmult", ["trading", "sl_atr_mult"], 0.1, False),
    ("TP × ATR", "cfg-tpmult", ["trading", "tp_atr_mult"], 0.1, False),
    ("Horizonte de recompensa (barras)", "cfg-horizon", ["reward_horizon"], 1, True),
    ("Sleep entre iteraciones (s)", "cfg-sleep", ["sleep_time"], 1, True),
]


def _cfg_get(conf, path):
    v = conf
    for k in path:
        v = v[k]
    return v


# Régimen de mercado (mismos colores que el Pine RegimeMaster)
REGIME_COLORS = {0: "#00A05A", 1: "#E6A014", 2: "#4682C8", 3: "#78D23C", 4: "#00C86E",
                 5: "#BE3C3C", 6: "#DC2828", 7: "#FF5028", 8: "#966ED2", 9: "#969696"}
REGIME_PLAY = {0: "Pullback alcista", 1: "FUERA DEL MERCADO", 2: "Reversión en bandas",
               3: "Pullback profundo (riesgo)", 4: "Momentum / ruptura", 5: "Pullback bajista",
               6: "Momentum bajista", 7: "Pullback profundo (riesgo)", 8: "Reversión extrema",
               9: "Sin entradas nuevas"}


# --- Régimen H4 (contexto): se calcula en el dashboard, no lo escribe el robot ---
# El robot opera en M5 y clasifica en M5. Pero el regime_master fue validado en H4/D1
# (en M5 las ventanas ≈ horas → ruido). Aquí calculamos el régimen H4 como CONTEXTO,
# con caché de 5 min porque una barra H4 cambia como mucho cada 4 horas.
_H4_CACHE = {"ts": 0.0, "data": None}


def h4_regime(symbol):
    now = time.time()
    if _H4_CACHE["data"] is not None and (now - _H4_CACHE["ts"]) < 300:
        return _H4_CACHE["data"]
    try:
        import MetaTrader5 as mt5
        from mt5_connect import ensure
        from reward_engine import compute_indicators
        from regime_master import classify, Params
        ensure()
        mt5.symbol_select(symbol, True)
        r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H4, 0, 1200)
        if r is None or len(r) < 260:
            return None
        df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
        df = compute_indicators(df)
        last = classify(df, Params()).iloc[-1]
        def _num(x, d=0.0):
            try:
                return float(x) if x == x else d
            except Exception:
                return d
        data = {
            "id": int(_num(last["id"], -1)), "code": int(_num(last["code"], -1)),
            "name": str(last["regime"]), "family": str(last["family"]),
            "conf": _num(last["confidence"]), "bars": int(_num(last["bars_in_regime"])),
            "knn_edge": _num(last["knn_edge"]),
        }
        _H4_CACHE.update(ts=now, data=data)
        return data
    except Exception:
        return None


# Sesiones de trading (clave, etiqueta)
SESSION_KEYS = [("tokyo", "Tokio"), ("london", "Londres"), ("newyork", "Nueva York")]


def _sess(conf, key, field, default):
    return (conf.get("sessions", {}).get(key, {}) or {}).get(field, default)


def _sessions_checklist_value(conf):
    s = conf.get("sessions", {})
    val = ["master"] if s.get("enabled", True) else []
    for k, _ in SESSION_KEYS:
        if (s.get(k, {}) or {}).get("enabled", True):
            val.append(k)
    return val


# ============================
# TABLA CON ESTILO (bordes + alineación consistente)
# ============================
_TH_STYLE = {"border": "1px solid #ccc", "padding": "6px 12px",
             "backgroundColor": "#f0f0f0", "textAlign": "left"}
_TABLE_STYLE = {"borderCollapse": "collapse", "width": "100%",
                "fontFamily": "monospace", "fontSize": "14px"}


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}"
    return v


def make_table(columns, rows):
    """columns: lista de (encabezado, clave, alineación 'left'/'right')."""
    header = html.Tr([html.Th(h, style=_TH_STYLE) for h, _, _ in columns])
    body = [
        html.Tr([
            html.Td(_fmt(r[k]), style={"border": "1px solid #eee",
                                       "padding": "6px 12px", "textAlign": a})
            for _, k, a in columns
        ]) for r in rows
    ]
    return html.Table([header] + body, style=_TABLE_STYLE)


# Columnas de la tabla paginada de historial (DataTable)
_num = Format(precision=2, scheme=Scheme.fixed)
ORDER_COLUMNS = [
    {"name": "Ticket", "id": "ticket"},
    {"name": "Symbol", "id": "symbol"},
    {"name": "Estrategia", "id": "strategy"},
    {"name": "Type", "id": "type"},
    {"name": "Volume", "id": "volume", "type": "numeric", "format": _num},
    {"name": "Apertura", "id": "open_price", "type": "numeric", "format": _num},
    {"name": "Cierre", "id": "close_price", "type": "numeric", "format": _num},
    {"name": "Profit", "id": "profit", "type": "numeric", "format": _num},
    {"name": "Cierre (hora)", "id": "time"},
]


app = dash.Dash(__name__)

app.layout = html.Div(
    style={"fontFamily": "Arial", "margin": "20px"},
    children=[
        html.H1(
            f"Contextual Bandit Trading Dashboard — Activo: {SYMBOL}",
            style={"textAlign": "center"}
        ),

        html.Div(id="account-banner"),

        dcc.Interval(id="interval-component", interval=5 * 1000, n_intervals=0),

        html.Div(id="mt5-status", style={
            "padding": "15px",
            "backgroundColor": "#f0f0f0",
            "borderRadius": "8px",
            "marginBottom": "20px",
            "fontSize": "18px"
        }),

        dcc.Graph(id="equity-graph"),

        html.H2("Régimen de mercado", style={"marginBottom": "6px"}),
        html.Div(id="regime-panel", style={"marginBottom": "20px"}),

        html.H2("Estrategias en vivo", style={"marginBottom": "6px"}),
        html.Div(id="strategies-panel", style={"marginBottom": "20px"}),

        dcc.Graph(id="reward-histogram"),

        html.Div(id="metrics-output"),

        html.H2("Operaciones Abiertas"),
        html.Div(id="positions-panel", style={
            "padding": "10px",
            "backgroundColor": "#fafafa",
            "borderRadius": "8px",
            "marginBottom": "20px"
        }),

        html.H2("Historial de Operaciones"),
        dash_table.DataTable(
            id="orders-table",
            columns=ORDER_COLUMNS,
            data=[],
            page_size=10,
            page_action="native",
            sort_action="native",
            style_table={"overflowX": "auto"},
            style_as_list_view=True,
            style_cell={
                "fontFamily": "monospace", "fontSize": "13px",
                "padding": "6px 12px", "textAlign": "left"
            },
            style_cell_conditional=[
                {"if": {"column_id": c}, "textAlign": "right"}
                for c in ["volume", "price", "profit"]
            ],
            style_header={"backgroundColor": "#f0f0f0", "fontWeight": "bold"},
            style_data_conditional=[
                {"if": {"filter_query": "{profit} < 0", "column_id": "profit"},
                 "color": "#c0392b"},
                {"if": {"filter_query": "{profit} > 0", "column_id": "profit"},
                 "color": "#1e8449"},
            ],
        ),

        # ============================
        # CONTROL DE OPERACIONES (reset manual)
        # ============================
        html.H2("Control de operaciones", style={"marginTop": "30px"}),
        html.Div(
            style={"padding": "15px", "backgroundColor": "#fafafa",
                   "borderRadius": "8px", "maxWidth": "760px"},
            children=[
                html.Button("Resetear # Operaciones a 0", id="reset-ops-btn", n_clicks=0,
                            style={"padding": "8px 18px", "backgroundColor": "#b9770e",
                                   "color": "white", "border": "none", "borderRadius": "6px",
                                   "cursor": "pointer"}),
                html.Button("Reiniciar robot", id="restart-bot-btn", n_clicks=0,
                            style={"padding": "8px 18px", "backgroundColor": "#2c3e50",
                                   "color": "white", "border": "none", "borderRadius": "6px",
                                   "cursor": "pointer", "marginLeft": "10px"}),
                html.Button("⏹ DETENER robot", id="stop-bot-btn", n_clicks=0,
                            style={"padding": "8px 18px", "backgroundColor": "#c0392b",
                                   "color": "white", "border": "none", "borderRadius": "6px",
                                   "cursor": "pointer", "marginLeft": "10px", "fontWeight": "bold"}),
                html.Button("▶ Iniciar robot", id="start-bot-btn", n_clicks=0,
                            style={"padding": "8px 18px", "backgroundColor": "#1e8449",
                                   "color": "white", "border": "none", "borderRadius": "6px",
                                   "cursor": "pointer", "marginLeft": "10px"}),
                html.Span("  (el contador se resetea solo cada día · reinicia tras cambiar config)",
                          style={"fontSize": "12px", "color": "#888"}),
                html.Div(id="reset-ops-msg", style={"marginTop": "10px", "fontSize": "13px"}),
                html.Div(id="restart-bot-msg", style={"marginTop": "6px", "fontSize": "13px"}),
                html.Div(id="stop-bot-msg", style={"marginTop": "6px", "fontSize": "13px"}),
                html.Div(id="start-bot-msg", style={"marginTop": "6px", "fontSize": "13px"}),
            ]
        ),

        # ============================
        # EDITOR DE CONFIGURACIÓN
        # ============================
        html.H2("Configuración", style={"marginTop": "30px"}),
        html.Div(
            style={"padding": "15px", "backgroundColor": "#fafafa",
                   "borderRadius": "8px", "maxWidth": "760px"},
            children=[
                html.Div(
                    style={"display": "grid",
                           "gridTemplateColumns": "repeat(2, minmax(0, 1fr))",
                           "gap": "12px"},
                    children=[
                        html.Div([
                            html.Label(lbl, style={"display": "block", "fontSize": "13px",
                                                   "marginBottom": "4px"}),
                            dcc.Input(id=iid, type="number", value=_cfg_get(cfg, path),
                                      step=step, style={"width": "100%", "padding": "6px"}),
                        ]) for (lbl, iid, path, step, _is_int) in CONFIG_FIELDS
                    ]
                ),

                # ---- Sesiones de trading (horario México) ----
                html.H3("Sesiones de trading (horario México)",
                        style={"marginTop": "18px", "fontSize": "15px"}),
                dcc.Checklist(
                    id="cfg-sessions",
                    options=[{"label": " Filtro de sesiones activo", "value": "master"}]
                            + [{"label": f" {lbl}", "value": k} for k, lbl in SESSION_KEYS],
                    value=_sessions_checklist_value(cfg),
                    style={"fontSize": "14px"},
                    labelStyle={"display": "inline-block", "marginRight": "18px"},
                ),
                html.Div(
                    style={"display": "grid",
                           "gridTemplateColumns": "repeat(3, minmax(0, 1fr))",
                           "gap": "10px", "marginTop": "8px"},
                    children=[
                        html.Div([
                            html.Label(f"{lbl} inicio", style={"display": "block", "fontSize": "12px"}),
                            dcc.Input(id=f"cfg-{k}-start", type="text",
                                      value=_sess(cfg, k, "start", "00:00"),
                                      style={"width": "100%", "padding": "5px"}),
                            html.Label(f"{lbl} fin", style={"display": "block", "fontSize": "12px",
                                                            "marginTop": "4px"}),
                            dcc.Input(id=f"cfg-{k}-end", type="text",
                                      value=_sess(cfg, k, "end", "00:00"),
                                      style={"width": "100%", "padding": "5px"}),
                        ]) for k, lbl in SESSION_KEYS
                    ]
                ),

                html.Button("Guardar configuración", id="cfg-save", n_clicks=0,
                            style={"marginTop": "15px", "padding": "8px 18px",
                                   "backgroundColor": "#2c3e50", "color": "white",
                                   "border": "none", "borderRadius": "6px",
                                   "cursor": "pointer"}),
                html.Div(id="cfg-save-msg", style={"marginTop": "10px", "fontSize": "13px"}),
            ]
        )
    ]
)


def _read_json(name):
    try:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


_INIT_BAL_CACHE = {"login": None, "value": None}


def account_initial_balance():
    """Balance inicial de la cuenta CONECTADA = suma de operaciones de balance
    (depósitos − retiros) sobre todo el historial. Se recalcula solo si cambia la
    cuenta (caché por login). Devuelve (valor, login) o (None, None)."""
    try:
        import MetaTrader5 as mt5
        from mt5_connect import ensure
        from datetime import datetime
        ensure()
        acc = mt5.account_info()
        if acc is None:
            return None, None
        login = acc.login
        if _INIT_BAL_CACHE["login"] == login and _INIT_BAL_CACHE["value"] is not None:
            return _INIT_BAL_CACHE["value"], login
        deals = mt5.history_deals_get(datetime(2000, 1, 1), datetime.now())
        total, found = 0.0, False
        for d in deals or []:
            if d.type == mt5.DEAL_TYPE_BALANCE:      # depósito/retiro inicial
                total += d.profit; found = True
        val = round(total if found else acc.balance, 2)
        _INIT_BAL_CACHE.update(login=login, value=val)
        return val, login
    except Exception:
        return None, None


def _pos_badge(in_pos, side=None, unreal=None, unit="%"):
    if not in_pos:
        return html.Span("— flat", style={"color": "#999"})
    color = "#1e8449" if (unreal is None or unreal >= 0) else "#c0392b"
    lbl = (side or "pos").upper()
    txt = f"● {lbl}" + (f"  {unreal:+.2f}{unit}" if unreal is not None else "")
    return html.Span(txt, style={"color": color, "fontWeight": "600"})


def _strat_card(title, sub, st, headers, rows):
    """Card de una estrategia: cabecera (estado/modo) + tabla de símbolos."""
    if st is None:
        head_right = html.Span("sin datos (no arrancada)", style={"color": "#999", "fontSize": "12px"})
    else:
        run = st.get("running"); dry = st.get("dry_run"); ok = st.get("account_ok")
        run_txt = "🟢 vivo" if run else "🔴 detenido"
        mode = "DRY-RUN" if dry else "REAL"
        mode_c = "#b9770e" if dry else "#1e8449"
        acc_txt = "" if ok else "  ⚠ cuenta"
        head_right = html.Span([
            html.Span(run_txt, style={"fontSize": "12px", "marginRight": "8px"}),
            html.Span(mode, style={"fontSize": "11px", "color": "white", "backgroundColor": mode_c,
                                   "padding": "1px 6px", "borderRadius": "4px"}),
            html.Span(acc_txt, style={"color": "#c0392b", "fontSize": "12px"}),
        ])
    th = {"textAlign": "left", "padding": "4px 10px", "borderBottom": "2px solid #ddd",
          "fontSize": "11px", "color": "#888"}
    td = {"padding": "4px 10px", "borderBottom": "1px solid #eee", "fontSize": "13px"}
    table = html.Table([
        html.Thead(html.Tr([html.Th(h, style=th) for h in headers])),
        html.Tbody([html.Tr([html.Td(c, style=td) for c in r]) for r in rows]),
    ], style={"borderCollapse": "collapse", "width": "100%", "fontFamily": "monospace"})
    return html.Div([
        html.Div([
            html.Span([html.B(title), html.Span(f"  {sub}", style={"color": "#999", "fontSize": "11px"})]),
            head_right,
        ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                  "marginBottom": "6px"}),
        table if rows else html.Div("sin símbolos", style={"color": "#999", "padding": "6px"}),
    ], style={"flex": "1", "minWidth": "300px", "padding": "12px 14px",
              "backgroundColor": "#fafafa", "borderRadius": "8px", "border": "1px solid #eee"})


def build_strategies_panel():
    cards = []
    # --- RSI(2) demo-live (índices D1) ---
    r = _read_json("rsi2_live_status.json")
    rows = []
    for sym, s in ((r or {}).get("symbols", {}) or {}).items():
        sig = "🟢 entrada" if s.get("entry_signal") else ("🔴 salida" if s.get("exit_signal") else "—")
        rows.append([sym, f"{s.get('rsi2','—')}", f"{s.get('vs_sma200_pct','—')}%", sig,
                     _pos_badge(s.get("in_position"), "long", s.get("unrealized_pct"))])
    cards.append(_strat_card("RSI(2)", "reversión · índices D1", r,
                             ["Símbolo", "RSI2", "vs SMA200", "Señal", "Posición"], rows))
    # --- Intradía Zarattini (índices M30) ---
    i = _read_json("intraday_live_status.json")
    rows = []
    for sym, s in ((i or {}).get("symbols", {}) or {}).items():
        if s.get("in_session"):
            loc = f"slot {s.get('slot','?')}/12"
            band = f"{s.get('cum_pct','—'):+.2f}% (±{s.get('band_pct','—')})" if isinstance(s.get('cum_pct'), (int, float)) else "—"
        else:
            loc = "fuera sesión"; band = "—"
        rows.append([sym, loc, band, f"{s.get('vwap','—')}",
                     _pos_badge(s.get("in_position"), s.get("side"), s.get("unrealized_pct"))])
    cards.append(_strat_card("Intradía (Zarattini)", "breakout · índices M30", i,
                             ["Símbolo", "Sesión", "cum% (banda)", "VWAP", "Posición"], rows))
    # --- Smart Trend Follower (oro/BTC H4) ---
    t = _read_json("stf_live_status.json")
    rows = []
    for sym, s in ((t or {}).get("symbols", {}) or {}).items():
        vs = s.get("vs_ema", "—")
        vs_c = "#1e8449" if vs == "sobre" else "#c0392b"
        rows.append([sym, f"{s.get('close','—')}",
                     html.Span(f"{vs} EMA200", style={"color": vs_c}),
                     f"{s.get('dch_lo','—')}–{s.get('dch_hi','—')}",
                     _pos_badge(s.get("in_position"), s.get("side"),
                                s.get("unrealized_R"), unit="R")])
    cards.append(_strat_card("Smart Trend Follower", "tendencia · oro/BTC H4", t,
                             ["Símbolo", "Close", "Tendencia", "Donchian 55", "Posición (R)"], rows))
    return html.Div(cards, style={"display": "flex", "gap": "14px", "flexWrap": "wrap"})


@app.callback(
    [
        Output("equity-graph", "figure"),
        Output("reward-histogram", "figure"),
        Output("metrics-output", "children"),
        Output("mt5-status", "children"),
        Output("positions-panel", "children"),
        Output("orders-table", "data"),
        Output("account-banner", "children"),
        Output("regime-panel", "children"),
        Output("strategies-panel", "children")
    ],
    [Input("interval-component", "n_intervals")]
)
def update_dashboard(n):
    # ============================
    # MT5 STATUS PANEL
    # ============================
    status = get_mt5_status()

    if not status["connected"]:
        mt5_panel = html.Div([
            html.H3("Estado MT5"),
            html.P("❌ No conectado"),
            html.P(status["error"])
        ])
    else:
        mt5_panel = html.Div([
            html.H3("Estado MT5"),
            html.P(f"Cuenta: {status['login']} ({status['account_type']})"),
            html.P(f"Servidor: {status['server']}"),
            html.P(f"Balance: {status['balance']:.2f}"),
            html.P(f"Equity: {status['equity']:.2f}"),
            html.P(f"Margen: {status['margin']:.2f}"),
            html.P(f"Free Margin: {status['free_margin']:.2f}"),
            html.P("Conexión: ✔ OK")
        ])

    # ============================
    # POSITIONS PANEL
    # ============================
    positions = get_positions()

    if len(positions) == 0:
        positions_panel = html.Div("No hay operaciones abiertas.")
    else:
        positions_panel = make_table([
            ("Ticket", "ticket", "left"),
            ("Symbol", "symbol", "left"),
            ("Estrategia", "strategy", "left"),
            ("Type", "type", "left"),
            ("Volume", "volume", "right"),
            ("Open Price", "price_open", "right"),
            ("Current Price", "price_current", "right"),
            ("Profit", "profit", "right"),
        ], positions)

    # ============================
    # ORDERS (tabla paginada → se devuelve la lista de dicts)
    # ============================
    orders = get_order_history()

    # ============================
    # TRADING DATA FROM CSV FILES
    # ============================
    df_equity = load_equity()
    df_rewards = load_rewards()

    # Equity curve (eje X temporal a partir de la columna 'time' epoch)
    equity_fig = go.Figure()
    if not df_equity.empty:
        if "time" in df_equity.columns:
            x = pd.to_datetime(df_equity["time"], unit="s")
        else:
            x = df_equity.index
        equity_fig.add_trace(go.Scatter(
            x=x,
            y=df_equity["equity"],
            mode="lines",
            name=f"Equity {SYMBOL}"
        ))
    # Línea de referencia = balance INICIAL de la cuenta conectada (dinámico:
    # se ajusta solo si se conecta otra cuenta).
    init_bal, init_login = account_initial_balance()
    if init_bal:
        equity_fig.add_hline(
            y=init_bal, line_color="#eb6834", line_width=2,
            annotation_text=f"Balance inicial {init_login}: {init_bal:,.0f}",
            annotation_position="top left",
            annotation_font_color="#eb6834",
        )
    equity_fig.update_layout(
        title=f"Equity Curve — {SYMBOL}",
        xaxis_title="Tiempo",
        yaxis_title="Equity"
    )

    # Nº mínimo de recompensas maduradas para mostrar histograma/métricas con sentido.
    # (Las recompensas son futuras: maduran tras reward_horizon barras.)
    MIN_REWARDS = 10
    n_rewards = len(df_rewards)

    # Reward histogram — barras rojo/verde por signo, línea en 0, ejes rotulados
    hist_fig = go.Figure()
    if n_rewards >= MIN_REWARDS:
        rewards = df_rewards["reward"].astype(float).values
        lo = min(rewards.min(), 0.0)
        hi = max(rewards.max(), 0.0)
        counts, edges = np.histogram(rewards, bins=30, range=(lo, hi))
        centers = (edges[:-1] + edges[1:]) / 2
        width = edges[1] - edges[0]
        colors = ["#1baf7a" if c >= 0 else "#e34948" for c in centers]
        hist_fig.add_trace(go.Bar(
            x=centers, y=counts, width=width, marker_color=colors,
            hovertemplate="reward ≈ %{x:.2f}<br>%{y} operaciones<extra></extra>"
        ))
        hist_fig.add_shape(type="line", x0=0, x1=0, yref="paper", y0=0, y1=1,
                           line=dict(color="#666", dash="dash"))
        hist_fig.add_annotation(x=0, yref="paper", y=1.04, text="0", showarrow=False,
                                font=dict(color="#666", size=12))
        hist_fig.update_layout(
            title=f"Distribución de Recompensas — {SYMBOL}",
            xaxis_title="Recompensa (P&L futuro / ATR)",
            yaxis_title="Frecuencia (nº de operaciones)",
            bargap=0, showlegend=False
        )
    else:
        hist_fig.update_layout(
            title=f"Distribución de Recompensas — {SYMBOL}",
            annotations=[dict(
                text=f"Recopilando recompensas… ({n_rewards}/{MIN_REWARDS})<br>"
                     f"<span style='font-size:12px'>maduran tras el horizonte de recompensa</span>",
                xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
                font=dict(size=16, color="#888")
            )],
            xaxis=dict(visible=False), yaxis=dict(visible=False)
        )

    # Metrics
    bot_status = read_status()
    ops = f"{bot_status.get('trades_today', '?')}/{bot_status.get('max_daily_trades', '?')}"
    openpos = f"{bot_status.get('open_positions', '?')}/{bot_status.get('max_open_positions', '?')}"

    _running = _bot_is_running()
    metrics_children = [
        html.H3(f"Métricas — {SYMBOL}"),
        html.P("🟢 Robot activo" if _running else "🔴 Robot detenido",
               style={"fontWeight": "bold",
                      "color": "#1e8449" if _running else "#c0392b"}),
    ]
    if n_rewards >= MIN_REWARDS:
        rewards = df_rewards["reward"].astype(float)
        metrics_children += [
            html.P(f"Sharpe: {compute_sharpe(rewards):.2f}"),
            html.P(f"Drawdown: {compute_drawdown(rewards):.2f}"),
            html.P(f"Winrate: {compute_winrate(rewards)*100:.2f}%"),
            html.P(f"Expectancy: {compute_expectancy(rewards):.5f}"),
        ]
    else:
        metrics_children.append(
            html.P(f"Recopilando recompensas… ({n_rewards}/{MIN_REWARDS})",
                   style={"color": "#888"})
        )
    sess_active = bot_status.get("session_active")
    sess_name = bot_status.get("session") or "—"
    local_t = bot_status.get("local_time", "")
    if sess_active is True:
        sess_txt, sess_color = f"✔ Operable ({sess_name})", "#1e8449"
    elif sess_active is False:
        sess_txt, sess_color = "⏸ Fuera de sesión", "#b9770e"
    else:
        sess_txt, sess_color = "—", "#888"
    dpnl = bot_status.get("daily_pnl")
    dmax = bot_status.get("max_daily_loss")
    if dpnl is not None:
        hit_loss = (dmax is not None and dmax < 0 and dpnl <= dmax)
        pnl_color = "#c0392b" if (dpnl < 0) else "#1e8449"
        pnl_txt = f"P&L del día: {dpnl:+.2f}"
        if dmax is not None and dmax < 0:
            pnl_txt += f"  (límite {dmax:+.0f})"
        if hit_loss:
            pnl_txt += "  ⛔ tope alcanzado — sin nuevas hasta mañana"
        pnl_line = [html.P(pnl_txt, style={"fontWeight": "bold", "color": pnl_color})]
    else:
        pnl_line = []
    metrics_children += [
        html.P(f"# Operations: {ops}", style={"fontWeight": "bold"}),
        *pnl_line,
        html.P(f"Posiciones abiertas: {openpos}", style={"color": "#555"}),
        html.P(f"Sesión: {sess_txt}", style={"color": sess_color}),
        html.P(f"Hora local: {local_t}", style={"color": "#888", "fontSize": "13px"}),
    ]
    metrics_html = html.Div(metrics_children)

    # ============================
    # BANNER DE CUENTA (seguridad)
    # ============================
    acc_ok = bot_status.get("account_ok")
    acc_login = bot_status.get("account_login")
    acc_server = bot_status.get("account_server")
    if acc_ok is True:
        account_banner = html.Div(
            f"🔒 Cuenta verificada: {acc_login} ({acc_server}) — DEMO",
            style={"padding": "8px", "backgroundColor": "#eafaf1", "color": "#1e8449",
                   "borderRadius": "6px", "textAlign": "center", "fontSize": "13px",
                   "marginBottom": "10px"}
        )
    elif acc_ok is False:
        reasons = ", ".join(bot_status.get("account_reasons", []) or [])
        account_banner = html.Div(
            [html.Div("⚠️ CUENTA NO VERIFICADA — EL BOT NO OPERARÁ",
                      style={"fontWeight": "bold", "fontSize": "16px"}),
             html.Div(f"Conectado a: {acc_login} ({acc_server}) · {reasons}",
                      style={"fontSize": "13px", "marginTop": "4px"})],
            style={"padding": "12px", "backgroundColor": "#c0392b", "color": "white",
                   "borderRadius": "6px", "textAlign": "center", "marginBottom": "10px"}
        )
    else:
        account_banner = html.Div()

    # ============================
    # PANEL DE RÉGIMEN (regime_master) — H4 (contexto) arriba + M5 (robot) abajo
    # ============================
    def _cell(label, value, color="#333", bold=False):
        return html.Div([
            html.Div(label, style={"fontSize": "11px", "color": "#888"}),
            html.Div(value, style={"fontSize": "15px", "color": color,
                                   "fontWeight": "600" if bold else "400"}),
        ])

    def _regime_block(reg, title, subtitle):
        head = html.Div([
            html.Span(title, style={"fontSize": "13px", "fontWeight": "700", "color": "#333"}),
            html.Span("  " + subtitle, style={"fontSize": "11px", "color": "#999"}),
        ], style={"marginBottom": "5px"})
        rid = (reg or {}).get("id", -1)
        if reg is None or rid is None or rid < 0:
            body = html.Div("Sin clasificar (recopilando datos)…",
                            style={"color": "#888", "padding": "10px"})
            return html.Div([head, body], style={"marginBottom": "12px"})
        rcolor = REGIME_COLORS.get(rid, "#888")
        rconf = reg.get("conf", 0.0); rbars = reg.get("bars", 0)
        kedge = reg.get("knn_edge", 0.0)
        conf_color = "#1e8449" if rconf >= 0.6 else ("#b9770e" if rconf >= 0.4 else "#c0392b")
        edge_color = "#1e8449" if kedge > 0.05 else ("#c0392b" if kedge < -0.05 else "#888")
        body = html.Div(
            style={"display": "grid",
                   "gridTemplateColumns": "repeat(auto-fit, minmax(120px, 1fr))",
                   "gap": "10px", "padding": "12px 14px", "borderRadius": "8px",
                   "borderLeft": f"6px solid {rcolor}", "backgroundColor": "#fafafa"},
            children=[
                _cell("Régimen", f"{reg.get('code','—')} · {reg.get('name','—')}", rcolor, bold=True),
                _cell("Familia", reg.get("family", "—")),
                _cell("Confianza", f"{rconf*100:.0f}%  ({rbars} barras)", conf_color),
                _cell("Edge k-NN", f"{kedge:+.2f} ATR", edge_color, bold=True),
                _cell("Estrategia sugerida", REGIME_PLAY.get(rid, "—"), rcolor),
            ]
        )
        return html.Div([head, body], style={"marginBottom": "12px"})

    m5_reg = {
        "id": bot_status.get("regime_id", -1), "code": bot_status.get("regime_code", "—"),
        "name": bot_status.get("regime_name", "—"), "family": bot_status.get("regime_family", "—"),
        "conf": bot_status.get("regime_conf", 0.0), "bars": bot_status.get("regime_bars", 0),
        "knn_edge": bot_status.get("knn_edge", 0.0),
    }
    regime_panel = html.Div([
        _regime_block(h4_regime(SYMBOL), f"H4 · {SYMBOL}",
                      "contexto — donde el clasificador discrimina"),
        _regime_block(m5_reg, f"M5 · {SYMBOL}",
                      "el que usa el robot para operar"),
    ])

    return (equity_fig, hist_fig, metrics_html, mt5_panel,
            positions_panel, orders, account_banner, regime_panel,
            build_strategies_panel())


_SESS_TIME_STATES = []
for _k, _ in SESSION_KEYS:
    _SESS_TIME_STATES += [State(f"cfg-{_k}-start", "value"), State(f"cfg-{_k}-end", "value")]


@app.callback(
    Output("cfg-save-msg", "children"),
    Input("cfg-save", "n_clicks"),
    [State(iid, "value") for (_, iid, _, _, _) in CONFIG_FIELDS]
    + [State("cfg-sessions", "value")] + _SESS_TIME_STATES,
    prevent_initial_call=True
)
def save_config(n_clicks, *values):
    try:
        nf = len(CONFIG_FIELDS)
        field_vals = values[:nf]
        sess_val = values[nf] or []
        times = values[nf + 1:]   # 6 valores: inicio/fin por sesión

        with open(CONFIG_FILE, encoding="utf-8") as f:
            conf = json.load(f)

        for (lbl, iid, path, step, is_int), val in zip(CONFIG_FIELDS, field_vals):
            if val is None:
                continue
            val = int(val) if is_int else float(val)
            d = conf
            for k in path[:-1]:
                d = d.setdefault(k, {})
            d[path[-1]] = val

        # sesiones
        conf.setdefault("sessions", {})
        conf["sessions"]["enabled"] = "master" in sess_val
        for i, (k, _) in enumerate(SESSION_KEYS):
            conf["sessions"].setdefault(k, {})
            conf["sessions"][k]["enabled"] = k in sess_val
            conf["sessions"][k]["start"] = times[i * 2] or "00:00"
            conf["sessions"][k]["end"] = times[i * 2 + 1] or "00:00"

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(conf, f, indent=2)

        ts = datetime.now().strftime("%H:%M:%S")
        return html.Span(
            f"✔ Guardado {ts}. Riesgo/SL/TP y SESIONES aplican al instante; "
            f"máx. posiciones, operaciones/día, horizonte y sleep requieren reiniciar el bot.",
            style={"color": "#1e8449"}
        )
    except Exception as e:
        return html.Span(f"✖ Error al guardar: {e}", style={"color": "#c0392b"})


@app.callback(
    Output("reset-ops-msg", "children"),
    Input("reset-ops-btn", "n_clicks"),
    prevent_initial_call=True
)
def reset_ops(n_clicks):
    write_command({"reset_trades_ts": datetime.now().timestamp()})
    ts = datetime.now().strftime("%H:%M:%S")
    return html.Span(
        f"✔ Reset enviado {ts}. El bot pondrá # Operaciones en 0 en su próxima iteración.",
        style={"color": "#1e8449"}
    )


@app.callback(
    Output("restart-bot-msg", "children"),
    Input("restart-bot-btn", "n_clicks"),
    prevent_initial_call=True
)
def restart_bot(n_clicks):
    write_command({"restart_bot_ts": datetime.now().timestamp()})
    ts = datetime.now().strftime("%H:%M:%S")
    return html.Span(
        f"✔ Reinicio enviado {ts}. El robot se relanzará re-leyendo el config "
        f"(unos segundos; el contador diario se conserva).",
        style={"color": "#2c3e50"}
    )


def _bot_is_running():
    try:
        return (time.time() - os.path.getmtime(STATUS_FILE)) < 15
    except Exception:
        return False


@app.callback(
    Output("stop-bot-msg", "children"),
    Input("stop-bot-btn", "n_clicks"),
    prevent_initial_call=True
)
def stop_bot(n_clicks):
    write_command({"stop_bot_ts": datetime.now().timestamp()})
    ts = datetime.now().strftime("%H:%M:%S")
    return html.Span(
        f"⏹ Paro enviado {ts}. El robot se detendrá en su próxima iteración "
        f"(no abrirá más órdenes).",
        style={"color": "#c0392b", "fontWeight": "bold"}
    )


@app.callback(
    Output("start-bot-msg", "children"),
    Input("start-bot-btn", "n_clicks"),
    prevent_initial_call=True
)
def start_bot(n_clicks):
    if _bot_is_running():
        return html.Span("El robot ya está activo.", style={"color": "#b9770e"})
    try:
        script = os.path.join(BASE_DIR, "main_live_v2.py")
        flags = (0x08000000 | 0x00000200) if os.name == "nt" else 0
        subprocess.Popen(
            [sys.executable, script], cwd=BASE_DIR, creationflags=flags,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        ts = datetime.now().strftime("%H:%M:%S")
        return html.Span(f"▶ Robot iniciado {ts}.", style={"color": "#1e8449"})
    except Exception as e:
        return html.Span(f"✖ Error al iniciar: {e}", style={"color": "#c0392b"})


if __name__ == "__main__":
    app.run(debug=True)

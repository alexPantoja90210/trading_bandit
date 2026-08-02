"""
michaelfx_cockpit.py — COCKPIT discrecional para la estrategia MichaelFX (puerto 8051).

NO opera. Da CONTEXTO en vivo (sesgo D/4H/1H, PDH/PDL, sesión UTC-5, niveles, OB aprox, Fib,
noticias) y una BITÁCORA (formulario + tabla + expectancy por escenario/sesión/cumplimiento).
El objetivo: medir si la discreción del trader tiene edge, con el mismo rigor del resto del sistema.
debug=False a propósito (evita el reloader). Kill: cerrar el proceso.
"""
import sys

import dash
from dash import dcc, html, dash_table, Input, Output, State, ctx
import MetaTrader5 as mt5

from mt5_connect import ensure
import michaelfx_engine as E

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from news_calendar import events as news_events
except Exception:
    news_events = None

app = dash.Dash(__name__, title="MichaelFX Cockpit")

GREEN, RED, GRAY, AMBER = "#1e8449", "#c0392b", "#888", "#b9770e"


def _trend_color(t):
    return {"alcista": GREEN, "bajista": RED, "rango": AMBER}.get(t, GRAY)


def _zona_line(z):
    if not z:
        return None
    col = GREEN if z.get("efficient") else GRAY
    return html.Div([
        "Zona retroceso: ",
        html.B(f"{z['depth']*100:.0f}% · {z['zone']}", style={"color": col}),
        html.Span(f"  ({z['side']})", style={"color": GRAY}),
    ], style={"fontSize": "12px", "marginTop": "2px"})


def cockpit_card(ctx_):
    px = ctx_["price"]
    rows = []
    for tfn in ["D1", "H4", "H1"]:
        b = ctx_.get(tfn)
        if not b:
            continue
        rows.append(html.Tr([
            html.Td(tfn, style={"padding": "2px 8px", "fontWeight": "600"}),
            html.Td(b["trend"], style={"padding": "2px 8px", "color": _trend_color(b["trend"])}),
            html.Td(b["estructura"], style={"padding": "2px 8px", "fontSize": "12px", "color": GRAY}),
        ]))
    lv = ctx_.get("levels", {})
    fib = ctx_.get("fib", {})
    obh, obm = ctx_.get("ob_H1", {}), ctx_.get("ob_M15", {})

    def fmt(v):
        return f"{v:.5f}" if v and v < 10 else (f"{v:.2f}" if v else "—")

    def ob_txt(ob):
        if not ob:
            return "—"
        b = f"🟢 {fmt(ob['bull'][0])}-{fmt(ob['bull'][1])}" if ob.get("bull") else ""
        s = f"🔴 {fmt(ob['bear'][0])}-{fmt(ob['bear'][1])}" if ob.get("bear") else ""
        return html.Span([b, html.Br() if b and s else "", s])

    return html.Div([
        html.Div([html.Span(ctx_["symbol"], style={"fontWeight": "700", "fontSize": "16px"}),
                  html.Span(f"  {fmt(px)}", style={"color": GRAY, "marginLeft": "6px"})]),
        html.Table(html.Tbody(rows), style={"margin": "4px 0"}),
        html.Div([
            html.Span(f"PDH {fmt(lv.get('PDH'))} · PDL {fmt(lv.get('PDL'))}",
                      style={"fontSize": "12px", "display": "block"}),
            html.Span(f"Hoy H {fmt(lv.get('HOY_H'))} · L {fmt(lv.get('HOY_L'))}",
                      style={"fontSize": "12px", "color": GRAY, "display": "block"}),
            html.Span(f"Fib {fib.get('dir','')}: 61.8% {fmt(fib.get('61.8%'))} · 75% {fmt(fib.get('75%'))}",
                      style={"fontSize": "12px", "color": "#5b6", "display": "block"}) if fib else None,
            _zona_line(ctx_.get("zona", {})),
            html.Div(["OB 1H aprox: ", ob_txt(obh)], style={"fontSize": "12px", "marginTop": "3px"}),
            html.Div(["OB 15m aprox: ", ob_txt(obm)], style={"fontSize": "12px"}),
        ]),
    ], style={"border": "1px solid #ddd", "borderRadius": "8px", "padding": "10px",
              "margin": "5px", "minWidth": "250px", "flex": "1"})


RULES = [
    ("Antes", ["Respetar todas las reglas", "Descansé 6-7h", "Hábitos previos hechos",
               "Mente tranquila hoy", "Calendario económico a la mano"]),
    ("Durante", ["Dirección del día clara", "Sin noticia de alto impacto", "Paciencia con los escenarios",
                 "Dentro del horario operativo", "Escenarios/OB activador claros", "Gestión de riesgo por trade",
                 "Riesgo día ≤1% (lunes 0.5%)", "No mover el SL (solo BE)", "Cerrar si +40min cerca de entrada",
                 "Si TP me saca, no opero más hoy", "No vengarme tras perdedora"]),
    ("Después", ["Registrar en bitácora", "Anotar errores", "Conclusiones para mejorar", "Backtesting visual"]),
]


def rules_panel():
    blocks = []
    for titulo, items in RULES:
        blocks.append(html.Div([
            html.Div(titulo, style={"fontWeight": "700", "marginTop": "6px", "color": AMBER}),
            html.Ul([html.Li(x, style={"fontSize": "12px"}) for x in items], style={"margin": "2px 0"}),
        ]))
    return html.Div(blocks)


def _num(id_, ph, step="any"):
    return dcc.Input(id=id_, type="number", placeholder=ph, step=step,
                     style={"width": "100%", "marginBottom": "4px"})


def _dd(id_, opts, ph):
    return dcc.Dropdown(id=id_, options=[{"label": o, "value": o} for o in opts],
                        placeholder=ph, style={"marginBottom": "4px"})


journal_form = html.Div([
    html.H4("Registrar operación"),
    html.Div([
        html.Div([_dd("f-sym", E.load_watchlist(), "Símbolo"), _dd("f-ses", ["London", "New York", "Tokio"], "Sesión"),
                  _dd("f-dir", ["long", "short"], "Dirección")], style={"flex": "1", "margin": "0 4px"}),
        html.Div([_dd("f-esc", ["1", "2", "3"], "Escenario"), _dd("f-ord", ["limit", "stop"], "Tipo orden"),
                  _dd("f-obtf", ["4H", "1H", "15m"], "OB temporalidad")], style={"flex": "1", "margin": "0 4px"}),
        html.Div([_num("f-entrada", "Entrada"), _num("f-sl", "Stop Loss"), _num("f-tp", "Take Profit")],
                 style={"flex": "1", "margin": "0 4px"}),
        html.Div([_num("f-riesgo", "Riesgo %"), _dd("f-res", ["win", "loss", "BE", "en curso"], "Resultado"),
                  _num("f-r", "R obtenido")], style={"flex": "1", "margin": "0 4px"}),
    ], style={"display": "flex", "flexWrap": "wrap"}),
    dcc.Input(id="f-conf", placeholder="Confluencias (PDH/PDL, Fib, liquidez, vacío...)", style={"width": "49%"}),
    dcc.Dropdown(id="f-reglas", options=[{"label": "Reglas: SÍ las respeté", "value": "si"},
                                         {"label": "Reglas: NO (rompí alguna)", "value": "no"}],
                 placeholder="¿Respeté las reglas?", style={"width": "49%", "display": "inline-block"}),
    dcc.Input(id="f-errores", placeholder="Errores cometidos", style={"width": "49%"}),
    dcc.Input(id="f-concl", placeholder="Conclusión / cómo mejorar", style={"width": "49%"}),
    html.Button("💾 Guardar operación", id="f-save", n_clicks=0,
                style={"marginTop": "8px", "padding": "6px 16px", "backgroundColor": GREEN,
                       "color": "white", "border": "none", "borderRadius": "6px", "cursor": "pointer"}),
    html.Span(id="f-msg", style={"marginLeft": "10px", "color": GREEN}),
])

app.layout = html.Div([
    html.H2("🎯 MichaelFX — Cockpit discrecional + Bitácora"),
    html.Div(id="session-bar", style={"padding": "6px 10px", "backgroundColor": "#f4f4f4",
                                       "borderRadius": "6px", "marginBottom": "8px"}),
    html.Div(id="news-bar", style={"padding": "6px 10px", "marginBottom": "8px", "fontSize": "13px"}),
    html.Div([
        html.Span("Watchlist:  ", style={"fontWeight": "600"}),
        dcc.Input(id="wl-add", type="text", placeholder="Símbolo (ej. GBPJPY)",
                  style={"width": "150px", "marginRight": "4px"}),
        html.Button("+ Agregar", id="wl-add-btn", n_clicks=0,
                    style={"marginRight": "14px", "cursor": "pointer"}),
        html.Div(dcc.Dropdown(id="wl-remove", placeholder="Quitar símbolo..."),
                 style={"width": "170px", "display": "inline-block", "verticalAlign": "middle"}),
        html.Button("− Quitar", id="wl-remove-btn", n_clicks=0,
                    style={"marginLeft": "4px", "cursor": "pointer"}),
        html.Span(id="wl-msg", style={"marginLeft": "12px", "color": GREEN}),
    ], style={"padding": "6px 10px", "backgroundColor": "#fafafa", "borderRadius": "6px",
              "marginBottom": "8px"}),
    html.H3("Contexto (sesgo · niveles · OB aprox · Fib)"),
    html.Div(id="cockpit", style={"display": "flex", "flexWrap": "wrap"}),
    html.Hr(),
    journal_form,
    html.H3("Rendimiento de la bitácora (expectancy)"),
    html.Div(id="stats-panel"),
    html.H4("Operaciones registradas"),
    dash_table.DataTable(id="journal-table", columns=[{"name": c, "id": c} for c in
        ["id", "fecha", "simbolo", "sesion", "direccion", "escenario", "tipo_orden", "rr_plan",
         "resultado", "r_obtenido", "respeto_reglas"]],
        style_cell={"fontSize": "12px", "padding": "3px 8px", "textAlign": "left"},
        style_header={"fontWeight": "700"}, page_size=15),
    html.Hr(),
    html.H3("Las 20 reglas de oro (leer cada día — regla 20)"),
    rules_panel(),
    dcc.Interval(id="tick", interval=15000, n_intervals=0),
], style={"fontFamily": "system-ui, sans-serif", "maxWidth": "1200px", "margin": "0 auto", "padding": "10px"})


@app.callback(
    [Output("session-bar", "children"), Output("news-bar", "children"),
     Output("cockpit", "children"), Output("stats-panel", "children"),
     Output("journal-table", "data"), Output("f-sym", "options"),
     Output("wl-remove", "options")],
    [Input("tick", "n_intervals"), Input("f-save", "n_clicks"),
     Input("wl-add-btn", "n_clicks"), Input("wl-remove-btn", "n_clicks")])
def refresh(_n, _s, _a, _r):
    ensure()
    watchlist = E.load_watchlist()
    ss = E.current_session()
    if ss.get("en_horario"):
        sess = html.Span([html.B(f"🟢 SESIÓN {ss['activa']} ACTIVA"),
                          f"  (cierra en {ss['cierra_en_min']} min · UTC-5 {ss['hora_utc5']})"],
                         style={"color": GREEN})
    else:
        sess = html.Span([html.B("⏸ Fuera de horario operativo"),
                          f"  próxima: {ss.get('proxima')} en {ss.get('faltan_min')} min · UTC-5 {ss['hora_utc5']}"],
                         style={"color": GRAY})

    # noticias alto impacto próximas para la watchlist
    news = "Calendario: (news_calendar no disponible)"
    if news_events:
        try:
            from datetime import datetime, timedelta
            now = datetime.utcnow()
            evs = [e for e in news_events(min_impact="High")
                   if now <= e["time"] <= now + timedelta(hours=24)
                   and any(s in e["symbols"] for s in watchlist)]
            if evs:
                items = [f"⚠ {e['time']:%H:%M}UTC [{e['ccy']}] {e['title'][:38]}" for e in evs[:6]]
                news = html.Span(["🗞 Noticias HIGH próx. 24h: "] +
                                 [html.Span(x + "   ", style={"color": RED}) for x in items])
            else:
                news = html.Span("🗞 Sin noticias HIGH en las próximas 24h para la watchlist", style={"color": GREEN})
        except Exception as ex:
            news = f"Calendario: error ({ex})"

    cards = []
    for s in watchlist:
        try:
            cards.append(cockpit_card(E.symbol_context(s)))
        except Exception as ex:
            cards.append(html.Div(f"{s}: error {ex}", style={"margin": "5px"}))

    st = E.stats()
    stats_panel = _render_stats(st)
    d = E.load_trades()
    data = d.to_dict("records") if len(d) else []
    opts = [{"label": s, "value": s} for s in watchlist]
    return sess, news, cards, stats_panel, data, opts, opts


@app.callback(Output("wl-msg", "children"),
    Input("wl-add-btn", "n_clicks"), State("wl-add", "value"), prevent_initial_call=True)
def wl_add(n, sym):
    ensure()
    ok, msg = E.add_symbol(sym)
    return html.Span(msg, style={"color": GREEN if ok else RED})


@app.callback(Output("wl-msg", "children", allow_duplicate=True),
    Input("wl-remove-btn", "n_clicks"), State("wl-remove", "value"), prevent_initial_call=True)
def wl_remove(n, sym):
    ok, msg = E.remove_symbol(sym)
    return html.Span(msg, style={"color": GREEN if ok else RED})


def _render_stats(st):
    if st.get("n") == 0 or "global" not in st or st["global"] is None:
        return html.Div("Sin operaciones cerradas aún — registra trades con R obtenido para ver expectancy.",
                        style={"color": GRAY})
    def blk(title, d):
        if not d:
            return None
        col = GREEN if d["expectancy_R"] > 0 else RED
        return html.Div([
            html.Div(title, style={"fontWeight": "700", "fontSize": "12px"}),
            html.Div(f"n={d['n']} · WR {d['winrate']:.0f}% · exp {d['expectancy_R']:+.2f}R · Σ {d['total_R']:+.1f}R",
                     style={"color": col, "fontSize": "13px"}),
        ], style={"border": "1px solid #eee", "borderRadius": "6px", "padding": "6px", "margin": "4px", "minWidth": "190px"})
    parts = [blk("GLOBAL", st["global"])]
    for k, v in st.get("por_escenario", {}).items():
        parts.append(blk(k, v))
    for k, v in st.get("por_sesion", {}).items():
        parts.append(blk(k, v))
    for k, v in st.get("por_reglas", {}).items():
        parts.append(blk(k, v))
    return html.Div([p for p in parts if p], style={"display": "flex", "flexWrap": "wrap"})


@app.callback(Output("f-msg", "children"),
    Input("f-save", "n_clicks"),
    [State("f-sym", "value"), State("f-ses", "value"), State("f-dir", "value"),
     State("f-esc", "value"), State("f-ord", "value"), State("f-obtf", "value"),
     State("f-entrada", "value"), State("f-sl", "value"), State("f-tp", "value"),
     State("f-riesgo", "value"), State("f-res", "value"), State("f-r", "value"),
     State("f-conf", "value"), State("f-reglas", "value"), State("f-errores", "value"),
     State("f-concl", "value")], prevent_initial_call=True)
def save_trade(n, sym, ses, dir_, esc, ordn, obtf, entrada, sl, tp, riesgo, res, r, conf, reglas, err, concl):
    if not sym or not esc:
        return "⚠ falta al menos símbolo y escenario"
    tid = E.add_trade({
        "simbolo": sym, "sesion": ses, "direccion": dir_, "escenario": esc, "tipo_orden": ordn,
        "ob_tf": obtf, "entrada": entrada, "sl": sl, "tp": tp, "riesgo_pct": riesgo,
        "resultado": res, "r_obtenido": r, "confluencias": conf, "respeto_reglas": reglas,
        "errores": err, "conclusion": concl,
    })
    return f"✔ guardada #{tid}"


if __name__ == "__main__":
    ensure()
    print("=== MichaelFX Cockpit en http://localhost:8051 ===")
    app.run(debug=False, port=8051)

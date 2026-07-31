# Forward-test en vivo (demo) — conclusiones

Registro vivo del comportamiento de las estrategias en demo, para comparar lo
REALIZADO contra el backtest y decidir si pasan a dinero real. Se actualiza a
medida que se acumulan trades. Datos crudos: `data/*_live_trades.csv`; foto
agregada: `python live_report.py` → `data/live_report.json`.

## Estrategias en observación (arranque 2026-07-29)

| Estrategia | Mercado/TF | Ejecutor (magic) | Expectativa backtest |
|---|---|---|---|
| RSI(2) reversión | Índices US D1 | `rsi2_live.py` (220002) | PF 2-3, wr 70-82% |
| Zarattini breakout | Índices US M30 | `intraday_live.py` (220003) | PF ~1.2, Sharpe 0.81 OOS |
| Smart Trend Follower | Oro/BTC H4 | `stf_live.py` (220004) | PF ~1.25, wr ~40% (R) |
| Bandit (5 brazos) | XAUUSD M5 | `main_live_v2.py` (123456) | **solo-aprende** (sin edge; no opera) |

## Qué estamos comprobando
- ¿El **PF / winrate** en vivo converge al del backtest? (el juez que hundió a las SMC: PF 4.7→0.85).
- ¿Los **costos reales** (spread + slippage + swap) están dentro de lo estresado?
- ¿La **ejecución** (fills, trailing, flat al cierre) se comporta como el modelo?
- Baja frecuencia (STF ~40/año, RSI2 ~10/año/índice, Zarattini ~1/día) → muestra sólida en **semanas-meses**.

## Observaciones (cronológico)

### 2026-07-29 — arranque
- Montados los 3 ejecutores demo-live + panel en dashboard. Bandit pasado a solo-aprende.
- Estado inicial: RSI2 largo NAS100 abierto; Zarattini US500 corto (1 trade ya cerrado −0.28%); STF flat (oro/BTC bajo EMA200, sin ruptura).
- Aún sin muestra — solo infraestructura. Primeras conclusiones cuando haya ≥10-15 trades cerrados por estrategia.

### 2026-07-29 — primer día del intradía Zarattini (US500)
- **Bug corregido**: `intraday_live` no registraba stop-outs del bróker → un short stopeado (−0.67%) faltaba en el CSV. Añadida la detección + backfill. Los otros ejecutores ya lo tenían.
- **Día choppy en US500** (cayó de ~7400 a ~7305): 3 trades → −0.28% (VWAP), −0.67% (SL), +0.82% (eod_close). **Neto −0.1% (≈plano)**. El ganador compensó los dos perdedores → el diseño (pocos ganadores cubren muchos perdedores pequeños) **aguantó el whipsaw sin necesitar guarda**.
- **`eod_close` (plano al cierre) funcionó** — el 3er short cerró exacto al cierre de sesión.
- **NAS100 (N=1.5) no operó** en todo el día (banda más ancha) — coherente con el walk-forward que le pidió N alto. El diseño discrimina bien.
- Matiz: el SL de desastre del vivo no está en el backtest (que sale por VWAP) → en días muy choppy el vivo puede perder algo más. A vigilar sobre varias sesiones.
- **Veredicto parcial**: sin señal de alarma; guarda no necesaria por ahora. Muestra aún trivial (3 trades).

### 2026-07-30 — resumen nocturno
- Noche tranquila: sin trades nuevos (sesión intradía cerrada, STF sin ruptura, RSI2 sosteniendo swings). 5 procesos vivos toda la noche, sin caídas ni intervenciones.
- **RSI(2) dip-buys se dieron vuelta**: NAS100 largo −$31→**+$32**, US500 largo +$6→**+$19**; no-realizado abierto **+$51**. El rebote confirma la tesis (comprar pánico), pero es NO-realizado — se valida al cerrar.
- Intradía US500: día previo cerró neto −0.13% (3 trades, el ganador eod_close compensó el whipsaw).
- STF flat (oro/BTC consolidando bajo EMA200). Cuenta: balance $9,911 / equity $9,962.

### 2026-07-30 — expansión de símbolos (más muestra, misma cuenta)
Validé candidatos antes de agregar (fiel a la metodología). Añadidos en vivo por hot-reload (sin reinicio):
- **RSI(2)** +US30 (PF 1.32), +US2000 (PF 1.78), +FRA40 (PF 1.68) → 5 símbolos. max_positions 2→5. Descartados: GER40/UK100/AUS200 (PF ~1.0-1.1, flojos).
- **Intradía** +US30 (N=1.0; WF OOS Sharpe 0.20, PF 1.05 — marginal, solo índices de sesión US). Descartado US2000 (WF ~ruido, PF 1.01).
- **STF** +ETHUSD (PF 1.09, +10R/9a — marginal, cripto familia BTC). Descartados XAGUSD (breakeven) / XPTUSD (pierde).
- Nota: US30/ETH son marginales (por debajo del core) — se agregan para acumular muestra independiente, no por edge fuerte. Los del RSI2 (US2000/FRA40/US30) sí son sólidos (PF 1.3-1.78).

### 2026-07-30 — incidente: crash del intradía por carrera de archivos (corregido)
- El ejecutor intradía murió con `PermissionError` en `os.replace` al guardar el status JSON — **carrera de archivos de Windows** (otro proceso, probablemente mis lecturas de verificación + dashboard cada 5s, tenía el JSON abierto). NO fue bug de los símbolos nuevos; la sesión operó bien.
- **Acción autónoma:** hice `_save` robusto (reintenta 6× + fallback a escritura directa, nunca crashea) en los 3 ejecutores (intraday, rsi2, stf). Relancé los 3; reconciliaron sus posiciones sin duplicar. Intradía conservó sus 2 largos (US500/NAS100).
- Aprendizaje: `os.replace` no es atómico-seguro en Windows si el destino está abierto. Fix aplicado también protege a rsi2/stf.

### 2026-07-30 (pm) — primeras salidas del RSI(2), ambas verdes
- RSI(2) cerró sus 2 primeros swings por `close>SMA5`: **NAS100 +1.92%, US500 +1.81%** (Σ +3.7%, 2/2). El NAS100 era el "cuchillo" que estuvo −$31 bajo el agua → aguantó → cerró +1.92%. Tesis completa: comprar pánico → aguantar → salir en rebote.
- Agregado vivo: RSI2 2 tr / wr 100 / prom +1.86% (perfil consistente con backtest, muestra trivial). Intradía 8 tr / wr 50 / PF 1.04 (breakeven, en línea). STF 0 (flat).
- Fix aplicado: el no-realizado de los 3 paneles usaba el cierre de barra (viejo intradía) en vez del precio actual → corregido a `pos.price_current`.

### 2026-07-31 (mañana) — la PC se reinició en la noche: recuperado + blindado
- La PC se reinició de madrugada → **todos los procesos murieron** (eran python en segundo plano atados a la sesión). Hubo un **hueco de monitoreo** post-reinicio (sin data hasta la mañana).
- **Fix aplicado:** (1) `start_bots.ps1` (raíz del proyecto) lanza los 8 componentes como **procesos independientes** (sobreviven al cierre del agente Y a reinicios), idempotente. (2) **Auto-arranque al iniciar sesión** vía clave Run de HKCU (`TradingBots`) — un reinicio ahora se auto-recupera. (3) Todo desde el **venv python** (tiene MT5 + dash + todo).
- Estado: 8 componentes arriba y funcionales (bandit, RSI2, intradía, STF, dashboard, collector, meta_observer, meta_retrain), conectados a la demo. Data del meta-pipeline intacta en disco.
- **Para levantar manual:** ejecutar `start_bots.ps1`. **Para desactivar auto-arranque:** borrar la entrada `TradingBots` de HKCU\...\Run.

<!-- Añadir aquí cada revisión: fecha, nº trades acumulados, PF/wr vivo vs backtest, y veredicto parcial. -->

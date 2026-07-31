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

### 2026-07-31 — investigación CROSS-ASSET / intermarket (búsqueda de alpha nuevo)
Motivación: el muro del proyecto es la SEÑAL, no el algoritmo → buscar inputs genuinamente
predictivos. Se eligió la vía **cross-asset / intermarket**: que el movimiento PASADO de un
activo prediga el FUTURO de otro (lead-lag = operable; la correlación contemporánea no lo es).
- **Predictores disponibles en el bróker**: USDX (dólar), VIX (miedo), CN50 (China) + universo propio.
- **`cross_asset.py`** — matriz lead-lag D1 (corr A[t]→B[t+1], t-stat). Hallazgo dominante:
  **reversión de corto plazo del complejo de equity** (US500/NAS100/GER40 se predicen entre sí
  NEGATIVO, corr ~−0.13, t ~−7 sobre ~2500 días) con **spillover al dólar** (equity↑ hoy → USDX↓ /
  EURUSD↑ mañana). La reversión equity↔equity **solapa con RSI(2)**; lo NUEVO y cross-clase es
  **equity(hoy) → FX(mañana)**.
- **`backtest_cross_asset.py`** — backtest de la señal nueva `sign(equity hoy) → EURUSD mañana`
  (2015-2026, ~2839 días, costo ~0.6 pip/vuelta):
  - Bruto Sharpe **+0.52** / neto **+0.33**, PF 1.06, wr 50.6%.
  - **Test de nulidad (200 barajados): percentil 96 → PASA >95%.** La señal NO es casualidad.
  - PERO **no robusta año a año**: 6 verdes / 6 rojos, y **2023-24-25 en rojo** (decaimiento /
    no-estacionaria). Años fuertes 2018 (+2.61) y 2022 (+2.04).
- **Veredicto**: señal cross-asset **genuina pero fina y no-estacionaria** (patrón del proyecto).
  **No para desplegar sola.** Uso correcto: **feature del meta-modelo** (contexto cross-asset para
  que el value decida cuándo pesa). Se agregaron 2 features al `build_meta_dataset.py`:
  `ctx` extra = retorno del complejo equity (z) y del USDX del **día previo** (sin lookahead).
- **Ablación meta (con vs sin cross-asset, `train_meta_model.py --drop-xa`)**: **NO mejora**.
  META filtro mean +0.049 vs +0.048, Sharpe +0.06 = +0.06, dirección 52.7% vs 52.6% → despreciable.
  Coherente: la señal cross-asset predice **EURUSD/dólar**, pero los edges del meta operan
  **oro/BTC/índices** → el contexto equity/USDX no ayuda a asignar entre STF/RSI2/Zarattini. Las 2
  features quedan en el pipeline (inocuas) pero **no aportan**. Para explotar el cross-asset habría que
  **meter EURUSD como edge propio** en el meta — pendiente, baja prioridad (señal decayendo). El muro
  sigue siendo la señal.

### 2026-07-31 — backtest de NOTICIAS sobre el intradía Zarattini (`backtest_news.py`)
Pregunta: ¿un filtro de blackout de noticias mejora la estrategia? (motivación: "prevenir operar
en momentos de noticias"). Método sin calendario scrapeado: eventos deterministas — **NFP** (1er
viernes, 8:30 ET, exacto) y **FOMC** (anuncio 14:00 ET, fechas conocidas 2021-2026) — partiendo el
P&L DIARIO de la estrategia (N validado por símbolo) por tipo de día. Data M30 ~2022-2026.
- **Hallazgo (contra-intuitivo): los días-evento son MEJORES que los normales** en US500/NAS100/US30
  (Sharpe días-evento ~2.3-2.5 vs ~0.8-1.0 normales). **NFP es un viento a favor fuerte**
  (Sharpe **+2.57 NAS100, +4.78 US30** esos días): el momentum cabalga el movimiento direccional
  pre-apertura del NFP. **La noticia ES donde vive el edge** para una estrategia de momentum.
- **El filtro (saltar días-evento) EMPEORA los 3** (ΔSharpe −0.17/−0.17/−0.07): tira los mejores días.
- **Excepción FOMC** (14:00 ET intra-sesión = whipsaw): US500 Sh −0.60, US30 Sh −2.91 **pero
  NAS100 Sh +1.95** → signo MIXTO por símbolo, n=34 → **no robusto** para un filtro FOMC-only.
- Robustez por año: P&L en días-evento positivo en la mayoría (US500 5/5, NAS100 4/5, US30 4/5).
- **VEREDICTO: filtro de noticias RECHAZADO como regla general** — resta rendimiento. La intuición
  "no operar en noticias" está **al revés** para momentum. No se integra a `intraday_live` ni al
  `collector`. `news_calendar.py` queda como infra disponible (blackout util para estrategias de
  REVERSIÓN como RSI2, no probado), pero el intradía NO lo usa. Un test más fino (saltar solo la
  ventana ±X min del anuncio FOMC, no el día entero) queda pendiente y de baja prioridad (efecto
  mixto por símbolo). Confirma el ethos: probar antes de creer; aquí la intuición común era falsa.

### 2026-07-31 — VIX y VOLUMEN como indicadores (¿edge o reducir DD?) — AMBOS RECHAZADOS
Diagnóstico condicionando la recompensa (meta_dataset, ATR scale-free) por VIX y por volumen
relativo al entrar. `analyze_vix.py`, `analyze_volume.py`, `validate_vix_rsi2.py`.
- **VIX — no edge**: corr(reward, VIX)≈0 los 3 edges (RSI2 −0.001, STF −0.010, Zarat +0.037).
- **VIX — no reduce DD (robusto)**: el diagnóstico por terciles sugería "RSI2 muere en VIX alto"
  (reward +0.388 medio → +0.002 alto) pero la **validación lo tumba**: capar RSI2 solo mejora en
  VIX≤20 (Sharpe 3.41→4.80, DD −18.6→−13.4) — **no monótono** (≤22 y ≤25 PEORES) = artefacto de
  umbral. Y las entradas omitidas con VIX>25 (pánico profundo) **suman +12.5 ATR, mean +0.232,
  positivas TODOS los años** → los dips en pánico profundo rebotan MÁS fuerte; capar VIX tira los
  mejores trades. La intuición "evitar VIX alto" es FALSA para RSI2. Muestra 2020+ (COVID/2022).
- **Volumen — no edge**: corr(reward, vol_relativo)≈0 en TODO (Zarat −0.055 a 0, RSI2 +0.01..0.07,
  STF ~0). "Ruptura CON volumen = real" es **falso** aquí (Zarat NAS100 corr −0.055: volumen BAJO
  rinde MÁS). tick_volume del CFD no distingue ruptura real de falsa. Único matiz: dips RSI2 con
  volumen alto rebotan algo mejor en NAS100 (monótono +0.119→0.211) pero corr ~0.05, n chico,
  inconsistente entre índices → no accionable.
- **VEREDICTO**: ni VIX ni volumen construyen edge ni reducen DD de forma robusta. No se integran.
  Refuerza el muro del proyecto (la señal es el límite) y el valor de VALIDAR: el filtro VIX parecía
  bueno por terciles y se cayó al medir DD/Sharpe/robustez real.

### 2026-07-31 — CADENAS DE MARKOV sobre la señal — sin edge nuevo (`markov_analysis.py`)
Una cadena de Markov de 1er orden = modelo de DEPENDENCIA SERIAL: P(estado_sig | estado_actual).
Test [A] direccional: estados por terciles de retorno, E[ret_sig|estado] estimado en TRAIN,
operar signo en TEST (walk-forward + costos + test de nulidad 50 barajados).
- **5 de 6 activos = azar**: US500·D1 (Sh +0.30 vs b&h +0.74, pctl 42%), XAUUSD·H4 (+0.36 vs b&h
  +1.27), BTCUSD·H4 (−0.63), **US500·M30 (−2.70**, los costos intradía lo matan), EURUSD·D1 (−0.40).
- **Único que pasa nulidad: NAS100·D1** (Sh +0.85, pctl 96%) — pero apenas iguala a b&h (+0.89) y
  **re-descubre la MISMA reversión diaria de índices que RSI2 YA explota**. No aporta nada nuevo.
- Matriz de transición up/down [B]: donde hay memoria es **reversión** modesta (P(up|dn)−P(up|up)
  ~+0.03..0.06: NAS100 0.534/0.570, BTC 0.475/0.540, EURUSD 0.477/0.512); oro/US500·M30 sin memoria.
  Es exactamente el Hurst≈0.5 visto por otra lente + la reversión que RSI2/cross-asset ya vieron.
- **VEREDICTO**: Markov es PRÁCTICO (barato, sin sobreajuste) pero **validado NO añade edge** — la
  dependencia serial es demasiado débil (near-random-walk) y nuestras reglas hechas a mano (RSI2
  reversión, STF momentum) ya capturan lo poco que hay; Markov re-descubre lo mismo o pierde (intradía).
  Rama NO muerta (baja prioridad): Markov sobre RÉGIMENES (persistencia/transición) como feature de
  ASIGNACIÓN del meta-modelo — no para dirección; payoff esperado bajo por la casi-memorylessness.

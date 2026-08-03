# Edge de futuros: term structure del VIX (roll / prima de vol) — 2026-08-03

**PRIMER input genuinamente nuevo que rompe el muro del proyecto.** No es predicción de precio
(todo eso falló) sino una **prima de riesgo estructural** cosechada vía la curva de futuros del VIX.
Script: `vix_term_structure.py`. Data externa (yfinance, cacheada en `data/futures/`): VIX, VIX3M,
VIXY, S&P — con el tail en muestra (2008/2018/2020).

## Contexto
Antes concluimos que la curva (term structure/carry/roll) era la única vía de edge propio de
futuros, pero la data del bróker no servía (VIXY 5a sin tail). Con yfinance conseguimos VIX3M
desde 2006 y VIXY desde 2011 (incluye Volmageddon feb-2018 y COVID mar-2020).

## Señal
`TS = VIX / VIX3M`. **<1 = CONTANGO** (curva al alza, calma → carry de vol-corto favorable, VIXY
decae). **>1 = BACKWARDATION** (estrés → evitar/salir del short-vol). Señal usa TS de AYER (sin lookahead).

## Resultados (2011-2026, VIXY, con costos ~3bps/día)
| Estrategia | Anual | Sharpe | maxDD | Peor día | x inicial |
|---|---|---|---|---|---|
| A) Short-vol SIEMPRE | +42% | 0.51 | −93% | −43% | 4.3x |
| **B) Short-vol TIMED (curva)** | **+49%** | **0.67** | **−66%** | −34% | **28x** |
| Buy&hold VIXY (largo) | −35% | −0.61 | −100% | | ~0 |

**El TAIL (lo crítico):** Volmageddon feb-2018: SIEMPRE −48% vs TIMED **−11%**. COVID mar-2020:
SIEMPRE −72% vs TIMED **+0%** (evitado). Yen-carry ago-2024: −33% vs −12%. La curva se invierte a
backwardation y saca antes del desastre sostenido.

Robustez: 12/16 años con Sharpe+; negativos 2018/2022/2024/2026. Sobrevive costo 3× (Sharpe 0.44).

## Veredicto
- **ES un edge real y propio de futuros** (prima de riesgo de volatilidad vía el roll). Sin lookahead,
  robusto por año, sobrevive costos. Estructuralmente distinto a todo lo previo (que era precio → muro).
- **PERO alto riesgo de cola**: maxDD −66% incluso gestionado, peor día −34%, retorno negativamente
  sesgado (el Sharpe subestima). Te PAGAN por cargar riesgo de crash. Reacciona con lag (cortó, no
  evitó, Volmageddon). Requiere shortear VIXY (borrow/spread).

## Pendiente para hacerlo desplegable
1. **Overlay de gestión de riesgo**: vol-targeting / sizing / stop duro / filtro de nivel de VIX para
   capar el DD de −66% a algo sobrevivible (¿mantiene el Sharpe?).
2. **Robustez del umbral**: probar TS<0.95/1.0/1.05 (evitar knife-edge). El TS<1 es parameter-free (bien).
3. **Cross-check** con el roll real de futuros VIX (CBOE VX1/VX2) para descartar artefacto de VIXY.
4. **Despliegue**: el bróker tiene VIX + VIXY.US (5a) pero NO VIX3M → habría que alimentar la señal con
   data externa en vivo. Shortear VIXY en CFD: verificar disponibilidad.
5. **Extender la tesis de la curva**: carry cross-seccional multi-mercado (AQR) + COT (CFTC, gratis).

*Honestidad:* es lo primero que pasa "hecho bien", pero NO es para operar aún — es alto riesgo de
cola que exige gestión seria. El hallazgo clave: **la curva de futuros es el input nuevo que faltaba.**

## DOMADO + DIVERSIFICACIÓN (2026-08-03) — `vix_carry_managed.py`, `vix_carry_portfolio.py`
**Domar (opción 1):** los overlays "inteligentes" (filtro de nivel VIX, vol-targeting) FALLARON
(bajan el Sharpe — la term structure ya captura el régimen). Lo único que doma es **dimensionar chico
(fracción fija)**: el Sharpe es invariante a escala. Sized 30%: **Sharpe 0.67, +13%/año, maxDD −23%,
peor día −10%**, tail sobrevivible (feb-2018 −2.9%, COVID 0%). Sleeve real y sobrevivible.
**Diversificación (lo que decide si vale):** correlación diaria vs STF **+0.01**, vs RSI2 **+0.10** —
casi independiente. Agregarlo a la cartera: **2-way (STF+RSI2) Sharpe 1.03/DD−14% → 3-way (+VIXcarry)
Sharpe 1.18/DD−10%.** Mejora Sharpe Y baja DD. **Primer input nuevo que además MEJORA la cartera validada.**
Caveat: co-movimiento de COLA (RSI2 y VIXcarry sufren juntos en crash → la diversificación se comprime
en crisis, la corr media +0.10 lo subestima). Despliegue: necesita feed VIX3M en vivo + shortear VIXY.

## ROBUSTEZ (2026-08-03) — `vix_carry_robust.py` — SOBREVIVE el rigor que mató al ORB
Cinco pruebas, todas **pasadas**:
1. **Umbral NO es knife-edge:** Sharpe positivo en TODOS los cortes de contango — TS<0.90 (+0.47),
   <0.95 (+0.48), <1.0 (+0.67), <1.05 (+0.69). Monótono y estable, no un punto frágil.
2. **Split OOS 60/40:** TRAIN Sharpe **+0.69** vs TEST Sharpe **+0.65** — casi idénticos. (Contraste:
   el ORB colapsó +1.37→−0.25 OOS. Esto NO colapsa.)
3. **MECANISMO confirmado (es el roll, no curve-fit):** E[retorno de VIXY | contango] = **−51%/año**
   (decae) vs | backwardation] = **+52%/año** (dispara). El edge ES la decadencia estructural del roll.
4. **Cross-check en OTRO ETF (SVXY, inverso, construcción distinta, cambió de −1x a −0.5x en 2018):**
   Sharpe **+0.64**, mismo signo y magnitud. → NO es artefacto de la data de VIXY.
5. **Correlación de COLA — el caveat se REFUTA:** corr con RSI2 todos los días +0.10, pero en días de
   ESTRÉS (VIX>25 o S&P<−1.5%, n=623) = **−0.05**. La diversificación NO se comprime en crisis; se
   MANTIENE, porque la señal timed sale a cash (backwardation) justo cuando RSI2 compra el dip.

**Veredicto de robustez:** el VIX carry es el **primer edge del proyecto que sobrevive OOS + mecanismo
+ cross-check + cola**. Es real, estructural y diversificante. Únicos pendientes ya NO son de validación
sino de **despliegue**: (a) feed de VIX3M en vivo, (b) shortear VIXY/comprar SVXY en CFD (verificar
disponibilidad y borrow en Pepperstone), (c) sizing fijo ~30% del sleeve.

## DISPONIBILIDAD EN PEPPERSTONE (2026-08-03) — el instrumento resuelto
Revisé los símbolos del bróker (demo 61566435). Instrumentos de volatilidad: **VIX**, **VIXY.US**, **SVXY.US**.
- **VIXY.US = LONGONLY** (trade_mode=1) → **NO se puede shortear**. Mata la implementación "short VIXY".
  (Normal: los ETF apalancados/inversos suelen estar restringidos a largo.)
- **SVXY.US = LONGONLY** → pero SVXY es el ETF INVERSO, así que **LARGO SVXY = short-vol** = justo lo que
  queremos. Es exactamente la versión que ya validé en el cross-check #4 (Sharpe +0.64). Cotiza con volumen
  real (~12-18k/día, ~$57, "All Sessions"). swap_long ~−6%/año (financiamiento, cubierto por el costo 3bps/día
  del backtest que sobrevive 3×). **→ SVXY.US LARGO es el instrumente desplegable y validado.**
- **VIX (índice) = FULL** (se puede shortear, swap_short POSITIVO +3.08 = te pagan el carry). PERO rastrea
  el VIX SPOT (revierte a la media ~15-20), NO el roll de la curva → no replica el backtest y su cola es
  PEOR (spot VIX x6.8 en COVID vs VIXY x4). Sería otra estrategia; requeriría su propia validación. No usar.

**Plan de despliegue concreto:** ejecutor `LARGO SVXY.US` cuando TS=VIX/VIX3M de ayer <1 (contango), plano
en backwardation, sizing fijo 30% del sleeve. Input externo de la señal: **VIX3M diario de CBOE** (CSV oficial;
yfinance ^VIX3M va semanas retrasado → solo respaldo). Ejecutor: `svxy_live.py` (magic 220005, dry-run).

## VALIDACIÓN EN DATA REAL DEL BRÓKER (2026-08-03) — `svxy_broker_validate.py`
Cierre del círculo: corrí la estrategia sobre `SVXY.US` **de Pepperstone** (8.3 años, 2018-04→2026-07), no yfinance.
- **Tracking:** corr(SVXY bróker, SVXY yfinance) = **+0.988**, vol diaria 2.32% vs 2.33% → **es el mismo
  instrumento**, sin divergencia ni artefacto de data del ETF.
- **Estrategia en el instrumento real (2018-2026, sin escalar):** Sharpe **+0.38**, +12.6%/año, maxDD −38%
  — casi idéntico a yfinance en la misma ventana (+0.39). **El edge se sostiene en lo que de verdad operamos.**
- **HONESTIDAD (recalibración):** este +0.38 es MÁS BAJO que el +0.67/+0.64 de titular porque (a) el bróker
  NO tiene data pre-2018 → esta es solo la ventana reciente y dura (arranca en Volmageddon), y (b) SVXY es
  −0.5x desde 2018 (media exposición). El +0.67 venía de VIXY con historia completa 2011-2026. Por año: 5
  positivos / 4 negativos (2023 +1.91 pero 2018 −0.80, 2022 −0.43, 2026 −0.42) → real pero GRUMOSO: te pagan
  por cargar riesgo de crash y algunos años la "tasa del crash" domina. Sized 30% el DD baja a ~−11%.
- **Expectativa realista desplegable:** sleeve positivo, descorrelacionado y diversificante, pero **modesto**
  (Sharpe ~0.4 en el régimen reciente), no un home run. Su valor es de CARTERA (baja DD, no correlaciona),
  no de retorno standalone. Coherente con el estándar del proyecto: sobrevive, pero sin inflar el número.

## APORTE A LA CARTERA — número HONESTO en ventana bróker (2026-08-03) — `svxy_portfolio_broker.py`
Rehíce el aporte de cartera con SVXY.US del bróker y recortado a 2018-2026 (no el VIXY 2011+ optimista).
- **Correlaciones se mantienen bajas:** VIXcarry~STF +0.01, ~RSI2 +0.09, y en ESTRÉS (VIX>25, n=369) **−0.04**.
  La propiedad diversificante SOBREVIVE en la ventana honesta. STF~RSI2 +0.02.
- **Equal-weight 1/3 (3-way):** Sharpe ~flat (2-way 0.93→3-way 0.94) pero **DD −12.5%→−10.2%**.
- **Barrido de peso (overlay sobre el 2-way) — el hallazgo accionable:** el equal-weight sobre-asigna.
  El punto dulce es **~15-20% de peso**: Sharpe **0.93→0.98** (MEJORA) y DD **−12.5%→−11.0%**. A 30% aún
  baja DD pero empieza a diluir Sharpe (0.96); a 50% lo daña (0.81). → **asignarle ~15-20% del riesgo de
  cartera, no 1/3.** A ese peso MEJORA Sharpe Y baja DD, honestamente, sobre el instrumento y período reales.
- **Nota de sizing:** el `exposure_pct=0.30` del ejecutor controla la vol PROPIA del sleeve (domar su −38%
  a ~−11%); el "peso 15-20%" es cuánto CAPITAL/riesgo darle al ejecutor SVXY vs RSI2/STF. Son cosas distintas.

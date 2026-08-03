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

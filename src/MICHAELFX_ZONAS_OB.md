# MichaelFX — Volatilidad de XAUUSD y zona de OB más eficiente

Análisis empírico (2026-07-31) para responder: ¿el oro requiere ajustes, conviene bajar a 1M,
y en qué **zona de retroceso** es más eficiente el OB? Scripts: `xauusd_michaelfx_analysis.py`
(volatilidad/spread/régimen) y `xauusd_ob_zones.py` (eficiencia por zona tras HH/LL). Solo LEE.

## Contexto: XAUUSD en régimen de vol ALTA
- ATR diario del oro **2.2–3.8%** los últimos meses (vs ~1% histórico; EURUSD/GBPUSD ~0.3%).
- Intradía **2–3× más volátil** que el FX (M15: 0.133% vs 0.061%).

## Respuestas a las 3 preguntas
| Pregunta | Veredicto (con dato) |
|---|---|
| ¿Ajuste por volatilidad? | **Sí, proporcional al ATR** (sizing, SL, targets) — no cambiar la lógica; en R es scale-free. |
| ¿Bajar a 1M? | **No** el análisis. Spread mediano sano (8pts ≈ 4.4% del ATR M1) PERO: sin data para validar, mucho ruido, slippage en noticias. 1m/3m ya es la **capa de ejecución** del método, no de setup. |
| ¿Solo quiebres del 40% (STOP)? | **No.** El oro retrocede profundo → los setups del **80% con LIMIT** (OB profundo) son de mayor calidad; el 40%/STOP se whipsapea más. |

## Zona de OB más eficiente (retroceso tras HH/LL)
Para cada nivel de entrada Z se midió: fill% (¿se llena la limit?), cont% (¿continúa al extremo
antes de invalidar?), R:R (entrada en Z, stop al origen 100%, objetivo el extremo 0% → Z/(1−Z)),
y exp(R)=cont%·R:R−(1−cont%). Muestra con n usable: **H4 tras HH (n=55)**, H1 tras HH (n=33),
M15 tras LL (n=24). M5/M15-HH n<10 → ruido.

**H4 · tras HH (n=55, retroceso mediano 62%):**
| Zona OB | fill% | cont% | R:R | exp(R) |
|---|---|---|---|---|
| 38.2% | 78% | 58% | 0.62 | −0.06 |
| 50% | 62% | 47% | 1.00 | −0.06 |
| 61.8% | 53% | 38% | 1.62 | −0.01 |
| **79%** | 42% | 22% | 3.76 | **+0.04** |

**Conclusiones:**
1. **Mecánicamente NO hay zona "mágica" rentable** — expectancias ~cero/negativas en casi todo.
   Coherente con el muro del proyecto: la estructura SMC mecánica no da edge limpio en oro.
2. **Tendencia clara y accionable: favorece zonas PROFUNDAS.** Cuanto más profundo el retroceso,
   menos continuación (58%→22%) pero mucho mejor R:R (0.62→3.76); el neto favorece **61.8–79%**
   (descuento/premium), que coincide con la confluencia Fib 61.8/75 del método. → **OB profundo,
   no momentum somero.**
3. **TF importa: la estructura vive en H4/H1** (y 15m); M5/M15 sin muestra → refuerza NO bajar a 1M.

## Traducción operativa
- Ubica el **OB en la zona 61.8–79%** (descuento para compras / premium para ventas), no en
  retrocesos someros. Objetivo: el extremo previo / liquidez (targets amplios).
- Marco: **4H/1H** para impulso y zona · **15m/5m** para afinar el OB · **1m/3m** solo para gatillar.
- **El juez final es la bitácora**: desglosar expectancy por **zona de entrada** y ver en 20-30
  trades si en la práctica las entradas profundas pagan mejor (como sugiere el dato).
- El **cockpit** (8051) muestra en vivo la **zona de retroceso** actual por símbolo (columna nueva).

*Honestidad:* esto caracteriza el comportamiento (dónde mirar), no garantiza edge — el OB
discrecional con confluencia probablemente supera a esta medición mecánica burda. La tendencia
(profundo > somero, TF alto > bajo) es el aporte sólido.

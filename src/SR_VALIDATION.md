# Validación de estrategias de Soporte/Resistencia (S/R) — 2026-08-02

Pregunta: ¿hay evidencia de estrategias de S/R con edge robusto validable con nuestro proceso?
Script: `sr_strategy_test.py`. Niveles = pivots diarios (PP/R1/S1/R2/S2) del día PREVIO (sin
lookahead), entradas en H1, 1 trade/día/lado, plano al cierre de día. 7 instrumentos.
Batería: expectancy en R, split OOS 60/40, sensibilidad a costos (x3), nulidad, robustez.

## Evidencia académica (contexto)
- **Osler (2000, 2003) "Support for Resistance"**: los niveles S/R publicados por bancos SÍ tienen
  poder predictivo en FX — rebotes se agrupan cerca de números redondos, rupturas aceleran. Es de
  los pocos resultados de análisis técnico con respaldo peer-reviewed. PERO es sobre la DISTRIBUCIÓN
  de stops/rebotes, no una estrategia mecánica simple que sobreviva costos.
- Pivots diarios y S/R retail: mucha creencia, poco respaldo robusto.

## Resultado — y el BUG que cazó el proceso
1. **Primera corrida (BREAK): +0.21 a +0.30R en los 7 instrumentos, WR 65-69%, OOS+, null 100%.**
   Demasiado limpio para un proyecto donde nada mecánico funciona → señal de bug, no de hallazgo.
2. **Bug encontrado**: el BREAK detectaba con `close > R1` pero llenaba a `R1` → entraba a un precio
   YA rebasado (ventaja gratis: empieza en ganancia, stop más lejos). **Look-ahead/fill artifact.**
3. **Fix a stop-order real (fill al TOCAR el nivel)**: el edge COLAPSÓ (+0.30 → ~0, WR 67→52%).
4. **Fix conservador de intrabarra** (chequear stop/target en la barra de entrada, stop primero):
   BREAK queda **negativo** (−0.04 a −0.20); solo XAU marginal (+0.03, falla null, muere a costo).

## Veredicto
- **BREAK de niveles: NO edge.** El resultado espectacular era 100% artefacto de fill/lookahead.
  Bien simulado, es plano-a-negativo (las rupturas de pivots se stopean).
- **FADE en niveles: sin edge robusto.** FX negativo; los índices US aparecen positivos (US500
  +0.127, OOS estable, null 100%, sobrevive x3) PERO SOLO bajo el supuesto optimista de exit en la
  misma barra. Con exit solo en la barra siguiente, US500 cae a +0.015 (muere a costo). El signo
  DEPENDE del supuesto de intrabarra → el "edge" vive en movimientos intra-H1 cuyo orden H1 no puede
  resolver. **No validable sin M1/tick** (que no tenemos; scalping en CFD prohibitivo por costo/slippage).
- **Meta-lección**: el proceso robusto FUNCIONÓ — cazó un falso positivo espectacular (+0.30R en todo)
  que habría sido un "descubrimiento" desastroso. Misma disciplina que salvó de SMC (PF 4.7→0.85),
  ORB (colapso OOS) y del filtro VIX (artefacto de umbral).
- Consistente con el muro: **el límite es la señal.** S/R es de las ideas más arbitradas del retail;
  no hay edge mecánico robusto en pivots con nuestra data. El único hilo no-muerto (fade de índices
  US) es inseparable del ruido intrabarra en H1 → inconcluyente, requeriría tick data.

## Pendiente/limitación
La variante con respaldo académico (Osler: números redondos en FX) NO se pudo testear bien: necesita
resolución intrabarra (tick) para medir dónde se agrupan rebotes/rupturas — dato que no tenemos.

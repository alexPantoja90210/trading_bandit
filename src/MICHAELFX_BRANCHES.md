# MichaelFX — Ciclo de BRANCHES (aprender de los trades, estilo bandit/AlphaZero)

## Idea
No ejecutar solo un bias fijo: mantener **variantes de las reglas (branches)**, medir cuál
genera más **efectividad (expectancy en R)** aprendiendo de los trades, y quedarnos con lo que
gana — igual que el ciclo de recompensas del bandit + AlphaZero (comparar aproximaciones por
resultado, iterar). **Dos fuentes de reward corren en paralelo:**
1. **Branches mecánicos** (`michaelfx_backtest.py`): aproximaciones automatizadas de MichaelFX.
2. **Journal discrecional** (`michaelfx_cockpit.py`, puerto 8051): TUS trades manuales, con
   expectancy por escenario/sesión/cumplimiento-de-reglas.

El indicador de TradingView (`michaelfx_indicator.pine`) muestra las reglas en vivo para operar
el sistema (no el bias suelto).

## Regla de decisión (para no engañarnos)
- **1 mes = ruido.** Ningún branch se "adopta" por un mes bueno. Se necesita señal robusta
  **acumulada varios meses** y consistente entre pares — mismo estándar OOS del resto del proyecto.
- La mecánica es un **baseline**; el objetivo real es ver si TU discreción (journal) le gana.
- Documentar cada mes aquí: resultados por branch + acciones/mejoras.

## Definición de branches (`michaelfx_backtest.py`)
Mecanización: sesgo HTF (EMA20/50) → operar a favor · OB = última vela opuesta antes de un BOS
(cierre rompe máx/mín de `ob_str` velas) · entrada al mitigar el OB en dirección del sesgo · SL
al borde opuesto + buffer · TP a R:R · filtro de sesión UTC-5 · máx 2 ops/día · 1 activa a la vez.

| Branch | Sesgo | R:R | Sesiones | Notas |
|---|---|---|---|---|
| A_baseline | H1 | 2.5 | sí | referencia |
| B_rr5 | H1 | 5.0 | sí | ratio alto |
| C_no_session | H1 | 2.5 | **no** | control (sin filtro de sesión) |
| D_bias4H | H4 | 2.5 | sí | sesgo más alto |
| E_no_bias | — | 2.5 | sí | sin filtro de sesgo (control) |

## Ledger de resultados

### 2026-07 (mes 1) — pares: XAUUSD, EURUSD, GBPUSD, US500, NAS100
| Branch | n | winrate | expectancy R | ΣR |
|---|---|---|---|---|
| C_no_session | 228 | 36% | **+0.25** | +55.9 |
| E_no_bias | 230 | 32% | +0.06 | +13.5 |
| A_baseline | 213 | 30% | +0.01 | +3.1 |
| B_rr5 | 211 | 19% | −0.02 | −3.5 |
| D_bias4H | 192 | 28% | −0.02 | −4.1 |

**Lectura honesta (mes 1):**
- **Ningún branch validado.** 1 mes = ruido alto; no se adopta nada.
- **C_no_session "gana" pero sospechoso**: gana operando MÁS (sin filtro de sesión) → probable
  ruido/sobreajuste al mes; contradice la premisa de disciplina de sesión de MichaelFX. NO adoptar.
- **Sobre-operación**: ~43 trades/par/mes (~2/día/par) — la mecánica opera MUCHO más que el
  MichaelFX discrecional (selectivo, máx 2/día total). La mecanización pierde la **selectividad**,
  que es justo donde vive el edge discrecional. → **mejora #1**.
- winrate 19-36% con expectancy ~0 = perfil de "muchas entradas mediocres" — coherente con que la
  mecánica de OB (como el test de SMC previo) no captura la lectura discrecional.

**Acciones / mejoras propuestas (para mes 2):**
1. **Subir selectividad** (mejora #1): exigir confluencia (OB en zona macro 4H/1H + PDH/PDL o Fib
   61.8/75) y limitar a máx 2 ops/día TOTAL, no por par. Esperado: muchas menos entradas, mejor calidad.
2. Añadir **branch F** = A + confluencia PDH/PDL, y **branch G** = A + Fibonacci 61.8/75.
3. Empezar a poblar el **journal discrecional** (tus trades reales en demo) → primer punto de
   comparación humano vs mecánico.
4. Repetir el backtest a fin de mes y **acumular** aquí (no reemplazar) para ver estabilidad.

<!-- Añadir cada mes: tabla de branches + lectura + acciones. Nunca adoptar por 1 mes. -->

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

Desde **mejora #1 (2026-07-31)**: cap **2 ops/día TOTAL** (no por par) + branches con **confluencia**.

| Branch | Sesgo | R:R | Sesiones | Confluencia | Notas |
|---|---|---|---|---|---|
| A_baseline | H1 | 2.5 | sí | — | referencia (sin confluencia) |
| C_no_session | H1 | 2.5 | **no** | — | control (sin filtro de sesión) |
| F_conf_pdhl | H1 | 2.5 | sí | OB cerca de **PDH/PDL** | mejora #1 |
| G_conf_fib | H1 | 2.5 | sí | OB en zona **Fib 61.8-75** | mejora #1 |
| H_conf_both | H1 | 5.0 | sí | PDH/PDL + R:R alto | mejora #1 |

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

### 2026-07 (mes 1, v2 — MEJORA #1 aplicada: cap 2/día TOTAL + confluencia)
Cambios: (a) máx 2 entradas/día TOTAL across pares (antes 2/par); (b) branches con confluencia
(OB cerca de PDH/PDL, o en zona Fib 61.8-75). Mismos pares.

| Branch | pre-cap → cap | winrate | expectancy R | ΣR |
|---|---|---|---|---|
| G_conf_fib | 16 → 15 | 73% | **+1.40** | +21.0 |
| H_conf_both | 36 → 22 | 36% | +0.83 | +18.3 |
| F_conf_pdhl | 37 → 24 | 54% | +0.77 | +18.4 |
| C_no_session (sin conf) | 555 → 46 | 37% | +0.29 | +13.5 |
| A_baseline (sin conf) | 340 → 46 | 26% | −0.13 | −5.9 |

**Lectura:**
- **Logro estructural**: la confluencia cortó la sobre-operación de **~340 candidatas a 16-37** →
  la mecánica ahora es **selectiva como el método**. Se arregló la mejora #1.
- **Hipótesis de MichaelFX validada en dirección**: confluencia > sin-confluencia en expectancy
  (Fib el más fuerte), consistente con "zona de valor = mayor probabilidad" — no es aleatorio.
- **PERO no se adopta nada**: G con **n=15 en 1 mes** es ruido; el +1.40R lo inflaron pocos TP a
  +2.5R. Varios pares con n=1 (sin sentido individual). El estándar sigue siendo robustez multi-mes.

**Acciones / mejoras (mes 2):**
1. **Acumular** — repetir a fin de mes y ver si G_conf_fib/F_conf_pdhl sostienen el signo. Solo tras
   3-4 meses consistentes + n razonable se consideraría adoptar una confluencia como default.
2. **Poblar el journal discrecional** (tus trades con el indicador en demo) → comparar humano vs
   estas branches mecánicas (el objetivo real del ciclo).
3. Posible mejora #2: confluencia de **liquidez** (máximos/mínimos iguales) — aún no implementada.

<!-- Añadir cada mes: tabla de branches + lectura + acciones. Nunca adoptar por 1 mes. -->

# Sistema de trading — estado consolidado (fuente de verdad)
*Actualizado 2026-08-03. Todo corre en DEMO Pepperstone (login 61566435). Nada con dinero real aún.*

Este documento consolida lo que **sobrevivió** la investigación. La regla del proyecto: un edge cuenta
solo si pasa OOS + nulidad + robustez a la perturbación + costos. Esa disciplina mató muchos falsos
positivos (ver Cementerio). Lo que queda es modesto pero **real**.

---

## 1. Los 4 edges validados (en VIVO-DEMO)

| Edge | Mercado / TF | Tipo | Magic | Validación | Sharpe solo* |
|---|---|---|---|---|---|
| **RSI(2)** | Índices US · D1 (NAS100, US500, US30, US2000, FRA40) | Reversión | 220002 | OOS, robusto por año | **~0.89** |
| **STF** (Smart Trend Follower) | XAUUSD, BTCUSD, ETHUSD · H4 | Tendencia | 220004 | OOS, cesta | ~0.52 |
| **Intradía Zarattini** | US500, NAS100, US30 · M30 | Ruptura de sesión | 220003 | Walk-forward OOS 0.81 | ~0.81 |
| **VIX carry** | SVXY.US (largo en contango) | Prima de volatilidad | 220005 | OOS + mecanismo + cross-check + cola | ~0.38 |

*Sharpe standalone en la ventana honesta del bróker (2018-2026). Todos son **modestos y grumosos** — el
valor está en la CARTERA, no en cada uno solo.

**Notas:**
- VIX carry: es de POSICIÓN (hold) → pocos trades; se mide por curva de retorno, no por PF. Feed de la señal
  = CBOE oficial (yfinance ^VIX3M va retrasado). Instrumento resuelto: SVXY.US (VIXY es solo-largo). Ahora
  mismo mantiene una posición larga real (contango). Sizing 30% del sleeve.
- DAX/GER40 intradía está VALIDADO (OOS 0.60) pero **no desplegado** — necesita refactor multi-sesión
  (`MULTISESSION_DESIGN.md`). Único edge validado sin desplegar.

## 2. La cartera combinada (número honesto, ventana bróker 2018-2026)

| Cartera | Sharpe | maxDD |
|---|---|---|
| 2-way (STF + RSI2) | ~0.93–1.0 | −12% |
| **3-way (+ VIX carry, peso ~15-20%)** | **~0.98** | **−11%** |

Correlaciones casi nulas (VIXcarry~STF **+0.01**, ~RSI2 **+0.09**, STF~RSI2 **+0.02**) → diversifican de
verdad. El VIX carry a peso pequeño **mejora Sharpe Y baja DD**; a 1/3 (equal-weight) sobre-asigna.
El intradía es un sleeve adicional descorrelacionado (sesión US).

## 3. Infraestructura operativa

- **11 procesos** (venv python): dashboard (8050), bandit solo-aprende, los 4 ejecutores, learning_collector,
  meta_observer, meta_retrain, data_backup, michaelfx_cockpit (8051). Levantar: `start_bots.ps1` (idempotente).
- **Auto-arranque:** Tarea Programada `TradingBotsAutostart` — domingo 17:15 MX (apertura Tokio) + al logon.
- **Candado de cuenta:** todos los ejecutores verifican DEMO 61566435 antes de cada orden; rechazan cualquier
  otra cuenta. Kill switch por estrategia: `data/<estrategia>_command.json` con `{"stop":true}`.
- **Medición:** `live_report.py` agrega los trades reales (vivo vs papel); `LIVE_CONCLUSIONS.md` acumula
  conclusiones. Pendiente: tracker de curva de retorno para los sleeves de posición (VIX carry, STF).

## 4. El bandit (el proyecto original) — solo-aprende

`execute=false`. Guarda recompensas y entrena el modelo pero **NO opera**: las 5 señales sobre XAUUSD tienen
recompensa media significativamente NEGATIVA (t≈−4), <50% de aciertos en todos los brazos → sin edge
direccional. Se queda como infraestructura de aprendizaje, sin arriesgar la demo. **El límite es la señal
del oro, no el algoritmo.**

## 5. Cementerio — NO re-perforar (ya probado con rigor, muerto)

Predicción de precio: 5 brazos del bandit, Hurst/DFA, cross-asset lead-lag, Markov, eigen/stat-arb,
cointegración, manifold/regularización. Intradía mecánico: ORB M15, S/R pivots, breakout NY del oro,
handoff Londres→NY. Filtros/indicadores: noticias, VIX-como-indicador, volumen. Carry (intentos de
generalizar): **FX carry** (falla nulidad) y **cripto funding carry** (falla nulidad+OOS, −63% DD).

**Lección transversal:** pasar 1-2 pruebas no basta (el handoff del oro pasó OOS+nulidad y aun así era
artefacto — lo delató la robustez a mover la hora). Y **el carry NO es genéricamente cosechable** aquí: el
VIX carry (prima de volatilidad) es el caso especial que funciona, no el primero de una familia.

## 6. Conclusiones de fondo

1. El edge no vino del bandit adaptativo sino de **reglas simples validadas** (RSI2, trend, breakout) y de
   **una prima estructural nueva** (la curva del VIX).
2. Los edges son **específicos por mercado**: reversión en índices (no en oro), tendencia en oro/BTC,
   ruptura en índices (no en oro), volatilidad vía VIX. La arquitectura ganadora = **especialistas por
   mercado**, no un bandit eligiendo brazos en un solo símbolo.
3. El espacio de edges **fáciles y desplegables está casi agotado**. Lo que quedó en pie es lo que hay:
   una cartera de Sharpe ~1.0 / DD ~11% (backtest), en forward-test de papel.

## 7. Estado y próximos pasos honestos

- **Ahora:** los 4 ejecutores en vivo-demo acumulando evidencia; el bandit aprendiendo; MichaelFX midiéndose.
- **Gate antes de dinero real** (decisión del usuario, sin prisa): el forward-test en papel debe confirmar
  PF/win-rate vs backtest a lo largo de un ciclo. Ver `maturing-before-real`.
- **Ramas sin explorar (baja prioridad):** COT/CFTC (posicionamiento), overnight-vs-intraday drift, cerrar
  la validación del momentum cross-seccional (Sharpe +0.31). Ninguna urgente.
- **Cuando el papel dé veredicto:** ingeniería del salto a real (config de cuenta real con guards
  equivalentes, circuit breakers de cartera, monitor de divergencia).

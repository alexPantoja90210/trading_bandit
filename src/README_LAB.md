# Laboratorio de validación (backtest offline)

Herramientas para **vetar cualquier idea de estrategia contra histórico antes de
tocar la cuenta**. Todo es offline y de solo lectura: bajan datos de MT5, simulan,
y reportan. **Ninguno envía órdenes.**

## Requisitos
- Terminal **MT5 abierto y logueado** (los scripts usan `copy_rates_from_pos`).
- Ejecutar **desde `src/`** con el intérprete del venv:
  ```bash
  cd src && ../.venv/Scripts/python.exe <script>.py
  ```
- El bróker entrega ~50.000 barras por timeframe (M5 ≈ 8 meses, H1 ≈ 8.5 años).
  Para más historia, subir el límite de barras en MT5 (Herramientas → Opciones → Gráficos).

## Cómo se define la estrategia (base común)
- **5 brazos**: `trend`, `mean`, `flat`, `momentum`, `volatility` (ver `ARM_NAMES`).
- **Dirección por brazo** (`implied_direction` / `directions`): trend y momentum siguen
  la familia del régimen (SELL en `TREND_DOWN`); mean revierte a la SMA20; volatility
  sigue la última barra; flat no opera.
- **Recompensa**:
  - *direccional* (proxy rápido): `dir × (close[t+H] − close[t]) / ATR[t]`.
  - *realista*: entra con SL = ATR×1.5 y TP = ATR×2.0, simula barra a barra qué toca
    primero (high/low) y resta el spread.
- **Contexto (26 dims)**: 6 features + bloque de régimen (`build_context`).

## Scripts

| Script | Qué hace | TF | Salida clave |
|---|---|---|---|
| `train_bandit.py` | Entrena el LinTS (info completa) y hace backtest out-of-sample | M5 | media/winrate/sharpe vs baselines; guarda `data/lints_state.json` |
| `sweep_bandit.py` | Barrido horizonte × subconjuntos de features | M5 | tabla bandit/random/bestfix por config |
| `sweep_tf.py` | Mismo barrido en varios timeframes | M15, H1 | tabla por TF; marca configs rentables |
| `sltp_backtest.py` | Backtest con SL/TP + costos por brazo y bandit | M5 | P&L por estrategia a varios niveles de costo |
| `sltp_h1.py` | SL/TP + costos, bandit entrenado sobre P&L realista | H1 | P&L out-of-sample por brazo y bandit |
| `robustness_h1.py` | P&L SL/TP **año por año** (estrategias fijas) | H1 | edge por año vs retorno del oro |
| `walkforward_h1.py` | Walk-forward **online** (bandit se actualiza como en vivo) | H1 | P&L acumulado adaptativo vs fijo, por año |
| `walkforward_gamma.py` | Walk-forward variando el **factor de olvido** (memoria) | H1 | P&L por gamma; ¿la memoria corta ayuda? |
| `walkforward_gate.py` | Aplica el filtro `should_trade` (régimen) — config exacta del bot vivo | H1 | cada estrategia con/sin gate |

Módulo de apoyo: **`bandit_contextual.py`** — clase `LinTSBandit` (Thompson lineal,
con `gamma` de olvido y scaler; `save`/`load`).

## Cómo probar una idea NUEVA
1. **Nuevas features** → editar `ALL_FEATURES` (y `build_features` si es un indicador nuevo).
2. **Nuevas reglas de dirección** → editar `implied_direction` / `directions` (mantener
   la misma lógica en el bot vivo `main_live_v2.py` para que coincidan).
3. **Otro timeframe** → cambiar `mt5.TIMEFRAME_*` (o usar `sweep_tf.py`).
4. **Otra recompensa** → ajustar `SL_MULT`/`TP_MULT` o la función `sim`.

**Secuencia de validación recomendada** (de barato a exhaustivo):
```
sweep_tf.py        → ¿hay señal direccional en algún TF/config?
   ↓ si algo asoma
sltp_h1.py         → ¿sobrevive a SL/TP + costos reales?
   ↓ si sigue positivo
robustness_h1.py   → ¿aguanta año por año, o es una sola ventana?
   ↓ si es robusto
walkforward_h1.py  → ¿funciona online, atravesando cambios de régimen?
```
Regla de oro: **si `random` le gana, no hay edge.** Y **una sola ventana verde no
es un edge** — exige robustez por sub-períodos.

## Hallazgo — Familia A: bandit + 5 estrategias direccionales (DESCARTADA)
- **Sin edge robusto** en M5 ni H1. El único año positivo (2025) es el melt-up del oro,
  no la estrategia.
- El bandit (simple, contextual, con olvido) **colapsa a un solo brazo** porque el
  contexto **no tiene señal predictiva** que distinga qué brazo ganará. Ninguna variante
  de bandit crea edge donde no lo hay.
- **`regime_master` + `should_trade`** (`walkforward_gate.py`): el filtro de régimen
  **recorta las pérdidas ~90%** (trend −6931→−544, vol −3158→−257 en H1) pero **ninguna
  estrategia se vuelve positiva**. El régimen sirve como **control de riesgo** (pierde lento,
  no explota), NO como generador de edge. Config exacta del bot vivo (bandit+gate) = −4748.
- Conclusión: el límite es el **input/señal**, no el algoritmo.

## Análisis del regime_master (clasificador de régimen)

`regime_master.py` (= el Pine RegimeMaster) clasifica 10 regímenes con motor difuso +
capa k-NN. Se usa en el bot (contexto + `should_trade` + `implied_direction`) y se
muestra en el dashboard (panel de régimen).

| Script | Qué hace |
|---|---|
| `regime_discrimination.py` | Mide si cada régimen separa el outcome FUTURO (retorno, vol, acierto direccional) |
| `regime_reroute.py` | Compara short vs comprar-el-dip (long) en regímenes bajistas/rango |
| `regime_reroute_real.py` | Re-ruteo como estrategia realista (stop ATR + trailing + salir en caos) vs buy&hold |

**Hallazgos:**
- **La identificación de tendencia ALCISTA es buena** (índices D1: 62-67% de acierto direccional).
  Los regímenes alcistas predicen subida de verdad.
- **Los regímenes BAJISTAS NO predicen bajada** en activos con deriva alcista (oro, índices):
  un régimen "bajista" es seguido, en promedio, por un movimiento **al alza**. Shortearlos
  **pierde feo** (−83% oro, −85% US500). El `knn_edge` es más honesto que las etiquetas (marca ~0
  en bajistas). La sub-clasificación de **volatilidad discrimina flojo** (caos no es el más volátil).
- **RE-RUTEO**: en activos con deriva alcista, **comprar el dip (long) en regímenes bajistas/rango**
  bate a shortear (+71%/+84% vs −83%/−85%; RANGO-long +166%/+130%). La estrategia "todo long salvo
  caos" pasa de perder (−5%/−22%) a ganar (+280%/+198%). **El clasificador estaba bien; el routing
  (regPlay) estaba mal.**
- **Matiz clave**: el re-ruteo es **largo-sesgado** (cabalga la deriva alcista) y es la MISMA familia
  de edge que STF (trend-long) + RSI(2) (dip-buy), vista desde el régimen. Confirma, no agrega edge nuevo.
- **Prueba realista** (`regime_reroute_real.py`, stop 2.5ATR + trailing 3ATR + salir-en-caos, vs buy&hold):
  positivo y robusto (ret +144%/+149%/+223%, positivo 15/28, 17/21, 16/18 años en oro/US500/NAS100),
  PF 1.15-1.55. Es un **buy&hold des-riesgado**: captura ~10-15% del retorno bruto pero **recorta el DD ~10×**
  (oro −551%→−51%, NAS −396%→−33%). Risk-adjusted mejora en NAS100 (6.77 vs 4.49) y oro, pero **pierde
  vs buy&hold en US500** (5.24 vs 3.03). **Veredicto**: desplegable pero **no supera a RSI(2)** (PF 2-3);
  el valor del régimen es **filtro de riesgo (recorte de DD) + routing**, no estrategia estrella.

---

## Dead-end — Exponente de Hurst para régimen / brazo (DESCARTADO, 2026-07-29)

Scripts archivados en **`pruebas_fallidas/`** (`hurst_analysis.py` simple, `hurst_dfa.py` robusto).

Hipótesis: el Hurst separa persistencia (H>0.5 → momentum) de reversión (H<0.5) → mejoraría el régimen
o el ruteo del brazo. Se probó con 3 tests (redundancia vs ER/R², significado, filtro sobre RSI(2)) en
oro H4 + índices D1, con dos estimadores.
- El estimador **simple** (función de estructura) sale sesgado por el drift (H medio ~0.39, falso anti-persist).
- La **DFA** (detrended por escala) lo corrige: α medio se **centra en ~0.5** → los activos son **casi
  random-walk** a estas escalas; no hay memoria tipo-Hurst que cosechar.
- Significado: el retorno futuro **no se alinea** con los cubos de Hurst (|corr|<0.09, no monótono).
- Aplicación: el filtro sobre RSI(2) **no da edge robusto** — recorta >50% de trades, parte el retorno y el
  PF queda igual o peor (el PF 5.05 de NAS100 con DFA<0.45 son 16 trades = ruido).

**Veredicto**: no se integra. El `regime_master` ya captura la trendiness útil con ER + R² (corr con Hurst ≈0);
añadir Hurst mete ruido, no señal. Mismo muro: *el límite es la señal, no la herramienta.*

---

## Laboratorio intradía (M30) — research de papers (2026-07-29)

Infraestructura: `intraday_cache.py` baja M30 de los índices y lo apila a `data/intraday/<sym>_M30.csv`
(el bróker solo da ~4.2 años rodantes; el cache acumula más con el tiempo). Sesión cash EEUU 9:30–16:00 ET =
13 barras M30; **validado empíricamente** que servidor del bróker = ET+7h (pico de vol/volumen en 16:30 bróker).
Datos disponibles: M30 ~4.2a (útil), M15 ~2.1a, M5 ~0.7a, M1 ~50d (inútil). El scalping tick real necesita
L2/microestructura que NO tenemos → descartado.

| # | Estrategia | Fuente | Veredicto |
|---|---|---|---|
| 1 | Market Intraday Momentum (1ª media hora → última) | Gao, Han, Li, Zhou 2018 (JFE) | ❌ **NO replica** (dead-end, ver `pruebas_fallidas/`) — decaimiento post-pub + CFD sin subasta de cierre |
| 2 | Momentum intradía por bandas de ruido | Zarattini, Aziz, Barbon 2024 | ✅ **VALIDADO** (pasó walk-forward) — `intraday_breakout_zarattini.py` |
| 3 | Overnight vs intradía / drift | Lou-Polk-Skouras; NY Fed | pendiente (ojo swap en CFD) |
| 4 | Opening Range Breakout | Zarattini-Aziz 2023 | pendiente |

**#2 Zarattini (bandas de ruido)** — banda `Open*(1±N*Move(t))`, `Move(t)`=media 14d del |precio/open−1| por slot;
entra al romper banda en HH:00/HH:30, trailing por VWAP, plano al cierre (sin swap). Réplica M30 (conservadora, el
paper es 1-min). **Primer pase positivo y robusto**:
- US500 (N=1.0): ret +40%, Sharpe **1.06** (vs BH 0.76), DD −6.3% (vs −14.6%), PF 1.24, 4/5 años positivos.
- NAS100 (N=1.5): ret +37%, Sharpe **1.17** (vs BH 0.57), DD −6.4% (vs −21.9%), PF 1.27, **5/5 años positivos**.
- Todo el barrido de N es positivo (no es un N con suerte). **Sobrevive costos** (PF>1 hasta 5× spread).
- **Walk-forward (train 252d → test 63d, N out-of-sample): PASA.** OOS US500 Sharpe 0.81 / PF 1.20 / 3-4 años; NAS100
  Sharpe 0.81 / PF 1.21 / 3-4 años. Degradación normal (1.1→0.81), no colapso → edge real, no ajuste. US500 elige N=1.0
  estable; NAS100 N más disperso (a vigilar). 2026 flojo en ambos.
- **Extensión sesión EUROPEA (`intraday_eu_test.py`, 2026-07-31):** ¿el momentum intradía funciona en Europa? Sesión DAX/CAC
  detectada empíricamente (apertura 10:00 broker = 09:00 CET; ~17 slots M30 hasta 18:00). Resultado con el mismo walk-forward:
  - **GER40 (DAX): edge REAL** — OOS Sharpe **+0.60**, PF 1.15, **4/4 años positivos**, N=2.0 (selectivo, ~0.5 trades/día).
    Más débil que el US (0.60 vs 0.81) pero robusto. Mecanismo: el DAX recibe **doble catalizador** (apertura EU + apertura US a las 16:30 broker).
  - **FRA40 (CAC): SIN edge** — negativo en todo el barrido, WF Sharpe −0.14, 2/7 años → **descartado con datos**.
  - Diversificación temporal: sesión DAX ≈ **1-9 AM hora de México** (antes de la sesión US) → llenaría la mañana temprana.
  - **Pendiente**: soporte multi-sesión en `intraday_live` (hoy US-específico) — ver `MULTISESSION_DESIGN.md`. Agregar DAX cuando esté.
- **Demo-live**: `intraday_live.py` (ver comportamiento abajo). Opcional: cross-check M15, seguir acumulando M30 en el cache.

### Ejecutor demo-live #2 — `intraday_live.py` (comportamiento)

Coloca órdenes reales en la demo. Reutiliza el candado de cuenta (ensure+account_status),
kill switch y sizing por riesgo. Config en bloque `intraday` (magic **220003**, aislado de
bandit 123456 y RSI2 220002). Comportamiento por iteración (~cada 30s):

1. **Candado de cuenta** — si no es la demo Pepperstone (61566435), no opera.
2. **Ventana de sesión** — solo actúa dentro de la sesión cash US (13 barras M30). Fuera de
   sesión **fuerza plano** cualquier posición (seguridad EOD, nunca overnight → sin swap).
3. **Decisión por barra M30 cerrada** (una por slot, HH:00/HH:30). Calcula la banda
   `Open*(1±N*Move(t))` con `Move(t)` = media 14 días del |precio/open−1| en ese slot.
4. **Entrada** (slots 1..11, si plano): rompe banda superior → largo; inferior → corto.
   SL de desastre = banda opuesta; lote dimensionado a `risk_per_trade` (0.5%) sobre ese SL.
5. **Gestión** (si en posición): cierra por **VWAP trailing** (largo sale si close<VWAP;
   corto si close>VWAP); **flip** si rompe la banda opuesta (cierra y abre al revés).
6. **Cierre de sesión** (slot 12 = 15:30 ET): **plano forzado**, sin nuevas entradas.

N por símbolo (US500=1.0, NAS100=1.5, del walk-forward). Estado a `data/intraday_live_status.json`
(para el dashboard); bitácora de trades a `data/intraday_live_trades.csv` (= registro de su
comportamiento). Kill switch: `{"stop":true}` en `data/intraday_command.json`. `dry_run` en config
(recargable en caliente) alterna entre simular y órdenes reales.

---

## Smart Trend Follower — Familia B (VALIDADA en H4)

Estrategia trend-following event-driven: ruptura Donchian 55 + filtro EMA200 +
stop inicial 2.5×ATR + trailing Chandelier 3.0×ATR (trinquete, sin TP fijo) + flip +
riesgo 0.5%. Doc completa: `Downloads/Estrategia_Smart_Trend_Follower.md`.

| Script | Qué hace | Uso |
|---|---|---|
| `smart_trend_follower.py` | Backtest event-driven base + sub-períodos + robustez de params | `python smart_trend_follower.py [H1\|H4\|M15\|D1]` (default H1) |
| `stf_filter_h4.py` | Compara filtros de tendencia (ADX, pendiente EMA200) vs base | filtrar entradas **no** mejora (corta la cola ganadora) |
| `stf_multi.py` | Corre la estrategia en varios instrumentos (oro, BTC, WTI) | detecta símbolos del bróker automáticamente |
| `stf_portfolio.py` | Combina Oro + BTC en cartera (0.5% c/u), mide DD conjunto | diversificación 2013-2026 |

`backtest()` en `smart_trend_follower.py` acepta un `gate` opcional (array bool por
barra) para filtros de entrada. Todo es relativo a ATR → se auto-adapta a cualquier símbolo.

**Veredicto (XAUUSD + BTC):**
- **El timeframe era clave.** H1 pierde en el ciclo completo (whipsaw en rango, DD −40%).
  **H4 es robusto**: PF 1.25, DD −7.6% sobre 28 años; out-of-sample 2018-21 positivo;
  las 5 configs de params positivas. Reproduce la validación de la doc (2024-26, PF 1.30).
- **Filtrar entradas no ayuda** (confirma "tomar todas las señales": cortas los runners +9R).
- **BTC** tiene edge propio en H4 (+45R desde 2013; descartar datos pre-2013 rotos).
  **WTI** (perpetuo del bróker) no funciona. **Cartera Oro+BTC** duplica el retorno
  (+56R) con DD −11.4% (menos que aditivo), pero correlación anual +0.60 → diversificación
  **parcial**, no hedge.
- **Config de despliegue sugerida**: H4, sin filtro, cesta Oro+BTC, cuenta paciente.
- **Pendiente**: forward-test estricto en demo con la EA real (el laboratorio es backtest).

---

## RSI(2) Mean-Reversion — Familia C (VALIDADA en índices US, D1)

Reversión a la media de corto plazo (Connors): entrada `RSI(2) < 10` **y** `close > SMA(200)`;
salida `close > SMA(5)` / `RSI(2) > 70` / max_hold. Solo largos. Complementaria al STF
(otro mercado, otro régimen, correlación ~0).

| Script | Qué hace |
|---|---|
| `rsi2_meanrev.py` | Backtest D1 en US30/NAS100/US500/GER40 + robustez por año + params (entry 10/5) |
| `rsi2_costs.py` | Sensibilidad a costos (spread + swap overnight) por nivel de carry |

**Veredicto:**
- **Edge sólido en NAS100 y US500**: PF **2-3**, winrate **70-82%**, sobre **18-27 años**
  (incluye 2000, 2008, 2020, 2022). Positivo en la gran mayoría de años, DD **−6 a −12%**.
- `RSI2 < 5` (más estricto) → mejor PF y menos DD.
- **Sobrevive costos**: swap real del bróker ~0.007-0.02%/día (holds de 3-4 días); PF ~2 al
  carry real, robusto incluso a 3× ese nivel.
- **US30 poco concluyente** (solo 9 años) y **GER40 flojo** (PF ~1.1-1.4, DD −22%).
- **No le gana a buy&hold en retorno bruto** — es un overlay de **bajo drawdown**, no un reemplazo.

## Cartera combinada STF + RSI(2)

| Script | Qué hace |
|---|---|
| `combined_portfolio.py` | Combina STF (oro+BTC, H4) + RSI2 (US500+NAS100, D1), vol-targeting 10%, 50/50 |

**Veredicto (2004-2026, 22 años):** correlación diaria STF vs RSI2 = **+0.01** (independientes).
La cartera 50/50 **bate el Sharpe de ambas** (0.88 vs STF 0.39 / RSI2 0.85) y **baja el
drawdown a −16.1%** (vs −29% STF / −22% RSI2). Diversificación real: cada una gana cuando la
otra sufre (trend bleedea en rangos, MR bleedea en crashes).

## Forward-test en papel (observadores en vivo)

| Script | Qué hace |
|---|---|
| `rsi2_observer.py` | Señales RSI(2) de NAS100/US500 en papel (log-only), estado en `data/rsi2_paper_*` |
| `stf_observer.py` | Señales STF de oro/BTC H4 en papel (trailing stateful), `data/stf_paper_*` |

Se corren periódicamente (monitor cada hora). Registran entradas/salidas-papel a CSV para
comparar el **vivo vs backtest** — sin órdenes reales. Es el forward-test out-of-sample.

---

## Resumen de estrategias

| Familia | Estrategia | Mercado / TF | Veredicto |
|---|---|---|---|
| A | Bandit + 5 direccionales | oro M5/H1 | ❌ Sin edge |
| B | Smart Trend Follower | oro+BTC H4 | ✅ Trend robusto |
| C | RSI(2) Mean-Reversion | US500+NAS100 D1 | ✅ Reversión robusta |
| — | **Cartera B+C** | multi | ✅ **Diversificada (Sharpe 0.88, DD −16%)** |

## Infraestructura (aparte del laboratorio)
- `main_live_v2.py` — bot en vivo (recompensa diferida, riesgo 0.5%, guardarraíles).
- `dashboard.py` — dashboard Dash (`:8050`): equity, histograma de recompensas,
  métricas + `# Operations`, historial paginado, editor de config.
- Todo probado y funcional; corre en **cuenta demo**.

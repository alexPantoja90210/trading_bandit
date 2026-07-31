# Mapa de régimen → estrategia (RegimeMaster)

Referencia de `regime_master.py`: los 10 regímenes, cómo el clasificador llega a
cada uno, la estrategia sugerida (colores del Pine RegimeMaster) y cómo se
traduce a operar vía `policy.should_trade`.

## Cómo decide (pipeline)

```
OHLC → 10 features (0..1) → reglas difusas → 10 scores → capa k-NN → confianza + histéresis → régimen + código
```

Cada régimen sale de combinar **3 ejes** + 2 estados especiales:

| Eje | Features | Salidas |
|---|---|---|
| **Dirección** | balance DI±, pendiente MA, spread MA rápida-lenta, posición vs EMA200 | alcista / bajista / plano |
| **Estructura** | ADX, R², Efficiency Ratio | tendencia / rango |
| **Volatilidad** | percentil ATR, percentil ancho Bollinger | calma / normal / alta |

Los scores son productos de esos grados (p.ej. `ALCISTA_CALMADA = alcista × tendencia × calma`),
más **Caos** (vol alta + ineficiente + sin dirección + ADX bajo) y **Transición**
(pendiente que voltea + ADX cruzando umbral). La capa k-NN fusiona el score de reglas
(60%) con el voto de casos históricos parecidos (40%) y da el `knn_edge` (retorno futuro
medio en ATR de esos vecinos). La histéresis (`confirm_bars=2`, `min_conf_change`) evita el parpadeo.

**Código:** `code = id×10 + paso_de_confianza(1..10)`. Ej.: "40" = régimen 4 con confianza ~alta; "25" = régimen 2 con ~50%.

## Tabla

| id | Régimen | Familia | Firma (cómo llega) | Estrategia sugerida | Ejemplo de estrategia | Bandit habilita |
|---|---|---|---|---|---|---|
| 0 | ALCISTA_CALMADA | TREND_UP | alcista · tendencia · vol baja | Pullback alcista | Comprar retrocesos a la EMA20/50, trailing ceñido | trend, momentum |
| 4 | ALCISTA_NORMAL | TREND_UP | alcista · tendencia · vol normal | **Momentum / ruptura** | Ruptura de máximos de 55 → **STF largo**; momentum intradía **Zarattini** | trend, momentum |
| 3 | ALCISTA_VOLATIL | TREND_UP | alcista · tendencia · vol alta | Pullback profundo (riesgo) | Dip-buy **RSI(2)<10 en uptrend**, stop amplio, tamaño reducido | trend, momentum |
| 5 | BAJISTA_CALMADA | TREND_DOWN | bajista · tendencia · vol baja | Pullback bajista | Vender repuntes a la EMA20/50, trailing ceñido | trend, momentum |
| 6 | BAJISTA_NORMAL | TREND_DOWN | bajista · tendencia · vol normal | **Momentum bajista** | Ruptura de mínimos de 55 → **STF corto**; momentum bajista Zarattini | trend, momentum |
| 7 | BAJISTA_VOLATIL | TREND_DOWN | bajista · tendencia · vol alta | Pullback profundo (riesgo) | Short de repunte profundo, stop amplio, tamaño reducido | trend, momentum |
| 2 | RANGO_TRANQUILO | RANGE | sin dirección · dentro de bandas · vol baja/normal | **Reversión en bandas** | **RSI(2) reversión**: comprar banda inferior / vender superior de Bollinger | mean, flat, volatility |
| 8 | RANGO_VOLATIL | RANGE | sin dirección · vol alta | Reversión extrema | Fade de extremos (RSI2<5 / >95) con confirmación, tamaño reducido | mean, flat, volatility |
| 1 | CAOS_VOLATIL | NO_TRADE | vol alta · ineficiente (ER bajo) · ADX bajo | ⛔ Fuera del mercado | Sin trades; cerrar/reducir exposición y esperar a que baje la vol | — (no opera) |
| 9 | FASE_TRANSICION | NO_TRADE | pendiente voltea · ADX cruzando | ⏸ Sin entradas nuevas | No abrir; gestionar lo abierto, ajustar stops, esperar confirmación | — (no opera) |

Colores (Pine RegimeMaster): 0 `#00A05A` · 4 `#00C86E` · 3 `#78D23C` · 5 `#BE3C3C` · 6 `#DC2828` ·
7 `#FF5028` · 2 `#4682C8` · 8 `#966ED2` · 1 `#E6A014` · 9 `#969696`.

## El gate (`policy.should_trade`)

Antes de que el bandit ejecute:
1. **Confianza < 0.40 → no opera** (régimen incierto).
2. **NO_TRADE** (Caos / Transición) → no opera.
3. **TREND_UP / TREND_DOWN** → solo brazos **trend / momentum** (seguir tendencia).
4. **RANGE** → solo brazos **mean / flat / volatility** (reversión).

La familia decide *qué tipo* de brazo tiene permiso; el bandit elige *cuál* dentro de ese grupo.

> Nota: la lógica de la estrategia sugerida (`REGIME_PLAY`) vive en `dashboard.py`; la taxonomía
> (`REGIME_NAMES`, `FAMILY`) y las reglas en `regime_master.py`; el gate en `policy.py`.

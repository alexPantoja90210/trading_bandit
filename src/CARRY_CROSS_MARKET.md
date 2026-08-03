# Path B: carry cross-seccional multi-mercado (AQR) — 2026-08-03

Extensión de la tesis de la curva/carry más allá del VIX. AQR (Koijen-Moskowitz-Pedersen-Vrugt 2018,
"Carry"): el carry —lo que ganas si el precio no se mueve— es un premio que existe en TODA clase de
activo, y funciona MEJOR diversificado entre mercados (cada crash de carry es idiosincrático).
Script: `fx_carry.py`. Data externa cacheada en `data/carry/`.

## Pata 1: carry de DIVISAS (el más limpio con data gratis + desplegable en Pepperstone FX)
Señal = tasa interbancaria 3m (FRED/OECD) por divisa = el diferencial de interés (forward discount).
Cada mes: LARGO top-2 tasa / CORTO bottom-2, dollar-neutral. Retorno total = spot en USD + interés
devengado. Universo: EUR, GBP, JPY, AUD, CAD, CHF, NZD vs USD. Spot de yfinance.

**Resultado (ventana con data completa 2004-2026, 269 meses, ~2bps/pata):**
| | Sharpe | Anual | maxDD | Nulidad |
|---|---|---|---|---|
| FX carry naive | **+0.24** | +2.2% | **−36%** | **percentil 66% (FALLA)** |

OOS split: TRAIN +0.23 / TEST +0.30 (consistente, no colapsa). Cola de crash: **Lehman 2008-10 −11.8%**,
CHF unpeg 2015-01 −5.9%, COVID 2020-03 −5.7%. Por año: buenos años (2005 +2.72, 2009 +1.67, 2024 +1.09)
intercalados con el crash de carry clásico (2008 −28%).

**Veredicto pata FX:** NO clasifica solo. **No pasa el test de nulidad** (el rankeo por tasa apenas le gana
al azar) y el retorno (+2.2%/año) no compensa el DD de −36% con su cola de crash brutal. Es el premio de
carry FX **decaído post-2008** que documenta la literatura (gran corrida pre-2008, blow-up en 2008,
mediocre desde). Honesto: por sí solo no es edge desplegable con nuestro estándar.

## Lo IMPORTANTE: el carry DIVERSIFICADO entre mercados sí ayuda (tesis AQR, en miniatura)
Combiné la pata FX con el **VIX carry** ya validado (2 mercados de carry distintos), ventana común
2018-2026 (99 meses), cada uno escalado a vol común:
| | Sharpe | maxDD |
|---|---|---|
| FX carry solo | +0.38 | −10.2% |
| VIX carry solo | +0.39 | −9.4% |
| **50/50 carry basket** | **+0.47** | **−7.7%** |

corr(FX carry, VIX carry) = **+0.36** (moderada; ambos son primas de riesgo, co-mueven algo en risk-off).
**El basket mejora Sharpe Y baja DD respecto a cada carry solo** → diversificar carry entre mercados
FUNCIONA direccionalmente, tal como predice AQR. Ninguna pata sola es un home run, pero juntas se ayudan.

## Conclusión de Path B (honesta)
- El carry ES un premio cross-mercado real, pero **ninguna pata sola clasifica** con nuestro rigor. El
  VIX carry sigue siendo el carry individual más fuerte (pasó nulidad/OOS/mecanismo; la FX no pasa nulidad).
- **El valor está en la CESTA**: combinar carries poco correlacionados (VIX +0.36 FX) mejora el ratio.
  Para hacerlo real haría falta AÑADIR más mercados de carry (commodities vía curva de futuros, bonos)
  y gestionar el riesgo de cada uno (timing tipo "salir en backwardation" del VIX) — es un programa de
  investigación, no un edge de una sesión.
- **Bloqueo de data para extender:** el carry de commodities/bonos necesita la CURVA de futuros (front vs
  diferido), que no consigo limpia gratis (yfinance solo da front-month continuo; Pepperstone da spot/PERP,
  no futuros con vencimiento). Ese es el próximo cuello: una fuente de term structure multi-mercado.
- **Aplicación inmediata:** no desplegar FX carry solo. Mantener el VIX carry como el sleeve de carry, y
  tratar la pata FX como candidata para una futura CESTA de carry (cuando haya 3-4 mercados), no antes.

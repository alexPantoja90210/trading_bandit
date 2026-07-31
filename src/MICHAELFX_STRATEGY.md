# Estrategia MichaelFX — reglas (fuente: seminario, PDF)

Método **discrecional SMC** (Smart Money Concepts). Enfoque manual — el sistema NO la
ejecuta; le da CONTEXTO (cockpit) y MIDE los resultados (bitácora). Regla 20: leer esto
cada día antes de operar. Horario de referencia **UTC-5 (Perú/Ecuador)**.

## 1. DIRECCIÓN (sesgo)
**Macro:**
- Revisar tendencia y estructura en **Diario, 4H y 1H**.
- Marcar los **OB (order blocks)** más cercanos al precio (4H y 1H).
- 1H = temporalidad de referencia para micro.
- **Dirección del día anterior**: marcar máximo y mínimo del día previo (PDH/PDL) para
  continuaciones (excepto lunes).

**Micro:**
- Reconocer estructura de **15m y 5m** (apoyo de macro).
- Si el precio NO está en zonas macro (4H/1H) → marcar OB más cercanos **no mitigados** de 15m.

## 2. ESCENARIOS DE ENTRADA
Esperar reacción a los OB marcados (4H/1H/15m):
- **Escenario 1** — *Quiebre estructural del 80%* (3m/1m): descuento al **OB Ruptura** + potencial vacío (imbalance).
- **Escenario 2** — *Quiebre vertical del 80%* (3m/1m): descuento al **OB Break** del primer quiebre.
- **Escenario 3** — *Continuación* (3m): descuento al **OB Origen** + potencial vacío + liquidez.
- Si hace **2 quiebres del 80%** → reconocer OB activador → orden **buy/sell LIMIT**.
- Si hace **1 quiebre del 40%** → esperar comportamiento al OB activador → orden **buy/sell STOP**.
- Opcional (escenarios 1 y 3): confluencia **Fibonacci 61.8% y 75%**.

## 3. GESTIÓN DE CAPITAL
**Take Profit:** en Max/Min a neutralizar de 5-15min. Parciales en Max/Min anteriores o a la
mitad del TP (cerrar máx. 30% del lote).
**Stop Loss:** 2-4 pips del **OB activador**, de preferencia sobre/bajo la zona de 15m.
SL promedio EURUSD 5-10 pips · XAUUSD 15-30 pips. **Break Even** al romper con fuerza un
Max/Min anterior o en ratio 1:1. **No mover el SL** (solo para poner en BE).
**Ratios:** base **1:2.5** hasta **1:5** (según liquidez a neutralizar).

**Gestión de riesgo:**
- Personales ($100-500): 2-3%/trade (máx **2 ops/día**); semanal 10% (3 días de pérdida → fuera); mensual 20% (2 semanas negativas → fuera).
- Pruebas de fondeo — Conservador 0.5%/trade; Promedio 1%/trade (primeras 3 ops ratio 1:1; primer trade de semana 1:1 / 1:1.5; no cerrar parciales).
- Fondeadas ($5k-100k): 0.5-1%/trade (máx 2 ops/día); semanal 2.5%; mensual 5%; sí parciales. Si racha negativa pasa -5% de la máx. pérdida (-10%) → bajar a 0.25-0.5% hasta recuperar.

## 4. PLAN DE TRADING — horario UTC-5, **máx. 3 horas operativas**
- **London:** 1:30-4:30 am · **New York:** 7:30-10:30 am · **Tokio:** 6:30-9:30 pm

## LAS 20 REGLAS DE ORO
**Antes:** 1) Respetar todas las reglas. 2) Descansar 6-7h. 3) Hábitos previos. 4) No operar sin mente tranquila. 5) Calendario económico a la mano.
**Durante:** 6) No operar sin dirección clara del día. 7) No operar en noticia de alto impacto (operar después o usar Estrategia de Noticias). 8) Paciencia, esperar los escenarios. 9) Respetar horario. 10) No operar sin escenarios/OB activador claros. 11) Respetar gestión de riesgo por trade. 12) Riesgo máx/día 1% (lunes 0.5%). 13) No mover el SL (solo BE). 14) Cerrar si acumula cerca de la entrada +40 min. 15) Si un TP me saca, no opero más ese día. 16) No vengarme tras una perdedora.
**Después:** 17) Registrar todas las operaciones en la bitácora. 18) Anotar errores. 19) Conclusiones para mejorar entradas. 20) Backtesting visual de la sesión/semana.

---
*Soporte del sistema:* `michaelfx_cockpit.py` (contexto en vivo: sesgo D/4H/1H, PDH/PDL, sesión,
niveles, Fib, noticias) + `michaelfx_engine.py` (cálculos + bitácora). La bitácora mide expectancy
por escenario/sesión/cumplimiento-de-reglas para saber si TU discreción tiene edge (mismo juez que
el resto de la investigación).

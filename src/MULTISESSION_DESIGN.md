# Diseño — soporte multi-sesión para el ejecutor intradía

Estado: **diseño** (aún no implementado). Prerequisito para agregar el DAX (edge
validado, ver `README_LAB.md`) y futuras sesiones sin meter complejidad a medias.

## Motivación
El momentum intradía (Zarattini) está validado en:
- **US** (US500/NAS100/US30) — en producción demo-live.
- **EU/DAX** (GER40) — validado (OOS Sharpe 0.60, 4/4 años), pendiente de desplegar.
- CAC descartado; Asia sin probar.

Hoy `intraday_live.py` es **US-específico** (hardcode `ET_SHIFT_H=-7`, `RTH_SLOTS` en ET).
Hay que generalizarlo a N sesiones.

## Idea central: definir TODA sesión en hora del BRÓKER
El error a evitar es mapear cada región con su TZ (ET, CET, JST…). En su lugar:
**cada sesión = una lista de slots M30 en hora del bróker** (referencia única, sin conversiones).
- US: `16:30 → 22:30` bróker (= 9:30–15:30 ET), 13 slots.
- EU: `10:00 → 18:00` bróker (= 09:00–17:30 CET), 17 slots.
Los slots se **detectan empíricamente** (pico de vol/volumen M30 = apertura cash;
método ya usado y validado para US y EU).

## Config propuesta (`config.json` → `intraday`)
```json
"intraday": {
  "enabled": true, "dry_run": false, "lookback": 14,
  "risk_per_trade": 0.005, "magic": 220003, "sleep": 30,
  "sessions": {
    "US": { "slots": ["16:30","17:00", "...", "22:30"],
            "symbols": { "US500": 1.0, "NAS100": 1.5, "US30": 1.0 } },
    "EU": { "slots": ["10:00","10:30", "...", "18:00"],
            "symbols": { "GER40": 2.0 } }
  }
}
```
Un símbolo pertenece a **una** sesión (US500→US, GER40→EU) → el estado se puede seguir
indexando por símbolo (sin colisión).

## Refactor (3 archivos)
1. **`intraday_cache.py`**: quitar `ET_SHIFT_H`/`RTH_SLOTS` fijos. Nuevo helper
   `add_broker_time(df)` (solo `hm`/`date` en hora bróker) y que los slots sean un
   parámetro (no constante). La sesión los provee.
2. **`intraday_live.py`**: `compute_signals(sym, slots, N, lookback)` parametrizado por
   los slots de la sesión; `process()` itera **sesiones × símbolos**. `LAST_SLOT` = último
   slot de esa sesión (flat al cierre de SU sesión).
3. **`intraday_breakout_zarattini.py`**: `build_matrices(df, slots)` genérico (ya probado
   el patrón en `intraday_eu_test.py`, que usa slots EU). Unificar ambos.

## Consideraciones
- **Plano al cierre por sesión**: cada símbolo se aplana al último slot de SU sesión (no hay overnight).
- **Solapes de horario**: US (16:30–22:30 bróker) y EU (10:00–18:00) se solapan 16:30–18:00.
  Ok — cada símbolo opera en su propia sesión; el DAX en su ventana, los US en la suya.
- **Magic**: mismo magic 220003 (todas intradía) o uno por sesión si se quiere separar métricas.
- **Detección de slots**: script de utilidad que, dado un símbolo, imprima el perfil de vol/volumen
  M30 y proponga los slots (apertura→cierre). Congelar los slots en config tras validar.
- **Backtest ↔ vivo**: el backtest (`intraday_breakout_zarattini`/`intraday_eu_test`) y el vivo deben
  compartir la MISMA función `build_matrices(df, slots)` para que coincidan.

## Sesiones candidatas
| Sesión | Símbolos | Slots bróker | Estado |
|---|---|---|---|
| US | US500, NAS100, US30 | 16:30–22:30 | ✅ en vivo |
| EU | GER40 (N=2.0) | 10:00–18:00 | ✅ validado, pendiente desplegar |
| — | FRA40 | — | ❌ descartado (sin edge) |
| Asia | ? (JP225 no existe; HK50 no probado) | por detectar | sin probar |

## Plan de implementación (cuando toque)
1. Unificar `build_matrices(df, slots)` (backtest) + validar que reproduce US y EU.
2. Migrar config a `sessions`, refactor `compute_signals`/`process` en `intraday_live`.
3. Probar en dry-run que US sigue idéntico (no-regresión) + que EU/DAX opera en su ventana.
4. Desplegar DAX (N=2.0) en demo-live.
5. (Opcional) detectar+probar sesión asiática con el mismo laboratorio.

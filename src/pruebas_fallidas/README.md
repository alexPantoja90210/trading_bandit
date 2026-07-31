# Pruebas fallidas (dead-ends)

Ideas que se **probaron con rigor y NO aportaron edge**. Se archivan aquí (en vez
de borrarlas) para no volver a recorrer el mismo callejón y para poder re-verificar.
Cada script trae un bootstrap de `sys.path` → se puede correr desde esta carpeta:

```bash
python pruebas_fallidas/hurst_dfa.py
```

---

## Exponente de Hurst para régimen / brazo (2026-07-29)

**Hipótesis:** el Hurst distingue mercado persistente (H>0.5 → momentum) de
anti-persistente (H<0.5 → reversión), así que podría mejorar la detección de
régimen o rutear mejor el brazo del bandit (trend vs mean).

**Tests** (oro H4 28 años, US500/NAS100 D1 ~18-27 años):
1. **Redundancia** con las features que el `regime_master` ya tiene (ER, R²).
2. **Significado**: ¿el retorno futuro se alinea con los cubos de Hurst?
3. **Aplicación**: filtrar el RSI(2) por Hurst<umbral.

Dos estimadores:
- `hurst_analysis.py` — función de estructura (std de diferencias rezagadas).
  Sesgado por el drift (H medio ~0.39, falso "anti-persistente").
- `hurst_dfa.py` — **DFA** (Detrended Fluctuation Analysis), robusto: quita la
  tendencia local en cada escala. H medio se centra en ~0.5 (correcto).

**Resultado (con la DFA robusta):**
- Los activos son **casi random-walk** a estas escalas (α≈0.5) → no hay memoria
  tipo-Hurst que cosechar.
- Test [2]: el retorno futuro **no se alinea** con los cubos de α (|corr|<0.09,
  no monótono). La persistencia medida no predice el retorno.
- Test [3]: el filtro sobre RSI(2) **no da edge robusto** — recorta >50% de los
  trades, parte el retorno a la mitad y el PF queda igual o peor. (El `PF=5.05`
  de NAS100 con DFA<0.45 son 16 trades en 18 años = ruido.)

**Conclusión:** no se integra (ni simple ni DFA). Mismo muro del proyecto:
*el límite es la señal, no la herramienta.* El `regime_master` ya captura la
trendiness útil con ER + R²; añadir Hurst mete ruido, no señal.

---

## Market Intraday Momentum — Gao, Han, Li & Zhou 2018 JFE (2026-07-29)

**Hipótesis:** el retorno de la primera media hora predice el de la última
(momentum intradía). `intraday_momentum.py` lo replica en US500/NAS100 M30
(sesión cash reconstruida, servidor = ET+7h validado empíricamente).

**Resultado — NO replica en nuestros CFD (2022-2026):**
- Regresión `r_last ~ r_first`: beta **negativo** e insignificante (t≈−0.8),
  R²~0.06% (el paper: positivo, R²~1.6%).
- Todas las estrategias de timing pierden (Sharpe −0.5 a −0.8), 1/5 años positivos.
- Condicional (la afirmación fuerte del paper: días de primer movimiento grande +
  alto volumen): el beta **sigue negativo** — en NAS100 alto volumen es
  significativamente negativo (t=−2.75) → la última media hora **revierte** la
  primera, lo opuesto al momentum publicado.

**Por qué falla:** (1) **decaimiento post-publicación** (paper 1993-2013, nosotros
2022-2026); (2) **los CFD no tienen subasta de cierre** — el mecanismo del paper
(órdenes market-on-close del SPY) no existe en un CFD 24h.

**Nota:** lo único que asoma es **reversión** (no momentum) → coherente con que en
índices funcione el RSI(2). El cache de data M30 (`intraday_cache.py`) SÍ se
conserva en `src/` — es infraestructura reutilizable, no parte del dead-end.
El breakout intradía de Zarattini (mecanismo distinto, NO depende de la subasta)
sí pasó el primer filtro y sigue vivo en `src/intraday_breakout_zarattini.py`.

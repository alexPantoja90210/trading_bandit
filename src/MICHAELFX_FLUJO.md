# MichaelFX — Diagrama de flujo

Secuencia operativa completa, del sesgo a la bitácora, con sus guardas (las salidas rojas =
**no operar**; la disciplina es parte de la estrategia). Horario UTC-5, máx. 3 h operativas.
Fuente: *Reglas de la Estrategia MichaelFX* (seminario). Ver [[MICHAELFX_STRATEGY.md]] para el detalle.

```mermaid
flowchart TD
  START([Inicio del día]):::start
  START --> PRE{Pre-operativa listo?<br/>descanso · mente tranquila · calendario}:::dec
  PRE -->|falta algo| STOP1[No operar hoy]:::guard

  subgraph S1 [1 · DIRECCIÓN · sesgo]
    DIR[Tendencia y estructura<br/>Diario · 4H · 1H]:::fase
    DIR --> OBM[Marcar OB 4H/1H cercanos<br/>+ PDH/PDL del día previo]:::fase
  end
  PRE -->|listo| DIR
  OBM --> Q1{Dirección del día clara?}:::dec
  Q1 -->|No| STOP1
  Q1 -->|Sí| Q2{Precio en zona macro 4H/1H?}:::dec
  Q2 -->|No| OB15[Marcar OB 15m no mitigados]:::fase

  subgraph S2 [2 · ESPERAR · horario operativo]
    WAIT[Esperar reacción al OB<br/>London / NY / Tokio · máx 3h]:::fase
  end
  Q2 -->|Sí| WAIT
  OB15 --> WAIT
  WAIT --> Q3{Noticia de alto impacto?}:::dec
  Q3 -->|Sí| NEWS[Esperar u operar después]:::wait
  Q3 -->|No| Q4{Escenario + OB activador claros?}:::dec
  NEWS --> Q4
  Q4 -->|No| STOP1

  subgraph S3 [3 · ESCENARIO DE ENTRADA · micro 3m/1m]
    SCN{Tipo de quiebre}:::dec
    SCN -->|2 quiebres del 80%| LIMIT[Orden LIMIT al OB activador]:::entry
    SCN -->|1 quiebre del 40%| STOPO[Orden STOP al OB activador]:::entry
  end
  Q4 -->|Sí| SCN
  LIMIT --> FIB[Confluencia opcional · Fib 61.8% / 75%]:::fase
  STOPO --> FIB

  subgraph S4 [4 · GESTIÓN · al entrar]
    MGMT[SL 2-4 pips del OB activador<br/>TP a liquidez · Ratio 1:2.5 – 1:5<br/>Riesgo ≤1%/día lun 0.5% · máx 2 ops]:::fase
  end
  FIB --> MGMT

  subgraph S5 [5 · EN EL TRADE]
    TRADE[Break Even al romper Max/Min o 1:1<br/>No mover el SL · Parciales ≤30%]:::fase
  end
  MGMT --> TRADE
  TRADE --> EXIT{Salida}:::dec
  EXIT -->|+40min sin avanzar| CLOSE[Cerrar manual]:::wait
  EXIT -->|TP alcanzado| TP[No operar más hoy]:::entry
  EXIT -->|SL| SL[No vengarse]:::guard

  subgraph S6 [6 · DESPUÉS]
    AFTER[Bitácora · errores · conclusiones<br/>Backtesting visual de la sesión]:::fase
  end
  CLOSE --> AFTER
  TP --> AFTER
  SL --> AFTER
  AFTER --> END([Fin de la sesión]):::start

  classDef start fill:#1e222d,stroke:#787b86,color:#d1d4dc;
  classDef fase fill:#1b2434,stroke:#2f4f74,color:#cdd6e4;
  classDef dec fill:#241f16,stroke:#f5a623,color:#f3d7a3;
  classDef entry fill:#12312c,stroke:#26a69a,color:#8fd8cc;
  classDef guard fill:#2c1a1c,stroke:#ef5350,color:#f0a6a4;
  classDef wait fill:#2a2512,stroke:#c98a1e,color:#e6c98a;
```

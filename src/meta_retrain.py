"""
meta_retrain.py — bucle de AUTO-REENTRENAMIENTO del meta-modelo (paso b).

El self-improvement de AlphaZero aplicado: a medida que llega nueva data de mercado,
reconstruye el meta_dataset (histórico, sin lookahead) y reentrena el meta-modelo →
`meta_model.json` actualizado. El `meta_observer` recarga ese modelo en cada pasada,
así el forward-test siempre usa la versión más reciente. Cierra el bucle:
   nuevos resultados → reentrenar → mejor modelo → aplicado en vivo → nuevos resultados.

Uso:
  python meta_retrain.py --once     # una corrida (para un scheduler externo)
  python meta_retrain.py            # loop persistente (reentrena cada `interval_days`)
"""
import sys
import time
from datetime import datetime, timezone

import build_meta_dataset
import train_meta_model

INTERVAL_DAYS = 7


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def retrain_once():
    print(f"\n===== [retrain {_now()}] reconstruyendo meta_dataset (histórico) =====")
    build_meta_dataset.main()
    print(f"\n===== [retrain {_now()}] reentrenando + guardando meta_model.json =====")
    train_meta_model.main()
    print(f"===== [retrain {_now()}] listo — modelo actualizado =====")


def main():
    once = "--once" in sys.argv
    while True:
        try:
            retrain_once()
        except Exception as e:
            print(f"[retrain] error: {e}")
        if once:
            break
        time.sleep(INTERVAL_DAYS * 24 * 3600)


if __name__ == "__main__":
    main()

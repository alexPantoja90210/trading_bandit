import json
from datetime import datetime
import logging
from pythonjsonlogger import jsonlogger
from logging.handlers import RotatingFileHandler

from paths import LOG_FILE  # ruta central, independiente del cwd


def setup_logger(name, filename):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(filename, maxBytes=5_000_000, backupCount=5)
    fmt = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(message)s')
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger


def log_event(event, data=None):
    """Agrega un evento en formato JSON-lines a logs.jsonl.

    Formato: {"timestamp": ISO8601, "event": <str>, "data": {...}}
    Nunca lanza excepción: el logging no debe tumbar el loop de trading.
    """
    record = {
        "timestamp": datetime.now().isoformat(),
        "event": event,
        "data": data if data is not None else {},
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Como último recurso, no interrumpir la ejecución por un fallo de log.
        pass

"""
Rutas centralizadas del proyecto.

Todas las rutas se derivan de la ubicación de ESTE archivo (src/), así que el
bot y el dashboard leen/escriben en los mismos sitios sin importar desde qué
directorio se lancen (cwd-independiente).
"""
import os

# Carpeta src/ (donde vive este archivo)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Config
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Carpeta de datos que consume el dashboard
DATA_DIR = os.path.join(BASE_DIR, "data")

# Archivos de datos
EQUITY_CSV = os.path.join(DATA_DIR, "equity.csv")
REWARDS_CSV = os.path.join(DATA_DIR, "rewards.csv")
BANDIT_STATE = os.path.join(DATA_DIR, "bandit_state.json")
# Dataset de entrenamiento: condiciones (contexto + régimen) → recompensa realizada
LEARNING_CSV = os.path.join(DATA_DIR, "learning_dataset.csv")

# Estado en vivo del bot (contadores) que consume el dashboard
STATUS_FILE = os.path.join(DATA_DIR, "status.json")

# Comandos del dashboard hacia el bot (ej. reset manual del contador)
COMMAND_FILE = os.path.join(DATA_DIR, "command.json")

# Cola de decisiones pendientes (recompensa diferida) — persiste entre paros/reinicios
PENDING_FILE = os.path.join(DATA_DIR, "pending.json")

# Log de eventos (JSON-lines)
LOG_FILE = os.path.join(BASE_DIR, "logs.jsonl")

# Asegurar que la carpeta de datos exista siempre
os.makedirs(DATA_DIR, exist_ok=True)


def load_config():
    """Carga config.json de forma robusta (ruta absoluta)."""
    import json
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)

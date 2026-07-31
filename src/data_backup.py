"""
data_backup.py — respalda los DATOS vivos (src/data/) a OneDrive.

Git respalda el código; esto respalda lo IRREMPLAZABLE: el dataset de aprendizaje
que se acumula, meta_dataset, los CSV de trades, meta_forward, estados. Hace un
ZIP DIARIO (comprimido, con historial) en OneDrive, que se sincroniza y versiona solo.

Uso:  python data_backup.py --once   (un respaldo)
      python data_backup.py          (loop: respalda cada INTERVAL_H horas)
"""
import os
import sys
import time
import zipfile
from datetime import datetime

from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEST = r"C:\Users\tojap\OneDrive\Backups\trading_bandit"
INTERVAL_H = 6


def backup_once():
    os.makedirs(DEST, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d")
    zippath = os.path.join(DEST, f"data_{date}.zip")   # uno por día (se reescribe en el día)
    tmp = zippath + ".tmp"
    n = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(DATA_DIR):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    z.write(fp, os.path.relpath(fp, DATA_DIR))
                    n += 1
                except Exception:
                    pass                                # archivo en escritura → saltar
    os.replace(tmp, zippath)
    mb = os.path.getsize(zippath) / 1e6
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] backup {n} archivos -> {zippath} ({mb:.1f}MB)")


def main():
    once = "--once" in sys.argv
    while True:
        try:
            backup_once()
        except Exception as e:
            print(f"[backup] error: {e}")
        if once:
            break
        time.sleep(INTERVAL_H * 3600)


if __name__ == "__main__":
    main()

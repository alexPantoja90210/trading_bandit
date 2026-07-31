"""
train_from_dataset.py — entrena un modelo BASE sobre `data/learning_dataset.csv`
(condiciones → recompensa) y reporta si el CONTEXTO predice la RECOMPENSA.

Es el juez de si vale la pena una "versión mejorada" que aprenda del histórico de
recompensas: si el contexto NO predice el reward mejor que el baseline (la media),
no hay señal que aprender — coherente con la conclusión de que el bandit no tiene edge.

Sin dependencias más allá de numpy/pandas (NO sklearn). Ridge por forma cerrada,
split TEMPORAL (sin shuffle → sin fuga de datos), comparado contra el baseline.

Uso:  python train_from_dataset.py
"""
import sys
import numpy as np
import pandas as pd

from paths import LEARNING_CSV

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MIN_ROWS = 60          # muestra mínima para un split con sentido


def ridge_fit(X, y, lam=1.0):
    p = X.shape[1]
    return np.linalg.solve(X.T @ X + lam * np.eye(p), X.T @ y)


def main():
    try:
        df = pd.read_csv(LEARNING_CSV)
    except Exception:
        print(f"Aún no existe {LEARNING_CSV} — el dataset se llena al madurar "
              f"decisiones (~horizonte de recompensa). Vuelve cuando haya datos.")
        return

    n = len(df)
    print(f"Dataset: {n} filas  ({df['time'].min():.0f}..{df['time'].max():.0f})" if n else "vacío")
    if n < MIN_ROWS:
        print(f"Muestra insuficiente ({n} < {MIN_ROWS}). El bandit está en solo-aprende "
              f"acumulando; deja correr días/semanas y reintenta.")
        return

    ctx_cols = [c for c in df.columns if c.startswith("ctx_")]
    df = df.dropna(subset=ctx_cols + ["reward"]).reset_index(drop=True)

    # cobertura por símbolo (multi-par)
    print("\n[Cobertura por par]")
    for sym, cnt in df["symbol"].value_counts().items():
        print(f"  {sym:<8} {cnt} filas")

    # NORMALIZACIÓN POR SÍMBOLO — clave para multi-par: close/sma20/volume son
    # niveles crudos no comparables entre oro y NAS; el z-score dentro de cada
    # símbolo los pone en la misma escala (los demás features ya son scale-free).
    Xdf = df[ctx_cols].copy()
    for sym in df["symbol"].unique():
        m = df["symbol"] == sym
        sub = Xdf.loc[m]
        Xdf.loc[m] = (sub - sub.mean()) / (sub.std().replace(0, 1e-9))
    Xctx = Xdf.fillna(0.0).values.astype(float)

    y = df["reward"].values.astype(float)
    arms = pd.get_dummies(df["arm"], prefix="arm").values.astype(float)  # one-hot del brazo
    X = np.hstack([Xctx, arms])

    # split temporal (respeta el orden: entrena con el pasado, prueba con el futuro)
    cut = int(n * 0.7)
    Xtr, Xte, ytr, yte = X[:cut], X[cut:], y[:cut], y[cut:]

    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    Xtr = np.hstack([np.ones((len(Xtr), 1)), (Xtr - mu) / sd])
    Xte = np.hstack([np.ones((len(Xte), 1)), (Xte - mu) / sd])

    w = ridge_fit(Xtr, ytr, lam=1.0)
    pred = Xte @ w
    base = ytr.mean()

    ss_res = ((yte - pred) ** 2).sum()
    ss_tot = ((yte - base) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mae, mae_base = np.abs(yte - pred).mean(), np.abs(yte - base).mean()
    dir_acc = (np.sign(pred) == np.sign(yte)).mean()
    base_rate = max((yte > 0).mean(), (yte <= 0).mean())

    print(f"\n[MODELO] Ridge sobre contexto+brazo  (train {cut} / test {n-cut}, split temporal)")
    print(f"  R² (test):            {r2:+.3f}   (>0 = el contexto predice mejor que la media)")
    print(f"  MAE modelo vs media:  {mae:.3f} vs {mae_base:.3f}   ({'mejora' if mae < mae_base else 'no mejora'})")
    print(f"  Acierto direccional:  {dir_acc*100:.1f}%   vs base {base_rate*100:.1f}%   "
          f"({'señal' if dir_acc > base_rate + 0.03 else 'sin señal clara'})")

    verdict = ("HAY señal aprendible: el contexto predice el reward por encima del azar."
               if (r2 > 0.02 and dir_acc > base_rate + 0.03) else
               "SIN señal robusta: el contexto no predice el reward mejor que la media "
               "(coherente con 'el límite es la señal, no el modelo').")
    print(f"\n[VEREDICTO] {verdict}")

    # --- interpretable: dónde le fue mejor a la versión anterior ---
    print("\n[Recompensa media por familia de régimen]")
    if "family" in df.columns:
        g = df.groupby("family")["reward"].agg(["mean", "count"]).sort_values("mean", ascending=False)
        for fam, row in g.iterrows():
            print(f"  {str(fam):<12} media={row['mean']:+.3f}  n={int(row['count'])}")
    print("\n[Recompensa media por brazo]")
    g = df.groupby("arm_name")["reward"].agg(["mean", "count"]).sort_values("mean", ascending=False)
    for arm, row in g.iterrows():
        print(f"  {str(arm):<12} media={row['mean']:+.3f}  n={int(row['count'])}")


if __name__ == "__main__":
    main()

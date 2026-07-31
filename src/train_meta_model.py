"""
train_meta_model.py — META-MODELO estilo AlphaZero sobre meta_dataset.csv.

Idea (aplicada, no ingenua): NO predecir el precio desde cero, sino aprender de
RESULTADOS `E[reward | contexto, edge]` (el "value network") y usarlo para ASIGNAR
entre edges reales — tomar/sobreponderar las apuestas que el valor predice buenas,
cortar las malas (típicamente las de STF en TREND_DOWN).

Juez: WALK-FORWARD (entrena con el pasado, decide sobre el futuro) comparando la
asignación aprendida contra el **1/N** (tomar todas las apuestas por igual). Si el
meta-modelo bate al 1/N out-of-sample en reward/Sharpe → hay meta-alpha.

Sin sklearn: Ridge por forma cerrada, normalización por símbolo (multi-par).

Uso:  python train_meta_model.py
"""
import os
import sys

import numpy as np
import pandas as pd

from paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

META = os.path.join(DATA_DIR, "meta_dataset.csv")
FOLDS = 4
LAM = 8.0


def ridge_fit(X, y, lam=LAM):
    p = X.shape[1]
    return np.linalg.solve(X.T @ X + lam * np.eye(p), X.T @ y)


def sharpe(r):
    r = np.asarray(r, float)
    return r.mean() / r.std() if len(r) > 1 and r.std() > 0 else 0.0


def main():
    try:
        d = pd.read_csv(META)
    except Exception:
        print(f"No existe {META}. Corre build_meta_dataset.py primero."); return
    d = d.sort_values("time").reset_index(drop=True)
    n = len(d)
    print(f"meta_dataset: {n} filas · edges {sorted(d['edge'].unique())} · "
          f"pares {sorted(d['symbol'].unique())}")

    # Se EXCLUYEN las features de nivel crudo (close, volume, sma20 = ctx_0/1/3): no
    # son comparables entre pares y no predicen. El resto ya es scale-free → el modelo
    # es PORTABLE (estandarización global, aplicable en vivo sin stats por símbolo).
    RAW = {"ctx_0", "ctx_1", "ctx_3"}
    ctx = [c for c in d.columns if c.startswith("ctx_") and c not in RAW]
    edges = sorted(d["edge"].unique())
    edge_oh = pd.DataFrame({f"is_{e}": (d["edge"] == e).astype(float) for e in edges})
    X = np.hstack([d[ctx].values.astype(float), edge_oh.values])
    y = d["reward"].values.astype(float)

    idx = np.array_split(np.arange(n), FOLDS)
    base_all, meta_all, wt_all, wt_w = [], [], [], []
    dir_hit, dir_n = 0, 0
    print("\nWALK-FORWARD (train pasado → decide futuro):")
    for k in range(1, FOLDS):
        tr = np.concatenate(idx[:k]); te = idx[k]
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        Xtr = np.hstack([np.ones((len(tr), 1)), (X[tr] - mu) / sd])
        Xte = np.hstack([np.ones((len(te), 1)), (X[te] - mu) / sd])
        w = ridge_fit(Xtr, y[tr])
        pred = Xte @ w
        yte = y[te]
        take = pred > 0                                       # asignador: filtro
        weight = np.clip(pred, 0, None)                       # asignador: sobrepeso ∝ valor
        base_all += list(yte)
        meta_all += list(yte[take])
        if weight.sum() > 0:
            wt_all.append(float((weight * yte).sum() / weight.sum()))
            wt_w.append(len(te))
        dir_hit += int((np.sign(pred) == np.sign(yte)).sum()); dir_n += len(te)
        print(f"  fold {k}: test={len(te):>4}  1/N mean={yte.mean():+.3f} sh={sharpe(yte):+.2f}"
              f"  |  META(pred>0) n={int(take.sum()):>4} mean={yte[take].mean() if take.sum() else 0:+.3f} "
              f"sh={sharpe(yte[take]):+.2f}")

    base = np.array(base_all); meta = np.array(meta_all)
    wmean = np.average(wt_all, weights=wt_w) if wt_all else 0.0
    print("\n=== OOS agregado ===")
    print(f"  1/N (todas las apuestas):  n={len(base):>4}  mean={base.mean():+.3f} ATR  sharpe={sharpe(base):+.2f}")
    print(f"  META filtro (pred>0):      n={len(meta):>4}  mean={meta.mean():+.3f} ATR  sharpe={sharpe(meta):+.2f}")
    print(f"  META sobrepeso (∝ valor):        mean_ponderado={wmean:+.3f} ATR")
    print(f"  acierto direccional del value: {dir_hit/dir_n*100:.1f}% ({dir_n} apuestas OOS)")

    beats = meta.mean() > base.mean() and sharpe(meta) > sharpe(base)
    print(f"\n[VEREDICTO] " + (
        "META SUPERA al 1/N out-of-sample → hay meta-alpha: el value aprende a cortar "
        "las apuestas malas por régimen (típico: STF en TREND_DOWN)."
        if beats else
        "META no supera al 1/N de forma robusta → el 1/N ya es un baseline duro; el "
        "value no añade selección fiable (coherente con señal modesta y no-estacionaria)."))

    # interpretable: lo que el modelo aprendió por régimen (E[reward] predicho por familia×edge)
    print("\n[Lo que el value predice — reward medio por edge×familia, OOS del último fold]")
    te = idx[-1]
    mu, sd = X[np.concatenate(idx[:-1])].mean(0), X[np.concatenate(idx[:-1])].std(0) + 1e-9
    w = ridge_fit(np.hstack([np.ones((n - len(te), 1)),
                             (X[np.concatenate(idx[:-1])] - mu) / sd]), y[np.concatenate(idx[:-1])])
    predte = np.hstack([np.ones((len(te), 1)), (X[te] - mu) / sd]) @ w
    dt = d.iloc[te].copy(); dt["pred"] = predte
    g = dt.groupby(["edge", "family"])["pred"].mean().round(3)
    for (edge, fam), v in g.items():
        print(f"  {edge:5} {fam:<11} pred={v:+.3f}")

    # --- guardar el modelo FINAL (entrenado con todo) para el observador live ---
    import json
    muA, sdA = X.mean(0), X.std(0) + 1e-9
    XA = np.hstack([np.ones((n, 1)), (X - muA) / sdA])
    wf = ridge_fit(XA, y)
    with open(os.path.join(DATA_DIR, "meta_model.json"), "w", encoding="utf-8") as f:
        json.dump({"weights": wf.tolist(), "mu": muA.tolist(), "sd": sdA.tolist(),
                   "ctx_cols": ctx, "edges": edges}, f)
    print(f"\nmodelo → data/meta_model.json  ({len(ctx)} ctx + {len(edges)} edges)")


if __name__ == "__main__":
    main()

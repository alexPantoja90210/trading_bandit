"""
reg_manifold_test.py — aplicabilidad de Quadratic Regularization y Latent Manifold Learning.

[A] QUADRATIC REGULARIZATION (L2/Ridge, ya en uso en el meta): barrido de LAM en el
    walk-forward del meta-modelo. ¿Está bien calibrado? ¿Más shrinkage ayuda (tiende a 1/N)?
[B] LATENT MANIFOLD LEARNING: ¿hay estructura NO LINEAL en el contexto que el meta lineal
    (Ridge) se pierde? Test: predecir E[reward|contexto,edge] con kNN (método LOCAL/manifold)
    y con kNN sobre una proyección PCA (manifold lineal comprimido), vs Ridge. Si kNN no bate
    a Ridge OOS → no hay manifold explotable (la señal no es no-lineal, es que casi no hay señal).
Reusa meta_dataset. Solo LEE.
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


def ridge_fit(X, y, lam):
    p = X.shape[1]
    return np.linalg.solve(X.T @ X + lam * np.eye(p), X.T @ y)


def sharpe(r):
    r = np.asarray(r, float)
    return r.mean() / r.std() if len(r) > 1 and r.std() > 0 else 0.0


def load_xy():
    d = pd.read_csv(META).sort_values("time").reset_index(drop=True)
    RAW = {"ctx_0", "ctx_1", "ctx_3"}
    ctx = [c for c in d.columns if c.startswith("ctx_") and c not in RAW]
    edges = sorted(d["edge"].unique())
    eoh = np.column_stack([(d["edge"] == e).astype(float) for e in edges])
    X = np.hstack([d[ctx].values.astype(float), eoh])
    y = d["reward"].values.astype(float)
    return X, y, ctx, edges


def eval_pred(pred, yte, acc):
    take = pred > 0
    acc["base"] += list(yte)
    acc["meta"] += list(yte[take])
    acc["dir_hit"] += int((np.sign(pred) == np.sign(yte)).sum()); acc["dir_n"] += len(yte)


def knn_predict(Xtr, ytr, Xte, k=50, sub=4000):
    """kNN regresión (manifold local). Subsamplea train para velocidad. Vectorizado."""
    if len(Xtr) > sub:
        idx = np.linspace(0, len(Xtr) - 1, sub).astype(int)
        Xtr, ytr = Xtr[idx], ytr[idx]
    out = np.zeros(len(Xte))
    B = 500
    for i in range(0, len(Xte), B):
        xb = Xte[i:i + B]
        d2 = ((xb[:, None, :] - Xtr[None, :, :]) ** 2).sum(2)
        nn = np.argpartition(d2, k, axis=1)[:, :k]
        out[i:i + B] = ytr[nn].mean(1)
    return out


def pca_reduce(Xtr, Xte, dim):
    mu = Xtr.mean(0)
    U, S, Vt = np.linalg.svd(Xtr - mu, full_matrices=False)
    P = Vt[:dim].T
    return (Xtr - mu) @ P, (Xte - mu) @ P


def walk(X, y, method, lam=8.0, pca_dim=None):
    n = len(y); idx = np.array_split(np.arange(n), FOLDS)
    acc = {"base": [], "meta": [], "dir_hit": 0, "dir_n": 0}
    for k in range(1, FOLDS):
        tr = np.concatenate(idx[:k]); te = idx[k]
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        Xtr, Xte = (X[tr] - mu) / sd, (X[te] - mu) / sd
        if method == "ridge":
            Xtr1 = np.hstack([np.ones((len(tr), 1)), Xtr]); Xte1 = np.hstack([np.ones((len(te), 1)), Xte])
            w = ridge_fit(Xtr1, y[tr], lam); pred = Xte1 @ w
        else:  # knn (opcional sobre PCA = manifold comprimido)
            if pca_dim:
                Xtr, Xte = pca_reduce(Xtr, Xte, pca_dim)
            pred = knn_predict(Xtr, y[tr], Xte)
        eval_pred(pred, y[te], acc)
    base, meta = np.array(acc["base"]), np.array(acc["meta"])
    return dict(meta_mean=meta.mean(), meta_sh=sharpe(meta), n=len(meta),
                dir=acc["dir_hit"] / acc["dir_n"] * 100, base_mean=base.mean())


def main():
    X, y, ctx, edges = load_xy()
    print(f"meta_dataset: {len(y)} filas · {len(ctx)} ctx + {len(edges)} edges")
    print(f"1/N baseline (todas): mean={y.mean():+.3f}")

    print("\n[A] QUADRATIC REGULARIZATION — barrido de LAM (Ridge walk-forward):")
    print(f"    {'LAM':>7}{'META mean':>11}{'META sharpe':>13}{'dir%':>8}{'n_take':>8}")
    for lam in [0.5, 2, 8, 32, 128, 512]:
        r = walk(X, y, "ridge", lam=lam)
        print(f"    {lam:>7}{r['meta_mean']:>+11.3f}{r['meta_sh']:>+13.3f}{r['dir']:>8.1f}{r['n']:>8}")

    print("\n[B] LATENT MANIFOLD — kNN (local/no-lineal) vs Ridge (lineal):")
    rr = walk(X, y, "ridge", lam=8.0)
    print(f"    {'Ridge (lineal)':<24} mean={rr['meta_mean']:+.3f}  sharpe={rr['meta_sh']:+.3f}  "
          f"dir={rr['dir']:.1f}%  n={rr['n']}")
    rk = walk(X, y, "knn")
    print(f"    {'kNN full (manifold)':<24} mean={rk['meta_mean']:+.3f}  sharpe={rk['meta_sh']:+.3f}  "
          f"dir={rk['dir']:.1f}%  n={rk['n']}")
    for dim in [3, 5, 8]:
        rp = walk(X, y, "knn", pca_dim=dim)
        print(f"    kNN sobre PCA-{dim}D{'':<11} mean={rp['meta_mean']:+.3f}  sharpe={rp['meta_sh']:+.3f}  "
              f"dir={rp['dir']:.1f}%  n={rp['n']}")

    print("\n[C] ¿Cuánta señal es lineal? varianza explicada por PCA del contexto:")
    Xc = (X[:, :len(ctx)] - X[:, :len(ctx)].mean(0)) / (X[:, :len(ctx)].std(0) + 1e-9)
    _, S, _ = np.linalg.svd(Xc - Xc.mean(0), full_matrices=False)
    ev = (S ** 2) / (S ** 2).sum()
    print(f"    top-3 dims: {ev[:3].sum()*100:.0f}%  top-5: {ev[:5].sum()*100:.0f}%  "
          f"top-8: {ev[:8].sum()*100:.0f}%  (de {len(ctx)} features de contexto)")


if __name__ == "__main__":
    main()

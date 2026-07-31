"""
Bandit contextual bayesiano — Linear Thompson Sampling (LinTS).

Por cada brazo mantiene una regresión lineal bayesiana sobre el contexto:
    A[brazo] = lam*I + Σ z z^T      (información acumulada)
    b[brazo] = Σ reward * z
    theta_hat = A^-1 b              (mejor estimación de los pesos)

Elección (Thompson): muestrea theta ~ N(theta_hat, v^2 A^-1) por brazo y toma
el de mayor z·theta. Donde hay pocos datos → más incertidumbre → más explora.

El contexto se estandariza (z-score) con un scaler FIJO (media/desv de los
datos de entrenamiento) para que A esté bien condicionada. El mismo scaler debe
usarse en vivo → se guarda junto al estado.
"""
import json
import numpy as np


class LinTSBandit:
    def __init__(self, n_features, n_arms, v=0.2, lam=1.0, gamma=1.0):
        self.d = int(n_features)
        self.n_arms = int(n_arms)
        self.v = float(v)          # escala de exploración (posterior)
        self.lam = float(lam)      # regularización
        self.gamma = float(gamma)  # factor de olvido (<1 = memoria corta)
        self.A = np.array([np.eye(self.d) * self.lam for _ in range(self.n_arms)])
        self.b = np.zeros((self.n_arms, self.d))
        self.feat_mean = np.zeros(self.d)
        self.feat_std = np.ones(self.d)
        self._Ainv = None          # cache de inversas

    # ---- scaler ----
    def set_scaler(self, mean, std):
        self.feat_mean = np.asarray(mean, dtype=float)
        std = np.asarray(std, dtype=float).copy()
        std[std < 1e-8] = 1.0
        self.feat_std = std

    def _z(self, x):
        x = np.nan_to_num(np.asarray(x, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
        return (x - self.feat_mean) / self.feat_std

    # ---- aprendizaje ----
    def update(self, arm, x, reward):
        z = self._z(x)
        if self.gamma < 1.0:
            # Descuento: olvida datos viejos, prioriza el régimen reciente.
            self.A[arm] = (self.gamma * self.A[arm]
                           + (1.0 - self.gamma) * self.lam * np.eye(self.d)
                           + np.outer(z, z))
            self.b[arm] = self.gamma * self.b[arm] + float(reward) * z
        else:
            self.A[arm] += np.outer(z, z)
            self.b[arm] += float(reward) * z
        self._Ainv = None  # invalidar cache

    def _ensure_inv(self):
        if self._Ainv is None:
            self._Ainv = np.array([np.linalg.inv(self.A[a]) for a in range(self.n_arms)])

    def theta_hat(self, arm):
        self._ensure_inv()
        return self._Ainv[arm] @ self.b[arm]

    # ---- inferencia ----
    def expected_scores(self, x):
        """Puntaje esperado (greedy, sin ruido) por brazo. Para backtest/inspección."""
        self._ensure_inv()
        z = self._z(x)
        return np.array([float(z @ (self._Ainv[a] @ self.b[a])) for a in range(self.n_arms)])

    def select_arm(self, x):
        """Elección Thompson: muestrea theta por brazo y toma el mejor puntaje.

        Usa la diagonal de la posterior (A^-1) para el ruido → numéricamente
        robusto (sin SVD/cholesky) y con la misma exploración por peso.
        """
        self._ensure_inv()
        z = self._z(x)
        best, best_score = 0, -np.inf
        for a in range(self.n_arms):
            Ainv = self._Ainv[a]
            mu = Ainv @ self.b[a]
            sd = self.v * np.sqrt(np.clip(np.diag(Ainv), 0.0, None))
            theta = mu + sd * np.random.standard_normal(self.d)
            score = float(z @ theta)
            if score > best_score:
                best_score, best = score, a
        return best

    # ---- persistencia ----
    def save(self, path):
        state = {
            "d": self.d, "n_arms": self.n_arms, "v": self.v, "lam": self.lam,
            "A": self.A.tolist(), "b": self.b.tolist(),
            "feat_mean": self.feat_mean.tolist(), "feat_std": self.feat_std.tolist(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        bandit = cls(s["d"], s["n_arms"], v=s["v"], lam=s["lam"])
        bandit.A = np.asarray(s["A"], dtype=float)
        bandit.b = np.asarray(s["b"], dtype=float)
        bandit.feat_mean = np.asarray(s["feat_mean"], dtype=float)
        bandit.feat_std = np.asarray(s["feat_std"], dtype=float)
        return bandit

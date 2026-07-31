import numpy as np


class ContextualBanditTS:
    def __init__(self, n_features, n_arms, lr=0.01, ema=0.01, clip=5.0):
        self.n_features = n_features
        self.n_arms = n_arms
        self.lr = lr          # tasa de aprendizaje de theta
        self.ema = ema        # tasa de actualización de la normalización online
        self.clip = clip      # recorte del z-score para frenar outliers

        # Parámetros de la distribución normal para cada arm
        self.mu = np.zeros(n_arms)
        self.sigma = np.ones(n_arms)

        # Parámetros de regresión lineal contextual
        self.theta = np.zeros((n_arms, n_features))

        # Estadísticos online para normalizar el contexto (z-score vía EMA)
        self.feat_mean = np.zeros(n_features)
        self.feat_var = np.ones(n_features)

    def _standardize(self, x, update_stats):
        """Devuelve el contexto estandarizado (media 0, varianza ~1) y recortado.

        update_stats=True actualiza media/varianza (una sola vez por iteración,
        en update); select_arm usa las estadísticas actuales sin modificarlas.
        """
        x = np.asarray(x, dtype=float)
        x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)

        if update_stats:
            self.feat_mean = (1 - self.ema) * self.feat_mean + self.ema * x
            diff = x - self.feat_mean
            self.feat_var = (1 - self.ema) * self.feat_var + self.ema * diff ** 2

        std = np.sqrt(self.feat_var) + 1e-8
        z = (x - self.feat_mean) / std
        return np.clip(z, -self.clip, self.clip)

    def select_arm(self, x):
        # Thompson Sampling: mu + ruido gaussiano
        samples = np.random.normal(self.mu, self.sigma)
        return int(np.argmax(samples))

    def update(self, arm, x, reward):
        # Estandarizar el contexto ANTES de usarlo (evita que theta explote
        # por features de gran magnitud como close ~4000).
        z = self._standardize(x, update_stats=True)

        # Actualizar theta (regresión simple sobre el contexto normalizado)
        self.theta[arm] += self.lr * (reward - np.dot(self.theta[arm], z)) * z

        # Actualizar mu y sigma
        self.mu[arm] = 0.9 * self.mu[arm] + 0.1 * reward
        self.sigma[arm] = max(0.1, self.sigma[arm] * 0.99)

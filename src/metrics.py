import numpy as np


def compute_sharpe(returns):
    returns = np.asarray(returns, dtype=float)
    if returns.size == 0:
        return 0.0
    s = np.std(returns)
    return float(np.mean(returns) / s) if s != 0 else 0.0


def compute_drawdown(returns):
    returns = np.asarray(returns, dtype=float)
    if returns.size == 0:
        return 0.0
    equity = np.cumsum(returns)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    return float(dd.min())


def compute_winrate(returns):
    returns = np.asarray(returns, dtype=float)
    if returns.size == 0:
        return 0.0
    return float(np.mean(returns > 0))


def compute_expectancy(returns):
    returns = np.asarray(returns, dtype=float)
    n = returns.size
    if n == 0:
        return 0.0
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    w = float(wins.mean()) * len(wins) / n if wins.size else 0.0
    l = float(losses.mean()) * len(losses) / n if losses.size else 0.0
    return w + l

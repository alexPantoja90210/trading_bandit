"""
make_equity_chart.py — genera la curva de equity de la cartera (Caso C del reporte) para el
portafolio freelance. Reusa la lógica de combined_portfolio.py. Guarda PNG en ../portafolio/.
Solo LEE.
"""
import os
import sys
import numpy as np
import pandas as pd

from mt5_connect import ensure
from combined_portfolio import stf_daily, rsi2_daily, scale_to_vol, metrics

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = r"C:\Users\tojap\OneDrive\Documents\2. Trading\Algo Trading Development\Portafolio\Combined_Portfolio_Equity.png"


def main():
    ensure()
    print("Construyendo cartera (STF H4 + RSI2 D1)...")
    stf = stf_daily("XAUUSD").add(stf_daily("BTCUSD", start_year=2013), fill_value=0)
    rsi2 = rsi2_daily("US500").add(rsi2_daily("NAS100"), fill_value=0)
    start = max(stf.index.min(), rsi2.index.min()); end = min(stf.index.max(), rsi2.index.max())
    idx = pd.bdate_range(start, end)
    stf = stf.reindex(idx, fill_value=0.0); rsi2 = rsi2.reindex(idx, fill_value=0.0)
    stf_s = scale_to_vol(stf); rsi2_s = scale_to_vol(rsi2); comb = 0.5*stf_s + 0.5*rsi2_s

    _, _, sh_s, dd_s = metrics(stf_s)
    _, _, sh_r, dd_r = metrics(rsi2_s)
    _, _, sh_c, dd_c = metrics(comb)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except ImportError:
        print("matplotlib no instalado; instálalo: .venv\\Scripts\\python -m pip install matplotlib")
        return

    teal, amber, gray = "#157f5f", "#b8860b", "#8a8f98"
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": "#cfd3d8",
                         "axes.linewidth": 0.8, "figure.dpi": 150})
    fig, ax = plt.subplots(figsize=(10, 5.2))
    fig.patch.set_facecolor("white"); ax.set_facecolor("white")
    x = idx
    ax.plot(x, np.cumsum(stf_s.values)*100, color=gray, lw=1.1, label=f"Trend (STF)  ·  Sharpe {sh_s:.2f} · DD {dd_s:.0f}%")
    ax.plot(x, np.cumsum(rsi2_s.values)*100, color=amber, lw=1.1, label=f"Mean-reversion (RSI2)  ·  Sharpe {sh_r:.2f} · DD {dd_r:.0f}%")
    ax.plot(x, np.cumsum(comb.values)*100, color=teal, lw=2.2, label=f"Combined 50/50 (vol-targeted)  ·  Sharpe {sh_c:.2f} · DD {dd_c:.1f}%")
    ax.axhline(0, color="#dcdfe3", lw=0.8)
    ax.grid(True, color="#eef0f2", lw=0.8)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}%"))
    ax.set_ylabel("Cumulative return (vol-targeted units)", fontsize=9, color="#4a4d55")
    ax.legend(loc="upper left", frameon=False, fontsize=9.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.text(0.985, 0.02, "Backtest — not a promise of future returns · A. Pantoja",
             ha="right", fontsize=7.5, color=gray)
    # titulo + subtitulo como texto de figura, con separacion clara (sin encimarse)
    fig.subplots_adjust(top=0.85, bottom=0.12, left=0.075, right=0.97)
    fig.text(0.075, 0.955, "Combined edge portfolio — Trend + Mean-Reversion (vol-targeted 10%)",
             fontsize=13.5, fontweight="bold", color="#1a1c22", ha="left", va="top")
    fig.text(0.075, 0.905, f"{start.year}–{end.year}  ·  daily correlation +0.01  ·  out-of-sample walk-forward validated",
             fontsize=9, color=gray, ha="left", va="top")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, facecolor="white", dpi=150)
    print(f"OK -> {os.path.abspath(OUT)}")
    print(f"Sharpe cartera {sh_c:.2f} · DD {dd_c:.1f}%  (STF {sh_s:.2f}/{dd_s:.0f}% · RSI2 {sh_r:.2f}/{dd_r:.0f}%)")


if __name__ == "__main__":
    main()

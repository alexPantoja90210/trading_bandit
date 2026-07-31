"""
backtest_news.py — ¿el filtro de NOTICIAS mejora la estrategia intradía (Zarattini)?

Hipótesis a decidir con datos:
  H1 (noticia = combustible): el momentum se alimenta de los breakouts de noticias → NO filtrar.
  H2 (noticia = whipsaw): las noticias dan rupturas falsas → filtrar mejora.

Método (sin necesitar calendario scrapeado): usar los DOS mayores movers programados de EE.UU.
con fecha determinista/conocida y partir el P&L DIARIO de la estrategia por tipo de día:
  - NFP  : 1er viernes de cada mes, 8:30 ET (pre-apertura) — EXACTO.
  - FOMC : anuncio 14:00 ET (intra-sesión), fechas conocidas 2021-2026 — best-effort.
Compara: rendimiento en días-evento vs resto, y el FILTRO (saltar esos días) vs baseline.
Reusa la maquinaria validada de intraday_breakout_zarattini. Solo LEE mercado.
"""
import sys
from datetime import date
from collections import defaultdict

import numpy as np
import MetaTrader5 as mt5

from mt5_connect import ensure
from intraday_breakout_zarattini import load_m30, build_matrices, move_matrix, simulate, stats

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# N validado por símbolo (walk-forward, README_LAB): US500=1.0, NAS100=1.5, US30=1.0
SYMBOLS = {"US500": 1.0, "NAS100": 1.5, "US30": 1.0}

# FOMC: día del ANUNCIO (2pm ET), best-effort 2021-2026 (data M30 del bróker ~2021+).
FOMC = {
    (2021, 1, 27), (2021, 3, 17), (2021, 4, 28), (2021, 6, 16), (2021, 7, 28),
    (2021, 9, 22), (2021, 11, 3), (2021, 12, 15),
    (2022, 1, 26), (2022, 3, 16), (2022, 5, 4), (2022, 6, 15), (2022, 7, 27),
    (2022, 9, 21), (2022, 11, 2), (2022, 12, 14),
    (2023, 2, 1), (2023, 3, 22), (2023, 5, 3), (2023, 6, 14), (2023, 7, 26),
    (2023, 9, 20), (2023, 11, 1), (2023, 12, 13),
    (2024, 1, 31), (2024, 3, 20), (2024, 5, 1), (2024, 6, 12), (2024, 7, 31),
    (2024, 9, 18), (2024, 11, 7), (2024, 12, 18),
    (2025, 1, 29), (2025, 3, 19), (2025, 5, 7), (2025, 6, 18), (2025, 7, 30),
    (2025, 9, 17), (2025, 10, 29), (2025, 12, 10),
    (2026, 1, 28), (2026, 3, 18), (2026, 4, 29), (2026, 6, 17), (2026, 7, 29),
}


def is_nfp(d):
    """1er viernes del mes (NFP release day)."""
    first = date(d.year, d.month, 1)
    offset = (4 - first.weekday()) % 7        # viernes=4
    return d.day == 1 + offset


def is_fomc(d):
    return (d.year, d.month, d.day) in FOMC


def desc(R):
    R = np.asarray(R, float)
    if len(R) < 5 or R.std() == 0:
        return f"n={len(R):>4}  mean={R.mean() if len(R) else 0:+.3f}%  (muestra chica)"
    sh = R.mean() / R.std() * np.sqrt(252)
    wr = (R > 0).mean() * 100
    return f"n={len(R):>4}  mean={R.mean():+.3f}%  Sharpe={sh:+.2f}  wr={wr:.0f}%  total={R.sum():+.1f}%"


def run(sym, N):
    df, path, ntot = load_m30(sym)
    if df is None or ntot < 2000:
        print(f"### {sym}: data insuficiente"); return
    M = build_matrices(df)
    mv = move_matrix(M["cum"])
    dates = M["dates"]
    info = mt5.symbol_info(sym)
    cost = (info.spread * info.point) / M["oday"][-1] * 100.0 if info else 0.0
    dp, _ = simulate(M, mv, N, cost)          # P&L por día de la estrategia (N validado)

    nfp = np.array([is_nfp(d) for d in dates])
    fomc = np.array([is_fomc(d) for d in dates])
    ev = nfp | fomc
    print(f"\n{'='*74}\n### {sym} · N={N} · {len(dates)} días ({dates[0]} -> {dates[-1]})  costo≈{cost:.4f}%")
    print(f"    días NFP={nfp.sum()}  FOMC={fomc.sum()}  evento(unión)={ev.sum()}  "
          f"({ev.mean()*100:.0f}% de los días)")

    print(f"\n[1] P&L de la estrategia por tipo de día:")
    print(f"    NFP  (1er vie): {desc(dp[nfp])}")
    print(f"    FOMC (anuncio): {desc(dp[fomc])}")
    print(f"    EVENTO (unión): {desc(dp[ev])}")
    print(f"    RESTO (normal): {desc(dp[~ev])}")

    # [2] FILTRO: no operar en días-evento (P&L=0) vs baseline (operar todos)
    base = dp.copy()
    filt = dp.copy(); filt[ev] = 0.0
    print(f"\n[2] FILTRO de noticias (saltar días-evento) vs baseline:")
    print(f"    baseline (opera todo): {desc(base)}")
    print(f"    con filtro (salta evt): {desc(filt)}")
    d_sh = (filt.mean()/filt.std() - base.mean()/base.std()) * np.sqrt(252) if base.std() and filt.std() else 0
    verdict = ("FILTRO AYUDA" if d_sh > 0.05 and dp[ev].mean() < 0 else
               "FILTRO NO AYUDA (evento no es peor / o pierde días buenos)")
    print(f"    ΔSharpe(filtro-base)={d_sh:+.2f}  |  {verdict}")

    # robustez: ¿el signo del efecto-evento se repite por año?
    yrs = np.array([d.year for d in dates])
    print(f"\n[3] P&L en días-evento por año (¿consistente?):")
    line = []
    for y in sorted(set(yrs)):
        m = ev & (yrs == y)
        if m.sum():
            line.append(f"{y}:{dp[m].sum():+.1f}%(n{m.sum()})")
    print("    " + "  ".join(line))


def main():
    ensure()
    for sym, N in SYMBOLS.items():
        run(sym, N)
    print(f"\n{'='*74}")
    print("Nota: NFP exacto; FOMC fechas best-effort 2021-2026. Test a nivel DÍA "
          "(NFP 8:30 ET pre-apertura afecta todo el día; FOMC 14:00 ET intra-sesión).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Tendencia de largo plazo (44 anios, 1980-2023) sobre la serie reconstruida
con WS_80mA_mean sustituido en los dias marcados
(tools/corrected_reconstruction.py), y AEP anual / P50-P75-P90 empiricos via
core.energy.AEPCalculator.compute_from_timeseries (NO se reimplementa el
calculo de percentiles, se reutiliza tal cual).

No existe una implementacion de Mann-Kendall en el repo (ni en core/ ni en
windmcp/): se implementa aqui, en tools/, siguiendo la formula clasica
(Mann 1945 / Kendall 1975) con correccion por empates y aproximacion normal
para el p-valor -- sin dependencias nuevas.

No modifica windmcp/, core/ ni runs/mcp/: solo lee y escribe bajo tools/.

Salida: tools/longterm_trend_aep_output/
  annual_wind_speed.csv     velocidad media anual 80 m, 1980-2023
  annual_aep.csv            AEP anual (MWh) turbina de referencia, 1980-2023
  mann_kendall_wind.txt     resultado de la prueba sobre velocidad anual
  aep_percentiles.json      salida cruda de AEPCalculator.compute_from_timeseries
  summary.txt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS_DIR))

import capture_validation as cv  # noqa: E402
from core.energy import AEPCalculator, extrapolate_wind_hellmann  # noqa: E402
from corrected_reconstruction import load_corrected_reconstruction  # noqa: E402

HUB_HEIGHT_M = 80.0
SOURCE_HEIGHT_M = 80.0
FULL_YEARS = list(range(1980, 2024))  # 44 anios completos; 2024 queda parcial (hasta 31-ago)
OUT_DIR = TOOLS_DIR / "longterm_trend_aep_output"


def mann_kendall(x: np.ndarray) -> dict:
    """Prueba de Mann-Kendall clasica (dos colas), con correccion por
    empates en la varianza. Devuelve S, varianza, Z y p-valor."""
    n = len(x)
    s = 0
    for k in range(n - 1):
        s += np.sum(np.sign(x[k + 1:] - x[k]))
    s = int(s)

    _vals, counts = np.unique(x, return_counts=True)
    tie_term = np.sum(counts * (counts - 1) * (2 * counts + 5))
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0

    if s > 0:
        z = (s - 1) / np.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s)
    else:
        z = 0.0
    p_value = 2 * (1 - stats.norm.cdf(abs(z)))

    # Pendiente de Sen (mediana de todas las pendientes por pares) como
    # magnitud de la tendencia, complementaria al test de significancia.
    slopes = []
    for k in range(n - 1):
        diffs = (x[k + 1:] - x[k]) / np.arange(1, n - k)
        slopes.extend(diffs.tolist())
    sen_slope = float(np.median(slopes))

    trend = "creciente" if (s > 0 and p_value < 0.05) else (
        "decreciente" if (s < 0 and p_value < 0.05) else "sin tendencia significativa")

    return {
        "n": n, "S": s, "var_S": round(float(var_s), 3), "Z": round(float(z), 4),
        "p_value": round(float(p_value), 4), "sen_slope_per_year": round(sen_slope, 6),
        "trend": trend,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    turbine = cv.load_turbine()

    print("Construyendo serie de viento corregida (80mA en dias marcados) ...")
    df, flagged, stats_fix = load_corrected_reconstruction()
    valid = np.isfinite(df["long_term_wind_speed_corrected"]) & (df["long_term_wind_speed_corrected"] >= 0)
    ws_80m = df.loc[valid, "long_term_wind_speed_corrected"]
    ws_hub = pd.Series(
        extrapolate_wind_hellmann(ws_80m.to_numpy(), SOURCE_HEIGHT_M, HUB_HEIGHT_M), index=ws_80m.index)

    # -- Velocidad media anual, 1980-2023 (44 anios completos; excluye 2024 parcial) --
    annual_ws = ws_80m.resample("YE").mean()
    annual_hours = ws_80m.resample("YE").count()
    expected_hours = pd.Series(
        [8784 if ts.is_leap_year else 8760 for ts in annual_ws.index], index=annual_ws.index, dtype=float)
    coverage = annual_hours / expected_hours
    annual_ws_full = annual_ws[(coverage >= 0.90) & (annual_ws.index.year.isin(FULL_YEARS))]
    print(f"Anios completos usados para MK: {len(annual_ws_full)} "
          f"({annual_ws_full.index.year.min()}-{annual_ws_full.index.year.max()})")

    annual_ws_df = pd.DataFrame({
        "year": annual_ws_full.index.year,
        "mean_ws_80m_ms": annual_ws_full.values.round(4),
        "n_hours": annual_hours.reindex(annual_ws_full.index).astype(int).values,
        "coverage_pct": (coverage.reindex(annual_ws_full.index) * 100).round(2).values,
    })
    annual_ws_df.to_csv(OUT_DIR / "annual_wind_speed.csv", index=False)

    mk_result = mann_kendall(annual_ws_full.to_numpy())
    with open(OUT_DIR / "mann_kendall_wind.txt", "w") as f:
        f.write("MANN-KENDALL -- velocidad media anual 80 m, 1980-2023 (44 anios)\n")
        f.write("=" * 66 + "\n")
        for k, v in mk_result.items():
            f.write(f"{k}: {v}\n")
    print("Mann-Kendall (velocidad anual 80 m, 44 anios):", mk_result)

    # -- AEP anual (turbina de referencia) y percentiles empiricos --
    energy_kwh = pd.Series(
        turbine.power_kw_density_corrected(ws_hub.to_numpy(), 1.225), index=ws_hub.index, name="energy_net_kwh")
    energy_df = energy_kwh.to_frame()

    calc = AEPCalculator()
    aep_result = calc.compute_from_timeseries(energy_df, energy_col="energy_net_kwh")
    with open(OUT_DIR / "aep_percentiles.json", "w") as f:
        json.dump(aep_result, f, indent=2, default=lambda o: float(o))

    annual_rows = pd.DataFrame(aep_result["annual"])
    annual_rows.to_csv(OUT_DIR / "annual_aep.csv", index=False)

    full_years_rows = annual_rows[annual_rows["year"].isin(FULL_YEARS)]
    mean_1980_2023 = float(full_years_rows["aep_mwh"].mean())
    rows_2017_2023 = full_years_rows[full_years_rows["year"].between(2017, 2023)]
    mean_2017_2023 = float(rows_2017_2023["aep_mwh"].mean())
    diff_pct = 100.0 * (mean_2017_2023 - mean_1980_2023) / mean_1980_2023

    with open(OUT_DIR / "summary.txt", "w") as f:
        f.write("TENDENCIA DE LARGO PLAZO -- 44 anios (1980-2023), serie corregida (80mA)\n")
        f.write("Turbina de referencia: Clipper 2.5MW/89m @ 80 m, sin estela, sin perdidas extra\n")
        f.write("=" * 78 + "\n\n")
        f.write("-- Mann-Kendall, velocidad media anual 80 m --\n")
        for k, v in mk_result.items():
            f.write(f"  {k}: {v}\n")
        f.write("\n-- AEP anual, P50/P75/P90 empiricos (AEPCalculator.compute_from_timeseries) --\n")
        f.write(f"  aep_p50_mwh (mediana de {aep_result['n_years']} anios): {aep_result['aep_p50_mwh']}\n")
        f.write(f"  aep_p75_mwh (percentil 25): {aep_result['aep_p75_mwh']}\n")
        f.write(f"  aep_p90_mwh (percentil 10): {aep_result['aep_p90_mwh']}\n")
        f.write(f"  aep_mean_mwh: {aep_result['aep_mean_mwh']}  aep_std_mwh: {aep_result['aep_std_mwh']}\n")
        f.write(f"  percentile_definition: {aep_result['percentile_definition']}\n\n")
        f.write("-- Energia 2017-2023 vs promedio 1980-2023 (anios completos, turbina de referencia) --\n")
        f.write(f"  AEP media 1980-2023 ({len(full_years_rows)} anios completos): {mean_1980_2023:.1f} MWh\n")
        f.write(f"  AEP media 2017-2023 ({len(rows_2017_2023)} anios): {mean_2017_2023:.1f} MWh\n")
        f.write(f"  diferencia: {mean_2017_2023 - mean_1980_2023:+.1f} MWh ({diff_pct:+.2f}%)\n\n")
        f.write(rows_2017_2023.to_string(index=False) + "\n")

    print(f"\nAEP media 1980-2023: {mean_1980_2023:.1f} MWh | AEP media 2017-2023: "
          f"{mean_2017_2023:.1f} MWh | diff: {diff_pct:+.2f}%")
    print("P50/P75/P90:", aep_result["aep_p50_mwh"], aep_result["aep_p75_mwh"], aep_result["aep_p90_mwh"])
    print("\nListo. Salidas en", OUT_DIR)


if __name__ == "__main__":
    main()

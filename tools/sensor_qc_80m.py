#!/usr/bin/env python3
"""Detector diario de falla del sensor WS_80mB_mean (CERTE M04), con
sensibilidad al umbral y chequeo de sesgo sistemático A vs (A+B)/2.

No modifica windmcp/, core/ ni runs/mcp/: solo lee el archivo CERTE crudo via
windmcp.data.read_certe_files (igual que el resto de tools/).

Reemplaza el detector anterior (tools/capture_validation_2020_sensor_fix.py,
persistencia >=6 muestras de 10 min idénticas): ese detector solo atrapaba B
literalmente congelado en un valor exacto y dejaba pasar horas con B todavía
muy desviado pero no "congelado" bit a bit (en ene-feb 2020 dejaba pasar 105
horas con ratio A/B hasta 111x). Este detector compara el promedio DIARIO de
A y B directamente -- más robusto porque promedia 144 muestras de 10 min y
cancela el ruido de turbulencia normal entre los dos sensores (que por hora
suelta hasta ~4% de desviación incluso en años limpios, ver
sensitivity_by_year() / threshold=0.10 en 2019).

Criterio: día "fallado" si |mean(A)/mean(B) - 1| > umbral, usando el promedio
diario (no horario). Umbral principal: 15% -- 2018 da 0/365 días, 2019 da
1/291 (25-dic-2019, un glitch aislado de un día), ambos muy por debajo de
cualquier desviación real del sensor en 2020 (ratios mensuales de 14x a 69x).

Salidas (tools/sensor_qc_80m_output/):
  threshold_sensitivity.csv   n días marcados por año x umbral (10/15/25%)
  flagged_days_2019_2020.csv  un renglón por día, con el ratio y si cada
                               umbral lo marca -- incluye junio 2020 completo
  monthly_bias_a_vs_combined.csv  cociente A/((A+B)/2) mensual, 2018 y 2019
                               limpios (excluye días marcados al 15%)
  summary.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windmcp.data import read_certe_files  # noqa: E402

CERTE_FILE = "/Users/ale/Desktop/2222dose/copiaunoMCPSOFT/certe/M04_CERTE_CONSOLIDADO_2017-2020.txt"
OBS_TIMEZONE = "Etc/GMT+6"
TARGET_COLUMN = "WS_80mA_mean"

THRESHOLDS = [0.10, 0.15, 0.25]
MAIN_THRESHOLD = 0.15
OUT_DIR = Path(__file__).resolve().parent / "sensor_qc_80m_output"


def load_raw() -> tuple[pd.Series, pd.Series, pd.Series]:
    frame, _ = read_certe_files([CERTE_FILE], target_column=TARGET_COLUMN, timezone=OBS_TIMEZONE)
    a = pd.to_numeric(frame["WS_80mA_mean"], errors="coerce")
    b = pd.to_numeric(frame["WS_80mB_mean"], errors="coerce")
    combined = pd.to_numeric(frame["wind_speed_obs"], errors="coerce")
    return a, b, combined


def daily_means(a: pd.Series, b: pd.Series) -> tuple[pd.Series, pd.Series]:
    return a.resample("1D").mean(), b.resample("1D").mean()


def flagged_days(ratio_daily: pd.Series, threshold: float) -> pd.DatetimeIndex:
    return ratio_daily[ratio_daily.sub(1.0).abs() > threshold].dropna().index


def hourly_mask_from_flagged_days(flagged: pd.DatetimeIndex, hourly_index: pd.DatetimeIndex) -> np.ndarray:
    return hourly_index.normalize().isin(flagged)


def threshold_sensitivity(ratio_daily: pd.Series) -> pd.DataFrame:
    rows = []
    for year in sorted(ratio_daily.index.year.unique()):
        r = ratio_daily[ratio_daily.index.year == year].dropna()
        for thr in THRESHOLDS:
            bad = flagged_days(r, thr)
            rows.append({
                "year": int(year), "threshold_pct": int(thr * 100),
                "n_days_flagged": len(bad), "n_days_total": len(r),
                "pct_flagged": round(100.0 * len(bad) / len(r), 2) if len(r) else np.nan,
            })
    return pd.DataFrame(rows)


def flagged_days_table(ratio_daily: pd.Series) -> pd.DataFrame:
    sub = ratio_daily.loc["2019-01-01":"2020-12-31"].dropna()
    rows = []
    for day, ratio in sub.items():
        rows.append({
            "date": day.date().isoformat(),
            "ratio_a_over_b": round(float(ratio), 4),
            **{f"flagged_{int(thr*100)}pct": bool(abs(ratio - 1.0) > thr) for thr in THRESHOLDS},
        })
    return pd.DataFrame(rows)


def monthly_bias_a_vs_combined(a: pd.Series, b: pd.Series, flagged_15pct: pd.DatetimeIndex) -> pd.DataFrame:
    a_h, b_h = a.resample("1h").mean(), b.resample("1h").mean()
    combined_h = (a_h + b_h) / 2.0
    ratio = a_h / combined_h
    clean_mask = ~ratio.index.normalize().isin(flagged_15pct)
    ratio_clean = ratio[clean_mask]
    rows = []
    for year in (2018, 2019):
        s = ratio_clean[ratio_clean.index.year == year]
        monthly = s.groupby(s.index.month).agg(["mean", "std", "count"])
        for month, row in monthly.iterrows():
            rows.append({
                "year": year, "month": int(month),
                "ratio_a_over_combined_mean": round(float(row["mean"]), 5),
                "ratio_a_over_combined_std": round(float(row["std"]), 5),
                "n_hours": int(row["count"]),
            })
    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    a, b, _combined = load_raw()
    daily_a, daily_b = daily_means(a, b)
    ratio_daily = daily_a / daily_b

    sensitivity_df = threshold_sensitivity(ratio_daily)
    sensitivity_df.to_csv(OUT_DIR / "threshold_sensitivity.csv", index=False)

    flagged_table = flagged_days_table(ratio_daily)
    flagged_table.to_csv(OUT_DIR / "flagged_days_2019_2020.csv", index=False)

    flagged_main = flagged_days(ratio_daily[ratio_daily.index.year == 2020], MAIN_THRESHOLD)
    flagged_main_2019 = flagged_days(ratio_daily[ratio_daily.index.year == 2019], MAIN_THRESHOLD)
    flagged_all_main = flagged_main.union(flagged_main_2019)

    bias_df = monthly_bias_a_vs_combined(a, b, flagged_all_main)
    bias_df.to_csv(OUT_DIR / "monthly_bias_a_vs_combined.csv", index=False)

    write_summary(sensitivity_df, flagged_table, flagged_main, bias_df)
    print("\nListo. Salidas en %s" % OUT_DIR)


def write_summary(sensitivity_df: pd.DataFrame, flagged_table: pd.DataFrame,
                   flagged_main_2020: pd.DatetimeIndex, bias_df: pd.DataFrame) -> None:
    lines = []
    lines.append("DETECTOR DIARIO WS_80mB_mean -- SENSIBILIDAD AL UMBRAL Y SESGO A vs (A+B)/2")
    lines.append("=" * 76)
    lines.append("")
    lines.append(f"Criterio principal: desviación diaria |A/B - 1| > {int(MAIN_THRESHOLD*100)}%.")
    lines.append("")

    lines.append("-- Sensibilidad al umbral (días marcados por año) --")
    lines.append(sensitivity_df.to_string(index=False))
    lines.append("")

    lines.append("-- Días de junio 2020 (los tres umbrales) --")
    june_2020 = flagged_table[flagged_table["date"].str.startswith("2020-06")]
    lines.append(june_2020.to_string(index=False))
    lines.append("")
    lines.append(f"Al {int(MAIN_THRESHOLD*100)}%, última fecha marcada en 2020: "
                 f"{flagged_main_2020.max().date() if len(flagged_main_2020) else 'ninguna'}.")
    lines.append("")

    lines.append("-- Cociente A/((A+B)/2) mensual, periodos limpios (excluye días marcados al 15%) --")
    lines.append(bias_df.to_string(index=False))
    overall_mean = bias_df["ratio_a_over_combined_mean"].mean()
    overall_std = bias_df["ratio_a_over_combined_mean"].std()
    lines.append("")
    lines.append(f"Media de las medias mensuales: {overall_mean:.5f} (std entre meses: {overall_std:.5f}).")
    if abs(overall_mean - 1.0) < 0.01 and overall_std < 0.01:
        lines.append("Sin sesgo sistemático relevante: A se mantiene dentro de ±1% del promedio "
                      "combinado en todos los meses limpios de 2018-2019.")
    else:
        lines.append("ADVERTENCIA: hay una desviación sistemática de A respecto del promedio "
                      "combinado mayor a 1% -- revisar antes de usar A como sustituto por defecto.")
    lines.append("")

    (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

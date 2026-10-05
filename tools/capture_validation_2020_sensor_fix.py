#!/usr/bin/env python3
"""Validacion de captura 2020, corrigiendo por el sensor WS_80mB_mean trabado.

Diagnostico previo (ver HANDOFF.md, "Sensor WS_80mB_mean trabado en 2020"):
WS_80mB_mean quedo congelado en ~0.21 m/s desde 2020-01-03 18:40 UTC hasta
2020-05-01 06:00 UTC (se recupera en junio). windmcp/data.py::_combine_height_sensors
promedia WS_80mA_mean (sano) con WS_80mB_mean (trabado pero numericamente
"valido", no NaN) para formar WS_80m_mean -- el target de entrenamiento/
validacion del MCP -- y esa ventana queda contaminada a la mitad del valor
real.

Este script compara DOS formas de tratar esa ventana contra la prediccion ya
generada por windmcp.service.run_mcp_validation para test_year=2020
(tools/capture_validation_output/mcp_run_test_year_2020/, de la corrida
anterior -- se reutiliza el CSV ya escrito, no se vuelve a entrenar ni se
toca windmcp/ ni runs/mcp/):

  (i)  Excluir del todo las horas donde WS_80mB_mean esta trabado. El target
       observado sigue siendo el WS_80m_mean combinado normal (A+B)/2 de
       siempre, solo que esas horas contaminadas no entran a la comparacion.
  (ii) Sustituir, SOLO dentro de la ventana trabada, el target observado por
       WS_80mA_mean puro (no combinado) -- fuera de la ventana sigue usando
       el combinado normal, igual que (i). Esto SI usa esas horas, bajo el
       supuesto de que WS_80mA_mean es una medicion sana de la velocidad real.

Ese supuesto se verifica con datos, no se asume: se ajusta un perfil de
cizalladura ley de potencia (20/40/60/80mA) por año (2018, 2019, 2020) y se
reporta el exponente alpha -- si WS_80mA_mean tambien estuviera dañado, el
perfil se distorsionaria. Resultado (ver print al inicio de la corrida):
alpha 2018=0.183, 2019=0.192, 2020=0.183 -- estable, nada distinto en 2020,
lo que respalda usar A como referencia sana dentro de la ventana.

Deteccion de "hora trabada": WS_80mB_mean se considera trabado en una muestra
de 10 min si repite el mismo valor (tolerancia 1e-6) durante al menos 6
muestras consecutivas (1 hora) -- mismo umbral que max_persistence=6 ya
usado en el proyecto para detectar sensores pegados, reutilizado aqui, no
reinventado. Una hora se marca "trabada" si tiene AL MENOS una muestra de
10 min marcada asi (criterio conservador: 2806 horas con >=1 muestra trabada
vs 2794 horas con >=3/6 -- la diferencia es mínima, no es sensible al umbral
exacto).

No modifica windmcp/, core/ ni runs/mcp/. Reutiliza funciones de
tools/capture_validation.py (build_price_array, hour_of_year_from_index,
chronological_result, block_bootstrap, load_turbine) en vez de
reimplementarlas.

Salidas (tools/capture_validation_2020_sensor_output/):
  summary_by_version.csv
  hourly_profile_by_version.csv
  bootstrap_by_version.csv
  summary.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS_DIR))

import capture_validation as cv  # noqa: E402  (reutiliza, no reimplementa)
from windmcp.data import read_certe_files  # noqa: E402

YEAR = 2020
STUCK_RUN_LENGTH_THRESHOLD = 6  # mismo valor que max_persistence en este proyecto
FORWARD_2020_CSV = (
    TOOLS_DIR / "capture_validation_output" / "mcp_run_test_year_2020"
    / "UnionWindnavigator_MERRA2_N16_500_W95_000.txt_forward.csv"
)
OUT_DIR = TOOLS_DIR / "capture_validation_2020_sensor_output"


def verify_shear_profile_stability() -> None:
    """A es consistente con 60/40/20 m en 2018, 2019 y 2020 (ver docstring)."""
    frame, _ = read_certe_files([cv.CERTE_FILE], target_column=cv.TARGET_COLUMN, timezone=cv.OBS_TIMEZONE)
    heights = {"WS_20m_mean": 20.0, "WS_40m_mean": 40.0, "WS_60m_mean": 60.0, "WS_80mA_mean": 80.0}
    sub = frame[list(heights.keys())].apply(pd.to_numeric, errors="coerce")
    hourly = sub.resample("1h").mean()
    print("Verificación de cizalladura (power-law, ref 80mA) por año:")
    for year in (2018, 2019, YEAR):
        s = hourly.loc[hourly.index.year == year].dropna()
        means = s.mean()
        z = np.array([heights[c] for c in means.index])
        v = means.to_numpy()
        logz = np.log(z / 80.0)
        logv = np.log(v / means["WS_80mA_mean"])
        alpha, _ = np.polyfit(logz, logv, 1)
        print(f"  {year}: n_horas={len(s)}, alpha={alpha:.4f}")


def detect_frozen_hours(frame: pd.DataFrame) -> pd.DatetimeIndex:
    b = pd.to_numeric(frame["WS_80mB_mean"], errors="coerce")
    change = b.diff().abs() > 1e-6
    change.iloc[0] = True
    group_id = change.cumsum()
    group_sizes = b.groupby(group_id).transform("size")
    stuck_mask = (group_sizes >= STUCK_RUN_LENGTH_THRESHOLD) & b.notna()
    hourly_stuck_count = stuck_mask.astype(int).resample("1h").sum()
    return hourly_stuck_count[hourly_stuck_count > 0].index


def build_hourly_series(frame: pd.DataFrame, column: str) -> tuple[pd.Series, pd.Series]:
    raw = pd.to_numeric(frame[column], errors="coerce")
    return raw.resample("1h").mean(), raw.resample("1h").count()


def build_version_index(combined_complete_idx, a_complete_idx, frozen_hours) -> dict[str, pd.DatetimeIndex]:
    non_frozen_complete = combined_complete_idx.difference(frozen_hours)
    frozen_with_good_a = a_complete_idx.intersection(frozen_hours)
    return {
        "i_excluye_horas_trabadas": non_frozen_complete,
        "ii_sustituye_80mA_en_ventana": non_frozen_complete.union(frozen_with_good_a),
    }


def build_observed_values(version: str, idx: pd.DatetimeIndex, combined_hourly: pd.Series,
                           a_hourly: pd.Series, frozen_hours: pd.DatetimeIndex) -> pd.Series:
    if version == "i_excluye_horas_trabadas":
        return combined_hourly.reindex(idx)
    values = pd.Series(index=idx, dtype=float)
    frozen_part = idx.intersection(frozen_hours)
    non_frozen_part = idx.difference(frozen_hours)
    values.loc[non_frozen_part] = combined_hourly.reindex(non_frozen_part)
    values.loc[frozen_part] = a_hourly.reindex(frozen_part)
    return values


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    verify_shear_profile_stability()

    print("\nCargando CERTE M04 crudo (10 min) ...")
    frame, _ = read_certe_files([cv.CERTE_FILE], target_column=cv.TARGET_COLUMN, timezone=cv.OBS_TIMEZONE)
    frozen_hours = detect_frozen_hours(frame)
    print(f"Horas 2020 marcadas 'WS_80mB_mean trabado': {len(frozen_hours[frozen_hours.year == YEAR])} "
          f"({frozen_hours.min()} -> {frozen_hours.max()})")

    combined_hourly, combined_completeness = build_hourly_series(frame, "wind_speed_obs")
    a_hourly, a_completeness = build_hourly_series(frame, "WS_80mA_mean")

    combined_complete_idx = combined_completeness[
        (combined_completeness.index.year == YEAR) & (combined_completeness == 6)
    ].index
    a_complete_idx = a_completeness[
        (a_completeness.index.year == YEAR) & (a_completeness == 6)
    ].index

    versions = build_version_index(combined_complete_idx, a_complete_idx, frozen_hours)

    print("Cargando predicción MCP 2020 (reutilizada de la corrida anterior) ...")
    forward = pd.read_csv(FORWARD_2020_CSV)
    forward["timestamp"] = pd.to_datetime(forward["timestamp"], utc=True)
    forward = forward.set_index("timestamp")["predicted_wind_speed"].dropna()

    price_array = cv.build_price_array(YEAR)
    turbine = cv.load_turbine()

    summary_rows = []
    hourly_profile_rows = []
    bootstrap_rows = []

    for version_name, version_idx in versions.items():
        obs_series = build_observed_values(version_name, version_idx, combined_hourly, a_hourly, frozen_hours)
        obs_series = obs_series.dropna()
        common_idx = obs_series.index.intersection(forward.index).sort_values()

        obs = obs_series.reindex(common_idx).to_numpy()
        pred = forward.reindex(common_idx).to_numpy()
        months_covered = sorted(common_idx.month.unique().tolist())

        print(f"\n[{version_name}] n_horas={len(common_idx)}, meses cubiertos={months_covered}")

        result_obs = cv.chronological_result(obs, common_idx, price_array, turbine)
        result_pred = cv.chronological_result(pred, common_idx, price_array, turbine)
        for branch, result in [("observado", result_obs), ("predicho_merra2", result_pred)]:
            summary_rows.append({
                "version": version_name, "branch": branch,
                "months_covered": ",".join(str(m) for m in months_covered), **result,
            })

        overall_r = (
            float(np.corrcoef(obs, pred)[0, 1]) if len(obs) >= cv.MIN_PAIRS_FOR_STATS else np.nan
        )
        print(f"  r(ws_obs, ws_pred) = {overall_r:.4f}")

        power_obs_kw = turbine.power_kw_density_corrected(obs, 1.225)
        power_pred_kw = turbine.power_kw_density_corrected(pred, 1.225)
        local_hour = (common_idx.hour - 6) % 24
        for hour in range(24):
            mask = local_hour == hour
            n = int(mask.sum())
            if n == 0:
                continue
            hourly_profile_rows.append({
                "version": version_name, "local_hour": hour, "n": n,
                "ws_obs_mean": float(np.mean(obs[mask])), "ws_pred_mean": float(np.mean(pred[mask])),
                "power_obs_kw_mean": float(np.mean(power_obs_kw[mask])),
                "power_pred_kw_mean": float(np.mean(power_pred_kw[mask])),
                "pearson_r": float(np.corrcoef(obs[mask], pred[mask])[0, 1]) if n >= cv.MIN_PAIRS_FOR_STATS else np.nan,
            })

        print(f"  Bootstrap por bloques de día (n_boot={cv.N_BOOTSTRAP}) ...")
        boot = cv.block_bootstrap(obs, pred, common_idx, price_array, turbine, cv.N_BOOTSTRAP, cv.BOOTSTRAP_SEED)
        bootstrap_rows.append({
            "version": version_name, "metric": "correlation_ws_obs_pred", **boot["correlation"],
            "n_days": boot["n_days"], "n_boot": boot["n_boot"],
        })
        bootstrap_rows.append({
            "version": version_name, "metric": "capture_price_diff_pred_minus_obs_usd_kwh",
            **boot["capture_price_diff_usd_kwh"], "n_days": boot["n_days"], "n_boot": boot["n_boot"],
        })

    summary_df = pd.DataFrame(summary_rows)
    hourly_df = pd.DataFrame(hourly_profile_rows)
    bootstrap_df = pd.DataFrame(bootstrap_rows)

    summary_df.to_csv(OUT_DIR / "summary_by_version.csv", index=False)
    hourly_df.to_csv(OUT_DIR / "hourly_profile_by_version.csv", index=False)
    bootstrap_df.to_csv(OUT_DIR / "bootstrap_by_version.csv", index=False)

    write_summary(summary_df, bootstrap_df)
    print("\nListo. Salidas en %s" % OUT_DIR)


def write_summary(summary_df: pd.DataFrame, bootstrap_df: pd.DataFrame) -> None:
    lines = []
    lines.append("VALIDACION DE CAPTURA 2020 -- CORRIGIENDO EL SENSOR WS_80mB_mean TRABADO")
    lines.append("=" * 72)
    lines.append("")
    for version, group in summary_df.groupby("version"):
        obs_row = group[group["branch"] == "observado"].iloc[0]
        pred_row = group[group["branch"] == "predicho_merra2"].iloc[0]
        lines.append(f"-- {version} -- meses={obs_row['months_covered']}, n_hours={int(obs_row['n_hours'])}")
        lines.append(f"  observado        -> precio capturado={obs_row['avg_realized_price_usd_kwh']:.6f} USD/kWh, "
                     f"capture_ratio={obs_row['capture_ratio']}, aep={obs_row['aep_mwh']:.1f} MWh/año(anualizado)")
        lines.append(f"  predicho (MERRA-2) -> precio capturado={pred_row['avg_realized_price_usd_kwh']:.6f} USD/kWh, "
                     f"capture_ratio={pred_row['capture_ratio']}, aep={pred_row['aep_mwh']:.1f} MWh/año(anualizado)")
        delta_pct = 100.0 * (pred_row["avg_realized_price_usd_kwh"] - obs_row["avg_realized_price_usd_kwh"]) / obs_row["avg_realized_price_usd_kwh"]
        delta_aep_pct = 100.0 * (pred_row["aep_mwh"] - obs_row["aep_mwh"]) / obs_row["aep_mwh"]
        lines.append(f"  diferencia precio capturado (predicho - observado): {delta_pct:+.2f}%")
        lines.append(f"  diferencia AEP (predicho - observado): {delta_aep_pct:+.2f}%")
        lines.append("")

    lines.append("-- Bootstrap por bloques de día --")
    for _, row in bootstrap_df.iterrows():
        sig = "SI" if row["significant_0_excluded"] else "no"
        lines.append(f"  {row['version']:28s} | {row['metric']:40s} -> observado={row['observed']:+.5f}, "
                     f"IC95%=[{row['ci_2_5']:+.5f}, {row['ci_97_5']:+.5f}], significativo: {sig} "
                     f"(n_dias={int(row['n_days'])})")
    lines.append("")

    (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Recalcula, con el detector diario de tools/sensor_qc_80m.py (no un
calendario fijo), (1) el cambio en la energía anual 2020 del
historical_reconstruction.csv del run activo al sustituir WS_80mA_mean en los
días marcados, y (2) la validación de captura "(ii)" -- sustituir por 80mA en
esos mismos días -- de tools/capture_validation.py.

Supersede a tools/capture_validation_2020_sensor_fix.py, que usaba un
detector de persistencia (>=6 muestras de 10 min idénticas) que dejaba pasar
horas todavía contaminadas pero no literalmente congeladas (ver HANDOFF.md,
sección 9, corregida). Ese script y sus salidas se conservan sin tocar, como
registro de ese primer intento.

No modifica windmcp/, core/ ni runs/mcp/ (el run oficial): solo lee
runs/mcp/20260926T185148Z_f23fc01b/historical_reconstruction.csv y el CERTE
crudo, y escribe exclusivamente bajo tools/.

Salidas (tools/capture_validation_2020_corrected_output/):
  flagged_days_used.csv        los días de 2020 que se sustituyen (umbral 15%)
  annual_energy_comparison.csv energía anual 2020: reconstruction original vs
                                corregido
  capture_summary.csv          validación (ii) recalculada: observado vs
                                predicho MERRA-2, solo horas 6/6, sustituyendo
                                80mA en los días marcados
  bootstrap_capture_diff.csv
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

import capture_validation as cv  # noqa: E402
import sensor_qc_80m as qc  # noqa: E402
from windmcp.data import read_certe_files  # noqa: E402

YEAR = 2020
ACTIVE_RUN_RECONSTRUCTION_CSV = (
    ROOT / "runs" / "mcp" / "20260926T185148Z_f23fc01b" / "historical_reconstruction.csv"
)
FORWARD_2020_CSV = (
    TOOLS_DIR / "capture_validation_output" / "mcp_run_test_year_2020"
    / "UnionWindnavigator_MERRA2_N16_500_W95_000.txt_forward.csv"
)
OUT_DIR = TOOLS_DIR / "capture_validation_2020_corrected_output"


def get_flagged_days_2020() -> pd.DatetimeIndex:
    a, b, _combined = qc.load_raw()
    daily_a, daily_b = qc.daily_means(a, b)
    ratio_daily = daily_a / daily_b
    ratio_2020 = ratio_daily[ratio_daily.index.year == YEAR]
    return qc.flagged_days(ratio_2020, qc.MAIN_THRESHOLD)


def recompute_annual_energy(flagged_days: pd.DatetimeIndex) -> pd.DataFrame:
    df = pd.read_csv(ACTIVE_RUN_RECONSTRUCTION_CSV, usecols=["timestamp", "long_term_wind_speed", "data_source"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp")
    y2020 = df.loc[f"{YEAR}-01-01":f"{YEAR}-12-31 23:00"].copy()

    a, _b, _combined = qc.load_raw()
    a_hourly = a.resample("1h").mean()

    is_flagged_day = y2020.index.normalize().isin(flagged_days)
    is_observed = y2020["data_source"] == "CERTE_observed"
    swap_mask = is_flagged_day & is_observed

    corrected = y2020["long_term_wind_speed"].copy()
    corrected.loc[swap_mask] = a_hourly.reindex(y2020.index[swap_mask])
    n_missing_a = int(corrected.loc[swap_mask].isna().sum())
    corrected = corrected.fillna(y2020["long_term_wind_speed"])

    turbine = cv.load_turbine()
    power_orig = turbine.power_kw_density_corrected(y2020["long_term_wind_speed"].to_numpy(), 1.225)
    power_corr = turbine.power_kw_density_corrected(corrected.to_numpy(), 1.225)
    energy_orig_mwh = float(np.sum(power_orig) * 1.0 / 1000.0)
    energy_corr_mwh = float(np.sum(power_corr) * 1.0 / 1000.0)

    return pd.DataFrame([{
        "year": YEAR,
        "n_hours_total": len(y2020),
        "n_hours_flagged_days": int(is_flagged_day.sum()),
        "n_hours_swapped_observed": int(swap_mask.sum()),
        "n_hours_swap_missing_a": n_missing_a,
        "energy_original_mwh": round(energy_orig_mwh, 1),
        "energy_corrected_mwh": round(energy_corr_mwh, 1),
        "delta_mwh": round(energy_corr_mwh - energy_orig_mwh, 1),
        "delta_pct": round(100.0 * (energy_corr_mwh - energy_orig_mwh) / energy_orig_mwh, 2),
    }])


def recompute_capture_validation_ii(flagged_days: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame, _ = read_certe_files([cv.CERTE_FILE], target_column=cv.TARGET_COLUMN, timezone=cv.OBS_TIMEZONE)
    combined_ws = pd.to_numeric(frame["wind_speed_obs"], errors="coerce")
    combined_hourly = combined_ws.resample("1h").mean()
    combined_completeness = combined_ws.resample("1h").count()
    a_hourly = pd.to_numeric(frame["WS_80mA_mean"], errors="coerce").resample("1h").mean()
    a_completeness = pd.to_numeric(frame["WS_80mA_mean"], errors="coerce").resample("1h").count()

    combined_complete_idx = combined_completeness[
        (combined_completeness.index.year == YEAR) & (combined_completeness == 6)
    ].index
    a_complete_idx = a_completeness[
        (a_completeness.index.year == YEAR) & (a_completeness == 6)
    ].index

    is_flagged = combined_complete_idx.normalize().isin(flagged_days)
    non_flagged_complete = combined_complete_idx[~is_flagged]
    flagged_with_good_a = a_complete_idx[a_complete_idx.normalize().isin(flagged_days)]
    version_ii_idx = non_flagged_complete.union(flagged_with_good_a).sort_values()

    obs_series = pd.Series(index=version_ii_idx, dtype=float)
    frozen_part = version_ii_idx[version_ii_idx.normalize().isin(flagged_days)]
    clean_part = version_ii_idx.difference(frozen_part)
    obs_series.loc[clean_part] = combined_hourly.reindex(clean_part)
    obs_series.loc[frozen_part] = a_hourly.reindex(frozen_part)
    obs_series = obs_series.dropna()

    forward = pd.read_csv(FORWARD_2020_CSV)
    forward["timestamp"] = pd.to_datetime(forward["timestamp"], utc=True)
    forward = forward.set_index("timestamp")["predicted_wind_speed"].dropna()

    common_idx = obs_series.index.intersection(forward.index).sort_values()
    obs = obs_series.reindex(common_idx).to_numpy()
    pred = forward.reindex(common_idx).to_numpy()
    months_covered = sorted(common_idx.month.unique().tolist())

    price_array = cv.build_price_array(YEAR)
    turbine = cv.load_turbine()
    result_obs = cv.chronological_result(obs, common_idx, price_array, turbine)
    result_pred = cv.chronological_result(pred, common_idx, price_array, turbine)

    overall_r = float(np.corrcoef(obs, pred)[0, 1]) if len(obs) >= cv.MIN_PAIRS_FOR_STATS else np.nan

    summary_rows = [
        {"branch": "observado", "months_covered": ",".join(str(m) for m in months_covered),
         "overall_r": round(overall_r, 4), **result_obs},
        {"branch": "predicho_merra2", "months_covered": ",".join(str(m) for m in months_covered),
         "overall_r": round(overall_r, 4), **result_pred},
    ]

    boot = cv.block_bootstrap(obs, pred, common_idx, price_array, turbine, cv.N_BOOTSTRAP, cv.BOOTSTRAP_SEED)
    bootstrap_rows = [
        {"metric": "correlation_ws_obs_pred", **boot["correlation"],
         "n_days": boot["n_days"], "n_boot": boot["n_boot"]},
        {"metric": "capture_price_diff_pred_minus_obs_usd_kwh", **boot["capture_price_diff_usd_kwh"],
         "n_days": boot["n_days"], "n_boot": boot["n_boot"]},
    ]
    return pd.DataFrame(summary_rows), pd.DataFrame(bootstrap_rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    flagged_days = get_flagged_days_2020()
    pd.DataFrame({"date": [d.date().isoformat() for d in flagged_days]}).to_csv(
        OUT_DIR / "flagged_days_used.csv", index=False)
    print(f"Días de 2020 sustituidos por 80mA (detector, umbral {int(qc.MAIN_THRESHOLD*100)}%): "
          f"{len(flagged_days)} ({flagged_days.min().date()} -> {flagged_days.max().date()})")

    energy_df = recompute_annual_energy(flagged_days)
    energy_df.to_csv(OUT_DIR / "annual_energy_comparison.csv", index=False)

    summary_df, bootstrap_df = recompute_capture_validation_ii(flagged_days)
    summary_df.to_csv(OUT_DIR / "capture_summary.csv", index=False)
    bootstrap_df.to_csv(OUT_DIR / "bootstrap_capture_diff.csv", index=False)

    write_summary(flagged_days, energy_df, summary_df, bootstrap_df)
    print("\nListo. Salidas en %s" % OUT_DIR)


def write_summary(flagged_days, energy_df, summary_df, bootstrap_df) -> None:
    lines = []
    lines.append("VALIDACION DE CAPTURA 2020 CORREGIDA -- DIAS DEL DETECTOR, NO CALENDARIO")
    lines.append("=" * 74)
    lines.append("")
    lines.append(f"Días sustituidos (umbral {int(qc.MAIN_THRESHOLD*100)}%): {len(flagged_days)} "
                 f"({flagged_days.min().date()} -> {flagged_days.max().date()})")
    lines.append("")

    lines.append("-- Energía anual 2020 (reconstruction del run activo) --")
    row = energy_df.iloc[0]
    lines.append(f"  horas totales={int(row['n_hours_total'])}, horas en días marcados="
                 f"{int(row['n_hours_flagged_days'])}, horas sustituidas (CERTE_observed)="
                 f"{int(row['n_hours_swapped_observed'])}")
    lines.append(f"  energía original: {row['energy_original_mwh']:.1f} MWh")
    lines.append(f"  energía corregida: {row['energy_corrected_mwh']:.1f} MWh")
    lines.append(f"  diferencia: {row['delta_mwh']:+.1f} MWh ({row['delta_pct']:+.2f}%)")
    lines.append("")

    lines.append("-- Validación de captura (ii), recalculada con días del detector --")
    obs_row = summary_df[summary_df["branch"] == "observado"].iloc[0]
    pred_row = summary_df[summary_df["branch"] == "predicho_merra2"].iloc[0]
    lines.append(f"  meses={obs_row['months_covered']}, n_hours={int(obs_row['n_hours'])}, "
                 f"r(ws_obs,ws_pred)={obs_row['overall_r']:.4f}")
    lines.append(f"  observado        -> precio capturado={obs_row['avg_realized_price_usd_kwh']:.6f} USD/kWh, "
                 f"capture_ratio={obs_row['capture_ratio']}, aep={obs_row['aep_mwh']:.1f} MWh/año(anualizado)")
    lines.append(f"  predicho (MERRA-2) -> precio capturado={pred_row['avg_realized_price_usd_kwh']:.6f} USD/kWh, "
                 f"capture_ratio={pred_row['capture_ratio']}, aep={pred_row['aep_mwh']:.1f} MWh/año(anualizado)")
    delta_pct = 100.0 * (pred_row["avg_realized_price_usd_kwh"] - obs_row["avg_realized_price_usd_kwh"]) / obs_row["avg_realized_price_usd_kwh"]
    lines.append(f"  diferencia precio capturado (predicho - observado): {delta_pct:+.2f}%")
    lines.append("")

    lines.append("-- Bootstrap por bloques de día --")
    for _, r in bootstrap_df.iterrows():
        sig = "SI" if r["significant_0_excluded"] else "no"
        lines.append(f"  {r['metric']:40s} -> observado={r['observed']:+.5f}, "
                     f"IC95%=[{r['ci_2_5']:+.5f}, {r['ci_97_5']:+.5f}], significativo: {sig} "
                     f"(n_dias={int(r['n_days'])})")
    lines.append("")

    (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

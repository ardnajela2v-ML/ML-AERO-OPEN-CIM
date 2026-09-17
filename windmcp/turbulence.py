"""Measured 10-minute turbulence and an auditable long-term extension.

The MCP wind-speed reconstruction is hourly and cannot contain microscale
turbulence.  This module therefore keeps two products deliberately separate:

* measured turbulence from logger-provided 10-minute mean/std statistics; and
* a conditional statistical extension trained only on the declared campaign
  year and applied to the hourly hindcast.

The second product is never labelled as measured or IEC-certified turbulence.
"""

from __future__ import annotations

import calendar
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd


MONTH_NAMES = [
    "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
]


def _json_float(value: Any, digits: int = 5) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if np.isfinite(number) else None


def _native_resolution_minutes(index: pd.DatetimeIndex) -> float | None:
    if len(index) < 2:
        return None
    unique = pd.DatetimeIndex(index).sort_values().unique()
    seconds = pd.Series(unique).diff().dropna().dt.total_seconds()
    seconds = seconds.loc[seconds > 0]
    if seconds.empty:
        return None
    return float(seconds.median() / 60.0)


def _local_index(index: pd.DatetimeIndex, timezone: str) -> pd.DatetimeIndex:
    values = pd.DatetimeIndex(index)
    if values.tz is None:
        values = values.tz_localize("UTC")
    try:
        return values.tz_convert(timezone)
    except (KeyError, TypeError, ValueError):
        return values.tz_convert("UTC")


def _first_column(frame: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    lower = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        if candidate in frame.columns:
            return str(candidate)
        found = lower.get(str(candidate).strip().lower())
        if found:
            return found
    return None


def _turbulence_columns(frame: pd.DataFrame) -> Tuple[str | None, str | None, str | None]:
    speed = _first_column(frame, (
        "WS_80mA_mean", "wind_speed_obs", "wind_speed", "ws_mean",
    ))
    sigma = _first_column(frame, (
        "WS_80mA_stdev", "wind_speed_obs_stdev", "wind_speed_stdev",
        "wind_speed_std", "ws_stdev", "ws_std", "sigma_u", "sigma",
    ))
    direction = _first_column(frame, (
        "wind_direction_obs", "wind_direction", "wind_dir", "WD_78m_mean",
        "direction", "wdir",
    ))
    return speed, sigma, direction


def _valid_turbulence_records(
    observations: pd.DataFrame,
    min_speed_ms: float,
    max_speed_ms: float,
    max_sigma_ms: float,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    speed_column, sigma_column, direction_column = _turbulence_columns(observations)
    if speed_column is None or sigma_column is None:
        missing = "velocidad media" if speed_column is None else "desviación estándar del logger"
        return pd.DataFrame(), {
            "available": False,
            "reason": f"No se encontró {missing} en la serie observada nativa.",
            "speed_column": speed_column,
            "sigma_column": sigma_column,
            "direction_column": direction_column,
        }

    speed = pd.to_numeric(observations[speed_column], errors="coerce")
    sigma = pd.to_numeric(observations[sigma_column], errors="coerce")
    direction = (
        pd.to_numeric(observations[direction_column], errors="coerce") % 360.0
        if direction_column else pd.Series(np.nan, index=observations.index)
    )
    ti = sigma / speed.replace(0.0, np.nan)
    finite = np.isfinite(speed) & np.isfinite(sigma) & np.isfinite(ti)
    speed_ok = speed.between(float(min_speed_ms), float(max_speed_ms), inclusive="both")
    sigma_ok = sigma.between(0.0, float(max_sigma_ms), inclusive="both")
    ti_ok = ti.between(0.0, 1.0, inclusive="both")
    valid = finite & speed_ok & sigma_ok & ti_ok

    clean = pd.DataFrame(index=observations.index[valid])
    clean.index.name = "timestamp"
    clean["wind_speed"] = speed.loc[valid].to_numpy(dtype=float)
    clean["sigma_u"] = sigma.loc[valid].to_numpy(dtype=float)
    clean["ti"] = ti.loc[valid].to_numpy(dtype=float)
    clean["wind_direction"] = direction.loc[valid].to_numpy(dtype=float)
    report = {
        "available": True,
        "records_original": int(len(observations)),
        "records_valid": int(valid.sum()),
        "records_rejected": int((~valid).sum()),
        "speed_column": speed_column,
        "sigma_column": sigma_column,
        "direction_column": direction_column,
        "filters": {
            "min_speed_ms": float(min_speed_ms),
            "max_speed_ms": float(max_speed_ms),
            "max_sigma_ms": float(max_sigma_ms),
            "max_ti": 1.0,
        },
    }
    return clean, report


def _measured_bins(frame: pd.DataFrame) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for centre in np.arange(1.0, 41.0, 1.0):
        mask = frame["wind_speed"].between(centre - 0.5, centre + 0.5, inclusive="left")
        values = frame.loc[mask]
        if len(values) < 5:
            continue
        rows.append({
            "ws_center": float(centre),
            "ws_bin": f"{centre - 0.5:.1f}–{centre + 0.5:.1f}",
            "n": int(len(values)),
            "sigma_mean": _json_float(values["sigma_u"].mean()),
            "sigma_p90": _json_float(values["sigma_u"].quantile(0.90)),
            "ti_mean": _json_float(values["ti"].mean()),
            "ti_p90": _json_float(values["ti"].quantile(0.90)),
        })
    return rows


def _monthly_summary(frame: pd.DataFrame, local: pd.DatetimeIndex) -> list[Dict[str, Any]]:
    work = frame.copy()
    work["month"] = local.month
    rows: list[Dict[str, Any]] = []
    for month in range(1, 13):
        values = work.loc[work["month"] == month]
        rows.append({
            "month": month,
            "label": MONTH_NAMES[month - 1],
            "n": int(len(values)),
            "ti_mean": _json_float(values["ti"].mean()),
            "ti_p90": _json_float(values["ti"].quantile(0.90)),
            "sigma_mean": _json_float(values["sigma_u"].mean()),
        })
    return rows


def _sector_summary(frame: pd.DataFrame, sector_count: int = 16) -> list[Dict[str, Any]]:
    width = 360.0 / int(sector_count)
    direction = pd.to_numeric(frame["wind_direction"], errors="coerce") % 360.0
    sector = np.floor(((direction + width / 2.0) % 360.0) / width)
    rows: list[Dict[str, Any]] = []
    for value in range(int(sector_count)):
        values = frame.loc[sector == value]
        rows.append({
            "sector": value,
            "center_deg": round(value * width, 3),
            "n": int(len(values)),
            "ti_mean": _json_float(values["ti"].mean()),
            "ti_p90": _json_float(values["ti"].quantile(0.90)),
        })
    return rows


def _sensor_comparison(observations: pd.DataFrame, min_speed_ms: float) -> Dict[str, Any]:
    required = {"wind_speed_obs", "WS_80mA_stdev", "wind_speed_obs_b", "WS_80mB_stdev"}
    if not required.issubset(observations.columns):
        return {"available": False, "reason": "No están disponibles ambos sensores A/B con desviación estándar."}
    a_speed = pd.to_numeric(observations.get("WS_80mA_mean", observations["wind_speed_obs"]), errors="coerce")
    b_speed = pd.to_numeric(observations["wind_speed_obs_b"], errors="coerce")
    a_sigma = pd.to_numeric(observations["WS_80mA_stdev"], errors="coerce")
    b_sigma = pd.to_numeric(observations["WS_80mB_stdev"], errors="coerce")
    mask = (
        a_speed.between(min_speed_ms, 40.0) & b_speed.between(min_speed_ms, 40.0)
        & a_sigma.between(0.0, 20.0) & b_sigma.between(0.0, 20.0)
    )
    if int(mask.sum()) < 10:
        return {"available": False, "reason": "Pocos pares A/B válidos.", "N": int(mask.sum())}
    ti_a = a_sigma.loc[mask] / a_speed.loc[mask]
    ti_b = b_sigma.loc[mask] / b_speed.loc[mask]
    difference = b_speed.loc[mask] - a_speed.loc[mask]
    return {
        "available": True,
        "N": int(mask.sum()),
        "speed_bias_b_minus_a_ms": _json_float(difference.mean()),
        "speed_rmse_ms": _json_float(np.sqrt(np.mean(np.square(difference)))),
        "speed_correlation": _json_float(a_speed.loc[mask].corr(b_speed.loc[mask])),
        "ti_bias_b_minus_a": _json_float((ti_b - ti_a).mean()),
    }


def measured_turbulence_summary(
    observations: pd.DataFrame,
    training_year: int = 2018,
    timezone: str = "UTC",
    measurement_height_m: float | None = 80.0,
    min_speed_ms: float = 3.0,
    max_speed_ms: float = 40.0,
    max_sigma_ms: float = 20.0,
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """Summarize real logger statistics for the fixed training year."""

    clean, qc = _valid_turbulence_records(
        observations, min_speed_ms=min_speed_ms,
        max_speed_ms=max_speed_ms, max_sigma_ms=max_sigma_ms,
    )
    resolution = _native_resolution_minutes(observations.index)
    if clean.empty:
        return {
            "available": False,
            "source_kind": "observed_native",
            "training_year": int(training_year),
            "native_resolution_minutes": _json_float(resolution, 3),
            "qc": qc,
            "note": "Sin desviación estándar nativa no es posible calcular TI medida.",
        }, clean

    local = _local_index(clean.index, timezone)
    campaign = clean.loc[local.year == int(training_year)].copy()
    campaign_local = local[local.year == int(training_year)]
    if campaign.empty:
        return {
            "available": False,
            "source_kind": "observed_native",
            "training_year": int(training_year),
            "native_resolution_minutes": _json_float(resolution, 3),
            "qc": qc,
            "note": f"No existen estadísticas de turbulencia válidas para {training_year}.",
        }, campaign

    expected = None
    if resolution and 0 < resolution <= 60:
        expected = int((366 if calendar.isleap(int(training_year)) else 365) * 24 * 60 / resolution)
    raw_local = _local_index(observations.index, timezone)
    raw_period = observations.loc[raw_local.year == int(training_year)]
    raw_speed = pd.to_numeric(raw_period[qc["speed_column"]], errors="coerce")
    raw_sigma = pd.to_numeric(raw_period[qc["sigma_column"]], errors="coerce")
    logger_pairs = int((
        raw_speed.between(0.5, max_speed_ms, inclusive="both")
        & raw_sigma.between(0.0, max_sigma_ms, inclusive="both")
    ).sum())
    at_15 = campaign.loc[campaign["wind_speed"].between(14.5, 15.5, inclusive="left")]
    campaign_years: list[Dict[str, Any]] = []
    available_years = sorted({int(value) for value in raw_local.year})
    clean_local_all = _local_index(clean.index, timezone)
    for year in available_years:
        raw_year = observations.loc[raw_local.year == year]
        valid_year = clean.loc[clean_local_all.year == year]
        valid_local_year = clean_local_all[clean_local_all.year == year]
        speed_year = pd.to_numeric(raw_year[qc["speed_column"]], errors="coerce")
        sigma_year = pd.to_numeric(raw_year[qc["sigma_column"]], errors="coerce")
        pair_records = int((
            speed_year.between(0.5, max_speed_ms, inclusive="both")
            & sigma_year.between(0.0, max_sigma_ms, inclusive="both")
        ).sum())
        expected_year = (
            int((366 if calendar.isleap(year) else 365) * 24 * 60 / resolution)
            if resolution and 0 < resolution <= 60 else None
        )
        pair_coverage = pair_records / expected_year * 100.0 if expected_year else None
        months = sorted({int(value) for value in valid_local_year.month})
        campaign_years.append({
            "year": int(year),
            "status": "complete" if pair_coverage is not None and pair_coverage >= 90.0 else "partial",
            "is_training_year": bool(year == int(training_year)),
            "expected_records": expected_year,
            "logger_pair_records": pair_records,
            "logger_pair_coverage_pct": _json_float(pair_coverage, 3),
            "analysis_records": int(len(valid_year)),
            "analysis_eligible_pct": (
                _json_float(len(valid_year) / expected_year * 100.0, 3) if expected_year else None
            ),
            "months_with_measurements": months,
            "months_without_measurements": [month for month in range(1, 13) if month not in months],
            "ti_mean": _json_float(valid_year["ti"].mean()),
            "ti_p90": _json_float(valid_year["ti"].quantile(0.90)),
        })
    measured = {
        "available": True,
        "source_kind": "observed_native",
        "measurement_method": "logger_mean_and_standard_deviation_same_10min_window",
        "training_year": int(training_year),
        "measurement_height_m": _json_float(measurement_height_m, 2),
        "native_resolution_minutes": _json_float(resolution, 3),
        "logger_statistics_confirmed": bool(resolution is not None and 8.0 <= resolution <= 12.0),
        "N": int(len(campaign)),
        "expected_records": expected,
        "logger_pair_records": logger_pairs,
        "logger_pair_coverage_pct": _json_float(logger_pairs / expected * 100.0, 3) if expected else None,
        "analysis_eligible_pct": _json_float(len(campaign) / expected * 100.0, 3) if expected else None,
        # Kept as a compatibility alias; this is eligibility after U>=3 m/s,
        # not missing-data availability.
        "coverage_pct": _json_float(len(campaign) / expected * 100.0, 3) if expected else None,
        "ti_overall": _json_float(campaign["ti"].mean()),
        "ti_median": _json_float(campaign["ti"].median()),
        "ti_15ms": _json_float(at_15["ti"].mean()),
        "ti_15ms_p90": _json_float(at_15["ti"].quantile(0.90)),
        "ti_15ms_n": int(len(at_15)),
        "iec_class": None,
        "iec_is_proxy": False,
        "bins": _measured_bins(campaign),
        "monthly": _monthly_summary(campaign, campaign_local),
        "sectors": _sector_summary(campaign, 16),
        "campaign_years": campaign_years,
        "sensor_comparison": _sensor_comparison(
            observations.loc[_local_index(observations.index, timezone).year == int(training_year)],
            min_speed_ms,
        ),
        "qc": qc,
        "note": (
            "TI medida = σ10min/V10min a partir de la media y desviación estándar del logger CERTE. "
            "El filtro U≥3 m/s evita cocientes inestables a baja velocidad. La clase IEC no se "
            "asigna automáticamente sin auditar metadatos, calibración y sectores de sombra de torre."
        ),
    }
    return measured, campaign


def measured_turbulence_records(
    observations: pd.DataFrame,
    timezone: str = "UTC",
    min_speed_ms: float = 3.0,
) -> pd.DataFrame:
    """Return all genuinely measured campaign TI records for audit/export."""

    clean, _ = _valid_turbulence_records(observations, min_speed_ms, 40.0, 20.0)
    if clean.empty:
        return pd.DataFrame(columns=(
            "timestamp", "wind_speed", "sigma_u", "turbulence_intensity",
            "wind_direction", "logger_year", "logger_month", "data_source",
        ))
    local = _local_index(clean.index, timezone)
    result = clean.rename(columns={"ti": "turbulence_intensity"}).reset_index()
    result["logger_year"] = local.year
    result["logger_month"] = local.month
    result["data_source"] = "CERTE_measured_10min"
    return result


def _group_lookup(
    frame: pd.DataFrame,
    columns: Sequence[str],
    minimum_count: int,
) -> Dict[Tuple[int, ...], Tuple[float, float, int]]:
    lookup: Dict[Tuple[int, ...], Tuple[float, float, int]] = {}
    grouped = frame.groupby(list(columns), observed=True)["ti"]
    for raw_key, values in grouped:
        clean = pd.to_numeric(values, errors="coerce").dropna()
        if len(clean) < int(minimum_count):
            continue
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        lookup[tuple(int(value) for value in key)] = (
            float(clean.quantile(0.50)), float(clean.quantile(0.90)), int(len(clean)),
        )
    return lookup


def _fit_lookup_model(training: pd.DataFrame, local: pd.DatetimeIndex) -> Dict[str, Any]:
    work = training.copy()
    width = 360.0 / 16.0
    work["month"] = local.month.astype(int)
    direction = pd.to_numeric(work["wind_direction"], errors="coerce") % 360.0
    work["sector"] = np.floor(((direction + width / 2.0) % 360.0) / width).fillna(-1).astype(int)
    work["speed_bin"] = np.floor(work["wind_speed"] + 0.5).clip(0, 40).astype(int)
    directional = work.loc[work["sector"] >= 0]
    return {
        "month_sector_speed": _group_lookup(directional, ("month", "sector", "speed_bin"), 20),
        "month_speed": _group_lookup(work, ("month", "speed_bin"), 30),
        "sector_speed": _group_lookup(directional, ("sector", "speed_bin"), 30),
        "speed": _group_lookup(work, ("speed_bin",), 50),
        "global": (float(work["ti"].quantile(0.50)), float(work["ti"].quantile(0.90)), int(len(work))),
    }


def _predict_lookup(
    model: Mapping[str, Any],
    speed: pd.Series,
    direction: pd.Series,
    timestamps: pd.DatetimeIndex,
    timezone: str,
    min_speed_ms: float,
) -> pd.DataFrame:
    local = _local_index(timestamps, timezone)
    width = 360.0 / 16.0
    speed_values = pd.to_numeric(speed, errors="coerce").to_numpy(dtype=float)
    direction_values = pd.to_numeric(direction, errors="coerce").to_numpy(dtype=float)
    months = local.month.to_numpy(dtype=int)
    speed_bins = np.floor(speed_values + 0.5)
    sectors = np.full(len(speed_values), -1, dtype=int)
    finite_direction = np.isfinite(direction_values)
    sectors[finite_direction] = np.floor(
        ((np.mod(direction_values[finite_direction], 360.0) + width / 2.0) % 360.0) / width
    ).astype(int)

    p50 = np.full(len(speed_values), np.nan, dtype=float)
    p90 = np.full(len(speed_values), np.nan, dtype=float)
    levels = np.full(len(speed_values), "unavailable", dtype=object)
    for i, (month, sector, raw_bin, value) in enumerate(zip(months, sectors, speed_bins, speed_values)):
        if not np.isfinite(value) or value < float(min_speed_ms) or value > 40.0:
            continue
        speed_bin = int(np.clip(raw_bin, 0, 40))
        candidates = (
            ("month_sector_speed", (int(month), int(sector), speed_bin)) if sector >= 0 else None,
            ("month_speed", (int(month), speed_bin)),
            ("sector_speed", (int(sector), speed_bin)) if sector >= 0 else None,
            ("speed", (speed_bin,)),
        )
        selected = None
        for candidate in candidates:
            if candidate is None:
                continue
            level, key = candidate
            selected = model[level].get(key)
            if selected is not None:
                levels[i] = level
                break
        if selected is None:
            selected = model["global"]
            levels[i] = "global"
        p50[i], p90[i] = selected[0], selected[1]
    return pd.DataFrame({
        "turbulence_intensity_p50": p50,
        "turbulence_intensity_p90": p90,
        "sigma_u_p50": p50 * speed_values,
        "sigma_u_p90": p90 * speed_values,
        "turbulence_model_level": levels,
    }, index=timestamps)


def _validation_metrics(actual: pd.Series, predicted: pd.Series) -> Dict[str, Any]:
    paired = pd.concat([
        pd.to_numeric(actual, errors="coerce").rename("actual"),
        pd.to_numeric(predicted, errors="coerce").rename("predicted"),
    ], axis=1).dropna()
    if paired.empty:
        return {"N": 0, "MAE": None, "RMSE": None, "Bias": None, "R2": None}
    error = paired["predicted"] - paired["actual"]
    denominator = float(np.sum(np.square(paired["actual"] - paired["actual"].mean())))
    r2 = None if denominator <= 0 else 1.0 - float(np.sum(np.square(error))) / denominator
    return {
        "N": int(len(paired)),
        "MAE": _json_float(np.mean(np.abs(error))),
        "RMSE": _json_float(np.sqrt(np.mean(np.square(error)))),
        "Bias": _json_float(np.mean(error)),
        "R2": _json_float(r2),
    }


def build_turbulence_products(
    observations: pd.DataFrame,
    historical: pd.DataFrame,
    training_year: int = 2018,
    validation_year: int = 2019,
    timezone: str = "UTC",
    measurement_height_m: float | None = 80.0,
    target_height_m: float | None = 80.0,
    min_speed_ms: float = 3.0,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Attach modelled TI quantiles to the hourly hindcast and return provenance."""

    measured, training = measured_turbulence_summary(
        observations,
        training_year=training_year,
        timezone=timezone,
        measurement_height_m=measurement_height_m,
        min_speed_ms=min_speed_ms,
    )
    output = historical.copy()
    if not measured.get("available") or training.empty:
        return output, {
            "measured_10min": measured,
            "historical_model": {
                "available": False,
                "source_kind": "modelled_not_measured",
                "reason": "El archivo observado no contiene estadísticas nativas suficientes para entrenar TI.",
            },
        }

    model = _fit_lookup_model(training, _local_index(training.index, timezone))
    timestamps = pd.DatetimeIndex(pd.to_datetime(output["timestamp"], errors="coerce", utc=True))
    speed = pd.to_numeric(output.get("long_term_wind_speed"), errors="coerce")
    direction = pd.to_numeric(
        output.get("wind_direction", pd.Series(np.nan, index=output.index)), errors="coerce"
    )
    predicted = _predict_lookup(model, speed, direction, timestamps, timezone, min_speed_ms)
    for column in predicted.columns:
        output[column] = predicted[column].to_numpy()
    output["turbulence_data_source"] = np.where(
        output["turbulence_intensity_p50"].notna(),
        f"modelled_from_CERTE_{int(training_year)}", "unavailable",
    )

    all_clean, _ = _valid_turbulence_records(observations, min_speed_ms, 40.0, 20.0)
    local_clean = _local_index(all_clean.index, timezone) if not all_clean.empty else pd.DatetimeIndex([])
    validation = all_clean.loc[local_clean.year == int(validation_year)].copy() if len(local_clean) else pd.DataFrame()
    if not validation.empty:
        validation_pred = _predict_lookup(
            model,
            validation["wind_speed"],
            validation["wind_direction"],
            validation.index,
            timezone,
            min_speed_ms,
        )
        validation_payload = {
            "available": True,
            "year": int(validation_year),
            "months_with_measurements": sorted({int(value) for value in _local_index(validation.index, timezone).month}),
            "months_without_measurements": [
                month for month in range(1, 13)
                if month not in set(_local_index(validation.index, timezone).month)
            ],
            "metrics_p50": _validation_metrics(
                validation["ti"].reset_index(drop=True),
                validation_pred["turbulence_intensity_p50"].reset_index(drop=True),
            ),
            "note": "Sólo se validan registros realmente medidos; los meses ausentes no se imputan como observación.",
        }
    else:
        validation_payload = {
            "available": False,
            "year": int(validation_year),
            "months_with_measurements": [],
            "months_without_measurements": list(range(1, 13)),
            "metrics_p50": {"N": 0, "MAE": None, "RMSE": None, "Bias": None, "R2": None},
        }

    summary_frame = pd.DataFrame({
        "timestamp": timestamps,
        "ti_p50": output["turbulence_intensity_p50"].to_numpy(),
        "ti_p90": output["turbulence_intensity_p90"].to_numpy(),
        "level": output["turbulence_model_level"].to_numpy(),
    }).dropna(subset=["timestamp"])
    summary_local = _local_index(pd.DatetimeIndex(summary_frame["timestamp"]), timezone)
    summary_frame["month"] = summary_local.month
    summary_frame["year"] = summary_local.year
    summary_frame["direction"] = direction.to_numpy()
    width = 360.0 / 16.0
    summary_frame["sector"] = np.floor(
        ((summary_frame["direction"] % 360.0 + width / 2.0) % 360.0) / width
    )
    monthly: list[Dict[str, Any]] = []
    for month in range(1, 13):
        values = summary_frame.loc[summary_frame["month"] == month]
        monthly.append({
            "month": month,
            "label": MONTH_NAMES[month - 1],
            "n": int(values["ti_p50"].notna().sum()),
            "ti_p50": _json_float(values["ti_p50"].mean()),
            "ti_p90": _json_float(values["ti_p90"].mean()),
        })
    annual: list[Dict[str, Any]] = []
    for year, values in summary_frame.groupby("year"):
        annual.append({
            "year": int(year),
            "n": int(values["ti_p50"].notna().sum()),
            "ti_p50": _json_float(values["ti_p50"].mean()),
            "ti_p90": _json_float(values["ti_p90"].mean()),
        })
    sectors: list[Dict[str, Any]] = []
    for sector in range(16):
        values = summary_frame.loc[summary_frame["sector"] == sector]
        sectors.append({
            "sector": sector,
            "center_deg": round(sector * width, 3),
            "n": int(values["ti_p50"].notna().sum()),
            "ti_p50": _json_float(values["ti_p50"].mean()),
            "ti_p90": _json_float(values["ti_p90"].mean()),
        })
    fallback_counts = output["turbulence_model_level"].value_counts().to_dict()
    historical_model = {
        "available": True,
        "source_kind": "modelled_not_measured",
        "method": "conditional_empirical_ti_quantiles",
        "predictand": "TI_10min=sigma_u_10min/mean_wind_speed_10min",
        "training_year": int(training_year),
        "training_policy": "fixed_single_year_no_validation_leakage",
        "training_records": int(len(training)),
        "seasonality": "calendar_month",
        "direction_sectors": 16,
        "speed_bin_width_ms": 1.0,
        "minimum_model_speed_ms": float(min_speed_ms),
        "measurement_height_m": _json_float(measurement_height_m, 2),
        "target_height_m": _json_float(target_height_m, 2),
        "height_warning": (
            None if measurement_height_m is None or target_height_m is None
            or abs(float(measurement_height_m) - float(target_height_m)) <= 0.5
            else "El modelo se entrenó a la altura medida y se aplicó a otra altura objetivo; requiere validación adicional."
        ),
        "period_start": timestamps.min().isoformat() if len(timestamps) else None,
        "period_end": timestamps.max().isoformat() if len(timestamps) else None,
        "modelled_records": int(output["turbulence_intensity_p50"].notna().sum()),
        "fallback_counts": {str(key): int(value) for key, value in fallback_counts.items()},
        "monthly": monthly,
        "annual": annual,
        "sectors": sectors,
        "validation": validation_payload,
        "note": (
            "Extensión estadística de TI condicionada por mes, velocidad y 16 sectores, entrenada sólo "
            f"con CERTE {training_year}. No es turbulencia medida ni sustituye una campaña IEC."
        ),
    }
    return output, {"measured_10min": measured, "historical_model": historical_model}

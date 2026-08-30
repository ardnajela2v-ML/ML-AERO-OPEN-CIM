"""High-level WindMCP run orchestration and reproducible artifacts."""

from __future__ import annotations

import json
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import pandas as pd

from .data import (ReferenceData, align_observations_and_reference, read_certe_files,
                   read_reference_files, resample_hourly)
from .energy import PowerCurve, compare_period_energy
from .config import MCPConfig, VERSION, validate_input_files
from .backtest import run_walk_forward_backtest, select_speed_reconstruction_method
from .mcp import (EXTENDED_METHODS, SEASONAL_QUANTILE_METHOD,
                  apply_seasonal_sector_direction_model, compare_mcp_methods,
                  fit_mcp_model, fit_seasonal_sector_direction_model,
                  fit_weibull_mle, rank_references, select_mcp_method,
                  validate_direction)
from .ml import compare_ml_methods, fit_ml_model, select_ml_method, validate_ml_direction
from .multireference import campaign_sensitivity, evaluate_multireference
from .profile import VerticalProfile, fit_vertical_profile, normalize_observation_height, normalize_reference_height
from .qc import quality_control
from .readiness import assess_reconstruction_readiness
from .reconstruction import reconstruct_historical
from .report import write_final_report
from .turbulence import build_turbulence_products, measured_turbulence_records


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _period_coverage(frame: pd.DataFrame, year: int) -> Dict[str, Any]:
    period = frame.loc[frame.index.year == int(year), ["wind_speed_obs", "wind_speed_ref"]].dropna()
    if period.empty:
        return {"year": int(year), "pairs": 0, "availability_pct": 0.0}
    expected = pd.date_range(
        start=period.index.min().floor("h"),
        end=period.index.max().floor("h"),
        freq="1h",
        tz="UTC",
    )
    expected_count = max(1, len(expected))
    months_with_pairs = sorted({int(value) for value in period.index.month})
    months_without_pairs = [month for month in range(1, 13) if month not in months_with_pairs]
    return {
        "year": int(year),
        "start": period.index.min().isoformat(),
        "end": period.index.max().isoformat(),
        "pairs": int(len(period)),
        "expected_hours_between_first_last": int(expected_count),
        "availability_pct": round(float(len(period) / expected_count * 100.0), 4),
        "months_with_pairs": months_with_pairs,
        "months_without_pairs": months_without_pairs,
    }


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_jsonable(value), indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _file_manifest(paths: Sequence[str]) -> List[Dict[str, Any]]:
    """Return immutable input identities for auditability and reruns."""

    manifest: List[Dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        stat = path.stat()
        manifest.append({
            "path": str(path),
            "filename": path.name,
            "size_bytes": int(stat.st_size),
            "sha256": digest.hexdigest(),
        })
    return manifest


def _write_validation_artifacts(output_dir: Path, label: str, validation: Mapping[str, Any]) -> str:
    path = output_dir / (label + ".csv")
    predictions = validation.get("predictions")
    if isinstance(predictions, pd.DataFrame):
        predictions.to_csv(path, index=False)
    return str(path)


def _wind_rose_payload(
    speed: pd.Series,
    direction: pd.Series,
    sector_count: int,
    speed_bins: Sequence[float] = (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 70.0),
) -> Dict[str, Any]:
    """Return a north-centred meteorological wind rose.

    Directions are bearings *from* north, clockwise.  Adding half a sector
    before flooring makes the first sector symmetric around 0° rather than
    spanning 0°→width, which is important when comparing against the CERTE
    thesis figure.
    """

    sector_count = int(sector_count)
    speed_values = pd.to_numeric(speed, errors="coerce").to_numpy(dtype=float)
    direction_values = pd.to_numeric(direction, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(speed_values) & np.isfinite(direction_values) & (speed_values >= 0.0)
    values = speed_values[valid]
    bearings = np.mod(direction_values[valid], 360.0)
    width = 360.0 / sector_count
    sectors = np.floor(((bearings + width / 2.0) % 360.0) / width).astype(int)
    total = max(1, len(values))
    total_cube = float(np.sum(np.maximum(values, 0.0) ** 3))
    frequency: List[float] = []
    mean_speed: List[float | None] = []
    energy: List[float] = []
    bin_matrix: List[List[float]] = []
    for index in range(sector_count):
        selected = values[sectors == index]
        frequency.append(float(len(selected) / total * 100.0))
        mean_speed.append(float(np.mean(selected)) if len(selected) else None)
        energy.append(
            float(np.sum(np.maximum(selected, 0.0) ** 3) / total_cube * 100.0)
            if total_cube else 0.0
        )
        bin_matrix.append([
            float(np.sum((selected >= low) & (selected < high)) / total * 100.0)
            for low, high in zip(speed_bins[:-1], speed_bins[1:])
        ])
    if sector_count == 16:
        labels = [
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
        ]
    else:
        labels = [f"{index * width:g}°" for index in range(sector_count)]
    dominant = int(np.argmax(frequency)) if frequency else 0
    return {
        "labels": labels,
        "frequency_pct": frequency,
        "mean_speed": mean_speed,
        "energy_pct": energy,
        "bin_matrix": bin_matrix,
        "bin_labels": [
            f"{low:g}-{high:g}"
            for low, high in zip(speed_bins[:-1], speed_bins[1:])
        ],
        "calm_pct": float(np.mean(values < 1.0) * 100.0) if len(values) else 0.0,
        "dominant_direction": labels[dominant] if labels else None,
        "dominant_pct": frequency[dominant] if frequency else None,
        "N": int(len(values)),
        "n_sectors": sector_count,
        "sector_width_deg": width,
        "direction_convention": "meteorological_from_north_clockwise",
        "sector_alignment": "centred_on_label",
    }


def _direction_frequency(direction: pd.Series, sector_count: int = 16) -> np.ndarray:
    values = pd.to_numeric(direction, errors="coerce").to_numpy(dtype=float)
    values = values[np.isfinite(values)] % 360.0
    if not len(values):
        return np.zeros(int(sector_count), dtype=float)
    width = 360.0 / int(sector_count)
    sectors = np.floor(((values + width / 2.0) % 360.0) / width).astype(int)
    return np.bincount(sectors, minlength=int(sector_count)).astype(float) / len(values) * 100.0


def _direction_reference_selection(
    aligned_by_name: Mapping[str, pd.DataFrame],
    training_year: int,
    sector_count: int = 16,
) -> Dict[str, Any]:
    """Select direction independently using only blocked holdouts from 2018."""

    rows: List[Dict[str, Any]] = []
    for name, aligned in aligned_by_name.items():
        required = {"wind_direction_obs", "wind_direction_ref"}
        if not required.issubset(aligned.columns):
            continue
        period = aligned.loc[aligned.index.year == int(training_year), sorted(required)].dropna()
        training_parts: List[pd.DataFrame] = []
        holdout_parts: List[pd.DataFrame] = []
        for _, month_frame in period.groupby(period.index.month):
            if len(month_frame) < 8:
                continue
            split = min(max(int(np.floor(len(month_frame) * 0.75)), 2), len(month_frame) - 2)
            training_parts.append(month_frame.iloc[:split])
            holdout_parts.append(month_frame.iloc[split:])
        if not training_parts or not holdout_parts:
            continue
        training = pd.concat(training_parts).sort_index()
        holdout = pd.concat(holdout_parts).sort_index()
        model = fit_seasonal_sector_direction_model(
            training["wind_direction_obs"], training["wind_direction_ref"], training.index,
            n_sectors=sector_count,
        )
        predicted = apply_seasonal_sector_direction_model(
            holdout["wind_direction_ref"], holdout.index, model
        )
        observed = holdout["wind_direction_obs"].to_numpy(dtype=float)
        valid = np.isfinite(observed) & np.isfinite(predicted)
        if not valid.any():
            continue
        corrected_error = np.abs(((predicted[valid] - observed[valid] + 180.0) % 360.0) - 180.0)
        raw = holdout["wind_direction_ref"].to_numpy(dtype=float)
        raw_error = np.abs(((raw[valid] - observed[valid] + 180.0) % 360.0) - 180.0)
        rose_distance = 0.5 * float(np.sum(np.abs(
            _direction_frequency(pd.Series(observed[valid]), sector_count)
            - _direction_frequency(pd.Series(predicted[valid]), sector_count)
        )))
        rows.append({
            "reference": str(name),
            "training_year": int(training_year),
            "selection_policy": "monthly_chronological_75_25_holdout_inside_training_year",
            "training_pairs": int(len(training)),
            "holdout_pairs": int(valid.sum()),
            "mae_raw_deg": float(np.mean(raw_error)),
            "mae_corrected_deg": float(np.mean(corrected_error)),
            "rose_total_variation_pct_points": rose_distance,
        })
    if not rows:
        raise ValueError("No reference has enough 2018 direction pairs for seasonal selection")
    rows.sort(key=lambda row: (
        row["rose_total_variation_pct_points"], row["mae_corrected_deg"], row["reference"]
    ))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {
        "selected_reference": rows[0]["reference"],
        "training_year": int(training_year),
        "selection": "minimum_16_sector_rose_distance_then_circular_mae_on_2018_holdouts",
        "candidates": rows,
    }


def _weibull_distribution_payload(values: pd.Series) -> Dict[str, Any]:
    sample = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    positive = sample[np.isfinite(sample) & (sample > 0.0)]
    fit = fit_weibull_mle(positive, minimum_points=20)
    upper = max(32.0, float(np.ceil(np.nanpercentile(positive, 99.95))) if len(positive) else 32.0)
    edges = np.arange(0.0, np.ceil(upper) + 1.0, 1.0)
    counts, _ = np.histogram(positive, bins=edges)
    centres = (edges[:-1] + edges[1:]) / 2.0
    density = counts.astype(float) / max(1, len(positive)) / np.diff(edges)
    k = fit.get("k")
    scale = fit.get("lambda")
    if k is None or scale is None:
        pdf = np.full(len(centres), np.nan)
    else:
        pdf = (float(k) / float(scale)) * np.power(centres / float(scale), float(k) - 1.0) * np.exp(
            -np.power(centres / float(scale), float(k))
        )
    return {
        **fit,
        "mean_ms": float(np.mean(positive)) if len(positive) else None,
        "labels_ms": centres.tolist(),
        "empirical_density": density.tolist(),
        "weibull_pdf": pdf.tolist(),
        "bin_width_ms": 1.0,
    }


def _training_observation_baseline(
    observations: pd.DataFrame,
    training_year: int,
    observation_timezone: str,
) -> Dict[str, Any]:
    """Audit the exact CERTE training population before hourly aggregation."""

    local_index = observations.index.tz_convert(observation_timezone)
    period = observations.loc[local_index.year == int(training_year)].copy()
    required = {"wind_speed_obs", "wind_direction_obs"}
    if period.empty or not required.issubset(period.columns):
        return {}
    hourly = resample_hourly(period)
    raw_weibull = _weibull_distribution_payload(period["wind_speed_obs"])
    hourly_weibull = _weibull_distribution_payload(hourly["wind_speed_obs"])
    raw_rose_36 = _wind_rose_payload(period["wind_speed_obs"], period["wind_direction_obs"], 36)
    hourly_rose_36 = _wind_rose_payload(hourly["wind_speed_obs"], hourly["wind_direction_obs"], 36)
    variation = 0.5 * float(np.sum(np.abs(
        np.asarray(raw_rose_36["frequency_pct"]) - np.asarray(hourly_rose_36["frequency_pct"])
    )))
    time_deltas = period.index.to_series().diff().dropna().dt.total_seconds() / 60.0
    raw_resolution = float(time_deltas.median()) if not time_deltas.empty else None
    thesis_k = 1.809
    thesis_lambda = 10.522
    return {
        "year": int(training_year),
        "selection_clock": observation_timezone,
        "source_height_speed_m": 80.0,
        "source_height_direction_m": 78.0,
        "raw_resolution_minutes": raw_resolution,
        "raw_rows": int(len(period)),
        "hourly_rows": int(len(hourly)),
        "raw_10min": {
            "weibull": raw_weibull,
            "wind_rose_36": raw_rose_36,
            "wind_rose_16": _wind_rose_payload(
                period["wind_speed_obs"], period["wind_direction_obs"], 16
            ),
        },
        "hourly_circular": {
            "weibull": hourly_weibull,
            "wind_rose_36": hourly_rose_36,
            "wind_rose_16": _wind_rose_payload(
                hourly["wind_speed_obs"], hourly["wind_direction_obs"], 16
            ),
        },
        "documentary_reference": {
            "weibull_k": thesis_k,
            "weibull_lambda": thesis_lambda,
            "matches_raw_10min": bool(
                raw_weibull.get("k") is not None
                and abs(float(raw_weibull["k"]) - thesis_k) <= 0.01
                and abs(float(raw_weibull["lambda"]) - thesis_lambda) <= 0.02
            ),
        },
        "aggregation_effect": {
            "delta_weibull_k_hourly_minus_raw": (
                None if raw_weibull.get("k") is None or hourly_weibull.get("k") is None
                else float(hourly_weibull["k"]) - float(raw_weibull["k"])
            ),
            "delta_weibull_lambda_hourly_minus_raw_ms": (
                None if raw_weibull.get("lambda") is None or hourly_weibull.get("lambda") is None
                else float(hourly_weibull["lambda"]) - float(raw_weibull["lambda"])
            ),
            "rose_total_variation_pct_points": variation,
            "direction_aggregation": "circular_mean_sin_cos",
        },
        "note": (
            "Este control usa los registros CERTE originales del año de entrenamiento. "
            "El ajuste documental se reproduce a 10 minutos; la serie horaria es la población "
            "sincronizada usada por MCP y no debe confundirse con el Weibull histórico reconstruido."
        ),
    }


def _validation_chart_data(
    predictions: pd.DataFrame,
    max_points: int = 900,
    sector_count: int = 16,
) -> Dict[str, Any]:
    """Build compact chart payloads for the integrated MCP Lab UI."""

    if not isinstance(predictions, pd.DataFrame) or predictions.empty:
        return {}
    frame = predictions.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    for column in (
        "observed_wind_speed", "predicted_wind_speed",
        "observed_wind_direction", "predicted_wind_direction",
    ):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "observed_wind_speed", "predicted_wind_speed"]).sort_values("timestamp")
    if frame.empty:
        return {}

    def sampled(source: pd.DataFrame, limit: int) -> pd.DataFrame:
        if len(source) <= limit:
            return source
        positions = np.linspace(0, len(source) - 1, limit, dtype=int)
        return source.iloc[np.unique(positions)]

    time_sample = sampled(frame, max_points)
    scatter_sample = sampled(frame, min(1200, max_points * 2))
    residual = frame["predicted_wind_speed"] - frame["observed_wind_speed"]
    speed_max = float(np.nanpercentile(
        np.r_[frame["observed_wind_speed"].to_numpy(), frame["predicted_wind_speed"].to_numpy()], 99.5
    ))
    speed_edges = np.linspace(0.0, max(5.0, speed_max), 25)
    measured_hist, _ = np.histogram(frame["observed_wind_speed"], bins=speed_edges)
    estimated_hist, _ = np.histogram(frame["predicted_wind_speed"], bins=speed_edges)
    residual_limit = max(1.0, float(np.nanpercentile(np.abs(residual), 99.0)))
    residual_edges = np.linspace(-residual_limit, residual_limit, 31)
    residual_hist, _ = np.histogram(residual, bins=residual_edges)

    monthly = frame.assign(month=frame["timestamp"].dt.month).groupby("month").agg(
        measured=("observed_wind_speed", "mean"),
        estimated=("predicted_wind_speed", "mean"),
        pairs=("observed_wind_speed", "count"),
    )

    sector_count = 16 if int(sector_count) not in {12, 16} else int(sector_count)

    estimated_direction = frame.get("predicted_wind_direction")
    observed_direction = frame.get("observed_wind_direction")
    direction_note = (
        f"Rosa reconstruida con un modelo direccional independiente de {sector_count} sectores, "
        "entrenado en 2018; este modelo no selecciona ni modifica el método ganador de velocidad."
    )
    if observed_direction is None and estimated_direction is not None:
        observed_direction = estimated_direction
        direction_note = "La estación no contiene dirección válida; ambas rosas usan la dirección de la referencia seleccionada."
    wind_roses = None
    if observed_direction is not None and estimated_direction is not None:
        direction_valid = observed_direction.notna() & estimated_direction.notna()
        circular_mae = None
        if direction_valid.any():
            delta = np.abs(
                ((estimated_direction.loc[direction_valid] - observed_direction.loc[direction_valid] + 180.0) % 360.0) - 180.0
            )
            circular_mae = float(delta.mean())
        wind_roses = {
            "measured": _wind_rose_payload(
                frame["observed_wind_speed"], observed_direction, sector_count
            ),
            "estimated": _wind_rose_payload(
                frame["predicted_wind_speed"], estimated_direction, sector_count
            ),
            "direction_mae_deg": circular_mae,
            "note": direction_note,
        }

    return {
        "period_start": frame["timestamp"].min().isoformat(),
        "period_end": frame["timestamp"].max().isoformat(),
        "pairs": int(len(frame)),
        "timeseries": {
            "timestamps": [value.isoformat() for value in time_sample["timestamp"]],
            "measured": time_sample["observed_wind_speed"].astype(float).tolist(),
            "estimated": time_sample["predicted_wind_speed"].astype(float).tolist(),
        },
        "scatter": {
            "measured": scatter_sample["observed_wind_speed"].astype(float).tolist(),
            "estimated": scatter_sample["predicted_wind_speed"].astype(float).tolist(),
        },
        "speed_distribution": {
            "labels": [float((speed_edges[index] + speed_edges[index + 1]) / 2.0) for index in range(len(speed_edges) - 1)],
            "measured_pct": (measured_hist / max(1, measured_hist.sum()) * 100.0).tolist(),
            "estimated_pct": (estimated_hist / max(1, estimated_hist.sum()) * 100.0).tolist(),
        },
        "residual_distribution": {
            "labels": [float((residual_edges[index] + residual_edges[index + 1]) / 2.0) for index in range(len(residual_edges) - 1)],
            "frequency": residual_hist.astype(int).tolist(),
        },
        "monthly_profile": {
            "labels": list(range(1, 13)),
            "measured": [float(monthly.loc[value, "measured"]) if value in monthly.index else None for value in range(1, 13)],
            "estimated": [float(monthly.loc[value, "estimated"]) if value in monthly.index else None for value in range(1, 13)],
            "pairs": [int(monthly.loc[value, "pairs"]) if value in monthly.index else 0 for value in range(1, 13)],
        },
        "wind_roses": wind_roses,
    }


def _reconstruction_visualization(
    reconstruction: pd.DataFrame,
    annual: pd.DataFrame,
    observation_years: Sequence[int],
) -> Dict[str, Any]:
    """Build audit-friendly campaign, hindcast and future-climate payloads."""

    frame = reconstruction.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    years = sorted({int(value) for value in observation_years})
    if not years:
        years = sorted({int(value) for value in frame.index.year})
    campaign_start = pd.Timestamp(year=min(years), month=1, day=1, tz="UTC")
    campaign_end = pd.Timestamp(year=max(years) + 1, month=1, day=1, tz="UTC") - pd.Timedelta(hours=1)
    campaign = frame.loc[(frame.index >= campaign_start) & (frame.index <= campaign_end)]

    daily = campaign.resample("1D").agg(
        measured=("observed_wind_speed", "mean"),
        estimated=("model_wind_speed", "mean"),
        combined=("long_term_wind_speed", "mean"),
        measured_hours=("observed_wind_speed", lambda values: int(values.notna().sum())),
        estimated_hours=("model_wind_speed", lambda values: int(values.notna().sum())),
    )
    monthly = frame.resample("MS").agg(
        hindcast=("long_term_wind_speed", "mean"),
        model=("model_wind_speed", "mean"),
        measured=("observed_wind_speed", "mean"),
    )

    gap_rows: List[Dict[str, Any]] = []
    month_rows: List[Dict[str, Any]] = []
    for year in years:
        year_frame = campaign.loc[campaign.index.year == year]
        expected = int(8784 if pd.Timestamp(year=year, month=12, day=31).is_leap_year else 8760)
        measured_hours = int(year_frame["observed_wind_speed"].notna().sum())
        reconstructed_mask = year_frame["observed_wind_speed"].isna() & year_frame["model_wind_speed"].notna()
        reconstructed_hours = int(reconstructed_mask.sum())
        unresolved = max(0, expected - measured_hours - reconstructed_hours)
        gap_rows.append({
            "year": year,
            "expected_hours": expected,
            "measured_hours": measured_hours,
            "measured_availability_pct": float(measured_hours / expected * 100.0),
            "mcp_filled_hours": reconstructed_hours,
            "unresolved_hours": unresolved,
        })
        for month in range(1, 13):
            month_frame = year_frame.loc[year_frame.index.month == month]
            month_expected = int(pd.Period(f"{year}-{month:02d}", freq="M").days_in_month * 24)
            month_measured = int(month_frame["observed_wind_speed"].notna().sum())
            month_filled = int((month_frame["observed_wind_speed"].isna() & month_frame["model_wind_speed"].notna()).sum())
            month_rows.append({
                "year": year,
                "month": month,
                "expected_hours": month_expected,
                "measured_hours": month_measured,
                "mcp_filled_hours": month_filled,
                "unresolved_hours": max(0, month_expected - month_measured - month_filled),
            })

    annual_valid = annual.copy()
    if not annual_valid.empty:
        annual_valid = annual_valid.loc[
            annual_valid["mean_long_term_wind_speed"].notna()
            & (annual_valid["hours"] >= 8000)
        ]
    annual_values = annual_valid["mean_long_term_wind_speed"].to_numpy(dtype=float) if not annual_valid.empty else np.array([])
    if len(annual_values):
        p05, p50, p95 = [float(value) for value in np.percentile(annual_values, [5, 50, 95])]
        mean = float(np.mean(annual_values))
        iav_pct = float(np.std(annual_values, ddof=1) / mean * 100.0) if len(annual_values) > 1 and mean else 0.0
    else:
        p05 = p50 = p95 = None
        iav_pct = None
    last_historical_year = int(annual["year"].max()) if not annual.empty else max(years)
    future_years = list(range(last_historical_year + 1, last_historical_year + 26))

    def values(column: str, source: pd.DataFrame) -> List[Any]:
        return [None if pd.isna(value) else float(value) for value in source[column]]

    return {
        "campaign_daily": {
            "period_start": campaign_start.isoformat(),
            "period_end": campaign_end.isoformat(),
            "resolution": "daily_mean",
            "timestamps": [value.isoformat() for value in daily.index],
            "measured": values("measured", daily),
            "estimated": values("estimated", daily),
            "combined": values("combined", daily),
            "measured_hours": daily["measured_hours"].astype(int).tolist(),
        },
        "historical_monthly": {
            "period_start": frame.index.min().isoformat(),
            "period_end": frame.index.max().isoformat(),
            "resolution": "monthly_mean",
            "timestamps": [value.isoformat() for value in monthly.index],
            "hindcast": values("hindcast", monthly),
            "model": values("model", monthly),
            "measured": values("measured", monthly),
        },
        "future_climate_25y": {
            "years": future_years,
            "p05": [p05] * 25,
            "p50": [p50] * 25,
            "p95": [p95] * 25,
            "iav_pct": iav_pct,
            "historical_years_used": annual_valid["year"].astype(int).tolist() if not annual_valid.empty else [],
            "method": "stationary_empirical_climatology",
            "note": "Escenario climatológico de 25 años bajo estacionariedad; no es un pronóstico meteorológico determinista.",
        },
        "gap_treatment": {
            "method": "MCP reconstruction from selected reanalysis reference",
            "preserves_observations": True,
            "yearly": gap_rows,
            "monthly": month_rows,
        },
    }


def run_mcp_validation(request: Mapping[str, Any]) -> Dict[str, Any]:
    """Run the complete v1.0 workflow from file paths and JSON-like options.

    Required request keys are ``observation_files`` and ``reference_files``.
    The function is deliberately independent from WindProPlatform modules.
    """

    config = MCPConfig.from_mapping(request)
    validate_input_files(config)
    observation_files = list(config.observation_files)
    reference_files = list(config.reference_files)
    target_column = config.target_column
    train_year = config.train_year
    test_year = config.test_year
    ranking_year = config.ranking_year
    # The thesis rose is always reconstructed with 16 directional sectors.
    # Speed-model selection remains independent from this directional policy.
    direction_sector_count = 16
    run_id = str(config.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]))
    output_dir = Path(config.output_dir or (Path.cwd() / "runs" / run_id)).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    power_curve_path = config.power_curve_file
    power_curve = PowerCurve.from_file(str(power_curve_path)) if power_curve_path else None
    turbine_count = config.turbine_count
    losses_pct = config.losses_pct

    observations, observation_metadata = read_certe_files(
        observation_files,
        target_column=target_column,
        timezone=config.observation_timezone,
        target_height_m=config.target_height_m,
    )
    is_native_certe = bool(observation_metadata.get("files")) and all(
        row.get("format") == "certe" for row in observation_metadata.get("files", [])
    )
    training_observation_baseline = (
        _training_observation_baseline(
            observations,
            training_year=train_year,
            observation_timezone=config.observation_timezone,
        )
        if is_native_certe else {}
    )
    direction_column = "wind_direction_obs" if "wind_direction_obs" in observations.columns else None
    observations_qc, observation_qc_report = quality_control(
        observations,
        speed_column="wind_speed_obs",
        direction_column=direction_column,
        expected_minutes=(
            10
            if (observation_metadata.get("files") or [{}])[0].get("format") == "certe"
            else None
        ),
        max_wind_speed=config.max_wind_speed,
        max_jump=config.max_jump,
        max_persistence=config.max_persistence,
    )
    observation_valid = observations_qc.loc[observations_qc["quality_eligible"]].copy()
    observation_valid = observation_valid[[c for c in observations.columns if c in observation_valid.columns]]

    # The height encoded by the actually selected column is authoritative.
    # Fall back to the user declaration only when the column has no height.
    observation_height = observation_metadata.get("measurement_height_m")
    if observation_height is None:
        observation_height = config.observation_height_m
    if observation_height is None:
        raise ValueError("The CERTE target column does not declare a measurement height")
    try:
        vertical_profile = fit_vertical_profile(
            observation_valid,
            observation_height_m=float(observation_height),
            target_height_m=config.target_height_m,
            training_year=train_year,
            max_wind_speed=config.max_wind_speed,
        )
    except ValueError as error:
        if "at least two mean wind-speed heights" not in str(error):
            raise
        # User observations frequently have a single anemometer height. Use a
        # declared, auditable neutral-terrain exponent instead of pretending a
        # shear profile was inferred from unavailable measurements.
        vertical_profile = VerticalProfile(
            target_height_m=float(config.target_height_m),
            observation_height_m=float(observation_height),
            alpha=0.143,
            rows_used=0,
            profile_points=0,
            alpha_p10=0.143,
            alpha_p50=0.143,
            alpha_p90=0.143,
            source_columns={str(observation_height): "wind_speed_obs"},
        )
    observation_valid, observation_adjustment = normalize_observation_height(
        observation_valid,
        source_height_m=float(observation_height),
        profile=vertical_profile,
    )

    references = read_reference_files(reference_files)
    aligned_by_name: Dict[str, pd.DataFrame] = {}
    reference_qc_reports: Dict[str, Dict[str, object]] = {}
    reference_metadata: Dict[str, Dict[str, object]] = {}
    reference_adjustments: Dict[str, Dict[str, object]] = {}
    clean_references_by_name: Dict[str, ReferenceData] = {}
    for reference in references:
        reference, reference_adjustment = normalize_reference_height(reference, vertical_profile)
        reference_adjustments[reference.name] = reference_adjustment
        ref_direction = "wind_direction_ref" if "wind_direction_ref" in reference.frame.columns else None
        reference_qc, reference_report = quality_control(
            reference.frame,
            speed_column="wind_speed_ref",
            direction_column=ref_direction,
            expected_minutes=60,
            max_wind_speed=config.max_wind_speed,
            max_jump=config.max_jump,
            max_persistence=config.max_persistence,
        )
        reference_qc_reports[reference.name] = reference_report
        reference_metadata[reference.name] = reference.metadata
        reference_valid = reference_qc.loc[reference_qc["quality_eligible"]].copy()
        reference_valid = reference_valid[[c for c in reference.frame.columns if c in reference_valid.columns]]
        clean_reference = ReferenceData(reference.name, reference_valid, reference.metadata)
        clean_references_by_name[reference.name] = clean_reference
        aligned_by_name[reference.name] = align_observations_and_reference(observation_valid, clean_reference)

    ranking = rank_references(aligned_by_name, ranking_year=ranking_year)
    if not ranking:
        raise ValueError("No reference produced concurrent data for ranking")
    eligible_ranking = [row for row in ranking if int(row.get("N", 0)) >= 2]
    if not eligible_ranking:
        raise ValueError("No reference has at least two concurrent pairs in the ranking year")
    selected_reference = str(config.selected_reference or eligible_ranking[0]["reference"])
    if selected_reference not in aligned_by_name:
        raise ValueError("selected_reference is not among the loaded references")
    try:
        direction_reference_selection = _direction_reference_selection(
            aligned_by_name, training_year=train_year, sector_count=direction_sector_count
        )
        selected_direction_reference = str(direction_reference_selection["selected_reference"])
    except ValueError as error:
        selected_direction_reference = selected_reference
        direction_reference_selection = {
            "selected_reference": selected_direction_reference,
            "training_year": int(train_year),
            "selection": "fallback_to_speed_reference_no_direction_holdout",
            "candidates": [],
            "warning": str(error),
        }

    validation_by_reference: Dict[str, Dict[str, Any]] = {}
    artifact_paths: Dict[str, str] = {}
    if training_observation_baseline:
        baseline_path = output_dir / "certe_2018_training_baseline.json"
        _write_json(baseline_path, training_observation_baseline)
        artifact_paths["certe_2018_training_baseline_json"] = str(baseline_path)
    for name, aligned in aligned_by_name.items():
        try:
            method_selection_forward = select_mcp_method(
                aligned, training_year=train_year, methods=EXTENDED_METHODS
            )
            method_selection_reverse = select_mcp_method(
                aligned, training_year=test_year, methods=EXTENDED_METHODS
            )
            forward_method = str(method_selection_forward["selected_method"])
            reverse_method = str(method_selection_reverse["selected_method"])
            forward = validate_direction(
                aligned, train_year=train_year, test_year=test_year, method=forward_method,
                direction_sector_count=direction_sector_count,
            )
            reverse = validate_direction(
                aligned, train_year=test_year, test_year=train_year, method=reverse_method,
                direction_sector_count=direction_sector_count,
            )
            comparison_2019 = compare_mcp_methods(
                aligned, train_year=train_year, test_year=test_year, methods=EXTENDED_METHODS
            )
            validation_by_reference[name] = {
                "method_selection_forward": method_selection_forward,
                "method_selection_reverse": method_selection_reverse,
                "model_comparison_2019": comparison_2019,
                "forward": {k: v for k, v in forward.items() if k != "predictions"},
                "reverse": {k: v for k, v in reverse.items() if k != "predictions"},
                "chart_data_forward": _validation_chart_data(
                    forward["predictions"], sector_count=direction_sector_count
                ),
                "chart_data_reverse": _validation_chart_data(
                    reverse["predictions"], sector_count=direction_sector_count
                ),
                "coverage": {
                    str(train_year): _period_coverage(aligned, train_year),
                    str(test_year): _period_coverage(aligned, test_year),
                },
            }
            artifact_paths[name + "_forward"] = _write_validation_artifacts(output_dir, _safe_filename(name) + "_forward", forward)
            artifact_paths[name + "_reverse"] = _write_validation_artifacts(output_dir, _safe_filename(name) + "_reverse", reverse)
            comparison_frame = pd.DataFrame([
                {"method": row.get("method"), "status": row.get("status"), **(row.get("metrics") or {})}
                for row in comparison_2019
            ])
            comparison_path = output_dir / (_safe_filename(name) + "_model_comparison_2019.csv")
            comparison_frame.to_csv(comparison_path, index=False)
            artifact_paths[name + "_model_comparison_2019"] = str(comparison_path)
            if power_curve is not None:
                energy_forward, energy_forward_rows = compare_period_energy(
                    forward["predictions"], power_curve, turbine_count=turbine_count, losses_pct=losses_pct
                )
                energy_reverse, energy_reverse_rows = compare_period_energy(
                    reverse["predictions"], power_curve, turbine_count=turbine_count, losses_pct=losses_pct
                )
                validation_by_reference[name]["energy_forward"] = energy_forward
                validation_by_reference[name]["energy_reverse"] = energy_reverse
                energy_forward_path = output_dir / (_safe_filename(name) + "_energy_forward.csv")
                energy_reverse_path = output_dir / (_safe_filename(name) + "_energy_reverse.csv")
                energy_forward_rows.to_csv(energy_forward_path, index=False)
                energy_reverse_rows.to_csv(energy_reverse_path, index=False)
                artifact_paths[name + "_energy_forward"] = str(energy_forward_path)
                artifact_paths[name + "_energy_reverse"] = str(energy_reverse_path)
        except ValueError as error:
            validation_by_reference[name] = {"error": str(error)}

    selected_validation = validation_by_reference[selected_reference]
    if "forward" not in selected_validation or "reverse" not in selected_validation:
        raise ValueError(
            "Selected reference %r cannot be validated in both directions: %s"
            % (selected_reference, selected_validation.get("error", "insufficient data"))
        )
    selected_aligned = aligned_by_name[selected_reference]
    multi_reference = evaluate_multireference(
        observation_valid,
        clean_references_by_name,
        train_year=train_year,
        test_year=test_year,
    )
    campaign_sensitivity_rows = campaign_sensitivity(
        selected_aligned,
        train_year=train_year,
        test_year=test_year,
    )
    multi_reference_path = output_dir / "multi_reference_comparison.json"
    campaign_sensitivity_path = output_dir / "campaign_sensitivity.csv"
    _write_json(multi_reference_path, multi_reference)
    pd.DataFrame([
        {
            **{key: value for key, value in row.items() if key != "metrics"},
            **(row.get("metrics") or {}),
        }
        for row in campaign_sensitivity_rows
    ]).to_csv(campaign_sensitivity_path, index=False)
    artifact_paths.update({
        "multi_reference_comparison_json": str(multi_reference_path),
        "campaign_sensitivity_csv": str(campaign_sensitivity_path),
    })
    ml_method_selection = select_ml_method(selected_aligned, training_year=train_year)
    ml_method_comparison_2019 = compare_ml_methods(selected_aligned, train_year=train_year, test_year=test_year)
    ml_validation_2019 = validate_ml_direction(
        selected_aligned,
        train_year=train_year,
        test_year=test_year,
        method=str(ml_method_selection["selected_method"]),
        direction_sector_count=direction_sector_count,
    )
    selected_validation["ml_method_selection_forward"] = ml_method_selection
    selected_validation["ml_model_comparison_2019"] = ml_method_comparison_2019
    selected_validation["ml_forward"] = {k: v for k, v in ml_validation_2019.items() if k != "predictions"}
    ml_forward_path = output_dir / (_safe_filename(selected_reference) + "_ml_forward.csv")
    ml_comparison_path = output_dir / (_safe_filename(selected_reference) + "_ml_model_comparison_2019.csv")
    _write_validation_artifacts(output_dir, _safe_filename(selected_reference) + "_ml_forward", ml_validation_2019)
    pd.DataFrame([
        {"method": row.get("method"), "status": row.get("status"), **(row.get("metrics") or {})}
        for row in ml_method_comparison_2019
    ]).to_csv(ml_comparison_path, index=False)
    artifact_paths.update({
        selected_reference + "_ml_forward": str(ml_forward_path),
        selected_reference + "_ml_model_comparison_2019": str(ml_comparison_path),
    })
    requested_backtest_folds = [(train_year, test_year)]
    backtesting = run_walk_forward_backtest(
        selected_aligned,
        requested_backtest_folds,
        mcp_methods=EXTENDED_METHODS,
    )
    rmse_only_method = backtesting.get("selected_method")
    speed_model_selection = select_speed_reconstruction_method(
        backtesting["summary"], preferred_method=SEASONAL_QUANTILE_METHOD
    )
    backtesting["rmse_only_selected_method"] = rmse_only_method
    backtesting["selected_method"] = speed_model_selection["selected_method"]
    backtesting["selected_family"] = speed_model_selection["selected_family"]
    backtesting["selection"] = speed_model_selection["selection"]
    backtesting_path = output_dir / "backtesting_multi_year.csv"
    pd.DataFrame(backtesting["summary"]).to_csv(backtesting_path, index=False)
    artifact_paths["backtesting_multi_year_csv"] = str(backtesting_path)
    reconstruction_readiness_by_reference: Dict[str, Dict[str, Any]] = {}
    for name, aligned in aligned_by_name.items():
        try:
            reconstruction_readiness_by_reference[name] = assess_reconstruction_readiness(aligned)
        except ValueError as error:
            reconstruction_readiness_by_reference[name] = {
                "status": "insufficient",
                "error": str(error),
            }
    reconstruction_readiness = reconstruction_readiness_by_reference[selected_reference]
    selected_method_selection = selected_validation["method_selection_forward"]
    selected_reconstruction_method = str(speed_model_selection["selected_method"])

    selected_direction_aligned = aligned_by_name[selected_direction_reference]
    direction_training = selected_direction_aligned.loc[
        selected_direction_aligned.index.year == int(train_year),
        [column for column in ("wind_direction_obs", "wind_direction_ref")
         if column in selected_direction_aligned.columns],
    ]
    if {"wind_direction_obs", "wind_direction_ref"}.issubset(direction_training.columns):
        direction_training = direction_training.dropna(
            subset=["wind_direction_obs", "wind_direction_ref"]
        )
        direction_model = fit_seasonal_sector_direction_model(
            direction_training["wind_direction_obs"],
            direction_training["wind_direction_ref"],
            direction_training.index,
            n_sectors=direction_sector_count,
        )
    else:
        direction_model = {
            "method": "seasonal_sectorwise_circular_offset",
            "purpose": "wind_rose_direction_only",
            "seasonality": "unavailable",
            "n_sectors": direction_sector_count,
            "offset_deg": 0.0,
            "global_offset_deg": 0.0,
            "N_train": 0,
            "annual_model": {"n_sectors": direction_sector_count, "sectors": []},
            "monthly_models": {},
            "mae_before_deg": None,
            "mae_after_deg": None,
        }
    direction_model["selected_reference"] = selected_direction_reference
    direction_model["reference_selection"] = direction_reference_selection["selection"]

    # Rebuild the displayed validation with the actual winning speed model.
    # Direction always follows its own 16-sector model and cannot change this
    # speed selection.
    if selected_reconstruction_method.startswith("ml_"):
        final_forward = validate_ml_direction(
            selected_aligned, train_year, test_year, selected_reconstruction_method,
            direction_sector_count=direction_sector_count,
        )
        final_reverse = validate_ml_direction(
            selected_aligned, test_year, train_year, selected_reconstruction_method,
            direction_sector_count=direction_sector_count,
        )
    else:
        final_forward = validate_direction(
            selected_aligned, train_year, test_year, selected_reconstruction_method,
            direction_sector_count=direction_sector_count,
        )
        final_reverse = validate_direction(
            selected_aligned, test_year, train_year, selected_reconstruction_method,
            direction_sector_count=direction_sector_count,
        )
    # The speed winner and direction winner are intentionally independent.
    # Replace only the forward rose bearings with the 2018-trained seasonal
    # model from the best directional reference; speed predictions stay intact.
    forward_predictions = final_forward["predictions"]
    forward_index = pd.DatetimeIndex(pd.to_datetime(
        forward_predictions["timestamp"], errors="coerce", utc=True
    ))
    if "wind_direction_ref" in selected_direction_aligned.columns:
        direction_reference_test = selected_direction_aligned["wind_direction_ref"].reindex(forward_index)
        forward_predictions["reference_wind_direction_raw"] = direction_reference_test.to_numpy()
        forward_predictions["predicted_wind_direction"] = apply_seasonal_sector_direction_model(
            direction_reference_test, forward_index, direction_model
        )
    if "wind_direction_obs" in selected_direction_aligned.columns:
        forward_predictions["observed_wind_direction"] = selected_direction_aligned[
            "wind_direction_obs"
        ].reindex(forward_index).to_numpy()
    final_forward["model"]["rose_direction_model"] = direction_model
    selected_validation["forward"] = {key: value for key, value in final_forward.items() if key != "predictions"}
    selected_validation["reverse"] = {key: value for key, value in final_reverse.items() if key != "predictions"}
    selected_validation["chart_data_forward"] = _validation_chart_data(
        final_forward["predictions"], sector_count=direction_sector_count
    )
    forward_roses = selected_validation["chart_data_forward"].get("wind_roses")
    if forward_roses:
        forward_roses["note"] = (
            f"Validación 2019 sobre horas concurrentes. Dirección reconstruida desde "
            f"{selected_direction_reference} con 16 sectores y calibración mensual entrenada "
            f"exclusivamente con 2018; mayo de 2019 no se presenta como medición."
        )
    selected_validation["chart_data_reverse"] = _validation_chart_data(
        final_reverse["predictions"], sector_count=direction_sector_count
    )
    artifact_paths[selected_reference + "_forward"] = _write_validation_artifacts(
        output_dir, _safe_filename(selected_reference) + "_forward", final_forward
    )
    artifact_paths[selected_reference + "_reverse"] = _write_validation_artifacts(
        output_dir, _safe_filename(selected_reference) + "_reverse", final_reverse
    )
    reconstruction_columns = ["wind_speed_obs", "wind_speed_ref"]
    reconstruction_training = selected_aligned.loc[
        selected_aligned.index.year == int(train_year), reconstruction_columns
    ].dropna(subset=["wind_speed_obs", "wind_speed_ref"])
    if selected_reconstruction_method.startswith("ml_"):
        reconstruction_model = fit_ml_model(
            selected_aligned,
            selected_reconstruction_method.removeprefix("ml_"),
            years=[train_year],
        )
        reconstruction_model_summary = {
            "method": selected_reconstruction_method,
            "features": reconstruction_model.feature_columns,
            "training_year": int(train_year),
            "training_pairs": int(len(reconstruction_training)),
            "training_policy": "fixed_single_year_no_test_leakage",
            "selection_basis": speed_model_selection["selection"],
        }
    else:
        reconstruction_model = fit_mcp_model(
            reconstruction_training["wind_speed_ref"],
            reconstruction_training["wind_speed_obs"],
            method=selected_reconstruction_method,
            timestamps=reconstruction_training.index,
        )
        reconstruction_model_summary = {
            **reconstruction_model,
            "training_year": int(train_year),
            "training_pairs": int(len(reconstruction_training)),
            "training_policy": "fixed_single_year_no_test_leakage",
            "selection_basis": speed_model_selection["selection"],
        }
    selected_backtest_row = next(
        (row for row in backtesting.get("summary", []) if row.get("method") == selected_reconstruction_method),
        {},
    )
    uncertainty = {
        "error_p05": selected_backtest_row.get("error_p05"),
        "error_p95": selected_backtest_row.get("error_p95"),
        "interval_level": selected_backtest_row.get("interval_level"),
    }
    reconstruction_frame, reconstruction_summary, reconstruction_annual = reconstruct_historical(
        observation_valid,
        clean_references_by_name[selected_reference],
        reconstruction_model,
        uncertainty=uncertainty,
        direction_offset_deg=float(direction_model["offset_deg"] or 0.0),
        direction_model=direction_model,
        direction_reference=clean_references_by_name[selected_direction_reference],
    )
    # Turbulence is not reconstructed from the hourly rolling variability.
    # Preserve the CERTE logger's native 10-minute mean/std statistics and fit
    # an explicitly modelled long-term extension using the same fixed 2018
    # training policy as the thesis MCP workflow.
    reconstruction_frame, turbulence_products = build_turbulence_products(
        observations,
        reconstruction_frame,
        training_year=train_year,
        validation_year=test_year,
        timezone=config.observation_timezone,
        measurement_height_m=observation_height,
        target_height_m=config.target_height_m,
    )
    historical_weibull = {
        "fit_method": "two_parameter_Weibull_MLE_floc_0",
        "training_year": int(train_year),
        "validation_year": int(test_year),
        "selected_reference": selected_reference,
        "selected_direction_reference": selected_direction_reference,
        "selected_speed_method": selected_reconstruction_method,
        "seasonality": reconstruction_model_summary.get("seasonality"),
        "model_hindcast": {
            **fit_weibull_mle(reconstruction_frame["model_wind_speed"], minimum_points=20),
            "source": "2018_month_conditioned_speed_model_applied_to_full_selected_reanalysis_history",
        },
        "project_series": {
            **fit_weibull_mle(reconstruction_frame["long_term_wind_speed"], minimum_points=20),
            "source": "CERTE_observed_where_valid_else_model_hindcast",
        },
        "observed_campaign": {
            **fit_weibull_mle(reconstruction_frame["observed_wind_speed"], minimum_points=20),
            "source": "valid_CERTE_observations_only",
        },
        "validation_2019": {
            key: selected_validation["forward"].get("metrics", {}).get(key)
            for key in (
                "observed_weibull_k", "predicted_weibull_k", "delta_weibull_k",
                "observed_weibull_lambda", "predicted_weibull_lambda", "delta_weibull_lambda",
                "KS", "N",
            )
        },
        "note": (
            "El Weibull histórico modelado se ajusta al hindcast producido al aplicar el Quantile Mapping "
            "estacional por mes, entrenado sólo con 2018, a todo el registro de la referencia seleccionada. La serie del "
            "proyecto conserva CERTE donde existe y usa el hindcast únicamente para completar huecos."
        ),
    }
    reconstruction_summary["weibull"] = historical_weibull
    reconstruction_path = output_dir / "historical_reconstruction.csv"
    reconstruction_annual_path = output_dir / "historical_reconstruction_annual.csv"
    reconstruction_summary_path = output_dir / "reconstruction_summary.json"
    turbulence_path = output_dir / "historical_turbulence.csv"
    measured_turbulence_path = output_dir / "measured_turbulence_10min.csv"
    turbulence_summary_path = output_dir / "turbulence_summary.json"
    reconstruction_frame.to_csv(reconstruction_path, index=False)
    reconstruction_annual.to_csv(reconstruction_annual_path, index=False)
    _write_json(reconstruction_summary_path, reconstruction_summary)
    turbulence_columns = [
        column for column in (
            "timestamp", "long_term_wind_speed", "wind_direction",
            "turbulence_intensity_p50", "turbulence_intensity_p90",
            "sigma_u_p50", "sigma_u_p90", "turbulence_model_level",
            "turbulence_data_source",
        ) if column in reconstruction_frame.columns
    ]
    reconstruction_frame[turbulence_columns].to_csv(turbulence_path, index=False)
    measured_turbulence_records(
        observations, timezone=config.observation_timezone
    ).to_csv(measured_turbulence_path, index=False)
    _write_json(turbulence_summary_path, turbulence_products)
    annual_hindcast_chart = {
        "years": reconstruction_annual["year"].astype(int).tolist(),
        "long_term": reconstruction_annual["mean_long_term_wind_speed"].where(
            reconstruction_annual["mean_long_term_wind_speed"].notna(), None
        ).tolist(),
        "measured": reconstruction_annual["mean_observed_wind_speed"].where(
            reconstruction_annual["mean_observed_wind_speed"].notna()
            & (reconstruction_annual["observed_hours"] >= 0.90 * reconstruction_annual["hours"]),
            None,
        ).tolist(),
        "measured_hours": reconstruction_annual["observed_hours"].astype(int).tolist(),
        "measured_coverage_pct": (
            reconstruction_annual["observed_hours"] / reconstruction_annual["hours"].clip(lower=1) * 100.0
        ).tolist(),
        "estimated": reconstruction_annual["mean_model_wind_speed"].where(
            reconstruction_annual["mean_model_wind_speed"].notna(), None
        ).tolist(),
    }
    reconstruction_visualization = _reconstruction_visualization(
        reconstruction_frame,
        reconstruction_annual,
        observation_metadata.get("years", []),
    )
    gap_treatment_path = output_dir / "m04_gap_treatment_monthly.csv"
    direction_correction_path = output_dir / "direction_correction.json"
    direction_ranking_path = output_dir / "direction_reference_ranking.csv"
    pd.DataFrame(reconstruction_visualization["gap_treatment"]["monthly"]).to_csv(
        gap_treatment_path, index=False
    )
    _write_json(direction_correction_path, direction_model)
    pd.DataFrame(direction_reference_selection.get("candidates", [])).to_csv(
        direction_ranking_path, index=False
    )
    artifact_paths.update({
        "historical_reconstruction_csv": str(reconstruction_path),
        "historical_reconstruction_annual_csv": str(reconstruction_annual_path),
        "reconstruction_summary_json": str(reconstruction_summary_path),
        "historical_turbulence_csv": str(turbulence_path),
        "measured_turbulence_10min_csv": str(measured_turbulence_path),
        "turbulence_summary_json": str(turbulence_summary_path),
        "m04_gap_treatment_csv": str(gap_treatment_path),
        "direction_correction_json": str(direction_correction_path),
        "direction_reference_ranking_csv": str(direction_ranking_path),
    })
    ranking_frame = pd.DataFrame(ranking)
    ranking_path = output_dir / "reference_ranking.csv"
    ranking_frame.to_csv(ranking_path, index=False)
    _write_json(output_dir / "observation_qc.json", observation_qc_report)
    _write_json(output_dir / "reference_qc.json", reference_qc_reports)

    config_snapshot = config.as_dict()
    config_snapshot["run_id"] = run_id
    config_snapshot["output_dir"] = str(output_dir.resolve())
    config_snapshot["direction_sector_count"] = direction_sector_count
    config_snapshot["effective_target_column"] = observation_metadata.get("target_column")
    config_snapshot["effective_observation_height_m"] = observation_height
    result: Dict[str, Any] = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "software": {"name": "WindMCP", "version": VERSION},
        "config": config_snapshot,
        "input_manifest": _file_manifest(observation_files + reference_files + ([str(power_curve_path)] if power_curve_path else [])),
        "vertical_profile": vertical_profile.as_dict(),
        "vertical_adjustments": {
            "observations": observation_adjustment,
            "references": reference_adjustments,
        },
        "reconstruction_readiness": reconstruction_readiness,
        "reconstruction_readiness_by_reference": reconstruction_readiness_by_reference,
        "mcp_method_selection": selected_method_selection,
        "mcp_method_comparison_2019": selected_validation["model_comparison_2019"],
        "ml_method_selection": selected_validation["ml_method_selection_forward"],
        "ml_model_comparison_2019": selected_validation["ml_model_comparison_2019"],
        "ml_validation_2019": selected_validation["ml_forward"],
        "multi_reference": multi_reference,
        "campaign_sensitivity": campaign_sensitivity_rows,
        "backtesting_multi_year": backtesting,
        "speed_model_selection": speed_model_selection,
        "historical_reconstruction": reconstruction_summary,
        "historical_reconstruction_model": reconstruction_model_summary,
        "direction_correction": direction_model,
        "wind_rose_direction_model": direction_model,
        "direction_reference_selection": direction_reference_selection,
        "weibull_provenance": historical_weibull,
        "turbulence": turbulence_products,
        "uncertainty": uncertainty,
        "selected_reference": selected_reference,
        "selected_direction_reference": selected_direction_reference,
        "observation_metadata": observation_metadata,
        "reference_metadata": reference_metadata,
        "quality_control": {
            "observations": observation_qc_report,
            "references": reference_qc_reports,
        },
        "coverage": selected_validation.get("coverage", {}),
        "reference_ranking": ranking,
        "validation_forward": selected_validation.get("forward", {}),
        "validation_reverse": selected_validation.get("reverse", {}),
        "visualization": {
            "validation_forward": selected_validation.get("chart_data_forward", {}),
            "validation_reverse": selected_validation.get("chart_data_reverse", {}),
            "training_observation_baseline": training_observation_baseline,
            "annual_hindcast": annual_hindcast_chart,
            **reconstruction_visualization,
        },
        "energy_forward": selected_validation.get("energy_forward", {}),
        "energy_reverse": selected_validation.get("energy_reverse", {}),
        "validation_by_reference": validation_by_reference,
        "artifacts": {
            "reference_ranking_csv": str(ranking_path),
            "observation_qc_json": str(output_dir / "observation_qc.json"),
            "reference_qc_json": str(output_dir / "reference_qc.json"),
            "vertical_profile_json": str(output_dir / "vertical_profile.json"),
            "reconstruction_readiness_json": str(output_dir / "reconstruction_readiness.json"),
            **artifact_paths,
        },
    }
    _write_json(output_dir / "vertical_profile.json", result["vertical_profile"] | {
        "observation_adjustment": observation_adjustment,
        "reference_adjustments": reference_adjustments,
    })
    _write_json(output_dir / "reconstruction_readiness.json", {
        "selected_reference": selected_reference,
        "selected": reconstruction_readiness,
        "by_reference": reconstruction_readiness_by_reference,
    })
    manifest_path = output_dir / "run_manifest.json"
    _write_json(manifest_path, {
        "software": result["software"],
        "run_id": run_id,
        "created_at": result["created_at"],
        "config": result["config"],
        "input_manifest": result["input_manifest"],
    })
    result["artifacts"]["run_manifest_json"] = str(manifest_path)
    result["artifacts"]["result_json"] = str(output_dir / "result.json")
    _write_json(output_dir / "result.json", result)
    report_path = output_dir / "windmcp_final_report.html"
    write_final_report(result, report_path)
    result["artifacts"]["final_report_html"] = str(report_path)
    _write_json(output_dir / "result.json", result)
    return _jsonable(result)


def _safe_filename(value: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_." else "_" for character in value)
    return safe[:120] or "reference"

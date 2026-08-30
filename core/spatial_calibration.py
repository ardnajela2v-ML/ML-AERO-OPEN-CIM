"""Anchored spatial MCP calibration and physically informed Kriging.

The temporal climate is supplied by a published MCP hindcast at M04.  ERA5
and MERRA-2 nodes only contribute their relative spatial anomalies.  This is
deliberately different from fitting an independent temporal MCP at every node:
the calibrated nodes retain gradients instead of becoming copies of M04.

All angles in this module are meteorological directions (the direction the
wind comes *from*), clockwise from true north.  Angles are never kriged
directly; physical ``u/v`` components are used instead.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from scipy.stats import weibull_min

from core.kriging import (
    SECTOR_COUNT,
    SECTOR_LABELS,
    _haversine_km,
    _legacy_prefit_economics,
    fit_power_curve_gauss4,
    kriging_grid,
    kriging_interp,
    parse_turbine_filename,
)
from windmcp.data import read_windnavigator_file, resample_hourly
from windmcp.mcp import fit_linear, predict_linear


METHOD = "anchored_spatial_anomaly_v1"
PROJECT_REFERENCE_HEIGHT_M = 100.0
PROJECT_REFERENCE_ROUGHNESS_M = 0.03
DEFAULT_TARGET_HEIGHT_M = 80.0
LOW_WIND_OFFSET_MS = 0.5
MIN_PROVIDER_NODES = 3
MIN_CLIMATOLOGY_YEARS = 10
MIN_ANNUAL_COVERAGE = 0.90


def wrap_degrees(values: Iterable[float] | float) -> np.ndarray:
    """Normalize angles to ``[0, 360)``."""

    return np.mod(np.asarray(values, dtype=float), 360.0)


def circular_difference(values: Iterable[float], reference: Iterable[float]) -> np.ndarray:
    """Signed smallest angular difference in ``[-180, 180)`` degrees."""

    return (np.asarray(values, dtype=float) - np.asarray(reference, dtype=float) + 180.0) % 360.0 - 180.0


def meteorological_to_uv(speed: Iterable[float], direction_deg: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    """Convert meteorological *from* direction to east/north flow components."""

    speed_array = np.asarray(speed, dtype=float)
    radians = np.deg2rad(np.asarray(direction_deg, dtype=float))
    return -speed_array * np.sin(radians), -speed_array * np.cos(radians)


def uv_to_meteorological(u: Iterable[float], v: Iterable[float]) -> np.ndarray:
    """Convert east/north flow components to meteorological *from* direction."""

    return wrap_degrees(np.rad2deg(np.arctan2(-np.asarray(u, dtype=float), -np.asarray(v, dtype=float))))


def log_profile_factor(height_from_m: float, z0_from_m: float,
                       height_to_m: float, z0_to_m: float) -> float:
    """Ratio used to strip/reapply roughness with a neutral logarithmic profile."""

    h_from = float(height_from_m)
    h_to = float(height_to_m)
    z_from = max(1.0e-4, float(z0_from_m))
    z_to = max(1.0e-4, float(z0_to_m))
    if h_from <= z_from or h_to <= z_to:
        raise ValueError("Wind height must be greater than roughness length")
    return math.log(h_to / z_to) / math.log(h_from / z_from)


def generalize_speed(speed: Iterable[float], height_m: float, local_z0_m: Iterable[float] | float,
                     reference_height_m: float = PROJECT_REFERENCE_HEIGHT_M,
                     reference_z0_m: float = PROJECT_REFERENCE_ROUGHNESS_M) -> np.ndarray:
    """Strip local roughness and move speed to the single project reference."""

    values = np.asarray(speed, dtype=float)
    z0 = np.asarray(local_z0_m, dtype=float)
    factors = np.log(float(reference_height_m) / float(reference_z0_m)) / np.log(float(height_m) / np.maximum(z0, 1.0e-4))
    return values * factors


def reapply_speed(speed_reference: Iterable[float], target_height_m: float,
                  local_z0_m: Iterable[float] | float,
                  reference_height_m: float = PROJECT_REFERENCE_HEIGHT_M,
                  reference_z0_m: float = PROJECT_REFERENCE_ROUGHNESS_M) -> np.ndarray:
    """Reapply pixel/site roughness at a target height after interpolation."""

    values = np.asarray(speed_reference, dtype=float)
    z0 = np.asarray(local_z0_m, dtype=float)
    factors = np.log(float(target_height_m) / np.maximum(z0, 1.0e-4)) / np.log(float(reference_height_m) / float(reference_z0_m))
    return values * factors


def sector_indices(direction: Iterable[float], n_sectors: int = SECTOR_COUNT) -> np.ndarray:
    direction_array = wrap_degrees(direction)
    width = 360.0 / int(n_sectors)
    return (np.floor(((direction_array + width / 2.0) % 360.0) / width).astype(int) % int(n_sectors))


def sector_statistics(speed: Iterable[float], direction: Iterable[float],
                      density: Iterable[float] | float = 1.225,
                      n_sectors: int = SECTOR_COUNT) -> dict[str, Any]:
    """Return frequency, speed and energy summaries on north-centred sectors."""

    ws = np.asarray(speed, dtype=float)
    wd = np.asarray(direction, dtype=float)
    rho = np.broadcast_to(np.asarray(density, dtype=float), ws.shape)
    valid = np.isfinite(ws) & np.isfinite(wd) & np.isfinite(rho) & (ws >= 0.0)
    labels = [f"{int(i * 360.0 / n_sectors)}°" for i in range(n_sectors)]
    if not valid.any():
        return {
            "n_sectors": n_sectors, "labels": labels,
            "frequency_pct": [0.0] * n_sectors,
            "mean_speed": [0.0] * n_sectors,
            "power_density_wm2": [0.0] * n_sectors,
            "energy_pct": [0.0] * n_sectors,
            "dominant_direction_deg": None,
        }
    ws = ws[valid]
    wd = wd[valid]
    rho = rho[valid]
    indices = sector_indices(wd, n_sectors)
    counts = np.bincount(indices, minlength=n_sectors).astype(float)
    mean_speed = np.zeros(n_sectors, dtype=float)
    power = np.zeros(n_sectors, dtype=float)
    energy = np.zeros(n_sectors, dtype=float)
    instantaneous = 0.5 * rho * np.power(ws, 3)
    for idx in range(n_sectors):
        mask = indices == idx
        if mask.any():
            mean_speed[idx] = float(np.mean(ws[mask]))
            power[idx] = float(np.mean(instantaneous[mask]))
            energy[idx] = float(np.sum(instantaneous[mask]))
    frequency_pct = counts / max(1.0, counts.sum()) * 100.0
    energy_pct = energy / energy.sum() * 100.0 if energy.sum() > 0 else np.zeros(n_sectors)
    dominant = int(np.argmax(energy_pct if energy.sum() > 0 else frequency_pct))
    return {
        "n_sectors": int(n_sectors), "labels": labels,
        "frequency_pct": frequency_pct.tolist(),
        "mean_speed": mean_speed.tolist(),
        "power_density_wm2": power.tolist(),
        "energy_pct": energy_pct.tolist(),
        "dominant_direction_deg": float(dominant * 360.0 / n_sectors),
    }


def _as_hourly_utc(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Spatial calibration requires a DatetimeIndex")
    result = frame.copy().sort_index()
    if result.index.tz is None:
        result.index = result.index.tz_localize("UTC")
    else:
        result.index = result.index.tz_convert("UTC")
    result = result.loc[~result.index.duplicated(keep="last")]
    if len(result.index) > 1:
        median_step = result.index.to_series().diff().dropna().median()
        if median_step != pd.Timedelta(hours=1):
            result = resample_hourly(result)
    result.index = result.index.floor("h")
    result.index.name = "timestamp"
    return result


def _expected_hours(year: int) -> int:
    return 8784 if pd.Timestamp(year=year, month=1, day=1).is_leap_year else 8760


def annual_coverage(series: pd.Series) -> dict[int, float]:
    values = pd.to_numeric(series, errors="coerce")
    counts = values.groupby(values.index.year).count()
    return {int(year): float(count) / _expected_hours(int(year)) for year, count in counts.items()}


def longest_consecutive_years(years: Iterable[int]) -> list[int]:
    ordered = sorted({int(value) for value in years})
    if not ordered:
        return []
    best: list[int] = []
    current = [ordered[0]]
    for year in ordered[1:]:
        if year == current[-1] + 1:
            current.append(year)
        else:
            if len(current) > len(best):
                best = current
            current = [year]
    return current if len(current) > len(best) else best


def monthly_balanced_metrics(observed: pd.Series, estimated: pd.Series) -> dict[str, Any]:
    """Score months equally; an absent month is omitted, never imputed as zero."""

    joined = pd.concat([observed.rename("observed"), estimated.rename("estimated")], axis=1).dropna()
    monthly: list[dict[str, Any]] = []
    for month, group in joined.groupby(joined.index.month):
        if group.empty:
            continue
        error = group["estimated"].to_numpy() - group["observed"].to_numpy()
        monthly.append({
            "month": int(month), "n": int(len(group)),
            "rmse": float(np.sqrt(np.mean(np.square(error)))),
            "mae": float(np.mean(np.abs(error))),
            "bias": float(np.mean(error)),
        })
    if not monthly:
        return {"available": False, "months_used": [], "n": 0}
    return {
        "available": True,
        "n": int(len(joined)),
        "months_used": [row["month"] for row in monthly],
        "missing_months": [month for month in range(1, 13) if month not in {row["month"] for row in monthly}],
        "rmse_monthly_balanced": float(np.mean([row["rmse"] for row in monthly])),
        "mae_monthly_balanced": float(np.mean([row["mae"] for row in monthly])),
        "bias_monthly_balanced": float(np.mean([row["bias"] for row in monthly])),
        "monthly": monthly,
    }


def _idw_poi(frames: Sequence[pd.DataFrame], distances_km: Sequence[float]) -> pd.DataFrame:
    """Time-aware IDW at M04, including circular direction aggregation."""

    if not frames:
        raise ValueError("At least one reference node is required")
    weights = np.asarray([1.0 / max(0.5, float(distance)) ** 2 for distance in distances_km], dtype=float)
    speed = pd.concat([frame["wind_speed_ref"].rename(str(idx)) for idx, frame in enumerate(frames)], axis=1)
    valid = speed.notna().to_numpy(dtype=float)
    numerator = np.nansum(speed.to_numpy(dtype=float) * weights[None, :], axis=1)
    denominator = valid @ weights
    poi = pd.DataFrame(index=speed.index)
    poi["wind_speed_ref"] = np.divide(numerator, denominator, out=np.full_like(numerator, np.nan), where=denominator > 0)
    if any("wind_direction_ref" in frame for frame in frames):
        directions = pd.concat([
            frame.get("wind_direction_ref", pd.Series(np.nan, index=frame.index)).rename(str(idx))
            for idx, frame in enumerate(frames)
        ], axis=1).reindex(speed.index)
        radians = np.deg2rad(directions.to_numpy(dtype=float))
        finite = np.isfinite(radians).astype(float)
        sin_num = np.nansum(np.sin(radians) * weights[None, :], axis=1)
        cos_num = np.nansum(np.cos(radians) * weights[None, :], axis=1)
        dir_den = finite @ weights
        angle = wrap_degrees(np.rad2deg(np.arctan2(sin_num, cos_num)))
        angle[dir_den <= 0] = np.nan
        poi["wind_direction_ref"] = angle
    return poi


def _validation_weight(provider_poi: pd.DataFrame, observed: pd.DataFrame,
                       train_year: int = 2018, validation_year: int = 2019) -> dict[str, Any]:
    if observed is None or "wind_speed" not in observed:
        return {"available": False, "reason": "observed source unavailable"}
    joined = observed[["wind_speed"]].join(provider_poi[["wind_speed_ref"]], how="inner").dropna()
    train = joined.loc[joined.index.year == int(train_year)]
    validation = joined.loc[joined.index.year == int(validation_year)]
    if len(train) < 100 or len(validation) < 24:
        return {
            "available": False, "reason": "insufficient train/validation pairs",
            "n_train": int(len(train)), "n_validation": int(len(validation)),
        }
    model = fit_linear(train["wind_speed_ref"], train["wind_speed"])
    _, prediction = predict_linear(model, validation["wind_speed_ref"])
    metrics = monthly_balanced_metrics(
        validation["wind_speed"], pd.Series(prediction, index=validation.index)
    )
    return {
        **metrics, "model": model, "train_year": int(train_year),
        "validation_year": int(validation_year), "n_train": int(len(train)),
        "n_validation": int(len(validation)),
        "missing_month_policy": "absent_months_omitted_months_equally_weighted",
    }


def _provider_weights(validations: Mapping[str, Mapping[str, Any]], participating: Sequence[str]) -> dict[str, float]:
    if not participating:
        return {}
    inverse: dict[str, float] = {}
    for provider in participating:
        rmse = validations.get(provider, {}).get("rmse_monthly_balanced")
        if rmse is not None and np.isfinite(rmse) and float(rmse) > 0:
            inverse[provider] = 1.0 / float(rmse)
    if len(inverse) != len(participating):
        return {provider: 1.0 / len(participating) for provider in participating}
    total = sum(inverse.values())
    return {provider: value / total for provider, value in inverse.items()}


def _weibull(values: Iterable[float]) -> tuple[float | None, float | None]:
    sample = np.asarray(values, dtype=float)
    sample = sample[np.isfinite(sample) & (sample > 0)]
    if len(sample) < 100:
        return None, None
    try:
        k, _, scale = weibull_min.fit(sample, floc=0)
        return float(k), float(scale)
    except Exception:
        return None, None


def _sector_roughness(direction: np.ndarray, sector_z0: Sequence[float]) -> np.ndarray:
    z0 = np.asarray(list(sector_z0), dtype=float)
    if len(z0) != SECTOR_COUNT:
        z0 = np.full(SECTOR_COUNT, PROJECT_REFERENCE_ROUGHNESS_M)
    indices = sector_indices(np.where(np.isfinite(direction), direction, 0.0), SECTOR_COUNT)
    result = z0[indices]
    result[~np.isfinite(direction)] = float(np.nanmedian(z0))
    return result


def resolve_sector_roughness(lat: float, lon: float, fetch_m: float = 750.0) -> dict[str, Any]:
    """Sample WorldCover with one consistent fetch criterion for all sites.

    A failure is explicit and falls back to the declared project reference;
    topography is never substituted as a roughness or speed-up correction.
    """

    from core.terrain import get_landcover

    values: list[float] = []
    sources: list[str] = []
    for sector in range(SECTOR_COUNT):
        bearing = sector * 360.0 / SECTOR_COUNT
        rad = math.radians(bearing)
        sample_lat = float(lat) + (fetch_m / 111_000.0) * math.cos(rad)
        sample_lon = float(lon) + (fetch_m / (111_000.0 * max(0.2, math.cos(math.radians(lat))))) * math.sin(rad)
        landcover = get_landcover(sample_lat, sample_lon, z=11, window=7)
        value = landcover.get("z0")
        values.append(float(value) if value is not None else PROJECT_REFERENCE_ROUGHNESS_M)
        sources.append("WorldCover_2021" if value is not None else "project_reference_fallback")
    return {
        "z0_by_sector_m": values, "fetch_m": float(fetch_m),
        "source": "WorldCover_2021_sector_fetch",
        "fallback_sectors": int(sum(value != "WorldCover_2021" for value in sources)),
    }


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_station(station: Mapping[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    paths = list(station.get("_paths") or [])
    if len(paths) != 1:
        raise ValueError(f"Reference {station.get('station_id')} must resolve to exactly one file")
    reference = read_windnavigator_file(paths[0])
    frame = _as_hourly_utc(reference.frame)
    return dict(station), frame


def calibrate_spatial_nodes(
    anchor_frame: pd.DataFrame,
    observed_frame: pd.DataFrame | None,
    stations: Sequence[Mapping[str, Any]],
    *,
    poi: Mapping[str, float],
    target_height_m: float = DEFAULT_TARGET_HEIGHT_M,
    reference_height_m: float = PROJECT_REFERENCE_HEIGHT_M,
    reference_z0_m: float = PROJECT_REFERENCE_ROUGHNESS_M,
    train_year: int = 2018,
    validation_year: int = 2019,
    min_years: int = MIN_CLIMATOLOGY_YEARS,
    min_coverage: float = MIN_ANNUAL_COVERAGE,
    roughness_resolver: Callable[[float, float], Mapping[str, Any]] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Calibrate typed reanalysis nodes around a published M04 MCP hindcast."""

    if not str(target_height_m).strip() or float(target_height_m) <= 0:
        raise ValueError("target_height_m must be positive")
    anchor = _as_hourly_utc(anchor_frame)
    if "wind_speed" not in anchor:
        raise ValueError("Published MCP source requires wind_speed")
    observed = _as_hourly_utc(observed_frame) if observed_frame is not None else None
    typed = [dict(row) for row in stations if row.get("data_kind") == "reanalysis" and row.get("provider") in {"ERA5", "MERRA2"}]
    if not typed:
        raise ValueError("No typed ERA5/MERRA-2 nodes were supplied")

    # Do not let duplicate exports at the same provider/grid coordinate bias IDW.
    deduplicated: dict[tuple[str, float, float], dict[str, Any]] = {}
    excluded: list[dict[str, Any]] = []
    for row in typed:
        key = (str(row["provider"]), round(float(row["latitude"]), 5), round(float(row["longitude"]), 5))
        prior = deduplicated.get(key)
        if prior is None or int(row.get("size_bytes") or 0) > int(prior.get("size_bytes") or 0):
            if prior is not None:
                excluded.append({"station_id": prior["station_id"], "reason": "duplicate_provider_coordinate"})
            deduplicated[key] = row
        else:
            excluded.append({"station_id": row["station_id"], "reason": "duplicate_provider_coordinate"})
    typed = list(deduplicated.values())
    if progress_callback:
        progress_callback(8, f"Leyendo {len(typed)} nodos ERA5/MERRA-2 tipificados")

    loaded: list[tuple[dict[str, Any], pd.DataFrame]] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(typed)))) as pool:
        futures = {pool.submit(_load_station, row): row for row in typed}
        for future in as_completed(futures):
            row = futures[future]
            try:
                loaded.append(future.result())
            except Exception as error:
                excluded.append({"station_id": row.get("station_id"), "reason": "read_error", "detail": str(error)})
    if not loaded:
        raise ValueError("No reanalysis node could be read")

    anchor_coverage = annual_coverage(anchor["wind_speed"])
    coverage_by_station: dict[str, dict[int, float]] = {
        row["station_id"]: annual_coverage(frame["wind_speed_ref"])
        for row, frame in loaded
    }
    providers_available = sorted({row["provider"] for row, _ in loaded})
    candidate_years = sorted(year for year, coverage in anchor_coverage.items() if coverage >= min_coverage)
    valid_years = []
    for year in candidate_years:
        provider_counts = {
            provider: sum(
                coverage_by_station[row["station_id"]].get(year, 0.0) >= min_coverage
                for row, _ in loaded if row["provider"] == provider
            )
            for provider in providers_available
        }
        if any(count >= MIN_PROVIDER_NODES for count in provider_counts.values()):
            valid_years.append(year)
    period_years = longest_consecutive_years(valid_years)
    if len(period_years) < int(min_years):
        raise ValueError(
            f"The longest continuous common period has {len(period_years)} years; {min_years} are required"
        )
    start = pd.Timestamp(year=period_years[0], month=1, day=1, tz="UTC")
    end = pd.Timestamp(year=period_years[-1] + 1, month=1, day=1, tz="UTC") - pd.Timedelta(hours=1)
    anchor_period = anchor.loc[start:end]
    if progress_callback:
        progress_callback(28, f"Periodo común {period_years[0]}–{period_years[-1]} · {len(period_years)} años")

    eligible: dict[str, list[tuple[dict[str, Any], pd.DataFrame]]] = defaultdict(list)
    for row, frame in loaded:
        cov = coverage_by_station[row["station_id"]]
        minimum = min((cov.get(year, 0.0) for year in period_years), default=0.0)
        if minimum >= min_coverage:
            eligible[row["provider"]].append((row, frame.loc[start:end]))
        else:
            excluded.append({
                "station_id": row["station_id"], "provider": row["provider"],
                "reason": "coverage_below_threshold", "minimum_annual_coverage": float(minimum),
            })

    validations: dict[str, dict[str, Any]] = {}
    provider_poi: dict[str, pd.DataFrame] = {}
    for provider, rows in eligible.items():
        frames = [frame for _, frame in rows]
        distances = [
            _haversine_km(float(poi["lat"]), float(poi["lon"]), float(row["latitude"]), float(row["longitude"]))
            for row, _ in rows
        ]
        poi_frame = _idw_poi(frames, distances)
        provider_poi[provider] = poi_frame
        validations[provider] = _validation_weight(poi_frame, observed, train_year, validation_year)

    participating = sorted(provider for provider, rows in eligible.items() if len(rows) >= MIN_PROVIDER_NODES)
    if not participating:
        counts = {provider: len(rows) for provider, rows in eligible.items()}
        raise ValueError(f"Each official provider field needs at least three valid nodes: {counts}")
    weights = _provider_weights(validations, participating)
    if progress_callback:
        progress_callback(42, "Calibrando anomalías espaciales y dirección circular")

    roughness_resolver = roughness_resolver or (lambda _lat, _lon: {
        "z0_by_sector_m": [reference_z0_m] * SECTOR_COUNT,
        "fetch_m": None, "source": "project_reference_fallback", "fallback_sectors": SECTOR_COUNT,
    })
    anchor_direction = pd.to_numeric(anchor_period.get("wind_direction"), errors="coerce") if "wind_direction" in anchor_period else None
    anchor_speed = pd.to_numeric(anchor_period["wind_speed"], errors="coerce")
    anchor_roughness = dict(roughness_resolver(float(poi["lat"]), float(poi["lon"])))
    anchor_z0 = _sector_roughness(
        anchor_direction.to_numpy() if anchor_direction is not None else np.full(len(anchor_speed), np.nan),
        anchor_roughness.get("z0_by_sector_m", []),
    )
    anchor_generalized = generalize_speed(anchor_speed, target_height_m, anchor_z0, reference_height_m, reference_z0_m)
    anchor_u, anchor_v = meteorological_to_uv(
        anchor_generalized,
        anchor_direction.to_numpy() if anchor_direction is not None else np.full(len(anchor_speed), np.nan),
    )
    anchor_k, anchor_lambda = _weibull(anchor_generalized)
    anchor_sector = sector_statistics(
        anchor_generalized,
        anchor_direction.to_numpy() if anchor_direction is not None else np.full(len(anchor_speed), np.nan),
    )
    mcp_half_width = None
    if {"wind_speed_p05", "wind_speed_p95"}.issubset(anchor_period.columns):
        width = (pd.to_numeric(anchor_period["wind_speed_p95"], errors="coerce") -
                 pd.to_numeric(anchor_period["wind_speed_p05"], errors="coerce")) / 2.0
        mcp_half_width = float(width.dropna().mean()) if width.notna().any() else None

    calibrated_nodes: list[dict[str, Any]] = []
    for provider in sorted(eligible):
        poi_frame = provider_poi[provider]
        for row, frame in eligible[provider]:
            node_period = frame.loc[(frame.index >= start) & (frame.index <= end)]
            expected_period_hours = sum(_expected_hours(year) for year in period_years)
            node_coverage_pct = min(
                100.0,
                100.0 * float(node_period["wind_speed_ref"].notna().sum()) / max(1, expected_period_hours),
            )
            aligned = anchor_period[["wind_speed"]].join(
                poi_frame.add_prefix("poi_"), how="inner"
            ).join(frame.add_prefix("node_"), how="inner")
            required = ["wind_speed", "poi_wind_speed_ref", "node_wind_speed_ref"]
            aligned = aligned.dropna(subset=required)
            if aligned.empty:
                excluded.append({"station_id": row["station_id"], "reason": "no_anchor_overlap"})
                continue
            anomaly = np.log(aligned["node_wind_speed_ref"] + LOW_WIND_OFFSET_MS) - np.log(
                aligned["poi_wind_speed_ref"] + LOW_WIND_OFFSET_MS
            )
            training_anomaly = anomaly.loc[anomaly.index.year == int(train_year)].dropna()
            fit_sample = training_anomaly if len(training_anomaly) >= 100 else anomaly.dropna()
            q01, q99 = np.quantile(fit_sample, [0.01, 0.99])
            anomaly_limited = anomaly.clip(float(q01), float(q99))
            calibrated_speed = np.maximum(
                0.0,
                (aligned["wind_speed"] + LOW_WIND_OFFSET_MS) * np.exp(anomaly_limited) - LOW_WIND_OFFSET_MS,
            )
            if anchor_direction is not None and {"node_wind_direction_ref", "poi_wind_direction_ref"}.issubset(aligned.columns):
                node_delta = circular_difference(
                    aligned["node_wind_direction_ref"], aligned["poi_wind_direction_ref"]
                )
                calibrated_direction = wrap_degrees(
                    anchor_direction.reindex(aligned.index).to_numpy(dtype=float) + node_delta
                )
            elif "node_wind_direction_ref" in aligned:
                calibrated_direction = wrap_degrees(aligned["node_wind_direction_ref"])
            else:
                calibrated_direction = np.full(len(aligned), np.nan)
            roughness = dict(roughness_resolver(float(row["latitude"]), float(row["longitude"])))
            z0_series = _sector_roughness(calibrated_direction, roughness.get("z0_by_sector_m", []))
            generalized = generalize_speed(
                calibrated_speed, target_height_m, z0_series, reference_height_m, reference_z0_m
            )
            u, v = meteorological_to_uv(generalized, calibrated_direction)
            k_value, lambda_value = _weibull(generalized)
            density = np.full(len(generalized), 1.225)
            if {"node_temperature_ref", "node_pressure_ref_kpa"}.issubset(aligned.columns):
                temperature = pd.to_numeric(aligned["node_temperature_ref"], errors="coerce").to_numpy(dtype=float)
                # Windnavigator temperature can be Celsius or Kelvin.
                temperature_k = np.where(temperature < 170.0, temperature + 273.15, temperature)
                pressure = pd.to_numeric(aligned["node_pressure_ref_kpa"], errors="coerce").to_numpy(dtype=float) * 1000.0
                calculated_density = pressure / (287.05 * temperature_k)
                density = np.where(np.isfinite(calculated_density), calculated_density, 1.225)
            sectors = sector_statistics(generalized, calibrated_direction, density)
            power_density = float(np.nanmean(0.5 * density * np.power(generalized, 3)))
            calibrated_nodes.append({
                "station_id": row["station_id"], "display_name": row.get("display_name") or row["station_id"],
                "provider": provider, "latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
                "source_height_m": float((row.get("measurement_heights") or [np.nan])[0]),
                "target_height_m": float(target_height_m), "n_hours": int(len(aligned)),
                "coverage_pct": node_coverage_pct,
                "raw_mean_speed": float(aligned["node_wind_speed_ref"].mean()),
                "calibrated_mean_speed": float(np.nanmean(calibrated_speed)),
                "generalized_mean_speed": float(np.nanmean(generalized)),
                "generalized_mean_u": float(np.nanmean(u)), "generalized_mean_v": float(np.nanmean(v)),
                "gust_p995": float(np.nanpercentile(generalized, 99.5)),
                "weibull_k": k_value, "weibull_lambda": lambda_value,
                "air_density_mean": float(np.nanmean(density)), "power_density_wm2": power_density,
                "anomaly_log_q01": float(q01), "anomaly_log_q99": float(q99),
                "anomaly_training_year": int(train_year), "sectorial": sectors,
                "roughness": roughness, "qc_status": "included" if provider in participating else "diagnostic_only",
                "field_role": "official" if provider in participating else "diagnostic_only",
                "file_ids": list(row.get("file_ids") or []),
                "file_sha256": [_file_sha256(path) for path in row.get("_paths") or []],
            })

    provider_counts = {provider: sum(node["provider"] == provider for node in calibrated_nodes) for provider in providers_available}
    participating = sorted(provider for provider in participating if provider_counts.get(provider, 0) >= MIN_PROVIDER_NODES)
    weights = _provider_weights(validations, participating)
    if not participating:
        raise ValueError("Calibration left no provider with at least three valid nodes")

    anchor_record = {
        "station_id": "obs-certe-m04", "display_name": "M04 CERTE · hindcast MCP",
        "provider": "MCP", "latitude": float(poi["lat"]), "longitude": float(poi["lon"]),
        "target_height_m": float(target_height_m), "n_hours": int(anchor_speed.notna().sum()),
        "mean_speed": float(anchor_speed.mean()), "generalized_mean_speed": float(np.nanmean(anchor_generalized)),
        "generalized_mean_u": float(np.nanmean(anchor_u)), "generalized_mean_v": float(np.nanmean(anchor_v)),
        "gust_p995": float(np.nanpercentile(anchor_generalized, 99.5)),
        "weibull_k": anchor_k, "weibull_lambda": anchor_lambda,
        "air_density_mean": 1.225,
        "power_density_wm2": float(np.nanmean(0.5 * 1.225 * np.power(anchor_generalized, 3))),
        "sectorial": anchor_sector, "roughness": anchor_roughness,
        "mcp_uncertainty_half_width_ms": mcp_half_width,
        "qc_status": "spatial_anchor", "field_role": "anchor",
    }
    if progress_callback:
        progress_callback(88, "Calculando pesos ERA5/MERRA-2 y trazabilidad")
    return {
        "method": METHOD,
        "direction_convention": "meteorological_from_true_north_clockwise",
        "training_year": int(train_year), "validation_year": int(validation_year),
        "missing_validation_policy": "missing months omitted; available months equally weighted",
        "common_period": {
            "start": start.isoformat(), "end": end.isoformat(),
            "start_year": period_years[0], "end_year": period_years[-1],
            "years": period_years, "n_years": len(period_years),
            "minimum_annual_coverage_pct": float(min_coverage * 100.0),
        },
        "project_reference": {
            "height_m": float(reference_height_m), "roughness_z0_m": float(reference_z0_m),
            "roughness_method": "sector_log_law_worldcover_same_fetch",
            "orography_removed": False,
            "orographic_limitation": "M04 local orographic component remains in the spatial anchor",
        },
        "anchor": anchor_record,
        "nodes": calibrated_nodes,
        "excluded_nodes": excluded,
        "provider_counts": provider_counts,
        "participating_providers": participating,
        "provider_validation": validations,
        "provider_weights": weights,
        "qc": {
            "minimum_provider_nodes": MIN_PROVIDER_NODES,
            "loaded_nodes": len(loaded), "calibrated_nodes": len(calibrated_nodes),
            "excluded_nodes": len(excluded),
        },
        "topography_policy": {
            "screening": "DEM slope/aspect/exposure/RIX proxy is diagnostic only",
            "speed_correction": "none_without_imported_wasp_or_cfd_speedup",
            "advanced_import_supported": True,
        },
    }


def select_variogram_loo(lons: Sequence[float], lats: Sequence[float], values: Sequence[float],
                         requested: str = "auto") -> dict[str, Any]:
    """Select exponential/Gaussian and range using leave-one-node-out RMSE."""

    x = np.asarray(lons, dtype=float)
    y = np.asarray(lats, dtype=float)
    z = np.asarray(values, dtype=float)
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[valid], y[valid], z[valid]
    models = [requested] if requested in {"exponential", "gaussian"} else ["exponential", "gaussian"]
    if len(z) < 4:
        return {"model": models[0], "range_km": 50.0, "loo_rmse": None, "n": int(len(z)), "selection": "fallback_lt4"}
    distances = []
    for idx in range(len(z)):
        for other in range(idx + 1, len(z)):
            distances.append(_haversine_km(y[idx], x[idx], y[other], x[other]))
    median = float(np.median(distances)) if distances else 50.0
    ranges = sorted({max(10.0, min(300.0, median * factor)) for factor in (0.5, 1.0, 2.0)})
    candidates = []
    for model in models:
        for range_km in ranges:
            predictions = []
            actual = []
            for idx in range(len(z)):
                mask = np.arange(len(z)) != idx
                try:
                    predicted, _ = kriging_interp(
                        x[mask], y[mask], z[mask], np.asarray([[x[idx]]]), np.asarray([[y[idx]]]),
                        model=model, rng=range_km,
                    )
                    predictions.append(float(predicted[0, 0]))
                    actual.append(float(z[idx]))
                except Exception:
                    pass
            rmse = float(np.sqrt(np.mean(np.square(np.asarray(predictions) - np.asarray(actual))))) if predictions else float("inf")
            candidates.append({"model": model, "range_km": float(range_km), "loo_rmse": rmse, "n": len(predictions)})
    best = min(candidates, key=lambda row: row["loo_rmse"])
    return {**best, "selection": "leave_one_node_out", "candidates": candidates}


def _grid_with_range(lons: Sequence[float], lats: Sequence[float], values: Sequence[float], *,
                     n: int, model: str, range_km: float, bounds: Mapping[str, float] | None,
                     title: str, names: Sequence[str] | None = None) -> dict[str, Any]:
    lons_array = np.asarray(lons, dtype=float)
    lats_array = np.asarray(lats, dtype=float)
    values_array = np.asarray(values, dtype=float)
    valid = np.isfinite(lons_array) & np.isfinite(lats_array) & np.isfinite(values_array)
    lons_array, lats_array, values_array = lons_array[valid], lats_array[valid], values_array[valid]
    if len(values_array) < 3:
        raise ValueError(f"Kriging requires at least three values for {title}")
    if bounds:
        limits = {key: float(bounds[key]) for key in ("lat_min", "lat_max", "lon_min", "lon_max")}
    else:
        lon_margin = max(0.1, (lons_array.max() - lons_array.min()) * 0.15)
        lat_margin = max(0.1, (lats_array.max() - lats_array.min()) * 0.15)
        limits = {
            "lat_min": float(lats_array.min() - lat_margin), "lat_max": float(lats_array.max() + lat_margin),
            "lon_min": float(lons_array.min() - lon_margin), "lon_max": float(lons_array.max() + lon_margin),
        }
    xq, yq = np.meshgrid(
        np.linspace(limits["lon_min"], limits["lon_max"], int(n)),
        np.linspace(limits["lat_min"], limits["lat_max"], int(n)),
    )
    zq, variance = kriging_interp(lons_array, lats_array, values_array, xq, yq, model=model, rng=range_km)
    result = {
        "lon_grid": xq.tolist(), "lat_grid": yq.tolist(), "z": zq.tolist(),
        "variance": variance.tolist(), "bounds": limits,
        "z_min": float(np.nanmin(zq)), "z_max": float(np.nanmax(zq)), "n": int(n),
        "model": model, "range_km": float(range_km), "title": title,
    }
    if names is not None:
        valid_names = [name for name, keep in zip(names, valid) if keep]
        result["points"] = [
            {"lat": float(lat), "lon": float(lon), "value": float(value), "name": name}
            for lat, lon, value, name in zip(lats_array, lons_array, values_array, valid_names)
        ]
    return result


def _wrap_grid(template: Mapping[str, Any], values: np.ndarray, title: str) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    return {
        **{key: template[key] for key in ("lon_grid", "lat_grid", "bounds", "n", "model", "range_km") if key in template},
        "z": array.tolist(), "z_min": float(np.nanmin(array)), "z_max": float(np.nanmax(array)), "title": title,
    }


def terrain_screening(elevation: Sequence[Sequence[float]], bounds: Mapping[str, float]) -> dict[str, Any]:
    """DEM screening only.  None of these values multiplies wind speed."""

    dem = np.asarray(elevation, dtype=float)
    ny, nx = dem.shape
    dy_m = max(1.0, (float(bounds["lat_max"]) - float(bounds["lat_min"])) * 111_000.0 / max(1, ny - 1))
    mid_lat = (float(bounds["lat_max"]) + float(bounds["lat_min"])) / 2.0
    dx_m = max(1.0, (float(bounds["lon_max"]) - float(bounds["lon_min"])) * 111_000.0 * math.cos(math.radians(mid_lat)) / max(1, nx - 1))
    grad_y, grad_x = np.gradient(dem, dy_m, dx_m)
    slope = np.degrees(np.arctan(np.hypot(grad_x, grad_y)))
    aspect = wrap_degrees(np.degrees(np.arctan2(-grad_x, grad_y)))
    rix_proxy = np.clip(slope / 20.0 * 100.0, 0.0, 100.0)
    exposure_means = []
    for sector in range(SECTOR_COUNT):
        direction = sector * 360.0 / SECTOR_COUNT
        exposure = np.cos(np.deg2rad(circular_difference(aspect, direction))) * slope
        exposure_means.append(float(np.nanmean(exposure)))
    return {
        "slope_deg": slope, "aspect_deg": aspect, "rix_proxy_pct": rix_proxy,
        "exposure_sector_mean": exposure_means,
        "label": "DEM diagnostic screening; not CFD and not applied to wind speed",
    }


def _blend_grids(grids: Mapping[str, Mapping[str, Any]], weights: Mapping[str, float], key: str = "z") -> np.ndarray:
    used = [(provider, grid) for provider, grid in grids.items() if provider in weights]
    if not used:
        raise ValueError("No provider grids can be blended")
    return sum(float(weights[provider]) * np.asarray(grid[key], dtype=float) for provider, grid in used)


def build_calibrated_maps(
    calibration: Mapping[str, Any],
    *,
    turbines_folder: str,
    n_grid: int = 80,
    model: str = "auto",
    bounds: Mapping[str, float] | None = None,
    target_height_m: float = DEFAULT_TARGET_HEIGHT_M,
    physical: bool = True,
    demand_mwh: float = 0.0,
    discount_rate: float = 0.08,
    price_usd_kwh: float = 0.20,
    fx_mxn_per_usd: float = 20.0,
    project_years: int = 20,
    cost_model: Any = None,
    terrain_timeout_s: float = 20.0,
    orographic_speedup_grid: Sequence[Sequence[float]] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Build provider and ensemble resource maps from a saved calibration."""

    participating = list(calibration.get("participating_providers") or [])
    weights = {str(key): float(value) for key, value in (calibration.get("provider_weights") or {}).items()}
    anchor = dict(calibration["anchor"])
    nodes = list(calibration.get("nodes") or [])
    project_ref = calibration.get("project_reference") or {}
    h_ref = float(project_ref.get("height_m", PROJECT_REFERENCE_HEIGHT_M))
    z0_ref = float(project_ref.get("roughness_z0_m", PROJECT_REFERENCE_ROUGHNESS_M))
    provider_grids: dict[str, dict[str, Any]] = {}
    selections: dict[str, Any] = {}
    official_nodes = [node for node in nodes if node.get("field_role") == "official"]
    if bounds:
        map_bounds = {key: float(bounds[key]) for key in ("lat_min", "lat_max", "lon_min", "lon_max")}
    else:
        extent_rows = [anchor] + official_nodes
        extent_lats = np.asarray([float(row["latitude"]) for row in extent_rows], dtype=float)
        extent_lons = np.asarray([float(row["longitude"]) for row in extent_rows], dtype=float)
        lon_margin = max(0.1, float(np.ptp(extent_lons)) * 0.15)
        lat_margin = max(0.1, float(np.ptp(extent_lats)) * 0.15)
        map_bounds = {
            "lat_min": float(extent_lats.min() - lat_margin),
            "lat_max": float(extent_lats.max() + lat_margin),
            "lon_min": float(extent_lons.min() - lon_margin),
            "lon_max": float(extent_lons.max() + lon_margin),
        }
    if progress_callback:
        progress_callback(10, "Validación espacial leave-one-node-out")
    for provider in participating:
        provider_nodes = [node for node in nodes if node["provider"] == provider and node.get("field_role") == "official"]
        rows = [anchor] + provider_nodes
        lats = [float(row["latitude"]) for row in rows]
        lons = [float(row["longitude"]) for row in rows]
        names = [str(row["display_name"]) for row in rows]
        speed = [float(row["generalized_mean_speed"]) for row in rows]
        selection = select_variogram_loo(lons, lats, speed, requested=model)
        selections[provider] = selection
        chosen_model = str(selection["model"])
        range_km = float(selection["range_km"])
        fields = {}
        for field, title in (
            ("generalized_mean_speed", f"{provider} · viento generalizado"),
            ("generalized_mean_u", f"{provider} · componente u"),
            ("generalized_mean_v", f"{provider} · componente v"),
            ("gust_p995", f"{provider} · ráfaga P99.5"),
            ("weibull_k", f"{provider} · Weibull k"),
            ("weibull_lambda", f"{provider} · Weibull λ"),
            ("air_density_mean", f"{provider} · densidad del aire"),
        ):
            values = [row.get(field) for row in rows]
            fields[field] = _grid_with_range(
                lons, lats, values, n=n_grid, model=chosen_model, range_km=range_km,
                bounds=map_bounds, title=title, names=names if field == "generalized_mean_speed" else None,
            )
        sector_fields = {"frequency_pct": [], "mean_speed": [], "energy_pct": []}
        for sector_field in sector_fields:
            for sector in range(SECTOR_COUNT):
                values = [(row.get("sectorial") or {}).get(sector_field, [0.0] * SECTOR_COUNT)[sector] for row in rows]
                sector_fields[sector_field].append(_grid_with_range(
                    lons, lats, values, n=n_grid, model=chosen_model, range_km=range_km,
                    bounds=map_bounds, title=f"{provider} · {sector_field} · {SECTOR_LABELS[sector]}",
                ))
        provider_grids[provider] = {"fields": fields, "sectorial": sector_fields, "nodes": rows}

    # Direct comparison field: raw reanalysis only, no M04, no temporal MCP,
    # no anchored anomaly and no validation-derived provider weights.  Each
    # node is moved from its declared source height to the single project
    # reference height using the same neutral log profile and fixed z0_ref.
    raw_provider_grids: dict[str, dict[str, Any]] = {}
    raw_selections: dict[str, Any] = {}
    raw_node_records: list[dict[str, Any]] = []
    for provider in participating:
        provider_nodes = [node for node in official_nodes if node["provider"] == provider]
        if len(provider_nodes) < MIN_PROVIDER_NODES:
            continue
        raw_lats = [float(node["latitude"]) for node in provider_nodes]
        raw_lons = [float(node["longitude"]) for node in provider_nodes]
        raw_names = [str(node["display_name"]) for node in provider_nodes]
        raw_values = []
        for node in provider_nodes:
            source_height = float(node["source_height_m"])
            raw_speed = float(node["raw_mean_speed"])
            extrapolated = float(generalize_speed(
                [raw_speed], source_height, z0_ref,
                reference_height_m=h_ref, reference_z0_m=z0_ref,
            )[0])
            raw_values.append(extrapolated)
            raw_node_records.append({
                "station_id": node["station_id"], "display_name": node["display_name"],
                "provider": provider, "latitude": float(node["latitude"]),
                "longitude": float(node["longitude"]), "source_height_m": source_height,
                "raw_mean_speed_ms": raw_speed, "reference_height_m": h_ref,
                "raw_reference_speed_ms": extrapolated,
            })
        raw_selection = select_variogram_loo(raw_lons, raw_lats, raw_values, requested=model)
        raw_selections[provider] = raw_selection
        raw_provider_grids[provider] = _grid_with_range(
            raw_lons, raw_lats, raw_values,
            n=n_grid, model=str(raw_selection["model"]),
            range_km=float(raw_selection["range_km"]), bounds=map_bounds,
            title=f"{provider} raw extrapolado verticalmente a {h_ref:g} m",
            names=raw_names,
        )

    if not raw_provider_grids:
        raise ValueError("No provider has enough raw nodes for the direct comparison map")
    raw_equal_weights = {
        provider: 1.0 / len(raw_provider_grids) for provider in raw_provider_grids
    }
    raw_reference = np.maximum(0.0, _blend_grids(raw_provider_grids, raw_equal_weights))

    speed_generalized = _blend_grids(
        {provider: data["fields"]["generalized_mean_speed"] for provider, data in provider_grids.items()}, weights
    )
    u_grid = _blend_grids({provider: data["fields"]["generalized_mean_u"] for provider, data in provider_grids.items()}, weights)
    v_grid = _blend_grids({provider: data["fields"]["generalized_mean_v"] for provider, data in provider_grids.items()}, weights)
    gust_generalized = _blend_grids({provider: data["fields"]["gust_p995"] for provider, data in provider_grids.items()}, weights)
    weibull_k = np.maximum(0.45, _blend_grids({provider: data["fields"]["weibull_k"] for provider, data in provider_grids.items()}, weights))
    weibull_lambda_ref = np.maximum(0.01, _blend_grids({provider: data["fields"]["weibull_lambda"] for provider, data in provider_grids.items()}, weights))
    density_nodes = _blend_grids({provider: data["fields"]["air_density_mean"] for provider, data in provider_grids.items()}, weights)
    template = next(iter(provider_grids.values()))["fields"]["generalized_mean_speed"]
    ny, nx = speed_generalized.shape
    elevation = np.zeros((ny, nx), dtype=float)
    roughness = np.full((ny, nx), z0_ref, dtype=float)
    terrain_source = "project_reference_fallback"
    if physical:
        if progress_callback:
            progress_callback(55, "Muestreando WorldCover y DEM por píxel")
        try:
            from core.terrain import sample_grid_elevation, sample_grid_worldcover_z0
            grid_lats = np.linspace(template["bounds"]["lat_min"], template["bounds"]["lat_max"], ny).tolist()
            grid_lons = np.linspace(template["bounds"]["lon_min"], template["bounds"]["lon_max"], nx).tolist()
            with ThreadPoolExecutor(max_workers=2) as pool:
                elevation_future = pool.submit(sample_grid_elevation, grid_lats, grid_lons)
                elevation = np.asarray(elevation_future.result(timeout=terrain_timeout_s), dtype=float)
                roughness_future = pool.submit(
                    sample_grid_worldcover_z0, grid_lats, grid_lons, z0_ref
                )
                roughness = np.asarray(roughness_future.result(timeout=terrain_timeout_s), dtype=float)
            terrain_source = "DEM_screening_and_WorldCover_2021_roughness"
        except Exception:
            elevation = np.zeros((ny, nx), dtype=float)
            roughness = np.full((ny, nx), z0_ref, dtype=float)
            terrain_source = "project_reference_fallback_after_timeout"

    target_factor = np.log(float(target_height_m) / np.maximum(roughness, 1.0e-4)) / math.log(h_ref / z0_ref)
    wind = np.maximum(0.0, speed_generalized * target_factor)
    gust = np.maximum(wind, gust_generalized * target_factor)
    lambda_target = weibull_lambda_ref * target_factor
    # Elevation changes density, never speed.  Pressure-derived node density is
    # retained when DEM is unavailable.
    density = 1.225 * np.exp(-np.maximum(0.0, elevation) / 8400.0) if physical else density_nodes
    power_density = 0.5 * density * np.power(lambda_target, 3) * np.vectorize(math.gamma)(1.0 + 3.0 / weibull_k)

    orographic_applied = False
    if orographic_speedup_grid is not None:
        speedup = np.asarray(orographic_speedup_grid, dtype=float)
        if speedup.shape != wind.shape or not np.all(np.isfinite(speedup)) or np.any(speedup <= 0):
            raise ValueError("Imported WAsP/CFD speed-up grid must match the Kriging grid and be positive")
        wind *= speedup
        gust *= speedup
        lambda_target *= speedup
        power_density *= np.power(speedup, 3)
        orographic_applied = True

    direction = uv_to_meteorological(u_grid, v_grid)
    provider_speed_grids = {
        provider: np.asarray(data["fields"]["generalized_mean_speed"]["z"], dtype=float) * target_factor
        for provider, data in provider_grids.items()
    }
    provider_spread = np.std(np.stack(list(provider_speed_grids.values())), axis=0) if len(provider_speed_grids) > 1 else np.zeros_like(wind)
    kriging_variance = sum(
        weights[provider] ** 2 * np.asarray(data["fields"]["generalized_mean_speed"]["variance"], dtype=float)
        for provider, data in provider_grids.items()
    )
    node_std = float(np.std([node["generalized_mean_speed"] for node in nodes if node.get("field_role") == "official"]))
    kriging_uncertainty = np.sqrt(np.maximum(0.0, kriging_variance)) * node_std * target_factor
    mcp_uncertainty = float(anchor.get("mcp_uncertainty_half_width_ms") or 0.0)
    uncertainty = np.sqrt(np.square(kriging_uncertainty) + np.square(provider_spread) + mcp_uncertainty ** 2)

    sectorial = {"n_sectors": SECTOR_COUNT, "labels": SECTOR_LABELS, "frequency_pct": [], "mean_speed": [], "energy_pct": []}
    for field in ("frequency_pct", "mean_speed", "energy_pct"):
        arrays = []
        for sector in range(SECTOR_COUNT):
            value = sum(
                weights[provider] * np.asarray(data["sectorial"][field][sector]["z"], dtype=float)
                for provider, data in provider_grids.items()
            )
            if field == "mean_speed":
                value = value * target_factor
            arrays.append(value)
        stack = np.stack(arrays)
        if field in {"frequency_pct", "energy_pct"}:
            denominator = stack.sum(axis=0)
            stack = np.divide(stack * 100.0, denominator, out=np.zeros_like(stack), where=denominator > 0)
        sectorial[field] = [_wrap_grid(template, stack[idx], f"{field} · {SECTOR_LABELS[idx]}") for idx in range(SECTOR_COUNT)]

    screening = terrain_screening(elevation, template["bounds"])
    result = {
        "wind": _wrap_grid(template, wind, "Viento MCP espacial calibrado a altura objetivo"),
        "wind_neutral": _wrap_grid(template, speed_generalized, f"Viento generalizado ({h_ref:g} m, z₀={z0_ref:g} m)"),
        "wind_raw_reference": _wrap_grid(
            template, raw_reference,
            f"Reanálisis raw · extrapolación vertical directa a {h_ref:g} m",
        ),
        "wind_calibration_delta": _wrap_grid(
            template, speed_generalized - raw_reference,
            f"Diferencia calibrado − raw a {h_ref:g} m",
        ),
        "wind_direction": _wrap_grid(template, direction, "Dirección meteorológica · norte verdadero"),
        "gust": _wrap_grid(template, gust, "Ráfaga histórica P99.5 calibrada"),
        "weibull_k": _wrap_grid(template, weibull_k, "Weibull k · reconstrucción histórica"),
        "weibull_lambda": _wrap_grid(template, lambda_target, "Weibull λ · reconstrucción histórica"),
        "power_density": _wrap_grid(template, power_density, "Densidad de potencia histórica"),
        "uncertainty": _wrap_grid(template, uncertainty, "Incertidumbre MCP + Kriging + dispersión de proveedores"),
        "elevation": _wrap_grid(template, elevation, "Altitud DEM · diagnóstico"),
        "roughness": _wrap_grid(template, roughness, "Rugosidad WorldCover sectorial/local"),
        "air_density": _wrap_grid(template, density, "Densidad del aire por elevación"),
        "terrain_complexity": _wrap_grid(template, screening["rix_proxy_pct"], "RIX proxy · diagnóstico, no CFD"),
        "sectorial": sectorial,
        "providers": {},
        "provider_weights": weights,
        "variogram_selection": selections,
        "raw_reference": {
            "reference_height_m": h_ref, "reference_roughness_z0_m": z0_ref,
            "vertical_method": "neutral_log_profile_fixed_project_z0",
            "uses_mcp_anchor": False, "uses_horizontal_calibration": False,
            "uses_local_roughness": False, "uses_orographic_correction": False,
            "provider_weights": raw_equal_weights, "variogram_selection": raw_selections,
            "nodes": raw_node_records,
        },
        "calibration_id": calibration.get("calibration_id"),
        "data_source": calibration.get("data_source"),
        "study_revision": calibration.get("study_revision"),
        "n_sites": int(1 + sum(node.get("field_role") == "official" for node in nodes)),
        "input_nodes": [anchor] + nodes,
        "physical": bool(physical), "n_corrected": int(np.isfinite(roughness).sum()),
        "terrain": {
            "source": terrain_source, "screening_label": screening["label"],
            "exposure_sector_mean": screening["exposure_sector_mean"],
            "orography_speed_correction_applied": orographic_applied,
            "orography_source": "imported_WAsP_CFD_speedup" if orographic_applied else "none",
        },
        "provenance": {
            "method": METHOD, "direction": "meteorological_from_true_north_clockwise",
            "temporal_anchor": calibration.get("data_source"),
            "common_period": calibration.get("common_period"),
            "orographic_limitation": calibration.get("project_reference", {}).get("orographic_limitation"),
        },
    }
    for provider, grid_values in provider_speed_grids.items():
        key = "wind_" + provider.lower()
        result[key] = _wrap_grid(template, grid_values, f"{provider} calibrado y anclado a M04 MCP")
        result["providers"][provider] = {
            "weight": weights.get(provider), "wind_layer": key,
            "variogram": selections.get(provider),
            "node_count": sum(node["provider"] == provider and node.get("field_role") == "official" for node in nodes),
        }
    for provider, raw_grid in raw_provider_grids.items():
        result["wind_raw_" + provider.lower()] = {
            **raw_grid,
            "title": f"{provider} raw · extrapolación vertical directa a {h_ref:g} m",
        }

    # Sparse vector field for Leaflet.  Arrow orientation is a true-north
    # meteorological bearing, independent of raster row direction.
    lat_grid = np.asarray(template["lat_grid"], dtype=float)
    lon_grid = np.asarray(template["lon_grid"], dtype=float)
    step = max(1, int(n_grid) // 12)
    result["direction_vectors"] = [
        {"lat": float(lat_grid[j, i]), "lon": float(lon_grid[j, i]),
         "direction_deg": float(direction[j, i]), "speed_ms": float(wind[j, i])}
        for j in range(0, ny, step) for i in range(0, nx, step)
    ]

    # Preserve the existing economics/layout hand-off with the calibrated field.
    turbine_curves = []
    turbine_files = sorted(path for path in Path(turbines_folder).glob("*.csv") if path.is_file())
    last_economic = None
    for path in turbine_files:
        try:
            data = pd.read_csv(path, header=None).values
            x_raw = data[:, 0].astype(float)
            y_raw_kw = data[:, 1].astype(float)
            name, rotor_m, rated_kw = parse_turbine_filename(path.name)
            fitted_w = fit_power_curve_gauss4(x_raw, y_raw_kw * 1000.0, rated_kw * 1000.0)
            x_fit = np.linspace(float(x_raw.min()), float(x_raw.max()), 200)
            y_fit_kw = np.maximum(interp1d(x_raw, fitted_w / 1000.0, bounds_error=False, fill_value=0.0)(x_fit), 0.0)
            turbine_curves.append({
                "name": name, "file": path.name, "p_nom_kw": float(rated_kw), "rotor_m": float(rotor_m),
                "x_raw": x_raw.tolist(), "y_raw_kw": y_raw_kw.tolist(),
                "x_fit": x_fit.tolist(), "y_fit_kw": y_fit_kw.tolist(),
            })
            last_economic = (x_raw, fitted_w, rotor_m, rated_kw)
        except Exception:
            continue
    result["turbine_curves"] = turbine_curves
    if last_economic is not None:
        x_raw, fitted_w, rotor_m, rated_kw = last_economic
        power_interp = interp1d(x_raw, fitted_w, bounds_error=False, fill_value=0.0)
        capex = np.zeros_like(wind)
        van = np.zeros_like(wind)
        for j in range(ny):
            for i in range(nx):
                capex[j, i], van[j, i] = _legacy_prefit_economics(
                    float(wind[j, i]), float(weibull_k[j, i]), float(density[j, i]),
                    power_interp, float(np.max(fitted_w)), float(rotor_m), float(rated_kw),
                    float(demand_mwh), float(discount_rate), float(price_usd_kwh),
                    float(fx_mxn_per_usd), int(project_years), cost_model=cost_model,
                )
        result["inversion"] = _wrap_grid(template, capex, "Inversión inicial (USD)")
        result["van"] = _wrap_grid(template, van, "Valor Actual Neto (USD)")
    if progress_callback:
        progress_callback(96, "Mapas MCP espaciales, direccionales y físicos listos")
    return result

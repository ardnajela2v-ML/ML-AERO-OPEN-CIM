"""Linear MCP fitting, metrics, ranking, and chronological validation."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp, weibull_min


def fit_direction_offset(
    observed_direction: Iterable[float],
    reference_direction: Iterable[float],
) -> Dict[str, Optional[float]]:
    """Fit a circular offset that maps reanalysis bearings onto CERTE.

    The offset is fitted separately from wind speed and only from the caller's
    training period, so direction validation remains leakage-safe.
    """

    observed = np.asarray(observed_direction, dtype=float)
    reference = np.asarray(reference_direction, dtype=float)
    valid = np.isfinite(observed) & np.isfinite(reference)
    if not valid.any():
        return {"offset_deg": 0.0, "N_train": 0, "mae_before_deg": None, "mae_after_deg": None}
    delta = ((observed[valid] - reference[valid] + 180.0) % 360.0) - 180.0
    radians = np.deg2rad(delta)
    offset = float(np.rad2deg(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())))
    corrected_delta = ((delta - offset + 180.0) % 360.0) - 180.0
    return {
        "offset_deg": offset,
        "N_train": int(valid.sum()),
        "mae_before_deg": float(np.mean(np.abs(delta))),
        "mae_after_deg": float(np.mean(np.abs(corrected_delta))),
    }


def apply_direction_offset(reference_direction: Iterable[float], offset_deg: float) -> np.ndarray:
    """Apply a fitted circular offset while preserving missing bearings."""

    reference = np.asarray(reference_direction, dtype=float)
    corrected = (reference + float(offset_deg)) % 360.0
    corrected[~np.isfinite(reference)] = np.nan
    return corrected


def fit_sector_direction_model(
    observed_direction: Iterable[float],
    reference_direction: Iterable[float],
    n_sectors: int = 16,
    minimum_pairs_per_sector: int = 100,
) -> Dict[str, object]:
    """Fit one circular direction offset for every reanalysis sector.

    This model is deliberately independent from the wind-speed MCP model.  It
    is trained on concurrent bearings only and is used exclusively to rebuild
    the local directional distribution and wind rose.
    """

    n_sectors = int(n_sectors)
    if n_sectors not in {12, 16}:
        raise ValueError("Direction reconstruction supports 12 or 16 sectors")
    observed = np.asarray(observed_direction, dtype=float)
    reference = np.asarray(reference_direction, dtype=float)
    valid = np.isfinite(observed) & np.isfinite(reference)
    global_model = fit_direction_offset(observed[valid], reference[valid])
    width = 360.0 / n_sectors
    sector_rows: List[Dict[str, object]] = []
    for sector in range(n_sectors):
        center = sector * width
        delta = np.abs(((reference[valid] - center + 180.0) % 360.0) - 180.0)
        selected = delta < width / 2.0
        count = int(selected.sum())
        fallback = count < int(minimum_pairs_per_sector)
        local = (
            global_model
            if fallback
            else fit_direction_offset(observed[valid][selected], reference[valid][selected])
        )
        sector_rows.append({
            "sector_index": sector,
            "center_deg": float(center),
            "N_train": count,
            "offset_deg": float(local.get("offset_deg") or 0.0),
            "fallback_global": fallback,
        })
    model: Dict[str, object] = {
        "method": "sectorwise_circular_offset",
        "purpose": "wind_rose_direction_only",
        "n_sectors": n_sectors,
        "minimum_pairs_per_sector": int(minimum_pairs_per_sector),
        "N_train": int(valid.sum()),
        "global_offset_deg": float(global_model.get("offset_deg") or 0.0),
        # Compatibility for older consumers that displayed one global offset.
        "offset_deg": float(global_model.get("offset_deg") or 0.0),
        "sectors": sector_rows,
        "mae_before_deg": global_model.get("mae_before_deg"),
    }
    corrected = apply_sector_direction_model(reference[valid], model)
    angular_error = np.abs(((corrected - observed[valid] + 180.0) % 360.0) - 180.0)
    model["mae_after_deg"] = float(np.mean(angular_error)) if len(angular_error) else None
    return model


def apply_sector_direction_model(
    reference_direction: Iterable[float],
    model: Mapping[str, object],
) -> np.ndarray:
    """Apply a sector-wise circular correction to reanalysis bearings."""

    reference = np.asarray(reference_direction, dtype=float)
    corrected = np.full(reference.shape, np.nan, dtype=float)
    finite = np.isfinite(reference)
    if not finite.any():
        return corrected
    n_sectors = int(model.get("n_sectors", 16))
    width = 360.0 / n_sectors
    indices = np.floor(((reference[finite] + width / 2.0) % 360.0) / width).astype(int)
    offsets = np.full(n_sectors, float(model.get("global_offset_deg", model.get("offset_deg", 0.0)) or 0.0))
    for row in model.get("sectors", []):
        sector = int(row.get("sector_index", -1))
        if 0 <= sector < n_sectors:
            offsets[sector] = float(row.get("offset_deg", offsets[sector]) or 0.0)
    corrected[finite] = (reference[finite] + offsets[indices]) % 360.0
    return corrected


def _timestamp_months(timestamps: Iterable[object] | None, expected_length: int) -> np.ndarray:
    """Return calendar months for a model that is explicitly seasonal."""

    if timestamps is None:
        raise ValueError("Seasonal MCP requires timestamps")
    index = pd.DatetimeIndex(pd.to_datetime(list(timestamps), errors="coerce", utc=True))
    if len(index) != int(expected_length):
        raise ValueError("Seasonal MCP timestamps and values lengths differ")
    if index.isna().any():
        raise ValueError("Seasonal MCP timestamps contain invalid values")
    return index.month.to_numpy(dtype=int)


def fit_seasonal_sector_direction_model(
    observed_direction: Iterable[float],
    reference_direction: Iterable[float],
    timestamps: Iterable[object],
    n_sectors: int = 16,
    minimum_pairs_per_sector: int = 30,
    minimum_pairs_per_month: int = 120,
) -> Dict[str, object]:
    """Fit month-conditioned sector offsets using only the supplied period.

    Each calendar month gets its own 16-sector circular correction.  A month
    with insufficient pairs borrows its meteorological season (DJF, MAM, JJA,
    SON), then the annual model.  This keeps the 2018 seasonal signal without
    ever copying a 2018 rose over a target year.
    """

    observed = np.asarray(observed_direction, dtype=float)
    reference = np.asarray(reference_direction, dtype=float)
    if len(observed) != len(reference):
        raise ValueError("Observed and reference direction lengths differ")
    months = _timestamp_months(timestamps, len(reference))
    valid = np.isfinite(observed) & np.isfinite(reference)
    annual_model = fit_sector_direction_model(
        observed[valid], reference[valid], n_sectors=n_sectors,
        minimum_pairs_per_sector=max(100, int(minimum_pairs_per_sector)),
    )
    seasons = {
        1: (12, 1, 2), 2: (12, 1, 2), 3: (3, 4, 5),
        4: (3, 4, 5), 5: (3, 4, 5), 6: (6, 7, 8),
        7: (6, 7, 8), 8: (6, 7, 8), 9: (9, 10, 11),
        10: (9, 10, 11), 11: (9, 10, 11), 12: (12, 1, 2),
    }
    monthly_models: Dict[str, object] = {}
    for month in range(1, 13):
        month_mask = valid & (months == month)
        source_mask = month_mask
        fallback = "month"
        if int(source_mask.sum()) < int(minimum_pairs_per_month):
            source_mask = valid & np.isin(months, seasons[month])
            fallback = "meteorological_season"
        if int(source_mask.sum()) < int(minimum_pairs_per_month):
            monthly_models[str(month)] = {
                **annual_model,
                "fallback_level": "annual",
                "month": month,
                "month_pairs": int(month_mask.sum()),
            }
            continue
        monthly_models[str(month)] = {
            **fit_sector_direction_model(
                observed[source_mask], reference[source_mask],
                n_sectors=n_sectors,
                minimum_pairs_per_sector=minimum_pairs_per_sector,
            ),
            "fallback_level": fallback,
            "month": month,
            "month_pairs": int(month_mask.sum()),
            "training_months": sorted({int(value) for value in months[source_mask]}),
        }
    model: Dict[str, object] = {
        "method": "seasonal_sectorwise_circular_offset",
        "purpose": "wind_rose_direction_only",
        "seasonality": "calendar_month_with_meteorological_season_and_annual_fallback",
        "n_sectors": int(n_sectors),
        "minimum_pairs_per_sector": int(minimum_pairs_per_sector),
        "minimum_pairs_per_month": int(minimum_pairs_per_month),
        "N_train": int(valid.sum()),
        "global_offset_deg": float(annual_model.get("global_offset_deg", 0.0) or 0.0),
        "offset_deg": float(annual_model.get("global_offset_deg", 0.0) or 0.0),
        "annual_model": annual_model,
        "monthly_models": monthly_models,
        "mae_before_deg": annual_model.get("mae_before_deg"),
    }
    corrected = apply_seasonal_sector_direction_model(reference, timestamps, model)
    angular_error = np.abs(((corrected[valid] - observed[valid] + 180.0) % 360.0) - 180.0)
    model["mae_after_deg"] = float(np.mean(angular_error)) if len(angular_error) else None
    return model


def apply_seasonal_sector_direction_model(
    reference_direction: Iterable[float],
    timestamps: Iterable[object],
    model: Mapping[str, object],
) -> np.ndarray:
    """Apply the calendar-month directional model to any target period."""

    reference = np.asarray(reference_direction, dtype=float)
    months = _timestamp_months(timestamps, len(reference))
    corrected = np.full(reference.shape, np.nan, dtype=float)
    monthly_models = model.get("monthly_models", {})
    annual_model = model.get("annual_model", model)
    for month in range(1, 13):
        mask = months == month
        if not mask.any():
            continue
        month_model = monthly_models.get(str(month), annual_model)
        corrected[mask] = apply_sector_direction_model(reference[mask], month_model)
    return corrected


def _finite_pairs(observed: Iterable[float], predicted: Iterable[float]) -> Tuple[np.ndarray, np.ndarray]:
    observed_array = np.asarray(observed, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    mask = np.isfinite(observed_array) & np.isfinite(predicted_array)
    return observed_array[mask], predicted_array[mask]


def fit_weibull_mle(values: Iterable[float], minimum_points: int = 3) -> Dict[str, object]:
    """Fit the same two-parameter Weibull used by Recurso Eólico.

    The location is fixed at zero and the returned scale is named ``lambda``.
    Keeping this estimator shared avoids comparing MCP method-of-moments values
    against a downstream maximum-likelihood fit.
    """

    sample = np.asarray(values, dtype=float)
    positive = sample[np.isfinite(sample) & (sample > 0.0)]
    if len(positive) < int(minimum_points):
        return {"k": None, "lambda": None, "N": int(len(positive)), "method": "MLE_floc_0"}
    shape, _, scale = weibull_min.fit(positive, floc=0)
    return {
        "k": float(shape),
        "lambda": float(scale),
        "N": int(len(positive)),
        "method": "MLE_floc_0",
    }


def metrics(observed: Iterable[float], predicted: Iterable[float]) -> Dict[str, Optional[float]]:
    y_true, y_pred = _finite_pairs(observed, predicted)
    n = len(y_true)
    if n == 0:
        return {"N": 0}
    error = y_pred - y_true
    sse = float(np.sum(error ** 2))
    centered = y_true - y_true.mean()
    sst = float(np.sum(centered ** 2))
    r = None
    if n > 1 and np.std(y_true) > 0 and np.std(y_pred) > 0:
        r = float(np.corrcoef(y_true, y_pred)[0, 1])
    r2 = None if sst == 0 else float(1.0 - sse / sst)
    observed_mean = float(np.mean(y_true))
    predicted_mean = float(np.mean(y_pred))

    observed_weibull = fit_weibull_mle(y_true)
    predicted_weibull = fit_weibull_mle(y_pred)
    observed_k, observed_lambda = observed_weibull["k"], observed_weibull["lambda"]
    predicted_k, predicted_lambda = predicted_weibull["k"], predicted_weibull["lambda"]
    ks = float(ks_2samp(y_true, y_pred, alternative="two-sided", method="asymp").statistic)
    return {
        "N": int(n),
        "R": r,
        "R2": r2,
        "RMSE": float(np.sqrt(np.mean(error ** 2))),
        "MAE": float(np.mean(np.abs(error))),
        "Bias": float(np.mean(error)),
        "KS": ks,
        "observed_mean": observed_mean,
        "predicted_mean": predicted_mean,
        "delta_mean": predicted_mean - observed_mean,
        "delta_mean_pct": None if observed_mean == 0 else (predicted_mean / observed_mean - 1.0) * 100.0,
        "observed_weibull_k": observed_k,
        "predicted_weibull_k": predicted_k,
        "delta_weibull_k": None if observed_k is None or predicted_k is None else predicted_k - observed_k,
        "observed_weibull_lambda": observed_lambda,
        "predicted_weibull_lambda": predicted_lambda,
        "delta_weibull_lambda": None if observed_lambda is None or predicted_lambda is None else predicted_lambda - observed_lambda,
        "weibull_fit_method": "MLE_floc_0",
        "observed_P50": float(np.percentile(y_true, 50)),
        "predicted_P50": float(np.percentile(y_pred, 50)),
        "observed_P90": float(np.percentile(y_true, 90)),
        "predicted_P90": float(np.percentile(y_pred, 90)),
        "observed_P99": float(np.percentile(y_true, 99)),
        "predicted_P99": float(np.percentile(y_pred, 99)),
    }


def fit_linear(reference: Iterable[float], observed: Iterable[float]) -> Dict[str, float]:
    x, y = _finite_pairs(reference, observed)
    if len(x) < 2:
        raise ValueError("At least two concurrent finite pairs are required")
    if np.std(x) == 0:
        raise ValueError("Reference wind speed has no variability")
    slope, intercept = np.polyfit(x, y, 1)
    return {"intercept": float(intercept), "slope": float(slope), "N_train": int(len(x))}


def predict_linear(model: Dict[str, float], reference: Iterable[float]) -> Tuple[np.ndarray, np.ndarray]:
    reference_array = np.asarray(reference, dtype=float)
    raw = model["intercept"] + model["slope"] * reference_array
    clipped = np.maximum(raw, 0.0)
    return raw, clipped


# Keep the public engine defaults backward compatible with the original test
# suite.  AERO's MCP Lab opts into the extended thesis methods explicitly.
DEFAULT_METHODS = ("linear", "variance_ratio", "quadratic")
SECTOR_QUANTILE_METHODS = ("sector_qm_12", "sector_qm_16")
SEASONAL_QUANTILE_METHOD = "seasonal_quantile_mapping"
EXTENDED_METHODS = DEFAULT_METHODS + ("quantile_mapping", SEASONAL_QUANTILE_METHOD) + SECTOR_QUANTILE_METHODS


def _fit_quantile_mapping(reference: np.ndarray, observed: np.ndarray) -> Dict[str, object]:
    quantiles = np.linspace(0.0, 1.0, 101)
    reference_quantiles = np.quantile(reference, quantiles)
    observed_quantiles = np.quantile(observed, quantiles)
    unique_reference, unique_indices = np.unique(reference_quantiles, return_index=True)
    return {
        "reference_quantiles": [float(value) for value in unique_reference],
        "observed_quantiles": [float(observed_quantiles[index]) for index in unique_indices],
        "N_train": int(len(reference)),
    }


def _predict_quantile_mapping(model: Dict[str, object], reference: np.ndarray) -> np.ndarray:
    """Monotonic empirical mapping preserving the trained distribution."""

    x = np.asarray(model["reference_quantiles"], dtype=float)
    y = np.asarray(model["observed_quantiles"], dtype=float)
    return np.interp(reference, x, y)


def fit_mcp_model(
    reference: Iterable[float],
    observed: Iterable[float],
    method: str = "linear",
    direction: Iterable[float] | None = None,
    timestamps: Iterable[object] | None = None,
) -> Dict[str, object]:
    """Fit one of the transparent MCP correction methods."""

    method = str(method).strip().lower()
    reference_input = np.asarray(reference, dtype=float)
    observed_input = np.asarray(observed, dtype=float)
    if len(reference_input) != len(observed_input):
        raise ValueError("Observed and reference speed lengths differ")
    if method == SEASONAL_QUANTILE_METHOD:
        months = _timestamp_months(timestamps, len(reference_input))
        finite = np.isfinite(reference_input) & np.isfinite(observed_input)
        x = reference_input[finite]
        y = observed_input[finite]
        training_months = months[finite]
        if len(x) < 2:
            raise ValueError("At least two concurrent finite pairs are required")
        global_model = _fit_quantile_mapping(x, y)
        monthly_models: Dict[str, object] = {}
        minimum_pairs = 100
        seasons = {
            1: (12, 1, 2), 2: (12, 1, 2), 3: (3, 4, 5),
            4: (3, 4, 5), 5: (3, 4, 5), 6: (6, 7, 8),
            7: (6, 7, 8), 8: (6, 7, 8), 9: (9, 10, 11),
            10: (9, 10, 11), 11: (9, 10, 11), 12: (12, 1, 2),
        }
        for month in range(1, 13):
            month_mask = training_months == month
            source_mask = month_mask
            fallback = "month"
            if int(source_mask.sum()) < minimum_pairs:
                source_mask = np.isin(training_months, seasons[month])
                fallback = "meteorological_season"
            if int(source_mask.sum()) < minimum_pairs or np.unique(x[source_mask]).size < 2:
                monthly_models[str(month)] = {
                    **global_model,
                    "fallback_level": "annual",
                    "month_pairs": int(month_mask.sum()),
                }
            else:
                monthly_models[str(month)] = {
                    **_fit_quantile_mapping(x[source_mask], y[source_mask]),
                    "fallback_level": fallback,
                    "month_pairs": int(month_mask.sum()),
                    "training_months": sorted({int(value) for value in training_months[source_mask]}),
                }
        return {
            "method": method,
            "seasonality": "calendar_month_with_meteorological_season_and_annual_fallback",
            "minimum_pairs_per_month": minimum_pairs,
            "global_model": global_model,
            "monthly_models": monthly_models,
            "N_train": int(len(x)),
        }
    x, y = _finite_pairs(reference_input, observed_input)
    if len(x) < 2:
        raise ValueError("At least two concurrent finite pairs are required")
    if method == "linear":
        model = fit_linear(x, y)
        model["method"] = method
        return model
    if method == "variance_ratio":
        reference_std = float(np.std(x, ddof=1))
        observed_std = float(np.std(y, ddof=1))
        if reference_std == 0:
            raise ValueError("Reference wind speed has no variability")
        slope = observed_std / reference_std
        return {
            "method": method,
            "intercept": float(np.mean(y) - slope * np.mean(x)),
            "slope": float(slope),
            "reference_mean": float(np.mean(x)),
            "observed_mean": float(np.mean(y)),
            "reference_std": reference_std,
            "observed_std": observed_std,
            "N_train": int(len(x)),
        }
    if method == "quadratic":
        if np.unique(x).size < 3:
            raise ValueError("Quadratic MCP requires at least three distinct reference speeds")
        coefficients = np.polyfit(x, y, 2)
        return {
            "method": method,
            "coefficients": [float(value) for value in coefficients],
            "N_train": int(len(x)),
        }
    if method == "quantile_mapping":
        return {"method": method, **_fit_quantile_mapping(x, y)}
    if method in SECTOR_QUANTILE_METHODS:
        if direction is None:
            raise ValueError("Sector quantile MCP requires reference wind direction")
        n_sectors = int(method.rsplit("_", 1)[-1])
        direction_array = np.asarray(direction, dtype=float)
        reference_array = np.asarray(reference, dtype=float)
        observed_array = np.asarray(observed, dtype=float)
        valid = np.isfinite(reference_array) & np.isfinite(observed_array) & np.isfinite(direction_array)
        if int(valid.sum()) < 2:
            raise ValueError("Sector quantile MCP requires concurrent speed and direction pairs")
        reference_array = reference_array[valid]
        observed_array = observed_array[valid]
        direction_array = direction_array[valid] % 360.0
        global_model = _fit_quantile_mapping(reference_array, observed_array)
        sectors: Dict[str, object] = {}
        sector_width = 360.0 / n_sectors
        minimum_pairs = 100
        for sector in range(n_sectors):
            center = sector * sector_width
            delta = np.abs(((direction_array - center + 180.0) % 360.0) - 180.0)
            mask = delta < sector_width / 2.0
            if int(mask.sum()) >= minimum_pairs and np.unique(reference_array[mask]).size >= 2:
                sectors[str(sector)] = {
                    **_fit_quantile_mapping(reference_array[mask], observed_array[mask]),
                    "fallback": False,
                    "sector_pairs": int(mask.sum()),
                }
            else:
                sectors[str(sector)] = {**global_model, "fallback": True, "sector_pairs": int(mask.sum())}
        return {
            "method": method,
            "sector_method": "empirical_quantile_mapping",
            "n_sectors": n_sectors,
            "minimum_pairs_per_sector": minimum_pairs,
            "global_model": global_model,
            "sectors": sectors,
            "N_train": int(len(reference_array)),
        }
    raise ValueError("Unknown MCP method: %s" % method)


def predict_mcp(
    model: Dict[str, object],
    reference: Iterable[float],
    direction: Iterable[float] | None = None,
    timestamps: Iterable[object] | None = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Predict with a fitted MCP model and clip physically negative speeds."""

    reference_array = np.asarray(reference, dtype=float)
    method = str(model.get("method", "linear"))
    if method in {"linear", "variance_ratio"}:
        raw = float(model["intercept"]) + float(model["slope"]) * reference_array
    elif method == "quadratic":
        raw = np.polyval(np.asarray(model["coefficients"], dtype=float), reference_array)
    elif method == "quantile_mapping":
        raw = _predict_quantile_mapping(model, reference_array)
    elif method == SEASONAL_QUANTILE_METHOD:
        months = _timestamp_months(timestamps, len(reference_array))
        raw = np.full(reference_array.shape, np.nan, dtype=float)
        global_model = model.get("global_model", model)
        monthly_models = model.get("monthly_models", {})
        for month in range(1, 13):
            mask = months == month
            if mask.any():
                raw[mask] = _predict_quantile_mapping(
                    monthly_models.get(str(month), global_model), reference_array[mask]
                )
    elif method in SECTOR_QUANTILE_METHODS:
        if direction is None:
            raise ValueError("Sector quantile MCP prediction requires reference wind direction")
        direction_array = np.asarray(direction, dtype=float)
        if len(direction_array) != len(reference_array):
            raise ValueError("Sector quantile MCP speed and direction lengths differ")
        correction = (model.get("direction_correction") or {}).get("offset_deg", 0.0)
        direction_array = apply_direction_offset(direction_array, float(correction or 0.0))
        finite_direction = np.isfinite(direction_array)
        sector_indices = np.full(direction_array.shape, -1, dtype=int)
        n_sectors = int(model.get("n_sectors", 16))
        half_sector = 180.0 / n_sectors
        sector_indices[finite_direction] = np.floor(
            ((direction_array[finite_direction] + half_sector) % 360.0) / (360.0 / n_sectors)
        ).astype(int)
        raw = np.full(reference_array.shape, np.nan, dtype=float)
        for sector in range(n_sectors):
            mask = sector_indices == sector
            sector_model = model["sectors"][str(sector)]
            raw[mask] = _predict_quantile_mapping(sector_model, reference_array[mask])
    else:
        raise ValueError("Unknown MCP method: %s" % method)
    clipped = np.maximum(raw, 0.0)
    clipped[~np.isfinite(reference_array)] = np.nan
    if method in SECTOR_QUANTILE_METHODS:
        clipped[~finite_direction] = np.nan
    return raw, clipped


def _method_columns(frame: pd.DataFrame, method: str) -> List[str]:
    columns = ["wind_speed_obs", "wind_speed_ref"]
    if method in SECTOR_QUANTILE_METHODS:
        if "wind_direction_ref" not in frame.columns:
            raise ValueError("Sector quantile MCP requires wind_direction_ref")
        columns.append("wind_direction_ref")
        if "wind_direction_obs" in frame.columns:
            columns.append("wind_direction_obs")
    return columns


def _fit_and_predict(method: str, train: pd.DataFrame, test: pd.DataFrame) -> Tuple[Dict[str, object], np.ndarray, np.ndarray]:
    sector_method = method in SECTOR_QUANTILE_METHODS
    if sector_method and {"wind_direction_obs", "wind_direction_ref"}.issubset(train.columns):
        direction_model = fit_direction_offset(train["wind_direction_obs"], train["wind_direction_ref"])
    else:
        direction_model = {"offset_deg": 0.0, "N_train": 0, "mae_before_deg": None, "mae_after_deg": None}
    train_direction = (
        apply_direction_offset(train["wind_direction_ref"], float(direction_model["offset_deg"] or 0.0))
        if sector_method else None
    )
    test_direction = test["wind_direction_ref"] if sector_method else None
    model = fit_mcp_model(
        train["wind_speed_ref"], train["wind_speed_obs"], method=method,
        direction=train_direction, timestamps=train.index,
    )
    if sector_method:
        model["direction_correction"] = direction_model
    raw, predicted = predict_mcp(
        model, test["wind_speed_ref"].to_numpy(), direction=test_direction,
        timestamps=test.index,
    )
    return model, raw, predicted


def select_mcp_method(
    aligned: pd.DataFrame,
    training_year: int,
    methods: Sequence[str] = DEFAULT_METHODS,
    validation_fraction: float = 0.25,
) -> Dict[str, object]:
    """Select a method using only a chronological holdout inside one year."""

    period = aligned.loc[aligned.index.year == int(training_year)].dropna(
        subset=["wind_speed_obs", "wind_speed_ref"]
    ).copy()
    if len(period) < 12:
        raise ValueError("Not enough pairs to select an MCP method in %s" % training_year)
    split = int(np.floor(len(period) * (1.0 - validation_fraction)))
    split = min(max(split, 2), len(period) - 2)
    train = period.iloc[:split]
    validation = period.iloc[split:]
    rows: List[Dict[str, object]] = []
    for method in methods:
        try:
            columns = _method_columns(period, method)
            method_train = train[columns].dropna()
            method_validation = validation[columns].dropna()
            model, _, predicted = _fit_and_predict(method, method_train, method_validation)
            result = metrics(method_validation["wind_speed_obs"], predicted)
            rows.append({"method": method, "status": "ok", "metrics": result, "model": model})
        except ValueError as error:
            rows.append({"method": method, "status": "error", "error": str(error)})
    valid = [row for row in rows if row["status"] == "ok"]
    if not valid:
        raise ValueError("No MCP method could be fitted for %s" % training_year)
    valid.sort(key=lambda row: (
        row["metrics"].get("RMSE", np.inf),
        row["metrics"].get("MAE", np.inf),
        -(row["metrics"].get("R2") if row["metrics"].get("R2") is not None else -np.inf),
    ))
    return {
        "training_year": int(training_year),
        "selection": "chronological_holdout_inside_training_year",
        "validation_fraction": validation_fraction,
        "pairs_train": int(len(train)),
        "pairs_selection": int(len(validation)),
        "selected_method": valid[0]["method"],
        "candidates": rows,
    }


def compare_mcp_methods(
    aligned: pd.DataFrame,
    train_year: int,
    test_year: int,
    methods: Sequence[str] = DEFAULT_METHODS,
) -> List[Dict[str, object]]:
    """Fit every candidate on the training year and report test-year results."""

    train = aligned.loc[aligned.index.year == int(train_year)].copy()
    test = aligned.loc[aligned.index.year == int(test_year)].copy()
    if len(train) < 2 or test.empty:
        raise ValueError("Not enough pairs to compare MCP methods")
    rows: List[Dict[str, object]] = []
    for method in methods:
        try:
            columns = _method_columns(aligned, method)
            method_train = train[columns].dropna()
            method_test = test[columns].dropna()
            model, _, predicted = _fit_and_predict(method, method_train, method_test)
            rows.append({"method": method, "status": "ok", "metrics": metrics(method_test["wind_speed_obs"], predicted), "model": model})
        except ValueError as error:
            rows.append({"method": method, "status": "error", "error": str(error)})
    return rows


def direct_reference_metrics(aligned: pd.DataFrame, year: int) -> Dict[str, object]:
    period = aligned.loc[aligned.index.year == int(year)]
    period = period[["wind_speed_obs", "wind_speed_ref"]].dropna()
    result = metrics(period["wind_speed_obs"], period["wind_speed_ref"])
    result["year"] = int(year)
    return result


def validate_direction(
    aligned: pd.DataFrame,
    train_year: int,
    test_year: int,
    method: str = "linear",
    direction_sector_count: int = 16,
) -> Dict[str, object]:
    base_columns = _method_columns(aligned, method)
    raw_reference_column = "wind_speed_ref_raw"
    chart_columns = [
        column for column in (
            "wind_direction_obs", "wind_direction_ref", "temperature_ref", "pressure_ref_kpa"
        )
        if column in aligned.columns
    ]
    test_columns = list(dict.fromkeys(
        base_columns
        + ([raw_reference_column] if raw_reference_column in aligned.columns else [])
        + chart_columns
    ))
    train = aligned.loc[aligned.index.year == int(train_year), base_columns].dropna()
    test = aligned.loc[aligned.index.year == int(test_year), test_columns].dropna(subset=base_columns)
    if len(train) < 2:
        raise ValueError("Not enough training pairs for %s" % train_year)
    if test.empty:
        raise ValueError("No validation pairs for %s" % test_year)
    model, raw, clipped = _fit_and_predict(method, train, test)
    result_metrics = metrics(test["wind_speed_obs"], clipped)
    result_metrics["coverage_start"] = test.index.min().isoformat()
    result_metrics["coverage_end"] = test.index.max().isoformat()
    result_metrics["coverage_hours_observed"] = int(len(test))
    result_metrics["coverage_pairs"] = int(len(test))
    predictions = pd.DataFrame(
        {
            "timestamp": test.index,
            "observed_wind_speed": test["wind_speed_obs"].to_numpy(),
            "reference_wind_speed_raw": test[raw_reference_column].to_numpy() if raw_reference_column in test.columns else test["wind_speed_ref"].to_numpy(),
            "reference_wind_speed": test["wind_speed_ref"].to_numpy(),
            "predicted_wind_speed_raw": raw,
            "predicted_wind_speed": clipped,
        }
    )
    if "wind_direction_obs" in test.columns:
        predictions["observed_wind_direction"] = test["wind_direction_obs"].to_numpy()
    for environmental_column in ("temperature_ref", "pressure_ref_kpa"):
        if environmental_column in test.columns:
            predictions[environmental_column] = test[environmental_column].to_numpy()
    if "wind_direction_ref" in test.columns:
        direction_training = aligned.loc[aligned.index.year == int(train_year)]
        direction_model = (
            fit_seasonal_sector_direction_model(
                direction_training["wind_direction_obs"],
                direction_training["wind_direction_ref"],
                direction_training.index,
                n_sectors=direction_sector_count,
            )
            if {"wind_direction_obs", "wind_direction_ref"}.issubset(direction_training.columns)
            else {
                "method": "sectorwise_circular_offset",
                "purpose": "wind_rose_direction_only",
                "n_sectors": int(direction_sector_count),
                "offset_deg": 0.0,
                "global_offset_deg": 0.0,
                "N_train": 0,
                "sectors": [],
                "mae_before_deg": None,
                "mae_after_deg": None,
            }
        )
        predictions["reference_wind_direction_raw"] = test["wind_direction_ref"].to_numpy()
        predictions["predicted_wind_direction"] = apply_seasonal_sector_direction_model(
            test["wind_direction_ref"], test.index, direction_model
        )
        model["rose_direction_model"] = direction_model
    return {
        "train_year": int(train_year),
        "test_year": int(test_year),
        "model": model,
        "metrics": result_metrics,
        "predictions": predictions,
    }


def rank_references(
    aligned_by_name: Dict[str, pd.DataFrame],
    ranking_year: int,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for name, aligned in aligned_by_name.items():
        row = direct_reference_metrics(aligned, ranking_year)
        row["reference"] = name
        rows.append(row)
    rows.sort(
        key=lambda row: (
            -(row.get("R2") if row.get("R2") is not None else -np.inf),
            row.get("RMSE", np.inf),
            row.get("MAE", np.inf),
            abs(row.get("Bias", np.inf)),
            -row.get("N", 0),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows

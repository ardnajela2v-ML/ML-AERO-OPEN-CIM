"""Leakage-safe multi-reference MCP and campaign sensitivity utilities."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import nnls
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .data import ReferenceData, resample_hourly
from .mcp import fit_linear, metrics, predict_linear


def build_multireference_frame(
    observations: pd.DataFrame,
    references: Mapping[str, ReferenceData],
) -> tuple[pd.DataFrame, list[str]]:
    """Align observations and all references on an hourly UTC index."""

    observed = resample_hourly(observations[["wind_speed_obs"]])
    pieces = [observed]
    speed_columns: list[str] = []
    for position, (name, reference) in enumerate(references.items()):
        reference_frame = reference.frame
        if not observations.empty and not reference_frame.empty:
            start = observations.index.min().floor("h")
            end = observations.index.max().ceil("h")
            reference_frame = reference_frame.loc[
                (reference_frame.index >= start) & (reference_frame.index <= end)
            ]
        hourly = resample_hourly(reference_frame)
        prefix = "ref_%02d" % position
        rename = {"wind_speed_ref": prefix + "_speed"}
        speed_columns.append(rename["wind_speed_ref"])
        for source, suffix in (
            ("wind_direction_ref", "_direction"),
            ("temperature_ref", "_temperature"),
            ("pressure_ref_kpa", "_pressure_kpa"),
        ):
            if source in hourly.columns:
                rename[source] = prefix + suffix
        selected = [source for source in rename if source in hourly.columns]
        part = hourly[selected].rename(columns=rename)
        part.attrs["reference_name"] = name
        pieces.append(part)
    frame = pd.concat(pieces, axis=1).sort_index()
    frame.attrs["reference_names"] = list(references)
    return frame, speed_columns


def _calendar_and_reference_features(frame: pd.DataFrame, speed_columns: Sequence[str]) -> pd.DataFrame:
    result = frame[list(speed_columns)].apply(pd.to_numeric, errors="coerce").copy()
    for column in frame.columns:
        if column.endswith("_direction"):
            radians = np.deg2rad(pd.to_numeric(frame[column], errors="coerce"))
            result[column + "_sin"] = np.sin(radians)
            result[column + "_cos"] = np.cos(radians)
        elif column.endswith("_temperature") or column.endswith("_pressure_kpa"):
            result[column] = pd.to_numeric(frame[column], errors="coerce")
    index = pd.DatetimeIndex(frame.index)
    result["hour_sin"] = np.sin(2 * np.pi * index.hour / 24.0)
    result["hour_cos"] = np.cos(2 * np.pi * index.hour / 24.0)
    result["day_sin"] = np.sin(2 * np.pi * index.dayofyear / 365.25)
    result["day_cos"] = np.cos(2 * np.pi * index.dayofyear / 365.25)
    return result.replace([np.inf, -np.inf], np.nan)


def _fit_nnls_sum_one(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    weights, _ = nnls(x, y)
    total = float(weights.sum())
    if total <= 0:
        return np.full(x.shape[1], 1.0 / x.shape[1])
    return weights / total


def evaluate_multireference(
    observations: pd.DataFrame,
    references: Mapping[str, ReferenceData],
    train_year: int,
    test_year: int,
) -> Dict[str, Any]:
    """Compare NNLS and multi-reference ML without using test-year data in fitting."""

    if len(references) < 2:
        return {"status": "not_applicable", "reason": "At least two references are required"}
    frame, speed_columns = build_multireference_frame(observations, references)
    complete = frame[["wind_speed_obs", *speed_columns]].dropna()
    train = complete.loc[complete.index.year == int(train_year)]
    test = complete.loc[complete.index.year == int(test_year)]
    if len(train) < max(20, len(speed_columns) * 3) or len(test) < 2:
        return {"status": "insufficient", "train_pairs": int(len(train)), "test_pairs": int(len(test))}

    weights = _fit_nnls_sum_one(train[speed_columns].to_numpy(), train["wind_speed_obs"].to_numpy())
    nnls_prediction = np.maximum(test[speed_columns].to_numpy().dot(weights), 0.0)
    names = list(references)
    nnls_result = {
        "method": "multi_reference_nnls",
        "status": "ok",
        "training_year": int(train_year),
        "test_year": int(test_year),
        "train_pairs": int(len(train)),
        "test_pairs": int(len(test)),
        "constraint": "nonnegative_weights_sum_to_one",
        "weights": {name: float(weight) for name, weight in zip(names, weights)},
        "metrics": metrics(test["wind_speed_obs"], nnls_prediction),
    }

    feature_frame = _calendar_and_reference_features(frame, speed_columns)
    combined = feature_frame.copy()
    combined["wind_speed_obs"] = frame["wind_speed_obs"]
    train_ml = combined.loc[combined.index.year == int(train_year)].dropna()
    test_ml = combined.loc[combined.index.year == int(test_year)].dropna()
    ml_rows = []
    if len(train_ml) >= 40 and len(test_ml) >= 2:
        x_columns = [column for column in train_ml.columns if column != "wind_speed_obs"]
        estimators = {
            "multi_reference_ridge": Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=10.0))]),
            "multi_reference_random_forest": RandomForestRegressor(
                n_estimators=80, max_depth=16, min_samples_leaf=5, n_jobs=-1, random_state=42
            ),
            "multi_reference_gradient_boosting": GradientBoostingRegressor(
                n_estimators=120, learning_rate=0.04, max_depth=3,
                min_samples_leaf=8, loss="huber", random_state=42
            ),
        }
        for method, estimator in estimators.items():
            try:
                estimator.fit(train_ml[x_columns], train_ml["wind_speed_obs"])
                predicted = np.maximum(estimator.predict(test_ml[x_columns]), 0.0)
                ml_rows.append({
                    "method": method,
                    "status": "ok",
                    "training_year": int(train_year),
                    "test_year": int(test_year),
                    "train_pairs": int(len(train_ml)),
                    "test_pairs": int(len(test_ml)),
                    "features": x_columns,
                    "metrics": metrics(test_ml["wind_speed_obs"], predicted),
                })
            except Exception as error:
                ml_rows.append({"method": method, "status": "error", "error": str(error)})
    valid_ml = [row for row in ml_rows if row.get("status") == "ok"]
    valid_ml.sort(key=lambda row: (row["metrics"].get("RMSE", np.inf), row["metrics"].get("MAE", np.inf)))
    return {
        "status": "ok",
        "selection_rule": "out_of_sample_rmse_then_mae",
        "nnls": nnls_result,
        "ml_comparison": ml_rows,
        "selected_ml_method": valid_ml[0]["method"] if valid_ml else None,
    }


def campaign_sensitivity(
    aligned: pd.DataFrame,
    train_year: int,
    test_year: int,
    months: Sequence[int] = (1, 3, 6, 9, 12, 18),
) -> list[Dict[str, Any]]:
    """Fit linear MCP on the first N campaign months and evaluate one fixed test year."""

    pairs = aligned[["wind_speed_obs", "wind_speed_ref"]].dropna().sort_index()
    campaign_start = pd.Timestamp(year=int(train_year), month=1, day=1, tz="UTC")
    test = pairs.loc[pairs.index.year == int(test_year)]
    rows: list[Dict[str, Any]] = []
    for duration in months:
        campaign_end = campaign_start + pd.DateOffset(months=int(duration))
        train = pairs.loc[(pairs.index >= campaign_start) & (pairs.index < campaign_end)]
        # Never permit the sensitivity window to absorb the validation year.
        train = train.loc[train.index.year < int(test_year)]
        if len(train) < 2 or len(test) < 2:
            rows.append({"campaign_months": int(duration), "status": "insufficient", "train_pairs": int(len(train))})
            continue
        model = fit_linear(train["wind_speed_ref"], train["wind_speed_obs"])
        _, predicted = predict_linear(model, test["wind_speed_ref"])
        rows.append({
            "campaign_months": int(duration),
            "status": "ok",
            "train_start": train.index.min().isoformat(),
            "train_end": train.index.max().isoformat(),
            "train_pairs": int(len(train)),
            "test_year": int(test_year),
            "metrics": metrics(test["wind_speed_obs"], predicted),
        })
    return rows

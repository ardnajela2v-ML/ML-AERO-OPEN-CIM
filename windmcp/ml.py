"""Leakage-safe machine-learning model arena for hourly MCP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .mcp import (apply_seasonal_sector_direction_model,
                  fit_seasonal_sector_direction_model, metrics)


# MLP remains supported, but is opt-in because repeated neural-network fits are
# too expensive for the default interactive run. The tree and Ridge models are
# deterministic, fast enough for the complete reference set, and auditable.
DEFAULT_ML_METHODS = ("random_forest", "gradient_boosting", "ridge")
OPTIONAL_ML_METHODS = ("mlp",)


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build features using only reference data and UTC calendar information."""

    if "wind_speed_ref" not in frame.columns:
        raise ValueError("ML features require wind_speed_ref")
    index = pd.DatetimeIndex(frame.index)
    reference_speed = pd.to_numeric(frame["wind_speed_ref"], errors="coerce")
    hour = index.hour + index.minute / 60.0
    day_of_year = index.dayofyear
    result = pd.DataFrame(index=frame.index)
    result["reference_wind_speed"] = reference_speed
    result["reference_wind_speed_sq"] = reference_speed ** 2
    result["reference_wind_speed_sqrt"] = np.sqrt(np.maximum(reference_speed, 0.0))
    result["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    result["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    result["day_sin"] = np.sin(2.0 * np.pi * day_of_year / 365.25)
    result["day_cos"] = np.cos(2.0 * np.pi * day_of_year / 365.25)
    if "wind_direction_ref" in frame.columns:
        direction = np.deg2rad(pd.to_numeric(frame["wind_direction_ref"], errors="coerce"))
        result["reference_direction_sin"] = np.sin(direction)
        result["reference_direction_cos"] = np.cos(direction)
    for source, target in (("temperature_ref", "reference_temperature"), ("pressure_ref_kpa", "reference_pressure_kpa")):
        if source in frame.columns:
            result[target] = pd.to_numeric(frame[source], errors="coerce")
    return result.replace([np.inf, -np.inf], np.nan)


def _estimator(method: str):
    if method == "random_forest":
        return RandomForestRegressor(
            n_estimators=80,
            max_depth=16,
            min_samples_leaf=5,
            n_jobs=-1,
            random_state=42,
        )
    if method == "gradient_boosting":
        return GradientBoostingRegressor(
            n_estimators=120,
            learning_rate=0.04,
            max_depth=3,
            min_samples_leaf=8,
            loss="huber",
            random_state=42,
        )
    if method == "ridge":
        return Pipeline([("scaler", StandardScaler()), ("ridge", Ridge(alpha=10.0))])
    if method == "mlp":
        return Pipeline([
            ("scaler", StandardScaler()),
            ("mlp", MLPRegressor(
                hidden_layer_sizes=(64, 32),
                max_iter=160,
                early_stopping=True,
                n_iter_no_change=12,
                random_state=42,
            )),
        ])
    raise ValueError("Unknown ML method: %s" % method)


def _period(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    if "wind_speed_obs" not in frame.columns:
        raise ValueError("ML training requires wind_speed_obs")
    result = frame.loc[frame.index.year == int(year)]
    return result[["wind_speed_obs", "wind_speed_ref"] + (["wind_direction_ref"] if "wind_direction_ref" in result.columns else [])].dropna()


@dataclass
class FittedMLModel:
    method: str
    estimator: Any
    feature_columns: List[str]

    def predict_frame(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        features = build_features(frame).reindex(columns=self.feature_columns)
        valid = features.notna().all(axis=1)
        raw = np.full(len(frame), np.nan, dtype=float)
        if valid.any():
            raw[valid.to_numpy()] = self.estimator.predict(features.loc[valid])
        clipped = np.maximum(raw, 0.0)
        return raw, clipped


def fit_ml_model(frame: pd.DataFrame, method: str, years: Iterable[int] | None = None) -> FittedMLModel:
    subset = frame if years is None else frame.loc[frame.index.year.isin([int(year) for year in years])]
    subset = subset[["wind_speed_obs", "wind_speed_ref"] + (["wind_direction_ref"] if "wind_direction_ref" in subset.columns else [])].dropna()
    if len(subset) < 20:
        raise ValueError("Not enough training rows for ML method %s" % method)
    features = build_features(subset)
    valid = features.notna().all(axis=1)
    features = features.loc[valid]
    target = subset.loc[valid, "wind_speed_obs"]
    estimator = _estimator(method)
    estimator.fit(features, target)
    return FittedMLModel(method=method, estimator=estimator, feature_columns=list(features.columns))


def _evaluate(fitted: FittedMLModel, frame: pd.DataFrame) -> Dict[str, object]:
    subset = frame[["wind_speed_obs", "wind_speed_ref"] + (["wind_direction_ref"] if "wind_direction_ref" in frame.columns else [])].dropna()
    raw, predicted = fitted.predict_frame(subset)
    valid = np.isfinite(predicted)
    result_metrics = metrics(subset.loc[valid, "wind_speed_obs"], predicted[valid])
    return {"metrics": result_metrics, "model": {"method": "ml_" + fitted.method, "N_train": None}}


def select_ml_method(
    aligned: pd.DataFrame,
    training_year: int,
    methods: Sequence[str] = DEFAULT_ML_METHODS,
    validation_fraction: float = 0.25,
) -> Dict[str, object]:
    """Select ML method on a chronological holdout inside the training year."""

    period = _period(aligned, training_year)
    if len(period) < 40:
        raise ValueError("Not enough rows to select ML method in %s" % training_year)
    split = min(max(int(np.floor(len(period) * (1.0 - validation_fraction))), 20), len(period) - 20)
    train = period.iloc[:split]
    validation = period.iloc[split:]
    rows: List[Dict[str, object]] = []
    for method in methods:
        try:
            fitted = fit_ml_model(train, method)
            raw, predicted = fitted.predict_frame(validation)
            valid = np.isfinite(predicted)
            rows.append({"method": "ml_" + method, "status": "ok", "metrics": metrics(validation.loc[valid, "wind_speed_obs"], predicted[valid])})
        except Exception as error:
            rows.append({"method": "ml_" + method, "status": "error", "error": str(error)})
    valid_rows = [row for row in rows if row["status"] == "ok"]
    if not valid_rows:
        raise ValueError("No ML method could be fitted in %s" % training_year)
    valid_rows.sort(key=lambda row: (row["metrics"].get("RMSE", np.inf), row["metrics"].get("MAE", np.inf)))
    return {
        "training_year": int(training_year),
        "selection": "chronological_holdout_inside_training_year",
        "validation_fraction": validation_fraction,
        "pairs_train": int(len(train)),
        "pairs_selection": int(len(validation)),
        "selected_method": valid_rows[0]["method"],
        "candidates": rows,
    }


def compare_ml_methods(aligned: pd.DataFrame, train_year: int, test_year: int, methods: Sequence[str] = DEFAULT_ML_METHODS) -> List[Dict[str, object]]:
    """Fit ML methods on one year and evaluate them on a later year."""

    train = _period(aligned, train_year)
    test = _period(aligned, test_year)
    if train.empty or test.empty:
        raise ValueError("Not enough pairs to compare ML methods")
    rows: List[Dict[str, object]] = []
    for method in methods:
        try:
            fitted = fit_ml_model(train, method)
            raw, predicted = fitted.predict_frame(test)
            valid = np.isfinite(predicted)
            rows.append({"method": "ml_" + method, "status": "ok", "metrics": metrics(test.loc[valid, "wind_speed_obs"], predicted[valid])})
        except Exception as error:
            rows.append({"method": "ml_" + method, "status": "error", "error": str(error)})
    return rows


def validate_ml_direction(
    aligned: pd.DataFrame,
    train_year: int,
    test_year: int,
    method: str,
    direction_sector_count: int = 16,
) -> Dict[str, object]:
    """Return MCP-compatible predictions for one selected ML method."""

    bare_method = method.removeprefix("ml_")
    train = _period(aligned, train_year)
    test = _period(aligned, test_year)
    fitted = fit_ml_model(train, bare_method)
    raw, predicted = fitted.predict_frame(test)
    valid = np.isfinite(predicted)
    test = test.loc[valid]
    raw = raw[valid]
    predicted = predicted[valid]
    result_metrics = metrics(test["wind_speed_obs"], predicted)
    result_metrics["coverage_start"] = test.index.min().isoformat()
    result_metrics["coverage_end"] = test.index.max().isoformat()
    result_metrics["coverage_hours_observed"] = int(len(test))
    result_metrics["coverage_pairs"] = int(len(test))
    predictions = pd.DataFrame({
        "timestamp": test.index,
        "observed_wind_speed": test["wind_speed_obs"].to_numpy(),
        "reference_wind_speed_raw": test["wind_speed_ref"].to_numpy(),
        "reference_wind_speed": test["wind_speed_ref"].to_numpy(),
        "predicted_wind_speed_raw": raw,
        "predicted_wind_speed": predicted,
    })
    if "wind_direction_obs" in aligned.columns:
        predictions["observed_wind_direction"] = aligned["wind_direction_obs"].reindex(test.index).to_numpy()
    if "wind_direction_ref" in aligned.columns:
        direction_training = aligned.loc[aligned.index.year == int(train_year)]
        direction_model = (
            fit_seasonal_sector_direction_model(
                direction_training["wind_direction_obs"],
                direction_training["wind_direction_ref"],
                direction_training.index,
                n_sectors=direction_sector_count,
            )
            if "wind_direction_obs" in direction_training.columns
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
        reference_direction = aligned["wind_direction_ref"].reindex(test.index)
        predictions["reference_wind_direction_raw"] = reference_direction.to_numpy()
        predictions["predicted_wind_direction"] = apply_seasonal_sector_direction_model(
            reference_direction, test.index, direction_model
        )
    else:
        direction_model = None
    model_summary = {"method": "ml_" + bare_method, "N_train": int(len(train)), "features": fitted.feature_columns}
    if direction_model is not None:
        model_summary["rose_direction_model"] = direction_model
    return {
        "train_year": int(train_year),
        "test_year": int(test_year),
        "model": model_summary,
        "metrics": result_metrics,
        "predictions": predictions,
    }

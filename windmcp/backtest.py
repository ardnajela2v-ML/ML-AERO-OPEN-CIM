"""Walk-forward multi-year backtesting for MCP and ML methods."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd

from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .mcp import (
    DEFAULT_METHODS,
    _fit_and_predict,
    _method_columns,
    metrics,
    validate_direction,
)
from .ml import DEFAULT_ML_METHODS, fit_ml_model, validate_ml_direction
from .multireference import _calendar_and_reference_features, _fit_nnls_sum_one


def _available_years(aligned: pd.DataFrame) -> set[int]:
    return {int(year) for year in pd.DatetimeIndex(aligned.index).year.unique()}


def _pooled_metrics(predictions: List[pd.DataFrame]) -> Dict[str, object]:
    if not predictions:
        return {"N": 0, "RMSE": None, "MAE": None, "Bias": None, "R2": None}
    observed = np.concatenate([frame["observed_wind_speed"].to_numpy(dtype=float) for frame in predictions])
    predicted = np.concatenate([frame["predicted_wind_speed"].to_numpy(dtype=float) for frame in predictions])
    return metrics(observed, predicted)


def _summary_rows(fold_rows: List[Dict[str, Any]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in fold_rows:
        if row["status"] == "ok":
            grouped.setdefault((str(row["family"]), str(row["method"])), []).append(row)
    summary: List[Dict[str, object]] = []
    for (family, method), rows in grouped.items():
        pooled = _pooled_metrics([row["predictions"] for row in rows])
        errors = np.concatenate([
            row["predictions"]["predicted_wind_speed"].to_numpy(dtype=float)
            - row["predictions"]["observed_wind_speed"].to_numpy(dtype=float)
            for row in rows
        ])
        summary.append({
            "family": family,
            "method": method,
            "status": "ok",
            "folds_passed": int(len(rows)),
            "folds_available": int(len({(row["train_year"], row["test_year"]) for row in rows})),
            "train_test_pairs": [f"{row['train_year']}->{row['test_year']}" for row in rows],
            "N": pooled.get("N"),
            "R": pooled.get("R"),
            "R2": pooled.get("R2"),
            "RMSE": pooled.get("RMSE"),
            "MAE": pooled.get("MAE"),
            "Bias": pooled.get("Bias"),
            "KS": pooled.get("KS"),
            "observed_mean": pooled.get("observed_mean"),
            "predicted_mean": pooled.get("predicted_mean"),
            "delta_mean": pooled.get("delta_mean"),
            "delta_mean_pct": pooled.get("delta_mean_pct"),
            "observed_weibull_k": pooled.get("observed_weibull_k"),
            "predicted_weibull_k": pooled.get("predicted_weibull_k"),
            "delta_weibull_k": pooled.get("delta_weibull_k"),
            "observed_weibull_lambda": pooled.get("observed_weibull_lambda"),
            "predicted_weibull_lambda": pooled.get("predicted_weibull_lambda"),
            "delta_weibull_lambda": pooled.get("delta_weibull_lambda"),
            "weibull_fit_method": pooled.get("weibull_fit_method"),
            "error_p05": float(np.percentile(errors, 5)),
            "error_p95": float(np.percentile(errors, 95)),
            "error_std": float(np.std(errors, ddof=1)) if len(errors) > 1 else 0.0,
            "interval_level": "empirical 90% global residual interval",
        })
    summary.sort(key=lambda row: (row.get("RMSE") if row.get("RMSE") is not None else np.inf, row.get("MAE") if row.get("MAE") is not None else np.inf))
    return summary


def select_speed_reconstruction_method(
    summary: List[Dict[str, object]],
    max_abs_bias_ms: float = 0.20,
    max_abs_delta_weibull_k: float = 0.10,
    max_abs_delta_weibull_lambda_ms: float = 0.20,
    max_ks: float = 0.08,
    preferred_method: str | None = None,
) -> Dict[str, object]:
    """Choose a speed model without allowing rose sectors to force the result.

    Models first have to preserve the out-of-sample speed distribution.  The
    lowest-RMSE model among those that pass is selected. Sector quantile models
    remain visible as diagnostics, but are excluded because the 16-sector
    model is reserved for direction and wind-rose reconstruction.
    """

    thresholds = {
        "max_abs_bias_ms": float(max_abs_bias_ms),
        "max_abs_delta_weibull_k": float(max_abs_delta_weibull_k),
        "max_abs_delta_weibull_lambda_ms": float(max_abs_delta_weibull_lambda_ms),
        "max_ks": float(max_ks),
    }
    eligible: List[Dict[str, object]] = []
    for row in summary:
        method = str(row.get("method", ""))
        if method.startswith("sector_qm_"):
            row["speed_selection_eligible"] = False
            row["speed_selection_status"] = "directional_diagnostic_only"
            continue
        if method.startswith("multi_reference_"):
            row["speed_selection_eligible"] = False
            row["speed_selection_status"] = "comparative_diagnostic_only"
            continue
        values = {
            "Bias": row.get("Bias"),
            "delta_weibull_k": row.get("delta_weibull_k"),
            "delta_weibull_lambda": row.get("delta_weibull_lambda"),
            "KS": row.get("KS"),
        }
        finite = all(value is not None and np.isfinite(float(value)) for value in values.values())
        qualified = bool(finite and (
            abs(float(values["Bias"])) <= max_abs_bias_ms
            and abs(float(values["delta_weibull_k"])) <= max_abs_delta_weibull_k
            and abs(float(values["delta_weibull_lambda"])) <= max_abs_delta_weibull_lambda_ms
            and float(values["KS"]) <= max_ks
        ))
        row["speed_selection_eligible"] = True
        row["distribution_qualified"] = qualified
        row["speed_selection_status"] = "qualified" if qualified else "distribution_rejected"
        penalty = np.inf
        if finite:
            penalty = (
                abs(float(values["Bias"])) / max_abs_bias_ms
                + abs(float(values["delta_weibull_k"])) / max_abs_delta_weibull_k
                + abs(float(values["delta_weibull_lambda"])) / max_abs_delta_weibull_lambda_ms
                + float(values["KS"]) / max_ks
            )
        row["distribution_penalty"] = None if not np.isfinite(penalty) else float(penalty)
        eligible.append(row)
    if not eligible:
        raise ValueError("No non-sectoral speed model is available for reconstruction")
    qualified_rows = [row for row in eligible if row.get("distribution_qualified")]
    preferred = next(
        (row for row in qualified_rows if str(row.get("method")) == str(preferred_method)),
        None,
    )
    if preferred is not None:
        candidates = [preferred]
        policy = "distribution_constraints_then_explicit_preferred_method"
        fallback_used = False
    elif qualified_rows:
        candidates = qualified_rows
        policy = "distribution_constraints_then_out_of_sample_rmse"
        fallback_used = False
    else:
        candidates = eligible
        policy = "minimum_distribution_penalty_then_out_of_sample_rmse_fallback"
        fallback_used = True
    candidates.sort(key=lambda row: (
        row.get("RMSE") if row.get("RMSE") is not None else np.inf,
        row.get("MAE") if row.get("MAE") is not None else np.inf,
    ) if not fallback_used else (
        row.get("distribution_penalty") if row.get("distribution_penalty") is not None else np.inf,
        row.get("RMSE") if row.get("RMSE") is not None else np.inf,
    ))
    selected = candidates[0]
    selected["speed_selection_status"] = "selected"
    rmse_winner = min(eligible, key=lambda row: (
        row.get("RMSE") if row.get("RMSE") is not None else np.inf,
        row.get("MAE") if row.get("MAE") is not None else np.inf,
    ))
    return {
        "selected_method": selected["method"],
        "selected_family": selected["family"],
        "selection": policy,
        "fallback_used": fallback_used,
        "thresholds": thresholds,
        "rmse_only_winner": rmse_winner["method"],
        "preferred_method": preferred_method,
        "preferred_method_applied": bool(
            preferred_method is not None and str(selected.get("method")) == str(preferred_method)
        ),
        "selected_metrics": {
            key: selected.get(key)
            for key in ("N", "R", "R2", "RMSE", "MAE", "Bias", "KS", "delta_weibull_k", "delta_weibull_lambda")
        },
        "excluded_speed_methods": [
            str(row.get("method")) for row in summary if not row.get("speed_selection_eligible", False)
        ],
        "direction_model_policy": "independent_16_sector_month_conditioned_circular_model_for_wind_rose_only",
    }


def select_speed_model_training_holdout(
    aligned: pd.DataFrame,
    training_year: int,
    mcp_methods: Sequence[str] = DEFAULT_METHODS,
    ml_methods: Sequence[str] = DEFAULT_ML_METHODS,
    validation_fraction: float = 0.25,
    multi_reference_frame: "pd.DataFrame | None" = None,
    multi_reference_speed_columns: "list[str] | None" = None,
) -> Dict[str, object]:
    """Select the final speed model without consulting the test year.

    Evaluates MCP, single-ref ML, and (when provided) multi-reference
    candidates on the same chronological 75/25 holdout of the training year.
    Distribution constraints are applied first; RMSE/MAE break ties.
    """

    period = aligned.loc[aligned.index.year == int(training_year)].sort_index()
    period = period.dropna(subset=["wind_speed_obs", "wind_speed_ref"])
    if len(period) < 40:
        raise ValueError("Not enough rows to select a speed model in %s" % training_year)
    split = min(
        max(int(np.floor(len(period) * (1.0 - float(validation_fraction)))), 20),
        len(period) - 20,
    )
    train = period.iloc[:split]
    validation = period.iloc[split:]
    rows: List[Dict[str, object]] = []

    for method in mcp_methods:
        try:
            columns = _method_columns(period, method)
            method_train = train[columns].dropna()
            method_validation = validation[columns].dropna()
            _, _, predicted = _fit_and_predict(method, method_train, method_validation)
            result = metrics(method_validation["wind_speed_obs"], predicted)
            rows.append({"family": "mcp", "method": method, "status": "ok", **result})
        except Exception as error:
            rows.append({"family": "mcp", "method": method, "status": "error", "error": str(error)})

    for method in ml_methods:
        public_method = "ml_" + str(method).removeprefix("ml_")
        try:
            fitted = fit_ml_model(train, str(method).removeprefix("ml_"))
            predicted_frame = validation[[
                "wind_speed_obs", "wind_speed_ref",
                *(["wind_direction_ref"] if "wind_direction_ref" in validation.columns else []),
            ]].dropna()
            _, predicted = fitted.predict_frame(predicted_frame)
            valid = np.isfinite(predicted)
            result = metrics(predicted_frame.loc[valid, "wind_speed_obs"], predicted[valid])
            rows.append({"family": "ml", "method": public_method, "status": "ok", **result})
        except Exception as error:
            rows.append({"family": "ml", "method": public_method, "status": "error", "error": str(error)})

    if multi_reference_frame is not None and multi_reference_speed_columns:
        mr_period = (
            multi_reference_frame.loc[multi_reference_frame.index.year == int(training_year)]
            .dropna(subset=["wind_speed_obs", *multi_reference_speed_columns])
            .sort_index()
        )
        if len(mr_period) >= 40:
            mr_split = min(
                max(int(np.floor(len(mr_period) * (1.0 - float(validation_fraction)))), 20),
                len(mr_period) - 20,
            )
            mr_train = mr_period.iloc[:mr_split]
            mr_val = mr_period.iloc[mr_split:]
            try:
                weights = _fit_nnls_sum_one(
                    mr_train[multi_reference_speed_columns].to_numpy(),
                    mr_train["wind_speed_obs"].to_numpy(),
                )
                pred = np.maximum(mr_val[multi_reference_speed_columns].to_numpy().dot(weights), 0.0)
                result = metrics(mr_val["wind_speed_obs"], pred)
                rows.append({"family": "multiref", "method": "multi_reference_nnls", "status": "ok", **result})
            except Exception as error:
                rows.append({"family": "multiref", "method": "multi_reference_nnls", "status": "error", "error": str(error)})
            feat_frame = _calendar_and_reference_features(mr_period, multi_reference_speed_columns)
            feat_frame = feat_frame.copy()
            feat_frame["wind_speed_obs"] = mr_period["wind_speed_obs"]
            feat_train = feat_frame.iloc[:mr_split].dropna()
            feat_val = feat_frame.iloc[mr_split:].dropna()
            if len(feat_train) >= 20 and len(feat_val) >= 2:
                x_cols = [c for c in feat_train.columns if c != "wind_speed_obs"]
                mr_estimators = {
                    "multi_reference_ridge": Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=10.0))]),
                    "multi_reference_random_forest": RandomForestRegressor(
                        n_estimators=80, max_depth=16, min_samples_leaf=5, n_jobs=-1, random_state=42),
                    "multi_reference_gradient_boosting": GradientBoostingRegressor(
                        n_estimators=120, learning_rate=0.04, max_depth=3,
                        min_samples_leaf=8, loss="huber", random_state=42),
                }
                for method, estimator in mr_estimators.items():
                    try:
                        estimator.fit(feat_train[x_cols], feat_train["wind_speed_obs"])
                        predicted = np.maximum(estimator.predict(feat_val[x_cols]), 0.0)
                        valid = np.isfinite(predicted)
                        result = metrics(feat_val.loc[valid, "wind_speed_obs"], predicted[valid])
                        rows.append({"family": "multiref", "method": method, "status": "ok", **result})
                    except Exception as error:
                        rows.append({"family": "multiref", "method": method, "status": "error", "error": str(error)})

    successful = [row for row in rows if row["status"] == "ok"]
    if not successful:
        raise ValueError("No MCP or ML speed model could be selected in %s" % training_year)
    selected = select_speed_reconstruction_method(successful, preferred_method=None)
    return {
        **selected,
        "training_year": int(training_year),
        "selection_scope": "chronological_holdout_inside_training_year_only",
        "validation_fraction": float(validation_fraction),
        "fit_period_start": train.index.min().isoformat(),
        "fit_period_end": train.index.max().isoformat(),
        "selection_period_start": validation.index.min().isoformat(),
        "selection_period_end": validation.index.max().isoformat(),
        "pairs_fit_base": int(len(train)),
        "pairs_selection_base": int(len(validation)),
        "candidates": rows,
        "test_year_used_for_selection": False,
    }


def run_walk_forward_backtest(
    aligned: pd.DataFrame,
    folds: Iterable[Tuple[int, int]],
    mcp_methods: Sequence[str] = DEFAULT_METHODS,
    ml_methods: Sequence[str] = DEFAULT_ML_METHODS,
) -> Dict[str, object]:
    """Evaluate methods on chronological train/test folds.

    The target year is never used for fitting. Pooled metrics are calculated
    from concatenated out-of-sample predictions across all available folds.
    """

    available = _available_years(aligned)
    requested_folds = [(int(train), int(test)) for train, test in folds]
    valid_folds = [(train, test) for train, test in requested_folds if train in available and test in available and train < test]
    skipped_folds = [{"train_year": train, "test_year": test, "reason": "year_not_available_or_not_chronological"} for train, test in requested_folds if (train, test) not in valid_folds]
    if not valid_folds:
        raise ValueError("No valid chronological folds available for multi-year backtesting")

    fold_rows: List[Dict[str, Any]] = []
    for train_year, test_year in valid_folds:
        for method in mcp_methods:
            try:
                validation = validate_direction(aligned, train_year, test_year, method=method)
                fold_rows.append({
                    "family": "mcp",
                    "method": method,
                    "train_year": train_year,
                    "test_year": test_year,
                    "status": "ok",
                    "metrics": validation["metrics"],
                    "predictions": validation["predictions"],
                })
            except (ValueError, KeyError) as error:
                fold_rows.append({
                    "family": "mcp",
                    "method": method,
                    "train_year": train_year,
                    "test_year": test_year,
                    "status": "error",
                    "error": str(error),
                })
        for method in ml_methods:
            try:
                validation = validate_ml_direction(aligned, train_year, test_year, method="ml_" + method)
                fold_rows.append({
                    "family": "ml",
                    "method": "ml_" + method,
                    "train_year": train_year,
                    "test_year": test_year,
                    "status": "ok",
                    "metrics": validation["metrics"],
                    "predictions": validation["predictions"],
                })
            except (ValueError, KeyError) as error:
                fold_rows.append({
                    "family": "ml",
                    "method": "ml_" + method,
                    "train_year": train_year,
                    "test_year": test_year,
                    "status": "error",
                    "error": str(error),
                })

    summary = _summary_rows(fold_rows)
    selected = summary[0] if summary else None
    return {
        "selection": "pooled_out_of_sample_rmse_then_mae",
        "requested_folds": [{"train_year": train, "test_year": test} for train, test in requested_folds],
        "valid_folds": [{"train_year": train, "test_year": test} for train, test in valid_folds],
        "skipped_folds": skipped_folds,
        "selected_method": selected["method"] if selected else None,
        "selected_family": selected["family"] if selected else None,
        "summary": summary,
        "folds": [
            {key: value for key, value in row.items() if key != "predictions"}
            for row in fold_rows
        ],
    }

"""Assess whether a concurrent MCP record is long enough for reconstruction.

This is a decision-support diagnostic, not a universal certification. A valid
long-term reconstruction depends on the quality and representativeness of the
reference, so the report combines calendar coverage, seasonal coverage,
out-of-sample performance and model stability.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .mcp import fit_linear, metrics, predict_linear


DEFAULT_DURATIONS_MONTHS = (3, 6, 12, 18, 24, 36)


def _season_count(index: pd.DatetimeIndex) -> int:
    if len(index) == 0:
        return 0
    months = set(int(value) for value in index.month)
    seasons = 0
    for season in ({12, 1, 2}, {3, 4, 5}, {6, 7, 8}, {9, 10, 11}):
        if months.intersection(season):
            seasons += 1
    return seasons


def _period_start(period: pd.Period) -> pd.Timestamp:
    return period.start_time.tz_localize("UTC")


def _window_end(period: pd.Period) -> pd.Timestamp:
    return (period + 1).start_time.tz_localize("UTC")


def _model_stability(window: pd.DataFrame) -> Dict[str, Optional[float]]:
    midpoint = len(window) // 2
    first = window.iloc[:midpoint]
    second = window.iloc[midpoint:]
    if len(first) < 2 or len(second) < 2:
        return {"slope_first": None, "slope_second": None, "slope_change_pct": None}
    try:
        first_model = fit_linear(first["wind_speed_ref"], first["wind_speed_obs"])
        second_model = fit_linear(second["wind_speed_ref"], second["wind_speed_obs"])
    except ValueError:
        return {"slope_first": None, "slope_second": None, "slope_change_pct": None}
    denominator = max(abs(first_model["slope"]), abs(second_model["slope"]), 1e-12)
    return {
        "slope_first": first_model["slope"],
        "slope_second": second_model["slope"],
        "slope_change_pct": abs(first_model["slope"] - second_model["slope"]) / denominator * 100.0,
    }


def _assess_window(
    window: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    duration_months: int,
    minimum_pairs: int,
    minimum_coverage_pct: float,
    maximum_slope_change_pct: float,
    minimum_r2: float,
    maximum_abs_bias: float,
) -> Dict[str, Any]:
    pairs = int(len(window))
    expected_hours = max(1, int((end - start).total_seconds() / 3600))
    coverage_pct = float(pairs / expected_hours * 100.0)
    months_present = sorted({int(value) for value in window.index.month})
    seasons = _season_count(window.index)
    minimum_seasons = 4 if duration_months >= 12 else 2
    split = max(2, int(np.floor(pairs * 0.7))) if pairs else 0
    train = window.iloc[:split]
    test = window.iloc[split:]
    try:
        model = fit_linear(train["wind_speed_ref"], train["wind_speed_obs"])
        _, predicted = predict_linear(model, test["wind_speed_ref"].to_numpy())
        validation_metrics = metrics(test["wind_speed_obs"], predicted)
    except (ValueError, IndexError):
        model = None
        validation_metrics = {"N": 0}
    stability = _model_stability(window)
    slope_change = stability["slope_change_pct"]
    r2 = validation_metrics.get("R2")
    bias = validation_metrics.get("Bias")
    checks = {
        "minimum_pairs": pairs >= minimum_pairs,
        "minimum_coverage": coverage_pct >= minimum_coverage_pct,
        "seasonal_coverage": seasons >= minimum_seasons,
        "out_of_sample_r2": r2 is not None and float(r2) >= minimum_r2,
        "out_of_sample_bias": bias is not None and abs(float(bias)) <= maximum_abs_bias,
        "model_stability": slope_change is not None and float(slope_change) <= maximum_slope_change_pct,
    }
    if duration_months >= 12 and all(checks.values()):
        status = "recommended"
    elif pairs < minimum_pairs or coverage_pct < minimum_coverage_pct or test.empty:
        status = "insufficient"
    else:
        status = "conditional"
    return {
        "duration_months": int(duration_months),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pairs": pairs,
        "expected_hours": expected_hours,
        "coverage_pct": round(coverage_pct, 4),
        "months_present": months_present,
        "seasons_present": seasons,
        "model": model,
        "validation_metrics": validation_metrics,
        "stability": stability,
        "checks": checks,
        "status": status,
    }


def assess_reconstruction_readiness(
    aligned: pd.DataFrame,
    *,
    durations_months: Iterable[int] = DEFAULT_DURATIONS_MONTHS,
    minimum_pairs: int = 1000,
    minimum_coverage_pct: float = 80.0,
    maximum_slope_change_pct: float = 20.0,
    minimum_r2: float = 0.5,
    maximum_abs_bias: float = 1.0,
) -> Dict[str, Any]:
    """Return a transparent duration/readiness report for one reference."""

    required = {"wind_speed_obs", "wind_speed_ref"}
    missing = required.difference(aligned.columns)
    if missing:
        raise ValueError("Readiness analysis requires columns: %s" % sorted(missing))
    frame = aligned[["wind_speed_obs", "wind_speed_ref"]].dropna().sort_index()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.empty:
        raise ValueError("Readiness analysis requires a non-empty UTC time index")
    frame = frame[~frame.index.duplicated(keep="first")]

    duration_values = tuple(sorted({int(value) for value in durations_months if int(value) > 0}))
    if not duration_values:
        raise ValueError("durations_months must contain at least one positive duration")
    first_period = frame.index.min().tz_localize(None).to_period("M")
    last_period = frame.index.max().tz_localize(None).to_period("M")
    assessments: List[Dict[str, Any]] = []
    for duration in duration_values:
        windows: List[Dict[str, Any]] = []
        for end_period in pd.period_range(first_period + duration, last_period + 1, freq="M"):
            start = _period_start(end_period - duration)
            end = _window_end(end_period)
            window = frame.loc[(frame.index >= start) & (frame.index < end)]
            if window.empty:
                continue
            windows.append(_assess_window(
                window,
                start=start,
                end=end,
                duration_months=duration,
                minimum_pairs=minimum_pairs,
                minimum_coverage_pct=minimum_coverage_pct,
                maximum_slope_change_pct=maximum_slope_change_pct,
                minimum_r2=minimum_r2,
                maximum_abs_bias=maximum_abs_bias,
            ))
        recommended_windows = [window for window in windows if window["status"] == "recommended"]
        conditional_windows = [window for window in windows if window["status"] == "conditional"]
        minimum_recommended_windows = max(1, int(np.ceil(len(windows) * 0.5))) if windows else 0
        best = max(windows, key=lambda row: (row["coverage_pct"], row["pairs"])) if windows else None
        assessments.append({
            "duration_months": duration,
            "windows_evaluated": len(windows),
            "recommended_windows": len(recommended_windows),
            "conditional_windows": len(conditional_windows),
            "minimum_recommended_windows": minimum_recommended_windows,
            "best_window": best,
            "status": "recommended" if windows and len(recommended_windows) >= minimum_recommended_windows else ("conditional" if conditional_windows else "insufficient"),
        })

    recommended = [row["duration_months"] for row in assessments if row["status"] == "recommended"]
    conditional = [row["duration_months"] for row in assessments if row["status"] == "conditional"]
    if recommended:
        overall = "recommended"
        minimum_recommended = min(recommended)
    elif conditional:
        overall = "conditional"
        minimum_recommended = None
    else:
        overall = "insufficient"
        minimum_recommended = None
    return {
        "status": overall,
        "minimum_recommended_months": minimum_recommended,
        "minimum_conditional_months": min(conditional) if conditional else None,
        "record_start": frame.index.min().isoformat(),
        "record_end": frame.index.max().isoformat(),
        "record_pairs": int(len(frame)),
        "criteria": {
            "minimum_pairs": minimum_pairs,
            "minimum_coverage_pct": minimum_coverage_pct,
            "maximum_slope_change_pct": maximum_slope_change_pct,
            "minimum_r2": minimum_r2,
            "maximum_abs_bias": maximum_abs_bias,
            "recommended_requires_at_least_months": 12,
            "recommended_requires_four_seasons": True,
            "recommended_requires_at_least_half_of_tested_windows": True,
        },
        "durations": assessments,
        "interpretation": (
            "The recommended duration is the shortest tested window meeting all criteria. "
            "This is an evidence-based screening result, not a bankability certification."
        ),
    }

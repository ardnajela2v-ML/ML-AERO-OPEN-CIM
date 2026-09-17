"""Vertical wind-profile estimation and height normalization.

Wind speeds from different sensors or reanalysis products must be brought to
one height before MCP fitting.  WindMCP estimates a power-law shear exponent
from the multi-height CERTE observations and applies that exponent to each
reference series.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from .data import ReferenceData


@dataclass(frozen=True)
class VerticalProfile:
    """Power-law vertical profile fitted to CERTE observations."""

    target_height_m: float
    observation_height_m: float
    alpha: float
    rows_used: int
    profile_points: int
    alpha_p10: Optional[float]
    alpha_p50: Optional[float]
    alpha_p90: Optional[float]
    source_columns: Dict[str, str]
    method: str = "median_rowwise_power_law"

    def factor(self, source_height_m: float) -> float:
        if source_height_m <= 0:
            raise ValueError("source_height_m must be positive")
        return float((self.target_height_m / source_height_m) ** self.alpha)

    def as_dict(self) -> Dict[str, object]:
        return {
            "method": self.method,
            "law": "V(z) = V(z_ref) * (z / z_ref)^alpha",
            "target_height_m": self.target_height_m,
            "observation_height_m": self.observation_height_m,
            "alpha": self.alpha,
            "alpha_p10": self.alpha_p10,
            "alpha_p50": self.alpha_p50,
            "alpha_p90": self.alpha_p90,
            "rows_used": self.rows_used,
            "profile_points": self.profile_points,
            "source_columns": self.source_columns,
        }


def _height_from_column(column: str) -> Optional[float]:
    match = re.search(r"_(\d+(?:\.\d+)?)m", str(column), re.I)
    return float(match.group(1)) if match else None


def _profile_columns(frame: pd.DataFrame, observation_height_m: float) -> Dict[float, str]:
    columns: Dict[float, str] = {}
    for column in frame.columns:
        height = _height_from_column(str(column))
        if height is not None and str(column).startswith("WS_") and str(column).endswith("_mean"):
            columns[height] = str(column)
    # Prefer each height's A/B composite (or its sole unsuffixed sensor).
    for height in list(columns):
        combined = f"WS_{height:g}m_mean"
        if combined in frame:
            columns[height] = combined
    if "wind_speed_obs" in frame.columns:
        columns[float(observation_height_m)] = "wind_speed_obs"
    return dict(sorted(columns.items()))


def fit_vertical_profile(
    frame: pd.DataFrame,
    *,
    observation_height_m: float,
    target_height_m: float,
    training_year: int,
    min_rows: int = 30,
    max_wind_speed: float = 70.0,
) -> VerticalProfile:
    """Estimate a robust power-law exponent from CERTE multi-height data.

    One exponent is fitted per timestamp using all available heights, then the
    median exponent from the training year is used. This keeps the correction
    stable while reducing the influence of individual turbulent observations.
    """

    if observation_height_m <= 0 or target_height_m <= 0:
        raise ValueError("Observation and target heights must be positive")
    columns = _profile_columns(frame, observation_height_m)
    if len(columns) < 2:
        raise ValueError("CERTE requires at least two mean wind-speed heights to estimate vertical shear")

    training = frame.loc[frame.index.year == int(training_year)]
    heights = np.asarray(list(columns.keys()), dtype=float)
    names = list(columns.values())
    row_alphas = []
    profile_points = 0
    for values in training[names].to_numpy(dtype=float):
        valid = np.isfinite(values) & (values > 0.0) & (values <= max_wind_speed)
        if int(valid.sum()) < 2:
            continue
        log_heights = np.log(heights[valid])
        log_speeds = np.log(values[valid])
        alpha = float(np.polyfit(log_heights, log_speeds, 1)[0])
        if np.isfinite(alpha) and -1.0 < alpha < 2.0:
            row_alphas.append(alpha)
            profile_points += int(valid.sum())

    if len(row_alphas) < min_rows:
        raise ValueError(
            "Insufficient valid CERTE multi-height rows in training year %s: %s/%s"
            % (training_year, len(row_alphas), min_rows)
        )
    alpha_values = np.asarray(row_alphas, dtype=float)
    alpha = float(np.median(alpha_values))
    return VerticalProfile(
        target_height_m=float(target_height_m),
        observation_height_m=float(observation_height_m),
        alpha=alpha,
        rows_used=int(len(alpha_values)),
        profile_points=int(profile_points),
        alpha_p10=float(np.percentile(alpha_values, 10)),
        alpha_p50=float(np.percentile(alpha_values, 50)),
        alpha_p90=float(np.percentile(alpha_values, 90)),
        source_columns={str(height): name for height, name in columns.items()},
    )


def normalize_reference_height(
    reference: ReferenceData,
    profile: VerticalProfile,
) -> Tuple[ReferenceData, Dict[str, object]]:
    """Normalize one reference speed series to the profile target height."""

    source_height = reference.metadata.get("wind_height_m")
    try:
        source_height_m = float(source_height)
    except (TypeError, ValueError):
        raise ValueError("Reference %s does not declare a valid wind height" % reference.name) from None
    if source_height_m <= 0:
        raise ValueError("Reference %s has an invalid wind height" % reference.name)

    factor = profile.factor(source_height_m)
    frame = reference.frame.copy()
    raw_column = "wind_speed_ref_raw"
    frame[raw_column] = pd.to_numeric(frame["wind_speed_ref"], errors="coerce")
    frame["wind_speed_ref"] = frame[raw_column] * factor
    if np.isclose(source_height_m, profile.target_height_m):
        operation = "same_height"
    elif source_height_m < profile.target_height_m:
        operation = "extrapolation_up"
    else:
        operation = "extrapolation_down"

    adjustment: Dict[str, object] = {
        "source_height_m": source_height_m,
        "target_height_m": profile.target_height_m,
        "alpha": profile.alpha,
        "factor": factor,
        "operation": operation,
        "speed_column": "wind_speed_ref",
        "raw_speed_column": raw_column,
    }
    metadata = dict(reference.metadata)
    metadata["original_wind_height_m"] = source_height_m
    metadata["wind_height_m"] = profile.target_height_m
    metadata["vertical_adjustment"] = adjustment
    return ReferenceData(reference.name, frame, metadata), adjustment


def normalize_observation_height(
    frame: pd.DataFrame,
    *,
    source_height_m: float,
    profile: VerticalProfile,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Normalize the CERTE target series when it differs from the target height."""

    factor = profile.factor(float(source_height_m))
    result = frame.copy()
    result["wind_speed_obs_raw"] = pd.to_numeric(result["wind_speed_obs"], errors="coerce")
    result["wind_speed_obs"] = result["wind_speed_obs_raw"] * factor
    if np.isclose(float(source_height_m), profile.target_height_m):
        operation = "same_height"
    elif float(source_height_m) < profile.target_height_m:
        operation = "extrapolation_up"
    else:
        operation = "extrapolation_down"
    return result, {
        "source_height_m": float(source_height_m),
        "target_height_m": profile.target_height_m,
        "alpha": profile.alpha,
        "factor": factor,
        "operation": operation,
        "speed_column": "wind_speed_obs",
        "raw_speed_column": "wind_speed_obs_raw",
    }

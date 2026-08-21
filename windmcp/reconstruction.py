"""Long-term reconstruction using a selected normalized reference and model."""

from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

from .data import ReferenceData, resample_hourly
from .mcp import (apply_direction_offset, apply_seasonal_sector_direction_model,
                  apply_sector_direction_model, predict_mcp)


def reconstruct_historical(
    observations: pd.DataFrame,
    reference: ReferenceData,
    model: Any,
    uncertainty: Dict[str, Any] | None = None,
    direction_offset_deg: float = 0.0,
    direction_model: Dict[str, Any] | None = None,
    direction_reference: ReferenceData | None = None,
) -> Tuple[pd.DataFrame, Dict[str, Any], pd.DataFrame]:
    """Build a long-term hourly series, preserving valid CERTE observations.

    The selected MCP or ML model is applied to the complete available reference record. Where
    CERTE contains a valid hourly observation, that observation is retained in
    ``long_term_wind_speed``; elsewhere the model prediction is used.
    """

    if "wind_speed_obs" not in observations.columns:
        raise ValueError("Observations require wind_speed_obs")
    if "wind_speed_ref" not in reference.frame.columns:
        raise ValueError("Reference requires wind_speed_ref")
    observation_columns = ["wind_speed_obs"]
    if "wind_direction_obs" in observations.columns:
        observation_columns.append("wind_direction_obs")
    observation_hourly = resample_hourly(observations[observation_columns])
    reference_hourly = resample_hourly(reference.frame)
    if isinstance(model, dict):
        direction = reference_hourly.get("wind_direction_ref") if str(model.get("method", "")).startswith("sector_qm_") else None
        raw, predicted = predict_mcp(
            model, reference_hourly["wind_speed_ref"].to_numpy(), direction=direction,
            timestamps=reference_hourly.index,
        )
        model_method = str(model.get("method", "linear"))
    elif hasattr(model, "predict_frame"):
        raw, predicted = model.predict_frame(reference_hourly)
        model_method = "ml_" + str(getattr(model, "method", "unknown"))
    else:
        raise ValueError("Unsupported reconstruction model")
    result = pd.DataFrame(index=reference_hourly.index)
    result.index.name = "timestamp"
    result["reference_wind_speed_raw"] = reference_hourly.get("wind_speed_ref_raw")
    result["reference_wind_speed"] = reference_hourly["wind_speed_ref"].to_numpy()
    direction_reference = direction_reference or reference
    direction_reference_hourly = resample_hourly(direction_reference.frame)
    direction_series = direction_reference_hourly.get("wind_direction_ref")
    if direction_series is not None:
        direction_series = direction_series.reindex(result.index)
        result["reference_wind_direction_raw"] = direction_series.to_numpy()
        result["model_wind_direction"] = (
            apply_seasonal_sector_direction_model(direction_series, result.index, direction_model)
            if direction_model and str(direction_model.get("method", "")).startswith("seasonal_")
            else apply_sector_direction_model(direction_series, direction_model)
            if direction_model
            else apply_direction_offset(direction_series, direction_offset_deg)
        )
    result["model_wind_speed_raw"] = raw
    result["model_wind_speed"] = predicted
    if uncertainty and uncertainty.get("error_p05") is not None and uncertainty.get("error_p95") is not None:
        result["model_wind_speed_p05"] = np.maximum(predicted + float(uncertainty["error_p05"]), 0.0)
        result["model_wind_speed_p95"] = np.maximum(predicted + float(uncertainty["error_p95"]), 0.0)
    # Keep MCP aliases for compatibility with earlier output consumers.
    result["mcp_wind_speed_raw"] = result["model_wind_speed_raw"]
    result["mcp_wind_speed"] = result["model_wind_speed"]
    result["observed_wind_speed"] = observation_hourly["wind_speed_obs"].reindex(result.index)
    result["long_term_wind_speed"] = result["observed_wind_speed"].combine_first(result["model_wind_speed"])
    result["data_source"] = np.where(result["observed_wind_speed"].notna(), "CERTE_observed", "MCP_reconstructed")
    if "model_wind_direction" in result.columns:
        result["observed_wind_direction"] = observation_hourly.get(
            "wind_direction_obs", pd.Series(index=observation_hourly.index, dtype=float)
        ).reindex(result.index)
        result["wind_direction"] = result["observed_wind_direction"].combine_first(result["model_wind_direction"])
        result["direction_data_source"] = np.where(
            result["observed_wind_direction"].notna(), "CERTE_observed", "MCP_direction_corrected"
        )
    result = result.reset_index()

    source_counts = result["data_source"].value_counts().to_dict()
    annual = result.assign(year=pd.to_datetime(result["timestamp"], utc=True).dt.year).groupby("year").agg(
        hours=("long_term_wind_speed", lambda values: int(values.notna().sum())),
        mean_long_term_wind_speed=("long_term_wind_speed", "mean"),
        mean_observed_wind_speed=("observed_wind_speed", "mean"),
        mean_model_wind_speed=("model_wind_speed", "mean"),
        mean_mcp_wind_speed=("mcp_wind_speed", "mean"),
        observed_hours=("observed_wind_speed", lambda values: int(values.notna().sum())),
        reconstructed_hours=("data_source", lambda values: int((values == "MCP_reconstructed").sum())),
    ).reset_index()
    summary: Dict[str, Any] = {
        "model_method": model_method,
        "period_start": result["timestamp"].min().isoformat(),
        "period_end": result["timestamp"].max().isoformat(),
        "rows": int(len(result)),
        "valid_long_term_hours": int(result["long_term_wind_speed"].notna().sum()),
        "source_counts": {str(key): int(value) for key, value in source_counts.items()},
        "observed_share_pct": round(float((result["data_source"] == "CERTE_observed").mean() * 100.0), 4),
        "reconstructed_share_pct": round(float((result["data_source"] == "MCP_reconstructed").mean() * 100.0), 4),
        "uncertainty": uncertainty or {},
        "direction_offset_deg": float(direction_offset_deg),
        "direction_model": {
            "method": direction_model.get("method"),
            "purpose": direction_model.get("purpose"),
            "n_sectors": direction_model.get("n_sectors"),
            "training_pairs": direction_model.get("N_train"),
            "seasonality": direction_model.get("seasonality"),
        } if direction_model else None,
        "speed_reference": reference.name,
        "direction_reference": direction_reference.name,
        "note": "CERTE valid hourly values are preserved; the selected speed model fills gaps, while the independent sector direction model reconstructs the wind rose.",
    }
    return result, summary, annual

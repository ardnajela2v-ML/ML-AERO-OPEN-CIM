"""Non-destructive quality-control flags for wind time series."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd


def quality_control(
    frame: pd.DataFrame,
    speed_column: str,
    direction_column: Optional[str] = None,
    expected_minutes: Optional[int] = None,
    max_wind_speed: float = 70.0,
    max_jump: float = 15.0,
    max_persistence: int = 6,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Flag records and return a report; input records are never deleted."""

    if speed_column not in frame.columns:
        raise ValueError("QC speed column not found: %s" % speed_column)
    result = frame.copy().sort_index()
    if not isinstance(result.index, pd.DatetimeIndex):
        raise ValueError("QC requires a DatetimeIndex")
    speed = pd.to_numeric(result[speed_column], errors="coerce")
    flags = pd.DataFrame(False, index=result.index, columns=[
        "missing", "speed_range", "direction_range", "duplicate", "gap", "persistence", "jump"
    ])
    flags["missing"] = speed.isna()
    flags["speed_range"] = speed.notna() & ((speed < 0) | (speed > max_wind_speed))
    if direction_column and direction_column in result.columns:
        direction = pd.to_numeric(result[direction_column], errors="coerce")
        flags["direction_range"] = direction.notna() & ((direction < 0) | (direction >= 360))
    duplicate_mask = result.index.duplicated(keep=False)
    flags["duplicate"] = duplicate_mask

    differences = result.index.to_series().diff()
    if expected_minutes is None:
        positive = differences[differences > pd.Timedelta(0)]
        expected = positive.median() if not positive.empty else pd.Timedelta(minutes=10)
    else:
        expected = pd.Timedelta(minutes=expected_minutes)
    flags["gap"] = differences > expected * 1.5

    rounded = speed.round(9)
    run_id = rounded.ne(rounded.shift()).cumsum()
    run_lengths = rounded.groupby(run_id).transform("size")
    flags["persistence"] = speed.notna() & (run_lengths >= max_persistence)

    flags["jump"] = speed.diff().abs() > max_jump
    invalid = flags["speed_range"] | flags["direction_range"]
    suspect = flags.drop(columns=["missing", "speed_range", "direction_range"]).any(axis=1)
    status = pd.Series("VALID", index=result.index, dtype="object")
    status.loc[suspect] = "SUSPECT"
    status.loc[flags["missing"]] = "MISSING"
    status.loc[invalid] = "INVALID"
    result["quality_status"] = status
    result["quality_eligible"] = status.eq("VALID")
    for column in flags.columns:
        result["qc_" + column] = flags[column]

    counts = status.value_counts().to_dict()
    report = {
        "records_original": int(len(result)),
        "records_valid": int(counts.get("VALID", 0)),
        "records_suspect": int(counts.get("SUSPECT", 0)),
        "records_invalid": int(counts.get("INVALID", 0)),
        "records_missing": int(counts.get("MISSING", 0)),
        "availability_pct": round(float(status.eq("VALID").mean() * 100) if len(status) else 0.0, 4),
        "expected_interval_minutes": round(expected.total_seconds() / 60.0, 4),
        "flags": {column: int(value) for column, value in flags.sum().items()},
        "thresholds": {
            "max_wind_speed": max_wind_speed,
            "max_jump": max_jump,
            "max_persistence": max_persistence,
        },
    }
    return result, report

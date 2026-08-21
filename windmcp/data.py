"""Readers and temporal alignment for CERTE and Windnavigator files."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


@dataclass
class ReferenceData:
    """A normalized reanalysis series and its source metadata."""

    name: str
    frame: pd.DataFrame
    metadata: Dict[str, object]


def _numericize(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in result.columns:
        if column != "timestamp":
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def _ensure_utc_index(values: Iterable[object]) -> pd.DatetimeIndex:
    index = pd.to_datetime(values, errors="coerce", utc=True)
    if not isinstance(index, pd.DatetimeIndex):
        index = pd.DatetimeIndex(index)
    return index


def _localize_observation_timestamps(
    values: pd.Series | pd.DatetimeIndex,
    timezone: str,
) -> pd.Series | pd.DatetimeIndex:
    """Interpret naive logger timestamps in their declared zone, then use UTC.

    CERTE exports do not include a UTC offset in the five date components.  A
    direct ``utc=True`` parse therefore *labels* local logger time as UTC and
    silently misaligns it with ERA5/MERRA-2.  This helper deliberately keeps
    the distinction between localization and conversion.
    """

    parsed = pd.to_datetime(values, errors="coerce")
    try:
        if isinstance(parsed, pd.Series):
            if getattr(parsed.dt, "tz", None) is None:
                parsed = parsed.dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
            return parsed.dt.tz_convert("UTC")
        index = parsed if isinstance(parsed, pd.DatetimeIndex) else pd.DatetimeIndex(parsed)
        if index.tz is None:
            index = index.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
        return index.tz_convert("UTC")
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("Invalid or unsupported observation timezone: %s" % timezone) from error


def _read_generic_observation(
    source: Path,
    target_column: str,
    timezone: str,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Read a user-provided tabular observation into the CERTE-compatible schema."""
    frame = pd.read_csv(source, sep=None, engine="python", na_values=["NaN", "nan", "", "-999", "-9999"])
    frame.columns = [str(value).strip() for value in frame.columns]
    datetime_column = next(
        (column for column in frame.columns if str(column).lower() in {"timestamp", "datetime", "date/time", "date_time", "fecha"}),
        None,
    )
    if datetime_column is None:
        raise ValueError("User observation %s requires a timestamp/datetime column" % source.name)
    parsed = _localize_observation_timestamps(frame.pop(datetime_column), timezone)
    if target_column not in frame.columns:
        candidates = [
            column for column in frame.columns
            if str(column).lower() in {"wind_speed", "windspeed", "ws", "velocidad_viento", "velocidad"}
        ]
        if not candidates:
            raise ValueError("Target column %r was not found in %s" % (target_column, source.name))
        target_column = str(candidates[0])
    frame = _numericize(frame)
    frame.insert(0, "timestamp", parsed)
    frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    height_match = re.search(r"(?:_|\b)(\d+(?:\.\d+)?)m", target_column, re.I)
    metadata = {
        "format": "user_observation",
        "filename": source.name,
        "target_column": target_column,
        "measurement_height_m": float(height_match.group(1)) if height_match else None,
        "rows": int(len(frame)),
        "timezone_input": timezone,
    }
    return frame, metadata


def read_certe_file(
    path: str,
    target_column: str = "WS_80mA_mean",
    timezone: str = "UTC",
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Read one CERTE monthly export with its two metadata rows."""

    source = Path(path)
    with source.open("r", encoding="utf-8", errors="ignore") as stream:
        first_line = stream.readline()
    if not {"YYYY", "MM", "DD", "hh", "mm"}.issubset(set(first_line.split())):
        return _read_generic_observation(source, target_column, timezone)
    frame = pd.read_csv(
        source,
        sep=r"\s+",
        skiprows=[1, 2],
        na_values=["NaN", "nan", "", "-999", "-9999"],
        engine="python",
    )
    required = {"YYYY", "MM", "DD", "hh", "mm"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("CERTE file %s is missing columns: %s" % (source.name, sorted(missing)))

    timestamps = pd.to_datetime(
        {
            "year": pd.to_numeric(frame["YYYY"], errors="coerce"),
            "month": pd.to_numeric(frame["MM"], errors="coerce"),
            "day": pd.to_numeric(frame["DD"], errors="coerce"),
            "hour": pd.to_numeric(frame["hh"], errors="coerce"),
            "minute": pd.to_numeric(frame["mm"], errors="coerce"),
        },
        errors="coerce",
    )
    timestamps = _localize_observation_timestamps(timestamps, timezone)
    frame = _numericize(frame)
    frame.insert(0, "timestamp", timestamps)
    frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    if target_column not in frame.columns:
        raise ValueError("Target column %r was not found in %s" % (target_column, source.name))
    height_match = re.search(r"_(\d+(?:\.\d+)?)m", target_column, re.I)
    measurement_height = float(height_match.group(1)) if height_match else None
    metadata = {
        "format": "certe",
        "filename": source.name,
        "target_column": target_column,
        "measurement_height_m": measurement_height,
        "rows": int(len(frame)),
        "timezone_input": timezone,
        "timezone_output": "UTC",
    }
    return frame, metadata


def read_certe_files(
    paths: Sequence[str],
    target_column: str = "WS_80mA_mean",
    timezone: str = "UTC",
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Read and combine monthly CERTE files without silently filling gaps."""

    if not paths:
        raise ValueError("At least one CERTE observation file is required")
    frames: List[pd.DataFrame] = []
    files: List[Dict[str, object]] = []
    for path in paths:
        frame, metadata = read_certe_file(path, target_column=target_column, timezone=timezone)
        frames.append(frame)
        files.append(metadata)
    combined = pd.concat(frames, axis=0).sort_index()
    records_before_dedup = int(len(combined))
    duplicate_mask = combined.index.duplicated(keep="last")
    duplicates_removed = int(duplicate_mask.sum())
    if duplicates_removed:
        combined = combined.loc[~duplicate_mask]
    metadata = {
        "format": "certe",
        "files": files,
        "rows": int(len(combined)),
        "records_before_deduplication": records_before_dedup,
        "duplicates_removed": duplicates_removed,
        "target_column": target_column,
        "measurement_height_m": files[0].get("measurement_height_m"),
        "profile_heights_m": [20.0, 40.0, 60.0, 80.0],
        # Campaign years follow the station logger clock.  After conversion to
        # UTC the final six local hours of 31 December belong to 1 January UTC
        # and must not create a spurious extra campaign year.
        "years": sorted({int(value) for value in combined.index.tz_convert(timezone).year}),
        "timezone_input": timezone,
        "timezone_output": "UTC",
    }
    effective_target = str(files[0].get("target_column") or target_column)
    metadata["target_column"] = effective_target
    return normalize_observations(combined, effective_target), metadata


def discover_certe_files(root: str) -> List[str]:
    """Discover CERTE inputs without double-counting annual and consolidated files.

    Preference order is: largest consolidated file, annual files, then legacy
    monthly files. The caller can still pass an explicit list to
    :func:`read_certe_files` when a custom subset is required.
    """

    base = Path(root).expanduser()
    consolidated = [path for path in (base / "consolidados").glob("*CONSOLIDADO*.txt") if path.is_file()]
    if consolidated:
        selected = max(consolidated, key=lambda path: path.stat().st_size)
        return [str(selected)]
    annual = sorted(path for path in (base / "consolidados").glob("M04_CERTE_20*.txt") if path.is_file())
    if annual:
        return [str(path) for path in annual]
    monthly = sorted(path for path in base.glob("M04_*.txt") if path.is_file())
    if monthly:
        return [str(path) for path in monthly]
    raise ValueError("No CERTE files found under %s" % base)


def normalize_observations(frame: pd.DataFrame, target_column: str = "WS_80mA_mean") -> pd.DataFrame:
    """Map raw observation columns to the stable WindMCP schema."""

    result = frame.copy()
    if not isinstance(result.index, pd.DatetimeIndex):
        if "timestamp" not in result.columns:
            raise ValueError("Observations require a DatetimeIndex or timestamp column")
        result.index = _ensure_utc_index(result.pop("timestamp"))
    else:
        result.index = _ensure_utc_index(result.index)
    result.index.name = "timestamp"
    if target_column not in result.columns:
        raise ValueError("Target column %r was not found" % target_column)
    result = result.rename(columns={target_column: "wind_speed_obs"})
    rename = {
        "WS_80mB_mean": "wind_speed_obs_b",
        "WD_78m_mean": "wind_direction_obs",
        "temp_80m_mean": "temperature_obs",
        "P_15m_mean": "pressure_obs_hpa",
    }
    for old, new in rename.items():
        if old in result.columns:
            result = result.rename(columns={old: new})
    if "wind_direction_obs" not in result.columns:
        generic_direction = next(
            (column for column in result.columns
             if str(column).strip().lower() in {"wind_direction", "wind_dir", "direction", "wd", "wdir"}),
            None,
        )
        if generic_direction is not None:
            result = result.rename(columns={generic_direction: "wind_direction_obs"})
    result["wind_speed_obs"] = pd.to_numeric(result["wind_speed_obs"], errors="coerce")
    if "wind_direction_obs" in result.columns:
        result["wind_direction_obs"] = pd.to_numeric(result["wind_direction_obs"], errors="coerce") % 360.0
    return result.sort_index()


def _metadata_from_windnavigator(lines: Sequence[str], filename: str) -> Dict[str, object]:
    metadata: Dict[str, object] = {"format": "windnavigator", "filename": filename}
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.startswith("Latitude:"):
            try:
                metadata["latitude"] = float(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif stripped.startswith("Longitude:"):
            try:
                metadata["longitude"] = float(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
        if "MERRA-2" in stripped:
            metadata["reanalysis_type"] = "MERRA-2"
        elif "ERA5" in stripped and "reanalysis_type" not in metadata:
            metadata["reanalysis_type"] = "ERA5"
    return metadata


def read_windnavigator_file(path: str) -> ReferenceData:
    """Read a tab-separated Windnavigator export and derive its speed series."""

    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if line.strip().startswith("Date/time")), None)
    if header_idx is None:
        raise ValueError("Could not find the Windnavigator header in %s" % source.name)
    table_lines = lines[header_idx:]
    header_fields = [field.strip() for field in table_lines[0].split("\t")]
    first_data = next((line for line in table_lines[1:] if line.strip()), "")
    first_data_fields = first_data.split("\t") if first_data else []
    if len(first_data_fields) != len(header_fields):
        # A few Windnavigator exports contain one or more unlabeled trailing
        # fields. Keep them instead of letting pandas reinterpret the first
        # data field as an index, which would corrupt timestamps and speeds.
        width = max(len(header_fields), len(first_data_fields))
        columns = header_fields + ["extra_%d" % i for i in range(1, width - len(header_fields) + 1)]
        raw = pd.read_csv(
            io.StringIO("\n".join(table_lines[1:])),
            sep="\t",
            header=None,
            names=columns,
            engine="python",
        )
    else:
        raw = pd.read_csv(io.StringIO("\n".join(table_lines)), sep="\t", engine="python")
    raw.columns = [str(column).strip() for column in raw.columns]
    metadata = _metadata_from_windnavigator(lines, source.name)

    datetime_column = next((c for c in raw.columns if c.lower().startswith("date/time")), None)
    if datetime_column is None:
        raise ValueError("Windnavigator file %s has no datetime column" % source.name)
    timestamps = _ensure_utc_index(raw.pop(datetime_column))

    speed_column = next((c for c in raw.columns if re.search(r"speed_\d+m", c, re.I)), None)
    direction_column = next((c for c in raw.columns if re.search(r"direction_\d+m", c, re.I)), None)
    if speed_column is None:
        raise ValueError("Windnavigator file %s has no Speed_* column" % source.name)
    height_match = re.search(r"_(\d+)m", speed_column, re.I)
    height_m = float(height_match.group(1)) if height_match else None
    metadata["wind_height_m"] = height_m
    metadata["source"] = metadata.get("reanalysis_type", "Windnavigator")

    result = pd.DataFrame(index=timestamps)
    result.index.name = "timestamp"
    result["wind_speed_ref"] = pd.to_numeric(raw[speed_column], errors="coerce").to_numpy()
    if direction_column is not None:
        result["wind_direction_ref"] = (pd.to_numeric(raw[direction_column], errors="coerce") % 360.0).to_numpy()
    temp_column = next((c for c in raw.columns if "temperature" in c.lower()), None)
    pressure_column = next((c for c in raw.columns if "pressure" in c.lower()), None)
    if temp_column is not None:
        result["temperature_ref"] = pd.to_numeric(raw[temp_column], errors="coerce").to_numpy()
    if pressure_column is not None:
        result["pressure_ref_kpa"] = pd.to_numeric(raw[pressure_column], errors="coerce").to_numpy()
    result = result[~result.index.isna()].sort_index()
    metadata["rows"] = int(len(result))
    return ReferenceData(name=source.name, frame=result, metadata=metadata)


def read_reference_files(paths: Sequence[str]) -> List[ReferenceData]:
    if not paths:
        raise ValueError("At least one reanalysis file is required")
    return [read_windnavigator_file(path) for path in paths]


def circular_mean(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) == 0:
        return np.nan
    radians = np.deg2rad(values)
    return float(np.rad2deg(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())) % 360.0)


def resample_hourly(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate numeric fields hourly while treating direction circularly."""

    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Hourly resampling requires a DatetimeIndex")
    result = frame.sort_index().resample("1h").mean(numeric_only=True)
    for direction in [c for c in frame.columns if "direction" in c]:
        # Vectorized circular mean. Resample(...).apply(circular_mean) is
        # scientifically equivalent but becomes prohibitively slow for the
        # multi-decade Windnavigator files.
        direction_values = pd.to_numeric(frame[direction], errors="coerce")
        radians = np.deg2rad(direction_values.to_numpy(dtype=float))
        sin_hourly = pd.Series(np.sin(radians), index=frame.index).resample("1h").mean()
        cos_hourly = pd.Series(np.cos(radians), index=frame.index).resample("1h").mean()
        direction_hourly = pd.Series(
            np.rad2deg(np.arctan2(sin_hourly.to_numpy(), cos_hourly.to_numpy())) % 360.0,
            index=sin_hourly.index,
        )
        direction_hourly[(sin_hourly.isna()) | (cos_hourly.isna())] = np.nan
        result[direction] = direction_hourly
    result.index.name = "timestamp"
    return result


def align_observations_and_reference(
    observations: pd.DataFrame,
    reference: ReferenceData,
    valid_observation_index: Optional[pd.Index] = None,
) -> pd.DataFrame:
    """Return concurrent hourly observations and one reference series."""

    obs = observations.copy()
    if valid_observation_index is not None:
        obs = obs.loc[obs.index.intersection(valid_observation_index)]
    obs_hourly = resample_hourly(obs)
    # WindMCP validates the campaign years only. Restricting the reference to
    # the observation window avoids repeatedly resampling 40+ years when the
    # user selects the supplied multi-decade files. The full long-term
    # reconstruction belongs to the later version defined in the plan.
    ref_frame = reference.frame
    if not obs.empty and not ref_frame.empty:
        start = obs.index.min().floor("h")
        end = obs.index.max().ceil("h")
        ref_frame = ref_frame.loc[(ref_frame.index >= start) & (ref_frame.index <= end)]
    ref_hourly = resample_hourly(ref_frame)
    joined = obs_hourly.join(ref_hourly, how="inner", lsuffix="_obs_extra", rsuffix="_ref_extra")
    if "wind_speed_obs" not in joined.columns or "wind_speed_ref" not in joined.columns:
        raise ValueError("Aligned data requires wind_speed_obs and wind_speed_ref")
    return joined.sort_index()

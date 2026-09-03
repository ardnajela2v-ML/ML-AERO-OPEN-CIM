"""Validated configuration and public version for WindMCP.

The service accepts JSON-like mappings from the CLI, Streamlit and Flask.  A
single validated configuration object keeps those entry points consistent and
prevents invalid physical or temporal settings from reaching the scientific
pipeline.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Tuple


VERSION = "1.0.0"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class ConfigurationError(ValueError):
    """Raised when a run request is invalid or incomplete."""


def _string_list(value: Any, field: str) -> Tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, Path)):
        values: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        raise ConfigurationError(f"{field} must be a list of file paths")
    result = tuple(str(item).strip() for item in values if str(item).strip())
    return result


def _number(value: Any, field: str, *, minimum: float, maximum: float, integer: bool = False) -> float | int:
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ConfigurationError(f"{field} must be a number") from None
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{field} must be between {minimum:g} and {maximum:g}")
    return parsed


@dataclass(frozen=True)
class MCPConfig:
    """Canonical, validated options for one reproducible MCP run."""

    observation_files: Tuple[str, ...]
    reference_files: Tuple[str, ...]
    target_column: str = "WS_80mA_mean"
    observation_height_m: Optional[float] = None
    observation_timezone: str = "UTC"
    target_height_m: float = 80.0
    train_year: int = 2018
    test_year: int = 2019
    ranking_year: int = 2018
    sector_count: int = 16
    resolution: str = "1h"
    timezone: str = "UTC"
    power_curve_file: Optional[str] = None
    turbine_count: int = 1
    losses_pct: float = 0.0
    max_wind_speed: float = 70.0
    max_jump: float = 15.0
    max_persistence: int = 6
    selected_reference: Optional[str] = None
    run_id: Optional[str] = None
    output_dir: Optional[str] = None

    @classmethod
    def from_mapping(cls, request: Mapping[str, Any]) -> "MCPConfig":
        if not isinstance(request, Mapping):
            raise ConfigurationError("request must be a JSON object")
        observations = _string_list(request.get("observation_files"), "observation_files")
        references = _string_list(request.get("reference_files"), "reference_files")
        if not observations:
            raise ConfigurationError("observation_files must contain at least one file")
        if not references:
            raise ConfigurationError("reference_files must contain at least one file")

        target_column = str(request.get("target_column", "WS_80mA_mean")).strip()
        if not target_column:
            raise ConfigurationError("target_column cannot be empty")
        resolution = str(request.get("resolution", "1h")).strip().lower()
        timezone = str(request.get("timezone", "UTC")).strip().upper()
        if resolution != "1h":
            raise ConfigurationError("Only hourly resolution ('1h') is supported")
        if timezone != "UTC":
            raise ConfigurationError("Only UTC timezone is supported for reproducible runs")

        train_year = int(_number(request.get("train_year", 2018), "train_year", minimum=1900, maximum=2200, integer=True))
        test_year = int(_number(request.get("test_year", 2019), "test_year", minimum=1900, maximum=2200, integer=True))
        if train_year >= test_year:
            raise ConfigurationError("train_year must be earlier than test_year")
        ranking_year = int(_number(request.get("ranking_year", train_year), "ranking_year", minimum=1900, maximum=2200, integer=True))
        sector_count = int(_number(request.get("sector_count", 16), "sector_count", minimum=12, maximum=16, integer=True))
        if sector_count not in {12, 16}:
            raise ConfigurationError("sector_count must be 12 or 16")

        run_id_value = request.get("run_id")
        run_id = None if run_id_value in (None, "") else str(run_id_value).strip()
        if run_id is not None and not RUN_ID_PATTERN.fullmatch(run_id):
            raise ConfigurationError("run_id contains invalid characters or is too long")

        power_curve = request.get("power_curve_file")
        power_curve_file = None if power_curve in (None, "") else str(power_curve).strip()
        output_dir = request.get("output_dir")
        output_dir_value = None if output_dir in (None, "") else str(output_dir).strip()
        selected = request.get("selected_reference")
        selected_reference = None if selected in (None, "") else str(selected)
        observation_height_raw = request.get("observation_height_m")
        observation_height_m = None if observation_height_raw in (None, "") else float(
            _number(observation_height_raw, "observation_height_m", minimum=1, maximum=300)
        )

        return cls(
            observation_files=observations,
            reference_files=references,
            target_column=target_column,
            observation_height_m=observation_height_m,
            observation_timezone=str(request.get("observation_timezone", "UTC")).strip() or "UTC",
            target_height_m=float(_number(request.get("target_height_m", 80), "target_height_m", minimum=1, maximum=300)),
            train_year=train_year,
            test_year=test_year,
            ranking_year=ranking_year,
            sector_count=sector_count,
            resolution=resolution,
            timezone=timezone,
            power_curve_file=power_curve_file,
            turbine_count=int(_number(request.get("turbine_count", 1), "turbine_count", minimum=1, maximum=100000, integer=True)),
            losses_pct=float(_number(request.get("losses_pct", 0), "losses_pct", minimum=0, maximum=99.999999)),
            max_wind_speed=float(_number(request.get("max_wind_speed", 70), "max_wind_speed", minimum=1, maximum=150)),
            max_jump=float(_number(request.get("max_jump", 15), "max_jump", minimum=0.01, maximum=150)),
            max_persistence=int(_number(request.get("max_persistence", 6), "max_persistence", minimum=2, maximum=10000, integer=True)),
            selected_reference=selected_reference,
            run_id=run_id,
            output_dir=output_dir_value,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_files": list(self.observation_files),
            "reference_files": list(self.reference_files),
            "target_column": self.target_column,
            "observation_height_m": self.observation_height_m,
            "observation_timezone": self.observation_timezone,
            "target_height_m": self.target_height_m,
            "train_year": self.train_year,
            "test_year": self.test_year,
            "ranking_year": self.ranking_year,
            "sector_count": self.sector_count,
            "resolution": self.resolution,
            "timezone": self.timezone,
            "power_curve_file": self.power_curve_file,
            "turbine_count": self.turbine_count,
            "losses_pct": self.losses_pct,
            "max_wind_speed": self.max_wind_speed,
            "max_jump": self.max_jump,
            "max_persistence": self.max_persistence,
            "selected_reference": self.selected_reference,
            "run_id": self.run_id,
            "output_dir": self.output_dir,
        }


def validate_input_files(config: MCPConfig) -> None:
    """Ensure every declared input is a readable regular file."""

    paths = list(config.observation_files) + list(config.reference_files)
    if config.power_curve_file:
        paths.append(config.power_curve_file)
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.is_file():
            raise ConfigurationError(f"Input file not found: {raw_path}")
        if not os.access(path, os.R_OK):
            raise ConfigurationError(f"Input file is not readable: {raw_path}")

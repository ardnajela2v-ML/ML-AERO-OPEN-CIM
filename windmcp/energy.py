"""Power-curve interpolation and period-energy comparison for WindMCP."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple

import numpy as np
import pandas as pd


@dataclass
class PowerCurve:
    """A physical turbine power curve expressed as wind speed and kW."""

    name: str
    wind_speed_ms: np.ndarray
    power_kw_values: np.ndarray

    @classmethod
    def from_file(cls, path: str) -> "PowerCurve":
        source = Path(path)
        raw = pd.read_csv(source, sep=None, engine="python", header=None, comment="#")
        if raw.shape[1] < 2:
            raise ValueError("Power curve requires at least two columns: wind speed and power")
        values = raw.iloc[:, :2].apply(pd.to_numeric, errors="coerce").dropna()
        if len(values) < 2:
            raise ValueError("Power curve requires at least two numeric points")
        values.columns = ["wind_speed_ms", "power_kw"]
        values = values[(values["wind_speed_ms"] >= 0) & (values["power_kw"] >= 0)]
        values = values.sort_values("wind_speed_ms").drop_duplicates("wind_speed_ms", keep="last")
        if len(values) < 2:
            raise ValueError("Power curve has insufficient valid points")
        return cls(
            name=source.name,
            wind_speed_ms=values["wind_speed_ms"].to_numpy(dtype=float),
            power_kw_values=values["power_kw"].to_numpy(dtype=float),
        )

    @property
    def rated_power_kw(self) -> float:
        return float(np.max(self.power_kw_values))

    @property
    def cut_in_ms(self) -> float:
        positive = self.wind_speed_ms[self.power_kw_values > 0]
        return float(positive.min()) if len(positive) else float(self.wind_speed_ms.min())

    @property
    def maximum_curve_speed_ms(self) -> float:
        return float(self.wind_speed_ms.max())

    def power_kw(self, wind_speed_ms: Iterable[float]) -> np.ndarray:
        speed = np.asarray(wind_speed_ms, dtype=float)
        power = np.interp(speed, self.wind_speed_ms, self.power_kw_values, left=0.0, right=0.0)
        power[~np.isfinite(speed)] = np.nan
        return np.clip(power, 0.0, self.rated_power_kw)

    def metadata(self) -> Dict[str, float]:
        return {
            "name": self.name,
            "rated_power_kw": self.rated_power_kw,
            "cut_in_ms": self.cut_in_ms,
            "maximum_curve_speed_ms": self.maximum_curve_speed_ms,
            "points": int(len(self.wind_speed_ms)),
        }


def compare_period_energy(
    predictions: pd.DataFrame,
    curve: PowerCurve,
    turbine_count: int = 1,
    losses_pct: float = 0.0,
) -> Tuple[Dict[str, object], pd.DataFrame]:
    """Convert concurrent hourly MCP validation data into energy comparison.

    Energy is summed only over valid concurrent periods. It is intentionally
    not extrapolated to a full year, so missing campaign months stay visible.
    """

    if turbine_count < 1:
        raise ValueError("turbine_count must be at least 1")
    if not 0 <= losses_pct < 100:
        raise ValueError("losses_pct must be between 0 (inclusive) and 100 (exclusive)")
    required = {"timestamp", "observed_wind_speed", "predicted_wind_speed"}
    missing = required.difference(predictions.columns)
    if missing:
        raise ValueError("Predictions lack required columns: %s" % sorted(missing))
    result = predictions.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True, errors="coerce")
    result = result.dropna(subset=["timestamp", "observed_wind_speed", "predicted_wind_speed"]).sort_values("timestamp")
    if result.empty:
        raise ValueError("No concurrent prediction rows are available for energy")

    loss_factor = 1.0 - losses_pct / 100.0
    result["observed_power_kw_gross"] = curve.power_kw(result["observed_wind_speed"].to_numpy()) * turbine_count
    result["predicted_power_kw_gross"] = curve.power_kw(result["predicted_wind_speed"].to_numpy()) * turbine_count
    result["observed_power_kw"] = result["observed_power_kw_gross"] * loss_factor
    result["predicted_power_kw"] = result["predicted_power_kw_gross"] * loss_factor
    # The MCP workflow is intentionally hourly in v1.0.
    result["interval_hours"] = 1.0
    result["observed_energy_kwh"] = result["observed_power_kw"] * result["interval_hours"]
    result["predicted_energy_kwh"] = result["predicted_power_kw"] * result["interval_hours"]

    observed_mwh = float(result["observed_energy_kwh"].sum() / 1000.0)
    predicted_mwh = float(result["predicted_energy_kwh"].sum() / 1000.0)
    difference_mwh = predicted_mwh - observed_mwh
    delta_pct = None if observed_mwh == 0 else float(difference_mwh / observed_mwh * 100.0)
    summary: Dict[str, object] = {
        "curve": curve.metadata(),
        "turbine_count": int(turbine_count),
        "losses_pct": float(losses_pct),
        "concurrent_hours": int(len(result)),
        "period_start": result["timestamp"].min().isoformat(),
        "period_end": result["timestamp"].max().isoformat(),
        "observed_period_energy_mwh": observed_mwh,
        "predicted_period_energy_mwh": predicted_mwh,
        "difference_mwh": difference_mwh,
        "delta_energy_pct": delta_pct,
        "note": "Energy is the sum of valid concurrent hourly records; it is not annualized over missing periods.",
    }
    return summary, result

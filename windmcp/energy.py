"""Power-curve interpolation and period-energy comparison for WindMCP."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Tuple

import numpy as np
import pandas as pd

from core.energy import PowerCurve as CorePowerCurve
from core.energy import air_density


REFERENCE_DENSITY_KG_M3 = 1.225


@dataclass
class PowerCurve:
    """WindMCP-compatible adapter over the platform's physical power curve."""

    name: str
    wind_speed_ms: np.ndarray
    power_kw_values: np.ndarray
    _core: CorePowerCurve = field(init=False, repr=False)

    def __post_init__(self) -> None:
        values = pd.DataFrame({
            "wind_speed_ms": np.asarray(self.wind_speed_ms, dtype=float),
            "power_kw": np.asarray(self.power_kw_values, dtype=float),
        }).replace([np.inf, -np.inf], np.nan).dropna()
        values = values[(values["wind_speed_ms"] >= 0) & (values["power_kw"] >= 0)]
        values = values.sort_values("wind_speed_ms").drop_duplicates("wind_speed_ms", keep="last")
        if len(values) < 2:
            raise ValueError("Power curve requires at least two valid numeric points")
        self.wind_speed_ms = values["wind_speed_ms"].to_numpy(dtype=float)
        self.power_kw_values = values["power_kw"].to_numpy(dtype=float)
        rated_power = float(np.max(self.power_kw_values))
        if rated_power <= 0:
            raise ValueError("Power curve requires positive power values")
        positive = self.power_kw_values > 0
        cut_in = float(self.wind_speed_ms[np.flatnonzero(self.power_kw_values > 1)[0]]) \
            if np.any(self.power_kw_values > 1) else float(self.wind_speed_ms[np.flatnonzero(positive)[0]])
        rated_candidates = np.flatnonzero(self.power_kw_values >= rated_power * 0.98)
        rated_speed = float(self.wind_speed_ms[rated_candidates[0]])
        cut_out = float(np.max(self.wind_speed_ms[positive]))
        self._core = CorePowerCurve(
            name=self.name,
            rated_power_kw=rated_power,
            cut_in=cut_in,
            rated_speed=rated_speed,
            cut_out=cut_out,
            curve_points=list(zip(self.wind_speed_ms.tolist(), self.power_kw_values.tolist())),
        )

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
        return float(self._core.rated_power_kw)

    @property
    def cut_in_ms(self) -> float:
        return float(self._core.cut_in)

    @property
    def rated_speed_ms(self) -> float:
        return float(self._core.rated_speed)

    @property
    def cut_out_ms(self) -> float:
        return float(self._core.cut_out)

    @property
    def maximum_curve_speed_ms(self) -> float:
        return float(self.wind_speed_ms.max())

    def power_kw(self, wind_speed_ms: Iterable[float]) -> np.ndarray:
        speed = np.asarray(wind_speed_ms, dtype=float)
        power = np.asarray(self._core.power_kw(speed), dtype=float)
        return np.where(np.isfinite(speed), power, np.nan)

    def power_kw_density_corrected(
        self,
        wind_speed_ms: Iterable[float],
        density_kg_m3: Iterable[float] | float,
    ) -> np.ndarray:
        """Use the same density correction as the platform energy engine."""

        speed = np.asarray(wind_speed_ms, dtype=float)
        power = np.asarray(
            self._core.power_kw_density_corrected(
                speed, np.asarray(density_kg_m3, dtype=float), REFERENCE_DENSITY_KG_M3
            ),
            dtype=float,
        )
        return np.where(np.isfinite(speed), power, np.nan)

    def metadata(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "rated_power_kw": self.rated_power_kw,
            "cut_in_ms": self.cut_in_ms,
            "rated_speed_ms": self.rated_speed_ms,
            "cut_out_ms": self.cut_out_ms,
            "maximum_curve_speed_ms": self.maximum_curve_speed_ms,
            "points": int(len(self.wind_speed_ms)),
            "density_correction": "P_curve_x_rho_over_rho_ref_capped_at_rated_power",
            "reference_density_kg_m3": REFERENCE_DENSITY_KG_M3,
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

    temperature = pd.to_numeric(result.get("temperature_ref"), errors="coerce") \
        if "temperature_ref" in result else pd.Series(np.nan, index=result.index)
    pressure_kpa = pd.to_numeric(result.get("pressure_ref_kpa"), errors="coerce") \
        if "pressure_ref_kpa" in result else pd.Series(np.nan, index=result.index)
    candidate_density = np.asarray(
        air_density(temperature.to_numpy(), pressure_kpa.to_numpy() * 1000.0), dtype=float
    )
    real_density = (
        temperature.notna().to_numpy()
        & pressure_kpa.notna().to_numpy()
        & np.isfinite(candidate_density)
        & (candidate_density > 0)
    )
    density = np.where(real_density, candidate_density, REFERENCE_DENSITY_KG_M3)
    result["air_density_kg_m3"] = density
    result["density_source"] = np.where(real_density, "reanalysis_temperature_pressure", "standard_fallback")

    loss_factor = 1.0 - losses_pct / 100.0
    observed_speed = result["observed_wind_speed"].to_numpy(dtype=float)
    predicted_speed = result["predicted_wind_speed"].to_numpy(dtype=float)
    result["observed_power_kw_gross"] = curve.power_kw(observed_speed) * turbine_count
    result["predicted_power_kw_gross"] = curve.power_kw(predicted_speed) * turbine_count
    result["observed_power_kw_gross_density_corrected"] = (
        curve.power_kw_density_corrected(observed_speed, density) * turbine_count
    )
    result["predicted_power_kw_gross_density_corrected"] = (
        curve.power_kw_density_corrected(predicted_speed, density) * turbine_count
    )
    result["observed_power_kw"] = result["observed_power_kw_gross_density_corrected"] * loss_factor
    result["predicted_power_kw"] = result["predicted_power_kw_gross_density_corrected"] * loss_factor
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
        "density_correction": {
            "policy": "reanalysis_temperature_pressure_else_standard_density_per_hour",
            "reference_density_kg_m3": REFERENCE_DENSITY_KG_M3,
            "mean_air_density_kg_m3": float(np.mean(density)),
            "hours_reanalysis_temperature_pressure": int(real_density.sum()),
            "hours_standard_fallback": int((~real_density).sum()),
        },
        "concurrent_hours": int(len(result)),
        "period_start": result["timestamp"].min().isoformat(),
        "period_end": result["timestamp"].max().isoformat(),
        "observed_period_energy_mwh": observed_mwh,
        "predicted_period_energy_mwh": predicted_mwh,
        "difference_mwh": difference_mwh,
        "delta_energy_pct": delta_pct,
        "note": (
            "Energy is summed over valid concurrent hourly records and is not annualized over missing periods. "
            "Power uses hourly reanalysis temperature/pressure when available and 1.225 kg/m³ otherwise."
        ),
    }
    return summary, result

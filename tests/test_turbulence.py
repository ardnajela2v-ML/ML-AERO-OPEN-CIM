from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from core.wind_stats import compute_wind_speed_variability
from windmcp.turbulence import (
    build_turbulence_products, measured_turbulence_records,
    measured_turbulence_summary,
)


class TurbulenceTests(unittest.TestCase):
    @staticmethod
    def _observations(validation_ti: float = 0.20) -> pd.DataFrame:
        train = pd.date_range("2018-01-01", periods=720, freq="10min", tz="UTC")
        april = pd.date_range("2019-04-01", periods=180, freq="10min", tz="UTC")
        june = pd.date_range("2019-06-01", periods=180, freq="10min", tz="UTC")
        index = train.append(april).append(june)
        phase = np.arange(len(index), dtype=float)
        speed = 9.0 + 4.0 * np.sin(phase / 31.0)
        train_sigma = speed[:len(train)] * (0.14 + 0.01 * np.cos(phase[:len(train)] / 17.0))
        validation_sigma = speed[len(train):] * float(validation_ti)
        sigma = np.r_[train_sigma, validation_sigma]
        return pd.DataFrame({
            "wind_speed_obs": speed,
            "WS_80mA_stdev": sigma,
            "wind_speed_obs_b": speed + 0.05,
            "WS_80mB_stdev": sigma + 0.01,
            "wind_direction_obs": (phase * 11.0) % 360.0,
        }, index=index)

    @staticmethod
    def _historical() -> pd.DataFrame:
        index = pd.date_range("2000-01-01", periods=24 * 365, freq="1h", tz="UTC")
        phase = np.arange(len(index), dtype=float)
        return pd.DataFrame({
            "timestamp": index,
            "long_term_wind_speed": 9.0 + 4.0 * np.sin(phase / 97.0),
            "wind_direction": (phase * 7.0) % 360.0,
        })

    def test_measured_ti_uses_same_window_logger_mean_and_sigma(self):
        observations = self._observations()
        summary, training = measured_turbulence_summary(
            observations, training_year=2018, timezone="UTC", measurement_height_m=80,
        )
        self.assertTrue(summary["available"])
        self.assertEqual(summary["native_resolution_minutes"], 10.0)
        self.assertEqual(summary["measurement_method"], "logger_mean_and_standard_deviation_same_10min_window")
        self.assertAlmostEqual(summary["ti_overall"], float(training["ti"].mean()), places=5)
        self.assertFalse(summary["iec_is_proxy"])
        self.assertIsNone(summary["iec_class"])
        years = {row["year"]: row for row in summary["campaign_years"]}
        self.assertTrue(years[2018]["is_training_year"])
        self.assertIn(5, years[2019]["months_without_measurements"])
        exported = measured_turbulence_records(observations, timezone="UTC")
        self.assertEqual(set(exported["logger_year"]), {2018, 2019})
        self.assertEqual(set(exported["data_source"]), {"CERTE_measured_10min"})

    def test_historical_ti_is_trained_only_on_2018_and_may_is_not_imputed_as_validation(self):
        first, products_first = build_turbulence_products(
            self._observations(validation_ti=0.20), self._historical(),
            training_year=2018, validation_year=2019, timezone="UTC",
            measurement_height_m=80, target_height_m=80,
        )
        second, products_second = build_turbulence_products(
            self._observations(validation_ti=0.45), self._historical(),
            training_year=2018, validation_year=2019, timezone="UTC",
            measurement_height_m=80, target_height_m=80,
        )
        model = products_first["historical_model"]
        self.assertTrue(model["available"])
        self.assertEqual(model["training_year"], 2018)
        self.assertEqual(model["training_policy"], "fixed_single_year_no_validation_leakage")
        self.assertIn(5, model["validation"]["months_without_measurements"])
        self.assertEqual(model["validation"]["months_with_measurements"], [4, 6])
        np.testing.assert_allclose(
            first["turbulence_intensity_p50"].to_numpy(),
            second["turbulence_intensity_p50"].to_numpy(),
            equal_nan=True,
        )
        self.assertEqual(
            products_first["historical_model"]["training_records"],
            products_second["historical_model"]["training_records"],
        )
        self.assertIn("sigma_u_p90", first.columns)
        self.assertTrue((first["turbulence_data_source"] == "modelled_from_CERTE_2018").any())

    def test_hourly_variability_never_assigns_an_iec_class(self):
        index = pd.date_range("2000-01-01", periods=200, freq="1h", tz="UTC")
        frame = pd.DataFrame({"wind_speed": 8.0 + np.sin(np.arange(200) / 6.0)}, index=index)
        result = compute_wind_speed_variability(frame)
        self.assertTrue(result["iec_is_proxy"])
        self.assertIsNone(result["iec_class"])
        self.assertIn("NO equivale a TI IEC", result["note"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from core.kriging import kriging_interp
from core.terrain import sample_grid_worldcover_z0
from core.spatial_calibration import (
    PROJECT_REFERENCE_HEIGHT_M,
    PROJECT_REFERENCE_ROUGHNESS_M,
    build_calibrated_maps,
    calibrate_spatial_nodes,
    circular_difference,
    generalize_speed,
    meteorological_to_uv,
    monthly_balanced_metrics,
    reapply_speed,
    sector_indices,
    select_variogram_loo,
    terrain_screening,
    uv_to_meteorological,
)


class SpatialCalibrationTests(unittest.TestCase):
    def test_worldcover_roughness_fallback_never_uses_dem_slope(self):
        with patch("core.terrain._prefetch_tiles"), patch(
            "core.terrain._fetch_landcover_tile", return_value=None
        ):
            z0 = sample_grid_worldcover_z0(
                [16.5, 16.6], [-95.0, -94.9], fallback_z0=0.03
            )
        np.testing.assert_allclose(z0, [[0.03, 0.03], [0.03, 0.03]])

    def test_direction_is_circular_true_north_and_uses_meteorological_from(self):
        delta = circular_difference([359.0, 0.0, 1.0], [0.0, 359.0, 359.0])
        np.testing.assert_allclose(delta, [-1.0, 1.0, 2.0])
        u, v = meteorological_to_uv([10.0, 10.0, 10.0, 10.0], [0.0, 90.0, 180.0, 270.0])
        np.testing.assert_allclose(u, [0.0, -10.0, 0.0, 10.0], atol=1e-10)
        np.testing.assert_allclose(v, [-10.0, 0.0, 10.0, 0.0], atol=1e-10)
        np.testing.assert_allclose(uv_to_meteorological(u, v), [0.0, 90.0, 180.0, 270.0], atol=1e-10)
        self.assertEqual(sector_indices([359.0, 0.0, 1.0, 22.4, 22.6]).tolist(), [0, 0, 0, 1, 1])

    def test_log_profile_round_trip_reproduces_original_speed(self):
        speed = np.asarray([2.0, 5.0, 9.0, 14.0])
        z0_by_sample = np.asarray([0.005, 0.03, 0.1, 0.8])
        generalized = generalize_speed(
            speed, 80.0, z0_by_sample,
            PROJECT_REFERENCE_HEIGHT_M, PROJECT_REFERENCE_ROUGHNESS_M,
        )
        restored = reapply_speed(
            generalized, 80.0, z0_by_sample,
            PROJECT_REFERENCE_HEIGHT_M, PROJECT_REFERENCE_ROUGHNESS_M,
        )
        np.testing.assert_allclose(restored, speed, rtol=1e-12, atol=1e-12)

    def test_monthly_balancing_omits_missing_may_instead_of_zero_filling(self):
        index = pd.date_range("2019-01-01", "2019-12-31 23:00", freq="h", tz="UTC")
        observed = pd.Series(8.0, index=index)
        observed = observed.loc[observed.index.month != 5]
        estimated = pd.Series(9.0, index=index)
        metrics = monthly_balanced_metrics(observed, estimated)
        self.assertEqual(metrics["months_used"], [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12])
        self.assertEqual(metrics["missing_months"], [5])
        self.assertAlmostEqual(metrics["rmse_monthly_balanced"], 1.0)
        self.assertAlmostEqual(metrics["bias_monthly_balanced"], 1.0)

    def _synthetic_case(self):
        index = pd.date_range("2018-01-01", "2020-12-31 23:00", freq="h", tz="UTC")
        hour = np.arange(len(index), dtype=float)
        seasonal = 8.0 + 2.1 * np.sin(2 * np.pi * hour / (24 * 365.25)) + 0.7 * np.sin(2 * np.pi * hour / 24)
        anchor = pd.DataFrame({
            "wind_speed": np.maximum(0.2, seasonal),
            "wind_speed_p05": np.maximum(0.0, seasonal - 0.7),
            "wind_speed_p95": seasonal + 0.7,
            "wind_direction": (350.0 + 25.0 * np.sin(2 * np.pi * hour / (24 * 20))) % 360.0,
        }, index=index)
        observed = anchor[["wind_speed"]].copy()
        observed = observed.loc[~((observed.index.year == 2019) & (observed.index.month == 5))]
        stations = []
        frames = {}
        coordinates = {
            "ERA5": [(16.25, -95.25), (16.50, -94.50), (16.85, -94.75)],
            "MERRA2": [(16.00, -95.00), (16.50, -95.00), (17.00, -95.00)],
        }
        tempdir = tempfile.TemporaryDirectory()
        for provider, coords in coordinates.items():
            provider_bias = 0.4 if provider == "ERA5" else -0.25
            for idx, (lat, lon) in enumerate(coords):
                station_id = f"{provider.lower()}-{idx}"
                path = Path(tempdir.name) / f"{station_id}.txt"
                path.write_text(station_id, encoding="utf-8")
                spatial = (idx - 1) * 0.08
                speed = np.maximum(0.1, seasonal + provider_bias + spatial * seasonal)
                direction = (anchor["wind_direction"].to_numpy() + (idx - 1) * 8.0 + (3.0 if provider == "ERA5" else -4.0)) % 360.0
                frames[station_id] = pd.DataFrame({
                    "wind_speed_ref": speed,
                    "wind_direction_ref": direction,
                    "temperature_ref": np.full(len(index), 25.0),
                    "pressure_ref_kpa": np.full(len(index), 100.0),
                }, index=index)
                stations.append({
                    "station_id": station_id, "display_name": station_id,
                    "data_kind": "reanalysis", "provider": provider,
                    "latitude": lat, "longitude": lon,
                    "measurement_heights": [100 if provider == "ERA5" else 50],
                    "file_ids": [path.name], "_paths": [str(path)], "size_bytes": 100 + idx,
                })
        return tempdir, anchor, observed, stations, frames

    def test_anchored_calibration_preserves_gradients_weights_and_anchor(self):
        temporary, anchor_frame, observed_frame, stations, frames = self._synthetic_case()
        self.addCleanup(temporary.cleanup)

        def fake_load(station):
            return dict(station), frames[station["station_id"]].copy()

        roughness = lambda _lat, _lon: {
            "z0_by_sector_m": [0.1] * 16,
            "source": "test_worldcover", "fetch_m": 750.0, "fallback_sectors": 0,
        }
        with patch("core.spatial_calibration._load_station", side_effect=fake_load):
            result = calibrate_spatial_nodes(
                anchor_frame, observed_frame, stations,
                poi={"lat": 16.55, "lon": -94.955},
                target_height_m=80.0, min_years=3,
                roughness_resolver=roughness,
            )
        self.assertEqual(result["common_period"]["n_years"], 3)
        self.assertEqual(set(result["participating_providers"]), {"ERA5", "MERRA2"})
        self.assertAlmostEqual(sum(result["provider_weights"].values()), 1.0, places=12)
        for provider in ("ERA5", "MERRA2"):
            self.assertEqual(result["provider_validation"][provider]["missing_months"], [5])
        calibrated_means = [round(node["calibrated_mean_speed"], 5) for node in result["nodes"]]
        self.assertGreater(len(set(calibrated_means)), 2, "calibrated nodes must retain spatial gradients")
        self.assertTrue(all(node["source_height_m"] in {50.0, 100.0} for node in result["nodes"]))
        self.assertFalse(result["project_reference"]["orography_removed"])

        # Ordinary Kriging at the exact M04 coordinate reproduces the MCP anchor.
        provider = "ERA5"
        rows = [result["anchor"]] + [node for node in result["nodes"] if node["provider"] == provider]
        lons = [row["longitude"] for row in rows]
        lats = [row["latitude"] for row in rows]
        values = [row["generalized_mean_speed"] for row in rows]
        predicted, _ = kriging_interp(
            lons, lats, values,
            np.asarray([[-94.955]]), np.asarray([[16.55]]), model="exponential", rng=50.0,
        )
        self.assertAlmostEqual(predicted[0, 0], result["anchor"]["generalized_mean_speed"], places=4)

        with tempfile.TemporaryDirectory() as turbine_folder:
            maps = build_calibrated_maps(
                result, turbines_folder=turbine_folder,
                n_grid=12, model="auto", target_height_m=80.0,
                physical=False,
            )
        self.assertIn("wind_era5", maps)
        self.assertIn("wind_merra2", maps)
        self.assertIn("wind_direction", maps)
        self.assertIn("uncertainty", maps)
        self.assertEqual(len(maps["sectorial"]["frequency_pct"]), 16)
        self.assertEqual(maps["provenance"]["direction"], "meteorological_from_true_north_clockwise")
        self.assertFalse(maps["terrain"]["orography_speed_correction_applied"])

    def test_variogram_loo_and_dem_screening_are_diagnostic(self):
        lons = [-95.2, -95.0, -94.8, -94.6]
        lats = [16.3, 16.5, 16.7, 16.9]
        values = [7.0, 7.5, 8.1, 8.4]
        selected = select_variogram_loo(lons, lats, values, requested="auto")
        self.assertIn(selected["model"], {"exponential", "gaussian"})
        self.assertEqual(selected["selection"], "leave_one_node_out")
        self.assertTrue(np.isfinite(selected["loo_rmse"]))
        screening = terrain_screening(
            [[0.0, 10.0, 20.0], [5.0, 15.0, 25.0], [10.0, 20.0, 30.0]],
            {"lat_min": 16.0, "lat_max": 16.1, "lon_min": -95.1, "lon_max": -95.0},
        )
        self.assertIn("not CFD", screening["label"])
        self.assertEqual(len(screening["exposure_sector_mean"]), 16)


if __name__ == "__main__":
    unittest.main()

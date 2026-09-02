from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from core.kriging import (
    analyze_spatial_topsis_maps,
    fit_variogram_loo,
    kriging_grid,
    kriging_interp,
)
from core.mcp_catalog import M04_LATITUDE, M04_LONGITUDE
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
    resolve_sector_roughness,
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

    def test_advanced_sector_roughness_uses_classic_dem_slope_fallback(self):
        with patch("core.terrain.get_landcover", return_value={"z0": None}), patch(
            "core.terrain.estimate_dem_slope_z0", return_value=0.24
        ):
            resolved = resolve_sector_roughness(M04_LATITUDE, M04_LONGITUDE)
        np.testing.assert_allclose(resolved["z0_by_sector_m"], [0.24] * 16)
        self.assertEqual(resolved["fallback_sectors"], 16)
        self.assertEqual(
            resolved["source"],
            "WorldCover_2021_sector_fetch_with_dem_slope_fallback",
        )

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
                poi={"lat": M04_LATITUDE, "lon": M04_LONGITUDE},
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
            np.asarray([[M04_LONGITUDE]]), np.asarray([[M04_LATITUDE]]), model="exponential", rng=50.0,
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
        self.assertEqual(maps["uncertainty"]["unit"], "m/s")
        self.assertEqual(maps["uncertainty"]["kriging_variance_unit"], "(m/s)^2")
        self.assertIn("wind_raw_reference", maps)
        self.assertIn("wind_calibration_delta", maps)
        self.assertEqual(maps["wind_raw_reference"]["bounds"], maps["wind_neutral"]["bounds"])
        raw_reference = maps["raw_reference"]
        self.assertFalse(raw_reference["uses_mcp_anchor"])
        self.assertFalse(raw_reference["uses_horizontal_calibration"])
        self.assertFalse(raw_reference["uses_local_roughness"])
        self.assertFalse(raw_reference["uses_orographic_correction"])
        self.assertAlmostEqual(sum(raw_reference["provider_weights"].values()), 1.0, places=12)
        self.assertEqual(set(raw_reference["provider_weights"]), {"ERA5", "MERRA2"})
        self.assertAlmostEqual(raw_reference["provider_weights"]["ERA5"], 0.5, places=12)
        raw_node = raw_reference["nodes"][0]
        expected_raw_reference = raw_node["raw_mean_speed_ms"] * (
            np.log(PROJECT_REFERENCE_HEIGHT_M / PROJECT_REFERENCE_ROUGHNESS_M)
            / np.log(raw_node["source_height_m"] / PROJECT_REFERENCE_ROUGHNESS_M)
        )
        self.assertAlmostEqual(raw_node["raw_reference_speed_ms"], expected_raw_reference, places=12)
        np.testing.assert_allclose(
            np.asarray(maps["wind_calibration_delta"]["z"]),
            np.asarray(maps["wind_neutral"]["z"]) - np.asarray(maps["wind_raw_reference"]["z"]),
        )
        self.assertEqual(len(maps["sectorial"]["frequency_pct"]), 16)
        self.assertEqual(maps["provenance"]["direction"], "meteorological_from_true_north_clockwise")
        self.assertFalse(maps["terrain"]["orography_speed_correction_applied"])

        dem = np.linspace(0.0, 450.0, 12 * 12).reshape(12, 12).tolist()
        z0 = np.full((12, 12), 0.24).tolist()
        with tempfile.TemporaryDirectory() as turbine_folder, patch(
            "core.terrain.sample_grid_elevation", return_value=dem
        ), patch("core.terrain.sample_grid_z0", return_value=z0) as roughness_sampler:
            physical_maps = build_calibrated_maps(
                result, turbines_folder=turbine_folder,
                n_grid=12, model="auto", target_height_m=80.0,
                physical=True,
            )
        roughness_sampler.assert_called_once()
        np.testing.assert_allclose(physical_maps["roughness"]["z"], z0)
        self.assertTrue(physical_maps["terrain"]["dem_slope_affects_wind_via_roughness"])
        self.assertFalse(physical_maps["terrain"]["direct_dem_speed_multiplier_applied"])
        self.assertEqual(
            physical_maps["terrain"]["roughness_method"],
            "WorldCover_preferred_DEM_slope_fallback",
        )
        self.assertFalse(np.allclose(
            np.asarray(physical_maps["wind"]["z"]),
            np.asarray(physical_maps["wind_neutral"]["z"]),
        ))

        with tempfile.TemporaryDirectory() as turbine_folder:
            turbine_name = "Prueba_p2000_r45.csv"
            (Path(turbine_folder) / turbine_name).write_text(
                "0,0\n3,0\n5,250\n8,900\n12,2000\n20,2000\n25,0\n",
                encoding="utf-8",
            )
            spatial = analyze_spatial_topsis_maps(
                maps, turbines_folder=turbine_folder,
                scope="exploratory", bounds=maps["wind"]["bounds"],
                price_usd_kwh=0.083, anios=31,
                max_points=6, turbine_filenames=[turbine_name],
            )
        self.assertEqual(spatial["field_source"], "mcp_calibrated_maps")
        self.assertEqual(spatial["n_input_stations"], maps["n_sites"])
        self.assertEqual(spatial["n_virtual_sites"], 6)
        self.assertTrue(all(site["source"] == "mcp_calibrated_kriging" for site in spatial["sites"]))
        self.assertTrue(all(len(site["sectorial"]["frequency_pct"]) == 16 for site in spatial["sites"]))

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

    def test_variogram_loo_matches_explicit_held_out_predictions(self):
        lons = np.asarray([-95.2, -95.0, -94.8, -94.6, -94.5])
        lats = np.asarray([16.3, 16.5, 16.7, 16.9, 17.0])
        values = np.asarray([7.0, 7.5, 8.1, 8.4, 8.6])
        selected = fit_variogram_loo(lons, lats, values, requested="auto")
        residuals = []
        for index in range(len(values)):
            keep = np.arange(len(values)) != index
            predicted, _ = kriging_interp(
                lons[keep], lats[keep], values[keep],
                np.asarray([[lons[index]]]), np.asarray([[lats[index]]]),
                variogram=selected,
            )
            residuals.append(float(predicted[0, 0]) - values[index])
        explicit_rmse = float(np.sqrt(np.mean(np.square(residuals))))
        self.assertAlmostEqual(selected["loo_rmse"], explicit_rmse, places=10)
        self.assertGreaterEqual(selected["sill"], selected["nugget"])
        self.assertNotEqual(selected["range_km"], 50.0)

    def test_kriging_variance_uses_squared_input_units(self):
        lons = [-95.2, -95.0, -94.8, -94.6, -94.5]
        lats = [16.3, 16.5, 16.7, 16.9, 17.0]
        values = np.asarray([7.0, 7.5, 8.1, 8.4, 8.6])
        base = kriging_grid(lons, lats, values, n=8, model="auto")
        scaled = kriging_grid(lons, lats, values * 10.0, n=8, model="auto")
        np.testing.assert_allclose(
            np.asarray(scaled["variance"]), np.asarray(base["variance"]) * 100.0,
            rtol=1e-6, atol=1e-10,
        )
        self.assertEqual(base["variance_units"], "squared_input_units")
        self.assertIn("empirical_variogram", base["variogram"])


if __name__ == "__main__":
    unittest.main()

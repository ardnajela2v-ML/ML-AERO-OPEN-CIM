from __future__ import annotations

import io
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from api import create_app
from config import Config
from core.mcp_catalog import M04_STATION_ID, build_mcp_catalog, classify_file
from core.forecasting import forecast_fx_recent


class MCPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_runs = Config.MCP_RUNS_FOLDER
        self.old_uploads = Config.MCP_UPLOAD_FOLDER
        Config.MCP_RUNS_FOLDER = str(Path(self.temporary.name) / "runs")
        Config.MCP_UPLOAD_FOLDER = str(Path(self.temporary.name) / "uploads")
        Path(Config.MCP_RUNS_FOLDER).mkdir(parents=True)
        Path(Config.MCP_UPLOAD_FOLDER).mkdir(parents=True)
        self.app = create_app()
        self.client = self.app.test_client()
        self.sid = self.client.post("/api/session/new", json={}).get_json()["session_id"]

    def tearDown(self):
        Config.MCP_RUNS_FOLDER = self.old_runs
        Config.MCP_UPLOAD_FOLDER = self.old_uploads
        self.temporary.cleanup()

    def test_catalog_uses_official_m04_raw_file_and_excludes_aliases(self):
        catalog = build_mcp_catalog(Config.DATA_FOLDER, Config.CERTE_FOLDER)
        observation = next(row for row in catalog["observations"] if row["station_id"] == M04_STATION_ID)
        self.assertEqual((observation["latitude"], observation["longitude"]), (16.55, -94.955))
        self.assertEqual(observation["file_ids"], ["M04_CERTE_CONSOLIDADO_2017-2020.txt"])
        self.assertEqual(observation["timezone"], "Etc/GMT+6")
        reference_files = {name for row in catalog["references"] for name in row["file_ids"]}
        self.assertFalse(reference_files.intersection(observation["legacy_aliases"]))

    def test_content_signature_wins_over_certe_filename(self):
        path = Path(self.temporary.name) / "CERTE_but_reanalysis.txt"
        path.write_text(
            "NASA Global Modeling and Assimilation Office\nMERRA-2\nDate/time [UTC]\tSpeed_50m [m/s]\n",
            encoding="utf-8",
        )
        detected = classify_file(path)
        self.assertEqual(detected["data_kind"], "reanalysis")
        self.assertEqual(detected["provider"], "MERRA2")
        self.assertEqual(detected["confidence"], "content")

    def test_study_enforces_roles_and_creates_revisions(self):
        catalog = self.client.get(f"/api/mcp/catalog?session_id={self.sid}").get_json()
        observation_id = catalog["observations"][0]["station_id"]
        reference_ids = [row["station_id"] for row in catalog["references"][:2]]
        invalid = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": reference_ids[0],
            "reference_station_ids": [observation_id],
        })
        self.assertEqual(invalid.status_code, 400)
        first = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": observation_id,
            "reference_station_ids": [reference_ids[0]],
            "poi": {"lat": 16.55, "lon": -94.955},
            "radius_km": 200,
            "target_height_m": 80,
        }).get_json()
        self.assertEqual(first["study_revision"], 1)
        self.assertIn("distance_km", first["reference_stations"][0])
        unchanged = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": observation_id,
            "reference_station_ids": [reference_ids[0]],
            "poi": {"lat": 16.55, "lon": -94.955},
            "radius_km": 200,
            "target_height_m": 80,
        }).get_json()
        self.assertEqual(unchanged["study_revision"], 1)
        changed = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": observation_id,
            "reference_station_ids": reference_ids,
            "poi": {"lat": 16.55, "lon": -94.955},
            "radius_km": 200,
            "target_height_m": 80,
        }).get_json()
        self.assertEqual(changed["study_revision"], 2)

    def test_park_configuration_enforces_commercial_mode_and_market_readiness(self):
        initial = self.client.get(
            f"/api/sessions/{self.sid}/park-configuration"
        ).get_json()
        self.assertEqual(initial["commercialization_mode"], "total_sale")
        self.assertTrue(initial["readiness"]["demand"])
        self.assertFalse(initial["ready_for_design"])
        self.assertIsNone(initial["base_fx_mxn_usd"])
        self.assertIsNone(initial["base_interest_rate_pct"])

        invalid = self.client.put(
            f"/api/sessions/{self.sid}/park-configuration",
            json={
                "commercialization_mode": "self_consumption",
                "sale_scheme": "spot_pml",
                "project_horizon_years": 25,
                "base_fx_mxn_usd": 20.0,
                "base_interest_rate_pct": 10.0,
            },
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("serie histórica", invalid.get_json()["error"])

        total_sale = self.client.put(
            f"/api/sessions/{self.sid}/park-configuration",
            json={
                "commercialization_mode": "total_sale",
                "sale_scheme": "spot_pml",
                "project_horizon_years": 25,
                "base_fx_mxn_usd": 20.0,
                "base_interest_rate_pct": 10.0,
            },
        ).get_json()
        self.assertEqual(total_sale["annual_demand_mwh"], 0.0)
        self.assertEqual(total_sale["demand_source"], "none")
        self.assertEqual(total_sale["base_fx_mxn_usd"], 20.0)
        self.assertEqual(total_sale["base_interest_rate_pct"], 10.0)

        pml = self.client.post("/api/pml/load", json={
            "session_id": self.sid,
            "preset": "pml_ipo",
        })
        self.assertEqual(pml.status_code, 200, pml.get_data(as_text=True))
        pml_data = pml.get_json()
        self.assertEqual(pml_data["mode"], "fixed_2018_profile")
        self.assertEqual(len(pml_data["profile_2018"]), 8760)
        self.assertEqual(pml_data["normalization"]["venta"]["filled_hours"], 1)
        legacy_pml = self.client.post("/api/pml/forecast", json={
            "session_id": self.sid, "preset": "pml_ipo",
        })
        self.assertEqual(legacy_pml.status_code, 410)

        fx = self.client.post("/api/fx/forecast", json={
            "session_id": self.sid,
            "preset": "usdmxn",
            "horizon": 5,
        })
        self.assertEqual(fx.status_code, 200, fx.get_data(as_text=True))
        fx_data = fx.get_json()
        self.assertEqual(fx_data["selection_basis"], "recent_windows_rolling_holdout")
        self.assertTrue(fx_data["validation"]["available"])
        self.assertTrue(fx_data["projection_policy"]["applied"])
        self.assertGreaterEqual(fx_data["projection_policy"]["training_window_years"], 5)
        self.assertGreaterEqual(int(fx_data["training_start"][:4]), 2015)
        fx_path = fx_data["projection"][fx_data["best_model"]]["annual"]
        self.assertEqual(len(fx_path), 5)
        self.assertTrue(all(value > 0 for value in fx_path))

        interest = self.client.post("/api/interest/forecast", json={
            "session_id": self.sid,
            "preset": "tiie_28d",
            "horizon": 5,
        })
        self.assertEqual(interest.status_code, 200, interest.get_data(as_text=True))
        interest_data = interest.get_json()
        self.assertEqual(interest_data["selection_basis"], "recent_window_chronological_holdout")
        self.assertTrue(interest_data["projection_policy"]["applied"])

        ready = self.client.get(
            f"/api/sessions/{self.sid}/park-configuration"
        ).get_json()
        self.assertTrue(ready["ready_for_design"])
        self.assertEqual(set(ready["market_models"]), {"pml", "fx", "interest"})
        self.assertEqual(ready["market_models"]["pml"]["mode"], "fixed_2018_profile")
        self.assertEqual(len(set(np.round(ready["market_models"]["pml"]["annual"], 12))), 1)
        self.assertEqual(len(ready["market_models"]["fx"]["annual"]), 5)

        files = self.client.get("/api/market/list_files").get_json()
        demand_file = files["grouped"]["demand"][0]["filename"]
        loaded_demand = self.client.post("/api/market/load_file", json={
            "session_id": self.sid,
            "filename": demand_file,
            "category": "demand",
        })
        self.assertEqual(loaded_demand.status_code, 200, loaded_demand.get_data(as_text=True))

        for mode in ("self_consumption", "self_consumption_surplus"):
            configured = self.client.put(
                f"/api/sessions/{self.sid}/park-configuration",
                json={
                    "commercialization_mode": mode,
                    "sale_scheme": "spot_pml",
                    "project_horizon_years": 25,
                    "base_fx_mxn_usd": 20.0,
                    "base_interest_rate_pct": 10.0,
                },
            )
            self.assertEqual(configured.status_code, 200, configured.get_data(as_text=True))
            configured_data = configured.get_json()
            self.assertEqual(configured_data["demand_source"], "market_series")
            self.assertEqual(configured_data["demand_series"]["filename"], demand_file)
            self.assertTrue(configured_data["readiness"]["demand"])
            self.assertTrue(configured_data["ready_for_design"])

    def test_fx_recent_forecast_is_independent_from_distant_history(self):
        index = pd.date_range("2005-01-31", "2024-12-31", freq="ME")
        phase = np.arange(len(index), dtype=float)
        values = 15.0 + 0.025 * phase + 0.7 * np.sin(phase / 9.0)
        original = pd.Series(values, index=index)
        altered = original.copy()
        altered.loc[altered.index.year <= 2012] *= 4.0

        base = forecast_fx_recent(original, horizon_years=5)
        changed = forecast_fx_recent(altered, horizon_years=5)
        self.assertEqual(base["best_model"], changed["best_model"])
        base_path = base["projection"][base["best_model"]]["annual"]
        changed_path = changed["projection"][changed["best_model"]]["annual"]
        np.testing.assert_allclose(base_path, changed_path, rtol=0, atol=1e-10)

    def _synthetic_inputs(self):
        first = pd.date_range("2018-01-01", periods=240, freq="1h")
        second = pd.date_range("2019-01-01", periods=240, freq="1h")
        index = first.append(second)
        phase = np.arange(len(index), dtype=float)
        reference = 7.0 + 2.0 * np.sin(phase / 11.0) + 0.3 * np.cos(phase / 3.0)
        observed = np.maximum(0.5 + 1.06 * reference + 0.05 * np.sin(phase / 7.0), 0.0)
        direction = (phase * 17.0) % 360.0
        observation_csv = pd.DataFrame({
            "timestamp": index.strftime("%Y-%m-%d %H:%M:%S"),
            "wind_speed": observed,
            "wind_direction": direction,
        }).to_csv(index=False).encode()
        lines = [
            "Reanalysis data requested by Windographer",
            "ERA5",
            "Latitude: 16.6",
            "Longitude: -95.0",
            "Date/time [UTC]\tSpeed_100m [m/s]\tDirection_100m [degrees]\tTemperature_2m [degrees C]\tPressure_0m [kPa]",
        ]
        lines.extend(
            f"{stamp:%Y-%m-%dT%H:%M:%S}\t{speed:.6f}\t{bearing:.3f}\t25.0\t100.0"
            for stamp, speed, bearing in zip(index, reference, direction)
        )
        return observation_csv, ("\n".join(lines) + "\n").encode()

    def test_end_to_end_upload_globe_mcp_publish_and_energy_source(self):
        observation_csv, reference_txt = self._synthetic_inputs()
        common = {"session_id": self.sid, "latitude": "16.55", "longitude": "-94.955"}
        observation = self.client.post("/api/mcp/uploads", data={
            **common,
            "file": (io.BytesIO(observation_csv), "tower.csv"),
            "data_kind": "observed",
            "provider": "USER",
            "display_name": "Torre sintética",
            "measurement_height_m": "80",
            "timezone": "UTC",
            "target_column": "wind_speed",
        }, content_type="multipart/form-data")
        self.assertEqual(observation.status_code, 201, observation.get_data(as_text=True))
        reference = self.client.post("/api/mcp/uploads", data={
            **common,
            "file": (io.BytesIO(reference_txt), "reference_ERA5.txt"),
            "data_kind": "reanalysis",
            "provider": "ERA5",
            "display_name": "ERA5 sintética",
            "measurement_height_m": "100",
            "timezone": "UTC",
        }, content_type="multipart/form-data")
        self.assertEqual(reference.status_code, 201, reference.get_data(as_text=True))
        observed_id = observation.get_json()["station"]["station_id"]
        reference_id = reference.get_json()["station"]["station_id"]
        study = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": observed_id,
            "reference_station_ids": [reference_id],
            "poi": {"lat": 16.55, "lon": -94.955},
            "radius_km": 100,
            "target_height_m": 80,
        })
        self.assertEqual(study.status_code, 200)
        started = self.client.post("/api/mcp/runs", json={
            "session_id": self.sid,
            "train_year": 2018,
            "test_year": 2019,
            "target_height_m": 80,
        })
        self.assertEqual(started.status_code, 202, started.get_data(as_text=True))
        ids = started.get_json()
        task = None
        for _ in range(300):
            task = self.client.get(f"/api/tasks/{ids['task_id']}").get_json()
            if task["status"] in {"done", "error"}:
                break
            time.sleep(0.05)
        self.assertEqual(task["status"], "done", task.get("message"))
        result = self.client.get(
            f"/api/mcp/runs/{ids['run_id']}?session_id={self.sid}"
        ).get_json()
        self.assertEqual(result["study"]["study_revision"], 1)
        self.assertIn("quantile_mapping", {row["method"] for row in result["mcp_method_comparison_2019"]})
        self.assertIn("seasonal_quantile_mapping", {
            row["method"] for row in result["mcp_method_comparison_2019"]
        })
        self.assertEqual(len(result["campaign_sensitivity"]), 6)
        visual = result["visualization"]["validation_forward"]
        self.assertGreater(len(visual["timeseries"]["timestamps"]), 0)
        self.assertIsNotNone(visual["wind_roses"])
        self.assertGreater(visual["wind_roses"]["measured"]["N"], 0)
        self.assertEqual(len(result["visualization"]["future_climate_25y"]["years"]), 25)
        self.assertGreater(len(result["visualization"]["campaign_daily"]["timestamps"]), 0)
        self.assertGreater(len(result["visualization"]["historical_monthly"]["timestamps"]), 0)
        self.assertEqual(len(result["visualization"]["gap_treatment"]["yearly"]), 2)
        self.assertIn("direction_correction", result)
        self.assertFalse(result["backtesting_multi_year"]["selected_method"].startswith("sector_qm_"))
        self.assertEqual(
            result["speed_model_selection"]["direction_model_policy"],
            "independent_16_sector_month_conditioned_circular_model_for_wind_rose_only",
        )
        self.assertEqual(result["wind_rose_direction_model"]["n_sectors"], 16)
        self.assertEqual(result["wind_rose_direction_model"]["purpose"], "wind_rose_direction_only")
        self.assertEqual(
            result["wind_rose_direction_model"]["method"],
            "seasonal_sectorwise_circular_offset",
        )
        self.assertEqual(result["selected_direction_reference"], result["selected_reference"])
        self.assertEqual(
            result["historical_reconstruction_model"]["method"],
            "seasonal_quantile_mapping",
        )
        self.assertIn("monthly_models", result["historical_reconstruction_model"])
        self.assertTrue(result["speed_model_selection"]["preferred_method_applied"])
        self.assertEqual(result["historical_reconstruction_model"]["training_year"], 2018)
        self.assertEqual(result["historical_reconstruction_model"]["training_pairs"], 240)
        self.assertNotIn("n_sectors", result["historical_reconstruction_model"])
        self.assertEqual(result["weibull_provenance"]["model_hindcast"]["method"], "MLE_floc_0")
        published = self.client.post(f"/api/mcp/runs/{ids['run_id']}/publish", json={"session_id": self.sid})
        self.assertEqual(published.status_code, 200, published.get_data(as_text=True))
        source_id = published.get_json()["source_id"]
        sources = self.client.get(f"/api/sessions/{self.sid}/data-sources").get_json()
        self.assertEqual(sources["active"], source_id)
        self.assertEqual({row["source_id"] for row in sources["sources"]}, {"observed", source_id})
        wind = self.client.post("/api/wind/stats", json={
            "session_id": self.sid,
            "data_source": source_id,
        })
        self.assertEqual(wind.status_code, 200, wind.get_data(as_text=True))
        self.assertIsNotNone(wind.get_json()["rose"])
        energy = self.client.post("/api/energy/compute", json={
            "session_id": self.sid,
            "data_source": source_id,
            "turbine": {"builtin_model": "V150_4.5"},
            "simulation": {"n_turbines": 1, "ws_height_m": 80},
        })
        self.assertEqual(energy.status_code, 200, energy.get_data(as_text=True))
        aep = energy.get_json()["aep"]
        self.assertIn("percentile_definition", aep)
        self.assertGreaterEqual(aep["aep_p50_mwh"], aep["aep_p75_mwh"])
        self.assertGreaterEqual(aep["aep_p75_mwh"], aep["aep_p90_mwh"])


if __name__ == "__main__":
    unittest.main()

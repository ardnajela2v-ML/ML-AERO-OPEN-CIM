from __future__ import annotations

import io
import gzip
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from api import _power_curve_at_hub_height, create_app
from config import Config
from core.energy import PowerCurve
from core.mcp_catalog import (
    M04_LATITUDE,
    M04_LONGITUDE,
    M04_STATION_ID,
    build_mcp_catalog,
    classify_file,
)
from core.forecasting import forecast_fx_recent


class MCPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_runs = Config.MCP_RUNS_FOLDER
        self.old_uploads = Config.MCP_UPLOAD_FOLDER
        self.old_general_uploads = Config.UPLOAD_FOLDER
        Config.MCP_RUNS_FOLDER = str(Path(self.temporary.name) / "runs")
        Config.MCP_UPLOAD_FOLDER = str(Path(self.temporary.name) / "uploads")
        Config.UPLOAD_FOLDER = str(Path(self.temporary.name) / "general_uploads")
        Path(Config.MCP_RUNS_FOLDER).mkdir(parents=True)
        Path(Config.MCP_UPLOAD_FOLDER).mkdir(parents=True)
        Path(Config.UPLOAD_FOLDER).mkdir(parents=True)
        self.app = create_app()
        self.client = self.app.test_client()
        self.sid = self.client.post("/api/session/new", json={}).get_json()["session_id"]

    def tearDown(self):
        Config.MCP_RUNS_FOLDER = self.old_runs
        Config.MCP_UPLOAD_FOLDER = self.old_uploads
        Config.UPLOAD_FOLDER = self.old_general_uploads
        self.temporary.cleanup()

    def test_project_export_import_restores_data_configuration_and_client_state(self):
        csv = (
            "timestamp,wind_speed,wind_direction\n"
            "2018-01-01 00:00:00,7.5,270\n"
            "2018-01-01 01:00:00,8.0,275\n"
            "2018-01-01 02:00:00,8.5,280\n"
        ).encode()
        uploaded = self.client.post('/api/data/upload', data={
            'session_id': self.sid,
            'source': 'auto',
            'file': (io.BytesIO(csv), 'project_wind.csv'),
        }, content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code, 200, uploaded.get_data(as_text=True))
        configured = self.client.put(
            f'/api/sessions/{self.sid}/park-configuration',
            json={
                'commercialization_mode': 'total_sale',
                'sale_scheme': 'ppa', 'project_horizon_years': 30,
                'base_fx_mxn_usd': 18.5, 'base_interest_rate_pct': 9.0,
            },
        )
        self.assertEqual(configured.status_code, 200, configured.get_data(as_text=True))

        exported = self.client.post('/api/projects/export', json={
            'session_id': self.sid,
            'project_name': 'Istmo prueba',
            'client_state': {
                'schema_version': 1,
                'krig': {'parkPoints': [[16.5, -95.0], [16.6, -95.0], [16.5, -94.9]]},
            },
        })
        self.assertEqual(exported.status_code, 200)
        self.assertIn('Istmo_prueba.aeroproj', exported.headers['Content-Disposition'])

        imported = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(exported.data), 'Istmo_prueba.aeroproj'),
        }, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201, imported.get_data(as_text=True))
        restored = imported.get_json()
        self.assertNotEqual(restored['session_id'], self.sid)
        self.assertEqual(restored['project_name'], 'Istmo prueba')
        self.assertEqual(restored['client_state']['krig']['parkPoints'][0], [16.5, -95.0])

        park = self.client.get(
            f"/api/sessions/{restored['session_id']}/park-configuration").get_json()
        self.assertEqual(park['project_horizon_years'], 30)
        self.assertEqual(park['sale_scheme'], 'ppa')
        stats = self.client.post('/api/wind/stats', json={
            'session_id': restored['session_id'],
        })
        self.assertEqual(stats.status_code, 200, stats.get_data(as_text=True))
        self.assertEqual(stats.get_json()['overview']['n_obs'], 3)

    def test_project_export_accepts_compressed_large_client_state(self):
        payload = {
            'session_id': self.sid,
            'project_name': 'UNISTMO comprimido',
            'client_state': {
                'schema_version': 1,
                'mcda': {'large_result': list(range(100_000))},
            },
        }
        compressed = gzip.compress(
            json.dumps(payload, separators=(',', ':')).encode('utf-8')
        )
        exported = self.client.post(
            '/api/projects/export', data=compressed,
            content_type='application/octet-stream',
            headers={'X-Aero-Content-Encoding': 'gzip'},
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn('UNISTMO_comprimido.aeroproj', exported.headers['Content-Disposition'])
        self.assertLess(len(compressed), len(json.dumps(payload).encode('utf-8')))
        imported = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(exported.data), 'UNISTMO_comprimido.aeroproj'),
        }, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201, imported.get_data(as_text=True))
        restored = imported.get_json()
        self.assertEqual(
            restored['client_state']['mcda']['large_result'][-1], 99_999)

    def test_project_import_rejects_non_project_file(self):
        response = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(b'not a zip'), 'fake.aeroproj'),
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        self.assertIn('No se pudo abrir el proyecto', response.get_json()['error'])

    def test_catalog_uses_official_m04_raw_file_and_excludes_aliases(self):
        catalog = build_mcp_catalog(Config.DATA_FOLDER, Config.CERTE_FOLDER)
        observation = next(row for row in catalog["observations"] if row["station_id"] == M04_STATION_ID)
        self.assertEqual(
            (observation["latitude"], observation["longitude"]),
            (M04_LATITUDE, M04_LONGITUDE),
        )
        self.assertEqual(observation["coordinate_status"], "verified")
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

    def test_catalog_prefers_internal_coordinates_and_groups_physical_aliases(self):
        data_root = Path(self.temporary.name) / "catalog_data"
        certe_root = Path(self.temporary.name) / "catalog_certe"
        data_root.mkdir()
        certe_root.mkdir()
        header = (
            "NASA Global Modeling and Assimilation Office\n"
            "MERRA-2\nLatitude: 16.500000\nLongitude: -95.000000\n"
            "Date/time [UTC]\tSpeed_50m [m/s]\n2018-01-01 00:00\t8.0\n"
        )
        first = data_root / "alias_MERRA2_N16,000_W94,000.txt"
        second = data_root / "alias_copy_MERRA2_N16,500_W95,000.txt"
        first.write_text(header, encoding="utf-8")
        second.write_text(header + "2018-01-01 01:00\t8.5\n", encoding="utf-8")

        catalog = build_mcp_catalog(data_root, certe_root)

        self.assertEqual(catalog["counts"]["references"], 1)
        node = catalog["references"][0]
        self.assertEqual((node["latitude"], node["longitude"]), (16.5, -95.0))
        self.assertEqual(node["coordinate_source"], "file_metadata")
        self.assertEqual(node["coordinate_status"], "corrected_filename_mismatch")
        self.assertEqual(node["alias_count"], 2)
        self.assertEqual(set(node["file_ids"]), {first.name, second.name})
        self.assertEqual(node["primary_file_id"], second.name)
        self.assertEqual(node["_paths"], [str(second.resolve())])

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
        changed_height = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": observation_id,
            "reference_station_ids": reference_ids,
            "poi": {"lat": 16.55, "lon": -94.955},
            "radius_km": 200,
            "target_height_m": 60,
        }).get_json()
        self.assertEqual(changed_height["study_revision"], 3)
        self.assertEqual(changed_height["target_height_m"], 60)
        invalid_height = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": observation_id,
            "reference_station_ids": reference_ids,
            "poi": {"lat": 16.55, "lon": -94.955},
            "radius_km": 200,
            "target_height_m": 0,
        })
        self.assertEqual(invalid_height.status_code, 400)

    def test_reanalysis_only_study_is_independent_from_mcp_and_restorable(self):
        catalog = self.client.get(f"/api/mcp/catalog?session_id={self.sid}").get_json()
        references = catalog["references"][:3]
        self.assertEqual(len(references), 3)
        reference_ids = [row["station_id"] for row in references]
        poi = {
            "lat": references[0]["latitude"],
            "lon": references[0]["longitude"],
        }

        insufficient = self.client.put(
            f"/api/sessions/{self.sid}/reanalysis-study",
            json={
                "reference_station_ids": reference_ids[:2],
                "poi": poi, "radius_km": 20_000, "target_height_m": 100,
            },
        )
        self.assertEqual(insufficient.status_code, 400)

        call_index = {'value': 0}

        def fake_reference_files(_paths):
            offset = call_index['value']
            call_index['value'] += 1
            timestamps = pd.date_range(
                '2018-01-01', periods=24 * 40, freq='h', tz='UTC')
            frame = pd.DataFrame({
                'wind_speed_ref': 6.0 + offset + np.sin(np.arange(len(timestamps)) / 24),
                'wind_direction_ref': (240 + offset * 10 + np.arange(len(timestamps)) % 30) % 360,
                'temperature_ref': 24.0 + offset,
                'pressure_ref_kpa': 99.0 - offset * 0.2,
            }, index=timestamps)
            return [SimpleNamespace(
                frame=frame, metadata={'wind_height_m': 50.0})]

        with patch('api.read_reference_files', side_effect=fake_reference_files):
            created = self.client.put(
                f"/api/sessions/{self.sid}/reanalysis-study",
                json={
                    "reference_station_ids": reference_ids,
                    "poi": poi, "radius_km": 20_000, "target_height_m": 100,
                },
            )
        self.assertEqual(created.status_code, 200, created.get_data(as_text=True))
        payload = created.get_json()
        self.assertEqual(payload["study_mode"], "reanalysis_only")
        self.assertEqual(payload["study_revision"], 1)
        self.assertEqual(len(payload["reference_stations"]), 3)
        self.assertNotIn("observed_station", payload)
        self.assertTrue(all("distance_km" in row for row in payload["reference_stations"]))
        self.assertTrue(payload['active_data_source'].startswith('reanalysis-area:'))
        self.assertEqual(payload['published_source']['kind'], 'reanalysis_area')
        self.assertEqual(payload['published_source']['node_count'], 3)
        self.assertFalse(payload['published_source']['is_mcp'])
        self.assertAlmostEqual(
            sum(row['weight'] for row in payload['published_source']['nodes']),
            1.0, places=6)

        unchanged = self.client.put(
            f"/api/sessions/{self.sid}/reanalysis-study",
            json={
                "reference_station_ids": reference_ids,
                "poi": poi, "radius_km": 20_000, "target_height_m": 100,
            },
        ).get_json()
        self.assertEqual(unchanged["study_revision"], 1)
        self.assertEqual(
            self.client.get(f"/api/sessions/{self.sid}/mcp-study").status_code,
            404,
        )
        resource = self.client.post('/api/wind/stats', json={
            'session_id': self.sid,
        })
        self.assertEqual(resource.status_code, 200, resource.get_data(as_text=True))
        self.assertEqual(resource.get_json()['data_source']['kind'], 'reanalysis_area')
        advanced = self.client.post('/api/wind/advanced', json={
            'session_id': self.sid,
        })
        self.assertEqual(advanced.status_code, 200, advanced.get_data(as_text=True))
        self.assertEqual(advanced.get_json()['data_source']['kind'], 'reanalysis_area')
        self.assertIn('no TI medida', advanced.get_json()['ti_warning'])

        quality = self.client.post('/api/data/quality', json={
            'session_id': self.sid,
        })
        self.assertEqual(quality.status_code, 200, quality.get_data(as_text=True))
        self.assertEqual(quality.get_json()['data_source']['kind'], 'reanalysis_area')

        features = self.client.post('/api/features/build', json={
            'session_id': self.sid,
            'target_col': 'wind_speed',
            'horizons': [1],
            'test_frac': 0.2,
        })
        self.assertEqual(features.status_code, 200, features.get_data(as_text=True))
        self.assertEqual(features.get_json()['data_source']['kind'], 'reanalysis_area')

        optional_demand = self.client.post('/api/farm/session_demand', json={
            'session_id': self.sid,
        })
        self.assertEqual(
            optional_demand.status_code, 200,
            optional_demand.get_data(as_text=True),
        )
        self.assertFalse(optional_demand.get_json()['found'])

        raw_export = self.client.post('/api/export/csv', json={
            'session_id': self.sid,
            'dataset': 'raw',
        })
        self.assertEqual(raw_export.status_code, 200, raw_export.get_data(as_text=True))
        raw_export.close()

        exported = self.client.post('/api/projects/export', json={
            'session_id': self.sid,
            'project_name': 'Área sólo reanálisis',
            'client_state': {
                'schema_version': 1,
                'wp': {
                    'studyMode': 'reanalysis_only',
                    'activeDataSource': payload['active_data_source'],
                },
            },
        })
        self.assertEqual(exported.status_code, 200)
        imported = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(exported.data), 'area.aeroproj'),
        }, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201, imported.get_data(as_text=True))
        restored = imported.get_json()
        self.assertIn('reanalysis_study', restored['restored_keys'])
        self.assertIn('active_study_mode', restored['restored_keys'])
        self.assertEqual(
            restored['client_state']['wp']['activeDataSource'],
            payload['active_data_source'])
        restored_study = self.client.get(
            f"/api/sessions/{restored['session_id']}/reanalysis-study"
        )
        self.assertEqual(restored_study.status_code, 200)
        self.assertEqual(restored_study.get_json()['study_mode'], 'reanalysis_only')
        restored_resource = self.client.post('/api/wind/stats', json={
            'session_id': restored['session_id'],
        })
        self.assertEqual(restored_resource.status_code, 200)
        self.assertEqual(
            restored_resource.get_json()['data_source']['kind'],
            'reanalysis_area')

    def test_explicit_hub_height_overrides_builtin_without_mutating_source(self):
        original = PowerCurve.from_builtin("V150_4.5")
        adjusted = _power_curve_at_hub_height(original, 120)
        self.assertEqual(original.hub_height_m, 105)
        self.assertEqual(adjusted.hub_height_m, 120)
        self.assertIsNot(original, adjusted)

    def test_run_target_height_is_persisted_in_study_snapshot(self):
        catalog = self.client.get(f"/api/mcp/catalog?session_id={self.sid}").get_json()
        observation_id = catalog["observations"][0]["station_id"]
        reference_id = catalog["references"][0]["station_id"]
        created = self.client.put(f"/api/sessions/{self.sid}/mcp-study", json={
            "observed_station_id": observation_id,
            "reference_station_ids": [reference_id],
            "poi": {"lat": 16.55, "lon": -94.955},
            "radius_km": 200,
            "target_height_m": 80,
        })
        self.assertEqual(created.status_code, 200)
        with patch("api.ThreadPoolExecutor.submit") as submit:
            started = self.client.post("/api/mcp/runs", json={
                "session_id": self.sid,
                "target_height_m": 60,
            })
        self.assertEqual(started.status_code, 202, started.get_data(as_text=True))
        self.assertEqual(started.get_json()["target_height_m"], 60)
        self.assertEqual(started.get_json()["study_revision"], 2)
        persisted = self.client.get(f"/api/sessions/{self.sid}/mcp-study").get_json()
        self.assertEqual(persisted["target_height_m"], 60)
        self.assertEqual(persisted["study_revision"], 2)
        submit.assert_called_once()

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
            self.assertGreater(configured_data["annual_demand_mwh"], 0)
            self.assertEqual(
                configured_data["annual_demand_mwh"],
                configured_data["demand_series"]["annual_mwh"],
            )
            self.assertTrue(configured_data["readiness"]["demand"])
            self.assertTrue(configured_data["ready_for_design"])

            design_demand = self.client.post("/api/farm/session_demand", json={
                "session_id": self.sid,
            })
            self.assertEqual(
                design_demand.status_code, 200,
                design_demand.get_data(as_text=True),
            )
            design_data = design_demand.get_json()
            self.assertTrue(design_data["found"])
            self.assertEqual(design_data["source"], "market_series")
            self.assertEqual(design_data["filename"], demand_file)
            self.assertAlmostEqual(
                design_data["annual_mwh"],
                configured_data["annual_demand_mwh"],
                places=3,
            )

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

    def test_farm_recommend_preserves_session_height_handoff(self):
        response = self.client.post("/api/farm/recommend", json={
            "session_id": self.sid,
            "turbine": {"builtin_model": "Vestas V90-2.0MW", "hub_height_m": 100},
            "demand_mwh": 5000,
            "v_mean": 8.0,
            "weibull_k": 2.0,
            "measurement_height_m": 80,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertGreaterEqual(response.get_json()["n_turbines"], 1)

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
        spatial_sites = self.client.get(
            "/api/kriging/sites",
            query_string={
                "session_id": self.sid,
                "data_source": source_id,
                "radius_km": 100,
                "poi_lat": 16.55,
                "poi_lon": -94.955,
            },
        )
        self.assertEqual(spatial_sites.status_code, 200, spatial_sites.get_data(as_text=True))
        spatial_payload = spatial_sites.get_json()
        self.assertEqual(spatial_payload["sites"][0]["station_id"], M04_STATION_ID)
        self.assertEqual(spatial_payload["sites"][0]["allowed_role"], "anchor")
        self.assertTrue(all(
            row["provider"] in {"ERA5", "MERRA2"}
            for row in spatial_payload["sites"][1:]
        ))
        self.assertTrue(all(
            row["station_id"] != M04_STATION_ID
            for row in spatial_payload["sites"][1:]
        ))
        self.assertTrue(spatial_payload["mcp_selection_independent"])
        wind = self.client.post("/api/wind/stats", json={
            "session_id": self.sid,
        })
        self.assertEqual(wind.status_code, 200, wind.get_data(as_text=True))
        wind_payload = wind.get_json()
        self.assertIsNotNone(wind_payload["rose"])
        self.assertEqual(wind_payload["data_source"]["source_id"], source_id)
        self.assertEqual(wind_payload["data_source"]["kind"], "mcp")

        features = self.client.post("/api/features/build", json={
            "session_id": self.sid,
            "target_col": "wind_speed",
            "horizons": [1],
            "test_frac": 0.2,
        })
        self.assertEqual(features.status_code, 200, features.get_data(as_text=True))
        self.assertEqual(features.get_json()["data_source"]["source_id"], source_id)

        energy = self.client.post("/api/energy/compute", json={
            "session_id": self.sid,
            "turbine": {"builtin_model": "V150_4.5"},
            "simulation": {"n_turbines": 1, "ws_height_m": 80},
        })
        self.assertEqual(energy.status_code, 200, energy.get_data(as_text=True))
        energy_payload = energy.get_json()
        self.assertEqual(energy_payload["data_source"]["source_id"], source_id)
        aep = energy_payload["aep"]
        self.assertIn("percentile_definition", aep)
        self.assertGreaterEqual(aep["aep_p50_mwh"], aep["aep_p75_mwh"])
        self.assertGreaterEqual(aep["aep_p75_mwh"], aep["aep_p90_mwh"])

        farm = self.client.post("/api/farm/simulate", json={
            "session_id": self.sid,
            "layout": [[0, 0]],
            "turbine": {"builtin_model": "V150_4.5"},
            "ws_height_m": 80,
        })
        self.assertEqual(farm.status_code, 200, farm.get_data(as_text=True))
        self.assertEqual(farm.get_json()["data_source"]["source_id"], source_id)

        recommendation = self.client.post("/api/farm/recommend", json={
            "session_id": self.sid,
            "turbine": {"builtin_model": "V150_4.5"},
            "demand_mwh": 10_000,
            "measurement_height_m": 80,
        })
        self.assertEqual(
            recommendation.status_code, 200,
            recommendation.get_data(as_text=True),
        )
        recommendation_payload = recommendation.get_json()
        self.assertEqual(recommendation_payload["data_source"]["source_id"], source_id)
        self.assertEqual(
            recommendation_payload["wind_input"]["source"],
            "active_project_source",
        )

        techno = self.client.post("/api/energy/techno_economic", json={
            "session_id": self.sid,
            "scenario": "integrated",
            "n_turbines": 1,
            "turbine": {"builtin_model": "V150_4.5"},
            "financial": {"project_years": 25},
        })
        self.assertEqual(techno.status_code, 200, techno.get_data(as_text=True))
        self.assertEqual(
            techno.get_json()["wind_source"]["data_source"]["source_id"],
            source_id,
        )


if __name__ == "__main__":
    unittest.main()

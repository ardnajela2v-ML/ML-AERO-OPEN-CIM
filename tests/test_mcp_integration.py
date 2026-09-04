from __future__ import annotations

import io
import gzip
import json
import tempfile
import time
import unittest
import zipfile
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

    def test_obsolete_arena_and_forecast_routes_are_not_registered(self):
        post_routes = (
            "/api/features/build", "/api/models/train",
            "/api/forecast/predictions", "/api/forecast/comparison",
            "/api/demand/load", "/api/demand/forecast",
            "/api/demand/forecast_hourly", "/api/pml/forecast",
            "/api/pml/forecast_hourly", "/api/interest/forecast",
            "/api/fx/forecast", "/api/wind/annual_forecast",
        )
        for route in post_routes:
            with self.subTest(route=route):
                response = self.client.post(route, json={"session_id": self.sid})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/models/status/dead-task").status_code, 404)
        self.assertEqual(self.client.get("/api/market/list_presets").status_code, 404)

    def test_economic_routes_reject_implicit_price_and_project_life(self):
        missing_both = self.client.post('/api/kriging/legacy_maps', json={})
        self.assertEqual(missing_both.status_code, 400)
        self.assertIn('precio', missing_both.get_json()['error'].lower())

        missing_years = self.client.post('/api/kriging/legacy_maps', json={
            'price_usd_kwh': 0.083,
        })
        self.assertEqual(missing_years.status_code, 400)
        self.assertIn('vida del proyecto', missing_years.get_json()['error'].lower())

        missing_price = self.client.post('/api/energy/techno_economic', json={
            'session_id': self.sid,
            'financial': {'project_years': 31},
        })
        self.assertEqual(missing_price.status_code, 400)
        self.assertIn('energy_price_usd_kwh', missing_price.get_json()['error'])

        leveraged = self.client.post('/api/energy/leveraged', json={
            'capex_usd': 1, 'annual_revenue_usd': 1, 'annual_opex_usd': 0,
        })
        self.assertEqual(leveraged.status_code, 400)
        self.assertIn('project_years', leveraged.get_json()['error'])

    def test_mcda_exposes_irena_design_cost_without_changing_techno_default(self):
        defaults = self.client.get('/api/multicriteria/defaults')
        self.assertEqual(defaults.status_code, 200)
        benchmark = defaults.get_json()['design_cost_benchmark']
        self.assertEqual(benchmark['total_installed_usd_per_kw'], 1041.0)
        self.assertEqual(benchmark['cost_model']['turbine_usd_per_kw'], 780.75)

        from core.cost_model import CostModel
        self.assertEqual(CostModel.from_defaults().turbine_usd_per_kw, 1800.0)

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

    def test_project_import_ignores_legacy_arena_market_models(self):
        manifest = {
            'format': 'aero-open-cim-project',
            'format_version': 1,
            'project_name': 'Proyecto anterior',
            'session_state': {
                'park_configuration': {
                    'commercialization_mode': 'total_sale',
                    'base_fx_mxn_usd': 19.5,
                    'base_interest_rate_pct': 9.5,
                    'market_models': {
                        'pml': {'mode': 'fixed_2018_profile', 'annual': [1.2]},
                        'demand': {'best_model': 'linear', 'annual': [100.0]},
                        'interest': {'best_model': 'arima', 'annual': [9.0]},
                        'fx': {'best_model': 'holt', 'annual': [20.0]},
                    },
                },
            },
            'client_state': {'schema_version': 1, 'wp': {'arenaCache': {'fx': {}}}},
        }
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
        imported = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(archive_bytes.getvalue()), 'legacy.aeroproj'),
        }, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201, imported.get_data(as_text=True))
        imported_payload = imported.get_json()
        self.assertEqual(imported_payload['client_state']['wp'], {})
        restored_sid = imported_payload['session_id']
        park = self.client.get(
            f'/api/sessions/{restored_sid}/park-configuration').get_json()
        self.assertEqual(set(park['market_models']), {'pml'})
        self.assertEqual(park['market_models']['pml']['mode'], 'fixed_2018_profile')

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
        ready = self.client.get(
            f"/api/sessions/{self.sid}/park-configuration"
        ).get_json()
        self.assertTrue(ready["ready_for_design"])
        self.assertEqual(set(ready["market_models"]), {"pml"})
        self.assertEqual(ready["market_models"]["pml"]["mode"], "fixed_2018_profile")
        self.assertEqual(len(set(np.round(ready["market_models"]["pml"]["annual"], 12))), 1)

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

    def test_current_commercial_design_energy_and_techno_flow(self):
        index = pd.date_range('2024-01-01', periods=240, freq='1h')
        phase = np.arange(len(index), dtype=float)
        wind_csv = pd.DataFrame({
            'timestamp': index,
            'wind_speed': 8.0 + 1.5 * np.sin(phase / 12.0),
            'wind_direction': (phase * 11.0) % 360.0,
        }).to_csv(index=False).encode()
        uploaded = self.client.post('/api/data/upload', data={
            'session_id': self.sid,
            'source': 'auto',
            'file': (io.BytesIO(wind_csv), 'active_flow_wind.csv'),
        }, content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code, 200, uploaded.get_data(as_text=True))

        files = self.client.get('/api/market/list_files').get_json()['grouped']
        for category in ('demand', 'pml'):
            loaded = self.client.post('/api/market/load_file', json={
                'session_id': self.sid,
                'filename': files[category][0]['filename'],
                'category': category,
            })
            self.assertEqual(loaded.status_code, 200, loaded.get_data(as_text=True))

        pml = self.client.post('/api/pml/load', json={
            'session_id': self.sid,
            'preset': 'session_market_pml',
        })
        self.assertEqual(pml.status_code, 200, pml.get_data(as_text=True))
        pml_profile = np.asarray(pml.get_json()['profile_2018'], dtype=float)
        self.assertEqual(len(pml_profile), 8760)

        configured = self.client.put(
            f'/api/sessions/{self.sid}/park-configuration', json={
                'commercialization_mode': 'self_consumption_surplus',
                'sale_scheme': 'spot_pml',
                'project_horizon_years': 25,
                'base_fx_mxn_usd': 20.0,
                'base_interest_rate_pct': 10.0,
            })
        self.assertEqual(configured.status_code, 200, configured.get_data(as_text=True))
        config = configured.get_json()
        self.assertTrue(config['ready_for_design'])
        self.assertEqual(set(config['market_models']), {'pml'})

        resized = self.client.put(
            f'/api/sessions/{self.sid}/park-configuration', json={
                'commercialization_mode': 'self_consumption_surplus',
                'sale_scheme': 'spot_pml',
                'project_horizon_years': 31,
                'base_fx_mxn_usd': 20.0,
                'base_interest_rate_pct': 10.0,
            })
        self.assertEqual(resized.status_code, 200, resized.get_data(as_text=True))
        resized_config = resized.get_json()
        self.assertEqual(resized_config['project_horizon_years'], 31)
        self.assertEqual(len(resized_config['market_models']['pml']['annual']), 31)
        self.assertEqual(len(resized_config['market_models']['pml']['years']), 31)
        config = resized_config

        design = self.client.post('/api/farm/recommend', json={
            'session_id': self.sid,
            'turbine': {'builtin_model': 'V150_4.5'},
            'demand_mwh': config['annual_demand_mwh'],
            'v_mean': 8.0,
            'weibull_k': 2.0,
            'measurement_height_m': 80,
        })
        self.assertEqual(design.status_code, 200, design.get_data(as_text=True))
        self.assertGreaterEqual(design.get_json()['n_turbines'], 1)

        energy = self.client.post('/api/energy/compute', json={
            'session_id': self.sid,
            'turbine': {'builtin_model': 'V150_4.5'},
            'simulation': {'n_turbines': 2, 'ws_height_m': 80},
        })
        self.assertEqual(energy.status_code, 200, energy.get_data(as_text=True))
        annual_mwh = energy.get_json()['summary']['energy_net_mwh']
        normalized_price = np.nan_to_num(
            pml_profile / max(float(np.nanmean(pml_profile)), 1e-9) * 0.07,
            nan=0.07, posinf=0.07, neginf=0.07,
        ).tolist()
        techno = self.client.post('/api/energy/techno_economic', json={
            'session_id': self.sid,
            'n_turbines': 2,
            'annual_energy_mwh': annual_mwh,
            'turbine': {'builtin_model': 'V150_4.5'},
            'financial': {
                'demand_mwh': config['annual_demand_mwh'],
                'energy_price_usd_kwh': 0.07,
                'energy_price_series_usd_kwh': [0.07] * config['project_horizon_years'],
                'price_hourly_year1_usd_kwh': normalized_price,
                'discount_rate_pct': 10.0,
                'project_years': config['project_horizon_years'],
                'usd_to_mxn': 20.0,
            },
        })
        self.assertEqual(techno.status_code, 200, techno.get_data(as_text=True))
        analysis = techno.get_json()['analysis']
        self.assertEqual(analysis['project_years'], 31)
        self.assertEqual(analysis['discount_rate_pct'], 10.0)
        self.assertEqual(analysis['fx_mxn_per_usd'], 20.0)
        self.assertNotIn('discount_rate_series_pct', analysis)
        self.assertNotIn('fx_mxn_per_usd_series', analysis)

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

    def _synthetic_inputs(self, train_year=2018, test_year=2019):
        first = pd.date_range(f"{train_year}-01-01", periods=240, freq="1h")
        second = pd.date_range(f"{test_year}-01-01", periods=240, freq="1h")
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
        train_year, test_year = 2020, 2021
        observation_csv, reference_txt = self._synthetic_inputs(train_year, test_year)
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
            "train_year": train_year,
            "test_year": test_year,
            "target_height_m": 80,
            "power_curve_file": "vestas20_p2000_r40.csv",
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
        self.assertEqual(result["config"]["train_year"], train_year)
        self.assertEqual(result["config"]["test_year"], test_year)
        self.assertEqual(result["mcp_method_comparison"], result["mcp_method_comparison_2019"])
        self.assertEqual(result["ml_model_comparison"], result["ml_model_comparison_2019"])
        self.assertEqual(result["ml_validation"], result["ml_validation_2019"])
        self.assertIn("quantile_mapping", {row["method"] for row in result["mcp_method_comparison"]})
        self.assertIn("seasonal_quantile_mapping", {
            row["method"] for row in result["mcp_method_comparison"]
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
        self.assertTrue(result["backtesting_multi_year"]["diagnostic_only"])
        self.assertFalse(result["backtesting_multi_year"]["affects_model_selection"])
        self.assertFalse(result["speed_model_selection"]["selected_method"].startswith("sector_qm_"))
        self.assertEqual(
            result["speed_model_selection"]["selection_scope"],
            "chronological_holdout_inside_training_year_only",
        )
        self.assertFalse(result["speed_model_selection"]["test_year_used_for_selection"])
        self.assertIsNone(result["speed_model_selection"]["preferred_method"])
        self.assertFalse(result["speed_model_selection"]["preferred_method_applied"])
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
            result["speed_model_selection"]["selected_method"],
        )
        self.assertEqual(result["historical_reconstruction_model"]["training_year"], train_year)
        self.assertEqual(result["historical_reconstruction_model"]["training_pairs"], 240)
        self.assertNotIn("n_sectors", result["historical_reconstruction_model"])
        self.assertEqual(result["selection_data_policy"]["test_year_used_for_selection"], False)
        self.assertIn(str(train_year), result["selection_data_policy"]["model_selection"])
        self.assertIn(str(test_year), result["selection_data_policy"]["final_evaluation"])
        self.assertEqual(result["weibull_provenance"]["model_hindcast"]["method"], "MLE_floc_0")
        density = result["energy_forward"]["density_correction"]
        self.assertGreater(density["hours_reanalysis_temperature_pressure"], 0)
        self.assertEqual(
            density["hours_reanalysis_temperature_pressure"]
            + density["hours_standard_fallback"],
            result["energy_forward"]["concurrent_hours"],
        )
        energy_artifact = Path(
            result["artifacts"][result["selected_reference"] + "_energy_forward"]
        )
        energy_columns = set(pd.read_csv(energy_artifact, nrows=1).columns)
        self.assertTrue({
            "air_density_kg_m3", "density_source",
            "observed_power_kw_gross", "observed_power_kw_gross_density_corrected",
            "predicted_power_kw_gross", "predicted_power_kw_gross_density_corrected",
        }.issubset(energy_columns))
        for artifact_path in result["artifacts"].values():
            self.assertTrue(Path(artifact_path).exists(), artifact_path)
        self.assertIn(str(test_year), Path(
            result["artifacts"][result["selected_reference"] + "_model_comparison"]
        ).name)
        report_text = Path(result["artifacts"]["final_report_html"]).read_text(encoding="utf-8")
        self.assertIn(f"Validación {test_year}", report_text)
        self.assertIn(f"interna de {train_year}", report_text)
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
            "financial": {
                "project_years": 25,
                "energy_price_usd_kwh": 0.07,
            },
        })
        self.assertEqual(techno.status_code, 200, techno.get_data(as_text=True))
        self.assertEqual(
            techno.get_json()["wind_source"]["data_source"]["source_id"],
            source_id,
        )


if __name__ == "__main__":
    unittest.main()

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
from unittest.mock import MagicMock, patch

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
        self.assertIn('hours_near_cpmax', defaults.get_json()['layout_weights'])
        self.assertIn('cp_effective_full_series', defaults.get_json()['layout_weights'])
        self.assertEqual(len(defaults.get_json()['layout_weights']), 7)
        from core.multicriteria import LAYOUT_WEIGHTS
        self.assertEqual(defaults.get_json()['layout_weights'], LAYOUT_WEIGHTS)
        self.assertAlmostEqual(defaults.get_json()['layout_weights']['factor_capacidad'],
                                0.1073, places=4)
        self.assertNotIn('discounted_payback_years', defaults.get_json()['layout_weights'])
        self.assertAlmostEqual(sum(defaults.get_json()['layout_weights'].values()), 1.0)
        from core.multicriteria import LAYOUT_AUDIT_ONLY
        for key in LAYOUT_AUDIT_ONLY:
            self.assertNotIn(key, defaults.get_json()['layout_weights'])
            self.assertNotIn(key, defaults.get_json()['layout_directions'])
            self.assertNotIn(key, defaults.get_json()['layout_labels'])
        self.assertNotIn('layout_scenarios', defaults.get_json())

        from core.cost_model import CostModel
        self.assertEqual(CostModel.from_defaults().turbine_usd_per_kw, 945.0)

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

    def test_project_snapshot_restores_outputs_and_portable_run_artifacts(self):
        from inspect import getclosurevars
        get_session = getclosurevars(self.app.view_functions['export_project']).nonlocals['get_session']
        session = get_session(self.sid)
        run_dir = Path(Config.MCP_RUNS_FOLDER) / 'portable-run'
        run_dir.mkdir()
        artifact = run_dir / 'historical.csv'
        artifact.write_text('timestamp,wind_speed\n2018-01-01,8.5\n')
        result_path = run_dir / 'result.json'
        result_path.write_text(json.dumps({
            'run_id': 'portable-run', 'metrics': {'r2': 0.987},
            'artifacts': {'historical_reconstruction_csv': str(artifact)},
        }))
        calibration_path = run_dir / 'calibration.json'
        calibration_path.write_text(json.dumps({'calibration_id': 'portable-cal', 'nodes': [1, 2]}))
        session.update({
            'mcp_runs': {'portable-run': {'result_path': str(result_path), 'study_revision': 1}},
            'mcp_study': {'study_revision': 1},
            'kriging_calibrations': {'portable-cal': {'result_path': str(calibration_path)}},
            'df_energy': pd.DataFrame({'energy_kwh': [12.5, 19.25]}),
        })
        wp = {key: {'saved': [1, 2, 3]} for key in (
            'advancedResult', 'qcResult', 'mcpResult', 'energySimulation')}
        exported = self.client.post('/api/projects/export', json={
            'session_id': self.sid, 'client_state': {'schema_version': 1, 'snapshot_version': 2, 'wp': wp},
        })
        self.assertEqual(exported.status_code, 200)
        artifact.unlink()
        result_path.unlink()
        calibration_path.unlink()
        self.client.delete(f'/api/sessions/{self.sid}')
        imported = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(exported.data), 'portable.aeroproj'),
        }, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201, imported.get_data(as_text=True))
        payload = imported.get_json()
        sid = payload['session_id']
        self.assertEqual(payload['client_state']['wp'], wp)
        pd.testing.assert_frame_equal(get_session(sid)['df_energy'], pd.DataFrame({'energy_kwh': [12.5, 19.25]}))
        self.assertEqual(self.client.get(f'/api/projects/restored/{sid}').get_json(), payload)
        run = self.client.get(f'/api/mcp/runs/portable-run?session_id={sid}')
        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.get_json()['metrics']['r2'], 0.987)
        download = self.client.get(f'/api/mcp/runs/portable-run/artifacts/historical_reconstruction_csv?session_id={sid}')
        self.assertEqual(download.status_code, 200)
        self.assertIn(b'2018-01-01,8.5', download.data)
        calibration = self.client.get(f'/api/kriging/calibrations/portable-cal?session_id={sid}')
        self.assertEqual(calibration.status_code, 200)
        self.assertEqual(calibration.get_json()['nodes'], [1, 2])
        # Re-saving an imported project must keep it portable too.
        again = self.client.post('/api/projects/export', json={'session_id': sid, 'client_state': payload['client_state']})
        self.assertEqual(again.status_code, 200)
        imported_again = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(again.data), 'again.aeroproj'),
        }, content_type='multipart/form-data').get_json()
        self.assertEqual(self.client.get(f"/api/mcp/runs/portable-run?session_id={imported_again['session_id']}").status_code, 200)

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

    def test_classify_file_retries_a_short_first_read_from_slow_storage(self):
        """A read() returning fewer bytes than the file actually has, without
        raising, is legitimate POSIX behaviour — observed in practice against
        files on slow-to-mount storage. It must not be mistaken for empty/
        unreadable content and silently misclassify a CERTE export via the
        filename fallback (see core/mcp_catalog.py's module docstring and
        _read_signature)."""
        import core.mcp_catalog as mcp_catalog

        path = Path(self.temporary.name) / "M04CERTE_2018_Windnavigator_MERRA2_slow.txt"
        content = (
            "Reanalysis-style data converted from CERTE measured data\n"
            "Source: CERTE measured mast data\n"
            "WS_80mA_mean\tWD_78m_mean\n" + "x" * 400
        ).encode("utf-8")
        path.write_bytes(content)

        handle = MagicMock()
        handle.read.side_effect = [b"", content]
        opened = MagicMock()
        opened.__enter__.return_value = handle
        opened.__exit__.return_value = False

        with patch.object(mcp_catalog.Path, "open", return_value=opened) as mock_open, \
                patch.object(mcp_catalog.time, "sleep"):
            result = mcp_catalog.classify_file(path)

        self.assertEqual(mock_open.call_count, 2, "must retry instead of trusting a short read")
        self.assertEqual(result, {"data_kind": "observed", "provider": "CERTE", "confidence": "content"})

    def test_classify_file_gives_up_after_repeated_short_reads(self):
        """If retries are exhausted, fall through to the (documented-unreliable)
        filename heuristic rather than hang or raise — a permanently unreadable
        cold file must not crash catalog building."""
        import core.mcp_catalog as mcp_catalog

        path = Path(self.temporary.name) / "M04CERTE_2018_Windnavigator_MERRA2_stuck.txt"
        path.write_bytes(b"Source: CERTE measured mast data\n" + b"x" * 400)

        handle = MagicMock()
        handle.read.return_value = b""
        opened = MagicMock()
        opened.__enter__.return_value = handle
        opened.__exit__.return_value = False

        with patch.object(mcp_catalog.Path, "open", return_value=opened) as mock_open, \
                patch.object(mcp_catalog.time, "sleep"):
            result = mcp_catalog.classify_file(path)

        self.assertEqual(mock_open.call_count, 3, "should stop after the documented attempt budget")
        self.assertEqual(result["data_kind"], "reanalysis")
        self.assertEqual(result["confidence"], "filename")

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

    def _upload_single_observation(self, speed=6.0, latitude=16.5, start='2020-01-01'):
        index = pd.date_range(start, periods=72, freq='h')
        frame = pd.DataFrame({'timestamp': index, 'wind_speed': speed + np.arange(72) / 100,
                              'wind_direction': 240.0 + np.arange(72) % 20,
                              'pressure': 995.52, 'temperature': 25.68})
        frame.loc[10, 'wind_speed'] = np.nan
        response = self.client.post('/api/mcp/uploads', data={
            'session_id': self.sid, 'data_kind': 'observed', 'provider': 'USER',
            'latitude': str(latitude), 'longitude': '-95', 'measurement_height_m': '80',
            'timezone': 'UTC', 'file': (io.BytesIO(frame.to_csv(index=False).encode()), 'real.csv'),
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()['station']

    def _select_single_station(self, station, **overrides):
        body = {'station_id': station['station_id'],
                'poi': {'lat': station['latitude'], 'lon': station['longitude']},
                'radius_km': 200, 'target_height_m': 80, **overrides}
        return self.client.put(f'/api/sessions/{self.sid}/single-station-study', json=body)

    def test_single_observed_without_mcp_keeps_gaps_provenance_and_restores(self):
        station = self._upload_single_observation()
        response = self._select_single_station(station)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        study = response.get_json()
        self.assertEqual(study['active_data_source'], 'observed')
        self.assertEqual(study['study_mode'], 'observed_only')
        self.assertFalse(study['published_source']['is_mcp'])
        self.assertFalse(study['published_source']['transformations'][0]['gap_filling'])
        self.assertEqual(study['published_source']['rows'], 72)
        stats = self.client.post('/api/wind/stats', json={'session_id': self.sid})
        self.assertEqual(stats.status_code, 200, stats.get_data(as_text=True))
        self.assertEqual(stats.get_json()['data_source']['station_id'], station['station_id'])
        self.assertEqual(stats.get_json()['data_source']['source_id'], 'observed')
        self.assertGreater(stats.get_json()['overview']['rho_used'], 1.0)
        self.assertEqual(stats.get_json()['overview']['pressure_mean'], 995.52)
        self.assertEqual(study['published_source']['years'], [2020])
        self.assertEqual(study['published_source']['period_selection'], 'full_available_record')
        quality = self.client.post('/api/data/quality', json={'session_id': self.sid})
        self.assertEqual(quality.status_code, 200, quality.get_data(as_text=True))
        exported = self.client.post('/api/projects/export', json={
            'session_id': self.sid, 'project_name': 'Observación única',
            'client_state': {'schema_version': 1, 'wp': {'singleStationStudy': study,
                'studyMode': 'observed_only', 'activeDataSource': 'observed'}}})
        self.assertEqual(exported.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
            # The saved canonical frame still has the original missing speed.
            frames = [json.loads(archive.read(name)) for name in archive.namelist()
                      if name.startswith('frames/') and name.endswith('.json')]
            speeds = [row['wind_speed'] for frame in frames for row in frame.get('data', [])
                      if 'wind_speed' in row]
            self.assertIn(None, speeds)
        imported = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(exported.data), 'single.aeroproj')}, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201, imported.get_data(as_text=True))
        restored = imported.get_json()
        self.assertIn('single_station_study', restored['restored_keys'])
        saved = self.client.get(f"/api/sessions/{restored['session_id']}/single-station-study").get_json()
        self.assertEqual(saved['active_data_source'], 'observed')
        self.assertEqual(saved['station_id'], station['station_id'])
        blocked = self.client.post('/api/mcp/runs/old-run/publish', json={'session_id': self.sid})
        self.assertEqual(blocked.status_code, 409)

    def test_topsis_receives_selected_historical_pairs_and_rejects_late_period(self):
        station = self._upload_single_observation(start='2019-12-31')
        self.assertEqual(self._select_single_station(station).status_code, 200)
        period_route = f'/api/sessions/{self.sid}/resource-period'
        self.client.put(period_route, json={'source_id': 'observed', 'year': 2020})
        spatial = {'sites': [{'v_mean': 6, 'rho_mean': 1.2}], 'n_virtual_sites': 1,
                   'spatial_sources': [{'station_id': 'era5-test'}]}
        captured = []
        change_period = [False]
        def evaluate(**kwargs):
            history = kwargs['historical_wind']
            captured.append(history)
            if change_period[0]:
                with self.app.test_client() as client:
                    result = client.put(period_route, json={'source_id': 'observed', 'year': 2019})
                    self.assertEqual(result.status_code, 200)
            return {'alternatives': [], 'ranking': {'ranking': []}}
        with patch('api.analyze_spatial_topsis_sites', return_value=spatial), patch(
                'api.analyze_park_layouts', side_effect=evaluate):
            for obsolete in [False, True]:
                change_period[0] = obsolete
                response = self.client.post('/api/multicriteria/spatial', json={
                    'session_id': self.sid, 'scope': 'park', 'price_usd_kwh': .08, 'project_years': 25,
                    'polygon': [{'lat': 16.5, 'lon': -95}, {'lat': 16.5, 'lon': -94.99},
                                {'lat': 16.51, 'lon': -95}]})
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                task_id = response.get_json()['task_id']
                for _ in range(200):
                    task = self.client.get(f'/api/kriging/status/{task_id}').get_json()
                    if task['status'] in ['done', 'error']:
                        break
                    time.sleep(.01)
                self.assertEqual(task['status'], 'error' if obsolete else 'done', task)
                if obsolete:
                    self.assertIn('periodo', task['message'])
        self.assertEqual(len(captured[0]['ws']), 48)
        self.assertEqual(len(captured[0]['wd']), 48)
        self.assertEqual(captured[0]['provenance']['selected_year'], 2020)
        self.assertGreater(float(np.mean(captured[0]['pressure_pa'])), 90000)

    def test_spatial_topsis_computes_hour_of_year_and_loads_pml_when_available(self):
        """Fase 1/2 de robustez de ingreso real (chronological_revenue, ver
        project_windpro_pending_investigations): api.py debe (a) calcular
        hour_of_year desde las fechas reales de la fuente activa y (b) pasar
        el PML horario cargado en la sesión — ambos None/ausentes si no
        aplica, sin romper el camino existente."""
        station = self._upload_single_observation(start='2020-01-01')
        self.assertEqual(self._select_single_station(station).status_code, 200)
        pml = self.client.post('/api/pml/load', json={'session_id': self.sid})
        self.assertEqual(pml.status_code, 200, pml.get_data(as_text=True))

        spatial = {'sites': [{'v_mean': 6, 'rho_mean': 1.2}], 'n_virtual_sites': 1,
                   'spatial_sources': [{'station_id': 'era5-test'}]}
        captured = {}
        def evaluate(**kwargs):
            captured['historical_wind'] = kwargs['historical_wind']
            captured['price_hourly_usd_kwh'] = kwargs.get('price_hourly_usd_kwh')
            return {'alternatives': [], 'ranking': {'ranking': []}}
        with patch('api.analyze_spatial_topsis_sites', return_value=spatial), patch(
                'api.analyze_park_layouts', side_effect=evaluate):
            response = self.client.post('/api/multicriteria/spatial', json={
                'session_id': self.sid, 'scope': 'park', 'price_usd_kwh': .08, 'project_years': 25,
                'polygon': [{'lat': 16.5, 'lon': -95}, {'lat': 16.5, 'lon': -94.99},
                            {'lat': 16.51, 'lon': -95}]})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            task_id = response.get_json()['task_id']
            for _ in range(200):
                task = self.client.get(f'/api/kriging/status/{task_id}').get_json()
                if task['status'] in ['done', 'error']:
                    break
                time.sleep(.01)
            self.assertEqual(task['status'], 'done', task)
        # Fila 0 = 2020-01-01 00:00 UTC -> hora 0 del año; 72 horas
        # consecutivas de enero no cruzan un cambio de mes/bisiesto.
        np.testing.assert_array_equal(captured['historical_wind']['hour_of_year'], np.arange(72))
        price = captured['price_hourly_usd_kwh']
        self.assertIsInstance(price, list)
        self.assertEqual(len(price), 8760)
        self.assertTrue(all(0 <= p <= 5 for p in price))  # rango plausible USD/kWh, no MXN/MWh sin convertir

    def test_resource_period_preserves_observed_source_and_project_roundtrip(self):
        station = self._upload_single_observation(start='2019-12-31')
        self.assertEqual(self._select_single_station(station).status_code, 200)
        route = f'/api/sessions/{self.sid}/resource-period'
        initial = self.client.get(route).get_json()
        self.assertIsNone(initial['year'])
        self.assertEqual([row['year'] for row in initial['available_years']], [2019, 2020])
        self.assertEqual(self.client.put(route, json={'source_id': 'old', 'year': 2020}).status_code, 409)
        self.assertEqual(self.client.put(route, json={'source_id': 'observed', 'year': 1990}).status_code, 400)
        response = self.client.put(route, json={'source_id': 'observed', 'year': 2020})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['year'], 2020)
        stats = self.client.post('/api/wind/stats', json={'session_id': self.sid}).get_json()
        self.assertEqual(stats['data_source']['source_id'], 'observed')
        self.assertEqual(stats['data_source']['selected_year'], 2020)
        self.assertEqual(stats['data_source']['rows'], 48)
        self.assertEqual(len(stats['rose_36']['frequency_pct']), 36)
        sites = self.client.get('/api/kriging/sites', query_string={
            'session_id': self.sid, 'data_source': 'observed', 'anchored': '1',
            'poi_lat': station['latitude'], 'poi_lon': station['longitude'], 'radius_km': 200})
        self.assertEqual(sites.status_code, 200, sites.get_data(as_text=True))
        self.assertEqual(sites.get_json()['sites'][0]['station_id'], station['station_id'])
        exported = self.client.post('/api/projects/export', json={'session_id': self.sid})
        self.assertEqual(exported.status_code, 200)
        imported = self.client.post('/api/projects/import', data={
            'file': (io.BytesIO(exported.data), 'period.aeroproj')}, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201)
        restored_route = f"/api/sessions/{imported.get_json()['session_id']}/resource-period"
        self.assertEqual(self.client.get(restored_route).get_json()['year'], 2020)
        restored = self.client.put(restored_route, json={'source_id': 'observed', 'year': None})
        self.assertEqual(restored.status_code, 200)
        self.assertIsNone(restored.get_json()['year'])
        restored_stats = self.client.post('/api/wind/stats', json={
            'session_id': imported.get_json()['session_id']}).get_json()
        self.assertEqual(restored_stats['data_source']['rows'], 72)

    def test_single_station_validates_area_height_and_failed_read_without_switching(self):
        station = self._upload_single_observation()
        self.assertEqual(self._select_single_station(station).status_code, 200)
        for override in ({'radius_km': 0}, {'target_height_m': 0},
                         {'target_height_m': float('nan')}, {'poi': {'lat': 91, 'lon': 0}},
                         {'poi': {'lat': 0, 'lon': 0}, 'radius_km': 1},
                         {'station_id': 'missing'}):
            with self.subTest(override=override):
                self.assertEqual(self._select_single_station(station, **override).status_code, 400)
        with patch('api.read_certe_files', side_effect=ValueError('unreadable')):
            self.assertEqual(self._select_single_station(station).status_code, 400)
        saved = self.client.get(f'/api/sessions/{self.sid}/single-station-study').get_json()
        self.assertEqual(saved['station_id'], station['station_id'])
        self.assertEqual(saved['active_data_source'], 'observed')

    def test_single_observation_switch_invalidates_cached_stats_for_same_shape(self):
        first = self._upload_single_observation(4)
        second = self._upload_single_observation(10)
        means = []
        for station in (first, second):
            self.assertEqual(self._select_single_station(station).status_code, 200)
            response = self.client.post('/api/wind/stats', json={'session_id': self.sid})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            payload = response.get_json()
            self.assertEqual(payload['data_source']['station_id'], station['station_id'])
            means.append(payload)
        self.assertNotEqual(means[0]['overview'], means[1]['overview'])

    def test_single_virtual_uses_only_selected_station_and_kriging_keeps_catalog(self):
        catalog = self.client.get(f'/api/mcp/catalog?session_id={self.sid}').get_json()
        station = catalog['references'][0]
        frame = pd.DataFrame({'wind_speed_ref': np.linspace(5, 8, 72),
                              'wind_direction_ref': 220.0 + np.arange(72) % 20},
                             index=pd.date_range('2020-01-01', periods=72, freq='h', tz='UTC'))
        with patch('api.read_reference_files', return_value=[SimpleNamespace(
                frame=frame, metadata={'wind_height_m': 50})]) as reader:
            response = self._select_single_station(station, target_height_m=100)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(reader.call_count, 1)
        study = response.get_json()
        self.assertEqual(study['source_id'], 'reanalysis:' + station['station_id'])
        self.assertEqual(study['study_mode'], 'single_reference')
        self.assertEqual(study['published_source']['source_height_m'], 50)
        self.assertEqual(study['published_source']['transformations'][-1]['method'], 'hellmann')
        stats = self.client.post('/api/wind/stats', json={'session_id': self.sid})
        self.assertEqual(stats.status_code, 200, stats.get_data(as_text=True))
        self.assertEqual(stats.get_json()['data_source']['source_id'], study['source_id'])
        # The spatial catalog is independent of the selected temporal station.
        spatial = self.client.get('/api/kriging/sites/light').get_json()
        self.assertGreater(spatial['count'], 1)
        self.assertEqual(self.client.get(f'/api/sessions/{self.sid}/data-sources').get_json()['active'], study['source_id'])

    def test_pending_statistics_cannot_overwrite_a_new_source(self):
        from concurrent.futures import ThreadPoolExecutor
        first = self._upload_single_observation(4)
        second = self._upload_single_observation(10)
        self.assertEqual(self._select_single_station(first).status_code, 200)
        switched = False
        from core.wind_stats import compute_site_overview as original_overview
        def switch_while_computing(*args, **kwargs):
            nonlocal switched
            if not switched:
                switched = True
                def select_new():
                    with self.app.test_client() as client:
                        return client.put(f'/api/sessions/{self.sid}/single-station-study', json={
                            'station_id': second['station_id'], 'poi': {'lat': 16.5, 'lon': -95},
                            'radius_km': 200, 'target_height_m': 80}).status_code
                with ThreadPoolExecutor(max_workers=1) as executor:
                    self.assertEqual(executor.submit(select_new).result(), 200)
            return original_overview(*args, **kwargs)
        with patch('api.compute_site_overview', side_effect=switch_while_computing):
            old = self.client.post('/api/wind/stats', json={'session_id': self.sid})
        self.assertEqual(old.status_code, 409)
        fresh = self.client.post('/api/wind/stats', json={'session_id': self.sid})
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(fresh.get_json()['data_source']['station_id'], second['station_id'])

    def test_late_single_station_preparation_cannot_replace_newer_selection(self):
        first = self._upload_single_observation(4)
        second = self._upload_single_observation(10)
        from api import read_certe_files as original_reader
        started_newer = False
        def read_with_newer_selection(*args, **kwargs):
            nonlocal started_newer
            if not started_newer:
                started_newer = True
                response = self._select_single_station(second)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            return original_reader(*args, **kwargs)
        with patch('api.read_certe_files', side_effect=read_with_newer_selection):
            response = self._select_single_station(first)
        self.assertEqual(response.status_code, 409)
        study = self.client.get(f'/api/sessions/{self.sid}/single-station-study').get_json()
        self.assertEqual(study['station_id'], second['station_id'])

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
        self.assertEqual(spatial_payload["sites"][0]["station_id"], observed_id)
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


        single = self._select_single_station(observation.get_json()['station'])
        self.assertEqual(single.status_code, 200, single.get_data(as_text=True))
        self.assertEqual(single.get_json()['active_data_source'], 'observed')
        blocked = self.client.post(f"/api/mcp/runs/{ids['run_id']}/publish", json={'session_id': self.sid})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.client.post('/api/mcp/runs', json={'session_id': self.sid}).status_code, 409)
        selected_again = self.client.put(f'/api/sessions/{self.sid}/mcp-study', json={
            'observed_station_id': observed_id, 'reference_station_ids': [reference_id],
            'poi': {'lat': 16.55, 'lon': -94.955}, 'radius_km': 100, 'target_height_m': 80})
        self.assertEqual(selected_again.status_code, 200)
        republished = self.client.post(f"/api/mcp/runs/{ids['run_id']}/publish", json={'session_id': self.sid})
        self.assertEqual(republished.status_code, 200, republished.get_data(as_text=True))
        self.assertEqual(republished.get_json()['source_id'], source_id)


if __name__ == "__main__":
    unittest.main()

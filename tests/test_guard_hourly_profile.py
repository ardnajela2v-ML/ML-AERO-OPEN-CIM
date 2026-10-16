"""Guard de hourly_profile_2018 en /api/multicriteria/spatial — §6 caso-estudio.

Prueba los casos del §6:
  1. 70,128 valores → _store crea pml_series + extrae 8760; spatial acepta (200)
  2. 8,760 valores  → guard no bloquea; price_hourly_usd_kwh tiene 8,760 floats
  3. [] (lista vacía) → tratado como sin perfil, precio escalar, sin 400
  4. Sin perfil (sesión sin PML) → precio escalar, sin 400
  5. pml_series presente → spatial acepta (200, guard levantado en paso 5)
  6. Migración en vuelo: sesión vieja con 70128 + hourly_timestamps → 200
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from api import create_app
from config import Config


class GuardHourlyProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_runs    = Config.MCP_RUNS_FOLDER
        self.old_uploads = Config.MCP_UPLOAD_FOLDER
        self.old_general = Config.UPLOAD_FOLDER
        Config.MCP_RUNS_FOLDER    = str(Path(self.temporary.name) / "runs")
        Config.MCP_UPLOAD_FOLDER  = str(Path(self.temporary.name) / "uploads")
        Config.UPLOAD_FOLDER      = str(Path(self.temporary.name) / "general_uploads")
        for d in [Config.MCP_RUNS_FOLDER, Config.MCP_UPLOAD_FOLDER, Config.UPLOAD_FOLDER]:
            Path(d).mkdir(parents=True)
        self.app    = create_app()
        self.client = self.app.test_client()
        self.sid    = self.client.post("/api/session/new", json={}).get_json()["session_id"]

    def tearDown(self):
        Config.MCP_RUNS_FOLDER = self.old_runs
        Config.MCP_UPLOAD_FOLDER = self.old_uploads
        Config.UPLOAD_FOLDER = self.old_general
        self.temporary.cleanup()

    _POLYGON = [
        {'lat': 16.5,  'lon': -95.0},
        {'lat': 16.5,  'lon': -94.99},
        {'lat': 16.51, 'lon': -95.0},
    ]

    def _spatial(self):
        return self.client.post('/api/multicriteria/spatial', json={
            'session_id': self.sid,
            'scope': 'park',
            'price_usd_kwh': 0.08,
            'project_years': 25,
            'polygon': self._POLYGON,
        })

    def _load_pml(self, preset):
        resp = self.client.post('/api/pml/load', json={
            'session_id': self.sid, 'preset': preset,
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def _poll_task(self, task_id, max_iters=300):
        for _ in range(max_iters):
            task = self.client.get(f'/api/kriging/status/{task_id}').get_json()
            if task['status'] in ('done', 'error'):
                return task
            time.sleep(0.01)
        return task

    # ── Test 1 ────────────────────────────────────────────────────────────────
    def test_70128_creates_pml_series_and_extracts_8760(self):
        """Carga multi-año (70,128 h) → _store crea pml_series + 8760 en
        hourly_profile_2018. Spatial acepta (200)."""
        self._load_pml('pml_2017_2024_02jud115')
        sess = self.app._get_session(self.sid)
        pml = sess['park_configuration']['market_models']['pml']
        self.assertIsNotNone(pml.get('pml_series'))
        self.assertEqual(pml['pml_series']['n_hours'], 70128)
        self.assertEqual(pml['pml_series']['schema_version'], 1)
        self.assertEqual(len(pml['hourly_profile_2018']), 8760)
        self.assertIsNone(pml.get('hourly_timestamps'))
        self.assertIn(2018, pml['pml_series']['years_complete'])

        resp = self._spatial()
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertIn('task_id', resp.get_json())

    # ── Test 2 ────────────────────────────────────────────────────────────────
    def test_8760_passes_and_price_list_has_8760_values(self):
        """hourly_profile_2018 con 8,760 valores → price_hourly_usd_kwh de 8,760 floats.

        Patch de analyze_spatial_topsis_sites y analyze_park_layouts para que el
        background task complete sin datos de viento reales.
        """
        self._load_pml('pml_ipo')
        captured = {}

        fake_spatial = {
            'sites': [{'v_mean': 6.0, 'rho_mean': 1.2}],
            'n_virtual_sites': 1,
            'spatial_sources': [],
        }

        def capture_price(**kwargs):
            captured['price_hourly_usd_kwh'] = kwargs.get('price_hourly_usd_kwh')
            return {'alternatives': [], 'ranking': {'ranking': []}}

        with patch('api.analyze_spatial_topsis_sites', return_value=fake_spatial), \
             patch('api.analyze_park_layouts', side_effect=capture_price):
            resp = self._spatial()

        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        task_id = resp.get_json()['task_id']
        task = self._poll_task(task_id)
        self.assertEqual(task['status'], 'done', task)

        price = captured.get('price_hourly_usd_kwh')
        self.assertIsInstance(price, list)
        self.assertEqual(len(price), 8760)

    # ── Test 3 ────────────────────────────────────────────────────────────────
    def test_empty_profile_treated_as_no_profile(self):
        """hourly_profile_2018 = [] → tratado como sin perfil (200, no 400)."""
        sess = self.app._get_session(self.sid)
        sess.setdefault('park_configuration', {}).setdefault(
            'market_models', {})['pml'] = {
            'category': 'pml',
            'hourly_profile_2018': [],
        }
        resp = self._spatial()
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertIn('task_id', resp.get_json())

    # ── Test 4 ────────────────────────────────────────────────────────────────
    def test_no_profile_scalar_no_error(self):
        """Sesión sin PML cargado → price_hourly_usd_kwh=None (precio escalar), sin 400."""
        resp = self._spatial()
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertIn('task_id', resp.get_json())

    # ── Test 5 ────────────────────────────────────────────────────────────────
    def test_pml_series_accepted(self):
        """pml_series presente → spatial acepta (200, guard levantado)."""
        sess = self.app._get_session(self.sid)
        sess.setdefault('park_configuration', {}).setdefault(
            'market_models', {})['pml'] = {
            'category': 'pml',
            'hourly_profile_2018': [0.05] * 8760,
            'pml_series': {
                'schema_version': 1,
                'start_utc': '2017-01-01T06:00:00+00:00',
                'n_hours': 70128,
                'values': [0.05] * 70128,
            },
        }
        resp = self._spatial()
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertIn('task_id', resp.get_json())

    # ── Test 6 ────────────────────────────────────────────────────────────────
    def test_migration_70128_without_pml_series(self):
        """Sesión vieja: 70128 en hourly_profile_2018 + hourly_timestamps,
        sin pml_series → migración en vuelo → spatial acepta (200)."""
        import numpy as np
        ts = [str(t) for t in
              np.arange('2017-01-01T06:00', '2025-01-01T06:00',
                        dtype='datetime64[h]')]
        self.assertEqual(len(ts), 70128)
        sess = self.app._get_session(self.sid)
        sess.setdefault('park_configuration', {}).setdefault(
            'market_models', {})['pml'] = {
            'category': 'pml',
            'hourly_profile_2018': [0.05] * 70128,
            'hourly_timestamps': ts,
        }
        resp = self._spatial()
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertIn('task_id', resp.get_json())

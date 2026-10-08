"""Tests para timestamps_utc en historical_wind y prepare_historical_wind — §7 paso 2.

Estructura:
  TestPrepareHistoricalWindTimestamps  (5 tests unitarios sobre core/)
  TestTsIntegrityComputation           (3 tests unitarios, replica lógica api.py)
  TimestampsUtcApiTests                (2 tests de integración via Flask test client)
"""
from __future__ import annotations

import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from core.historical_resource import prepare_historical_wind
from api import create_app
from config import Config


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_resource(n=10, start='2020-01-01', include_ts=True, nan_idx=None):
    index = pd.date_range(start, periods=n, freq='h', tz='UTC')
    ws = np.ones(n, dtype=float) * 6.0
    wd = np.ones(n, dtype=float) * 240.0
    if nan_idx is not None:
        ws[nan_idx] = np.nan
    hoy = ((np.minimum(index.dayofyear, 365) - 1) * 24 + index.hour).to_numpy(dtype=int) % 8760
    r = {'ws': ws, 'wd': wd, 'measurement_height_m': 80.0, 'dt_hours': 1.0, 'hour_of_year': hoy}
    if include_ts:
        r['timestamps_utc'] = index.values
    return r


# ─── 1. Unitarios: prepare_historical_wind ────────────────────────────────────

class TestPrepareHistoricalWindTimestamps(unittest.TestCase):

    def test_propagated_with_valid_mask(self):
        """timestamps_utc aplicado con la misma máscara valid que ws."""
        r = _make_resource(n=10, nan_idx=3)          # muestra 3 → NaN → excluida
        result = prepare_historical_wind(r, target_height_m=80.0)
        self.assertIn('timestamps_utc', result)
        self.assertEqual(len(result['timestamps_utc']), 9)
        hours = [pd.Timestamp(t).hour for t in result['timestamps_utc']]
        self.assertNotIn(3, hours)                   # hora 03:00 debe estar ausente

    def test_absent_when_not_in_resource(self):
        """Si resource no contiene timestamps_utc, el resultado tampoco."""
        r = _make_resource(n=10, include_ts=False)
        result = prepare_historical_wind(r, target_height_m=80.0)
        self.assertNotIn('timestamps_utc', result)

    def test_aligned_with_hour_of_year(self):
        """pd.Timestamp(ts).hour == hour_of_year % 24 para cada muestra."""
        r = _make_resource(n=24, start='2020-06-15')
        result = prepare_historical_wind(r, target_height_m=80.0)
        for ts, hoy in zip(result['timestamps_utc'], result['hour_of_year']):
            self.assertEqual(pd.Timestamp(ts).hour, hoy % 24)

    def test_leap_year_feb29_vs_hour_of_year(self):
        """
        2020-02-29 en UTC:
          dayofyear=60 → np.minimum(60,365)-1=59 → hour_of_year=59×24=1416.
          Slot 1416 corresponde a 2018-03-01 00:00 en el PML de referencia —
          desfase conocido: días post-feb-28 en año bisiesto se desplazan +1 día
          en el índice fijo de 8760. Documentado, no se corrige aquí.
          timestamps_utc debe conservar la fecha real 2020-02-29.
        """
        index = pd.DatetimeIndex([
            '2020-02-28 22:00+00:00',
            '2020-02-28 23:00+00:00',
            '2020-02-29 00:00+00:00',
            '2020-02-29 01:00+00:00',
            '2020-02-29 02:00+00:00',
        ])
        ws = np.array([6.0, 6.1, 6.2, 6.3, 6.4])
        wd = np.ones(5) * 240.0
        hoy = ((np.minimum(index.dayofyear, 365) - 1) * 24 + index.hour).to_numpy(dtype=int) % 8760
        r = {'ws': ws, 'wd': wd, 'measurement_height_m': 80.0, 'dt_hours': 1.0,
             'hour_of_year': hoy, 'timestamps_utc': index.values}
        result = prepare_historical_wind(r, target_height_m=80.0)

        # Feb-29 00:00 → hour_of_year = 1416 (slot 2018-03-01; desfase documentado)
        feb29_hoy_val = 59 * 24  # = 1416
        n_1416 = sum(1 for h in result['hour_of_year'] if h == feb29_hoy_val)
        self.assertEqual(n_1416, 1, 'Feb-29 00:00 debe producir exactamente hoy=1416')

        # timestamps_utc conserva la fecha real 2020-02-29
        feb29_ts = [t for t in result['timestamps_utc'] if pd.Timestamp(t).day == 29]
        self.assertEqual(len(feb29_ts), 3, 'Los tres registros del 29-feb deben aparecer')
        for ts in feb29_ts:
            self.assertEqual(pd.Timestamp(ts).year, 2020)
            self.assertEqual(pd.Timestamp(ts).month, 2)
            self.assertEqual(pd.Timestamp(ts).day, 29)

    def test_synthetic_44year_series(self):
        """Serie sintética horaria 1980-01-01 a 2024-08-31 23:00 UTC — 391,560 filas."""
        index = pd.date_range('1980-01-01', '2024-08-31 23:00', freq='h', tz='UTC')
        n = len(index)
        self.assertEqual(n, 391_560, f'Se esperan 391,560 filas; hay {n}')
        ws = np.full(n, 6.5)
        wd = np.full(n, 240.0)
        hoy = ((np.minimum(index.dayofyear, 365) - 1) * 24 + index.hour).to_numpy(dtype=int) % 8760
        r = {'ws': ws, 'wd': wd, 'measurement_height_m': 80.0, 'dt_hours': 1.0,
             'hour_of_year': hoy, 'timestamps_utc': index.values}
        result = prepare_historical_wind(r, target_height_m=80.0)
        self.assertEqual(len(result['timestamps_utc']), n)   # todos válidos, sin NaN
        first = pd.Timestamp(result['timestamps_utc'][0])
        self.assertEqual(first.year, 1980)
        self.assertEqual(first.month, 1)
        self.assertEqual(first.day, 1)
        last = pd.Timestamp(result['timestamps_utc'][-1])
        self.assertEqual(last.year, 2024)
        self.assertEqual(last.month, 8)
        self.assertEqual(last.day, 31)
        self.assertEqual(last.hour, 23)


# ─── 2. Unitarios: lógica de integridad (réplica de api.py) ──────────────────

def _compute_ts_integrity(index, dt_hours=1.0):
    """Réplica exacta de la lógica de integridad que api.py guarda en _ts_integrity."""
    ts = index.values if hasattr(index, 'values') else np.asarray(index)
    if len(ts) <= 1:
        return None
    diffs_ns = np.diff(ts.astype(np.int64))
    expected_ns = int(dt_hours * 3600 * 1e9)
    n_dup = int(np.sum(diffs_ns <= 0))
    n_gaps = int(np.sum(diffs_ns > int(expected_ns * 1.5)))
    max_gap_h = float(np.max(diffs_ns) / 3600e9)
    return {
        'is_strictly_increasing': n_dup == 0,
        'n_duplicates': n_dup,
        'n_gaps': n_gaps,
        'max_gap_hours': max_gap_h,
    }


class TestTsIntegrityComputation(unittest.TestCase):

    def test_clean_series_passes(self):
        """Serie horaria limpia → is_strictly_increasing, cero duplicados, cero huecos."""
        index = pd.date_range('2020-01-01', periods=72, freq='h', tz='UTC')
        ig = _compute_ts_integrity(index)
        self.assertTrue(ig['is_strictly_increasing'])
        self.assertEqual(ig['n_duplicates'], 0)
        self.assertEqual(ig['n_gaps'], 0)
        self.assertAlmostEqual(ig['max_gap_hours'], 1.0, places=6)

    def test_duplicate_timestamp_detected(self):
        """Timestamp duplicado intencional → n_duplicates==1, is_strictly_increasing==False."""
        base = pd.date_range('2020-01-01', periods=10, freq='h', tz='UTC')
        # Insertar duplicado de la hora 3 entre posiciones 3 y 4
        ts_list = list(base[:4]) + [base[3]] + list(base[4:])
        dup_index = pd.DatetimeIndex(ts_list)
        ig = _compute_ts_integrity(dup_index)
        self.assertFalse(ig['is_strictly_increasing'])
        self.assertEqual(ig['n_duplicates'], 1)

    def test_gap_detected(self):
        """Hueco de 3 h entre dos bloques → n_gaps==1, max_gap_hours≈3."""
        t1 = pd.date_range('2020-01-01 00:00', periods=5, freq='h', tz='UTC')
        t2 = pd.date_range('2020-01-01 07:00', periods=5, freq='h', tz='UTC')  # gap de 3h tras t1[-1]=04:00
        index = t1.append(t2)
        ig = _compute_ts_integrity(index)
        self.assertTrue(ig['is_strictly_increasing'])
        self.assertEqual(ig['n_gaps'], 1)
        self.assertAlmostEqual(ig['max_gap_hours'], 3.0, places=3)


# ─── 3. Integración: API Flask ────────────────────────────────────────────────

class TimestampsUtcApiTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_runs    = Config.MCP_RUNS_FOLDER
        self.old_uploads = Config.MCP_UPLOAD_FOLDER
        self.old_general = Config.UPLOAD_FOLDER
        Config.MCP_RUNS_FOLDER    = str(Path(self.temporary.name) / 'runs')
        Config.MCP_UPLOAD_FOLDER  = str(Path(self.temporary.name) / 'uploads')
        Config.UPLOAD_FOLDER      = str(Path(self.temporary.name) / 'general_uploads')
        for d in [Config.MCP_RUNS_FOLDER, Config.MCP_UPLOAD_FOLDER, Config.UPLOAD_FOLDER]:
            Path(d).mkdir(parents=True)
        self.app    = create_app()
        self.client = self.app.test_client()
        self.sid    = self.client.post('/api/session/new', json={}).get_json()['session_id']

    def tearDown(self):
        Config.MCP_RUNS_FOLDER = self.old_runs
        Config.MCP_UPLOAD_FOLDER = self.old_uploads
        Config.UPLOAD_FOLDER = self.old_general
        self.temporary.cleanup()

    def _upload_wind(self, start='2020-01-01', n=72, lat=16.5):
        index = pd.date_range(start, periods=n, freq='h')
        frame = pd.DataFrame({
            'timestamp': index, 'wind_speed': 6.0, 'wind_direction': 240.0,
            'pressure': 995.0, 'temperature': 25.0,
        })
        resp = self.client.post('/api/mcp/uploads', data={
            'session_id': self.sid, 'data_kind': 'observed', 'provider': 'USER',
            'latitude': str(lat), 'longitude': '-95', 'measurement_height_m': '80',
            'timezone': 'UTC',
            'file': (io.BytesIO(frame.to_csv(index=False).encode()), 'wind.csv'),
        }, content_type='multipart/form-data')
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        return resp.get_json()['station']

    def _select(self, station):
        body = {'station_id': station['station_id'],
                'poi': {'lat': station['latitude'], 'lon': station['longitude']},
                'radius_km': 200, 'target_height_m': 80}
        resp = self.client.put(f'/api/sessions/{self.sid}/single-station-study', json=body)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def _run_and_capture(self):
        captured = {}
        fake_spatial = {'sites': [{'v_mean': 6.0, 'rho_mean': 1.2}],
                        'n_virtual_sites': 1, 'spatial_sources': []}
        def capture(**kwargs):
            captured['hw'] = kwargs.get('historical_wind')
            return {'alternatives': [], 'ranking': {'ranking': []}}
        with patch('api.analyze_spatial_topsis_sites', return_value=fake_spatial), \
             patch('api.analyze_park_layouts', side_effect=capture):
            resp = self.client.post('/api/multicriteria/spatial', json={
                'session_id': self.sid, 'scope': 'park',
                'price_usd_kwh': 0.08, 'project_years': 25,
                'polygon': [{'lat': 16.5,  'lon': -95.0},
                            {'lat': 16.5,  'lon': -94.99},
                            {'lat': 16.51, 'lon': -95.0}],
            })
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            task_id = resp.get_json()['task_id']
            for _ in range(300):
                task = self.client.get(f'/api/kriging/status/{task_id}').get_json()
                if task['status'] in ('done', 'error'):
                    break
                time.sleep(0.01)
        self.assertEqual(task['status'], 'done', task)
        return captured.get('hw')

    def test_timestamps_utc_present_and_correct(self):
        """historical_wind tiene timestamps_utc alineado con upload de 2020-01-01."""
        station = self._upload_wind(start='2020-01-01', n=72)
        self._select(station)
        hw = self._run_and_capture()
        self.assertIsNotNone(hw)
        self.assertIn('timestamps_utc', hw)
        self.assertEqual(len(hw['timestamps_utc']), 72)
        first = pd.Timestamp(hw['timestamps_utc'][0])
        self.assertEqual(first.year, 2020)
        self.assertEqual(first.month, 1)
        self.assertEqual(first.day, 1)
        self.assertEqual(first.hour, 0)
        self.assertIn('_ts_integrity', hw)
        self.assertTrue(hw['_ts_integrity']['is_strictly_increasing'])
        self.assertEqual(hw['_ts_integrity']['n_duplicates'], 0)

    def test_year_2019_vs_2022_distinguishable(self):
        """timestamps_utc distingue 2019 de 2022 aunque hour_of_year[0]==0 en ambos."""
        station = self._upload_wind(start='2019-01-01', n=72)
        self._select(station)
        hw19 = self._run_and_capture()
        self.assertIsNotNone(hw19)
        self.assertEqual(pd.Timestamp(hw19['timestamps_utc'][0]).year, 2019)
        self.assertEqual(int(hw19['hour_of_year'][0]), 0)

        # Sesión nueva para 2022
        self.sid = self.client.post('/api/session/new', json={}).get_json()['session_id']
        station = self._upload_wind(start='2022-01-01', n=72)
        self._select(station)
        hw22 = self._run_and_capture()
        self.assertIsNotNone(hw22)
        self.assertEqual(pd.Timestamp(hw22['timestamps_utc'][0]).year, 2022)
        self.assertEqual(int(hw22['hour_of_year'][0]), 0)   # mismo hoy[0]=0, año distinto

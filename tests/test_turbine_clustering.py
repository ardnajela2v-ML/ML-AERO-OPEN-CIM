import io
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from api import create_app
from config import Config
from core.energy import list_turbine_info


def _wind_csv(n=200, calm=False):
    rows = ['timestamp,wind_speed,wind_direction']
    for i in range(n):
        speed = 0.1 if calm else max(9 + 7 * math.sin(i / 12.0), 0.2)
        rows.append(f'2020-01-{1 + i // 24:02d} {i % 24:02d}:00:00,{speed:.3f},180')
    return '\n'.join(rows).encode()


class TurbineClusteringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_uploads = Config.UPLOAD_FOLDER
        Config.UPLOAD_FOLDER = str(Path(self.temporary.name) / 'uploads')
        Path(Config.UPLOAD_FOLDER).mkdir(parents=True)
        self.app = create_app()
        self.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        self.client = self.app.test_client()
        self.sid = self.client.post('/api/session/new', json={}).get_json()['session_id']
        self.catalog = [t['filename'] for t in list_turbine_info(Config.TURBINES_FOLDER)]

    def tearDown(self):
        Config.UPLOAD_FOLDER = self.old_uploads
        self.temporary.cleanup()

    def _upload(self, sid, csv_bytes):
        uploaded = self.client.post('/api/data/upload', data={
            'session_id': sid, 'source': 'auto',
            'file': (io.BytesIO(csv_bytes), 'wind.csv'),
        }, content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code, 200, uploaded.get_data(as_text=True))

    def test_default_clustering_covers_full_catalog_with_valid_matrices(self):
        self._upload(self.sid, _wind_csv())
        response = self.client.post('/api/mcda/turbine_clustering', json={'session_id': self.sid})
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()

        self.assertEqual(data['labels'], self.catalog)
        self.assertEqual(len(data['turbines']), len(self.catalog))
        self.assertEqual(data['linkage_method'], 'complete')
        self.assertEqual(data['data_source']['rows'], 200)

        n = len(self.catalog)
        corr = np.array(data['correlation_matrix'])
        dist = np.array(data['distance_matrix'])
        self.assertEqual(corr.shape, (n, n))
        self.assertTrue(np.allclose(np.diag(corr), 1.0))
        self.assertTrue(np.allclose(corr, corr.T))
        self.assertTrue(np.allclose(np.diag(dist), 0.0))
        self.assertTrue(np.allclose(dist, dist.T))
        self.assertTrue(np.all(dist >= -1e-9))

        # scipy linkage output: n-1 merges, 4 columns (child a, child b, distance, count).
        linkage = np.array(data['linkage'])
        self.assertEqual(linkage.shape, (n - 1, 4))

        for row, filename in zip(data['turbines'], self.catalog):
            self.assertEqual(row['filename'], filename)
            for key in ('name', 'rated_power_kw', 'rotor_diameter_m',
                        'hub_height_m', 'mean_power_kw', 'aep_mwh', 'capacity_factor_pct'):
                self.assertIn(key, row)

    def test_turbine_filenames_filters_and_preserves_catalog_order(self):
        self._upload(self.sid, _wind_csv())
        picked = [self.catalog[2], self.catalog[0]]  # deliberately out of catalog order
        response = self.client.post('/api/mcda/turbine_clustering', json={
            'session_id': self.sid, 'turbine_filenames': picked})
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(data['labels'], [f for f in self.catalog if f in picked])
        self.assertEqual(len(data['linkage']), 1)

    def test_linkage_method_is_honored_for_every_supported_value(self):
        self._upload(self.sid, _wind_csv())
        for method in ('single', 'complete', 'average', 'weighted'):
            with self.subTest(method=method):
                response = self.client.post('/api/mcda/turbine_clustering', json={
                    'session_id': self.sid, 'linkage_method': method})
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()['linkage_method'], method)

    def test_unsupported_linkage_method_rejected(self):
        self._upload(self.sid, _wind_csv())
        response = self.client.post('/api/mcda/turbine_clustering', json={
            'session_id': self.sid, 'linkage_method': 'ward'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {
            'success': False, 'error': 'Método de agrupamiento no soportado'})

    def test_fewer_than_two_matching_turbines_rejected(self):
        self._upload(self.sid, _wind_csv())
        response = self.client.post('/api/mcda/turbine_clustering', json={
            'session_id': self.sid, 'turbine_filenames': [self.catalog[0]]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {
            'success': False,
            'error': 'Se necesitan al menos 2 turbinas del catálogo para agrupar.'})

    def test_unknown_turbine_filenames_are_silently_excluded_not_errored(self):
        self._upload(self.sid, _wind_csv())
        response = self.client.post('/api/mcda/turbine_clustering', json={
            'session_id': self.sid,
            'turbine_filenames': [self.catalog[0], self.catalog[1], 'does-not-exist.csv']})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['labels'],
                          [self.catalog[0], self.catalog[1]])

    def test_session_without_data_returns_400(self):
        response = self.client.post('/api/mcda/turbine_clustering', json={'session_id': self.sid})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {'error': 'No data loaded.'})

    def test_missing_session_id_returns_400(self):
        response = self.client.post('/api/mcda/turbine_clustering', json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {'error': 'session_id requerido'})

    def test_constant_power_series_rejected_instead_of_nan_correlation(self):
        self._upload(self.sid, _wind_csv(n=50, calm=True))
        response = self.client.post('/api/mcda/turbine_clustering', json={'session_id': self.sid})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {
            'success': False,
            'error': 'Las series de potencia son constantes; no se puede calcular correlación.'})


if __name__ == '__main__':
    unittest.main()

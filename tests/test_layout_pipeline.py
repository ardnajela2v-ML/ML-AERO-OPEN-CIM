"""Regression coverage for generation -> review -> independent TOPSIS."""
import copy
import time
import unittest
from unittest.mock import patch

from api import create_app
from core.farm_mcda import generate_park_layouts
from core.multicriteria import rank_layout_alternatives


class LayoutPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.polygon = [
            {'lat': 16.5, 'lon': -95}, {'lat': 16.5, 'lon': -94.9973},
            {'lat': 16.5027, 'lon': -94.9973}, {'lat': 16.5027, 'lon': -95}]
        cls.sites = [{'v_mean': 8.5, 'weibull_K': 2.1, 'rho_mean': 1.18,
                      'sectorial': {'frequency_pct': [6.25] * 16}}]
        with patch('core.farm_mcda.rank_layout_alternatives', side_effect=AssertionError('Generation ran TOPSIS')):
            cls.prepared = generate_park_layouts(
                sites=cls.sites, polygon=cls.polygon, turbines_folder='aerogeneradores',
                turbine_filenames=['gamesa21_p2000_r45.csv', 'acciona15_p1500_r35.csv'],
                n_range=(1, 1), project_years=20, price_usd_kwh=.083)
        cls.app = create_app()
        cls.client = cls.app.test_client()

    def test_generation_returns_evaluated_geometry_without_ranking(self):
        self.assertEqual(self.prepared['analysis_kind'], 'farm_layout_candidates_v1')
        self.assertNotIn('ranking', self.prepared)
        self.assertEqual(len(self.prepared['alternatives']), 2, self.prepared['errors'])
        for row in self.prepared['alternatives']:
            self.assertTrue(row['layout_latlon'])
            self.assertIn('van_usd', row)
            self.assertIn('environmental_feasibility', row)
            self.assertNotIn('topsis_score', row)

    def test_ranking_reuses_geometry_and_energy_without_generation(self):
        before = copy.deepcopy(self.prepared['alternatives'])
        with patch('api.generate_park_layouts', side_effect=AssertionError('Ranking generated layouts')):
            response = self.client.post('/api/multicriteria/rank', json={
                'mode': 'layout_alternatives', 'alternatives': before})
        self.assertEqual(response.status_code, 200)
        ranked = response.get_json()['ranking']
        self.assertEqual(len(ranked), 2)
        by_model = {row['turbina']: row for row in before}
        for row in ranked:
            for key in ['layout_latlon', 'layout_xy_m', 'aep_p50_mwh', 'van_usd', 'n_aerogeneradores']:
                self.assertEqual(row[key], by_model[row['turbina']][key])
        rank_layout_alternatives(before)
        self.assertEqual(before, self.prepared['alternatives'])

    def test_generation_endpoint_ignores_weights_and_does_not_rank(self):
        spatial = {'sites': self.sites, 'n_virtual_sites': 1, 'spatial_sources': []}
        with patch('api.analyze_spatial_topsis_sites', return_value=spatial) as sample, patch(
                'api.generate_park_layouts', return_value=copy.deepcopy(self.prepared)) as generate, patch(
                'api.analyze_park_layouts', side_effect=AssertionError('Combined legacy pipeline used')), patch(
                'api.rank_sites_turbines', side_effect=AssertionError('Site ranking used')):
            response = self.client.post('/api/farm/layouts/generate', json={
                'scope': 'park', 'polygon': self.polygon, 'price_usd_kwh': .083, 'project_years': 20,
                'turbine_filenames': ['gamesa21_p2000_r45.csv'],
                'max_slope_deg': 0, 'electrical_losses': 0,
                'ahp_assessment': {'invalid': True}, 'layout_weights': {'invalid': 1}})
            self.assertEqual(response.status_code, 200, response.get_json())
            tid = response.get_json()['task_id']
            for _ in range(200):
                task = self.client.get('/api/kriging/status/' + tid).get_json()
                if task['status'] in ('done', 'error'):
                    break
                time.sleep(.01)
            self.assertEqual(task['status'], 'done', task)
            self.assertIsNone(task['result'].get('ranking'))
            self.assertNotIn('weights', generate.call_args.kwargs)
            self.assertEqual(generate.call_args.kwargs['max_slope_deg'], 0)
            self.assertEqual(generate.call_args.kwargs['electrical_losses'], 0)
            self.assertTrue(sample.call_args.kwargs['resource_only'])

    def test_generation_rejects_exploratory_scope(self):
        response = self.client.post('/api/farm/layouts/generate', json={'scope': 'exploratory'})
        self.assertEqual(response.status_code, 400)

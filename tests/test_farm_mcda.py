import math
import unittest

import numpy as np

from core.energy import PowerCurve
from core.farm import (
    _build_structured_grid,
    gaussian_deficit,
    optimize_layout_structured,
    simulate_wind_farm,
)
from core.farm_mcda import analyze_park_layouts
from core.multicriteria import LAYOUT_WEIGHTS, rank_layout_alternatives


class FarmScientificModelTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')
        self.polygon = [
            {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
            {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
        ]

    def test_gaussian_wake_is_directional(self):
        self.assertGreater(gaussian_deficit(500, 0, 100), 0)
        self.assertLess(gaussian_deficit(500, 500, 100), 1e-3)
        ws = np.full(876, 8.0)
        wd = np.full(876, 270.0)
        aligned = simulate_wind_farm(
            [[0, 0], [600, 0]], self.turbine, ws, wd,
            wake_model='gaussian', k_wake=0.04,
        )
        crosswind = simulate_wind_farm(
            [[0, 0], [0, 600]], self.turbine, ws, wd,
            wake_model='gaussian', k_wake=0.04,
        )
        self.assertGreater(aligned['wake_loss_pct'], crosswind['wake_loss_pct'])
        self.assertLessEqual(aligned['aep_mwh'], aligned['aep_gross_mwh'])

    def test_layout_respects_exclusion_and_rotor_setback(self):
        exclusion = [[
            {'lat': 16.503, 'lon': -94.997}, {'lat': 16.503, 'lon': -94.993},
            {'lat': 16.507, 'lon': -94.993}, {'lat': 16.507, 'lon': -94.997},
        ]]
        result = optimize_layout_structured(
            self.polygon, self.turbine, 0,
            ws=np.full(200, 8.0), wd=np.full(200, 270.0),
            exclusion_polygons=exclusion, boundary_setback_m=45,
            wake_model='gaussian', k_wake=0.04,
        )
        self.assertGreater(result['max_n_fit'], 0)
        self.assertEqual(result['exclusion_count'], 1)
        self.assertGreaterEqual(result['boundary_setback_m'], 45)
        for lat, lon in result['layout_latlon']:
            self.assertFalse(16.503 < lat < 16.507 and -94.997 < lon < -94.993)

    def test_capacity_search_moves_grid_origin_to_avoid_undercount(self):
        # In this 1 km square, a boundary-anchored 5D x 3D grid fits only
        # three valid centres after a 0.5D setback. A half-cell translation
        # recovers a second complete row, proving that the phase is material.
        args = dict(
            poly_xs=[0.0, 1000.0, 1000.0, 0.0],
            poly_ys=[0.0, 0.0, 1000.0, 1000.0],
            lat0=0.0, lon0=0.0, D=100.0, dominant_dir_deg=0.0,
            s_para_d=5.0, s_perp_d=3.0, stagger=0.0,
            wind_grid=None, fallback_wind_ms=8.0,
            boundary_setback_m=50.0,
        )
        fixed = _build_structured_grid(**args)
        shifted = _build_structured_grid(
            **args, along_offset_fraction=0.5, cross_offset_fraction=0.5)
        self.assertEqual(len(fixed), 3)
        self.assertEqual(len(shifted), 6)

    def test_capacity_layout_uses_exact_discovered_nmax(self):
        result = optimize_layout_structured(
            self.polygon, self.turbine, 0,
            ws=np.full(200, 8.0), wd=np.full(200, 270.0),
            boundary_setback_m=45,
            orientation_step_deg=30,
            grid_offset_fractions=(0.0, 0.5),
        )
        self.assertEqual(result['n_placed'], result['max_n_fit'])
        self.assertGreater(result['layout_config']['n_configs_tried'], 0)
        self.assertIn('orientation_deg', result['capacity_config'])


class LayoutTopsisTests(unittest.TestCase):
    @staticmethod
    def alternative(label, scale):
        directions = {
            'lcoe_usd_kwh', 'capex_usd', 'opex_lifetime_usd', 'wake_loss_pct',
            'aep_uncertainty_pct', 'curtailment_loss_pct', 'collection_length_m',
            'civil_complexity_index',
        }
        row = {'label': label, 'feasible': True}
        for key in LAYOUT_WEIGHTS:
            row[key] = 1.0 / scale if key in directions else scale
        return row

    def test_layout_topsis_excludes_missing_without_turning_it_into_zero(self):
        complete_a = self.alternative('a', 1.0)
        complete_b = self.alternative('b', 2.0)
        incomplete = self.alternative('missing', 3.0)
        incomplete['van_usd'] = None
        result = rank_layout_alternatives([complete_a, complete_b, incomplete])
        self.assertEqual(result['n'], 2)
        self.assertEqual(result['ranking'][0]['label'], 'b')
        self.assertEqual(result['excluded'][0]['label'], 'missing')
        self.assertGreater(result['sensitivity_scenarios'], 1)
        self.assertIn('rank_sensitivity_min', result['ranking'][0])

    def test_layout_topsis_rejects_partial_capacity_and_duplicate_models(self):
        full = self.alternative('full', 2.0)
        full.update(turbina='modelo_a.csv', n_aerogeneradores=10, max_n_fisico=10)
        partial = self.alternative('partial', 3.0)
        partial.update(turbina='modelo_a.csv', n_aerogeneradores=9, max_n_fisico=10)
        duplicate = self.alternative('duplicate', 4.0)
        duplicate.update(turbina='modelo_a.csv', n_aerogeneradores=10, max_n_fisico=10)
        result = rank_layout_alternatives([partial, full, duplicate])
        self.assertEqual(result['n'], 1)
        self.assertEqual(result['ranking'][0]['label'], 'full')
        self.assertEqual(
            {row['reason'] for row in result['excluded']},
            {'layout_no_usa_nmax', 'modelo_duplicado'},
        )

    def test_one_nmax_alternative_and_ranking_row_per_valid_model(self):
        sites = [{
            'v_mean': 8.5, 'weibull_K': 2.1, 'rho_mean': 1.18,
            'sectorial': {'frequency_pct': [2, 2, 3, 4, 6, 8, 12, 18,
                                              16, 10, 6, 4, 3, 2, 2, 2]},
        }]
        result = analyze_park_layouts(
            sites, [
                {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
                {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
            ], 'aerogeneradores', [
                'gamesa21_p2000_r45.csv',
                'acciona16_p1500_r38.5.csv',
                'gamesa21_p2000_r45.csv',  # duplicate input must collapse
            ],
            demand_mwh=0, commercialization_mode='total_sale',
            s_para_range=(5, 7), s_perp_range=(3, 4), project_years=20,
        )
        self.assertEqual(result['n_layout_alternatives'], 2)
        self.assertEqual(result['n_requested_models'], 2)
        self.assertEqual(result['n_valid_models'], 2)
        self.assertEqual(len(result['alternatives']), 2)
        self.assertEqual(len({row['turbina'] for row in result['alternatives']}), 2)
        self.assertTrue(all(
            row['n_aerogeneradores'] == row['max_n_fisico']
            for row in result['alternatives']
        ))
        self.assertTrue(all(
            len(row['layout_latlon']) == row['max_n_fisico']
            for row in result['alternatives']
        ))
        self.assertEqual(result['ranking']['n'], 2)
        self.assertEqual(len(result['ranking']['ranking']), 2)
        self.assertEqual(
            {row['turbina'] for row in result['ranking']['ranking']},
            {row['turbina'] for row in result['alternatives']},
        )
        winner = result['ranking']['ranking'][0]
        self.assertEqual(winner['n_aerogeneradores'], winner['max_n_fisico'])
        self.assertGreater(winner['aep_mwh'], 0)
        self.assertGreater(winner['capex_usd'], 0)
        self.assertTrue(math.isfinite(winner['lcoe_usd_kwh']))


if __name__ == '__main__':
    unittest.main()

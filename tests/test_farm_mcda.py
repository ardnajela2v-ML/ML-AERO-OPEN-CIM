import math
import unittest

import numpy as np

from core.energy import PowerCurve
from core.farm import gaussian_deficit, optimize_layout_structured, simulate_wind_farm
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

    def test_total_sale_with_zero_demand_still_builds_a_farm(self):
        sites = [{
            'v_mean': 8.5, 'weibull_K': 2.1, 'rho_mean': 1.18,
            'sectorial': {'frequency_pct': [2, 2, 3, 4, 6, 8, 12, 18,
                                              16, 10, 6, 4, 3, 2, 2, 2]},
        }]
        result = analyze_park_layouts(
            sites, [
                {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
                {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
            ], 'aerogeneradores', ['gamesa21_p2000_r45.csv'],
            demand_mwh=0, commercialization_mode='total_sale',
            s_para_range=(5, 7), s_perp_range=(3, 4), project_years=20,
        )
        self.assertGreater(result['n_layout_alternatives'], 0)
        winner = result['ranking']['ranking'][0]
        self.assertGreater(winner['n_aerogeneradores'], 0)
        self.assertGreater(winner['aep_mwh'], 0)
        self.assertGreater(winner['capex_usd'], 0)
        self.assertTrue(math.isfinite(winner['lcoe_usd_kwh']))


if __name__ == '__main__':
    unittest.main()

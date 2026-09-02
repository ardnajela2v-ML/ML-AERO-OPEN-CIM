import math
import unittest

import numpy as np

from core.energy import PowerCurve, TechnoEconomicAnalyzer
from core.cost_model import CostModel, get_design_cost_benchmark
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

    def test_technoeconomic_uses_user_project_life_and_price(self):
        result = TechnoEconomicAnalyzer().analyze(
            self.turbine, n_turbines=1, annual_energy_kwh=4_000_000,
            energy_price_usd_kwh=0.083, project_years=31,
        )
        self.assertEqual(result['project_years'], 31)
        self.assertEqual(result['energy_price_usd_kwh'], 0.083)
        self.assertEqual(len(result['cashflow_table']), 31)

    def test_technoeconomic_rejects_missing_project_economics(self):
        with self.assertRaisesRegex(ValueError, 'configuración del proyecto'):
            TechnoEconomicAnalyzer().analyze(
                self.turbine, n_turbines=1, annual_energy_kwh=4_000_000,
            )

    def test_design_benchmark_is_separate_from_technoeconomic_default(self):
        techno = CostModel.from_defaults()
        design = CostModel.from_design_benchmark()
        benchmark = get_design_cost_benchmark()

        self.assertEqual(techno.turbine_usd_per_kw, 1800.0)
        self.assertEqual(design.turbine_usd_per_kw, 780.75)
        self.assertEqual(design.turbines_pct, 0.75)
        self.assertAlmostEqual(design.total_pct_check(), 1.0, places=12)
        installed = design.capex_breakdown(rated_kw=1000, n_turbines=1)
        self.assertEqual(installed['total_capex_usd'], 1_041_000)
        self.assertEqual(benchmark['total_installed_usd_per_kw'], 1041.0)


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
            {'layout_no_usa_cantidad_optima', 'modelo_duplicado'},
        )

    def test_wake_validated_lower_count_is_a_rankable_final_alternative(self):
        constrained = self.alternative('wake-optimal', 2.0)
        constrained.update(
            turbina='modelo_a.csv', n_aerogeneradores=8,
            n_optimo_estela=8, max_n_fisico=10,
            wake_loss_pct=4.9, max_wake_loss_pct=5.0,
        )
        result = rank_layout_alternatives([constrained])
        self.assertEqual(result['n'], 1)
        self.assertEqual(result['ranking'][0]['n_aerogeneradores'], 8)

    def test_unistmo_is_separate_and_explicitly_prefers_gamesa20_d80(self):
        gamesa = self.alternative('gamesa', 1.0)
        gamesa.update(
            turbina='gamesa20_p2000_r40.csv', n_aerogeneradores=10,
            max_n_fisico=10, unistmo_technology_preference_score=1.0,
        )
        competitor = self.alternative('competitor', 2.0)
        competitor.update(
            turbina='competitor_p2000_r40.csv', n_aerogeneradores=10,
            max_n_fisico=10, unistmo_technology_preference_score=0.0,
        )
        base = rank_layout_alternatives([gamesa, competitor])
        unistmo = rank_layout_alternatives(
            [gamesa, competitor], profile='unistmo_v1')
        self.assertEqual(base['ranking'][0]['turbina'], 'competitor_p2000_r40.csv')
        self.assertEqual(unistmo['ranking'][0]['turbina'], 'gamesa20_p2000_r40.csv')
        self.assertEqual(base['profile'], 'balanced_integral_v1')
        self.assertEqual(unistmo['profile'], 'unistmo_v1')
        self.assertNotIn('unistmo_technology_preference_score', base['criteria'])
        self.assertIn('unistmo_technology_preference_score', unistmo['criteria'])

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
            s_para_range=(5, 7), s_perp_range=(3, 4),
            project_years=20, price_usd_kwh=0.083,
        )
        self.assertEqual(result['n_layout_alternatives'], 2)
        self.assertEqual(result['n_requested_models'], 2)
        self.assertEqual(result['n_valid_models'], 2)
        self.assertEqual(len(result['alternatives']), 2)
        self.assertEqual(len({row['turbina'] for row in result['alternatives']}), 2)
        self.assertTrue(all(
            row['n_aerogeneradores'] == row['n_optimo_estela']
            and row['n_aerogeneradores'] <= row['max_n_fisico']
            for row in result['alternatives']
        ))
        self.assertTrue(all(
            len(row['layout_latlon']) == row['n_aerogeneradores']
            for row in result['alternatives']
        ))
        self.assertTrue(all(
            row['wake_loss_pct'] <= row['max_wake_loss_pct']
            for row in result['alternatives']
        ))
        self.assertEqual(result['ranking']['n'], 2)
        self.assertEqual(result['ranking_scenarios']['base']['n'], 2)
        self.assertEqual(result['ranking_scenarios']['unistmo']['n'], 2)
        self.assertEqual(len(result['ranking']['ranking']), 2)
        self.assertEqual(
            {row['turbina'] for row in result['ranking']['ranking']},
            {row['turbina'] for row in result['alternatives']},
        )
        winner = result['ranking']['ranking'][0]
        self.assertEqual(winner['n_aerogeneradores'], winner['n_optimo_estela'])
        self.assertGreater(winner['aep_mwh'], 0)
        self.assertGreater(winner['capex_usd'], 0)
        self.assertTrue(math.isfinite(winner['lcoe_usd_kwh']))
        installed_kw = (
            winner['turbine_specification']['rated_power_kw']
            * winner['n_aerogeneradores']
        )
        expected_base_capex = (
            installed_kw * winner['economic_assumptions']['turbine_usd_per_kw']
            / winner['economic_assumptions']['turbines_pct']
        )
        self.assertAlmostEqual(winner['base_capex_usd'], expected_base_capex, delta=1.0)
        self.assertAlmostEqual(
            winner['opex_usd_anual'],
            winner['capex_usd'] * winner['economic_assumptions']['opex_pct_capex'],
            delta=1e-6,
        )
        self.assertEqual(len(winner['cashflow_table']), 20)
        self.assertIn('base_capex', winner['economic_formulas'])
        self.assertIn('aep_gross_theoretical_mwh', winner['energy_balance'])

    def test_zero_wake_limit_reduces_and_reoptimises_until_compliant(self):
        sites = [{
            'v_mean': 8.5, 'weibull_K': 2.1, 'rho_mean': 1.18,
            'sectorial': {'frequency_pct': [6.25] * 16},
        }]
        result = analyze_park_layouts(
            sites, [
                {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
                {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
            ], 'aerogeneradores', ['gamesa21_p2000_r45.csv'],
            min_spacing_d=3, s_para_range=(3, 4, 5),
            s_perp_range=(3, 4), max_wake_loss_pct=0.0,
            project_years=31, price_usd_kwh=0.083,
        )
        row = result['alternatives'][0]
        self.assertGreater(row['max_n_fisico'], 1)
        self.assertLess(row['n_aerogeneradores'], row['max_n_fisico'])
        self.assertEqual(row['wake_loss_pct'], 0.0)
        self.assertTrue(row['reducido_por_estela'])
        self.assertEqual(row['wake_trials'][-1]['n'], row['n_aerogeneradores'])
        self.assertTrue(any(trial['wake_loss_pct'] > 0 for trial in row['wake_trials'][:-1]))
        self.assertEqual(row['economic_assumptions']['project_years'], 31)
        self.assertEqual(row['economic_assumptions']['price_usd_kwh'], 0.083)
        self.assertEqual(len(row['cashflow_table']), 31)

    def test_park_topsis_rejects_spacing_below_three_diameters(self):
        sites = [{
            'v_mean': 8.5, 'weibull_K': 2.1, 'rho_mean': 1.18,
            'sectorial': {'frequency_pct': [6.25] * 16},
        }]
        with self.assertRaisesRegex(ValueError, 'menor que 3D'):
            analyze_park_layouts(
                sites, [
                    {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
                    {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
                ], 'aerogeneradores',
                ['gamesa21_p2000_r45.csv'], min_spacing_d=2.5,
                project_years=25, price_usd_kwh=0.07,
            )


if __name__ == '__main__':
    unittest.main()

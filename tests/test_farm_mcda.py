import math
import unittest

import numpy as np

from core.energy import PowerCurve, TechnoEconomicAnalyzer
from core.cost_model import CostModel, get_design_cost_benchmark
from core.farm import (
    WakeModelDomainError,
    _build_structured_grid,
    _rotate_to_wind_axis,
    effective_wind_per_turbine,
    gaussian_deficit,
    gaussian_min_valid_distance_d,
    jensen_deficit,
    optimize_layout_structured,
    simulate_wind_farm,
)
from core.farm_mcda import analyze_park_layouts
from core.multicriteria import (
    LAYOUT_WEIGHTS,
    UNISTMO_LAYOUT_WEIGHTS,
    calibrate_unistmo_weights,
    rank_layout_alternatives,
)


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
        self.assertEqual(
            aligned['wake_model_validity']['near_wake_policy'],
            'reject_material_interaction_outside_domain',
        )

    def test_gaussian_near_wake_domain_is_not_clipped_to_full_deficit(self):
        minimum = gaussian_min_valid_distance_d(k=0.04, ct=0.8)
        self.assertAlmostEqual(minimum, 1.546, places=3)
        with self.assertRaisesRegex(WakeModelDomainError, 'fuera de dominio'):
            gaussian_deficit(100.0, 0.0, 100.0, k=0.04, ct=0.8)
        self.assertGreater(
            gaussian_deficit(minimum * 100.0, 0.0, 100.0, k=0.04, ct=0.8),
            0.99,
        )
        # A cross-wind pair in the immaterial Gaussian tail is not rejected.
        self.assertEqual(gaussian_deficit(100.0, 300.0, 100.0, k=0.04, ct=0.8), 0.0)

    @staticmethod
    def _serial_effective_wind(layout, v_free, direction, rotor, k, ct, model):
        """Reference implementation retained in tests for numerical parity."""
        points = np.asarray(layout, dtype=float)
        rotated = _rotate_to_wind_axis(points, direction)
        speeds = (
            np.full(len(points), float(v_free), dtype=float)
            if np.isscalar(v_free)
            else np.asarray(v_free, dtype=float)
        )
        result = speeds.copy()
        deficit_fn = gaussian_deficit if model == 'gaussian' else jensen_deficit
        for target in range(len(points)):
            squared_deficits = 0.0
            for source in range(len(points)):
                if target == source:
                    continue
                dx = rotated[target, 0] - rotated[source, 0]
                dy = rotated[target, 1] - rotated[source, 1]
                squared_deficits += deficit_fn(
                    dx, dy, rotor, k=k, ct=ct) ** 2
            result[target] = speeds[target] * (1.0 - math.sqrt(squared_deficits))
        return result

    def test_vectorized_jensen_matches_scalar_reference(self):
        rng = np.random.default_rng(20260903)
        layout = rng.uniform(-1500.0, 1500.0, size=(24, 2))
        speeds = rng.uniform(5.0, 13.0, size=len(layout))
        for direction in (0.0, 33.0, 90.0, 217.0, 270.0):
            expected = self._serial_effective_wind(
                layout, speeds, direction, 110.0, 0.075, 0.8, 'jensen')
            actual = effective_wind_per_turbine(
                layout, speeds, direction, 110.0,
                k=0.075, ct=0.8, wake_model='jensen')
            np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-13)

    def test_vectorized_gaussian_matches_scalar_reference(self):
        # A 500 m grid avoids material near-wake pairs for both tested axes.
        layout = np.array([
            [x, y] for x in range(0, 2001, 500) for y in range(0, 1501, 500)
        ], dtype=float)
        for direction in (0.0, 270.0):
            expected = self._serial_effective_wind(
                layout, 9.0, direction, 100.0, 0.04, 0.8, 'gaussian')
            actual = effective_wind_per_turbine(
                layout, 9.0, direction, 100.0,
                k=0.04, ct=0.8, wake_model='gaussian')
            np.testing.assert_allclose(actual, expected, rtol=1e-13, atol=1e-13)

    def test_vectorized_gaussian_preserves_near_wake_rejection(self):
        with self.assertRaisesRegex(WakeModelDomainError, 'fuera de dominio'):
            effective_wind_per_turbine(
                np.array([[0.0, 0.0], [100.0, 0.0]]),
                8.0, 270.0, 100.0, k=0.04, ct=0.8,
                wake_model='gaussian',
            )
        # The same short streamwise separation is harmless outside the wake tail.
        result = effective_wind_per_turbine(
            np.array([[0.0, 0.0], [100.0, 300.0]]),
            8.0, 270.0, 100.0, k=0.04, ct=0.8,
            wake_model='gaussian',
        )
        np.testing.assert_allclose(result, [8.0, 8.0])

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

    def test_unistmo_uses_real_performance_without_model_preference(self):
        gamesa = self.alternative('gamesa', 1.0)
        gamesa.update(turbina='gamesa20_p2000_r40.csv', n_aerogeneradores=10,
                       max_n_fisico=10)
        competitor = self.alternative('competitor', 1.0)
        competitor.update(turbina='competitor_p2000_r40.csv', n_aerogeneradores=10,
                          max_n_fisico=10)
        # The competitor leads in the integral profile's main production and
        # financial criteria. G80 leads on UNISTMO's compact-project criteria.
        competitor.update(aep_p90_mwh=1.30, van_usd=1.30,
                          factor_capacidad=1.08)
        gamesa.update(energy_density_mwh_km2=1.35,
                      capex_usd=0.78, lcoe_usd_kwh=0.82,
                      wake_loss_pct=0.82, civil_complexity_index=0.85,
                      collection_length_m=0.88)
        base = rank_layout_alternatives([gamesa, competitor])
        unistmo = rank_layout_alternatives(
            [gamesa, competitor], profile='unistmo_v1')
        self.assertEqual(base['ranking'][0]['turbina'], 'competitor_p2000_r40.csv')
        self.assertEqual(unistmo['ranking'][0]['turbina'], 'gamesa20_p2000_r40.csv')
        self.assertEqual(base['profile'], 'balanced_integral_v1')
        self.assertEqual(unistmo['profile'], 'unistmo_v1')
        self.assertNotIn('unistmo_technology_preference_score', unistmo['criteria'])
        self.assertEqual(set(UNISTMO_LAYOUT_WEIGHTS), set(LAYOUT_WEIGHTS))
        self.assertAlmostEqual(sum(UNISTMO_LAYOUT_WEIGHTS.values()), 1.0)

    def test_unistmo_does_not_award_points_for_turbine_name(self):
        gamesa = self.alternative('gamesa', 1.0)
        gamesa.update(turbina='gamesa20_p2000_r40.csv', n_aerogeneradores=10,
                       max_n_fisico=10)
        better = self.alternative('better', 1.1)
        better.update(turbina='competitor.csv', n_aerogeneradores=10,
                      max_n_fisico=10)
        result = rank_layout_alternatives([gamesa, better], profile='unistmo_v1')
        self.assertEqual(result['ranking'][0]['turbina'], 'competitor.csv')

    def test_unistmo_calibration_uses_real_site_fit_without_changing_values(self):
        gamesa = self.alternative('gamesa', 1.0)
        gamesa.update(
            turbina='gamesa20_p2000_r40.csv', n_aerogeneradores=10,
            max_n_fisico=10, cp_at_site_mean=0.48,
            cpmax_speed_mismatch_ms=0.2, hours_near_cpmax=5200,
        )
        competitor = self.alternative('competitor', 1.25)
        competitor.update(
            turbina='competitor.csv', n_aerogeneradores=10,
            max_n_fisico=10, cp_at_site_mean=0.34,
            cpmax_speed_mismatch_ms=2.0, hours_near_cpmax=1800,
        )
        original = [dict(gamesa), dict(competitor)]
        weights, audit = calibrate_unistmo_weights([gamesa, competitor])
        result = rank_layout_alternatives(
            [gamesa, competitor], weights=weights, profile='unistmo_v1')
        self.assertTrue(audit['achieved'])
        self.assertFalse(audit['data_values_modified'])
        self.assertLessEqual(max(weights.values()), 0.55)
        self.assertGreater(audit['target_margin'], 0.0)
        self.assertLess(audit['target_margin'], 0.1)
        self.assertEqual(result['ranking'][0]['turbina'], 'gamesa20_p2000_r40.csv')
        self.assertEqual(gamesa, original[0])
        self.assertEqual(competitor, original[1])
        self.assertNotIn('unistmo_technology_preference_score', result['criteria'])

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

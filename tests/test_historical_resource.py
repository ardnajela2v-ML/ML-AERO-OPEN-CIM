import copy
import json
import unittest
from unittest.mock import patch

import numpy as np
from core.energy import PowerCurve
from core.historical_resource import prepare_historical_wind, historical_layout_simulation
from core.farm_mcda import analyze_park_layouts
from core.layout_refinement import build_energy_context, energy_evaluator


class HistoricalProductionTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')
        self.turbine.hub_height_m = 80
        self.resource = {'ws': [4., 8., 12., np.nan, 10.], 'wd': [270., 90., 270., 0., np.nan],
                         'dt_hours': 1/6, 'measurement_height_m': 80,
                         'source_id': 'observed', 'source_kind': 'observed',
                         'provenance': {'selected_year': 2018, 'file_ids': ['certe.txt']}}
        self.polygon = [{'lat': y, 'lon': x} for y, x in [(16.5,-95), (16.5,-94.99), (16.51,-94.99), (16.51,-95)]]
        self.grid = {'bounds': {'lat_min':16.5,'lat_max':16.51,'lon_min':-95,'lon_max':-94.99},
                     'z': [[6.,10.],[6.,10.]]}
        self.sites = [{'v_mean': 8, 'weibull_K': 2, 'rho_mean': 1.2,
                       'sectorial': {'frequency_pct': [25]*4}}]

    def test_pairs_period_duration_and_height_are_preserved_without_filling(self):
        prepared = prepare_historical_wind(self.resource, 80)
        np.testing.assert_array_equal(prepared['ws'], [4, 8, 12])
        np.testing.assert_array_equal(prepared['wd'], [270, 90, 270])
        self.assertEqual(prepared['provenance']['rows_excluded'], 2)
        self.assertEqual(prepared['provenance']['valid_record_hours'], .5)
        self.assertEqual(prepared['provenance']['selected_year'], 2018)
        self.assertEqual(self.resource['ws'][0], 4)
        with self.assertRaisesRegex(ValueError, 'dirección'):
            prepare_historical_wind({**self.resource, 'wd': None}, 80)
        with self.assertRaises(ValueError):
            prepare_historical_wind({**self.resource, 'ws': [np.nan]*5}, 80)

    def test_same_mean_different_history_changes_energy_and_repetition_only_changes_record_energy(self):
        options = dict(wake_model='jensen', rho_override=1.2)
        def run(ws):
            prepared = prepare_historical_wind({**self.resource, 'ws':ws, 'wd':[270]*len(ws)}, 80)
            return historical_layout_simulation([[0,0]], self.turbine, prepared, None, (16.505,-94.995), **options)
        varied = run([4., 12.])
        constant = run([8., 8.])
        repeated = run([4., 12.]*10)
        self.assertNotEqual(varied['aep_mwh'], constant['aep_mwh'])
        self.assertEqual(varied['aep_mwh'], repeated['aep_mwh'])
        self.assertAlmostEqual(varied['record_energy_mwh']*10, repeated['record_energy_mwh'], delta=.06)
        self.assertEqual(varied['dt_h'], round(1/6, 4))

    def test_topsis_cashflow_and_refinement_use_the_same_historical_production(self):
        points = [[-300.,0.],[300.,0.]]
        def geometry(*args, **kwargs):
            result = {'max_n_fit':2, 'n_placed':2, 'layout_xy_m':points,
                      'layout_latlon': [[16.505,-94.998],[16.505,-94.992]],
                      'layout_config':{}, 'min_sep_m':600, 'area_km2':.4, 'n_layouts_evaluated':1}
            if kwargs.get('layout_evaluator'):
                result['constraint_evaluation'] = kwargs['layout_evaluator'](points)
            return result
        # La tercera familia (greedy-vértice) corre en paralelo a las dos
        # estructuradas dentro de generate_feasible_layouts_grid -- sin
        # patchearla también competiría con geometría real (no controlada
        # por el fixture) por el campeonato de la tecnología, rompiendo la
        # reproducibilidad exacta de `points` que esta prueba verifica.
        with patch('core.layout_generation.optimize_layout_structured', side_effect=geometry), patch(
                'core.layout_generation.optimize_layout_greedy_vertex',
                side_effect=ValueError('sin candidatos en este fixture')), patch(
                'core.farm_mcda._joint_synthetic_wind', side_effect=AssertionError('Must use history')):
            result = analyze_park_layouts(self.sites, self.polygon, 'aerogeneradores',
                ['gamesa21_p2000_r45.csv'], wind_grid=self.grid,
                project_years=25, price_usd_kwh=.08, target_height_m=80,
                max_wake_loss_pct=99, wake_model='jensen', historical_wind=copy.deepcopy(self.resource))
        self.assertEqual(result['n_valid_models'], 1, result['errors'])
        row = result['alternatives'][0]
        provenance = row['production_source']
        self.assertEqual(provenance['source_id'], 'observed')
        self.assertLess(provenance['local_speed_multipliers'][0], provenance['local_speed_multipliers'][1])
        self.assertEqual(len(row['turbine_production']), 2)
        self.assertNotIn('mcp', row['uncertainty_components_pct'])
        self.assertAlmostEqual(row['cashflow_table'][0]['energy_mwh'], row['aep_mwh'])
        self.assertEqual(len(row['cashflow_table']),25)
        evaluate, *_ = energy_evaluator(row['energy_context'], self.turbine)
        refined = evaluate(points)
        self.assertAlmostEqual(refined['aep_mwh'], row['aep_mwh'])
        self.assertEqual(refined['production_source'], provenance)
        self.assertEqual(result['ranking']['ranking'][0]['production_source'], provenance)

    def test_hour_of_year_survives_the_same_valid_mask_as_ws_wd(self):
        resource = {**self.resource, 'hour_of_year': [10, 20, 30, 40, 50]}
        prepared = prepare_historical_wind(resource, 80)
        # rows 3 (ws NaN) y 4 (wd NaN) se excluyen — hour_of_year debe seguir
        # exactamente esa misma máscara, no un recorte independiente.
        np.testing.assert_array_equal(prepared['hour_of_year'], [10, 20, 30])

    def test_large_history_context_is_portable_and_reproduces_energy(self):
        ws = np.tile(np.array([4., 8., 12., 10.]), 2501)
        wd = np.tile(np.array([270., 90., 270., 180.]), 2501)
        prepared = prepare_historical_wind({
            'ws': ws, 'wd': wd, 'measurement_height_m': 80, 'dt_hours': 1,
        }, 80)
        prepared['_wind_summary'] = (270., np.array([1., 2.]))
        context = build_energy_context(
            self.sites, self.turbine, wake_model='jensen', k_wake=.075, ct=.8,
            availability=.97, electrical_losses=.03, historical_resource=prepared,
            origin=(16.505, -94.995), rho=1.2, environmental_loss=0.0)
        stored = context['historical_resource']
        self.assertNotIn('ws', stored)
        self.assertNotIn('_wind_summary', stored)
        json.dumps(context)
        evaluate, *_ = energy_evaluator(context, self.turbine)
        points = [[0., 0.], [0., -600.]]
        expected = historical_layout_simulation(
            points, self.turbine,
            {key: value for key, value in prepared.items() if not key.startswith('_')},
            None, (16.505, -94.995),
            wake_model='jensen', k_wake=.075, ct=.8, availability=.97,
            electrical_losses=.03, rho_override=1.2)
        self.assertEqual(evaluate(points)['aep_mwh'], expected['aep_mwh'])

    def test_chronological_revenue_present_only_with_pml_and_hour_of_year(self):
        options = dict(wake_model='jensen', rho_override=1.2)
        price = np.full(8760, 0.08)
        price[18:22] = 0.20  # tarde-noche cara
        resource_with_hours = {**self.resource, 'hour_of_year': [1, 19, 20, 3, 4]}
        prepared = prepare_historical_wind(resource_with_hours, 80)

        with_price = historical_layout_simulation(
            [[0, 0]], self.turbine, prepared, None, (16.505, -94.995),
            price_hourly_usd_kwh=price, **options)
        self.assertIn('chronological_revenue', with_price)
        chron = with_price['chronological_revenue']
        self.assertGreater(chron['revenue_usd_year1'], 0)
        self.assertIsNotNone(chron['capture_ratio'])

        without_price = historical_layout_simulation(
            [[0, 0]], self.turbine, prepared, None, (16.505, -94.995), **options)
        self.assertNotIn('chronological_revenue', without_price)
        # AEP de la vía normal no cambia por calcular (o no) el ingreso real.
        self.assertEqual(with_price['aep_mwh'], without_price['aep_mwh'])

        resource_no_hours = prepare_historical_wind(self.resource, 80)  # sin hour_of_year
        no_hours_result = historical_layout_simulation(
            [[0, 0]], self.turbine, resource_no_hours, None, (16.505, -94.995),
            price_hourly_usd_kwh=price, **options)
        self.assertNotIn('chronological_revenue', no_hours_result)

    def test_flat_price_gives_capture_ratio_of_one(self):
        options = dict(wake_model='jensen', rho_override=1.2)
        flat_price = np.full(8760, 0.08)
        resource_with_hours = {**self.resource, 'hour_of_year': [1, 19, 20, 3, 4]}
        prepared = prepare_historical_wind(resource_with_hours, 80)
        result = historical_layout_simulation(
            [[0, 0]], self.turbine, prepared, None, (16.505, -94.995),
            price_hourly_usd_kwh=flat_price, **options)
        self.assertAlmostEqual(result['chronological_revenue']['capture_ratio'], 1.0, places=6)

    def test_park_layout_uses_real_hourly_revenue_when_pml_is_loaded(self):
        points = [[-300., 0.], [300., 0.]]

        def geometry(*args, **kwargs):
            result = {'max_n_fit': 2, 'n_placed': 2, 'layout_xy_m': points,
                      'layout_latlon': [[16.505, -94.998], [16.505, -94.992]],
                      'layout_config': {}, 'min_sep_m': 600, 'area_km2': .4, 'n_layouts_evaluated': 1}
            if kwargs.get('layout_evaluator'):
                result['constraint_evaluation'] = kwargs['layout_evaluator'](points)
            return result

        resource_with_hours = copy.deepcopy(self.resource)
        resource_with_hours['hour_of_year'] = [1, 19, 20, 3, 4]
        skewed_price = np.full(8760, 0.08)
        skewed_price[[1, 19, 20, 3, 4]] = [0.01, 0.01, 0.01, 0.01, 0.01]  # mucho más barato que el promedio

        # La tercera familia (greedy-vértice) corre en paralelo a las dos
        # estructuradas dentro de generate_feasible_layouts_grid -- sin
        # patchearla también competiría con geometría real (no controlada
        # por el fixture) por el campeonato de la tecnología, rompiendo la
        # reproducibilidad exacta de `points` que esta prueba verifica.
        with patch('core.layout_generation.optimize_layout_structured', side_effect=geometry), patch(
                'core.layout_generation.optimize_layout_greedy_vertex',
                side_effect=ValueError('sin candidatos en este fixture')), patch(
                'core.farm_mcda._joint_synthetic_wind', side_effect=AssertionError('Must use history')):
            flat = analyze_park_layouts(self.sites, self.polygon, 'aerogeneradores',
                ['gamesa21_p2000_r45.csv'], wind_grid=self.grid,
                project_years=25, price_usd_kwh=.08, target_height_m=80,
                max_wake_loss_pct=99, wake_model='jensen', historical_wind=copy.deepcopy(self.resource))
            real = analyze_park_layouts(self.sites, self.polygon, 'aerogeneradores',
                ['gamesa21_p2000_r45.csv'], wind_grid=self.grid,
                project_years=25, price_usd_kwh=.08, target_height_m=80,
                max_wake_loss_pct=99, wake_model='jensen', historical_wind=resource_with_hours,
                price_hourly_usd_kwh=skewed_price)
        flat_row, real_row = flat['alternatives'][0], real['alternatives'][0]
        self.assertIsNone(flat_row['chronological_revenue'])
        self.assertIsNotNone(real_row['chronological_revenue'])
        self.assertLess(real_row['chronological_revenue']['capture_ratio'], 1.0)
        # Mismo AEP (mismo viento) pero VAN real menor: el parque generó
        # sobre todo en horas que resultaron baratas en el PML.
        self.assertAlmostEqual(flat_row['aep_p50_mwh'], real_row['aep_p50_mwh'])
        self.assertLess(real_row['van_usd'], flat_row['van_usd'])

    def test_individual_site_energy_annualizes_the_selected_record(self):
        from core.kriging import turbine_site_economics
        x = np.array([0., 4., 8., 12., 25.])
        y = np.array([0., 100., 500., 2000., 0.])
        def energy(samples):
            return turbine_site_economics(np.asarray(samples), np.full(len(samples),1.2),
                x,y,90,2000, price_usd_kwh=.08, anios=25,
                fitted_power_w=y*1000, annualize=True)
        first, repeated = energy([4.,8.,12.]), energy([4.,8.,12.]*100)
        self.assertAlmostEqual(first['aep_mwh'], repeated['aep_mwh'])
        self.assertAlmostEqual(first['van_usd'], repeated['van_usd'])
        self.assertAlmostEqual(first['factor_capacidad'], repeated['factor_capacidad'])

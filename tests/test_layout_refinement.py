import unittest
from unittest.mock import patch

from core.energy import PowerCurve
from core.farm import simulate_wind_farm
from core.farm_mcda import _joint_synthetic_wind, analyze_park_layouts
from core.layout_refinement import build_energy_context, energy_evaluator


class EnergyBasisTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')
        self.sites = [dict(v_mean=8.2, weibull_K=2.1, rho_mean=1.17,
                           sectorial={'frequency_pct': [10, 20, 30, 40]})]
        self.context = build_energy_context(
            self.sites, self.turbine, wake_model='jensen', k_wake=.075, ct=.8,
            availability=.95, electrical_losses=.02,
            curtailment_loss=.04, environmental_loss=.03)

    def test_reproduces_topsis_energy_without_double_extrapolation_or_losses(self):
        points = [[0, 0], [700, 0]]
        ws, wd, _, _, rho = _joint_synthetic_wind(self.sites)
        reference = simulate_wind_farm(points, self.turbine, ws, wd,
            wake_model='jensen', k_wake=.075, ct=.8, n_sectors=36,
            availability=.95, electrical_losses=.02, rho_override=rho)
        evaluate, *_ = energy_evaluator(self.context, self.turbine)
        result = evaluate(points)
        self.assertAlmostEqual(result['aep_mwh'], reference['aep_mwh'] * .96 * .97)
        self.assertEqual(result['wake_loss_pct'], reference['wake_loss_pct'])
        self.assertAlmostEqual(result['aep_per_turbine_mwh'] * 2, result['aep_mwh'])
        self.assertAlmostEqual(result['mean_power_per_turbine_kw'] * 8760 / 1000,
                               result['aep_per_turbine_mwh'])
        self.assertAlmostEqual(sum(t['aep_mwh'] for t in result['turbines']),
                               result['aep_mwh'], delta=.2)

    def test_rejects_changed_hub_height(self):
        self.turbine.hub_height_m += 10
        with self.assertRaisesRegex(ValueError, 'altura'):
            energy_evaluator(self.context, self.turbine)

    def test_topsis_serializes_reproducible_energy_context(self):
        polygon = [dict(lat=0, lon=0), dict(lat=0, lon=.02),
                   dict(lat=.02, lon=.02), dict(lat=.02, lon=0)]
        points = [[0, 0], [700, 0]]
        def optimize(*args, **kwargs):
            result = dict(max_n_fit=2, n_placed=2, layout_xy_m=points,
                layout_latlon=[[.01, .01], [.01, .0163]], layout_config={},
                min_sep_m=270, area_km2=4.9, n_layouts_evaluated=1)
            if kwargs.get('layout_evaluator'):
                result['constraint_evaluation'] = kwargs['layout_evaluator'](points)
            return result
        # La tercera familia (greedy-vértice) corre en paralelo a las dos
        # estructuradas dentro de generate_feasible_layouts_grid -- sin
        # patchearla también competiría con geometría real (no controlada
        # por el fixture) por el campeonato de la tecnología, rompiendo la
        # reproducibilidad exacta de `points` que esta prueba verifica.
        with patch('core.layout_generation.optimize_layout_structured', side_effect=optimize), patch(
                'core.layout_generation.optimize_layout_greedy_vertex',
                side_effect=ValueError('sin candidatos en este fixture')):
            result = analyze_park_layouts(self.sites, polygon, 'aerogeneradores',
                ['gamesa21_p2000_r45.csv'], project_years=20, price_usd_kwh=.08,
                max_wake_loss_pct=99, wake_model='jensen', curtailment_loss=.04,
                environmental_loss=.03)
        row = result['alternatives'][0]
        evaluate, *_ = energy_evaluator(row['energy_context'], self.turbine)
        self.assertAlmostEqual(evaluate(points)['aep_mwh'], row['aep_p50_mwh'])
        self.assertEqual(row['layout_constraints']['polygon'], polygon)


class LayoutRefinementTests(EnergyBasisTests):
    def alternative(self):
        from core.farm import _latlon_to_xy
        import numpy as np
        polygon = [dict(lat=0, lon=0), dict(lat=0, lon=.01),
                   dict(lat=.01, lon=.01), dict(lat=.01, lon=0)]
        latlon = [[.003, .003], [.003, .008]]
        x, y = _latlon_to_xy([p[0] for p in latlon], [p[1] for p in latlon], .005, .005)
        points = np.column_stack([x, y]).tolist()
        evaluate, *_ = energy_evaluator(self.context, self.turbine)
        sim = evaluate(points)
        return dict(turbina='gamesa21_p2000_r45.csv', n_aerogeneradores=2,
            layout_latlon=latlon, layout_xy_m=points,
            layout_config={'stagger': 0, 'layout_type': 'alineado'},
            aep_p50_mwh=sim['aep_mwh'], energy_context=self.context,
            turbine_specification={key: getattr(self.turbine, key) for key in
                ('rated_power_kw', 'rotor_diameter_m', 'hub_height_m')},
            layout_constraints=dict(polygon=polygon, exclusions=[], boundary_setback_m=45,
                min_spacing_d=3, s_para_range=[5], s_perp_range=[3], max_wake_loss_pct=99))

    def test_real_family_winners_preserve_topsis_energy_and_count(self):
        from core.layout_refinement import refine_topsis_layout
        row = self.alternative()
        result = refine_topsis_layout(row, self.turbine)
        comparison = result['comparison']
        self.assertTrue(comparison['same_count'])
        self.assertGreaterEqual(result['simulation']['aep_mwh'], row['aep_p50_mwh'] - .05)
        self.assertEqual({r['family'] for r in comparison['winners']},
            {'topsis', 'topsis_improved', 'aligned', 'staggered'})
        for winner in comparison['winners']:
            self.assertEqual(winner['n_placed'], 2)
            self.assertEqual(winner['simulation']['wake_model'], 'jensen')
            self.assertAlmostEqual(winner['aep_per_turbine_mwh'] * 2, winner['aep_p50_mwh'])
        self.assertEqual(result['aep_p50_mwh'], max(r['aep_p50_mwh'] for r in comparison['winners']))
        least = next(r for r in comparison['winners'] if r['id'] == comparison['min_wake_id'])
        self.assertEqual(least['wake_loss_pct'], min(r['wake_loss_pct'] for r in comparison['winners']))

    def test_changed_energy_and_invalid_geometry_are_rejected(self):
        from core.layout_refinement import refine_topsis_layout
        row = self.alternative()
        row['aep_p50_mwh'] *= 2
        with self.assertRaisesRegex(ValueError, 'reproducible'):
            refine_topsis_layout(row, self.turbine)
        row = self.alternative()
        row['layout_constraints']['exclusions'] = [row['layout_constraints']['polygon']]
        with self.assertRaisesRegex(ValueError, 'geometría'):
            refine_topsis_layout(row, self.turbine)

    def test_family_filter_does_not_apply_baseline_of_another_family(self):
        from core.layout_refinement import refine_topsis_layout
        result = refine_topsis_layout(self.alternative(), self.turbine, layout_type='staggered')
        self.assertEqual(result['family'], 'staggered')
        self.assertFalse(result['comparison']['winners'][0]['feasible'])

    def test_more_turbines_than_topsis_requires_new_ranking(self):
        from core.layout_refinement import refine_topsis_layout
        with self.assertRaisesRegex(ValueError, 'aumentar'):
            refine_topsis_layout(self.alternative(), self.turbine, n_turbines=3)

    def test_family_unable_to_reach_topsis_count_is_rescued_not_dropped(self):
        """Un patrón fijo (solo alineado) puede no alcanzar la cuenta que la
        búsqueda combinada original de TOPSIS sí logró con otro arreglo. Antes
        esto dejaba la familia sin candidatos y con un mensaje que sonaba a
        límite absoluto del polígono; ahora se rescata el máximo propio del
        patrón como candidato informativo, marcado no elegible."""
        from core.layout_refinement import refine_topsis_layout
        from core import farm as farm_module
        real_optimize = farm_module.optimize_layout_structured

        def flaky_optimize(*args, **kwargs):
            if kwargs.get('stagger_options') == (0.0,) and args[2] > 0:
                raise ValueError(
                    'El polígono admite como máximo 1 aerogeneradores; se solicitaron 2.')
            if kwargs.get('stagger_options') == (0.0,) and args[2] == 0:
                return real_optimize(args[0], args[1], 1,
                                     **{k: v for k, v in kwargs.items() if k != 'stagger_options'},
                                     stagger_options=(0.0,))
            return real_optimize(*args, **kwargs)

        with patch('core.farm.optimize_layout_structured', side_effect=flaky_optimize):
            result = refine_topsis_layout(self.alternative(), self.turbine)
        comparison = result['comparison']
        aligned_errors = [e for e in comparison['errors'] if e['family'] == 'Alineado']
        self.assertEqual(len(aligned_errors), 1)
        self.assertIn('máximo 1', aligned_errors[0]['reason'])
        self.assertIn('Referencia TOPSIS llega a 2', aligned_errors[0]['reason'])
        aligned_winner = next(w for w in comparison['winners'] if w['family'] == 'aligned')
        self.assertFalse(aligned_winner['feasible'])
        self.assertEqual(aligned_winner['n_placed'], 1)
        self.assertIn('máximo 1', aligned_winner['label'])
        # The staggered family and the original TOPSIS reference are unaffected.
        self.assertTrue(any(w['family'] == 'topsis' for w in comparison['winners']))
        staggered_winner = next(w for w in comparison['winners'] if w['family'] == 'staggered')
        self.assertEqual(staggered_winner['n_placed'], 2)

    def test_endpoint_returns_comparison_and_rejects_stale_energy(self):
        from api import create_app
        client = create_app().test_client()
        row = self.alternative()
        response = client.post('/api/farm/refine_topsis', json={
            'alternative': row, 'layout_type': 'aligned'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['turbine_key'], row['turbina'])
        self.assertIn('comparison', response.get_json())
        row['aep_p50_mwh'] *= 2
        response = client.post('/api/farm/refine_topsis', json={'alternative': row})
        self.assertEqual(response.status_code, 400)

    def test_gaussian_refinement_preserves_topsis_wake_model(self):
        from core.layout_refinement import refine_topsis_layout
        self.context['wake_model'] = 'gaussian'
        self.context['k_wake'] = .04
        row = self.alternative()
        result = refine_topsis_layout(row, self.turbine, layout_type='aligned')
        self.assertEqual(result['simulation']['wake_model'], 'gaussian')
        self.assertGreaterEqual(result['simulation']['aep_mwh'], row['aep_p50_mwh'] - .05)

    def test_full_energy_selects_later_feasible_candidate_and_retains_least_wake(self):
        from core.layout_refinement import refine_topsis_layout
        import numpy as np
        row = self.alternative()
        row['aep_p50_mwh'] = 100
        row['layout_constraints']['max_wake_loss_pct'] = 5
        layouts = [dict(layout_xy_m=[[-300, y], [300, y]],
                        layout_latlon=[[0, 0], [0, 0]], wind_at_turbines=[8, 8],
                        layout_config={}) for y in [100, 200, 300]]
        def evaluate(points):
            energy, loss = {100: (500, 8), 200: (150, 4), 300: (120, 1)}.get(
                round(points[0][1]), (100, 3))
            return dict(aep_mwh=energy, wake_loss_pct=loss, turbines=[])
        with patch('core.layout_refinement.energy_evaluator',
                   return_value=(evaluate, np.array([8.]), np.array([270.]), 8, 1.225)), \
             patch('core.farm.optimize_layout_structured', return_value={
                 'candidate_layouts': layouts, 'layout_config': {'dominant_dir_deg': 270}}):
            result = refine_topsis_layout(row, self.turbine, layout_type='aligned')
        self.assertEqual(result['aep_p50_mwh'], 150)
        self.assertEqual(result['wake_loss_pct'], 4)
        least = next(c for c in result['comparison']['winners']
                     if c['id'] == result['comparison']['min_wake_id'])
        self.assertEqual(least['wake_loss_pct'], 1)
        self.assertTrue(least['pareto'])
        self.assertTrue(result['pareto'])

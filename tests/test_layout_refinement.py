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
        with patch('core.farm_mcda.optimize_layout_structured', side_effect=optimize):
            result = analyze_park_layouts(self.sites, polygon, 'aerogeneradores',
                ['gamesa21_p2000_r45.csv'], project_years=20, price_usd_kwh=.08,
                max_wake_loss_pct=99, wake_model='jensen', curtailment_loss=.04,
                environmental_loss=.03)
        row = result['alternatives'][0]
        evaluate, *_ = energy_evaluator(row['energy_context'], self.turbine)
        self.assertAlmostEqual(evaluate(points)['aep_mwh'], row['aep_p50_mwh'])
        self.assertEqual(row['layout_constraints']['polygon'], polygon)

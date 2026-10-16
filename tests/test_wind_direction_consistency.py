import unittest
import numpy as np
from core.energy import PowerCurve
from core.farm import (bin_wind_to_sectors, dominant_direction,
                       energy_weighted_dominant_direction, simulate_wind_farm)
from core.farm_mcda import _joint_synthetic_wind
from core.layout_refinement import energy_evaluator, build_energy_context


class WindDirectionTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')

    def test_nnw_sector_remains_337_5_from_synthesis_through_energy_rose(self):
        sites = [dict(v_mean=10, weibull_K=2, rho_mean=1.2,
                      sectorial={'frequency_pct': [0] * 15 + [100]})]
        ws, wd, *_ = _joint_synthetic_wind(sites)
        self.assertTrue(np.all(wd == 337.5))
        dom, centres, energy = energy_weighted_dominant_direction(ws, wd, self.turbine)
        self.assertEqual(dom, 337.5)
        self.assertEqual(centres[15], 337.5)
        self.assertAlmostEqual(energy[15], 100)
        sim = simulate_wind_farm([[0, 0]], self.turbine, ws, wd, rho_override=1.2)
        self.assertEqual(sim['dominant_energy_dir_deg'], 337.5)

    def test_north_wraps_at_360_and_cardinals_have_no_half_sector_shift(self):
        for direction in [0, 90, 180, 270, 337.5]:
            wd = np.full(50, direction)
            ws = np.full(50, 8.)
            centres, _, frequency = bin_wind_to_sectors(ws, wd, 16)
            self.assertEqual(centres[np.argmax(frequency.sum(axis=1))], direction)
            self.assertEqual(dominant_direction(wd, 16), direction)
        self.assertEqual(dominant_direction(np.array([359., 1., 0.]), 16), 0)

    def test_energetic_direction_can_differ_from_most_frequent_direction(self):
        ws = np.array([3.] * 60 + [12.] * 40)
        wd = np.array([90.] * 60 + [337.5] * 40)
        self.assertEqual(dominant_direction(wd, 16), 90)
        energy_dir, *_ = energy_weighted_dominant_direction(ws, wd, self.turbine)
        self.assertEqual(energy_dir, 337.5)

    def test_north_wind_reduces_southern_turbine_output(self):
        sim = simulate_wind_farm([[0, 0], [0, -600]], self.turbine,
            np.full(100, 8.), np.zeros(100), rho_override=1.2)
        self.assertGreater(sim['turbines'][0]['aep_mwh'], sim['turbines'][1]['aep_mwh'])
        self.assertEqual(sim['dominant_energy_dir_deg'], 0)

    def test_previous_sector_convention_requires_new_topsis(self):
        ctx = build_energy_context([dict(v_mean=8)], self.turbine,
            wake_model='jensen', k_wake=.075, ct=.8, availability=.97, electrical_losses=.03)
        ctx['version'] = 1
        with self.assertRaisesRegex(ValueError, 'TOPSIS nuevamente'):
            energy_evaluator(ctx, self.turbine)

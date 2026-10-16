import math
import unittest

from core.energy import PowerCurve
from core.acoustic_noise import (
    evaluate_noise_constraint,
    noise_screening_radius_m,
    repair_noise_violation,
    sound_power_at_receptor_dba,
    sound_pressure_from_source_dba,
)


class SoundPropagationTests(unittest.TestCase):
    """Verifica la fórmula de propagación (ISO 9613-2 simplificado) contra
    un cálculo manual, y la suma logarítmica de múltiples fuentes.
    """

    def test_single_source_matches_hand_calculation(self):
        lw = 105.0
        r = 500.0
        alpha = 1.5
        expected = lw - 20.0 * math.log10(r) - 11.0 - alpha * (r / 1000.0)
        self.assertAlmostEqual(sound_pressure_from_source_dba(lw, r, alpha), expected, places=6)

    def test_pressure_decreases_with_distance(self):
        lw = 105.0
        self.assertGreater(
            sound_pressure_from_source_dba(lw, 200.0),
            sound_pressure_from_source_dba(lw, 800.0),
        )

    def test_logarithmic_summation_of_equal_sources_adds_about_3db_when_doubled(self):
        # N fuentes idénticas a la misma distancia: Lp_total = Lp_uno + 10*log10(N).
        positions_one = [[0.0, 0.0]]
        positions_two = [[0.0, 0.0], [0.0, 0.0]]  # misma distancia al receptor
        receptor = [500.0, 0.0]
        lp_one = sound_power_at_receptor_dba(positions_one, [105.0], receptor)
        lp_two = sound_power_at_receptor_dba(positions_two, [105.0, 105.0], receptor)
        self.assertAlmostEqual(lp_two - lp_one, 10.0 * math.log10(2), places=6)

    def test_ten_turbines_add_about_ten_db_over_one(self):
        receptor = [1000.0, 0.0]
        one = sound_power_at_receptor_dba([[0.0, 0.0]], [103.0], receptor)
        ten = sound_power_at_receptor_dba([[0.0, 0.0]] * 10, [103.0] * 10, receptor)
        self.assertAlmostEqual(ten - one, 10.0 * math.log10(10), places=6)

    def test_farther_source_contributes_less_to_the_sum(self):
        receptor = [0.0, 0.0]
        near = sound_power_at_receptor_dba([[100.0, 0.0]], [103.0], receptor)
        far = sound_power_at_receptor_dba([[100.0, 0.0], [5000.0, 0.0]], [103.0, 103.0], receptor)
        # La fuente lejana casi no cambia el total dominado por la cercana.
        self.assertLess(far - near, 0.5)


class ScreeningRadiusTests(unittest.TestCase):
    def test_screening_radius_is_finite_and_positive(self):
        radius = noise_screening_radius_m(lw_max_catalog_dba=107.0, strictest_limit_dba=45.0)
        self.assertGreater(radius, 0.0)
        self.assertLess(radius, 50_000.0)

    def test_beyond_screening_radius_single_source_is_below_margin(self):
        radius = noise_screening_radius_m(lw_max_catalog_dba=107.0, strictest_limit_dba=45.0,
                                          safety_margin_db=6.0)
        lp_at_radius = sound_pressure_from_source_dba(107.0, radius)
        self.assertLessEqual(lp_at_radius, 45.0 - 6.0 + 1e-6)

    def test_louder_turbine_needs_larger_screening_radius(self):
        quiet = noise_screening_radius_m(lw_max_catalog_dba=97.0, strictest_limit_dba=45.0)
        loud = noise_screening_radius_m(lw_max_catalog_dba=109.0, strictest_limit_dba=45.0)
        self.assertGreater(loud, quiet)


class EvaluateNoiseConstraintTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')

    def test_turbine_catalog_gets_a_generic_sound_power_when_not_declared(self):
        self.assertIsNotNone(self.turbine.sound_power_lwa_db)
        self.assertEqual(self.turbine.sound_power_source, 'generic_class_estimate_iec61400_11')

    def test_far_receptor_is_ok(self):
        layout = [[0.0, 0.0], [300.0, 0.0]]
        receptors = [{'x': 20000.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda', 'period': 'night'}]
        result = evaluate_noise_constraint(layout, self.turbine, receptors, {'day': 55.0, 'night': 45.0})
        self.assertTrue(result['ok'])

    def test_near_receptor_with_strict_limit_fails(self):
        layout = [[0.0, 0.0], [300.0, 0.0]]
        receptors = [{'x': 50.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda', 'period': 'night'}]
        result = evaluate_noise_constraint(layout, self.turbine, receptors, {'day': 55.0, 'night': 45.0})
        self.assertFalse(result['ok'])
        self.assertFalse(result['per_receptor'][0]['ok'])


class RepairNoiseViolationTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')

    def test_repair_moves_offending_turbine_away_and_succeeds_when_space_allows(self):
        layout = [[0.0, 0.0], [400.0, 0.0]]
        receptors = [{'x': -80.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda', 'period': 'night'}]
        limit = {'day': 55.0, 'night': 45.0}

        def allowed(x, y):
            return -5000.0 <= x <= 5000.0 and -5000.0 <= y <= 5000.0

        result = repair_noise_violation(layout, self.turbine, receptors, limit,
                                        position_allowed_fn=allowed, min_spacing_m=150.0)
        self.assertTrue(result['repaired'])
        self.assertGreater(result['steps_used'], 0)
        self.assertTrue(result['evaluation']['ok'])

    def test_repair_fails_when_confined_to_a_tiny_allowed_area(self):
        layout = [[0.0, 0.0], [400.0, 0.0]]
        receptors = [{'x': -80.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda', 'period': 'night'}]
        limit = {'day': 55.0, 'night': 45.0}

        def allowed(x, y):
            return -10.0 <= x <= 10.0 and -10.0 <= y <= 10.0  # no deja moverse

        result = repair_noise_violation(layout, self.turbine, receptors, limit,
                                        position_allowed_fn=allowed, min_spacing_m=150.0)
        self.assertFalse(result['repaired'])
        self.assertEqual(result['steps_used'], 0)


if __name__ == '__main__':
    unittest.main()

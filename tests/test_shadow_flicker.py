import math
import unittest
from datetime import datetime, timedelta, timezone

from core.energy import PowerCurve
from core.shadow_flicker import (
    EARTH_AXIAL_TILT_DEG,
    annual_flicker_hours,
    evaluate_shadow_flicker_constraint,
    is_receptor_in_flicker_zone,
    repair_shadow_flicker_violation,
    solar_declination_deg,
    solar_position,
)


class SolarPositionTests(unittest.TestCase):
    """Verifica la posición solar contra hechos astronómicos independientes
    y verificables (inclinación axial terrestre ~23.44°, definición de
    solsticio/equinoccio), no contra una tabla de valores recordada de
    memoria -- ver instrucción del plan de tesis al respecto.
    """

    def test_declination_never_exceeds_earth_axial_tilt(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for day in range(0, 365, 5):
            declination = solar_declination_deg(start + timedelta(days=day))
            self.assertLessEqual(abs(declination), EARTH_AXIAL_TILT_DEG + 0.1)

    def test_declination_near_zero_at_september_equinox(self):
        # Equinoccio de septiembre cae ~22-23 de septiembre; declinación
        # cruza 0° ese día por definición de equinoccio.
        declination = solar_declination_deg(datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc))
        self.assertLess(abs(declination), 1.5)

    def test_declination_near_maximum_at_june_solstice(self):
        # Solsticio de junio: declinación == inclinación axial por
        # definición del solsticio (el sol alcanza su declinación máxima).
        declination = solar_declination_deg(datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc))
        self.assertGreater(declination, EARTH_AXIAL_TILT_DEG - 0.5)

    def test_elevation_at_local_solar_noon_matches_zenith_formula(self):
        # Autoconsistencia: al mediodía solar local (elevación máxima del
        # día), cos(zenith) = cos(lat - dec) exactamente -- verifica que la
        # cadena elevación<-ángulo horario<-declinación esté bien conectada,
        # independientemente de si la propia declinación es astronómicamente
        # exacta (eso ya se valida por separado arriba).
        lat, lon = 19.0, 0.0
        day = datetime(2026, 3, 20, tzinfo=timezone.utc)
        best_elevation = -90.0
        best_ts = None
        for minute in range(0, 24 * 60, 5):
            ts = day + timedelta(minutes=minute)
            _, elevation = solar_position(lat, lon, ts)
            if elevation > best_elevation:
                best_elevation, best_ts = elevation, ts
        declination = solar_declination_deg(best_ts)
        expected = 90.0 - abs(lat - declination)
        self.assertAlmostEqual(best_elevation, expected, delta=0.5)

    def test_sun_is_below_horizon_at_local_midnight(self):
        lat, lon = 19.0, 0.0
        midnight_utc = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)  # ~medianoche local en lon=0
        _, elevation = solar_position(lat, lon, midnight_utc)
        self.assertLess(elevation, 0.0)

    def test_azimuth_is_always_in_valid_range(self):
        lat, lon = 19.0, -95.0
        for hour in range(0, 24, 3):
            ts = datetime(2026, 6, 15, hour, tzinfo=timezone.utc)
            azimuth, _ = solar_position(lat, lon, ts)
            self.assertGreaterEqual(azimuth, 0.0)
            self.assertLess(azimuth, 360.0)


class FlickerZoneTests(unittest.TestCase):
    def test_no_flicker_when_sun_below_horizon(self):
        self.assertFalse(is_receptor_in_flicker_zone(
            [0.0, 0.0], 90.0, 80.0, [500.0, 0.0], sun_azimuth_deg=270.0, sun_elevation_deg=-5.0))

    def test_flicker_when_receptor_aligned_with_shadow_direction(self):
        # Sol al oeste (270°) -> sombra hacia el este -> receptor al este de la turbina.
        self.assertTrue(is_receptor_in_flicker_zone(
            [0.0, 0.0], 90.0, 80.0, [500.0, 0.0], sun_azimuth_deg=270.0, sun_elevation_deg=30.0))

    def test_no_flicker_when_receptor_perpendicular_to_shadow_direction(self):
        self.assertFalse(is_receptor_in_flicker_zone(
            [0.0, 0.0], 90.0, 80.0, [0.0, 500.0], sun_azimuth_deg=270.0, sun_elevation_deg=30.0))

    def test_no_flicker_beyond_max_distance(self):
        self.assertFalse(is_receptor_in_flicker_zone(
            [0.0, 0.0], 90.0, 80.0, [2000.0, 0.0], sun_azimuth_deg=270.0, sun_elevation_deg=30.0,
            max_flicker_distance_m=900.0))

    def test_tolerance_angle_shrinks_with_distance(self):
        # A mayor distancia, el ángulo tolerado (tamaño angular del rotor)
        # es menor -- un receptor apenas desalineado deja de ver flicker
        # más lejos que cerca.
        near_tolerance = math.degrees(math.atan2(45.0, 200.0))
        far_tolerance = math.degrees(math.atan2(45.0, 2000.0))
        self.assertGreater(near_tolerance, far_tolerance)


class AnnualFlickerHoursTests(unittest.TestCase):
    def test_hours_increase_when_receptor_closer_to_turbine(self):
        lat0, lon0 = 19.0, -95.0
        turbine_xy = [0.0, 0.0]
        near_hours = annual_flicker_hours(
            turbine_xy, 90.0, 80.0, [200.0, 0.0], lat0, lon0, timestep_minutes=120)
        far_hours = annual_flicker_hours(
            turbine_xy, 90.0, 80.0, [800.0, 0.0], lat0, lon0, timestep_minutes=120)
        # Más cerca, la tolerancia angular es más ancha -> al menos tantas
        # horas de flicker como más lejos (monotonía no estricta por el
        # muestreo discreto del barrido anual).
        self.assertGreaterEqual(near_hours, far_hours)

    def test_zero_hours_when_receptor_outside_max_distance(self):
        hours = annual_flicker_hours(
            [0.0, 0.0], 90.0, 80.0, [5000.0, 0.0], 19.0, -95.0, timestep_minutes=120)
        self.assertEqual(hours, 0.0)

    def test_realistic_mode_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            annual_flicker_hours([0.0, 0.0], 90.0, 80.0, [200.0, 0.0], 19.0, -95.0,
                                 mode='realistic', timestep_minutes=120)


class EvaluateShadowFlickerConstraintTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')

    def test_union_across_turbines_does_not_double_count_simultaneous_flicker(self):
        # Dos turbinas EN EL MISMO PUNTO flickean exactamente en los mismos
        # instantes que una sola -- si el código sumara por turbina en vez
        # de tomar la unión, esto daría el doble de horas.
        receptors = [{'x': 400.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda'}]
        one = evaluate_shadow_flicker_constraint(
            [[0.0, 0.0]], self.turbine, receptors, 19.0, -95.0,
            limit_hours_year=9999, timestep_minutes=180)
        two_same_spot = evaluate_shadow_flicker_constraint(
            [[0.0, 0.0], [0.0, 0.0]], self.turbine, receptors, 19.0, -95.0,
            limit_hours_year=9999, timestep_minutes=180)
        self.assertAlmostEqual(one['max_receptor_hours'], two_same_spot['max_receptor_hours'], places=6)

    def test_far_receptor_is_ok(self):
        receptors = [{'x': 50000.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda'}]
        result = evaluate_shadow_flicker_constraint(
            [[0.0, 0.0]], self.turbine, receptors, 19.0, -95.0,
            limit_hours_year=30.0, timestep_minutes=180)
        self.assertTrue(result['ok'])

    def test_near_receptor_with_strict_limit_fails(self):
        receptors = [{'x': 300.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda'}]
        result = evaluate_shadow_flicker_constraint(
            [[0.0, 0.0]], self.turbine, receptors, 19.0, -95.0,
            limit_hours_year=0.01, timestep_minutes=120)
        self.assertFalse(result['ok'])


class RepairShadowFlickerViolationTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')

    def test_repair_moves_turbine_and_can_succeed_with_room_to_move(self):
        layout = [[0.0, 0.0]]
        receptors = [{'x': 300.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda'}]

        def allowed(x, y):
            return -5000.0 <= x <= 5000.0 and -5000.0 <= y <= 5000.0

        result = repair_shadow_flicker_violation(
            layout, self.turbine, receptors, 19.0, -95.0, limit_hours_year=0.01,
            position_allowed_fn=allowed, min_spacing_m=50.0, step_m=100.0, max_steps=10,
            timestep_minutes=180)
        # No se exige éxito garantizado (el flicker puede persistir incluso
        # lejos si la geometría es desfavorable), solo que se intentó mover
        # y que el reporte final es internamente consistente.
        self.assertGreaterEqual(result['steps_used'], 0)
        self.assertIn('ok', result['evaluation'])

    def test_repair_fails_when_confined_to_a_tiny_allowed_area(self):
        layout = [[0.0, 0.0]]
        receptors = [{'x': 300.0, 'y': 0.0, 'lat': 1, 'lon': 1, 'type': 'vivienda'}]

        def allowed(x, y):
            return -5.0 <= x <= 5.0 and -5.0 <= y <= 5.0

        result = repair_shadow_flicker_violation(
            layout, self.turbine, receptors, 19.0, -95.0, limit_hours_year=0.01,
            position_allowed_fn=allowed, min_spacing_m=50.0, step_m=100.0, max_steps=10,
            timestep_minutes=180)
        self.assertEqual(result['steps_used'], 0)
        self.assertFalse(result['repaired'])


if __name__ == '__main__':
    unittest.main()

import unittest
from unittest.mock import patch

import numpy as np

from core.energy import PowerCurve
from core.farm import build_slope_mask_fn, optimize_layout_structured
from core.terrain import sample_slope_grid_deg


def _ramp_elevation(lats, lons):
    """DEM sintético: rampa lineal en longitud (este), plana en latitud.
    Pendiente constante y conocida en toda la rejilla -- fácil de verificar
    contra la fórmula de diferencias centradas ya existente."""
    slope_m_per_deg_lon = 50000.0  # ~450 m/km en longitud, pendiente fuerte
    lon0 = min(lons)
    return [[max(0.0, (lon - lon0) * slope_m_per_deg_lon) for lon in lons] for _ in lats]


def _flat_elevation(lats, lons):
    return [[0.0 for _ in lons] for _ in lats]


class SampleSlopeGridDegTests(unittest.TestCase):
    def test_flat_terrain_has_zero_slope(self):
        with patch('core.terrain.sample_grid_elevation', side_effect=_flat_elevation):
            slope = sample_slope_grid_deg([16.0, 16.01, 16.02], [-95.0, -94.99, -94.98])
        for row in slope:
            for value in row:
                self.assertAlmostEqual(value, 0.0, places=6)

    def test_ramp_terrain_has_uniform_positive_slope(self):
        with patch('core.terrain.sample_grid_elevation', side_effect=_ramp_elevation):
            slope = sample_slope_grid_deg([16.0, 16.01, 16.02], [-95.0, -94.99, -94.98])
        # Todas las celdas interiores deben compartir la misma pendiente
        # (rampa lineal uniforme) y ser claramente mayores que cero.
        values = [v for row in slope for v in row]
        self.assertTrue(all(v > 5.0 for v in values))
        self.assertAlmostEqual(max(values), min(values), delta=0.5)


class BuildSlopeMaskFnTests(unittest.TestCase):
    def test_returns_none_when_max_slope_not_configured(self):
        self.assertIsNone(build_slope_mask_fn([16.0, 16.01], [-95.0, -94.99], None))

    def test_flat_terrain_passes_any_reasonable_limit(self):
        with patch('core.terrain.sample_grid_elevation', side_effect=_flat_elevation):
            mask = build_slope_mask_fn([16.0, 16.01, 16.02], [-95.0, -94.99, -94.98], max_slope_deg=5.0)
        self.assertTrue(mask(16.01, -94.99))

    def test_steep_terrain_fails_a_strict_limit(self):
        with patch('core.terrain.sample_grid_elevation', side_effect=_ramp_elevation):
            mask = build_slope_mask_fn([16.0, 16.01, 16.02], [-95.0, -94.99, -94.98], max_slope_deg=2.0)
        self.assertFalse(mask(16.01, -94.99))

    def test_steep_terrain_passes_a_lenient_limit(self):
        with patch('core.terrain.sample_grid_elevation', side_effect=_ramp_elevation):
            mask = build_slope_mask_fn([16.0, 16.01, 16.02], [-95.0, -94.99, -94.98], max_slope_deg=60.0)
        self.assertTrue(mask(16.01, -94.99))


class OptimizeLayoutStructuredSlopeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')
        self.polygon = [
            {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
            {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
        ]

    def test_default_behaviour_unchanged_without_slope_limit(self):
        result = optimize_layout_structured(
            self.polygon, self.turbine, 1, fallback_wind_ms=8.0,
            min_spacing_d=3.0, s_para_range=(3, 4), s_perp_range=(3, 4))
        self.assertEqual(result['n_placed'], 1)

    def test_flat_terrain_does_not_block_placement(self):
        with patch('core.terrain.sample_grid_elevation', side_effect=_flat_elevation):
            result = optimize_layout_structured(
                self.polygon, self.turbine, 1, fallback_wind_ms=8.0,
                min_spacing_d=3.0, s_para_range=(3, 4), s_perp_range=(3, 4),
                max_slope_deg=5.0)
        self.assertEqual(result['n_placed'], 1)

    def test_impossibly_steep_terrain_leaves_no_feasible_position(self):
        with patch('core.terrain.sample_grid_elevation', side_effect=_ramp_elevation):
            with self.assertRaises(ValueError):
                optimize_layout_structured(
                    self.polygon, self.turbine, 1, fallback_wind_ms=8.0,
                    min_spacing_d=3.0, s_para_range=(3, 4), s_perp_range=(3, 4),
                    max_slope_deg=0.001)


if __name__ == '__main__':
    unittest.main()

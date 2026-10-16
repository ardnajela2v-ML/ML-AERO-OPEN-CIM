import math
import unittest

import numpy as np

from core.energy import PowerCurve
from core.farm import optimize_layout_greedy_vertex


class LayoutGreedyVertexTests(unittest.TestCase):
    """core.farm.optimize_layout_greedy_vertex: tercera familia de layout
    (packing-maximising, sembrada en un vértice, ejes alineados al viento),
    complementaria a las familias regulares aligned/staggered de
    optimize_layout_structured -- ver core.layout_generation.GREEDY_FAMILY.
    """

    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')
        # Polígono rectangular grande (~1.1km x 1.1km) para que quepan
        # varias turbinas a 3D con margen.
        self.polygon = [
            {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
            {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
        ]

    def test_places_multiple_turbines_respecting_min_spacing(self):
        result = optimize_layout_greedy_vertex(
            self.polygon, self.turbine, min_spacing_d=3.0, grid_step_d=1.0)
        self.assertGreater(result['max_n_fit'], 1)
        D = self.turbine.rotor_diameter_m
        min_sep_m = 3.0 * D
        points = np.asarray(result['sequence_xy_m'], dtype=float)
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                distance = np.linalg.norm(points[i] - points[j])
                self.assertGreaterEqual(distance, min_sep_m - 1e-6)

    def test_dominant_direction_follows_wind_rose_not_hardcoded_westerly(self):
        # Rosa de vientos totalmente dominada por el norte (0°): la
        # dirección dominante reportada debe reflejar eso, no el placeholder
        # 270° (oeste) que usaba la implementación anterior sin importar el
        # viento real del sitio.
        ws = np.full(500, 9.0)
        wd = np.full(500, 0.0)
        result = optimize_layout_greedy_vertex(
            self.polygon, self.turbine, ws=ws, wd=wd, min_spacing_d=3.0)
        self.assertLess(abs(result['dominant_dir_deg'] - 0.0), 15.0)
        self.assertNotAlmostEqual(result['dominant_dir_deg'], 270.0, delta=15.0)

    def test_seed_vertex_is_an_actual_polygon_vertex(self):
        result = optimize_layout_greedy_vertex(
            self.polygon, self.turbine, min_spacing_d=3.0)
        seed_lat, seed_lon = result['seed_vertex_latlon']
        poly_lats = [p['lat'] for p in self.polygon]
        poly_lons = [p['lon'] for p in self.polygon]
        self.assertTrue(min(poly_lats) - 1e-6 <= seed_lat <= max(poly_lats) + 1e-6)
        self.assertTrue(min(poly_lons) - 1e-6 <= seed_lon <= max(poly_lons) + 1e-6)
        # Debe coincidir (dentro de tolerancia de proyección) con alguno de
        # los 4 vértices declarados, no un punto interior arbitrario.
        distances = [
            math.hypot(seed_lat - p['lat'], seed_lon - p['lon'])
            for p in self.polygon
        ]
        self.assertLess(min(distances), 1e-3)

    def test_sequence_is_nested_prefix_valid_for_every_n(self):
        # La ventaja central de este motor: una sola pasada de crecimiento
        # produce una secuencia anidada -- sequence[:n] debe ser exactamente
        # el prefijo de la secuencia completa para cualquier n, sin
        # necesidad de repetir la búsqueda por N (a diferencia de
        # optimize_layout_structured).
        result = optimize_layout_greedy_vertex(
            self.polygon, self.turbine, min_spacing_d=3.0)
        full_sequence = result['sequence_xy_m']
        self.assertGreaterEqual(len(full_sequence), 3)
        half_n = len(full_sequence) // 2
        # Re-running with a cap must reproduce the same prefix deterministically.
        capped = optimize_layout_greedy_vertex(
            self.polygon, self.turbine, min_spacing_d=3.0, n_max_cap=half_n)
        self.assertEqual(capped['sequence_xy_m'], full_sequence[:half_n])

    def test_respects_exclusion_polygons(self):
        # Zona de exclusión que cubre la mitad este del polígono: ninguna
        # turbina colocada debe caer dentro de ella.
        exclusion = [
            {'lat': 16.500, 'lon': -94.995}, {'lat': 16.500, 'lon': -94.990},
            {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -94.995},
        ]
        result = optimize_layout_greedy_vertex(
            self.polygon, self.turbine, min_spacing_d=3.0,
            exclusion_polygons=[exclusion])
        for lat, lon in result['sequence_latlon']:
            self.assertFalse(-94.995 <= lon <= -94.990 and 16.500 <= lat <= 16.510)

    def test_raises_when_polygon_has_no_room(self):
        tiny_polygon = [
            {'lat': 16.5000, 'lon': -95.0000}, {'lat': 16.5000, 'lon': -94.99995},
            {'lat': 16.50005, 'lon': -94.99995}, {'lat': 16.50005, 'lon': -95.0000},
        ]
        with self.assertRaises(ValueError):
            optimize_layout_greedy_vertex(tiny_polygon, self.turbine, min_spacing_d=3.0)


if __name__ == '__main__':
    unittest.main()

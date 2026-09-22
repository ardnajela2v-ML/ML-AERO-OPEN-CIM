import unittest
from unittest.mock import Mock, patch

import numpy as np

from core.osm_infrastructure import (
    _parse_overpass_elements,
    feature_to_exclusion_polygon,
    fetch_overpass_features,
)


class ParseOverpassElementsTests(unittest.TestCase):
    """Parseo de una respuesta Overpass sintética -- nunca llama al servicio
    real en tests (ver plan de Fase 2b)."""

    def test_parses_power_line_way_as_line_feature(self):
        elements = [{
            'type': 'way', 'id': 111,
            'tags': {'power': 'line'},
            'geometry': [{'lat': 16.50, 'lon': -95.00}, {'lat': 16.51, 'lon': -95.00}],
        }]
        features = _parse_overpass_elements(elements)
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]['category'], 'power_line')
        self.assertEqual(features[0]['geometry_type'], 'line')
        self.assertEqual(features[0]['osm_id'], 'way/111')

    def test_parses_wind_turbine_node_as_point_feature(self):
        elements = [{
            'type': 'node', 'id': 222,
            'tags': {'generator:source': 'wind', 'name': 'Aero 3'},
            'lat': 16.52, 'lon': -94.98,
        }]
        features = _parse_overpass_elements(elements)
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]['category'], 'wind_turbine')
        self.assertEqual(features[0]['geometry_type'], 'point')
        self.assertEqual(features[0]['name'], 'Aero 3')

    def test_parses_substation_and_road(self):
        elements = [
            {'type': 'node', 'id': 1, 'tags': {'power': 'substation'}, 'lat': 16.5, 'lon': -95.0},
            {'type': 'way', 'id': 2, 'tags': {'highway': 'primary'},
             'geometry': [{'lat': 16.5, 'lon': -95.0}, {'lat': 16.5, 'lon': -94.9}]},
        ]
        features = _parse_overpass_elements(elements)
        categories = {f['category'] for f in features}
        self.assertEqual(categories, {'substation', 'road'})

    def test_ignores_elements_without_relevant_tags(self):
        elements = [{'type': 'node', 'id': 9, 'tags': {'shop': 'bakery'}, 'lat': 16.5, 'lon': -95.0}]
        self.assertEqual(_parse_overpass_elements(elements), [])

    def test_ignores_way_with_fewer_than_two_points(self):
        elements = [{'type': 'way', 'id': 3, 'tags': {'power': 'line'},
                    'geometry': [{'lat': 16.5, 'lon': -95.0}]}]
        self.assertEqual(_parse_overpass_elements(elements), [])

    def test_parses_residential_building_node_as_receptor(self):
        elements = [{'type': 'node', 'id': 42, 'tags': {'building': 'house'},
                    'lat': 16.505, 'lon': -95.002}]
        features = _parse_overpass_elements(elements)
        self.assertEqual(len(features), 1)
        f = features[0]
        self.assertEqual(f['category'], 'residential_building')
        self.assertEqual(f['kind'], 'receptor')
        self.assertEqual(f['receptor_type'], 'vivienda')
        self.assertEqual(f['receptor_point'], [16.505, -95.002])

    def test_parses_school_way_footprint_as_receptor_at_its_centroid(self):
        # Cuadrado ~ (16.500..16.501, -95.001..-95.000): centroide esperado
        # en el punto medio, no en el primer vértice del anillo.
        ring = [{'lat': 16.500, 'lon': -95.001}, {'lat': 16.501, 'lon': -95.001},
                {'lat': 16.501, 'lon': -95.000}, {'lat': 16.500, 'lon': -95.000},
                {'lat': 16.500, 'lon': -95.001}]
        elements = [{'type': 'way', 'id': 43, 'tags': {'amenity': 'school', 'name': 'Escuela X'},
                    'geometry': ring}]
        features = _parse_overpass_elements(elements)
        self.assertEqual(len(features), 1)
        f = features[0]
        self.assertEqual(f['category'], 'school')
        self.assertEqual(f['kind'], 'receptor')
        self.assertEqual(f['receptor_type'], 'escuela')
        self.assertAlmostEqual(f['receptor_point'][0], 16.5005, places=3)
        self.assertAlmostEqual(f['receptor_point'][1], -95.0005, places=3)

    def test_exclusion_kind_features_have_no_receptor_fields(self):
        elements = [{'type': 'node', 'id': 44, 'tags': {'power': 'tower'}, 'lat': 16.5, 'lon': -95.0}]
        f = _parse_overpass_elements(elements)[0]
        self.assertEqual(f['kind'], 'exclusion')
        self.assertIsNone(f['receptor_type'])
        self.assertIsNone(f['receptor_point'])


class FetchOverpassFeaturesTests(unittest.TestCase):
    def test_degrades_gracefully_on_network_failure(self):
        import requests
        with patch('core.osm_infrastructure.requests.post',
                  side_effect=requests.exceptions.Timeout('boom')):
            result = fetch_overpass_features((16.0, -95.0, 16.1, -94.9), use_cache=False)
        self.assertFalse(result['ok'])
        self.assertIn('error', result)
        self.assertEqual(result['features'], [])

    def test_returns_parsed_features_on_success(self):
        fake_response = Mock()
        fake_response.raise_for_status = Mock()
        fake_response.json = Mock(return_value={'elements': [
            {'type': 'node', 'id': 5, 'tags': {'power': 'tower'}, 'lat': 16.5, 'lon': -95.0},
        ]})
        with patch('core.osm_infrastructure.requests.post', return_value=fake_response):
            result = fetch_overpass_features((16.0, -95.0, 16.1, -94.9), use_cache=False)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['features']), 1)
        self.assertEqual(result['features'][0]['category'], 'power_tower')

    def test_receptor_candidates_beyond_receptor_max_radius_are_dropped(self):
        # bbox centrado en (16.5, -95.0). Una vivienda a ~200 m (debe quedarse)
        # y otra a ~3 km (debe filtrarse) -- una torre eléctrica (kind
        # 'exclusion') a la misma distancia lejana NO debe filtrarse: el
        # radio propio de receptores no debe afectar otras categorías.
        near_house = {'type': 'node', 'id': 1, 'tags': {'building': 'house'}, 'lat': 16.5015, 'lon': -95.0005}
        far_house  = {'type': 'node', 'id': 2, 'tags': {'building': 'house'}, 'lat': 16.53, 'lon': -95.0}
        far_tower  = {'type': 'node', 'id': 3, 'tags': {'power': 'tower'}, 'lat': 16.53, 'lon': -95.0}
        fake_response = Mock()
        fake_response.raise_for_status = Mock()
        fake_response.json = Mock(return_value={'elements': [near_house, far_house, far_tower]})
        with patch('core.osm_infrastructure.requests.post', return_value=fake_response):
            result = fetch_overpass_features((16.48, -95.02, 16.52, -94.98), use_cache=False)
        ids = {f['osm_id'] for f in result['features']}
        self.assertEqual(ids, {'node/1', 'node/3'})


class FeatureToExclusionPolygonTests(unittest.TestCase):
    def test_point_buffer_produces_roughly_circular_polygon_of_expected_radius(self):
        polygon = feature_to_exclusion_polygon([[16.5, -95.0]], 'point', buffer_m=100.0)
        self.assertGreaterEqual(len(polygon), 8)  # shapely aproxima el círculo con varios vértices
        lats = [p['lat'] for p in polygon]
        lons = [p['lon'] for p in polygon]
        from core.farm import _latlon_to_xy
        xs, ys = _latlon_to_xy(lats, lons, 16.5, -95.0)
        radii = [float(np.hypot(x, y)) for x, y in zip(xs, ys)]
        for r in radii:
            self.assertAlmostEqual(r, 100.0, delta=2.0)

    def test_line_buffer_covers_a_corridor_around_the_line(self):
        polygon = feature_to_exclusion_polygon(
            [[16.500, -95.000], [16.510, -95.000]], 'line', buffer_m=50.0)
        from shapely.geometry import Polygon as ShapelyPolygon
        from core.farm import _latlon_to_xy
        lats = [p['lat'] for p in polygon]
        lons = [p['lon'] for p in polygon]
        xs, ys = _latlon_to_xy(lats, lons, 16.505, -95.000)
        shape = ShapelyPolygon(zip(xs, ys))
        # Un corredor de 50m de radio sobre ~1.1km de línea: área esperada
        # aprox. 2*50*1110 + pi*50^2 (cápsula) -- tolerancia amplia, solo
        # para detectar errores gruesos de escala (p. ej. metros vs. grados).
        expected_area = 2 * 50.0 * 1110.0 + np.pi * 50.0 ** 2
        self.assertAlmostEqual(shape.area, expected_area, delta=expected_area * 0.15)

    def test_raises_for_non_positive_buffer(self):
        with self.assertRaises(ValueError):
            feature_to_exclusion_polygon([[16.5, -95.0]], 'point', buffer_m=0.0)


if __name__ == '__main__':
    unittest.main()

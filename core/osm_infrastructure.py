"""Restricciones de infraestructura automáticas vía OpenStreetMap (Overpass API).

Open Infrastructure Map (openinframap.org) es solo un visor especializado
de tags de OpenStreetMap -- este módulo consulta los mismos datos
directamente (`power=line`/`minor_line`/`tower`/`substation`,
`generator:source=wind`, `highway=*`), sin depender del visor.

No introduce un mecanismo nuevo de exclusión: convierte cada feature de OSM
(línea o punto) en un polígono con buffer -- mismo formato que un
`exclusion_polygon` dibujado a mano en el mapa (`core.farm_mcda.
effective_park_area`, `core.farm._point_allowed_xy` ya lo consumen tal
cual). El usuario decide, por objeto individual, si se agrega como
exclusión o se descarta (ver diseño acordado: "seleccionar o excluir un
objeto, buscado automáticamente en un radio alrededor del sitio").

Riesgo documentado: la cobertura de OSM en sitios rurales mexicanos puede
estar incompleta (líneas de transmisión menores o caminos de terracería mal
etiquetados) -- es una ayuda de screening, no sustituye el levantamiento de
restricciones real de un estudio de factibilidad definitivo.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from typing import Dict, List, Optional, Tuple

import requests
from shapely.geometry import LineString, Point, Polygon

from core.farm import _latlon_to_xy, _xy_to_latlon

# Los receptores (vivienda/escuela) son mucho más densos en OSM que
# infraestructura (líneas, subestaciones): un radio de exclusión de 2 km
# sobre un poblado puede traer cientos de nodos building=house. Se acotan a
# un radio propio, más chico, medido desde el centro del bbox consultado --
# coherente con que el propio shadow flicker ya corta su influencia a ~10D
# (≈1-1.5 km para turbinas típicas, ver core.shadow_flicker).
RECEPTOR_MAX_RADIUS_KM = 1.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
# Overpass exige identificar al cliente (rechaza con 406 sin un User-Agent
# explícito) -- ver https://wiki.openstreetmap.org/wiki/Overpass_API#Introduction.
_REQUEST_HEADERS = {'User-Agent': 'WindProPlatform-thesis/1.0 (prefeasibility screening)'}
_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', '.cache', 'osm_infrastructure')
_DISK_LOCK = threading.Lock()
os.makedirs(_CACHE_DIR, exist_ok=True)

# Categoría -> (filtros Overpass QL, tipo de geometría, buffer por defecto en
# metros). El buffer por defecto es un valor de screening razonable, no un
# retiro normativo -- el usuario lo puede ajustar por objeto en la UI antes
# de aceptarlo como exclusión.
CATEGORY_SPECS = {
    'power_line': {
        'filters': ['way["power"~"^(line|minor_line|cable)$"]'],
        'geometry_type': 'line', 'default_buffer_m': 50.0,
        'label': 'Línea de transmisión/distribución', 'kind': 'exclusion',
    },
    'power_tower': {
        'filters': ['node["power"~"^(tower|pole)$"]'],
        'geometry_type': 'point', 'default_buffer_m': 15.0,
        'label': 'Torre/poste eléctrico', 'kind': 'exclusion',
    },
    'substation': {
        'filters': ['node["power"="substation"]', 'way["power"="substation"]'],
        'geometry_type': 'mixed', 'default_buffer_m': 100.0,
        'label': 'Subestación (informativo -- no se recomienda excluir, útil para costo de interconexión)',
        'kind': 'exclusion',
    },
    'wind_turbine': {
        'filters': ['node["generator:source"="wind"]', 'way["generator:source"="wind"]'],
        'geometry_type': 'mixed', 'default_buffer_m': 0.0,
        'label': 'Aerogenerador existente (parque vecino -- informativo, estela no modelada internamente)',
        'kind': 'exclusion',
    },
    'road': {
        'filters': [
            'way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]',
        ],
        'geometry_type': 'line', 'default_buffer_m': 30.0,
        'label': 'Carretera principal', 'kind': 'exclusion',
    },
    # Receptores sensibles (ruido/shadow flicker) -- a diferencia de las
    # categorías anteriores, NO se convierten en polígono de exclusión con
    # buffer: alimentan directamente la lista de receptores (MCDA.receptors
    # en el frontend, receptor_points en core.acoustic_noise/shadow_flicker)
    # con su punto (centroide del footprint si es un way) y un tipo/periodo
    # por defecto que el usuario puede editar antes de aceptar. Acotadas a
    # RECEPTOR_MAX_RADIUS_KM independientemente del radio general de la
    # búsqueda -- ver fetch_overpass_features.
    'residential_building': {
        'filters': [
            'node["building"~"^(house|residential|detached|semidetached_house|terrace|apartments|bungalow)$"]',
            'way["building"~"^(house|residential|detached|semidetached_house|terrace|apartments|bungalow)$"]',
        ],
        'geometry_type': 'mixed', 'default_buffer_m': 0.0,
        'label': 'Vivienda (receptor sensible)', 'kind': 'receptor', 'receptor_type': 'vivienda',
    },
    'school': {
        'filters': [
            'node["amenity"~"^(school|kindergarten)$"]',
            'way["amenity"~"^(school|kindergarten)$"]',
        ],
        'geometry_type': 'mixed', 'default_buffer_m': 0.0,
        'label': 'Escuela (receptor sensible)', 'kind': 'receptor', 'receptor_type': 'escuela',
    },
}
DEFAULT_CATEGORIES = tuple(CATEGORY_SPECS.keys())


def _cache_key(bbox: Tuple[float, float, float, float], categories: Tuple[str, ...]) -> str:
    rounded = tuple(round(float(v), 3) for v in bbox)
    digest = hashlib.sha1(f'{rounded}|{sorted(categories)}'.encode('utf-8')).hexdigest()[:16]
    return f'overpass_{digest}.json'


def _read_cache(bbox, categories) -> Optional[dict]:
    path = os.path.join(_CACHE_DIR, _cache_key(bbox, categories))
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(bbox, categories, payload: dict) -> None:
    path = os.path.join(_CACHE_DIR, _cache_key(bbox, categories))
    try:
        with _DISK_LOCK:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
    except Exception:
        pass


def _build_overpass_query(bbox: Tuple[float, float, float, float],
                          categories: Tuple[str, ...], timeout: int) -> str:
    south, west, north, east = bbox
    bbox_str = f'{south},{west},{north},{east}'
    clauses = []
    for category in categories:
        spec = CATEGORY_SPECS.get(category)
        if not spec:
            continue
        for filt in spec['filters']:
            clauses.append(f'{filt}({bbox_str});')
    body = '\n  '.join(clauses)
    return f'[out:json][timeout:{timeout}];\n(\n  {body}\n);\nout geom;'


def _category_for_tags(tags: Dict[str, str]) -> Optional[str]:
    power = tags.get('power')
    if power in ('line', 'minor_line', 'cable'):
        return 'power_line'
    if power in ('tower', 'pole'):
        return 'power_tower'
    if power == 'substation':
        return 'substation'
    if tags.get('generator:source') == 'wind':
        return 'wind_turbine'
    if tags.get('highway') in ('motorway', 'trunk', 'primary', 'secondary', 'tertiary'):
        return 'road'
    if tags.get('building') in ('house', 'residential', 'detached', 'semidetached_house',
                                'terrace', 'apartments', 'bungalow'):
        return 'residential_building'
    if tags.get('amenity') in ('school', 'kindergarten'):
        return 'school'
    return None


def _receptor_point(coords_latlon: List[List[float]], geometry_type: str) -> List[float]:
    """Punto representativo (lat/lon) de un feature receptor: el nodo mismo,
    o el centroide de su footprint si es un way (building/amenity cerrado)
    -- promedio simple de vértices como respaldo si shapely no puede
    construir el polígono (footprint degenerado)."""
    if geometry_type == 'point' or len(coords_latlon) < 3:
        return list(coords_latlon[0])
    lats = [p[0] for p in coords_latlon]
    lons = [p[1] for p in coords_latlon]
    lat0, lon0 = sum(lats) / len(lats), sum(lons) / len(lons)
    try:
        xs, ys = _latlon_to_xy(lats, lons, lat0, lon0)
        centroid = Polygon(zip(xs, ys)).centroid
        out_lat, out_lon = _xy_to_latlon([centroid.x], [centroid.y], lat0, lon0)
        return [out_lat[0], out_lon[0]]
    except Exception:
        return [lat0, lon0]


def _parse_overpass_elements(elements: List[Dict]) -> List[Dict]:
    features = []
    for el in elements:
        tags = el.get('tags') or {}
        category = _category_for_tags(tags)
        if category is None:
            continue
        spec = CATEGORY_SPECS[category]
        if el.get('type') == 'node':
            if el.get('lat') is None or el.get('lon') is None:
                continue
            coords = [[float(el['lat']), float(el['lon'])]]
            geometry_type = 'point'
        elif el.get('type') == 'way':
            geom = el.get('geometry') or []
            coords = [[float(p['lat']), float(p['lon'])] for p in geom if 'lat' in p and 'lon' in p]
            if len(coords) < 2:
                continue
            geometry_type = 'point' if len(coords) == 1 else 'line'
        else:
            continue
        kind = spec.get('kind', 'exclusion')
        features.append({
            'osm_id': f"{el.get('type')}/{el.get('id')}",
            'category': category,
            'label': spec['label'],
            'geometry_type': geometry_type,
            'coords': coords,
            'default_buffer_m': spec['default_buffer_m'],
            'name': tags.get('name'),
            'tags': tags,
            'kind': kind,
            'receptor_type': spec.get('receptor_type'),
            'receptor_point': _receptor_point(coords, geometry_type) if kind == 'receptor' else None,
        })
    return features


def fetch_overpass_features(bbox: Tuple[float, float, float, float],
                            categories: Tuple[str, ...] = DEFAULT_CATEGORIES,
                            timeout: int = 25, use_cache: bool = True) -> Dict:
    """Consulta Overpass API por infraestructura dentro de `bbox`
    (south, west, north, east). Degrada a `{'ok': False, 'error': ...}` ante
    cualquier falla de red/timeout -- NUNCA debe bloquear el análisis manual
    del usuario (las exclusiones dibujadas a mano siguen funcionando igual).
    """
    categories = tuple(c for c in categories if c in CATEGORY_SPECS) or DEFAULT_CATEGORIES
    if use_cache:
        cached = _read_cache(bbox, categories)
        if cached is not None:
            return cached
    query = _build_overpass_query(bbox, categories, timeout)
    try:
        response = requests.post(OVERPASS_URL, data={'data': query}, timeout=timeout,
                                 headers=_REQUEST_HEADERS)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        return {'ok': False, 'error': str(exc), 'features': []}
    features = _parse_overpass_elements(data.get('elements', []))
    # Receptores acotados a RECEPTOR_MAX_RADIUS_KM desde el centro del bbox,
    # independientemente de qué tan grande sea el radio general de la
    # búsqueda -- evita que un radio de exclusión de varios km inunde la
    # lista de revisión con cientos de viviendas.
    center_lat = (bbox[0] + bbox[2]) / 2.0
    center_lon = (bbox[1] + bbox[3]) / 2.0
    features = [
        f for f in features
        if f['kind'] != 'receptor'
        or _haversine_km(center_lat, center_lon, f['receptor_point'][0], f['receptor_point'][1])
        <= RECEPTOR_MAX_RADIUS_KM
    ]
    result = {'ok': True, 'features': features, 'bbox': list(bbox), 'categories': list(categories)}
    if use_cache:
        _write_cache(bbox, categories, result)
    return result


def feature_to_exclusion_polygon(coords_latlon: List[List[float]], geometry_type: str,
                                 buffer_m: float) -> List[Dict[str, float]]:
    """Convierte un feature de OSM (punto o línea, lat/lon) en un polígono
    de exclusión (lista de {'lat','lon'}) con el buffer dado, en el MISMO
    formato que ya consumen core.farm_mcda.effective_park_area y
    core.farm._point_allowed_xy para exclusiones dibujadas a mano.

    La proyección usa como origen local el centroide del propio feature
    (irrelevante para el resultado final, que vuelve a lat/lon) -- consistente
    con el resto del código (ver core.farm._latlon_to_xy).
    """
    if buffer_m <= 0:
        raise ValueError('buffer_m debe ser mayor que cero para construir un polígono de exclusión.')
    lats = [p[0] for p in coords_latlon]
    lons = [p[1] for p in coords_latlon]
    lat0 = sum(lats) / len(lats)
    lon0 = sum(lons) / len(lons)
    xs, ys = _latlon_to_xy(lats, lons, lat0, lon0)
    if geometry_type == 'point' or len(coords_latlon) == 1:
        shape = Point(xs[0], ys[0])
    else:
        shape = LineString(list(zip(xs, ys)))
    buffered: Polygon = shape.buffer(float(buffer_m))
    if buffered.is_empty:
        raise ValueError('El buffer no produjo un polígono válido.')
    ext_xs, ext_ys = zip(*buffered.exterior.coords)
    out_lats, out_lons = _xy_to_latlon(list(ext_xs), list(ext_ys), lat0, lon0)
    return [{'lat': la, 'lon': lo} for la, lo in zip(out_lats, out_lons)]

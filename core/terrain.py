"""Real elevation (DEM) and land cover (ESA WorldCover) sampling.

Phase 2 — per-site enrichment for Kriging Clásico:
    * `get_elevations_batch(latlons)` → altitudes (m) vía OpenTopoData (SRTM 30m)
    * `get_landcover_class(lat, lon)` → clase WorldCover 2021 (10..100)
    * `class_to_z0(cls)` → longitud de rugosidad (m, Davenport)
    * `enrich_sites(sites)` → lista enriquecida con elevation_m / landcover_* / z0

Resultados cacheados a disco (≈1 km grid) para minimizar llamadas a las APIs
públicas y evitar rate-limit en uso normal.
"""

from __future__ import annotations

import json
import math
import os
import threading
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Optional, Tuple

import requests

try:  # Pillow es requerido sólo para WorldCover
    from PIL import Image
    _PIL_OK = True
except Exception:  # pragma: no cover
    _PIL_OK = False


# ── Tabla Davenport (ESA WorldCover → z0 [m]) ────────────────────────────────
ROUGHNESS_BY_WORLDCOVER = {
    10:  0.80,   # Tree cover
    20:  0.10,   # Shrubland
    30:  0.03,   # Grassland
    40:  0.10,   # Cropland
    50:  1.00,   # Built-up
    60:  0.005,  # Bare/sparse vegetation
    70:  0.001,  # Snow/ice
    80:  0.0002, # Permanent water
    90:  0.05,   # Herbaceous wetland
    95:  0.50,   # Mangroves
    100: 0.03,   # Moss/lichen
}

LANDCOVER_LABELS = {
    10:  'Bosque',
    20:  'Matorral',
    30:  'Pastizal',
    40:  'Cultivo',
    50:  'Urbano',
    60:  'Suelo desnudo',
    70:  'Nieve/hielo',
    80:  'Agua',
    90:  'Humedal',
    95:  'Manglar',
    100: 'Musgo/líquen',
}

# Colores RGB de la paleta oficial ESA WorldCover (rendered tiles)
_LANDCOVER_RGB = {
    10:  (0,   100, 0),
    20:  (255, 187, 34),
    30:  (255, 255, 76),
    40:  (240, 150, 255),
    50:  (250, 0,   0),
    60:  (180, 180, 180),
    70:  (240, 240, 240),
    80:  (0,   100, 200),
    90:  (0,   150, 160),
    95:  (0,   207, 117),
    100: (250, 230, 160),
}


def class_to_z0(cls: Optional[int]) -> Optional[float]:
    if cls is None:
        return None
    return ROUGHNESS_BY_WORLDCOVER.get(int(cls), 0.05)


# ── Caché en disco (≈ 1 km de resolución) ────────────────────────────────────
_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', '.cache', 'terrain')
os.makedirs(_CACHE_DIR, exist_ok=True)
_DISK_LOCK = threading.Lock()


def _cache_key(lat: float, lon: float, kind: str) -> str:
    lat_r = round(float(lat), 3)
    lon_r = round(float(lon), 3)
    return f'{kind}_{lat_r:+08.3f}_{lon_r:+09.3f}.json'


def _read_cache(lat: float, lon: float, kind: str) -> Optional[dict]:
    path = os.path.join(_CACHE_DIR, _cache_key(lat, lon, kind))
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(lat: float, lon: float, kind: str, payload: dict) -> None:
    path = os.path.join(_CACHE_DIR, _cache_key(lat, lon, kind))
    try:
        with _DISK_LOCK:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
    except Exception:
        pass


# ── Altitud: OpenTopoData srtm30m (batch hasta 100 puntos por GET) ───────────
_OPENTOPODATA_URL = 'https://api.opentopodata.org/v1/srtm30m'
_OPEN_ELEVATION_URL = 'https://api.open-elevation.com/api/v1/lookup'


def get_elevations_batch(latlons: Iterable[Tuple[float, float]],
                         timeout: int = 8) -> List[Optional[float]]:
    """Devuelve una lista paralela a latlons con altitudes en metros (None si falla)."""
    latlons = list(latlons)
    out: List[Optional[float]] = [None] * len(latlons)

    todo: List[int] = []
    for i, (lat, lon) in enumerate(latlons):
        cached = _read_cache(lat, lon, 'elev')
        if cached and cached.get('elevation_m') is not None:
            out[i] = float(cached['elevation_m'])
        else:
            todo.append(i)
    if not todo:
        return out

    for chunk_start in range(0, len(todo), 100):
        chunk = todo[chunk_start:chunk_start + 100]
        locs = '|'.join(
            f'{latlons[i][0]:.5f},{latlons[i][1]:.5f}' for i in chunk
        )

        # Primer intento: OpenTopoData SRTM 30 m
        elevs = _query_opentopodata(locs, timeout)
        # Fallback: Open-Elevation
        if elevs is None:
            elevs = _query_open_elevation(locs, timeout)
        if elevs is None:
            continue

        for j, el in enumerate(elevs):
            if el is None:
                continue
            idx = chunk[j]
            out[idx] = float(el)
            _write_cache(latlons[idx][0], latlons[idx][1], 'elev',
                         {'elevation_m': float(el)})
    return out


def _query_opentopodata(locs: str, timeout: int) -> Optional[List[Optional[float]]]:
    try:
        r = requests.get(_OPENTOPODATA_URL, params={'locations': locs},
                         timeout=timeout)
        if not r.ok:
            return None
        data = r.json()
        return [
            (float(res['elevation']) if res and res.get('elevation') is not None else None)
            for res in data.get('results', [])
        ]
    except Exception:
        return None


def _query_open_elevation(locs: str, timeout: int) -> Optional[List[Optional[float]]]:
    try:
        # Open-Elevation usa "lat,lon|lat,lon" igual con "|" pero a través de POST
        # con JSON es más fiable.
        items = []
        for pair in locs.split('|'):
            lat_s, lon_s = pair.split(',')
            items.append({'latitude': float(lat_s), 'longitude': float(lon_s)})
        r = requests.post(_OPEN_ELEVATION_URL, json={'locations': items},
                          timeout=timeout)
        if not r.ok:
            return None
        data = r.json()
        return [
            (float(res['elevation']) if res and res.get('elevation') is not None else None)
            for res in data.get('results', [])
        ]
    except Exception:
        return None


# ── Cobertura del suelo: WorldCover WMTS, decodificado por color de píxel ────
_WMTS_TEMPLATE = (
    'https://services.terrascope.be/wmts/v2?layer=WORLDCOVER_2021_MAP'
    '&style=default&tilematrixset=EPSG:3857'
    '&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image/png'
    '&TileMatrix=EPSG:3857:{z}&TileCol={x}&TileRow={y}'
)

_TILE_CACHE: dict = {}
_TILE_LOCK = threading.Lock()
_TILE_CACHE_MAX = 400


def _latlon_to_tile_pixel(lat: float, lon: float, z: int) -> Tuple[int, int, int, int]:
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    y_arg = math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))
    y = (1.0 - math.log(y_arg) / math.pi) / 2.0 * n
    tx, ty = int(x), int(y)
    px = max(0, min(255, int((x - tx) * 256)))
    py = max(0, min(255, int((y - ty) * 256)))
    return tx, ty, px, py


def _fetch_landcover_tile(z: int, x: int, y: int, timeout: int = 4):
    if not _PIL_OK:
        return None
    key = (z, x, y)
    with _TILE_LOCK:
        if key in _TILE_CACHE:
            return _TILE_CACHE[key]
    url = _WMTS_TEMPLATE.format(z=z, x=x, y=y)
    img = None
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert('RGB')
    except Exception:
        img = None
    with _TILE_LOCK:
        _TILE_CACHE[key] = img
        if len(_TILE_CACHE) > _TILE_CACHE_MAX:
            _TILE_CACHE.pop(next(iter(_TILE_CACHE)))
    return img


def _nearest_landcover_class(rgb: Tuple[int, int, int]) -> Tuple[Optional[int], float]:
    best_cls, best_d = None, 1.0e18
    for cls, c in _LANDCOVER_RGB.items():
        d = (rgb[0] - c[0]) ** 2 + (rgb[1] - c[1]) ** 2 + (rgb[2] - c[2]) ** 2
        if d < best_d:
            best_d, best_cls = d, cls
    return best_cls, math.sqrt(best_d)


def _sample_pixel(z: int, tx: int, ty: int, px: int, py: int):
    """Devuelve el RGB del píxel (px,py) del tile (tx,ty,z), saltando a tiles
    vecinos cuando px/py salen del rango [0,256)."""
    ntx, nty, qx, qy = tx, ty, px, py
    if qx < 0:
        ntx -= 1; qx += 256
    elif qx >= 256:
        ntx += 1; qx -= 256
    if qy < 0:
        nty -= 1; qy += 256
    elif qy >= 256:
        nty += 1; qy -= 256
    img = _fetch_landcover_tile(z, ntx, nty)
    if img is None:
        return None
    try:
        return img.getpixel((qx, qy))[:3]
    except Exception:
        return None


def get_landcover(lat: float, lon: float, z: int = 12, window: int = 5) -> dict:
    """Devuelve {'class', 'z0', 'label'} usando la **clase modal** dentro de una
    ventana de ``window × window`` píxeles alrededor del punto. A z=12 la
    ventana de 5 px equivale a ~50 m, suficiente para promediar la
    contaminación de píxeles aislados (p. ej. una plaza pavimentada en una
    ciudad clasificada como urbana)."""
    cached = _read_cache(lat, lon, 'lc')
    if cached and cached.get('class') is not None:
        return cached

    tx, ty, px, py = _latlon_to_tile_pixel(lat, lon, z)
    img = _fetch_landcover_tile(z, tx, ty)
    if img is None:
        return {'class': None, 'z0': None, 'label': None}

    half = window // 2
    counts: dict = {}
    for dy in range(-half, half + 1):
        for dx in range(-half, half + 1):
            rgb = _sample_pixel(z, tx, ty, px + dx, py + dy)
            if rgb is None:
                continue
            cls, dist = _nearest_landcover_class(rgb)
            if dist > 60:
                continue
            counts[cls] = counts.get(cls, 0) + 1
    if not counts:
        return {'class': None, 'z0': None, 'label': None}

    cls = max(counts.items(), key=lambda kv: kv[1])[0]
    total = sum(counts.values())
    purity = counts[cls] / total if total else 0.0
    z0 = class_to_z0(cls)
    label = LANDCOVER_LABELS.get(cls)
    payload = {
        'class': int(cls) if cls is not None else None,
        'z0': z0,
        'label': label,
        'purity': round(purity, 2),
    }
    _write_cache(lat, lon, 'lc', payload)
    return payload


# ── Enriquecedor de lista de sitios ───────────────────────────────────────────
def enrich_sites(sites: List[dict], landcover_zoom: int = 11,
                 landcover_window: int = 7) -> List[dict]:
    """Devuelve una nueva lista con elevation_m / landcover_class / landcover_label / z0.

    Por defecto se muestrea WorldCover a z=11 con ventana de 7×7 píxeles ≈
    700 m × 700 m, suficiente para representar el "footprint" típico de un
    anemómetro y robusto contra anomalías de un único pixel (plazas, edificios
    aislados)."""
    if not sites:
        return []
    latlons = []
    for s in sites:
        try:
            latlons.append((float(s['lat']), float(s['lon'])))
        except Exception:
            latlons.append((float('nan'), float('nan')))

    elevations = get_elevations_batch(latlons)

    # WorldCover en paralelo (la caché de tiles en memoria comparte trabajo
    # entre sitios cercanos). El cuello de botella es la red, así que un pool
    # de 8 workers da una mejora notable sin saturar al servidor de tiles.
    def _lc_for(idx: int):
        ll = latlons[idx]
        if math.isnan(ll[0]) or math.isnan(ll[1]):
            return {'class': None, 'z0': None, 'label': None, 'purity': None}
        return get_landcover(ll[0], ll[1], z=landcover_zoom, window=landcover_window)

    with ThreadPoolExecutor(max_workers=8) as pool:
        landcovers = list(pool.map(_lc_for, range(len(sites))))

    # Fallback z₀ por sitio: si WorldCover no devolvió clase, estimamos
    # rugosidad desde la pendiente local del DEM en un parche 3×3 de ±0.005°
    # (~550 m) alrededor del sitio. Esto integra topografía aunque WMTS falle.
    def _z0_from_local_dem(lat: float, lon: float) -> Optional[float]:
        if math.isnan(lat) or math.isnan(lon):
            return None
        try:
            return estimate_dem_slope_z0(lat, lon)
        except Exception:
            return None

    out: List[dict] = []
    for s, ll, el, lc in zip(sites, latlons, elevations, landcovers):
        if math.isnan(ll[0]) or math.isnan(ll[1]):
            out.append({**s, 'elevation_m': None, 'landcover_class': None,
                        'landcover_label': None, 'z0': None,
                        'z0_source': None, 'landcover_purity': None})
            continue
        z0_lc = lc.get('z0')
        if z0_lc is not None:
            z0 = round(float(z0_lc), 4)
            z0_source = 'worldcover'
        else:
            z0_dem = _z0_from_local_dem(ll[0], ll[1])
            z0 = round(float(z0_dem), 4) if z0_dem is not None else None
            z0_source = 'dem_slope' if z0_dem is not None else None
        out.append({
            **s,
            'elevation_m': round(float(el), 1) if el is not None else None,
            'landcover_class': lc.get('class'),
            'landcover_label': lc.get('label'),
            'landcover_purity': lc.get('purity'),
            'z0': z0,
            'z0_source': z0_source,
        })
    return out


# ─── Grid sampling rápido (Fase 3) ────────────────────────────────────────────
# Terrarium DEM tiles: la elevación se codifica en (R, G, B) como
#     elev_m = R*256 + G + B/256 - 32768
# Servidor sin rate-limit (S3) y caché local en memoria → muy rápido.
_TERRARIUM_TEMPLATE = (
    'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
)
_DEM_TILE_CACHE: dict = {}
_DEM_TILE_LOCK = threading.Lock()
_DEM_TILE_MAX = 200


def _fetch_dem_tile(z: int, x: int, y: int, timeout: int = 4):
    if not _PIL_OK:
        return None
    key = (z, x, y)
    with _DEM_TILE_LOCK:
        if key in _DEM_TILE_CACHE:
            return _DEM_TILE_CACHE[key]
    url = _TERRARIUM_TEMPLATE.format(z=z, x=x, y=y)
    img = None
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert('RGB')
    except Exception:
        img = None
    with _DEM_TILE_LOCK:
        _DEM_TILE_CACHE[key] = img
        if len(_DEM_TILE_CACHE) > _DEM_TILE_MAX:
            _DEM_TILE_CACHE.pop(next(iter(_DEM_TILE_CACHE)))
    return img


def _terrarium_pixel_elev(img, px: int, py: int) -> Optional[float]:
    try:
        r, g, b = img.getpixel((px, py))[:3]
    except Exception:
        return None
    elev = r * 256 + g + b / 256.0 - 32768.0
    # En agua, Terrarium devuelve exactamente -32768 (0,0,0); reemplazamos por 0.
    if elev <= -10000.0:
        return 0.0
    return float(elev)


def _zoom_for_resolution(bounds: Tuple[float, float, float, float],
                         nx: int, ny: int) -> int:
    """Elige un zoom Web-Mercator que dé al menos 1 píxel del tile por celda
    del grid solicitado (clamp 6..14 para evitar tile-storm)."""
    lat_min, lon_min, lat_max, lon_max = bounds
    # Píxeles por grado a zoom z: 256 * 2^z / 360
    deg_per_cell = max(
        (lon_max - lon_min) / max(nx, 1),
        (lat_max - lat_min) / max(ny, 1),
    )
    if deg_per_cell <= 0:
        return 10
    needed_pix_per_deg = 1.0 / deg_per_cell
    z = max(0, int(math.ceil(math.log2(needed_pix_per_deg * 360.0 / 256.0))))
    return max(6, min(z, 13))


def _required_tiles(lats: List[float], lons: List[float], z: int):
    """Conjunto de tiles (z,x,y) necesarios para muestrear todas las celdas."""
    tiles = set()
    for lat in lats:
        for lon in lons:
            tx, ty, _, _ = _latlon_to_tile_pixel(lat, lon, z)
            tiles.add((z, tx, ty))
    return tiles


def _cap_zoom_for_tile_budget(lats: List[float], lons: List[float], z: int,
                              max_tiles: int = 96) -> int:
    while z > 4 and len(_required_tiles(lats, lons, z)) > max_tiles:
        z -= 1
    return z


def _prefetch_tiles(tiles, fetcher, max_workers: int = 12):
    """Descarga en paralelo todos los tiles requeridos (todos van a caché)."""
    if not tiles:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(lambda t: fetcher(t[0], t[1], t[2]), tiles))


def sample_grid_elevation(lats: List[float], lons: List[float]) -> List[List[float]]:
    """Devuelve elevación (m) en una grilla rectangular definida por las
    coordenadas en `lats` (y) y `lons` (x). Pre-descarga los tiles Terrarium
    necesarios en paralelo y luego muestrea desde caché."""
    if not lats or not lons:
        return [[]]
    bounds = (min(lats), min(lons), max(lats), max(lons))
    # Para DEM no necesitamos resolución súper fina; cap a z=10 reduce
    # drásticamente tiles a descargar sin perder señal a escala kriging.
    z = _cap_zoom_for_tile_budget(lats, lons, min(_zoom_for_resolution(bounds, len(lons), len(lats)), 10))

    tiles = _required_tiles(lats, lons, z)
    _prefetch_tiles(tiles, _fetch_dem_tile)

    out: List[List[float]] = [[0.0] * len(lons) for _ in lats]
    for j, lat in enumerate(lats):
        for i, lon in enumerate(lons):
            tx, ty, px, py = _latlon_to_tile_pixel(lat, lon, z)
            img = _fetch_dem_tile(z, tx, ty)
            el = _terrarium_pixel_elev(img, px, py) if img is not None else None
            # Clamp por debajo de 0 (Terrarium puede devolver -10 a -100 m
            # en mar abierto donde el batímetro penetra al lecho marino).
            out[j][i] = max(0.0, float(el)) if el is not None else 0.0
    return out


def _z0_from_slope_deg(slope_deg: float) -> float:
    """Mapeo Davenport aproximado a partir de la pendiente del terreno.

    Cuando WorldCover no responde, usamos la rugosidad del DEM como proxy
    físico de z₀. Terrenos planos → pastizal abierto (~0.03 m), terrenos
    accidentados → bosque/rugoso (~0.5 m). Es una aproximación pero captura
    el efecto principal (montañas frenan más que llanuras) y es 100% local.
    """
    s = max(0.0, float(slope_deg))
    # Curva suave: 0° → 0.01, 1° → 0.03, 3° → 0.10, 8° → 0.35, 15°+ → 0.80
    return 0.01 + 0.79 * (1.0 - math.exp(-s / 5.5))


def _slope_grid_deg(elev: List[List[float]], lats: List[float],
                    lons: List[float]) -> List[List[float]]:
    """Pendiente en grados por celda mediante diferencias centradas."""
    ny = len(elev)
    nx = len(elev[0]) if ny else 0
    if ny < 2 or nx < 2:
        return [[0.0] * nx for _ in range(ny)]
    # Aproximación: 1° lat ≈ 111 km; 1° lon ≈ 111 km · cos(lat)
    out = [[0.0] * nx for _ in range(ny)]
    for j in range(ny):
        lat_c = lats[j] if 0 <= j < len(lats) else 0.0
        dy_m = 111000.0 * (lats[1] - lats[0]) if len(lats) > 1 else 1.0
        dx_m = 111000.0 * math.cos(math.radians(lat_c)) * (lons[1] - lons[0]) if len(lons) > 1 else 1.0
        for i in range(nx):
            jm = max(0, j - 1); jp = min(ny - 1, j + 1)
            im = max(0, i - 1); ip = min(nx - 1, i + 1)
            dz_dy = (elev[jp][i] - elev[jm][i]) / max(1e-6, (jp - jm) * dy_m)
            dz_dx = (elev[j][ip] - elev[j][im]) / max(1e-6, (ip - im) * dx_m)
            slope = math.degrees(math.atan(math.sqrt(dz_dx * dz_dx + dz_dy * dz_dy)))
            out[j][i] = slope
    return out


def estimate_dem_slope_z0(lat: float, lon: float, delta_deg: float = 0.005) -> float:
    """Estimate the classic WindProPlatform roughness fallback from DEM slope."""

    d = max(1.0e-5, float(delta_deg))
    patch_lats = [float(lat) - d, float(lat), float(lat) + d]
    patch_lons = [float(lon) - d, float(lon), float(lon) + d]
    patch_elev = sample_grid_elevation(patch_lats, patch_lons)
    slope_deg = _slope_grid_deg(patch_elev, patch_lats, patch_lons)
    return float(_z0_from_slope_deg(slope_deg[1][1]))


def sample_grid_z0(lats: List[float], lons: List[float],
                   elev_grid: Optional[List[List[float]]] = None) -> List[List[float]]:
    """Devuelve z₀ (m) por celda. Estrategia:

      1) Si WorldCover responde, mapea clase → z₀ (Davenport).
      2) Para celdas donde el tile WMTS no llegó, usa rugosidad DEM:
         pendiente local del DEM → z₀ (mapa físico, siempre disponible).

    Esto garantiza que la corrección física **siempre integre rugosidad**
    aunque Terrascope/WMTS no responda.
    """
    if not lats or not lons:
        return [[]]
    bounds = (min(lats), min(lons), max(lats), max(lons))
    z = _cap_zoom_for_tile_budget(lats, lons, min(_zoom_for_resolution(bounds, len(lons), len(lats)), 10))

    tiles = _required_tiles(lats, lons, z)
    _prefetch_tiles(tiles, _fetch_landcover_tile)

    # Default por celda — relleno desde DEM si lo tenemos; si no, 0.05 m.
    if elev_grid is None:
        elev_grid = sample_grid_elevation(lats, lons)
    slope_deg = _slope_grid_deg(elev_grid, lats, lons)

    out: List[List[float]] = [
        [_z0_from_slope_deg(slope_deg[j][i]) for i in range(len(lons))]
        for j in range(len(lats))
    ]

    # Si WorldCover sí responde para una celda, **lo preferimos** porque
    # discrimina cubierta (urbano, agua, bosque…) mejor que la pendiente.
    for j, lat in enumerate(lats):
        for i, lon in enumerate(lons):
            tx, ty, px, py = _latlon_to_tile_pixel(lat, lon, z)
            img = _fetch_landcover_tile(z, tx, ty)
            if img is None:
                continue
            try:
                rgb = img.getpixel((px, py))[:3]
            except Exception:
                continue
            cls, dist = _nearest_landcover_class(rgb)
            if dist > 60 or cls is None:
                continue
            z0_lc = class_to_z0(cls)
            if z0_lc is not None:
                out[j][i] = float(z0_lc)
    return out


def sample_grid_worldcover_z0(lats: List[float], lons: List[float],
                              fallback_z0: float = 0.03) -> List[List[float]]:
    """Return WorldCover-derived roughness without a topographic proxy.

    The anchored MCP downscaling pipeline uses this stricter sampler. Missing
    or undecodable WorldCover cells receive the single project-reference
    roughness; DEM slope is never converted into ``z0`` here. This keeps
    roughness correction and terrain screening as independent physical inputs.
    """
    if not lats or not lons:
        return [[]]
    fallback = max(1.0e-4, float(fallback_z0))
    bounds = (min(lats), min(lons), max(lats), max(lons))
    z = _cap_zoom_for_tile_budget(
        lats,
        lons,
        min(_zoom_for_resolution(bounds, len(lons), len(lats)), 10),
    )
    tiles = _required_tiles(lats, lons, z)
    _prefetch_tiles(tiles, _fetch_landcover_tile)

    out: List[List[float]] = [[fallback] * len(lons) for _ in lats]
    for j, lat in enumerate(lats):
        for i, lon in enumerate(lons):
            tx, ty, px, py = _latlon_to_tile_pixel(lat, lon, z)
            img = _fetch_landcover_tile(z, tx, ty)
            if img is None:
                continue
            try:
                rgb = img.getpixel((px, py))[:3]
            except Exception:
                continue
            cls, dist = _nearest_landcover_class(rgb)
            if dist > 60 or cls is None:
                continue
            z0_lc = class_to_z0(cls)
            if z0_lc is not None:
                out[j][i] = float(z0_lc)
    return out

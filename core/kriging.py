"""
Kriging interpolation and multi-site wind turbine analysis.
Ported and adapted from ajuste_aerogeneradores.py (MATLAB -> Python),
decoupled from tkinter for use in the AERO by OPEN CIM REST API.

Optimizaciones 2026-04:
  - read_wind_file usa pandas (skiprows automático) en lugar de regex sobre
    el contenido completo → 20-50x más rápido en archivos de 16-18 MB.
  - _SITE_STATS_CACHE cachea stats por (path, mtime, year, altura, z0) en memoria;
    la segunda llamada es O(1) sin releer el disco.
  - parallel_read_sites() usa ThreadPoolExecutor para leer N sitios en paralelo.
"""
import os
import re
import math
import logging
import warnings
import threading
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit, lsq_linear
from scipy.stats import weibull_min as wbl_dist
from scipy.interpolate import interp1d
from core.cost_model import CostModel

warnings.filterwarnings('ignore')
log = logging.getLogger(__name__)

# ── In-memory site-stats cache (thread-safe) ──────────────────────────────────
# Key: (abs_path, mtime_int, year_or_None) → dict returned by _compute_site_stats
_SITE_STATS_CACHE: Dict[Tuple, dict] = {}
_CACHE_LOCK = threading.Lock()

# ── Physical / Engineering constants ──────────────────────────────────────────
Z_REF = 100.0   # m — MERRA-2 reference height for wind speed extrapolation
Z_TGT = 80.0    # m — typical hub height target
Z0    = 0.03    # m — surface roughness length (open terrain)
R_ESP = 287.05  # J/(kg·K)
MAX_W = 25.0    # m/s — turbine cut-out assumption


def _require_project_economics(price_usd_kwh, anios):
    """Validate economic inputs supplied by the project configuration."""
    if price_usd_kwh is None or anios is None:
        raise ValueError(
            'price_usd_kwh y anios deben venir de la configuración del proyecto')
    price = float(price_usd_kwh)
    years = int(anios)
    if not np.isfinite(price) or price < 0:
        raise ValueError('price_usd_kwh debe ser un número no negativo')
    if not 5 <= years <= 40:
        raise ValueError('anios debe estar entre 5 y 40')
    return price, years


# ── Coordinate parsing — tolerates many naming variants:
#   _N16,500_W94,375 · _N16.500_W95.000 · ERA5N24.250_W110.500 · N26_W98.750
_COORD_RE = re.compile(r'(N|S)(\d+(?:[.,]\d+)?)[_\s]*W(\d+(?:[.,]\d+)?)')


def parse_coords_from_filename(fname: str):
    """Return (lat, lon) or None if not parsable."""
    m = _COORD_RE.search(fname)
    if not m:
        return None
    hemi = m.group(1).upper()
    lat = float(m.group(2).replace(',', '.')) * (-1 if hemi == 'S' else 1)
    lon = -float(m.group(3).replace(',', '.'))  # W => negative
    return (lat, lon)


# ── Weibull 4-gaussian power-curve fit helpers ────────────────────────────────

def gauss4(x, a1, b1, c1, a2, b2, c2, a3, b3, c3, a4, b4, c4):
    e = 1e-6
    return (a1 * np.exp(-((x - b1) / (c1 + e)) ** 2) +
            a2 * np.exp(-((x - b2) / (c2 + e)) ** 2) +
            a3 * np.exp(-((x - b3) / (c3 + e)) ** 2) +
            a4 * np.exp(-((x - b4) / (c4 + e)) ** 2))


def fit_power_curve_gauss4(x_curve, y_curve, p_nom_w):
    """Fit 4-gaussian to (v, P) data; return smooth curve clipped to [0, Pnom]."""
    x = np.asarray(x_curve, dtype=float)
    y = np.asarray(y_curve, dtype=float)
    try:
        xm, ym = float(np.mean(x)), float(np.max(y))
        p0 = [ym * .6, xm * .5, 3,
              ym * .8, xm,      4,
              ym * .5, xm * 1.5, 5,
              ym * .2, xm * 2,   4]
        popt, _ = curve_fit(gauss4, x, y, p0=p0, maxfev=15000)
        yf = gauss4(x, *popt)
    except Exception:
        k = max(3, len(y) // 5)
        yf = np.convolve(y, np.ones(k) / k, mode='same')
    return np.clip(yf, 0, p_nom_w)


# ── Turbine filename parser: name_pNNNN_rRR.csv ───────────────────────────────
_TURB_RE = re.compile(r'_p(\d+)_r([\d.]+)', re.IGNORECASE)


def parse_turbine_filename(fn: str):
    """Return (name, rotor_diameter_m, power_nominal_kw).

    El sufijo `_r<valor>` del filename es el RADIO del rotor en metros — se
    multiplica por 2 para devolver diámetro. Default 80 m si no hay patrón.
    """
    base = os.path.splitext(fn)[0]
    m = _TURB_RE.search(base)
    if m:
        return (base, float(m.group(2)) * 2.0, float(m.group(1)))
    return (base, 80.0, 1000.0)


# ── Kriging core (ordinary Kriging on a normalised variogram) ─────────────────

def _haversine_pairwise_km(lons1, lats1, lons2, lats2):
    """Matriz de distancias great-circle (km) entre dos sets de puntos (lon, lat).

    Reemplaza cdist sobre (lon, lat) — en México 1° lat ≈ 111 km pero 1° lon
    varía de ~96 km (norte 26°N) a ~106 km (sur 17°N), por lo que tratar lat/lon
    como cartesiano introduce anisotropía artificial de ~7-15%.
    """
    R = 6371.0
    lat1 = np.radians(np.asarray(lats1, dtype=float))[:, None]
    lat2 = np.radians(np.asarray(lats2, dtype=float))[None, :]
    lon1 = np.radians(np.asarray(lons1, dtype=float))[:, None]
    lon2 = np.radians(np.asarray(lons2, dtype=float))[None, :]
    dphi = lat2 - lat1
    dlam = lon2 - lon1
    a = np.sin(dphi/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlam/2)**2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _variogram_correlation(distance_km, model, range_km):
    """Correlation associated with the supported stationary variograms."""
    distance = np.asarray(distance_km, dtype=float)
    scale = max(float(range_km), 1e-9)
    if model == 'gaussian':
        return np.exp(-np.square(distance / scale))
    return np.exp(-distance / scale)


def _empirical_variogram(distances, semivariances, max_bins=12):
    """Bin a variogram cloud into distance-quantile classes."""
    distances = np.asarray(distances, dtype=float).ravel()
    semivariances = np.asarray(semivariances, dtype=float).ravel()
    valid = np.isfinite(distances) & np.isfinite(semivariances) & (distances > 0)
    distances, semivariances = distances[valid], semivariances[valid]
    if not len(distances):
        return np.asarray([]), np.asarray([]), np.asarray([])
    n_bins = max(3, min(int(max_bins), int(np.sqrt(len(distances)))))
    edges = np.unique(np.quantile(distances, np.linspace(0.0, 1.0, n_bins + 1)))
    if len(edges) < 2:
        return (np.asarray([float(np.mean(distances))]),
                np.asarray([float(np.mean(semivariances))]),
                np.asarray([len(distances)], dtype=float))
    bin_index = np.clip(np.digitize(distances, edges[1:-1], right=True), 0, len(edges) - 2)
    rows = []
    for index in range(len(edges) - 1):
        keep = bin_index == index
        if np.any(keep):
            rows.append((float(np.mean(distances[keep])),
                         float(np.mean(semivariances[keep])), int(np.sum(keep))))
    return tuple(np.asarray(column, dtype=float) for column in zip(*rows))


def _fit_variogram_wls(bin_distance, bin_gamma, bin_count, model, range_km,
                       fallback_variance):
    """Fit nugget and partial sill with Cressie-style iterated WLS.

    Reference: Cressie (1985), Mathematical Geology 17, 563–586,
    https://doi.org/10.1007/BF01032109
    """
    h = np.asarray(bin_distance, dtype=float)
    empirical = np.asarray(bin_gamma, dtype=float)
    counts = np.asarray(bin_count, dtype=float)
    basis = 1.0 - _variogram_correlation(h, model, range_km)
    design = np.column_stack([np.ones_like(basis), basis])
    total_variance = max(float(fallback_variance), 1e-12)
    params = np.asarray([0.0, total_variance], dtype=float)
    for _ in range(4):
        fitted = np.maximum(design @ params, total_variance * 1e-9)
        weights = np.sqrt(np.maximum(counts, 1.0)) / fitted
        solved = lsq_linear(design * weights[:, None], empirical * weights,
                            bounds=(0.0, np.inf), method='trf')
        params = solved.x
    nugget, partial_sill = float(params[0]), float(params[1])
    if partial_sill <= total_variance * 1e-9:
        partial_sill = total_variance * 1e-6
    fitted = nugget + partial_sill * basis
    cressie_weights = counts / np.maximum(np.square(fitted), total_variance ** 2 * 1e-12)
    wls = float(np.sum(cressie_weights * np.square(empirical - fitted)) /
                np.sum(cressie_weights))
    return nugget, partial_sill, wls


def _loo_rmse_from_covariance(x, y, z, model, range_km, nugget, partial_sill):
    """Fast ordinary-Kriging LOO RMSE from one augmented-system inverse."""
    distance = _haversine_pairwise_km(x, y, x, y)
    covariance = partial_sill * _variogram_correlation(distance, model, range_km)
    covariance += nugget * np.eye(len(z))
    covariance += max(nugget + partial_sill, 1.0) * 1e-10 * np.eye(len(z))
    system = np.empty((len(z) + 1, len(z) + 1), dtype=float)
    system[:-1, :-1] = covariance
    system[:-1, -1] = 1.0
    system[-1, :-1] = 1.0
    system[-1, -1] = 0.0
    try:
        inverse = np.linalg.inv(system)
    except np.linalg.LinAlgError:
        inverse = np.linalg.pinv(system)
    alpha = inverse @ np.r_[z, 0.0]
    diagonal = np.diag(inverse)[:-1]
    valid = np.isfinite(diagonal) & (np.abs(diagonal) > 1e-12)
    if not np.any(valid):
        return float('inf'), 0
    residuals = alpha[:-1][valid] / diagonal[valid]
    return float(np.sqrt(np.mean(np.square(residuals)))), int(np.sum(valid))


def fit_variogram_loo(lons, lats, values, requested='auto', structure=None):
    """Fit nugget/sill/range and select model/range by leave-one-node-out.

    The empirical semivariogram and returned variance use squared input units.
    Passing ``structure`` retains its model/range and only refits nugget/sill,
    which lets related map layers share one validated spatial structure.
    """
    x = np.asarray(lons, dtype=float).ravel()
    y = np.asarray(lats, dtype=float).ravel()
    z = np.asarray(values, dtype=float).ravel()
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[valid], y[valid], z[valid]
    if len(z) < 3:
        raise ValueError(f'Kriging requires >= 3 finite sites (got {len(z)}).')
    distance_matrix = _haversine_pairwise_km(x, y, x, y)
    pair_index = np.triu_indices(len(z), 1)
    pair_distance = distance_matrix[pair_index]
    pair_gamma = 0.5 * np.square(z[:, None] - z[None, :])[pair_index]
    bin_h, bin_gamma, bin_count = _empirical_variogram(pair_distance, pair_gamma)
    sample_variance = max(float(np.var(z, ddof=1)), 1e-12)
    models = ([str(structure['model'])] if structure else
              ([requested] if requested in {'exponential', 'gaussian'} else ['exponential', 'gaussian']))
    positive_distance = pair_distance[pair_distance > 0]
    median_distance = float(np.median(positive_distance)) if len(positive_distance) else 50.0
    if structure:
        ranges = [float(structure['range_km'])]
    else:
        lower = max(float(np.min(positive_distance)) * 0.5, 0.1) if len(positive_distance) else 1.0
        upper_range = max(float(np.max(positive_distance)) * 2.0, lower) if len(positive_distance) else 300.0
        ranges = sorted({float(np.clip(median_distance * factor, lower, upper_range))
                         for factor in (0.25, 0.5, 1.0, 2.0, 4.0)})
    candidates = []
    for candidate_model in models:
        for range_km in ranges:
            nugget, partial_sill, wls = _fit_variogram_wls(
                bin_h, bin_gamma, bin_count, candidate_model, range_km, sample_variance)
            if structure:
                loo_rmse, loo_n = None, 0
            else:
                loo_rmse, loo_n = _loo_rmse_from_covariance(
                    x, y, z, candidate_model, range_km, nugget, partial_sill)
            candidates.append({
                'model': candidate_model, 'range_km': float(range_km),
                'nugget': nugget, 'partial_sill': partial_sill,
                'sill': nugget + partial_sill, 'wls_score': wls,
                'loo_rmse': loo_rmse, 'loo_n': loo_n,
            })
    best = (candidates[0] if structure else
            min(candidates, key=lambda row: (row['loo_rmse'], row['wls_score'])))
    return {
        **best, 'n': int(len(z)),
        'selection': 'fixed_structure_wls' if structure else 'leave_one_node_out',
        'variance_units': 'squared_input_units',
        'empirical_variogram': {
            'distance_km': bin_h.tolist(), 'semivariance': bin_gamma.tolist(),
            'pair_count': bin_count.astype(int).tolist(),
        },
        'candidates': candidates if not structure else [],
    }


def kriging_interp(x, y, z, xq, yq, model='exponential', rng=50.0,
                   variogram=None):
    """Ordinary Kriging interpolation. Returns (z_interp, variance).

    Coordenadas (x=lon, y=lat) en grados; las distancias internas se calculan
    con Haversine (km), por lo que `rng` debe interpretarse en KILÓMETROS
    (default 50 km, escala típica de un cluster de sitios MERRA-2/ERA5).

    Implementa Ordinary Kriging ampliando el sistema con un multiplicador de
    Lagrange para imponer Σwᵢ=1 y evitar sesgo hacia cero fuera del rango local.
    """
    x = np.asarray(x).ravel()
    y = np.asarray(y).ravel()
    z = np.asarray(z, dtype=float).ravel()
    valid = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x = x[valid]
    y = y[valid]
    z = z[valid]
    if len(z) < 3:
        raise ValueError(f'Kriging requires >= 3 finite sites (got {len(z)}).')
    # Distancias Haversine en km — 1°lat ≠ 1°lon en México (ver _haversine_pairwise_km).
    d  = _haversine_pairwise_km(x, y, x, y)
    dq = _haversine_pairwise_km(x, y, xq.ravel(), yq.ravel())
    if variogram is None:
        if rng == 0:
            rng = np.max(d) / 3 if d.size else 1.0
        nugget, partial_sill = 0.0, 1.0
    else:
        model = str(variogram['model'])
        rng = float(variogram['range_km'])
        nugget = max(0.0, float(variogram.get('nugget', 0.0)))
        partial_sill = max(
            1e-12,
            float(variogram.get('partial_sill', float(variogram.get('sill', 1.0)) - nugget)),
        )
    total_sill = nugget + partial_sill
    C = partial_sill * _variogram_correlation(d, model, rng)
    C += nugget * np.eye(len(x))
    C += max(total_sill, 1.0) * 1e-10 * np.eye(len(x))
    k = partial_sill * _variogram_correlation(dq, model, rng)
    n = len(x)
    K = np.empty((n + 1, n + 1), dtype=float)
    K[:n, :n] = C
    K[:n, n] = 1.0
    K[n, :n] = 1.0
    K[n, n] = 0.0
    rhs = np.vstack([k, np.ones((1, k.shape[1]), dtype=float)])
    try:
        sol = np.linalg.solve(K, rhs)
    except np.linalg.LinAlgError:
        sol = np.linalg.lstsq(K, rhs, rcond=None)[0]
    w = sol[:n, :]
    lagrange = sol[n, :]
    z_hat = (w.T @ z).reshape(xq.shape)
    var = np.maximum(0.0, total_sill - np.sum(w * k, axis=0) - lagrange).reshape(xq.shape)
    return z_hat, var


# Aliases de compatibilidad.
simple_kriging = kriging_interp
interpolate_wind = kriging_interp


def kriging_grid(lons, lats, values, n=80, margin_frac=0.15, model='exponential',
                 bounds=None, variogram_structure=None):
    """Build a regular lat/lon grid, interpolate, return dict ready for JSON."""
    la = np.asarray(lats, dtype=float)
    lo = np.asarray(lons, dtype=float)
    va = np.asarray(values, dtype=float)
    if len(la) < 3:
        raise ValueError(f'Kriging requires >= 3 sites (got {len(la)}).')
    if bounds:
        lat_min = float(bounds['lat_min'])
        lat_max = float(bounds['lat_max'])
        lon_min = float(bounds['lon_min'])
        lon_max = float(bounds['lon_max'])
    else:
        lon_margin = max(0.1, (lo.max() - lo.min()) * margin_frac)
        lat_margin = max(0.1, (la.max() - la.min()) * margin_frac)
        lat_min = float(la.min() - lat_margin)
        lat_max = float(la.max() + lat_margin)
        lon_min = float(lo.min() - lon_margin)
        lon_max = float(lo.max() + lon_margin)
    xq, yq = np.meshgrid(
        np.linspace(lon_min, lon_max, n),
        np.linspace(lat_min, lat_max, n))
    variogram = fit_variogram_loo(lo, la, va, requested=model,
                                  structure=variogram_structure)
    zq, var = kriging_interp(lo, la, va, xq, yq, variogram=variogram)
    return {
        'lon_grid': xq.tolist(),
        'lat_grid': yq.tolist(),
        'z':        zq.tolist(),
        'variance': var.tolist(),
        'variance_units': 'squared_input_units',
        'variogram': variogram,
        'bounds': {
            'lat_min': lat_min,
            'lat_max': lat_max,
            'lon_min': lon_min,
            'lon_max': lon_max,
        },
        'z_min': float(np.nanmin(zq)),
        'z_max': float(np.nanmax(zq)),
        'n': n,
        'model': variogram['model'],
    }


# ── Wind file reader — FAST pandas-based version ─────────────────────────────
# Columnas esperadas en archivos Windnavigator (MERRA-2 / ERA5):
#   datetime  WS  WD  WS_corr  T2M  P0M  [otras opcionales]
# Se detecta la fila de cabecera automáticamente buscando la primera fila
# que contenga una fecha ISO válida en la primera columna.

def _find_data_start(path: str) -> int:
    """Retorna el número de líneas de cabecera a saltar antes de los datos.
    Lee sólo las primeras 80 líneas (cabeceras típicas < 30 líneas)."""
    import re as _re
    date_pat = _re.compile(r'^\d{4}-\d{2}-\d{2}')
    with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
        for i, line in enumerate(fh):
            if i > 80:
                break
            if date_pat.match(line.strip()):
                return i
    return 0  # fallback: intentar desde la línea 0


def _parse_header_columns(path: str, data_start_line: int):
    """Lee la fila de encabezado real (justo antes de data_start_line) y
    devuelve (idx, z_ref_m) donde idx mapea 'dt'/'ws'/'wd'/'t2m'/'p0m' a la
    posición de columna 0-indexada, y z_ref_m es la altura de medición del
    viento leída del propio encabezado (p.ej. 'Speed_100m' -> 100.0).

    MERRA-2 trae dos columnas de temperatura (Temperature_10m y
    Temperature_2m); se prioriza siempre la de 2 m, que es la que usa el
    cálculo de densidad del aire.

    Devuelve (None, None) si el encabezado no se puede parsear (formato
    desconocido) — el llamador debe usar un fallback posicional explícito.
    """
    if data_start_line <= 0:
        return None, None
    header_idx = data_start_line - 1
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            for i, line in enumerate(fh):
                if i == header_idx:
                    header_line = line
                    break
            else:
                return None, None
    except OSError:
        return None, None

    cols = [c.strip() for c in re.split(r'\t+', header_line.strip())]
    if len(cols) < 2:
        cols = [c.strip() for c in re.split(r'\s{2,}', header_line.strip())]

    idx: Dict[str, int] = {}
    z_ref = None
    for i, c in enumerate(cols):
        cl = c.lower()
        if cl.startswith('date'):
            idx['dt'] = i
        elif 'speed' in cl:
            idx['ws'] = i
            m = re.search(r'_(\d+)\s*m', c, re.IGNORECASE)
            if m:
                z_ref = float(m.group(1))
        elif 'direction' in cl:
            idx['wd'] = i
        elif 'temperature_2m' in cl.replace(' ', ''):
            idx['t2m'] = i  # siempre gana sobre Temperature_10m
        elif 'temp' in cl and 't2m' not in idx:
            idx['t2m'] = i  # fallback si sólo hay una columna de temperatura
        elif 'pressure' in cl:
            idx['p0m'] = i

    if not {'dt', 'ws', 'wd', 't2m', 'p0m'}.issubset(idx):
        return None, None
    return idx, z_ref


def read_wind_file(path: str, year: Optional[int] = None):
    """
    Lee un archivo Windnavigator (MERRA-2/ERA5) con pandas — 20-50x más rápido
    que la versión regex sobre el contenido completo.

    MERRA-2 y ERA5 tienen distinto número de columnas (6 vs 5: MERRA-2 trae
    Temperature_10m Y Temperature_2m, ERA5 sólo Temperature_2m) y distinta
    altura de referencia de viento (50 m vs 100 m). Las columnas se localizan
    por NOMBRE leyendo el encabezado real del archivo — nunca por posición
    fija — para no mezclar presión con temperatura entre formatos.

    Devuelve dict con claves: name, lat, lon, ws, wd, t2m, p0m, n, year,
    z_ref (altura de medición del viento en m, específica del archivo).
    """
    fname = os.path.basename(path)
    coords = parse_coords_from_filename(fname)
    if coords is None:
        raise ValueError(f'No se pueden parsear coordenadas de: {fname}')
    lat, lon = coords

    skip = _find_data_start(path)
    idx, z_ref = _parse_header_columns(path, skip)

    df = pd.read_csv(
        path, skiprows=skip, sep=r'\s+', header=None,
        na_values=['-', 'nan', 'NaN', ''], on_bad_lines='skip', engine='c',
    )
    df.columns = list(range(df.shape[1]))

    if idx is None:
        # Fallback posicional: el encabezado no se pudo leer. Se infiere el
        # layout por el número de columnas presentes en los datos (ERA5=5,
        # MERRA-2=6) en vez de asumir siempre el layout de 6 columnas.
        log.warning('read_wind_file: encabezado no reconocido en %s, usando fallback posicional', fname)
        if df.shape[1] >= 6:
            idx = {'dt': 0, 'ws': 1, 'wd': 2, 't2m': 4, 'p0m': 5}  # MERRA-2-like
            z_ref = z_ref or 50.0
        else:
            idx = {'dt': 0, 'ws': 1, 'wd': 2, 't2m': 3, 'p0m': 4}  # ERA5-like
            z_ref = z_ref or 100.0

    df = df.rename(columns={idx['dt']: 'dt', idx['ws']: 'ws', idx['wd']: 'wd',
                             idx['t2m']: 't2m', idx['p0m']: 'p0m'})
    df = df[['dt', 'ws', 'wd', 't2m', 'p0m']].copy()
    for c in ('ws', 'wd', 't2m', 'p0m'):
        df[c] = pd.to_numeric(df[c], errors='coerce').astype('float32')

    # Filtrar filas cuya primera columna no parece fecha ISO
    mask_date = df['dt'].astype(str).str.match(r'\d{4}-\d{2}-\d{2}')
    df = df[mask_date].dropna(subset=['ws'])

    if year is not None:
        df = df[df['dt'].astype(str).str[:4] == str(year)]

    return {
        'name': fname,
        'lat':  lat,
        'lon':  lon,
        'ws':   df['ws'].to_numpy(dtype=float),
        'wd':   df['wd'].to_numpy(dtype=float),
        't2m':  df['t2m'].to_numpy(dtype=float),
        'p0m':  df['p0m'].to_numpy(dtype=float),
        'n':    len(df),
        'year': year,
        'z_ref': float(z_ref) if z_ref is not None else Z_REF,
    }


# ── Per-site statistics (Weibull, density, extrapolated wind) ─────────────────

SECTOR_COUNT = 16
SECTOR_DEG = 360.0 / SECTOR_COUNT
SECTOR_LABELS = [f'{int(i * SECTOR_DEG)}°' for i in range(SECTOR_COUNT)]


def _sectorial_stats(ws_hub, wd, rho, n_sectors: int = SECTOR_COUNT):
    """Directional wind statistics by sector for kriging and TOPSIS metadata."""
    wd = np.asarray(wd, dtype=float)
    ws_hub = np.asarray(ws_hub, dtype=float)
    rho = np.asarray(rho, dtype=float)
    if wd.shape != ws_hub.shape:
        wd = np.full_like(ws_hub, np.nan, dtype=float)
    valid = np.isfinite(wd) & np.isfinite(ws_hub)
    if not np.any(valid):
        return {
            'n_sectors': n_sectors,
            'sector_labels': [f'{int(i * 360.0 / n_sectors)}°' for i in range(n_sectors)],
            'sector_frequency_pct': [0.0] * n_sectors,
            'sector_mean_speed': [0.0] * n_sectors,
            'sector_power_density_wm2': [0.0] * n_sectors,
            'sector_energy_pct': [0.0] * n_sectors,
            'dominant_sector_idx': None,
            'dominant_direction_deg': None,
            'dominant_frequency_pct': 0.0,
            'directional_concentration': 0.0,
        }

    wd_v = wd[valid] % 360.0
    ws_v = ws_hub[valid]
    rho_v = rho[valid] if rho.shape == ws_hub.shape else np.full_like(ws_v, float(np.nanmean(rho)))
    sector_deg = 360.0 / n_sectors
    half_sector = sector_deg / 2.0
    idx = ((wd_v + half_sector) % 360.0 / sector_deg).astype(int) % n_sectors
    freq = np.zeros(n_sectors, dtype=float)
    mean_speed = np.zeros(n_sectors, dtype=float)
    power_density = np.zeros(n_sectors, dtype=float)
    energy = np.zeros(n_sectors, dtype=float)
    p_inst = 0.5 * rho_v * np.power(ws_v, 3)
    total_energy = float(np.nansum(p_inst))
    for i in range(n_sectors):
        mask = idx == i
        freq[i] = float(np.sum(mask))
        if np.any(mask):
            mean_speed[i] = float(np.nanmean(ws_v[mask]))
            power_density[i] = float(np.nanmean(p_inst[mask]))
            energy[i] = float(np.nansum(p_inst[mask]))
    freq_pct = freq / max(1.0, float(len(ws_v))) * 100.0
    energy_pct = energy / total_energy * 100.0 if total_energy > 0 else np.zeros(n_sectors)
    dom_idx = int(np.nanargmax(energy_pct if total_energy > 0 else freq_pct))
    return {
        'n_sectors': n_sectors,
        'sector_labels': [f'{int(i * sector_deg)}°' for i in range(n_sectors)],
        'sector_frequency_pct': [float(v) for v in freq_pct],
        'sector_mean_speed': [float(v) for v in mean_speed],
        'sector_power_density_wm2': [float(v) for v in power_density],
        'sector_energy_pct': [float(v) for v in energy_pct],
        'dominant_sector_idx': dom_idx,
        'dominant_direction_deg': float(dom_idx * sector_deg),
        'dominant_frequency_pct': float(freq_pct[dom_idx]),
        'directional_concentration': float((energy_pct[dom_idx] if total_energy > 0 else freq_pct[dom_idx]) / 100.0),
    }

def site_wind_stats(site: dict, hub_height_m: Optional[float] = None,
                    z0: Optional[float] = None):
    """Compute Weibull + derived wind stats for a single site dict.

    hub_height_m: altura del rotor de la PowerCurve activa. Fallback Z_TGT=80 m.
    z0: rugosidad del terreno. Fallback Z0=0.03 (campo abierto/costa).
    Sierra Norte Oaxaca: usar Z0=0.1-0.5.

    La altura de referencia de la extrapolación (z_ref) se toma de
    site['z_ref'] — poblada por read_wind_file a partir del propio
    encabezado del archivo (50 m en MERRA-2, 100 m en ERA5) — nunca de una
    constante fija, porque mezclar ambas alturas invierte el signo de la
    corrección logarítmica.
    """
    z_tgt = float(hub_height_m) if hub_height_m is not None else Z_TGT
    z0_eff = float(z0) if z0 is not None else Z0
    z_ref = float(site.get('z_ref') or Z_REF)
    ws = site['ws']; wd = site.get('wd'); t2m = site['t2m']; p0m = site['p0m']
    if ws.size == 0:
        return None
    wsp = ws * (math.log(z_tgt / z0_eff) / math.log(z_ref / z0_eff))
    T = t2m + 273.15
    P = p0m * 1000.0
    rho = P / (R_ESP * T)
    meanrho = float(np.mean(rho))
    Vmax = float(np.max(ws))
    gust_ms = float(np.nanpercentile(wsp, 99.5)) if wsp.size else Vmax
    power_density_wm2 = float(np.nanmean(0.5 * rho * np.power(wsp, 3))) if wsp.size else 0.0
    try:
        sh, _, sc = wbl_dist.fit(ws, floc=0)
        C, K = float(sc), float(sh)
    except Exception:
        C, K = 6.0, 2.0
    sectorial = _sectorial_stats(wsp, wd, rho) if wd is not None else _sectorial_stats(wsp, [], rho)
    return {
        'v_mean':       float(np.mean(wsp)),
        'v_mean_raw':   float(np.mean(ws)),
        'v_max':        Vmax,
        'gust_ms':      gust_ms,
        'power_density_wm2': power_density_wm2,
        'weibull_C':    C,
        'weibull_K':    K,
        'rho_mean':     meanrho,
        'ws_hub':       wsp,
        'wd':           wd,
        'rho':          rho,
        **sectorial,
    }


def _compute_site_stats_cached(path: str, year: Optional[int] = None,
                                hub_height_m: Optional[float] = None,
                                z0: Optional[float] = None):
    """
    Versión cacheada de (read_wind_file + site_wind_stats).
    La clave de caché es (ruta_absoluta, mtime_entero, year, altura, z0).
    Si el archivo no ha cambiado (mismo mtime), devuelve el resultado previo
    sin releer el disco — esto hace la segunda llamada instantánea.

    NOTA: ws_hub y rho son arrays; se copian para evitar mutaciones externas.
    """
    abs_path = os.path.abspath(path)
    try:
        mtime = int(os.path.getmtime(abs_path))
    except OSError:
        mtime = 0
    cache_key = (abs_path, mtime, year,
                 None if hub_height_m is None else round(float(hub_height_m), 6),
                 None if z0 is None else round(float(z0), 9))
    with _CACHE_LOCK:
        if cache_key in _SITE_STATS_CACHE:
            return _SITE_STATS_CACHE[cache_key]

    # --- Caché miss: leer y calcular ---
    try:
        site  = read_wind_file(path, year=year)
        stats = site_wind_stats(site, hub_height_m=hub_height_m, z0=z0)
    except Exception as exc:
        log.warning('_compute_site_stats_cached: %s — %s', path, exc)
        return None

    if stats is None:
        return None

    # Adjuntar metadatos del archivo para que los llamadores no tengan que releer
    result = {
        **stats,
        '_name': site['name'],
        '_lat':  site['lat'],
        '_lon':  site['lon'],
        '_n':    site['n'],
    }
    with _CACHE_LOCK:
        _SITE_STATS_CACHE[cache_key] = result
    return result


def clear_site_stats_cache():
    """Vacía el caché de stats. Llámalo si los archivos de datos cambian."""
    with _CACHE_LOCK:
        _SITE_STATS_CACHE.clear()
    log.info('site_stats cache cleared')


def parallel_read_sites(paths: List[str], year: Optional[int] = None,
                        hub_height_m: Optional[float] = None,
                        z0: Optional[float] = None,
                        max_workers: int = 6) -> List[Optional[dict]]:
    """
    Lee y calcula stats de una lista de archivos en paralelo (ThreadPool).
    Devuelve lista en el mismo orden que `paths`.
    Usa caché interno, por lo que lecturas repetidas son O(1).
    """
    results = [None] * len(paths)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {
            pool.submit(_compute_site_stats_cached, p, year, hub_height_m, z0): i
            for i, p in enumerate(paths)
        }
        for fut in as_completed(futs):
            idx = futs[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                log.warning('parallel_read_sites idx=%d: %s', idx, exc)
    return results


# ── Energy & economics per turbine × site ─────────────────────────────────────

def turbine_site_economics(ws_hub, rho, curve_x, curve_y, rotor_m, p_nom_kw,
                           demand_mwh=1000, tasa=0.08, price_usd_kwh=None,
                           price_mxn_per_usd=20.0, anios=None, max_w=MAX_W,
                           cost_model: Optional[CostModel] = None):
    """
    Compute per-site × per-turbine technical + economic metrics.
    All inputs are arrays/scalars already read from files.
    """
    price_usd_kwh, anios = _require_project_economics(price_usd_kwh, anios)
    p_nom_w = p_nom_kw * 1000.0
    ps = fit_power_curve_gauss4(curve_x, curve_y * 1000.0, p_nom_w)  # kW -> W
    # interp1d 'linear' para preservar la suavidad del fit gauss4 (antes 'nearest'
    # producía escalones discretos que descartaban la suavización).
    ifn = interp1d(curve_x, ps, kind='linear', bounds_error=False, fill_value=0)
    psit = np.maximum(ifn(ws_hub), 0)
    psit[ws_hub > max_w] = 0

    # ── inferir paso temporal: si ws_hub es Series con DatetimeIndex usa la moda
    # de los deltas; si no, asume 1.0 h (datos horarios MERRA-2/ERA5).
    if isinstance(ws_hub, pd.Series) and isinstance(ws_hub.index, pd.DatetimeIndex) and len(ws_hub) > 1:
        dt_h = ws_hub.index.to_series().diff().mode()[0].total_seconds() / 3600
    else:
        dt_h = 1.0
    n_hours_actual = len(ws_hub) * dt_h

    pt = float(np.sum(psit)) * dt_h                 # Wh per turbine (energy)
    # Potencia cinética: P = ½ ρ A v³, con A = π·(D/2)². rotor_m es DIÁMETRO
    # (consistente con core/energy.py).
    pteo = 0.5 * rho * np.pi * (rotor_m / 2.0) ** 2 * ws_hub ** 3
    cp = np.minimum(np.where(pteo > 0, psit / pteo, 0), 0.593)  # Betz (16/27)
    cp_max  = float(np.max(cp))
    cp_prom = float(np.mean(cp))

    # Velocidad nominal derivada de la curva: primer ws donde P alcanza ≥98% de Pmax.
    # Fallback 12.0 m/s si la curva no permite estimarla.
    p_max_kw = float(np.max(curve_y)) if len(curve_y) > 0 else 0.0
    if p_max_kw > 0:
        idx_rated = int(np.argmax(np.asarray(curve_y) >= 0.98 * p_max_kw))
        rated_v = float(curve_x[idx_rated]) if idx_rated > 0 else 12.0
    else:
        rated_v = 12.0
    n_hours_near_rated = int(np.sum((ws_hub >= 0.9 * rated_v) & (ws_hub <= 1.1 * rated_v)))
    p_max_curve = float(np.max(ps))
    # FC con horas reales del registro (no 8760 fijo); si el archivo cubre <1 año
    # el FC queda correcto en lugar de subestimado.
    fc = pt / (p_max_curve * n_hours_actual) if p_max_curve > 0 and n_hours_actual > 0 else 0

    demand_wh = demand_mwh * 1e6
    n = math.ceil(demand_wh / pt) if pt > 0 else 0
    pan = pt * n                                # Wh/year total park
    pin = p_nom_kw * n                          # kW installed
    sep = 7 * rotor_m                           # 7·D (rotor_m = diámetro)
    area = sep ** 2 * n

    # Modelo económico central WindPro (core/cost_model.py)
    cm_model = cost_model or CostModel.from_design_benchmark()
    cap = cm_model.capex_breakdown(p_nom_kw, n)
    caero = cap['turbines_total_usd']           # USD aerogeneradores
    ctot  = cap['total_capex_usd']              # USD CAPEX total (incluye BOS)
    cm    = cm_model.opex_annual_usd(ctot)      # USD/año OPEX
    inv   = ctot                                # inversión inicial = CAPEX

    ekwh = pan / 1e3
    vtas_mxn = ekwh * (price_usd_kwh * price_mxn_per_usd)  # MXN/año (solo para display)
    vtas = ekwh * price_usd_kwh                              # USD/año (para cálculos financieros)
    flux = vtas - cm                # flujo neto anual = ventas − O&M anual (USD)
    # LCOE = PV(CAPEX + OPEX) / PV(energía), consistente con
    # core/farm_mcda.py:lcoe_usd_kwh (IEA/NREL) — NUNCA (ctot + cm·años) /
    # (ekwh·años) sin descontar, que subestima el LCOE porque el
    # denominador no penaliza la energía de años lejanos igual que el
    # numerador penaliza los costos futuros.
    if tasa > 0:
        annuity_factor = (1 - (1 + tasa) ** -anios) / tasa
        VAN = (flux * annuity_factor) - inv
        VAE = (VAN * tasa / (1 - (1 + tasa) ** -anios))
        pv_costs = ctot + cm * annuity_factor
        pv_energy = ekwh * annuity_factor
    else:
        VAN = 0.0
        VAE = 0.0
        pv_costs = ctot + cm * anios
        pv_energy = ekwh * anios
    lcoe = pv_costs / pv_energy if pv_energy > 0 else 0
    # ROI simple
    roi = (VAN / inv) if inv > 0 else 0

    return {
        'cp_max':               cp_max,
        'cp_prom':              cp_prom,
        'factor_capacidad':     float(fc),
        'horas_cerca_nominal':  n_hours_near_rated,
        'potencia_wh_sitio':    pt,
        'n_aerogeneradores':    n,
        'aep_mwh':              float(pan / 1e6),
        'potencia_nominal_kw':  p_nom_kw,
        'pot_instalada_kw':     pin,
        'area_m2':              float(area),
        'capex_usd':            float(ctot),
        'opex_usd_anual':       float(cm),
        'inversion_total_usd':  float(inv),
        'ventas_mxn_anuales':   float(vtas_mxn),
        'flujo_neto_anual':     float(flux),
        'van_usd':              float(VAN),
        'vae_usd':              float(VAE),
        'lcoe_usd_kwh':         float(lcoe),
        'roi':                  float(roi),
        'capex_breakdown':      {k: float(v) for k, v in cap.items()},
    }


# ── Top-level: analyze all sites × all turbines ───────────────────────────────

def analyze_sites(wind_folder: str, turbines_folder: str, year: int,
                  demand_mwh=1000, tasa=0.08, price_usd_kwh=None,
                  price_mxn_per_usd=20.0, anios=None, max_files=None,
                  filter_filenames: Optional[List[str]] = None,
                  bounds: Optional[dict] = None,
                  turbine_filenames: Optional[List[str]] = None,
                  cost_model: Optional[CostModel] = None):
    """
    Full analysis: read all wind files + turbine csvs, compute metrics,
    return sites list + turbine results matrix suitable for JSON.
    """
    price_usd_kwh, anios = _require_project_economics(price_usd_kwh, anios)
    wind_files = sorted(
        f for f in os.listdir(wind_folder)
        if f.lower().endswith('.txt') and not f.startswith('.')
    )
    if filter_filenames:
        keep = set(filter_filenames)
        wind_files = [f for f in wind_files if f in keep]
    if bounds:
        def _inside_bounds_from_name(fname: str) -> bool:
            coords = parse_coords_from_filename(fname)
            if coords is None:
                return True
            lat, lon = coords
            return (
                float(bounds['lat_min']) <= float(lat) <= float(bounds['lat_max'])
                and float(bounds['lon_min']) <= float(lon) <= float(bounds['lon_max'])
            )
        wind_files = [f for f in wind_files if _inside_bounds_from_name(f)]
    if max_files:
        wind_files = wind_files[:max_files]

    turb_files = sorted(
        f for f in os.listdir(turbines_folder)
        if f.lower().endswith('.csv') and not f.startswith('.')
    )
    if turbine_filenames:
        keep_turbines = set(turbine_filenames)
        turb_files = [f for f in turb_files if f in keep_turbines]
    if not turb_files:
        raise ValueError('Selecciona al menos una turbina válida para el análisis.')

    sites_out = []
    for wf in wind_files:
        path = os.path.join(wind_folder, wf)
        try:
            site = read_wind_file(path, year=year)
        except Exception as e:
            log.warning('analyze_sites: skip wind file %s — %s', wf, e)
            continue
        if bounds:
            if not (float(bounds['lat_min']) <= float(site['lat']) <= float(bounds['lat_max']) and
                    float(bounds['lon_min']) <= float(site['lon']) <= float(bounds['lon_max'])):
                continue
        if site['n'] < 24:
            continue
        stats = site_wind_stats(site)
        if stats is None:
            continue

        turbines_out = []
        for tf in turb_files:
            try:
                dat = pd.read_csv(os.path.join(turbines_folder, tf), header=None).values
                x_c = dat[:, 0].astype(float)
                y_c = dat[:, 1].astype(float)
            except Exception as e:
                log.warning('analyze_sites: skip turbine CSV %s — %s', tf, e)
                continue
            name, rotor, p_nom_kw = parse_turbine_filename(tf)
            econ = turbine_site_economics(
                stats['ws_hub'], stats['rho'], x_c, y_c, rotor, p_nom_kw,
                demand_mwh=demand_mwh, tasa=tasa,
                price_usd_kwh=price_usd_kwh,
                price_mxn_per_usd=price_mxn_per_usd, anios=anios,
                cost_model=cost_model)
            econ['turbina']   = name
            econ['rotor_m']   = rotor
            turbines_out.append(econ)

        sites_out.append({
            'name':        site['name'],
            'lat':         site['lat'],
            'lon':         site['lon'],
            'n_hours':     int(site['n']),
            'v_mean':      stats['v_mean'],
            'v_mean_raw':  stats['v_mean_raw'],
            'v_max':       stats['v_max'],
            'gust_ms':     stats['gust_ms'],
            'power_density_wm2': stats['power_density_wm2'],
            'weibull_C':   stats['weibull_C'],
            'weibull_K':   stats['weibull_K'],
            'rho_mean':    stats['rho_mean'],
            'dominant_direction_deg': stats['dominant_direction_deg'],
            'dominant_frequency_pct': stats['dominant_frequency_pct'],
            'directional_concentration': stats['directional_concentration'],
            'sectorial': {
                'n_sectors': stats['n_sectors'],
                'labels': stats['sector_labels'],
                'frequency_pct': stats['sector_frequency_pct'],
                'mean_speed': stats['sector_mean_speed'],
                'power_density_wm2': stats['sector_power_density_wm2'],
                'energy_pct': stats['sector_energy_pct'],
                'dominant_direction_deg': stats['dominant_direction_deg'],
                'dominant_frequency_pct': stats['dominant_frequency_pct'],
                'directional_concentration': stats['directional_concentration'],
            },
            'turbines':    turbines_out,
        })

    return sites_out


def _grid_sample_value(grid: dict, lat: float, lon: float):
    """Bilinear sample from a kriging grid at one lat/lon."""
    if not grid or not grid.get('bounds') or not grid.get('z'):
        return None
    b = grid['bounds']
    if lat < b['lat_min'] or lat > b['lat_max'] or lon < b['lon_min'] or lon > b['lon_max']:
        return None
    z = grid['z']
    ny = len(z)
    nx = len(z[0]) if ny else 0
    if ny < 2 or nx < 2:
        return None
    fx = (lon - b['lon_min']) / max(1e-12, b['lon_max'] - b['lon_min']) * (nx - 1)
    fy = (lat - b['lat_min']) / max(1e-12, b['lat_max'] - b['lat_min']) * (ny - 1)
    i0 = max(0, min(nx - 2, int(math.floor(fx))))
    j0 = max(0, min(ny - 2, int(math.floor(fy))))
    dx = fx - i0
    dy = fy - j0
    v00, v10 = float(z[j0][i0]), float(z[j0][i0 + 1])
    v01, v11 = float(z[j0 + 1][i0]), float(z[j0 + 1][i0 + 1])
    return v00 * (1 - dx) * (1 - dy) + v10 * dx * (1 - dy) + v01 * (1 - dx) * dy + v11 * dx * dy


def _point_in_polygon(lat: float, lon: float, polygon: List[dict]) -> bool:
    inside = False
    n = len(polygon or [])
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        lati, loni = float(polygon[i]['lat']), float(polygon[i]['lon'])
        latj, lonj = float(polygon[j]['lat']), float(polygon[j]['lon'])
        crosses = ((loni > lon) != (lonj > lon)) and (
            lat < (latj - lati) * (lon - loni) / ((lonj - loni) or 1e-12) + lati
        )
        if crosses:
            inside = not inside
        j = i
    return inside


def _bounds_from_polygon(polygon: List[dict]) -> dict:
    lats = [float(p['lat']) for p in polygon]
    lons = [float(p['lon']) for p in polygon]
    return {
        'lat_min': min(lats), 'lat_max': max(lats),
        'lon_min': min(lons), 'lon_max': max(lons),
    }


def _virtual_weibull_series(mean_speed: float, k: float, n_hours: int = 8760):
    """Deterministic annual wind-speed series from local Weibull parameters."""
    k = max(0.45, float(k) if np.isfinite(k) else 2.0)
    mean_speed = max(0.0, float(mean_speed) if np.isfinite(mean_speed) else 0.0)
    gamma_mean = math.gamma(1.0 + 1.0 / k)
    c = mean_speed / gamma_mean if gamma_mean > 0 else mean_speed
    p = (np.arange(n_hours, dtype=float) + 0.5) / n_hours
    return c * np.power(-np.log1p(-p), 1.0 / k)


def _min_max_grid(z):
    vals = [float(v) for row in z for v in row if np.isfinite(v)]
    if not vals:
        return 0.0, 0.0
    return min(vals), max(vals)


def _legacy_prefit_economics(mean_speed, k, rho_mean, power_interp, p_max_curve,
                             rotor_m, p_nom_kw, demand_mwh, tasa,
                             price_usd_kwh, price_mxn_per_usd, anios,
                             cost_model: Optional[CostModel] = None):
    ws_hub = _virtual_weibull_series(mean_speed, k)
    psit = np.maximum(power_interp(ws_hub), 0)
    psit[ws_hub > MAX_W] = 0
    pt = float(np.sum(psit))
    demand_wh = demand_mwh * 1e6
    n = math.ceil(demand_wh / pt) if pt > 0 else 0
    pan = pt * n

    cm_model = cost_model or CostModel.from_design_benchmark()
    cap = cm_model.capex_breakdown(p_nom_kw, n)
    ctot = cap['total_capex_usd']
    cm   = cm_model.opex_annual_usd(ctot)
    inv  = ctot
    ekwh = pan / 1e3
    vtas = ekwh * price_usd_kwh  # USD/año
    flux = vtas - cm
    if tasa > 0:
        van = (flux * ((1 - (1 + tasa) ** -anios) / tasa)) - inv
    else:
        van = 0.0
    if not np.isfinite(rho_mean) or rho_mean <= 0 or p_max_curve <= 0:
        return float(ctot), float(van)
    return float(ctot), float(van)


def analyze_spatial_topsis_sites(wind_folder: str, turbines_folder: str, year: int,
                                 scope: str,
                                 polygon: Optional[List[dict]] = None,
                                 bounds: Optional[dict] = None,
                                 demand_mwh=1000, tasa=0.08,
                                 price_usd_kwh=None,
                                 price_mxn_per_usd=20.0,
                                 anios=None,
                                 n_grid: int = 80,
                                 model: str = 'exponential',
                                 max_points: int = 72,
                                 filter_filenames: Optional[List[str]] = None,
                                 turbine_filenames: Optional[List[str]] = None,
                                 cost_model: Optional[CostModel] = None):
    """
    Build virtual analysis sites from the kriged geospatial field.

    Stations are only inputs to interpolate wind/resource layers; returned sites
    are coordinates sampled inside the park polygon or exploratory rectangle.
    """
    price_usd_kwh, anios = _require_project_economics(price_usd_kwh, anios)
    scope = 'park' if scope == 'park' else 'exploratory'
    if scope == 'park':
        if not polygon or len(polygon) < 3:
            raise ValueError('El análisis de área seleccionada requiere el polígono cerrado del parque.')
        grid_bounds = _bounds_from_polygon(polygon)
    else:
        if not bounds:
            raise ValueError('La exploración global requiere un área rectangular seleccionada.')
        grid_bounds = bounds

    wind_files = sorted(
        f for f in os.listdir(wind_folder)
        if f.lower().endswith('.txt') and not f.startswith('.')
    )
    if filter_filenames:
        keep = set(filter_filenames)
        wind_files = [f for f in wind_files if f in keep]
    paths = [os.path.join(wind_folder, f) for f in wind_files]
    stats_rows = parallel_read_sites(paths, year=year, max_workers=6)
    rows = [r for r in stats_rows if r and r.get('_lat') is not None and r.get('_lon') is not None]
    if len(rows) < 3:
        raise ValueError(f'Kriging necesita >= 3 estaciones válidas como entrada (hay {len(rows)}).')

    lats = [float(r['_lat']) for r in rows]
    lons = [float(r['_lon']) for r in rows]
    variogram_structure = fit_variogram_loo(
        lons, lats, [float(r.get('v_mean') or 0.0) for r in rows], requested=model)

    def _grid_for(key: str):
        return kriging_grid(lons, lats, [float(r.get(key) or 0.0) for r in rows],
                            n=n_grid, model=model, bounds=grid_bounds,
                            variogram_structure=variogram_structure)

    wind_grid = _grid_for('v_mean')
    gust_grid = _grid_for('gust_ms')
    power_grid = _grid_for('power_density_wm2')
    k_grid = _grid_for('weibull_K')
    rho_grid = _grid_for('rho_mean')
    sector_freq_grids = [
        kriging_grid(lons, lats, [float((r.get('sector_frequency_pct') or [0.0] * SECTOR_COUNT)[i]) for r in rows],
                     n=n_grid, model=model, bounds=grid_bounds,
                     variogram_structure=variogram_structure)
        for i in range(SECTOR_COUNT)
    ]
    sector_speed_grids = [
        kriging_grid(lons, lats, [float((r.get('sector_mean_speed') or [0.0] * SECTOR_COUNT)[i]) for r in rows],
                     n=n_grid, model=model, bounds=grid_bounds,
                     variogram_structure=variogram_structure)
        for i in range(SECTOR_COUNT)
    ]
    sector_energy_grids = [
        kriging_grid(lons, lats, [float((r.get('sector_energy_pct') or [0.0] * SECTOR_COUNT)[i]) for r in rows],
                     n=n_grid, model=model, bounds=grid_bounds,
                     variogram_structure=variogram_structure)
        for i in range(SECTOR_COUNT)
    ]
    elevation_grid = None
    roughness_grid = None
    physical = False

    try:
        from .terrain import enrich_sites, sample_grid_elevation, sample_grid_z0
        site_records = [
            {'filename': r.get('_name') or f'Station {i}', 'lat': lats[i], 'lon': lons[i]}
            for i, r in enumerate(rows)
        ]
        enriched = enrich_sites(site_records)
        z0_site = [float(s['z0']) if s.get('z0') is not None else Z0 for s in enriched]

        def _alpha(z0_local: float) -> float:
            z0_local = max(1e-4, float(z0_local))
            return math.log(Z_REF / Z0) / math.log(Z_REF / z0_local)

        neutral = [float(r.get('v_mean') or 0.0) * _alpha(z0) for r, z0 in zip(rows, z0_site)]
        neutral_grid = kriging_grid(
            lons, lats, neutral, n=n_grid, model=model, bounds=grid_bounds,
            variogram_structure=variogram_structure)
        b = neutral_grid['bounds']
        z = neutral_grid['z']
        ny = len(z)
        nx = len(z[0]) if ny else 0
        grid_lats = [b['lat_min'] + (b['lat_max'] - b['lat_min']) * j / (ny - 1)
                     for j in range(ny)] if ny > 1 else [b['lat_min']]
        grid_lons = [b['lon_min'] + (b['lon_max'] - b['lon_min']) * i / (nx - 1)
                     for i in range(nx)] if nx > 1 else [b['lon_min']]
        elevation = sample_grid_elevation(grid_lats, grid_lons)
        roughness = sample_grid_z0(grid_lats, grid_lons)
        v_real = [[0.0] * nx for _ in range(ny)]
        rho_real = [[0.0] * nx for _ in range(ny)]
        for j in range(ny):
            for i in range(nx):
                z0_p = max(1e-4, float(roughness[j][i]))
                alpha_p = math.log(Z_REF / Z0) / math.log(Z_REF / z0_p)
                v_real[j][i] = float(z[j][i]) / alpha_p if alpha_p else float(z[j][i])
                rho_real[j][i] = 1.225 * math.exp(-max(0.0, float(elevation[j][i])) / 8400.0)
        wind_grid = {
            **neutral_grid,
            'z': v_real,
            'z_min': min(min(row) for row in v_real),
            'z_max': max(max(row) for row in v_real),
            'title': 'Viento real con corrección física',
        }
        rho_grid = {
            **neutral_grid,
            'z': rho_real,
            'z_min': min(min(row) for row in rho_real),
            'z_max': max(max(row) for row in rho_real),
            'title': 'Densidad del aire local',
        }
        elevation_grid = {**neutral_grid, 'z': elevation}
        roughness_grid = {**neutral_grid, 'z': roughness}
        physical = True
    except Exception as exc:
        log.warning('analyze_spatial_topsis_sites: physical correction unavailable — %s', exc)

    b = wind_grid['bounds']
    ny = len(wind_grid['z'])
    nx = len(wind_grid['z'][0]) if ny else 0
    candidates = []
    for j in range(ny):
        lat = b['lat_min'] + (b['lat_max'] - b['lat_min']) * j / max(1, ny - 1)
        for i in range(nx):
            lon = b['lon_min'] + (b['lon_max'] - b['lon_min']) * i / max(1, nx - 1)
            if scope == 'park' and not _point_in_polygon(lat, lon, polygon or []):
                continue
            wind = _grid_sample_value(wind_grid, lat, lon)
            if wind is None or not np.isfinite(wind):
                continue
            gust = _grid_sample_value(gust_grid, lat, lon) or wind * 1.6
            power = _grid_sample_value(power_grid, lat, lon) or 0.0
            score = 0.55 * float(wind) + 0.003 * float(power) + 0.12 * float(gust)
            candidates.append({'lat': lat, 'lon': lon, 'score': score})

    if not candidates:
        raise ValueError('No hay puntos interpolados dentro del área seleccionada.')
    if scope == 'exploratory':
        candidates.sort(key=lambda p: p['score'], reverse=True)
        candidates = candidates[:max_points]
    elif len(candidates) > max_points:
        step = max(1, int(math.ceil(len(candidates) / max_points)))
        candidates = candidates[::step][:max_points]

    turb_files = sorted(
        f for f in os.listdir(turbines_folder)
        if f.lower().endswith('.csv') and not f.startswith('.')
    )
    if turbine_filenames:
        keep_turbines = set(turbine_filenames)
        turb_files = [f for f in turb_files if f in keep_turbines]
    if not turb_files:
        raise ValueError('Selecciona al menos una turbina válida para el análisis TOPSIS.')

    turbines = []
    for tf in turb_files:
        try:
            dat = pd.read_csv(os.path.join(turbines_folder, tf), header=None).values
            turbines.append((tf, dat[:, 0].astype(float), dat[:, 1].astype(float), *parse_turbine_filename(tf)))
        except Exception as exc:
            log.warning('analyze_spatial_topsis_sites: skip turbine %s — %s', tf, exc)

    sites_out = []
    for idx, p in enumerate(candidates, start=1):
        lat, lon = p['lat'], p['lon']
        wind = max(0.0, float(_grid_sample_value(wind_grid, lat, lon) or 0.0))
        k = max(0.45, float(_grid_sample_value(k_grid, lat, lon) or 2.0))
        rho_mean = max(0.5, float(_grid_sample_value(rho_grid, lat, lon) or 1.225))
        sector_frequency = [max(0.0, float(_grid_sample_value(g, lat, lon) or 0.0)) for g in sector_freq_grids]
        sector_speed = [max(0.0, float(_grid_sample_value(g, lat, lon) or 0.0)) for g in sector_speed_grids]
        sector_energy = [max(0.0, float(_grid_sample_value(g, lat, lon) or 0.0)) for g in sector_energy_grids]
        total_freq = sum(sector_frequency)
        total_energy = sum(sector_energy)
        if total_freq > 0:
            sector_frequency = [v / total_freq * 100.0 for v in sector_frequency]
        if total_energy > 0:
            sector_energy = [v / total_energy * 100.0 for v in sector_energy]
        dom_idx = int(np.argmax(sector_energy if total_energy > 0 else sector_frequency))
        directional_concentration = float((sector_energy[dom_idx] if total_energy > 0 else sector_frequency[dom_idx]) / 100.0)
        ws_hub = _virtual_weibull_series(wind, k)
        rho = np.full_like(ws_hub, rho_mean, dtype=float)
        turbines_out = []
        for tf, x_c, y_c, name, rotor, p_nom_kw in turbines:
            econ = turbine_site_economics(
                ws_hub, rho, x_c, y_c, rotor, p_nom_kw,
                demand_mwh=demand_mwh, tasa=tasa,
                price_usd_kwh=price_usd_kwh,
                price_mxn_per_usd=price_mxn_per_usd, anios=anios,
                cost_model=cost_model)
            econ['turbina'] = name
            econ['rotor_m'] = rotor
            turbines_out.append(econ)
        sites_out.append({
            'name': f'Sitio virtual {idx:02d}',
            'lat': float(lat),
            'lon': float(lon),
            'source': 'kriging_virtual',
            'scope': scope,
            'n_hours': int(len(ws_hub)),
            'v_mean': wind,
            'v_mean_raw': wind,
            'v_max': float(_grid_sample_value(gust_grid, lat, lon) or wind * 1.6),
            'gust_ms': float(_grid_sample_value(gust_grid, lat, lon) or wind * 1.6),
            'power_density_wm2': float(_grid_sample_value(power_grid, lat, lon) or 0.0),
            'weibull_C': float(wind / max(1e-9, math.gamma(1.0 + 1.0 / k))),
            'weibull_K': k,
            'rho_mean': rho_mean,
            'dominant_direction_deg': float(dom_idx * SECTOR_DEG),
            'dominant_frequency_pct': float(sector_frequency[dom_idx]),
            'directional_concentration': directional_concentration,
            'sectorial': {
                'n_sectors': SECTOR_COUNT,
                'labels': SECTOR_LABELS,
                'frequency_pct': sector_frequency,
                'mean_speed': sector_speed,
                'energy_pct': sector_energy,
            },
            'elevation_m': float(_grid_sample_value(elevation_grid, lat, lon) or 0.0) if elevation_grid else None,
            'roughness_z0': float(_grid_sample_value(roughness_grid, lat, lon) or Z0) if roughness_grid else None,
            'resource_score': float(p['score']),
            'turbines': turbines_out,
        })

    return {
        'sites': sites_out,
        'scope': scope,
        'physical': physical,
        'n_input_stations': len(rows),
        'n_virtual_sites': len(sites_out),
        'bounds': grid_bounds,
    }


def analyze_spatial_topsis_maps(field_bundle: dict, turbines_folder: str,
                                scope: str,
                                polygon: Optional[List[dict]] = None,
                                bounds: Optional[dict] = None,
                                demand_mwh=1000, tasa=0.08,
                                price_usd_kwh=None,
                                price_mxn_per_usd=20.0,
                                anios=None,
                                max_points: int = 72,
                                turbine_filenames: Optional[List[str]] = None,
                                cost_model: Optional[CostModel] = None):
    """Evaluate TOPSIS candidates from the published MCP spatial fields.

    This is the guided Design path.  It deliberately does not reopen raw
    ERA5/MERRA-2 files: wind, Weibull, density and 16-sector statistics are
    sampled from the calibrated field bundle produced by
    :func:`build_calibrated_maps`.
    """

    price_usd_kwh, anios = _require_project_economics(price_usd_kwh, anios)
    scope = 'park' if scope == 'park' else 'exploratory'
    if scope == 'park':
        if not polygon or len(polygon) < 3:
            raise ValueError('El análisis de área seleccionada requiere el polígono cerrado del parque.')
        requested_bounds = _bounds_from_polygon(polygon)
    else:
        if not bounds:
            raise ValueError('La exploración global requiere un área rectangular seleccionada.')
        requested_bounds = bounds

    required = ('wind', 'gust', 'power_density', 'weibull_k', 'air_density', 'sectorial')
    missing = [key for key in required if not field_bundle.get(key)]
    if missing:
        raise ValueError(f'Campos MCP incompletos para TOPSIS: {", ".join(missing)}')

    wind_grid = field_bundle['wind']
    gust_grid = field_bundle['gust']
    power_grid = field_bundle['power_density']
    k_grid = field_bundle['weibull_k']
    rho_grid = field_bundle['air_density']
    elevation_grid = field_bundle.get('elevation')
    roughness_grid = field_bundle.get('roughness')
    sectorial = field_bundle['sectorial']
    sector_freq_grids = list(sectorial.get('frequency_pct') or [])
    sector_speed_grids = list(sectorial.get('mean_speed') or [])
    sector_energy_grids = list(sectorial.get('energy_pct') or [])
    if not all(len(group) == SECTOR_COUNT for group in (
        sector_freq_grids, sector_speed_grids, sector_energy_grids,
    )):
        raise ValueError('El campo MCP debe contener exactamente 16 sectores direccionales.')

    grid_bounds = wind_grid.get('bounds') or requested_bounds
    candidates = []
    z_values = wind_grid.get('z') or []
    ny = len(z_values)
    nx = len(z_values[0]) if ny else 0
    if ny < 2 or nx < 2:
        raise ValueError('El campo MCP no contiene una grilla espacial válida.')
    for j in range(ny):
        lat = grid_bounds['lat_min'] + (grid_bounds['lat_max'] - grid_bounds['lat_min']) * j / max(1, ny - 1)
        for i in range(nx):
            lon = grid_bounds['lon_min'] + (grid_bounds['lon_max'] - grid_bounds['lon_min']) * i / max(1, nx - 1)
            if scope == 'park' and not _point_in_polygon(lat, lon, polygon or []):
                continue
            if scope == 'exploratory' and not (
                float(requested_bounds['lat_min']) <= lat <= float(requested_bounds['lat_max'])
                and float(requested_bounds['lon_min']) <= lon <= float(requested_bounds['lon_max'])
            ):
                continue
            wind = _grid_sample_value(wind_grid, lat, lon)
            if wind is None or not np.isfinite(wind):
                continue
            gust = _grid_sample_value(gust_grid, lat, lon) or wind * 1.6
            power = _grid_sample_value(power_grid, lat, lon) or 0.0
            score = 0.55 * float(wind) + 0.003 * float(power) + 0.12 * float(gust)
            candidates.append({'lat': lat, 'lon': lon, 'score': score})

    if not candidates:
        raise ValueError('No hay puntos MCP interpolados dentro del área seleccionada.')
    if scope == 'exploratory':
        candidates.sort(key=lambda point: point['score'], reverse=True)
        candidates = candidates[:max_points]
    elif len(candidates) > max_points:
        step = max(1, int(math.ceil(len(candidates) / max_points)))
        candidates = candidates[::step][:max_points]

    turb_files = sorted(
        filename for filename in os.listdir(turbines_folder)
        if filename.lower().endswith('.csv') and not filename.startswith('.')
    )
    if turbine_filenames:
        selected_turbines = set(turbine_filenames)
        turb_files = [filename for filename in turb_files if filename in selected_turbines]
    if not turb_files:
        raise ValueError('Selecciona al menos una turbina válida para el análisis TOPSIS.')

    turbines = []
    for filename in turb_files:
        try:
            data = pd.read_csv(os.path.join(turbines_folder, filename), header=None).values
            turbines.append((
                filename, data[:, 0].astype(float), data[:, 1].astype(float),
                *parse_turbine_filename(filename),
            ))
        except Exception as exc:
            log.warning('analyze_spatial_topsis_maps: skip turbine %s — %s', filename, exc)
    if not turbines:
        raise ValueError('Ninguna curva de potencia seleccionada pudo leerse.')

    sites_out = []
    for idx, point in enumerate(candidates, start=1):
        lat, lon = point['lat'], point['lon']
        wind = max(0.0, float(_grid_sample_value(wind_grid, lat, lon) or 0.0))
        k_value = max(0.45, float(_grid_sample_value(k_grid, lat, lon) or 2.0))
        rho_mean = max(0.5, float(_grid_sample_value(rho_grid, lat, lon) or 1.225))
        sector_frequency = [
            max(0.0, float(_grid_sample_value(grid, lat, lon) or 0.0))
            for grid in sector_freq_grids
        ]
        sector_speed = [
            max(0.0, float(_grid_sample_value(grid, lat, lon) or 0.0))
            for grid in sector_speed_grids
        ]
        sector_energy = [
            max(0.0, float(_grid_sample_value(grid, lat, lon) or 0.0))
            for grid in sector_energy_grids
        ]
        total_frequency = sum(sector_frequency)
        total_energy = sum(sector_energy)
        if total_frequency > 0:
            sector_frequency = [value / total_frequency * 100.0 for value in sector_frequency]
        if total_energy > 0:
            sector_energy = [value / total_energy * 100.0 for value in sector_energy]
        dominant_idx = int(np.argmax(sector_energy if total_energy > 0 else sector_frequency))
        directional_concentration = float(
            (sector_energy[dominant_idx] if total_energy > 0 else sector_frequency[dominant_idx]) / 100.0
        )
        ws_hub = _virtual_weibull_series(wind, k_value)
        rho = np.full_like(ws_hub, rho_mean, dtype=float)
        turbines_out = []
        for filename, x_curve, y_curve, name, rotor, nominal_kw in turbines:
            economics = turbine_site_economics(
                ws_hub, rho, x_curve, y_curve, rotor, nominal_kw,
                demand_mwh=demand_mwh, tasa=tasa,
                price_usd_kwh=price_usd_kwh,
                price_mxn_per_usd=price_mxn_per_usd, anios=anios,
                cost_model=cost_model,
            )
            economics['turbina'] = name
            economics['rotor_m'] = rotor
            turbines_out.append(economics)
        sites_out.append({
            'name': f'Sitio MCP virtual {idx:02d}',
            'lat': float(lat), 'lon': float(lon),
            'source': 'mcp_calibrated_kriging', 'scope': scope,
            'n_hours': int(len(ws_hub)),
            'v_mean': wind, 'v_mean_raw': wind,
            'v_max': float(_grid_sample_value(gust_grid, lat, lon) or wind * 1.6),
            'gust_ms': float(_grid_sample_value(gust_grid, lat, lon) or wind * 1.6),
            'power_density_wm2': float(_grid_sample_value(power_grid, lat, lon) or 0.0),
            'weibull_C': float(wind / max(1e-9, math.gamma(1.0 + 1.0 / k_value))),
            'weibull_K': k_value, 'rho_mean': rho_mean,
            'dominant_direction_deg': float(dominant_idx * SECTOR_DEG),
            'dominant_frequency_pct': float(sector_frequency[dominant_idx]),
            'directional_concentration': directional_concentration,
            'sectorial': {
                'n_sectors': SECTOR_COUNT, 'labels': SECTOR_LABELS,
                'frequency_pct': sector_frequency,
                'mean_speed': sector_speed, 'energy_pct': sector_energy,
            },
            'elevation_m': float(_grid_sample_value(elevation_grid, lat, lon) or 0.0) if elevation_grid else None,
            'roughness_z0': float(_grid_sample_value(roughness_grid, lat, lon) or Z0) if roughness_grid else None,
            'resource_score': float(point['score']), 'turbines': turbines_out,
        })

    return {
        'sites': sites_out, 'scope': scope,
        'physical': bool(field_bundle.get('physical')),
        'n_input_stations': int(field_bundle.get('n_sites') or 0),
        'n_virtual_sites': len(sites_out), 'bounds': grid_bounds,
        'field_source': 'mcp_calibrated_maps',
        'data_source': field_bundle.get('data_source'),
        'calibration_id': field_bundle.get('calibration_id'),
        'study_revision': field_bundle.get('study_revision'),
    }


# ── Legacy replica: 3 mapas Kriging como en ajuste_aerogeneradores.py ─────────

def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points in km."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def legacy_three_maps(wind_folder: str, turbines_folder: str, year: int,
                      demand_mwh=1000, tasa=0.08, price_usd_kwh=None,
                      price_mxn_per_usd=20.0, anios=None, max_files=None,
                      n_grid=80, model='exponential',
                      center_lat: Optional[float] = None,
                      center_lon: Optional[float] = None,
                      radius_km: Optional[float] = None,
                      filter_filenames: Optional[List[str]] = None,
                      physical: bool = False,
                      z_meas: float = 100.0,
                      z0_ref: float = 0.03,
                      grid_bounds: Optional[dict] = None,
                      cost_model: Optional[CostModel] = None,
                      z_tgt: Optional[float] = None,
                      include_sectorial: bool = False,
                      include_physical_economics: bool = False,
                      progress_callback=None):
    """
    Reproduce el comportamiento original del script MATLAB/Tk:
    para cada sitio elige el último aerogenerador del catálogo (como el
    script original, que quedaba con ctot_last / VAN_last) y genera 3
    grillas Kriging:
      1) velocidad promedio del viento
      2) inversión inicial (USD)
      3) valor actual neto (USD)

    Devuelve un dict con los 3 grids + las listas de puntos de entrada.
    """
    price_usd_kwh, anios = _require_project_economics(price_usd_kwh, anios)
    wind_files = sorted(
        f for f in os.listdir(wind_folder)
        if f.lower().endswith('.txt') and not f.startswith('.')
    )

    # Explicit selection: use only the user-picked filenames.
    if filter_filenames:
        keep = set(filter_filenames)
        wind_files = [f for f in wind_files if f in keep]

    # Optional: keep only files within radius_km of (center_lat, center_lon)
    if center_lat is not None and center_lon is not None and radius_km:
        keep = []
        for f in wind_files:
            c = parse_coords_from_filename(f)
            if c is None:
                continue
            if _haversine_km(c[0], c[1], center_lat, center_lon) <= radius_km:
                keep.append(f)
        wind_files = keep

    if max_files:
        wind_files = wind_files[:max_files]

    turb_files = sorted(
        f for f in os.listdir(turbines_folder)
        if f.lower().endswith('.csv') and not f.startswith('.')
    )
    if not turb_files:
        raise ValueError(f'No turbine CSVs in {turbines_folder}')

    # Pre-compute fitted power curves per turbine (catalog-level, site-independent).
    # Replicates ajuste_aerogeneradores.py: gauss4 on (v, P_kW), clipped to P_nom.
    turbine_curves = []
    for tf in turb_files:
        try:
            dat = pd.read_csv(os.path.join(turbines_folder, tf), header=None).values
            xd = dat[:, 0].astype(float)
            yd_kw = dat[:, 1].astype(float)
        except Exception as e:
            log.warning('legacy_three_maps: skip turbine curve %s — %s', tf, e)
            continue
        name, rotor, p_nom_kw = parse_turbine_filename(tf)
        # Fit on Watts (matches reference script), then convert back to kW for plotting.
        ps_w = fit_power_curve_gauss4(xd, yd_kw * 1000.0, p_nom_kw * 1000.0)
        # Smooth fitted curve for nicer plotting on a fine grid.
        x_fit = np.linspace(float(xd.min()), float(xd.max()), 200)
        ifn_fit = interp1d(xd, ps_w / 1000.0, kind='linear',
                           bounds_error=False, fill_value=0.0)
        y_fit_kw = np.maximum(ifn_fit(x_fit), 0.0)
        turbine_curves.append({
            'name':      name,
            'file':      tf,
            'p_nom_kw':  float(p_nom_kw),
            'rotor_m':   float(rotor),
            'x_raw':     xd.tolist(),
            'y_raw_kw':  yd_kw.tolist(),
            'x_fit':     x_fit.tolist(),
            'y_fit_kw':  y_fit_kw.tolist(),
        })

    lats, lons, v_proms, gusts, power_densities, invers, vans, names = [], [], [], [], [], [], [], []
    weibull_ks = []
    sector_freqs, sector_speeds, sector_powers, sector_energies = [], [], [], []

    paths = [os.path.join(wind_folder, wf) for wf in wind_files]
    if progress_callback:
        progress_callback(10, f'Leyendo {len(paths)} archivos meteorológicos…')
    stats_rows = parallel_read_sites(paths, year=year, hub_height_m=z_tgt, max_workers=6)

    last_tf = turb_files[-1] if turb_files else None
    last_x_c, last_y_c, last_name, last_rotor, last_p_nom_kw = None, None, None, None, None
    if last_tf:
        try:
            dat_last = pd.read_csv(os.path.join(turbines_folder, last_tf), header=None).values
            last_x_c = dat_last[:, 0].astype(float)
            last_y_c = dat_last[:, 1].astype(float)
            last_name, last_rotor, last_p_nom_kw = parse_turbine_filename(last_tf)
        except Exception as e:
            log.warning('legacy_three_maps: error con la última turbina %s — %s', last_tf, e)
            last_tf = None

    for r in stats_rows:
        if r is None or r.get('_lat') is None or r.get('_lon') is None:
            continue

        ctot_last, VAN_last = 0.0, 0.0
        if last_tf is not None and last_x_c is not None:
            try:
                econ = turbine_site_economics(
                    r['ws_hub'], r['rho'], last_x_c, last_y_c, last_rotor, last_p_nom_kw,
                    demand_mwh=demand_mwh, tasa=tasa,
                    price_usd_kwh=price_usd_kwh,
                    price_mxn_per_usd=price_mxn_per_usd, anios=anios,
                    cost_model=cost_model)
                ctot_last = econ['capex_usd']
                VAN_last  = econ['van_usd']
            except Exception as e:
                log.warning('legacy_three_maps: turbine_site_economics error — %s', e)

        lats.append(r['_lat']); lons.append(r['_lon'])
        v_proms.append(r['v_mean'])
        gusts.append(r['gust_ms'])
        power_densities.append(r['power_density_wm2'])
        invers.append(float(ctot_last))
        vans.append(float(VAN_last))
        names.append(r['_name'])
        weibull_ks.append(float(r['weibull_K']))
        sector_freqs.append(r['sector_frequency_pct'])
        sector_speeds.append(r['sector_mean_speed'])
        sector_powers.append(r['sector_power_density_wm2'])
        sector_energies.append(r['sector_energy_pct'])

    if len(lats) < 3:
        raise ValueError(f'Kriging necesita >= 3 sitios válidos (hay {len(lats)})')

    if progress_callback:
        progress_callback(45, f'{len(lats)} sitios válidos · generando capas principales…')

    variogram_structure = fit_variogram_loo(lons, lats, v_proms, requested=model)

    def _grid(values, title):
        g = kriging_grid(lons, lats, values, n=n_grid, model=model,
                         bounds=grid_bounds, variogram_structure=variogram_structure)
        g['title'] = title
        g['points'] = [
            {'lat': la, 'lon': lo, 'value': float(v), 'name': nm}
            for la, lo, v, nm in zip(lats, lons, values, names)
        ]
        return g

    def _sector_grid(values, title):
        g = kriging_grid(lons, lats, values, n=n_grid, model=model,
                         bounds=grid_bounds, variogram_structure=variogram_structure)
        g['title'] = title
        return g

    base = {
        'wind':            _grid(v_proms, 'Velocidad promedio del viento (m/s)'),
        'gust':            _grid(gusts, 'Ráfagas / extremo anual P99.5 (m/s)'),
        'power_density':   _grid(power_densities, 'Densidad de potencia eólica (W/m²)'),
        'inversion':       _grid(invers,  'Inversión inicial (USD)'),
        'van':             _grid(vans,    'Valor Actual Neto (USD)'),
        'n_sites':         len(lats),
        'turbine_curves':  turbine_curves,
        'physical':        False,
    }

    if include_sectorial:
        if progress_callback:
            progress_callback(62, 'Generando capas sectoriales…')
        base['sectorial'] = {
            'n_sectors': SECTOR_COUNT,
            'labels': SECTOR_LABELS,
            'frequency_pct': [
                _sector_grid([row[i] for row in sector_freqs], f'Frecuencia sector {SECTOR_LABELS[i]} (%)')
                for i in range(SECTOR_COUNT)
            ],
            'mean_speed': [
                _sector_grid([row[i] for row in sector_speeds], f'Velocidad media sector {SECTOR_LABELS[i]} (m/s)')
                for i in range(SECTOR_COUNT)
            ],
            'power_density': [
                _sector_grid([row[i] for row in sector_powers], f'Densidad potencia sector {SECTOR_LABELS[i]} (W/m²)')
                for i in range(SECTOR_COUNT)
            ],
            'energy_pct': [
                _sector_grid([row[i] for row in sector_energies], f'Participación energética sector {SECTOR_LABELS[i]} (%)')
                for i in range(SECTOR_COUNT)
            ],
        }

    if not physical:
        if progress_callback:
            progress_callback(95, 'Mapas Kriging listos.')
        return base

    # ─── Kriging físicamente informado (Fase 3) ──────────────────────────────
    # 1) Por sitio: lookup de altitud y z0 → bias-removal de v_mean a un campo
    #    "neutralizado" (rugosidad de referencia z0_ref = 0.03 m).
    # 2) Krigeamos el campo neutral.
    # 3) Por píxel: muestreamos z0_local y elevación, y reaplicamos la
    #    corrección logarítmica para obtener V_real.
    try:
        from .terrain import enrich_sites, sample_grid_elevation, sample_grid_z0
    except Exception as e:  # pragma: no cover
        log.warning('legacy_three_maps: terrain module unavailable (%s); fallback no-physical', e)
        return base

    site_records = [
        {'filename': nm, 'lat': la, 'lon': lo}
        for nm, la, lo in zip(names, lats, lons)
    ]
    if progress_callback:
        progress_callback(65, 'Aplicando corrección física de terreno…')
    # Las llamadas a terreno dependen de servicios externos (DEM/WorldCover).
    # Las envolvemos en futures con timeout para que la tarea NUNCA se quede
    # bloqueada esperando red: si los servicios no responden, usamos defaults
    # razonables (z0_ref, elevación 0 m) y seguimos.
    from concurrent.futures import ThreadPoolExecutor as _TPE, TimeoutError as _FTOut
    _terrain_pool = _TPE(max_workers=3)

    def _safe_terrain(call, timeout, fallback, label):
        fut = _terrain_pool.submit(call)
        try:
            return fut.result(timeout=timeout)
        except (_FTOut, Exception) as exc:
            log.warning('legacy_three_maps: %s falló/timeout (%s); usando defaults', label, exc)
            return fallback

    enriched = _safe_terrain(
        lambda: enrich_sites(site_records),
        timeout=20.0,
        fallback=[{**s, 'elevation_m': None, 'z0': None} for s in site_records],
        label='enrich_sites',
    )
    z0_site = [
        float(s['z0']) if s.get('z0') is not None else float(z0_ref)
        for s in enriched
    ]
    elev_site = [
        float(s['elevation_m']) if s.get('elevation_m') is not None else 0.0
        for s in enriched
    ]

    def _alpha(z0_local: float) -> float:
        z0_local = max(1e-4, float(z0_local))
        denom = math.log(z_meas / z0_local)
        if abs(denom) < 1e-9:
            return 1.0
        return math.log(z_meas / z0_ref) / denom

    v_neutral = [v * _alpha(z0i) for v, z0i in zip(v_proms, z0_site)]

    if progress_callback:
        progress_callback(75, 'Krigeando campo neutralizado…')
    # Krigeamos el campo neutralizado.
    g_neutral = kriging_grid(lons, lats, v_neutral, n=n_grid, model=model,
                             bounds=grid_bounds)
    b = g_neutral['bounds']
    z_neutral = g_neutral['z']
    ny = len(z_neutral); nx = len(z_neutral[0]) if ny else 0
    grid_lats = [b['lat_min'] + (b['lat_max'] - b['lat_min']) * j / (ny - 1)
                 for j in range(ny)] if ny > 1 else [b['lat_min']]
    grid_lons = [b['lon_min'] + (b['lon_max'] - b['lon_min']) * i / (nx - 1)
                 for i in range(nx)] if nx > 1 else [b['lon_min']]

    if progress_callback:
        progress_callback(80, 'Muestreando altitud DEM…')
    elev_grid = _safe_terrain(
        lambda: sample_grid_elevation(grid_lats, grid_lons),
        timeout=15.0,
        fallback=[[0.0] * nx for _ in range(ny)],
        label='sample_grid_elevation',
    )
    if progress_callback:
        progress_callback(85, 'Muestreando rugosidad WorldCover…')
    z0_grid = _safe_terrain(
        lambda: sample_grid_z0(grid_lats, grid_lons, elev_grid=elev_grid),
        timeout=15.0,
        fallback=[[float(z0_ref)] * nx for _ in range(ny)],
        label='sample_grid_z0',
    )
    _terrain_pool.shutdown(wait=False)

    if progress_callback:
        progress_callback(90, 'Aplicando corrección log-altura por píxel…')
    # Corrección por pixel: V_real = V_neutral / alpha_local
    z_neutral = g_neutral['z']
    ny = len(z_neutral); nx = len(z_neutral[0]) if ny else 0
    v_real = [[0.0] * nx for _ in range(ny)]
    for j in range(ny):
        for i in range(nx):
            z0_p = max(1e-4, float(z0_grid[j][i]) if j < len(z0_grid) and i < len(z0_grid[j]) else z0_ref)
            alpha_p = math.log(z_meas / z0_ref) / math.log(z_meas / z0_p)
            v_real[j][i] = float(z_neutral[j][i]) / alpha_p if alpha_p else float(z_neutral[j][i])

    # Densidad del aire local (escalada por altitud).
    rho0 = 1.225
    air_density = [
        [rho0 * math.exp(-max(0.0, float(elev_grid[j][i])) / 8400.0)
         for i in range(nx)]
        for j in range(ny)
    ]

    bounds = g_neutral.get('bounds')
    base_wind_z = base['wind']['z']
    gust_ratio_grid = _sector_grid([
        float(g / v) if v and np.isfinite(v) and v > 1e-9 else 1.6
        for g, v in zip(gusts, v_proms)
    ], 'Relación ráfaga / viento medio')
    k_grid = _sector_grid(weibull_ks, 'Factor de forma Weibull k') if include_physical_economics else None
    sector_freq_z = None
    sector_speed_z = None
    if include_sectorial:
        sector_freq_z = [
            _sector_grid([row[i] for row in sector_freqs], f'Frecuencia sector {SECTOR_LABELS[i]} (%)')['z']
            for i in range(SECTOR_COUNT)
        ]
        sector_speed_z = [
            _sector_grid([row[i] for row in sector_speeds], f'Velocidad media sector {SECTOR_LABELS[i]} (m/s)')['z']
            for i in range(SECTOR_COUNT)
        ]

    def _wrap_grid(z, title, unit, vmin=None, vmax=None):
        z_min, z_max = _min_max_grid(z)
        return {
            'z': z,
            'lats': grid_lats,
            'lons': grid_lons,
            'bounds': bounds,
            'title': title,
            'unit': unit,
            'model': model,
            'z_min': z_min if vmin is None else vmin,
            'z_max': z_max if vmax is None else vmax,
        }

    wind_directional = [[0.0] * nx for _ in range(ny)]
    gust_corrected = [[0.0] * nx for _ in range(ny)]
    power_density_corrected = [[0.0] * nx for _ in range(ny)]
    inversion_corrected = [[0.0] * nx for _ in range(ny)]
    van_corrected = [[0.0] * nx for _ in range(ny)]
    last_curve = turbine_curves[-1] if turbine_curves else None
    power_interp = None
    p_max_curve = 0.0
    if include_physical_economics and last_curve:
        x_last = np.asarray(last_curve['x_raw'], dtype=float)
        y_last_kw = np.asarray(last_curve['y_raw_kw'], dtype=float)
        p_nom_kw = float(last_curve['p_nom_kw'])
        ps_last = fit_power_curve_gauss4(x_last, y_last_kw * 1000.0, p_nom_kw * 1000.0)
        power_interp = interp1d(x_last, ps_last, kind='linear',
                                bounds_error=False, fill_value=0.0)
        p_max_curve = float(np.max(ps_last)) if len(ps_last) else 0.0
    for j in range(ny):
        for i in range(nx):
            base_v = float(base_wind_z[j][i]) if j < len(base_wind_z) and i < len(base_wind_z[j]) else 0.0
            physical_ratio = float(v_real[j][i]) / base_v if base_v > 1e-9 else 1.0
            if sector_freq_z is not None and sector_speed_z is not None:
                freqs = [max(0.0, float(sector_freq_z[s][j][i])) for s in range(SECTOR_COUNT)]
                speeds = [max(0.0, float(sector_speed_z[s][j][i])) for s in range(SECTOR_COUNT)]
                total_freq = sum(freqs)
                if total_freq > 0:
                    v_dir_raw = float(np.cbrt(sum((freqs[s] / total_freq) * speeds[s] ** 3
                                                  for s in range(SECTOR_COUNT))))
                else:
                    v_dir_raw = base_v
            else:
                v_dir_raw = base_v
            v_dir = max(0.0, v_dir_raw * physical_ratio)
            wind_directional[j][i] = v_dir
            gust_ratio = float(gust_ratio_grid['z'][j][i]) if j < len(gust_ratio_grid['z']) and i < len(gust_ratio_grid['z'][j]) else 1.6
            gust_corrected[j][i] = max(v_dir, v_dir * max(1.0, gust_ratio))
            rho_local = max(0.0, float(air_density[j][i]))
            power_density_corrected[j][i] = 0.5 * rho_local * (v_dir ** 3)
            if include_physical_economics and power_interp is not None and last_curve:
                k_local = float(k_grid['z'][j][i]) if j < len(k_grid['z']) and i < len(k_grid['z'][j]) else 2.0
                capex, van = _legacy_prefit_economics(
                    v_dir, k_local, rho_local, power_interp, p_max_curve,
                    float(last_curve['rotor_m']), float(last_curve['p_nom_kw']),
                    demand_mwh, tasa, price_usd_kwh, price_mxn_per_usd, anios,
                    cost_model=cost_model)
                inversion_corrected[j][i] = capex
                van_corrected[j][i] = van
            else:
                inversion_corrected[j][i] = float(base['inversion']['z'][j][i])
                van_corrected[j][i] = float(base['van']['z'][j][i])

    base['wind_neutral'] = _wrap_grid(z_neutral,
                                      'Viento neutralizado (campo geoestadístico)',
                                      'm/s')
    base['wind'] = _wrap_grid(v_real,
                              'Viento real con corrección física (z₀ + altitud)',
                              'm/s')
    base['wind_directional'] = _wrap_grid(
        wind_directional,
        'Viento corregido direccional (frecuencia + sectores)',
        'm/s')
    base['gust'] = _wrap_grid(gust_corrected,
                              'Ráfagas corregidas con viento físico-direccional',
                              'm/s')
    base['power_density'] = _wrap_grid(
        power_density_corrected,
        'Densidad de potencia con viento corregido',
        'W/m²')
    base['inversion'] = _wrap_grid(
        inversion_corrected,
        'Inversión inicial con viento corregido',
        'USD')
    base['van'] = _wrap_grid(
        van_corrected,
        'Valor Actual Neto con viento corregido',
        'USD')
    base['wind']['points'] = [
        {'lat': la, 'lon': lo, 'value': float(v), 'name': nm,
         'z0': float(z0i), 'elevation_m': float(ei)}
        for la, lo, v, nm, z0i, ei
        in zip(lats, lons, v_proms, names, z0_site, elev_site)
    ]
    base['roughness']   = _wrap_grid(z0_grid,    'Rugosidad superficial z₀',          'm')
    base['elevation']   = _wrap_grid(elev_grid,  'Topografía (altitud DEM)',          'm')
    base['air_density'] = _wrap_grid(air_density,'Densidad del aire local',           'kg/m³')

    base['physical'] = True
    base['z_meas']   = z_meas
    base['z0_ref']   = z0_ref
    base['n_corrected'] = sum(1 for s in enriched if s.get('z0') is not None)
    if progress_callback:
        progress_callback(95, 'Mapas Kriging físicos listos.')
    return base

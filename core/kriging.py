"""
Kriging interpolation and multi-site wind turbine analysis.
Ported and adapted from ajuste_aerogeneradores.py (MATLAB -> Python),
decoupled from tkinter for use in the AERO by OPEN CIM REST API.

Optimizaciones 2026-04:
  - read_wind_file usa pandas (skiprows automático) en lugar de regex sobre
    el contenido completo → 20-50x más rápido en archivos de 16-18 MB.
  - _SITE_STATS_CACHE cachea stats por (path, mtime, year) en memoria;
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
from scipy.optimize import curve_fit
from scipy.stats import weibull_min as wbl_dist
from scipy.interpolate import interp1d

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
ANIOS = 20      # project life (years)


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

    Convención unificada con core/energy.py: el segundo elemento es DIÁMETRO
    del rotor en metros, no radio. Default 80 m si no se encuentra el patrón.
    """
    base = os.path.splitext(fn)[0]
    m = _TURB_RE.search(base)
    if m:
        return (base, float(m.group(2)), float(m.group(1)))
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


def kriging_interp(x, y, z, xq, yq, model='exponential', rng=50.0):
    """Simple Kriging interpolation. Returns (z_interp, variance).

    Coordenadas (x=lon, y=lat) en grados; las distancias internas se calculan
    con Haversine (km), por lo que `rng` debe interpretarse en KILÓMETROS
    (default 50 km, escala típica de un cluster de sitios MERRA-2/ERA5).

    NOTA: implementa Simple Kriging (asume media conocida = 0 con sill normalizado).
    Lejos del rango de correlación las predicciones tienden a 0, no a la media de
    los datos. Para Ordinary Kriging real se requiere ampliar el sistema con un
    multiplicador de Lagrange que imponga Σwᵢ=1.
    """
    # Simple Kriging: asume media=0. Para Ordinary Kriging se requiere sistema con Lagrange.
    x = np.asarray(x).ravel()
    y = np.asarray(y).ravel()
    z = np.asarray(z, dtype=float).ravel()
    # Distancias Haversine en km — 1°lat ≠ 1°lon en México (ver _haversine_pairwise_km).
    d  = _haversine_pairwise_km(x, y, x, y)
    dq = _haversine_pairwise_km(x, y, xq.ravel(), yq.ravel())
    if rng == 0:
        rng = np.max(d) / 3 if d.size else 1.0
    if model == 'gaussian':
        g = lambda h, a: 1 - np.exp(-(h / a) ** 2)
    else:
        g = lambda h, a: 1 - np.exp(-h / a)
    # ridge 1e-6 para estabilidad numérica (sobre todo con variograma gaussiano).
    K = 1 - g(d,  rng) + 1e-6 * np.eye(len(x))
    k = 1 - g(dq, rng)
    w = np.linalg.solve(K, k)
    z_hat = (w.T @ z).reshape(xq.shape)
    var   = (1 - np.sum(w * k, axis=0)).reshape(xq.shape)
    return z_hat, var


# Aliases honestos: la función actual es Simple Kriging.
simple_kriging = kriging_interp
interpolate_wind = kriging_interp


def kriging_grid(lons, lats, values, n=80, margin_frac=0.15, model='exponential',
                 bounds=None):
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
    zq, var = kriging_interp(lo, la, va, xq, yq, model=model)
    return {
        'lon_grid': xq.tolist(),
        'lat_grid': yq.tolist(),
        'z':        zq.tolist(),
        'variance': var.tolist(),
        'bounds': {
            'lat_min': lat_min,
            'lat_max': lat_max,
            'lon_min': lon_min,
            'lon_max': lon_max,
        },
        'z_min': float(np.nanmin(zq)),
        'z_max': float(np.nanmax(zq)),
        'n': n,
        'model': model,
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


def read_wind_file(path: str, year: Optional[int] = None):
    """
    Lee un archivo Windnavigator (MERRA-2/ERA5) con pandas — 20-50x más rápido
    que la versión regex sobre el contenido completo.

    Devuelve dict con claves: name, lat, lon, ws, t2m, p0m, n, year.
    Mantiene la misma interfaz que la versión anterior.
    """
    fname = os.path.basename(path)
    coords = parse_coords_from_filename(fname)
    if coords is None:
        raise ValueError(f'No se pueden parsear coordenadas de: {fname}')
    lat, lon = coords

    skip = _find_data_start(path)
    try:
        df = pd.read_csv(
            path,
            skiprows=skip,
            sep=r'\s+',          # columnas separadas por espacios/tabs
            header=None,
            usecols=[0, 1, 4, 5],  # datetime, WS, T2M, P0M
            names=['dt', 'ws', 't2m', 'p0m'],
            na_values=['-', 'nan', 'NaN', ''],
            on_bad_lines='skip',
            engine='c',
            dtype={'ws': 'float32', 't2m': 'float32', 'p0m': 'float32'},
        )
    except Exception:
        # Fallback: sin usecols (el archivo puede tener menos columnas)
        df = pd.read_csv(
            path, skiprows=skip, sep=r'\s+', header=None,
            na_values=['-', 'nan', ''], on_bad_lines='skip', engine='c',
        )
        df.columns = list(range(df.shape[1]))
        df = df.rename(columns={0: 'dt', 1: 'ws'})
        df['t2m'] = df.get(4, pd.Series(15.0, index=df.index)).astype('float32')
        df['p0m'] = df.get(5, pd.Series(101.3, index=df.index)).astype('float32')

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
        't2m':  df['t2m'].to_numpy(dtype=float),
        'p0m':  df['p0m'].to_numpy(dtype=float),
        'n':    len(df),
        'year': year,
    }


# ── Per-site statistics (Weibull, density, extrapolated wind) ─────────────────

def site_wind_stats(site: dict, hub_height_m: Optional[float] = None,
                    z0: Optional[float] = None):
    """Compute Weibull + derived wind stats for a single site dict.

    hub_height_m: altura del rotor de la PowerCurve activa. Fallback Z_TGT=80 m.
    z0: rugosidad del terreno. Fallback Z0=0.03 (campo abierto/costa).
    Sierra Norte Oaxaca: usar Z0=0.1-0.5.
    """
    z_tgt = float(hub_height_m) if hub_height_m is not None else Z_TGT
    z0_eff = float(z0) if z0 is not None else Z0
    ws = site['ws']; t2m = site['t2m']; p0m = site['p0m']
    if ws.size == 0:
        return None
    wsp = ws * (math.log(z_tgt / z0_eff) / math.log(Z_REF / z0_eff))
    T = t2m + 273.15
    P = p0m * 1000.0
    rho = P / (R_ESP * T)
    meanrho = float(np.mean(rho))
    Vmax = float(np.max(ws))
    try:
        sh, _, sc = wbl_dist.fit(ws, floc=0)
        C, K = float(sc), float(sh)
    except Exception:
        C, K = 6.0, 2.0
    return {
        'v_mean':       float(np.mean(wsp)),
        'v_mean_raw':   float(np.mean(ws)),
        'v_max':        Vmax,
        'weibull_C':    C,
        'weibull_K':    K,
        'rho_mean':     meanrho,
        'ws_hub':       wsp,
        'rho':          rho,
    }


def _compute_site_stats_cached(path: str, year: Optional[int] = None,
                                hub_height_m: Optional[float] = None,
                                z0: Optional[float] = None):
    """
    Versión cacheada de (read_wind_file + site_wind_stats).
    La clave de caché es (ruta_absoluta, mtime_entero, year).
    Si el archivo no ha cambiado (mismo mtime), devuelve el resultado previo
    sin releer el disco — esto hace la segunda llamada instantánea.

    NOTA: ws_hub y rho son arrays; se copian para evitar mutaciones externas.
    """
    abs_path = os.path.abspath(path)
    try:
        mtime = int(os.path.getmtime(abs_path))
    except OSError:
        mtime = 0
    cache_key = (abs_path, mtime, year)
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
                           demand_mwh=1000, tasa=0.08, price_usd_kwh=0.20,
                           price_mxn_per_usd=20.0, anios=ANIOS, max_w=MAX_W):
    """
    Compute per-site × per-turbine technical + economic metrics.
    All inputs are arrays/scalars already read from files.
    """
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
    pr_aero = p_nom_kw * 2000.0     # USD per turbine (simple rule of thumb)
    pan     = pt * n                # Wh/year total park
    pin     = p_nom_kw * n          # kW installed
    caero   = n * pr_aero
    sep     = 7 * rotor_m           # 7 D streamwise (rotor_m = diámetro)
    area    = sep ** 2 * n
    road_cost_per_m2 = 10.0         # USD/m² (consistente con TechnoEconomicAnalyzer)
    roads_usd = area * road_cost_per_m2
    cm      = 0.03 * caero          # annual O&M
    ccim    = 1e6 * n               # foundations
    ccab    = 15e5 * n              # cabling
    # CAPEX en USD; antes se sumaba `area` (m²) directamente, mezcla de unidades.
    ctot    = caero + ccim + ccab + roads_usd
    inv     = ctot                  # inversión inicial = CAPEX (one-time, no × anios)

    ekwh = pan / 1e3
    vtas = ekwh * (price_usd_kwh * price_mxn_per_usd)
    flux = vtas - cm                # flujo neto anual = ventas − O&M anual
    if tasa > 0:
        VAN = (flux * ((1 - (1 + tasa) ** -anios) / tasa)) - inv
        VAE = (VAN * tasa / (1 - (1 + tasa) ** -anios))
    else:
        VAN = 0.0
        VAE = 0.0
    lcoe = (ctot + cm * anios) / (ekwh * anios) if ekwh > 0 else 0
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
        'ventas_mxn_anuales':   float(vtas),
        'flujo_neto_anual':     float(flux),
        'van_usd':              float(VAN),
        'vae_usd':              float(VAE),
        'lcoe_usd_kwh':         float(lcoe),
        'roi':                  float(roi),
    }


# ── Top-level: analyze all sites × all turbines ───────────────────────────────

def analyze_sites(wind_folder: str, turbines_folder: str, year: int,
                  demand_mwh=1000, tasa=0.08, price_usd_kwh=0.20,
                  price_mxn_per_usd=20.0, anios=ANIOS, max_files=None,
                  filter_filenames: Optional[List[str]] = None):
    """
    Full analysis: read all wind files + turbine csvs, compute metrics,
    return sites list + turbine results matrix suitable for JSON.
    """
    wind_files = sorted(
        f for f in os.listdir(wind_folder)
        if f.lower().endswith('.txt') and not f.startswith('.')
    )
    if filter_filenames:
        keep = set(filter_filenames)
        wind_files = [f for f in wind_files if f in keep]
    if max_files:
        wind_files = wind_files[:max_files]

    turb_files = sorted(
        f for f in os.listdir(turbines_folder)
        if f.lower().endswith('.csv') and not f.startswith('.')
    )

    sites_out = []
    for wf in wind_files:
        path = os.path.join(wind_folder, wf)
        try:
            site = read_wind_file(path, year=year)
        except Exception as e:
            log.warning('analyze_sites: skip wind file %s — %s', wf, e)
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
                price_mxn_per_usd=price_mxn_per_usd, anios=anios)
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
            'weibull_C':   stats['weibull_C'],
            'weibull_K':   stats['weibull_K'],
            'rho_mean':    stats['rho_mean'],
            'turbines':    turbines_out,
        })

    return sites_out


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
                      demand_mwh=1000, tasa=0.08, price_usd_kwh=0.20,
                      price_mxn_per_usd=20.0, anios=ANIOS, max_files=None,
                      n_grid=80, model='exponential',
                      center_lat: Optional[float] = None,
                      center_lon: Optional[float] = None,
                      radius_km: Optional[float] = None,
                      filter_filenames: Optional[List[str]] = None):
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

    lats, lons, v_proms, invers, vans, names = [], [], [], [], [], []

    for wf in wind_files:
        path = os.path.join(wind_folder, wf)
        try:
            site = read_wind_file(path, year=year)
        except Exception as e:
            log.warning('legacy_three_maps: skip wind file %s — %s', wf, e)
            continue
        if site['n'] < 24:
            continue
        stats = site_wind_stats(site)
        if stats is None:
            continue

        ctot_last, VAN_last = 0.0, 0.0
        for tf in turb_files:
            try:
                dat = pd.read_csv(os.path.join(turbines_folder, tf), header=None).values
                x_c = dat[:, 0].astype(float)
                y_c = dat[:, 1].astype(float)
            except Exception as e:
                log.warning('legacy_three_maps: skip turbine econ %s — %s', tf, e)
                continue
            name, rotor, p_nom_kw = parse_turbine_filename(tf)
            econ = turbine_site_economics(
                stats['ws_hub'], stats['rho'], x_c, y_c, rotor, p_nom_kw,
                demand_mwh=demand_mwh, tasa=tasa,
                price_usd_kwh=price_usd_kwh,
                price_mxn_per_usd=price_mxn_per_usd, anios=anios)
            ctot_last = econ['capex_usd']
            VAN_last  = econ['van_usd']

        lats.append(site['lat']); lons.append(site['lon'])
        v_proms.append(stats['v_mean'])
        invers.append(float(ctot_last))
        vans.append(float(VAN_last))
        names.append(site['name'])

    if len(lats) < 3:
        raise ValueError(f'Kriging necesita >= 3 sitios válidos (hay {len(lats)})')

    def _grid(values, title):
        g = kriging_grid(lons, lats, values, n=n_grid, model=model)
        g['title'] = title
        g['points'] = [
            {'lat': la, 'lon': lo, 'value': float(v), 'name': nm}
            for la, lo, v, nm in zip(lats, lons, values, names)
        ]
        return g

    return {
        'wind':            _grid(v_proms, 'Velocidad promedio del viento (m/s)'),
        'inversion':       _grid(invers,  'Inversión inicial (USD)'),
        'van':             _grid(vans,    'Valor Actual Neto (USD)'),
        'n_sites':         len(lats),
        'turbine_curves':  turbine_curves,
    }

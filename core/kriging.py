"""
Kriging interpolation and multi-site wind turbine analysis.
Ported and adapted from ajuste_aerogeneradores.py (MATLAB -> Python),
decoupled from tkinter for use in the WindPro Platform REST API.
"""
import os
import re
import math
import warnings
from typing import Optional, List
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.stats import weibull_min as wbl_dist
from scipy.spatial.distance import cdist
from scipy.interpolate import interp1d

warnings.filterwarnings('ignore')

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
    """Return (name, rotor_radius_m, power_nominal_kw)."""
    base = os.path.splitext(fn)[0]
    m = _TURB_RE.search(base)
    if m:
        return (base, float(m.group(2)), float(m.group(1)))
    return (base, 40.0, 1000.0)


# ── Kriging core (ordinary Kriging on a normalised variogram) ─────────────────

def kriging_interp(x, y, z, xq, yq, model='exponential', rng=0.5):
    """Ordinary Kriging interpolation. Returns (z_interp, variance)."""
    x = np.asarray(x).ravel()
    y = np.asarray(y).ravel()
    z = np.asarray(z, dtype=float).ravel()
    d  = cdist(np.c_[x, y], np.c_[x, y])
    dq = cdist(np.c_[x, y], np.c_[xq.ravel(), yq.ravel()])
    if rng == 0:
        rng = np.max(d) / 3 if d.size else 1.0
    if model == 'gaussian':
        g = lambda h, a: 1 - np.exp(-(h / a) ** 2)
    else:
        g = lambda h, a: 1 - np.exp(-h / a)
    K = 1 - g(d,  rng) + 1e-8 * np.eye(len(x))
    k = 1 - g(dq, rng)
    w = np.linalg.solve(K, k)
    z_hat = (w.T @ z).reshape(xq.shape)
    var   = (1 - np.sum(w * k, axis=0)).reshape(xq.shape)
    return z_hat, var


def kriging_grid(lons, lats, values, n=80, margin_frac=0.15, model='exponential'):
    """Build a regular lat/lon grid, interpolate, return dict ready for JSON."""
    la = np.asarray(lats, dtype=float)
    lo = np.asarray(lons, dtype=float)
    va = np.asarray(values, dtype=float)
    if len(la) < 3:
        raise ValueError(f'Kriging requires >= 3 sites (got {len(la)}).')
    lon_margin = max(0.1, (lo.max() - lo.min()) * margin_frac)
    lat_margin = max(0.1, (la.max() - la.min()) * margin_frac)
    xq, yq = np.meshgrid(
        np.linspace(lo.min() - lon_margin, lo.max() + lon_margin, n),
        np.linspace(la.min() - lat_margin, la.max() + lat_margin, n))
    zq, var = kriging_interp(lo, la, va, xq, yq, model=model)
    return {
        'lon_grid': xq.tolist(),
        'lat_grid': yq.tolist(),
        'z':        zq.tolist(),
        'variance': var.tolist(),
        'bounds': {
            'lat_min': float(la.min() - lat_margin),
            'lat_max': float(la.max() + lat_margin),
            'lon_min': float(lo.min() - lon_margin),
            'lon_max': float(lo.max() + lon_margin),
        },
        'z_min': float(np.nanmin(zq)),
        'z_max': float(np.nanmax(zq)),
        'n': n,
        'model': model,
    }


# ── Wind file reader (MERRA-2 / ERA5 tabular format from Windnavigator) ───────
_DATA_LINE_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)'
)


def read_wind_file(path: str, year: Optional[int] = None):
    """
    Read a Windnavigator-exported wind time-series file.
    Returns dict with keys: name, lat, lon, ws (m/s), t2m (°C), p0m (kPa).
    """
    fname = os.path.basename(path)
    coords = parse_coords_from_filename(fname)
    if coords is None:
        raise ValueError(f'Cannot parse coordinates from filename: {fname}')
    lat, lon = coords

    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    ws_list, t2m_list, p0m_list = [], [], []
    for t in _DATA_LINE_RE.findall(content):
        if year is not None and int(t[0][:4]) != year:
            continue
        ws_list.append(float(t[1]))
        t2m_list.append(float(t[4]))
        p0m_list.append(float(t[5]))

    return {
        'name': fname,
        'lat':  lat,
        'lon':  lon,
        'ws':   np.array(ws_list,  dtype=float),
        't2m':  np.array(t2m_list, dtype=float),
        'p0m':  np.array(p0m_list, dtype=float),
        'n':    len(ws_list),
        'year': year,
    }


# ── Per-site statistics (Weibull, density, extrapolated wind) ─────────────────

def site_wind_stats(site: dict):
    """Compute Weibull + derived wind stats for a single site dict."""
    ws = site['ws']; t2m = site['t2m']; p0m = site['p0m']
    if ws.size == 0:
        return None
    wsp = ws * (math.log(Z_TGT / Z0) / math.log(Z_REF / Z0))  # extrapolate to hub
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
    ifn = interp1d(curve_x, ps, kind='nearest', bounds_error=False, fill_value=0)
    psit = np.maximum(ifn(ws_hub), 0)
    psit[ws_hub > max_w] = 0

    pt = float(np.sum(psit))                        # W·h (sum over hours)
    pteo = 0.5 * rho * rotor_m ** 2 * ws_hub ** 3
    cp = np.minimum(np.where(pteo > 0, psit / pteo, 0), 0.59)
    cp_max  = float(np.max(cp))
    cp_prom = float(np.mean(cp))
    n_hours_near_rated = int(np.sum((ws_hub >= 0.9 * 12) & (ws_hub <= 1.1 * 12)))
    p_max_curve = float(np.max(ps))
    fc = pt / (p_max_curve * 8760) if p_max_curve > 0 else 0

    demand_wh = demand_mwh * 1e6
    n = math.ceil(demand_wh / pt) if pt > 0 else 0
    pr_aero = p_nom_kw * 2000.0     # USD per turbine (simple rule of thumb)
    pan     = pt * n                # Wh/year total park
    pin     = p_nom_kw * n          # kW installed
    caero   = n * pr_aero
    sep     = 7 * rotor_m
    area    = sep ** 2 * n
    cm      = 0.03 * caero          # annual O&M
    ccim    = 1e6 * n               # foundations
    ccab    = 15e5 * n              # cabling
    ctot    = caero + cm + ccim + ccab + area   # CAPEX
    capex_total = ctot * anios / anios          # keep name stable
    inv     = ctot * anios          # lifetime outflow (as in legacy code, for VAN)

    ekwh = pan / 1e3
    vtas = ekwh * (price_usd_kwh * price_mxn_per_usd)
    flux = vtas - ((area + cm) * anios)
    if tasa > 0:
        VAN = (flux * ((1 - (1 + tasa) ** -anios) / tasa)) - inv
        VAE = (VAN * tasa / (1 - (1 + tasa) ** -anios))
    else:
        VAN = 0.0
        VAE = 0.0
    lcoe = (ctot + cm) / ekwh if ekwh > 0 else 0
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
        except Exception:
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
            except Exception:
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
        except Exception:
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
        except Exception:
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
            except Exception:
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

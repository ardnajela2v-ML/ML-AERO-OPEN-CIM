"""
Wind farm AEP simulation with Jensen wake model.

Given a layout of turbines (x, y in metres), a power curve, and a free-stream
wind time series with directions, computes per-turbine production accounting
for wake interactions using:

  - Jensen single-wake deficit (Jensen 1983)
  - Katic wake superposition (Katic, Højstrup, Jensen 1986)

Outputs per-turbine AEP, total AEP, wake loss %, capacity factor, and the
dominant wind direction (for layout visualisation).
"""
import math
import warnings
import numpy as np
import pandas as pd
from typing import List, Tuple, Optional, Dict, Any

from core.energy import PowerCurve, air_density


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _rotate_to_wind_axis(layout_xy: np.ndarray, wind_dir_deg: float):
    """
    Rotate layout so the wind blows in the +x direction.
    Convention: wind_dir_deg is the meteorological direction the wind comes
    FROM (0=N, 90=E). After rotation, downstream is +x.
    """
    # met → rotation angle: 90 + wind_dir_deg coloca al aerogenerador downstream en +x
    # (wind FROM north blows toward math 270°, rotamos por +90° para llevarlo a +x)
    theta = math.radians(90.0 + wind_dir_deg)
    c, s = math.cos(theta), math.sin(theta)
    R = np.array([[c, -s], [s, c]])
    return layout_xy @ R.T


def jensen_deficit(distance_x_m: float, lateral_m: float,
                   rotor_d_m: float, k: float = 0.075,  # IEA onshore estándar; Istmo TI alto usar 0.09-0.10
                   ct: float = 0.8) -> float:  # Ct promedio operacional. Para mayor precisión usar curva Ct(v) del fabricante
    """
    Jensen NOJ single-wake velocity deficit at a downwind turbine,
    including a linear partial-overlap correction (Frandsen-style).

    distance_x_m: distance downwind from upwind turbine (>0 if downwind, else 0).
    lateral_m:    perpendicular distance to wake centreline.
    rotor_d_m:    rotor diameter of the upwind turbine.
    k:            wake decay (0.04 offshore, 0.05–0.075 onshore).
    ct:           thrust coefficient. ct=0.8 es CONSERVADOR para velocidades cercanas a
                  cut-in (Ct real ≈ 0.8 en parcial-load, baja a ~0.1 cerca de rated).
                  Sobreestima estela a v alta. Para mayor precisión pasar Ct(v) del fabricante.

    Returns delta_v / v_freestream (0 if not in wake).
    """
    if distance_x_m <= 0:
        return 0.0
    r0 = rotor_d_m / 2.0
    r_wake = r0 + k * distance_x_m
    dy = abs(lateral_m)
    if dy >= r_wake + r0:
        return 0.0
    # Linear overlap between wake disc (radius r_wake) and downwind rotor (radius r0).
    if dy <= r_wake - r0:
        overlap = 1.0
    else:
        overlap = max(0.0, (r_wake - dy) / r0)
    a = 0.5 * (1.0 - math.sqrt(max(0.0, 1.0 - ct)))
    deficit = 2.0 * a * (r0 / r_wake) ** 2 * overlap
    return max(0.0, min(1.0, deficit))


def effective_wind_per_turbine(layout_xy: np.ndarray, v_free,
                               wind_dir_deg: float, rotor_d_m: float,
                               k: float = 0.075, ct: float = 0.8) -> np.ndarray:
    """
    Compute effective free-stream-equivalent wind speed at each turbine
    accounting for upstream wake superposition (Katic — sum-of-squares).

    layout_xy: (N, 2) array of turbine positions in metres.
    v_free:    free-stream wind speed (m/s) at hub height. Can be scalar or (N,) array.
    wind_dir_deg: direction wind comes from (met convention).
    """
    rotated = _rotate_to_wind_axis(layout_xy, wind_dir_deg)
    n = len(rotated)
    v_free_arr = np.asarray(v_free, dtype=float)
    if v_free_arr.ndim == 0:
        v_free_arr = np.full(n, v_free_arr, dtype=float)
        
    v_eff = np.array(v_free_arr, copy=True)
    if np.all(v_free_arr <= 0) or n <= 1:
        return v_eff

    # For each target turbine i, sum squared deficits from all upwind turbines j.
    for i in range(n):
        xi, yi = rotated[i]
        sq_sum = 0.0
        for j in range(n):
            if i == j:
                continue
            dx = xi - rotated[j, 0]    # >0 if i is downwind of j
            dy = yi - rotated[j, 1]
            d_eff = jensen_deficit(dx, dy, rotor_d_m, k=k, ct=ct)
            sq_sum += d_eff ** 2
        v_eff[i] = v_free_arr[i] * (1.0 - math.sqrt(sq_sum))
    return v_eff


# ── Sector binning for performance ────────────────────────────────────────────

def bin_wind_to_sectors(ws: np.ndarray, wd: np.ndarray, n_sectors: int = 36):
    """
    Aggregate (ws, wd) pairs into (sector × ws-bin) frequency table for fast wake calc.
    Returns:
      sector_centres_deg: (S,) array
      ws_bin_centres:     (B,) array of m/s, 1 m/s wide
      freq:               (S, B) joint frequency (sums to 1)
    """
    valid = np.isfinite(ws) & np.isfinite(wd)
    ws = ws[valid]
    wd = wd[valid] % 360.0
    if ws.size == 0:
        sectors = np.linspace(0, 360, n_sectors, endpoint=False) + 180.0 / n_sectors
        return sectors, np.array([0.0]), np.zeros((n_sectors, 1))
    sector_width = 360.0 / n_sectors
    sector_idx = (wd / sector_width).astype(int) % n_sectors
    ws_max = max(1.0, math.ceil(ws.max() + 1))
    ws_bins = np.arange(0, ws_max + 1, 1.0)
    bin_idx = np.clip((ws / 1.0).astype(int), 0, len(ws_bins) - 2)
    n_b = len(ws_bins) - 1
    freq = np.zeros((n_sectors, n_b), dtype=float)
    for s, b in zip(sector_idx, bin_idx):
        freq[s, b] += 1
    freq /= freq.sum() if freq.sum() > 0 else 1
    sector_centres = np.arange(n_sectors) * sector_width + sector_width / 2
    ws_centres = ws_bins[:-1] + 0.5
    return sector_centres, ws_centres, freq


def dominant_direction(wd: np.ndarray, n_sectors: int = 36) -> float:
    """Return the centre of the most-frequent direction sector (degrees)."""
    valid = np.isfinite(wd)
    if not valid.any():
        return 0.0
    wd = wd[valid] % 360.0
    sector_width = 360.0 / n_sectors
    sector_idx = (wd / sector_width).astype(int) % n_sectors
    counts = np.bincount(sector_idx, minlength=n_sectors)
    s = int(np.argmax(counts))
    return float(s * sector_width + sector_width / 2)


# ── Top-level farm AEP ────────────────────────────────────────────────────────

def simulate_wind_farm(layout_xy: List[Tuple[float, float]],
                       turbine: PowerCurve,
                       ws: np.ndarray, wd: np.ndarray,
                       k_wake: float = 0.075,  # IEA onshore estándar; Istmo TI alto usar 0.09-0.10
                       ct: float = 0.8,
                       n_sectors: int = 36,
                       availability: float = 1.0,
                       electrical_losses: float = 0.0,
                       temperature_c=None,
                       pressure_pa=None,
                       humidity_pct=0.0,
                       local_wind_multipliers: Optional[np.ndarray] = None) -> dict:
    """
    Compute farm AEP per turbine with wake effects.

    layout_xy: list of (x_m, y_m) turbine positions EN METROS CARTESIANOS
               (no lat/lon). Para sitios con extensión >1 km usar UTM o
               coordenadas locales centradas.

    ws, wd:    serie temporal de velocidad (m/s) y dirección (deg, convención
               meteorológica: dirección de DONDE viene el viento).
               IMPORTANTE: ws debe estar extrapolada a hub height ANTES de llamar
               esta función. Usar extrapolate_wind_hellmann() o
               extrapolate_wind_log() de core.energy.

    temperature_c, pressure_pa, humidity_pct:
               Series o escalares para corregir potencia por densidad del aire vía
               air_density(). Si no se pasan, se usa rho=1.225 kg/m³ (nivel del mar)
               y se emite un warning — sesgo de 8-12% en sitios de altitud (Sierra
               Norte de Oaxaca, valles centrales).

    Returns dict with per-turbine AEP, totals, wake_loss%, CF%, dominant dir.
    """
    layout = np.asarray(layout_xy, dtype=float)
    n = len(layout)
    if n == 0:
        return {'error': 'Empty layout'}

    # ── validación de unidades del layout ──
    if layout.size > 0 and float(np.max(np.abs(layout))) > 1e6:
        raise ValueError(
            "layout_xy debe estar en metros cartesianos, no lat/lon "
            f"(max |coord| = {float(np.max(np.abs(layout))):.3e}). "
            "Convertir a UTM o coordenadas locales centradas."
        )

    rotor_d = float(turbine.rotor_diameter_m or 80.0)
    n_samples = len(ws)
    if n_samples == 0:
        return {'error': 'No wind data'}

    # ── densidad del aire (escalar): media de la serie si hay temp+presión,
    # si no fallback a 1.225 con warning ──
    if temperature_c is not None and pressure_pa is not None:
        rho_arr = air_density(temperature_c, pressure_pa, humidity_pct)
        rho_mean = float(np.mean(rho_arr))
    else:
        warnings.warn(
            "simulate_wind_farm: temperatura/presión no provistas; "
            "usando rho=1.225 kg/m³ (nivel del mar). En sitios mexicanos de "
            "altitud el sesgo de AEP puede ser 8-12%.",
            stacklevel=2,
        )
        rho_mean = 1.225

    # ── inferir paso temporal: si ws es Series con DatetimeIndex usa la moda
    # de los deltas; si no, asume 1.0 h (datos horarios MERRA-2/ERA5)
    if isinstance(ws, pd.Series) and isinstance(ws.index, pd.DatetimeIndex) and n_samples > 1:
        dt_h = ws.index.to_series().diff().mode()[0].total_seconds() / 3600
    else:
        dt_h = 1.0
    total_hours = n_samples * dt_h

    sector_centres, ws_centres, freq = bin_wind_to_sectors(ws, wd, n_sectors)

    # No-wake baseline: total energy if every turbine saw free-stream wind.
    if local_wind_multipliers is not None:
        local_wind_multipliers = np.asarray(local_wind_multipliers, dtype=float).reshape(-1)
        if local_wind_multipliers.shape[0] != n:
            local_wind_multipliers = None
        else:
            local_wind_multipliers = np.nan_to_num(
                local_wind_multipliers,
                nan=1.0,
                posinf=1.0,
                neginf=1.0,
            )
            local_wind_multipliers = np.clip(local_wind_multipliers, 0.0, None)
    if local_wind_multipliers is not None:
        # Multiply (B, 1) by (1, N) to get (B, N) effective free-stream
        ws_centres_n = ws_centres[:, None] * local_wind_multipliers[None, :]
        p_free_per_bin = turbine.power_kw_density_corrected(ws_centres_n, rho_mean)  # (B, N)
        freq_sum = freq.sum(axis=0)  # (B,)
        e_free_per_turbine_kwh = np.sum(freq_sum[:, None] * p_free_per_bin, axis=0) * total_hours
    else:
        p_free_per_bin = turbine.power_kw_density_corrected(ws_centres, rho_mean)  # (B,)
        e_free_per_turbine_kwh = float(np.sum(freq.sum(axis=0) * p_free_per_bin)) * total_hours
        e_free_per_turbine_kwh = np.full(n, e_free_per_turbine_kwh)
        
    e_free_total_kwh = float(np.sum(e_free_per_turbine_kwh))

    # Wake-aware energy: for each (sector, ws_bin) compute effective ws per turbine
    # then power via curve, weight by joint frequency × hours.
    e_per_turbine_kwh = np.zeros(n, dtype=float)
    sector_freq = freq.sum(axis=1)  # P(sector)

    for s in range(n_sectors):
        if sector_freq[s] <= 0:
            continue
        wind_dir = sector_centres[s]
        for b, v_free in enumerate(ws_centres):
            p_join = freq[s, b]
            if p_join <= 0 or v_free <= 0:
                continue
            v_free_arr = v_free * local_wind_multipliers if local_wind_multipliers is not None else v_free
            v_eff = effective_wind_per_turbine(
                layout, v_free_arr, wind_dir, rotor_d, k=k_wake, ct=ct)
            p_kw = turbine.power_kw_density_corrected(v_eff, rho_mean)  # con corrección de densidad
            e_per_turbine_kwh += p_kw * p_join * total_hours

    # wake_loss aislado: comparar ANTES de aplicar losses operacionales
    # (ambos lados sin losses → wake puro, consistente con el per-turbine donde se cancelan)
    e_total_pre_losses_kwh = float(e_per_turbine_kwh.sum())
    wake_loss = (1.0 - e_total_pre_losses_kwh / e_free_total_kwh) if e_free_total_kwh > 0 else 0.0

    survival = float(availability) * (1.0 - float(electrical_losses))
    e_per_turbine_kwh *= survival
    e_total_kwh = float(e_per_turbine_kwh.sum())

    rated_kw = float(turbine.rated_power_kw)
    installed_kw = rated_kw * n
    cf = e_total_kwh / (installed_kw * total_hours) if installed_kw > 0 else 0.0
    dom_dir = dominant_direction(wd, n_sectors)

    # Per-turbine free-stream comparison (no-wake AEP per turbine)
    e_no_wake_per_turbine = e_free_per_turbine_kwh * survival
    per_turbine = []
    for i, (x, y) in enumerate(layout_xy):
        e_i = float(e_per_turbine_kwh[i])
        per_turbine.append({
            'i': i,
            'x_m': float(x),
            'y_m': float(y),
            'aep_mwh': round(e_i / 1000.0, 3),
            'aep_no_wake_mwh': round(e_no_wake_per_turbine[i] / 1000.0, 3),
            'wake_loss_pct': round(
                100.0 * (1 - e_i / e_no_wake_per_turbine[i]) if e_no_wake_per_turbine[i] > 0 else 0.0,
                2),
            'cf_pct': round(
                100.0 * e_i / (rated_kw * total_hours) if rated_kw > 0 else 0.0,
                2),
        })

    return {
        'n_turbines':     n,
        'rotor_d_m':      rotor_d,
        'rated_kw':       rated_kw,
        'installed_mw':   round(installed_kw / 1000.0, 3),
        'aep_mwh':        round(e_total_kwh / 1000.0, 2),
        'aep_no_wake_mwh': round(e_free_total_kwh * survival / 1000.0, 2),
        'wake_loss_pct':  round(100.0 * wake_loss, 2),
        'cf_pct':         round(100.0 * cf, 2),
        'flh':            round(e_total_kwh / installed_kw, 1) if installed_kw > 0 else 0.0,
        'dominant_dir_deg': round(dom_dir, 1),
        'hours':          round(total_hours, 1),
        'dt_h':           round(dt_h, 4),
        'rho_mean':       round(rho_mean, 4),
        'turbines':       per_turbine,
    }


# ── Coordinate utilities ──────────────────────────────────────────────────────

def _latlon_to_xy(lats, lons, origin_lat: float, origin_lon: float):
    """
    Project lat/lon to local Cartesian metres (equirectangular approximation).
    Accurate to <0.3 % for park areas up to ~50 km across.
    """
    cos_lat0 = math.cos(math.radians(origin_lat))
    xs = [(lon - origin_lon) * 111_320.0 * cos_lat0 for lon in lons]
    ys = [(lat - origin_lat) * 111_320.0             for lat in lats]
    return xs, ys


def _xy_to_latlon(xs_m, ys_m, origin_lat: float, origin_lon: float):
    """Inverse of _latlon_to_xy."""
    cos_lat0 = math.cos(math.radians(origin_lat))
    lats = [origin_lat + y / 111_320.0                      for y in ys_m]
    lons = [origin_lon + x / (111_320.0 * cos_lat0)         for x in xs_m]
    return lats, lons


def _point_in_polygon_xy(x: float, y: float, poly_xs, poly_ys) -> bool:
    """Ray-casting point-in-polygon test for Cartesian coordinates."""
    n = len(poly_xs)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly_xs[i], poly_ys[i]
        xj, yj = poly_xs[j], poly_ys[j]
        if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _sample_wind_grid(grid: Dict[str, Any], lat: float, lon: float) -> Optional[float]:
    """
    Bilinear interpolation on a kriging grid {z: [[float]], bounds: dict}
    at a single (lat, lon) point.
    """
    if not grid or 'z' not in grid or 'bounds' not in grid:
        return None
    b  = grid['bounds']
    z  = grid['z']
    ny = len(z)
    nx = len(z[0]) if ny else 0
    if nx < 2 or ny < 2:
        return None
    lat_r = b['lat_max'] - b['lat_min']
    lon_r = b['lon_max'] - b['lon_min']
    if lat_r <= 0 or lon_r <= 0:
        return None
    tj = (lat - b['lat_min']) / lat_r * (ny - 1)
    ti = (lon - b['lon_min']) / lon_r * (nx - 1)
    tj = max(0.0, min(ny - 1.0, tj))
    ti = max(0.0, min(nx - 1.0, ti))
    j0, i0 = int(tj), int(ti)
    j1, i1 = min(j0 + 1, ny - 1), min(i0 + 1, nx - 1)
    dj, di = tj - j0, ti - i0
    v = (float(z[j0][i0]) * (1 - dj) * (1 - di) +
         float(z[j0][i1]) * (1 - dj) * di +
         float(z[j1][i0]) * dj       * (1 - di) +
         float(z[j1][i1]) * dj       * di)
    return v if math.isfinite(v) else None


# ── Turbine-count recommendation ─────────────────────────────────────────────

def recommend_n_turbines(
        turbine: PowerCurve,
        demand_mwh: float,
        v_mean: float,
        weibull_k: float = 2.0,
        rho: float = 1.225,
        availability: float = 0.97,
        electrical_losses: float = 0.03,
        avg_wake_loss: float = 0.08,
        n_hours: int = 8760) -> dict:
    """
    Estimate the number of turbines required to satisfy an annual energy demand.

    Uses a synthetic Weibull wind series at the site mean speed to integrate
    the power curve and obtain a single-turbine net AEP, then divides the
    demand by that AEP (ceiling).

    Parameters
    ----------
    avg_wake_loss : float
        Assumed farm-average wake loss fraction.
        Barthelmie et al. (2010) report 5–15 % for large offshore farms;
        onshore compact layouts typically 5–10 %.

    References
    ----------
    - Barthelmie RJ et al. (2010). Modelling and measuring flow and wind
      turbine wakes in large wind farms offshore. Wind Energy 12:431-444.
    """
    from scipy.stats import weibull_min

    k = max(0.45, float(weibull_k))
    c = float(v_mean) / math.gamma(1.0 + 1.0 / k)

    probs = np.linspace(0.005, 0.995, int(n_hours))
    ws    = weibull_min.ppf(probs, k, scale=c)
    ws    = np.clip(ws, 0.0, None)

    p_kw      = turbine.power_kw_density_corrected(ws, float(rho))
    p_kw      = np.nan_to_num(p_kw, nan=0.0)
    aep_gross = float(np.sum(p_kw)) / 1000.0          # kWh → MWh

    survival  = float(availability) * (1.0 - float(electrical_losses))
    aep_net   = aep_gross * survival * (1.0 - float(avg_wake_loss))

    n_needed  = max(1, math.ceil(float(demand_mwh) / aep_net)) if aep_net > 0 else 1
    rated_kw  = float(turbine.rated_power_kw)
    cf        = aep_net / (rated_kw * n_hours / 1000.0) if rated_kw > 0 else 0.0

    return {
        'n_turbines':           n_needed,
        'aep_single_gross_mwh': round(aep_gross, 1),
        'aep_single_net_mwh':   round(aep_net,   1),
        'cf_pct':               round(100.0 * cf, 2),
        'coverage_pct':         round(100.0 * n_needed * aep_net / float(demand_mwh), 1)
                                if demand_mwh > 0 else 0.0,
        'avg_wake_loss_assumed': float(avg_wake_loss),
    }


# ── Greedy Sequential Layout Optimisation ────────────────────────────────────

def optimize_layout_greedy(
        polygon_latlon,
        turbine: PowerCurve,
        n_turbines: int,
        wind_grid: Optional[Dict[str, Any]] = None,
        fallback_wind_ms: float = 9.0,
        min_spacing_d: float = 5.0,
        grid_step_d: float = 1.5,
        k_wake: float = 0.075,
        ct: float = 0.8) -> dict:
    """
    Greedy Sequential Turbine Placement (GSP) within a park polygon.

    Turbines are placed one by one; each step selects the candidate grid
    position that maximises instantaneous power at the local kriging wind
    speed, discounted by the Jensen NOJ wake deficit of already-placed
    turbines (Katic superposition, single dominant direction per step).
    Candidate positions closer than min_spacing_d × D to any placed turbine
    are excluded before the next iteration.

    Algorithm
    ---------
    1. Project polygon to a local Cartesian frame (equirectangular).
    2. Generate a regular candidate grid spaced grid_step_d × D inside the
       polygon.  Sample wind speed from the kriging grid at each candidate.
    3. Greedy loop until n_turbines placed or no candidates remain:
       a. Score every remaining candidate: P(v_eff), where v_eff accounts
          for wake deficits from placed turbines (representative direction).
       b. Select highest-scoring candidate; remove it and all neighbours
          within min_spacing_d × D from the candidate set.

    References
    ----------
    - Feng J, Shen WZ (2015). Solving the wind farm layout optimization
      problem using random search algorithm. Renewable Energy 78:182-192.
    - Jensen NO (1983). A Note on Wind Generator Interaction. Risø NL-I-2411.
    - IEA Wind Task 31 (2014). Wakebench wind farm modelling guidelines.
      (min spacing ≥ 3 D; industry practice 5–7 D onshore)
    """
    D         = float(turbine.rotor_diameter_m or 80.0)
    min_sep_m = min_spacing_d * D
    step_m    = grid_step_d   * D

    # Normalise polygon vertices to [[lat, lon]]
    poly: List[Tuple[float, float]] = []
    for p in polygon_latlon:
        if isinstance(p, dict):
            poly.append((float(p['lat']), float(p['lon'])))
        else:
            poly.append((float(p[0]), float(p[1])))
    if len(poly) < 3:
        raise ValueError('El polígono del parque necesita al menos 3 vértices.')

    lats_p = [p[0] for p in poly]
    lons_p = [p[1] for p in poly]
    lat0   = sum(lats_p) / len(lats_p)
    lon0   = sum(lons_p) / len(lons_p)

    poly_xs, poly_ys = _latlon_to_xy(lats_p, lons_p, lat0, lon0)
    xmin = min(poly_xs); xmax = max(poly_xs)
    ymin = min(poly_ys); ymax = max(poly_ys)

    # Build candidate grid
    candidates: List[Dict[str, float]] = []
    x = xmin + step_m * 0.5
    while x <= xmax + step_m * 0.1:
        y = ymin + step_m * 0.5
        while y <= ymax + step_m * 0.1:
            if _point_in_polygon_xy(x, y, poly_xs, poly_ys):
                lats_c, lons_c = _xy_to_latlon([x], [y], lat0, lon0)
                v = (_sample_wind_grid(wind_grid, lats_c[0], lons_c[0])
                     if wind_grid else None)
                if v is None or not math.isfinite(v) or v <= 0:
                    v = fallback_wind_ms
                candidates.append({
                    'x': x, 'y': y,
                    'lat': lats_c[0], 'lon': lons_c[0],
                    'v': float(v),
                })
            y += step_m
        x += step_m

    if not candidates:
        raise ValueError(
            f'Sin candidatos dentro del polígono con D={D:.0f} m y paso de '
            f'grilla {grid_step_d}×D={step_m:.0f} m.  Reduce la separación '
            f'mínima o usa un aerogenerador de menor diámetro de rotor.')

    placed: List[Dict[str, float]] = []
    remaining = list(candidates)

    while len(placed) < n_turbines and remaining:
        placed_arr = (np.array([[p['x'], p['y']] for p in placed])
                      if placed else None)
        best_idx, best_score = -1, -math.inf

        for idx, c in enumerate(remaining):
            sq_sum = 0.0
            if placed_arr is not None:
                cand_pt = np.array([[c['x'], c['y']]])
                dom_dir = 270.0  # representative westerly; full sim uses all sectors
                pts_rot  = _rotate_to_wind_axis(placed_arr, dom_dir)
                cand_rot = _rotate_to_wind_axis(cand_pt,   dom_dir)[0]
                for pr in pts_rot:
                    dx = float(cand_rot[0]) - float(pr[0])
                    dy = float(cand_rot[1]) - float(pr[1])
                    sq_sum += jensen_deficit(dx, dy, D, k=k_wake, ct=ct) ** 2
            v_eff = max(0.0, c['v'] * (1.0 - math.sqrt(sq_sum)))
            score = float(turbine.power_kw(v_eff))
            if score > best_score:
                best_score = score
                best_idx   = idx

        chosen = remaining[best_idx]
        placed.append(chosen)
        remaining = [
            c for c in remaining
            if math.hypot(c['x'] - chosen['x'], c['y'] - chosen['y']) >= min_sep_m
        ]

    if not placed:
        raise ValueError(
            'No fue posible colocar turbinas. '
            'Verifica el área del polígono y la separación mínima.')

    layout_latlon = [[p['lat'], p['lon']] for p in placed]
    layout_xy_m   = [[p['x'],  p['y']]   for p in placed]

    # Shoelace polygon area
    n_p    = len(poly_xs)
    area_m2 = abs(sum(
        poly_xs[i] * poly_ys[(i + 1) % n_p] -
        poly_xs[(i + 1) % n_p] * poly_ys[i]
        for i in range(n_p)
    )) / 2.0

    n_placed   = len(placed)
    installed_kw = n_placed * float(turbine.rated_power_kw)

    return {
        'n_placed':          n_placed,
        'n_requested':       int(n_turbines),
        'n_candidates':      len(candidates),
        'layout_latlon':     layout_latlon,
        'layout_xy_m':       layout_xy_m,
        'wind_at_turbines':  [round(p['v'], 3) for p in placed],
        'origin_lat':        lat0,
        'origin_lon':        lon0,
        'D_m':               D,
        'min_sep_m':         round(min_sep_m, 1),
        'area_km2':          round(area_m2 / 1e6, 4),
        'installed_mw':      round(installed_kw / 1000.0, 3),
        'density_mw_km2':    round(
            installed_kw / 1000.0 / (area_m2 / 1e6) if area_m2 > 0 else 0.0, 3),
    }

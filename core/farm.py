"""
Wind farm AEP simulation with Jensen and Gaussian engineering wake models.

Given a layout of turbines (x, y in metres), a power curve, and a free-stream
wind time series with directions, computes per-turbine production accounting
for wake interactions using:

  - Jensen single-wake deficit (Jensen 1983)
  - Bastankhah-Porté-Agel Gaussian deficit (2014)
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


def gaussian_deficit(distance_x_m: float, lateral_m: float,
                     rotor_d_m: float, k: float = 0.04,
                     ct: float = 0.8) -> float:
    """Bastankhah-Porté-Agel Gaussian wake velocity deficit.

    This engineering model is used for pre-feasibility comparisons. ``k`` is
    the wake-expansion coefficient and must be calibrated with ambient
    turbulence when site measurements are available.
    """
    if distance_x_m <= 0 or rotor_d_m <= 0:
        return 0.0
    ct = max(0.0, min(0.999, float(ct)))
    sqrt_one_minus_ct = math.sqrt(max(1e-9, 1.0 - ct))
    beta = 0.5 * (1.0 + sqrt_one_minus_ct) / sqrt_one_minus_ct
    epsilon = 0.2 * math.sqrt(beta)
    sigma_over_d = max(epsilon, float(k) * distance_x_m / rotor_d_m + epsilon)
    radical = max(0.0, 1.0 - ct / (8.0 * sigma_over_d ** 2))
    centre = 1.0 - math.sqrt(radical)
    sigma_m = sigma_over_d * rotor_d_m
    lateral = math.exp(-0.5 * (abs(lateral_m) / max(sigma_m, 1e-9)) ** 2)
    return max(0.0, min(1.0, centre * lateral))


def effective_wind_per_turbine(layout_xy: np.ndarray, v_free,
                               wind_dir_deg: float, rotor_d_m: float,
                               k: float = 0.075, ct: float = 0.8,
                               wake_model: str = 'jensen') -> np.ndarray:
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
            deficit_fn = gaussian_deficit if wake_model == 'gaussian' else jensen_deficit
            d_eff = deficit_fn(dx, dy, rotor_d_m, k=k, ct=ct)
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


def energy_weighted_dominant_direction(
        ws: np.ndarray, wd: np.ndarray,
        turbine: 'PowerCurve',
        n_sectors: int = 16,
        rho: float = 1.225,
) -> Tuple[float, np.ndarray, np.ndarray]:
    """
    Energy-weighted dominant wind direction from a 16-sector wind rose.

    Weights each direction sector by its total power contribution
    (P(v) × frequency), which is more representative than simple frequency
    because it accounts for how much energy each sector actually delivers.

    Returns
    -------
    dominant_dir_deg : float   — centre of the highest-energy sector
    sector_dirs_deg  : ndarray — centre direction of each of the n_sectors
    sector_energy_pct: ndarray — energy fraction per sector (sums to 100)
    """
    valid = np.isfinite(ws) & np.isfinite(wd) & (ws > 0)
    sector_width = 360.0 / n_sectors
    sector_dirs = np.arange(n_sectors) * sector_width + sector_width / 2.0

    if not valid.any():
        return 270.0, sector_dirs, np.ones(n_sectors) / n_sectors * 100.0

    ws_v = ws[valid]
    wd_v = wd[valid] % 360.0

    sector_energy = np.zeros(n_sectors)
    p_kw = turbine.power_kw_density_corrected(ws_v, rho)
    sector_idx = (wd_v / sector_width).astype(int) % n_sectors
    np.add.at(sector_energy, sector_idx, p_kw)

    total_e = sector_energy.sum()
    sector_energy_pct = (sector_energy / total_e * 100.0) if total_e > 0 \
        else np.ones(n_sectors) / n_sectors * 100.0

    dom_sector = int(np.argmax(sector_energy_pct))
    dom_dir = float(sector_dirs[dom_sector])
    return dom_dir, sector_dirs, sector_energy_pct


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
                       local_wind_multipliers: Optional[np.ndarray] = None,
                       wake_model: str = 'jensen',
                       rho_override: Optional[float] = None) -> dict:
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
    if rho_override is not None and math.isfinite(float(rho_override)) and float(rho_override) > 0:
        rho_mean = float(rho_override)
    elif temperature_c is not None and pressure_pa is not None:
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
    annualization_factor = 8760.0 / total_hours if total_hours > 0 else 1.0

    sector_centres, ws_centres, freq = bin_wind_to_sectors(ws, wd, n_sectors)

    if freq.sum() == 0 or (len(ws_centres) == 1 and ws_centres[0] == 0.0):
        warnings.warn(
            'simulate_wind_farm: serie de viento sin datos válidos '
            '(todos NaN o vacía). Devuelve AEP=0.',
            stacklevel=2,
        )
        return {'error': 'wind_series_invalid',
                'n_turbines': n, 'installed_mw': round(n * float(turbine.rated_power_kw) / 1000, 3),
                'aep_mwh': 0, 'aep_no_wake_mwh': 0, 'wake_loss_pct': 0,
                'cf_pct': 0, 'flh': 0, 'dominant_dir_deg': 0,
                'hours': round(total_hours, 1), 'dt_h': round(dt_h, 4), 'rho_mean': round(rho_mean, 4),
                'years_in_record': round(total_hours / 8760.0, 4) if total_hours > 0 else 0,
                'annualization_factor': round(annualization_factor, 6),
                'turbines': [{'i': i, 'x_m': float(layout_xy[i][0]), 'y_m': float(layout_xy[i][1]),
                               'aep_mwh': 0, 'aep_no_wake_mwh': 0, 'wake_loss_pct': 0, 'cf_pct': 0}
                              for i in range(n)]}

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
                layout, v_free_arr, wind_dir, rotor_d, k=k_wake, ct=ct,
                wake_model=wake_model)
            p_kw = turbine.power_kw_density_corrected(v_eff, rho_mean)  # con corrección de densidad
            e_per_turbine_kwh += p_kw * p_join * total_hours

    # wake_loss aislado: comparar ANTES de aplicar losses operacionales
    # (ambos lados sin losses → wake puro, consistente con el per-turbine donde se cancelan)
    e_total_pre_losses_kwh = float(e_per_turbine_kwh.sum())
    wake_loss = (1.0 - e_total_pre_losses_kwh / e_free_total_kwh) if e_free_total_kwh > 0 else 0.0

    survival = float(availability) * (1.0 - float(electrical_losses))
    e_per_turbine_kwh *= survival
    e_total_kwh = float(e_per_turbine_kwh.sum())
    e_per_turbine_annual_kwh = e_per_turbine_kwh * annualization_factor
    e_total_annual_kwh = float(e_per_turbine_annual_kwh.sum())

    rated_kw = float(turbine.rated_power_kw)
    installed_kw = rated_kw * n
    cf = e_total_annual_kwh / (installed_kw * 8760.0) if installed_kw > 0 else 0.0
    dom_dir = dominant_direction(wd, n_sectors)

    # Per-turbine free-stream comparison:
    #   aep_gross_mwh  → BRUTO (sin estela, sin pérdidas operativas)
    #   aep_no_wake_mwh → con survival, sin estela (referencia "limpia" pre-wake)
    e_gross_per_turbine = e_free_per_turbine_kwh * annualization_factor
    e_no_wake_per_turbine = e_free_per_turbine_kwh * survival * annualization_factor
    per_turbine = []
    for i, (x, y) in enumerate(layout_xy):
        e_i = float(e_per_turbine_annual_kwh[i])
        per_turbine.append({
            'i': i,
            'x_m': float(x),
            'y_m': float(y),
            'aep_mwh': round(e_i / 1000.0, 3),
            'aep_gross_mwh': round(e_gross_per_turbine[i] / 1000.0, 3),
            'aep_no_wake_mwh': round(e_no_wake_per_turbine[i] / 1000.0, 3),
            'wake_loss_pct': round(
                100.0 * (1 - e_i / e_no_wake_per_turbine[i]) if e_no_wake_per_turbine[i] > 0 else 0.0,
                2),
            'cf_pct': round(
                100.0 * e_i / (rated_kw * 8760.0) if rated_kw > 0 else 0.0,
                2),
            'cf_gross_pct': round(
                100.0 * e_gross_per_turbine[i] / (rated_kw * 8760.0) if rated_kw > 0 else 0.0,
                2),
        })

    # AEP "bruto" — sin estela y SIN pérdidas operativas (avail + electrical),
    # útil como referencia teórica (CF ideal) y para auditar el desglose.
    aep_gross_mwh = e_free_total_kwh * annualization_factor / 1000.0
    aep_pre_wake_mwh = e_free_total_kwh * survival * annualization_factor / 1000.0
    cf_gross = (aep_gross_mwh * 1000.0) / (installed_kw * 8760.0) if installed_kw > 0 else 0.0

    return {
        'n_turbines':     n,
        'rotor_d_m':      rotor_d,
        'rated_kw':       rated_kw,
        'installed_mw':   round(installed_kw / 1000.0, 3),
        'aep_mwh':        round(e_total_annual_kwh / 1000.0, 2),
        'aep_no_wake_mwh': round(aep_pre_wake_mwh, 2),
        'aep_gross_mwh':   round(aep_gross_mwh, 2),
        'aep_net_pre_wake_mwh': round(aep_pre_wake_mwh, 2),
        'cf_gross_pct':    round(100.0 * cf_gross, 2),
        'survival_factor': round(survival, 4),
        'record_energy_mwh': round(e_total_kwh / 1000.0, 2),
        'record_energy_no_wake_mwh': round(e_free_total_kwh * survival / 1000.0, 2),
        'wake_loss_pct':  round(100.0 * wake_loss, 2),
        'wake_model':     wake_model,
        'cf_pct':         round(100.0 * cf, 2),
        'flh':            round(e_total_annual_kwh / installed_kw, 1) if installed_kw > 0 else 0.0,
        'dominant_dir_deg': round(dom_dir, 1),
        'hours':          round(total_hours, 1),
        'dt_h':           round(dt_h, 4),
        'years_in_record': round(total_hours / 8760.0, 4) if total_hours > 0 else 0,
        'annualization_factor': round(annualization_factor, 6),
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


def _distance_to_polygon_edges_xy(x: float, y: float, poly_xs, poly_ys) -> float:
    """Shortest Cartesian distance from a point to a polygon boundary."""
    best = math.inf
    for i in range(len(poly_xs)):
        x1, y1 = float(poly_xs[i]), float(poly_ys[i])
        x2, y2 = float(poly_xs[(i + 1) % len(poly_xs)]), float(poly_ys[(i + 1) % len(poly_ys)])
        dx, dy = x2 - x1, y2 - y1
        denom = dx * dx + dy * dy
        t = 0.0 if denom <= 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / denom))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


def _point_allowed_xy(x: float, y: float, poly_xs, poly_ys,
                      exclusion_polys_xy=None, boundary_setback_m: float = 0.0) -> bool:
    if not _point_in_polygon_xy(x, y, poly_xs, poly_ys):
        return False
    if boundary_setback_m > 0 and _distance_to_polygon_edges_xy(x, y, poly_xs, poly_ys) < boundary_setback_m:
        return False
    for ex_xs, ex_ys in exclusion_polys_xy or []:
        if (_point_in_polygon_xy(x, y, ex_xs, ex_ys)
                or (boundary_setback_m > 0
                    and _distance_to_polygon_edges_xy(x, y, ex_xs, ex_ys) < boundary_setback_m)):
            return False
    return True


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
        ct: float = 0.8,
        exclusion_polygons=None,
        boundary_setback_m: float = 0.0,
        wake_model: str = 'jensen') -> dict:
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
    exclusion_polys_xy = []
    for exclusion in exclusion_polygons or []:
        ex_latlon = [
            (float(p['lat']), float(p['lon'])) if isinstance(p, dict)
            else (float(p[0]), float(p[1])) for p in exclusion
        ]
        if len(ex_latlon) >= 3:
            exclusion_polys_xy.append(_latlon_to_xy(
                [p[0] for p in ex_latlon], [p[1] for p in ex_latlon], lat0, lon0))
    xmin = min(poly_xs); xmax = max(poly_xs)
    ymin = min(poly_ys); ymax = max(poly_ys)

    # Build candidate grid
    candidates: List[Dict[str, float]] = []
    x = xmin + step_m * 0.5
    while x <= xmax + step_m * 0.1:
        y = ymin + step_m * 0.5
        while y <= ymax + step_m * 0.1:
            if _point_allowed_xy(x, y, poly_xs, poly_ys, exclusion_polys_xy,
                                 max(0.0, float(boundary_setback_m))):
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
                    deficit_fn = gaussian_deficit if wake_model == 'gaussian' else jensen_deficit
                    sq_sum += deficit_fn(dx, dy, D, k=k_wake, ct=ct) ** 2
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
        'max_n_fit':          n_placed,
        'boundary_setback_m': round(max(0.0, float(boundary_setback_m)), 2),
        'exclusion_count':    len(exclusion_polys_xy),
        'wake_model':         wake_model,
    }


# ── Structured (rose-aligned) layout helpers ─────────────────────────────────

def _build_structured_grid(
        poly_xs: list, poly_ys: list,
        lat0: float, lon0: float,
        D: float,
        dominant_dir_deg: float,
        s_para_d: float, s_perp_d: float,
        stagger: float,
        wind_grid, fallback_wind_ms: float,
        exclusion_polys_xy=None,
        boundary_setback_m: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Generate candidate turbine positions on a structured grid aligned with
    the dominant wind direction.

    Grid axes (in local Cartesian, x=East, y=North):
      u_down  = unit vector in the downwind direction (wind blows toward)
      u_cross = unit vector 90° clockwise from u_down (cross-wind)

    Rows (lines of turbines perpendicular to wind) are spaced s_para_d × D
    along u_down.  Within each row, turbines are spaced s_perp_d × D along
    u_cross.  Odd-numbered rows are shifted by stagger × s_perp_d × D in
    the crosswind direction (tresbolillo/quincunx when stagger=0.5).
    """
    toward_deg = (dominant_dir_deg + 180.0) % 360.0
    tr = math.radians(toward_deg)
    u_down  = np.array([ math.sin(tr),  math.cos(tr)])   # downwind (E,N)
    u_cross = np.array([ math.cos(tr), -math.sin(tr)])   # cross-wind

    s_para_m = s_para_d * D
    s_perp_m = s_perp_d * D
    stag_m   = stagger * s_perp_m

    # Project polygon vertices onto wind-aligned axes for extent
    poly_arr = np.array([poly_xs, poly_ys]).T
    along_proj = poly_arr @ u_down
    cross_proj = poly_arr @ u_cross

    along_min = along_proj.min() - s_para_m
    along_max = along_proj.max() + s_para_m
    cross_min = cross_proj.min() - s_perp_m
    cross_max = cross_proj.max() + s_perp_m

    candidates: List[Dict[str, Any]] = []
    r = 0
    a = along_min
    while a <= along_max + 1.0:
        row_stag = (r % 2) * stag_m
        c = cross_min
        while c <= cross_max + 1.0:
            x = a * u_down[0] + (c + row_stag) * u_cross[0]
            y = a * u_down[1] + (c + row_stag) * u_cross[1]
            if _point_allowed_xy(
                    x, y, poly_xs, poly_ys, exclusion_polys_xy,
                    boundary_setback_m):
                lats_c, lons_c = _xy_to_latlon([x], [y], lat0, lon0)
                v = (_sample_wind_grid(wind_grid, lats_c[0], lons_c[0])
                     if wind_grid else None)
                if v is None or not math.isfinite(v) or v <= 0:
                    v = fallback_wind_ms
                candidates.append({
                    'x': x, 'y': y,
                    'lat': lats_c[0], 'lon': lons_c[0],
                    'v': float(v),
                    'row': r,
                })
            c += s_perp_m
        a += s_para_m
        r += 1
    return candidates


def _min_pair_distance(layout_xy: List[List[float]], D: float) -> float:
    """Return the minimum pairwise distance as multiples of D."""
    if len(layout_xy) < 2:
        return 999.0
    arr = np.array(layout_xy, dtype=float)
    diffs = arr[:, None, :] - arr[None, :, :]   # (N,N,2)
    dists = np.sqrt((diffs ** 2).sum(axis=2))    # (N,N)
    np.fill_diagonal(dists, np.inf)
    return float(dists.min()) / D


def _score_layout_fast(
        layout_xy: List[List[float]], D: float,
        sector_dirs: np.ndarray, sector_weights: np.ndarray,
        turbine: 'PowerCurve', k_wake: float, ct: float,
        v_mean: float, rho: float = 1.225,
        wake_model: str = 'jensen',
) -> float:
    """
    Fast energy score: sector-weighted average farm power [kW].
    Loops over the 16 energy sectors, computes Jensen wake for each,
    returns expected farm power weighted by sector energy fraction.
    """
    if not layout_xy:
        return 0.0
    arr = np.array(layout_xy, dtype=float)
    score = 0.0
    for s_dir, s_w in zip(sector_dirs.tolist(), sector_weights.tolist()):
        if s_w <= 0.0:
            continue
        v_eff = effective_wind_per_turbine(arr, v_mean, float(s_dir), D,
                                           k=k_wake, ct=ct,
                                           wake_model=wake_model)
        p_kw  = turbine.power_kw_density_corrected(v_eff, rho)
        score += float(np.sum(p_kw)) * s_w
    return score


# ── Structured layout optimisation ───────────────────────────────────────────

def optimize_layout_structured(
        polygon_latlon,
        turbine: PowerCurve,
        n_turbines: int,
        ws: Optional[np.ndarray] = None,
        wd: Optional[np.ndarray] = None,
        wind_grid: Optional[Dict[str, Any]] = None,
        fallback_wind_ms: float = 9.0,
        k_wake: float = 0.075,
        ct: float = 0.8,
        min_spacing_d: float = 3.0,
        s_para_range: Tuple = (5, 6, 7, 8, 10),
        s_perp_range:  Tuple = (3, 4, 5, 6),
        stagger_options: Tuple = (0.0, 0.5),
        n_rose_sectors: int = 16,
        rho: float = 1.225,
        exclusion_polygons=None,
        boundary_setback_m: float = 0.0,
        wake_model: str = 'jensen',
) -> dict:
    """
    Wind-rose-aligned structured layout optimisation.

    Searches over combinations of:
      s_para_d  — along-wind row spacing (rotor diameters)
      s_perp_d  — cross-wind column spacing (rotor diameters)
      stagger   — 0.0 = aligned grid, 0.5 = tresbolillo (quincunx):
                  alternate rows shifted s_perp_d/2 in the crosswind
                  direction so each downwind turbine sits between two
                  upwind neighbours, avoiding direct wake exposure.

    The grid is rotated to align rows perpendicular to the dominant wind
    direction, which is computed as the energy-weighted maximum of the
    16-sector wind rose (power × frequency per sector).

    After finding the best configuration, falls back to optimize_layout_greedy
    if no valid structured layout could be built.

    Returns a dict compatible with optimize_layout_greedy output, plus:
      layout_config      — chosen (s_para_d, s_perp_d, stagger, dom_dir)
      top_configs        — top-5 ranked configurations tried
      sector_energy_pct  — energy % per rose sector
      sector_dirs_deg    — direction centre of each sector
    """
    D         = float(turbine.rotor_diameter_m or 80.0)
    min_sep_m = min_spacing_d * D

    # Parse polygon
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
    lat0 = sum(lats_p) / len(lats_p)
    lon0 = sum(lons_p) / len(lons_p)
    poly_xs, poly_ys = _latlon_to_xy(lats_p, lons_p, lat0, lon0)
    exclusion_polys_xy = []
    for exclusion in exclusion_polygons or []:
        ex_latlon = [
            (float(p['lat']), float(p['lon'])) if isinstance(p, dict)
            else (float(p[0]), float(p[1]))
            for p in exclusion
        ]
        if len(ex_latlon) >= 3:
            ex_xs, ex_ys = _latlon_to_xy(
                [p[0] for p in ex_latlon], [p[1] for p in ex_latlon], lat0, lon0)
            exclusion_polys_xy.append((ex_xs, ex_ys))

    n_p = len(poly_xs)
    area_m2 = abs(sum(
        poly_xs[i] * poly_ys[(i + 1) % n_p] -
        poly_xs[(i + 1) % n_p] * poly_ys[i]
        for i in range(n_p)
    )) / 2.0

    rho_val = float(rho) if rho and math.isfinite(float(rho)) and rho > 0 else 1.225

    # ── Energy-weighted dominant direction from wind rose ──────────────────
    if ws is not None and wd is not None and len(ws) > 0:
        dom_dir, sector_dirs, sector_energy_pct = energy_weighted_dominant_direction(
            ws, wd, turbine, n_sectors=n_rose_sectors, rho=rho_val,
        )
        ws_valid = ws[np.isfinite(ws) & (ws > 0)]
        v_mean = float(np.mean(ws_valid)) if len(ws_valid) > 0 else fallback_wind_ms
    else:
        dom_dir = 270.0
        sector_dirs = np.linspace(0.0, 360.0, n_rose_sectors, endpoint=False) \
                      + 180.0 / n_rose_sectors
        sector_energy_pct = np.ones(n_rose_sectors) / n_rose_sectors * 100.0
        v_mean = fallback_wind_ms

    sector_weights = sector_energy_pct / 100.0

    # ── Search over configuration space ───────────────────────────────────
    all_configs: List[Dict[str, Any]] = []

    for s_para_d in s_para_range:
        for s_perp_d in s_perp_range:
            for stagger in stagger_options:
                # Minimum distance check for this configuration
                # Same-row neighbour: s_perp_d × D
                # Staggered next-row diagonal: sqrt((stagger × s_perp_d)² + s_para_d²) × D
                min_same_row_d   = float(s_perp_d)
                if stagger > 0:
                    min_diag_d = math.hypot(stagger * s_perp_d, s_para_d)
                else:
                    min_diag_d = float(s_para_d)
                effective_min_d  = min(min_same_row_d, min_diag_d)
                if effective_min_d < min_spacing_d:
                    continue

                cands = _build_structured_grid(
                    poly_xs, poly_ys, lat0, lon0, D,
                    dom_dir, s_para_d, s_perp_d, stagger,
                    wind_grid, v_mean, exclusion_polys_xy,
                    max(0.0, float(boundary_setback_m)),
                )
                if not cands:
                    continue

                # A requested farm size is a hard feasibility constraint. A
                # configuration that fits fewer positions is not an
                # alternative for that request, even if its smaller layout
                # happens to have a higher aggregate score.
                if n_turbines > 0 and len(cands) < n_turbines:
                    continue

                # Trim to n_turbines: prefer highest-wind-speed positions
                if n_turbines > 0 and len(cands) > n_turbines:
                    sel = sorted(cands, key=lambda c: -c['v'])[:n_turbines]
                else:
                    sel = cands

                if not sel:
                    continue

                layout_xy = [[c['x'], c['y']] for c in sel]
                score = _score_layout_fast(
                    layout_xy, D, sector_dirs, sector_weights,
                    turbine, k_wake, ct, v_mean, rho_val,
                    wake_model=wake_model,
                )
                all_configs.append({
                    's_para_d':  float(s_para_d),
                    's_perp_d':  float(s_perp_d),
                    'stagger':   float(stagger),
                    'n_fit':     len(cands),
                    'n_used':    len(sel),
                    'score_kw':  round(score, 1),
                    '_layout_xy': layout_xy,
                    '_candidates': sel,
                })

    # ── Fall back to greedy if no structured config found ─────────────────
    if not all_configs:
        result = optimize_layout_greedy(
            polygon_latlon, turbine, n_turbines,
            wind_grid=wind_grid, fallback_wind_ms=fallback_wind_ms,
            min_spacing_d=min_spacing_d, k_wake=k_wake, ct=ct,
            exclusion_polygons=exclusion_polygons,
            boundary_setback_m=boundary_setback_m,
            wake_model=wake_model,
        )
        result['layout_config'] = {
            's_para_d': None, 's_perp_d': None, 'stagger': 0.0,
            'dominant_dir_deg': round(dom_dir, 1),
            'layout_type': 'greedy_fallback',
            'n_configs_tried': 0,
        }
        result['sector_energy_pct'] = [round(v, 2) for v in sector_energy_pct.tolist()]
        result['sector_dirs_deg']   = [round(v, 1) for v in sector_dirs.tolist()]
        return result

    best = max(all_configs, key=lambda c: c['score_kw'])
    capacity = max(all_configs, key=lambda c: (c['n_fit'], c['score_kw']))
    placed       = best['_candidates']
    layout_latlon = [[p['lat'], p['lon']] for p in placed]
    layout_xy_m   = [[p['x'],  p['y']]   for p in placed]
    n_placed      = len(placed)
    installed_kw  = n_placed * float(turbine.rated_power_kw)

    top5 = sorted(all_configs, key=lambda c: -c['score_kw'])[:5]
    top5_clean = [
        {k: v for k, v in c.items() if not k.startswith('_')}
        for c in top5
    ]

    return {
        'n_placed':         n_placed,
        'n_requested':      int(n_turbines),
        'n_candidates':     len(placed),
        'max_n_fit':        int(capacity['n_fit']),
        'layout_latlon':    layout_latlon,
        'layout_xy_m':      layout_xy_m,
        'wind_at_turbines': [round(p['v'], 3) for p in placed],
        'origin_lat':       lat0,
        'origin_lon':       lon0,
        'D_m':              D,
        'min_sep_m':        round(min_sep_m, 1),
        'area_km2':         round(area_m2 / 1e6, 4),
        'installed_mw':     round(installed_kw / 1000.0, 3),
        'density_mw_km2':   round(
            installed_kw / 1000.0 / (area_m2 / 1e6) if area_m2 > 0 else 0.0, 3),
        'layout_config': {
            's_para_d':         best['s_para_d'],
            's_perp_d':         best['s_perp_d'],
            'stagger':          best['stagger'],
            'dominant_dir_deg': round(dom_dir, 1),
            'layout_type':      'tresbolillo' if best['stagger'] >= 0.25 else 'alineado',
            'n_configs_tried':  len(all_configs),
        },
        'capacity_config': {
            's_para_d': capacity['s_para_d'],
            's_perp_d': capacity['s_perp_d'],
            'stagger': capacity['stagger'],
            'n_fit': int(capacity['n_fit']),
        },
        'boundary_setback_m': round(max(0.0, float(boundary_setback_m)), 2),
        'exclusion_count': len(exclusion_polys_xy),
        'wake_model': wake_model,
        'top_configs':          top5_clean,
        'sector_energy_pct':    [round(v, 2) for v in sector_energy_pct.tolist()],
        'sector_dirs_deg':      [round(v, 1) for v in sector_dirs.tolist()],
    }

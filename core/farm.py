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
from typing import List, Tuple, Optional, Dict, Any, Callable

from core.energy import PowerCurve, air_density
from core.terrain import sample_slope_grid_deg


class WakeModelDomainError(ValueError):
    """Raised when a layout requests a wake model outside its valid domain."""


# Piso técnico duro de separación entre turbinas, en diámetros de rotor.
# No es un valor de UI ni negociable por el usuario: por debajo de esto el
# modelo de estela Jensen deja de ser válido en campo cercano y las cargas
# de fatiga por turbulencia inducida (IEC 61400-1) se vuelven la restricción
# real, no la pérdida por estela. La búsqueda de layout puede explorar
# separaciones más cerradas que el valor de referencia del usuario, pero
# nunca por debajo de este piso.
TECHNICAL_MIN_SPACING_D = 2.5


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


def compute_row_column_spacing_d(layout_xy_m, dom_dir_deg: float, D: float,
                                  axis_tolerance_deg: float = 20.0) -> Dict[str, Any]:
    """Separación EMPÍRICA fila/columna (distancia al vecino más cercano, en
    diámetros de rotor D) a lo largo de los ejes alineados al viento
    dominante -- no la separación NOMINAL pedida al generar el layout
    (s_para_d/s_perp_d, que solo describe la rejilla rígida que
    optimize_layout_structured intentó construir, no lo que realmente quedó
    tras recorte por polígono/exclusiones/boundary_setback), y aplicable a
    CUALQUIER familia, incluida Greedy-vértice, que no tiene rejilla fija.

    Misma convención de vecino-por-eje que optimize_layout_greedy_vertex
    (axis_tolerance_deg): fila = vecino más cercano dentro de esa tolerancia
    angular del eje downwind (+/-); columna = ídem del eje crosswind (+/-).
    Una turbina sin ningún vecino dentro de tolerancia en un eje (borde del
    polígono, o un hueco aislado como el que motivó esta métrica) queda
    fuera de esa estadística -- 'n_measured' lo reporta explícitamente en
    vez de forzar un promedio con menos datos de los que N sugeriría.
    """
    points = np.asarray(layout_xy_m, dtype=float)
    n = len(points)
    empty = {'mean_d': None, 'min_d': None, 'max_d': None, 'n_measured': 0}
    if n < 2 or not D or not math.isfinite(D) or D <= 0:
        return {'row': dict(empty), 'column': dict(empty), 'n_turbines': n}
    pts_rot = _rotate_to_wind_axis(points, dom_dir_deg)
    tol_rad = math.radians(max(1.0, float(axis_tolerance_deg)))
    axis_dirs = {'row': ((1.0, 0.0), (-1.0, 0.0)), 'column': ((0.0, 1.0), (0.0, -1.0))}
    measured = {'row': [], 'column': []}
    for i in range(n):
        deltas = pts_rot - pts_rot[i]
        dist = np.hypot(deltas[:, 0], deltas[:, 1])
        dist[i] = np.inf
        for label, dirs in axis_dirs.items():
            best = math.inf
            for ax, ay in dirs:
                with np.errstate(invalid='ignore', divide='ignore'):
                    cosang = np.clip((deltas[:, 0] * ax + deltas[:, 1] * ay) / dist, -1.0, 1.0)
                angle_ok = np.arccos(cosang) <= tol_rad
                candidates = dist[angle_ok & np.isfinite(dist)]
                if candidates.size:
                    best = min(best, float(candidates.min()))
            if math.isfinite(best):
                measured[label].append(best / D)
    result: Dict[str, Any] = {'n_turbines': n}
    for label in ('row', 'column'):
        values = measured[label]
        result[label] = ({'mean_d': round(float(np.mean(values)), 3),
                          'min_d': round(float(np.min(values)), 3),
                          'max_d': round(float(np.max(values)), 3),
                          'n_measured': len(values)}
                         if values else dict(empty))
    return result


def jensen_deficit(distance_x_m: float, lateral_m: float,
                   rotor_d_m: float, k: float = 0.075,  # IEA onshore estándar; Istmo TI alto usar 0.09-0.10
                   ct: float = 0.8) -> float:  # Ct promedio operacional. Para mayor precisión usar curva Ct(v) del fabricante
    """
    Jensen NOJ single-wake velocity deficit at a downwind turbine,
    including an EXACT circular partial-overlap correction (area of
    intersection of the wake disc and the downwind rotor disc, as a
    fraction of the rotor's own area -- Katic et al. 1986 convention, same
    one PyWake's default AreaOverlapAvgModel uses).

    Corregido 2026-09-22 tras validación cruzada contra PyWake (ver
    validation/control_case_jensen_pywake.py): la versión anterior usaba
    una aproximación LINEAL del traslape ((r_wake - dy) / r0) que llegaba a
    CERO en dy = r_wake en vez de en dy = r_wake + r0 -- el punto
    geométricamente correcto donde el disco de estela deja de tocar el
    rotor. Eso subestimaba la estela (hasta ~44% del área del rotor
    ignorada) en toda la franja dy ∈ (r_wake, r_wake+r0) -- justo la zona
    donde un patrón tresbolillo/staggered coloca turbinas a propósito para
    esquivar la estela directa, así que ese patrón podía salir favorecido
    en VAN por un artefacto del modelo, no por su mérito físico real.
    Verificado con la fórmula exacta de intersección de dos círculos, en
    coincidencia perfecta con PyWake (NOJDeficit, ct2a=ct2a_mom1d,
    superposición Katic) en línea (dy=0, 0.00% de diferencia) y con
    diferencia explicada solo por el redondeo numérico fuera de ese eje.

    distance_x_m: distance downwind from upwind turbine (>0 if downwind, else 0).
    lateral_m:    perpendicular distance to wake centreline.
    rotor_d_m:    rotor diameter of the upwind turbine.
    k:            wake decay (0.04 offshore, 0.05–0.075 onshore).
    ct:           thrust coefficient. ct=0.8 es CONSERVADOR en carga parcial;
                  el Ct real suele bajar al alcanzar y superar la potencia nominal.
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
    # Exact overlap area of two circles (rotor r0, wake r_wake, centres dy
    # apart), as a fraction of the rotor's own area. r_wake > r0 always
    # here (r_wake = r0 + k*distance_x_m, distance_x_m > 0), so "one disc
    # fully inside the other" always means the ROTOR is inside the wake,
    # i.e. overlap = 1.0 -- never the reverse.
    if dy <= r_wake - r0:
        overlap = 1.0
    else:
        cos_alpha = max(-1.0, min(1.0, (dy ** 2 + r0 ** 2 - r_wake ** 2) / (2.0 * dy * r0)))
        cos_beta = max(-1.0, min(1.0, (dy ** 2 + r_wake ** 2 - r0 ** 2) / (2.0 * dy * r_wake)))
        alpha = math.acos(cos_alpha)
        beta = math.acos(cos_beta)
        area = (r0 ** 2 * (alpha - math.sin(2.0 * alpha) / 2.0)
                + r_wake ** 2 * (beta - math.sin(2.0 * beta) / 2.0))
        overlap = max(0.0, min(1.0, area / (math.pi * r0 ** 2)))
    a = 0.5 * (1.0 - math.sqrt(max(0.0, 1.0 - ct)))
    deficit = 2.0 * a * (r0 / r_wake) ** 2 * overlap
    return max(0.0, min(1.0, deficit))


def gaussian_min_valid_distance_d(k: float = 0.04, ct: float = 0.8) -> float:
    """Minimum x/D where the Bastankhah centreline radical is real."""
    k = float(k)
    if not math.isfinite(k) or k <= 0:
        raise ValueError('k_wake debe ser positivo para el modelo gaussiano.')
    ct = max(0.0, min(0.999, float(ct)))
    sqrt_one_minus_ct = math.sqrt(max(1e-9, 1.0 - ct))
    beta = 0.5 * (1.0 + sqrt_one_minus_ct) / sqrt_one_minus_ct
    epsilon = 0.2 * math.sqrt(beta)
    return max(0.0, (math.sqrt(ct / 8.0) - epsilon) / k)


def gaussian_deficit(distance_x_m: float, lateral_m: float,
                     rotor_d_m: float, k: float = 0.04,
                     ct: float = 0.8) -> float:
    """Bastankhah-Porté-Agel Gaussian wake velocity deficit.

    This engineering model is used for pre-feasibility comparisons. ``k`` is
    the wake-expansion coefficient and must be calibrated with ambient
    turbulence when site measurements are available.

    Bastankhah & Porté-Agel (2014), Renewable Energy 70, 116–123,
    https://doi.org/10.1016/j.renene.2014.01.002
    """
    if distance_x_m <= 0:
        return 0.0
    if rotor_d_m <= 0:
        raise ValueError('rotor_d_m debe ser positivo.')
    ct = max(0.0, min(0.999, float(ct)))
    sqrt_one_minus_ct = math.sqrt(max(1e-9, 1.0 - ct))
    beta = 0.5 * (1.0 + sqrt_one_minus_ct) / sqrt_one_minus_ct
    epsilon = 0.2 * math.sqrt(beta)
    min_x_over_d = gaussian_min_valid_distance_d(k=k, ct=ct)
    x_over_d = float(distance_x_m) / float(rotor_d_m)
    if x_over_d < min_x_over_d:
        # The Gaussian has infinite mathematical support. Ignore only a tail
        # whose maximum possible deficit at the validity boundary is <0.01%;
        # a material near-wake interaction must not be fabricated by clipping.
        sigma_boundary_m = math.sqrt(ct / 8.0) * float(rotor_d_m)
        boundary_tail = math.exp(
            -0.5 * (abs(float(lateral_m)) / max(sigma_boundary_m, 1e-9)) ** 2)
        if boundary_tail <= 1e-4:
            return 0.0
        raise WakeModelDomainError(
            'Bastankhah-Porté-Agel fuera de dominio en la estela cercana: '
            f'x/D={x_over_d:.3f}, requiere x/D>={min_x_over_d:.3f} '
            f'para Ct={ct:.3f} y k={float(k):.4f}. '
            'Aumenta la separación, usa Jensen o aporta un modelo de estela cercana.'
        )
    sigma_over_d = float(k) * x_over_d + epsilon
    radical = 1.0 - ct / (8.0 * sigma_over_d ** 2)
    centre = 1.0 - math.sqrt(radical)
    sigma_m = sigma_over_d * rotor_d_m
    lateral = math.exp(-0.5 * (abs(lateral_m) / max(sigma_m, 1e-9)) ** 2)
    return max(0.0, min(1.0, centre * lateral))


def _jensen_deficit_matrix(dx: np.ndarray, dy: np.ndarray,
                           rotor_d_m: float, k: float, ct: float) -> np.ndarray:
    """Vectorized Jensen deficits with the same branches as ``jensen_deficit``
    -- exact circular overlap area (see that function's docstring for the
    2026-09-22 fix and why the previous linear approximation understated
    wake exposure for offset/staggered turbines)."""
    deficits = np.zeros_like(dx, dtype=float)
    upstream = dx > 0.0
    if not np.any(upstream):
        return deficits
    r0 = float(rotor_d_m) / 2.0
    dx_valid = dx[upstream]
    dy_valid = np.abs(dy[upstream])
    r_wake = r0 + float(k) * dx_valid
    overlap = np.zeros_like(dx_valid)
    full = dy_valid <= r_wake - r0
    partial = (~full) & (dy_valid < r_wake + r0)
    overlap[full] = 1.0
    if np.any(partial):
        d = dy_valid[partial]
        rw = r_wake[partial]
        with np.errstate(invalid='ignore', divide='ignore'):
            cos_alpha = np.clip((d ** 2 + r0 ** 2 - rw ** 2) / (2.0 * d * r0), -1.0, 1.0)
            cos_beta = np.clip((d ** 2 + rw ** 2 - r0 ** 2) / (2.0 * d * rw), -1.0, 1.0)
        alpha = np.arccos(cos_alpha)
        beta = np.arccos(cos_beta)
        area = (r0 ** 2 * (alpha - np.sin(2.0 * alpha) / 2.0)
                + rw ** 2 * (beta - np.sin(2.0 * beta) / 2.0))
        overlap[partial] = np.clip(area / (math.pi * r0 ** 2), 0.0, 1.0)
    ct_safe = max(0.0, 1.0 - float(ct))
    induction = 0.5 * (1.0 - math.sqrt(ct_safe))
    values = 2.0 * induction * np.square(r0 / r_wake) * overlap
    deficits[upstream] = np.clip(values, 0.0, 1.0)
    return deficits


def _gaussian_deficit_matrix(dx: np.ndarray, dy: np.ndarray,
                             rotor_d_m: float, k: float, ct: float) -> np.ndarray:
    """Vectorized Gaussian deficits while preserving near-wake rejection."""
    deficits = np.zeros_like(dx, dtype=float)
    upstream = dx > 0.0
    if not np.any(upstream):
        return deficits
    rotor = float(rotor_d_m)
    if rotor <= 0:
        raise ValueError('rotor_d_m debe ser positivo.')
    ct_safe = max(0.0, min(0.999, float(ct)))
    min_x_over_d = gaussian_min_valid_distance_d(k=k, ct=ct_safe)
    x_over_d = np.zeros_like(dx, dtype=float)
    x_over_d[upstream] = dx[upstream] / rotor
    near = upstream & (x_over_d < min_x_over_d)
    if np.any(near):
        sigma_boundary_m = math.sqrt(ct_safe / 8.0) * rotor
        boundary_tail = np.exp(
            -0.5 * np.square(np.abs(dy) / max(sigma_boundary_m, 1e-9)))
        material = near & (boundary_tail > 1e-4)
        if np.any(material):
            target, source = np.argwhere(material)[0]
            invalid_x_over_d = float(x_over_d[target, source])
            raise WakeModelDomainError(
                'Bastankhah-Porté-Agel fuera de dominio en la estela cercana: '
                f'x/D={invalid_x_over_d:.3f}, requiere x/D>={min_x_over_d:.3f} '
                f'para Ct={ct_safe:.3f} y k={float(k):.4f}. '
                'Aumenta la separación, usa Jensen o aporta un modelo de estela cercana.'
            )
    valid = upstream & ~near
    if not np.any(valid):
        return deficits
    sqrt_one_minus_ct = math.sqrt(max(1e-9, 1.0 - ct_safe))
    beta = 0.5 * (1.0 + sqrt_one_minus_ct) / sqrt_one_minus_ct
    epsilon = 0.2 * math.sqrt(beta)
    sigma_over_d = float(k) * x_over_d[valid] + epsilon
    radical = np.maximum(0.0, 1.0 - ct_safe / (8.0 * np.square(sigma_over_d)))
    centre = 1.0 - np.sqrt(radical)
    sigma_m = sigma_over_d * rotor
    lateral = np.exp(-0.5 * np.square(np.abs(dy[valid]) / np.maximum(sigma_m, 1e-9)))
    deficits[valid] = np.clip(centre * lateral, 0.0, 1.0)
    return deficits


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
    if v_free_arr.shape != (n,):
        raise ValueError('v_free debe ser escalar o contener un valor por turbina.')

    # Rows are target turbines and columns are possible upstream sources.
    dx = rotated[:, 0, None] - rotated[None, :, 0]
    dy = rotated[:, 1, None] - rotated[None, :, 1]
    deficits = (
        _gaussian_deficit_matrix(dx, dy, rotor_d_m, k, ct)
        if wake_model == 'gaussian'
        else _jensen_deficit_matrix(dx, dy, rotor_d_m, k, ct)
    )
    combined = np.sqrt(np.sum(np.square(deficits), axis=1))
    return v_free_arr * (1.0 - combined)


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
        sectors = np.linspace(0, 360, n_sectors, endpoint=False)
        return sectors, np.array([0.0]), np.zeros((n_sectors, 1))
    sector_width = 360.0 / n_sectors
    sector_idx = ((wd + sector_width / 2.0) % 360.0 / sector_width).astype(int) % n_sectors
    ws_max = max(1.0, math.ceil(ws.max() + 1))
    ws_bins = np.arange(0, ws_max + 1, 1.0)
    bin_idx = np.clip((ws / 1.0).astype(int), 0, len(ws_bins) - 2)
    n_b = len(ws_bins) - 1
    freq = np.zeros((n_sectors, n_b), dtype=float)
    for s, b in zip(sector_idx, bin_idx):
        freq[s, b] += 1
    freq /= freq.sum() if freq.sum() > 0 else 1
    sector_centres = np.arange(n_sectors) * sector_width
    ws_centres = ws_bins[:-1] + 0.5
    return sector_centres, ws_centres, freq


def dominant_direction(wd: np.ndarray, n_sectors: int = 36) -> float:
    """Return the centre of the most-frequent direction sector (degrees)."""
    valid = np.isfinite(wd)
    if not valid.any():
        return 0.0
    wd = wd[valid] % 360.0
    sector_width = 360.0 / n_sectors
    sector_idx = ((wd + sector_width / 2.0) % 360.0 / sector_width).astype(int) % n_sectors
    counts = np.bincount(sector_idx, minlength=n_sectors)
    s = int(np.argmax(counts))
    return float(s * sector_width)


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
    sector_dirs = np.arange(n_sectors) * sector_width

    if not valid.any():
        return 270.0, sector_dirs, np.ones(n_sectors) / n_sectors * 100.0

    ws_v = ws[valid]
    wd_v = wd[valid] % 360.0

    sector_energy = np.zeros(n_sectors)
    p_kw = turbine.power_kw_density_corrected(ws_v, rho)
    sector_idx = ((wd_v + sector_width / 2.0) % 360.0 / sector_width).astype(int) % n_sectors
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
                       rho_override: Optional[float] = None,
                       sample_duration_hours: Optional[float] = None,
                       wind_bins=None,
                       wind_summary=None) -> dict:
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
    For the Gaussian model, material interactions before the first real-valued
    Bastankhah solution are rejected instead of clipping the radical to zero.
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
    wake_model_validity = {
        'model': wake_model,
        'near_wake_policy': (
            'reject_material_interaction_outside_domain'
            if wake_model == 'gaussian' else 'not_applicable'
        ),
        'minimum_valid_x_over_d': (
            round(gaussian_min_valid_distance_d(k=k_wake, ct=ct), 6)
            if wake_model == 'gaussian' else 0.0
        ),
        'ct_source': 'constant_user_input',
        'ct_curve_available': False,
    }
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
    if sample_duration_hours is not None:
        dt_h = float(sample_duration_hours)
        if not math.isfinite(dt_h) or dt_h <= 0:
            raise ValueError('sample_duration_hours debe ser positivo.')
    total_hours = n_samples * dt_h
    annualization_factor = 8760.0 / total_hours if total_hours > 0 else 1.0

    sector_centres, ws_centres, freq = (
        wind_bins if wind_bins is not None else bin_wind_to_sectors(ws, wd, n_sectors))

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
        # Ct is constant in this model, so the wake multiplier depends on
        # geometry and direction, not on the speed bin. Reuse it for all bins.
        wake_factor = effective_wind_per_turbine(
            layout, np.ones(n), wind_dir, rotor_d, k=k_wake, ct=ct,
            wake_model=wake_model)
        # Interpolate the power curve once for every speed bin in this
        # direction; retain the original per-bin accumulation order below.
        if local_wind_multipliers is not None:
            v_eff_bins = ((ws_centres[:, None] * local_wind_multipliers[None, :])
                          * wake_factor[None, :])
        else:
            v_eff_bins = ws_centres[:, None] * wake_factor[None, :]
        p_kw_bins = turbine.power_kw_density_corrected(v_eff_bins, rho_mean)
        for b, v_free in enumerate(ws_centres):
            p_join = freq[s, b]
            if p_join <= 0 or v_free <= 0:
                continue
            p_kw = p_kw_bins[b]
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
    if wind_summary is None:
        dom_dir = dominant_direction(wd, n_sectors)
        energy_dir, rose_dirs, rose_energy = energy_weighted_dominant_direction(
            ws, wd, turbine, n_sectors=16, rho=rho_mean)
    else:
        dom_dir, energy_dir, rose_dirs, rose_energy = wind_summary

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
        'wake_model_validity': wake_model_validity,
        'cf_pct':         round(100.0 * cf, 2),
        'flh':            round(e_total_annual_kwh / installed_kw, 1) if installed_kw > 0 else 0.0,
        'dominant_dir_deg': round(dom_dir, 1),
        'dominant_energy_dir_deg': energy_dir,
        'direction_convention': 'meteorological_from_north_clockwise_centered_sectors',
        'energy_rose': {'directions_deg': rose_dirs.tolist(), 'energy_pct': rose_energy.tolist()},
        'hours':          round(total_hours, 1),
        'dt_h':           round(dt_h, 4),
        'years_in_record': round(total_hours / 8760.0, 4) if total_hours > 0 else 0,
        'annualization_factor': round(annualization_factor, 6),
        'rho_mean':       round(rho_mean, 4),
        'turbines':       per_turbine,
    }


def chronological_revenue(layout_xy: np.ndarray, turbine: 'PowerCurve',
                          ws: np.ndarray, wd: np.ndarray,
                          hour_of_year: Optional[np.ndarray] = None,
                          price_hourly_usd_kwh: Optional[np.ndarray] = None, *,
                          timestamps_utc: Optional[np.ndarray] = None,
                          pml_series_values: Optional[np.ndarray] = None,
                          pml_series_timestamps_utc=None,
                          ratio_by_sector: Optional[np.ndarray] = None,
                          min_coverage: float = 0.90,
                          sample_duration_hours: float = 1.0,
                          k_wake: float = 0.075, ct: float = 0.8, n_sectors: int = 36,
                          availability: float = 1.0, electrical_losses: float = 0.0,
                          temperature_c=None, pressure_pa=None, humidity_pct: float = 0.0,
                          local_wind_multipliers: Optional[np.ndarray] = None,
                          wake_model: str = 'jensen',
                          rho_override: Optional[float] = None) -> dict:
    """
    Cruza la generación horaria con estela contra el precio real de ESA MISMA
    hora, en vez de AEP × precio promedio.

    DOS CAMINOS (automático según parámetros):

    CAMINO DATED (nuevos: timestamps_utc + pml_series_values + pml_series_timestamps_utc)
      · timestamps_utc: UTC naive (datetime64[ns] sin tz) = inicio de intervalo.
        Si viene tz-aware se convierte a UTC antes del emparejamiento.
      · Empareja contra pml_series_timestamps_utc con floor(ts, "1h").
      · Horas sin precio se excluyen ANTES del cálculo de estela y potencia.
      · Requiere sample_duration_hours=1.0 (cobertura y anualización asumen 1h/muestra).
      · ValueError si ts_floored del viento tiene duplicados.
      · Devuelve by_year con métricas por año calendario.
      · Claves de totales: revenue_usd_mean_complete_years y aep_mwh_overlap_mean.
        NO son insumos de la economía. El §7 paso 4 usa
        by_year[y]['avg_realized_price_usd_kwh'] (2017-2023) y aep_p50.
      · min_coverage (default 0.90): cobertura = horas_viento+precio / horas_esperadas.
      · ratio_by_sector (n_sectors × n_turbinas) opcional: omite el bucle de estela.

    CAMINO HOUR_OF_YEAR (legado, retorno idéntico al previo a este cambio)
      · hour_of_year: índice 0-8759, price_hourly_usd_kwh[0] = 1 ene hora 0.
        29 de febrero reasignado al 28. price_hourly_usd_kwh requiere len==8760.
    """
    import calendar as _cal

    # ── Validar combinación de parámetros ─────────────────────────────────
    _n_dated = sum(p is not None
                   for p in (timestamps_utc, pml_series_values, pml_series_timestamps_utc))
    if 0 < _n_dated < 3:
        raise ValueError(
            'timestamps_utc, pml_series_values y pml_series_timestamps_utc '
            'deben proporcionarse todos o ninguno.')
    use_dated = (_n_dated == 3)

    if use_dated and price_hourly_usd_kwh is not None:
        raise ValueError(
            'En el camino dated (timestamps_utc activos), '
            'price_hourly_usd_kwh debe ser None.')
    if use_dated and hour_of_year is not None:
        raise ValueError(
            'En el camino dated (timestamps_utc activos), '
            'hour_of_year debe ser None.')

    # ── Parseo de arrays ──────────────────────────────────────────────────
    layout = np.asarray(layout_xy, dtype=float)
    n = len(layout)
    ws = np.asarray(ws, dtype=float).reshape(-1)
    wd = np.asarray(wd, dtype=float).reshape(-1) % 360.0
    T_orig = len(ws)

    dt_h = float(sample_duration_hours) if sample_duration_hours else 1.0

    if use_dated:
        if dt_h != 1.0:
            raise ValueError(
                f'El camino dated requiere sample_duration_hours=1.0 '
                f'(recibido {dt_h}). '
                'Cobertura y anualización asumen exactamente 1 muestra = 1 hora.')
        if len(wd) != T_orig:
            raise ValueError('ws y wd deben compartir la misma longitud.')
        _ts_in = np.asarray(timestamps_utc).reshape(-1)
        if len(_ts_in) != T_orig:
            raise ValueError('timestamps_utc debe tener la misma longitud que ws.')
    else:
        if hour_of_year is None:
            raise ValueError('hour_of_year es obligatorio en el camino hour_of_year.')
        if price_hourly_usd_kwh is None:
            raise ValueError('price_hourly_usd_kwh es obligatorio en el camino hour_of_year.')
        price_hourly_usd_kwh = np.asarray(price_hourly_usd_kwh, dtype=float).reshape(-1)
        if len(price_hourly_usd_kwh) != 8760:
            raise ValueError('price_hourly_usd_kwh debe tener exactamente 8760 valores.')
        _hoy = np.asarray(hour_of_year, dtype=int).reshape(-1) % 8760
        if len(wd) != T_orig or len(_hoy) != T_orig:
            raise ValueError('ws, wd y hour_of_year deben compartir la misma longitud.')

    if n == 0 or T_orig == 0:
        if use_dated:
            raise ValueError('Layout o serie de viento vacíos.')
        return {'revenue_usd_year1': 0.0, 'aep_mwh': 0.0,
                'avg_realized_price_usd_kwh': None, 'capture_ratio': None, 'n_hours': 0}

    # ── Máscara de muestras válidas ────────────────────────────────────────
    valid = np.isfinite(ws) & np.isfinite(wd) & (ws >= 0)
    if not valid.any():
        if use_dated:
            raise ValueError('Ninguna muestra de viento es válida (ws/wd finito, ws≥0).')
        return {'revenue_usd_year1': 0.0, 'aep_mwh': 0.0,
                'avg_realized_price_usd_kwh': None, 'capture_ratio': None, 'n_hours': 0}
    ws_v = ws[valid]
    wd_v = wd[valid]

    # ── Camino dated: índice PML y filtro a horas con precio ──────────────
    if use_dated:
        ts_v = _ts_in[valid]

        # Normalizar PML a UTC naive; verificar duplicados con valores distintos
        _pml_idx_raw = pd.DatetimeIndex(pml_series_timestamps_utc)
        if _pml_idx_raw.tz is not None:
            _pml_idx_raw = _pml_idx_raw.tz_convert('UTC').tz_localize(None)
        _pml_ser_raw = pd.Series(
            np.asarray(pml_series_values, dtype=float), index=_pml_idx_raw)

        _pml_dup_any = _pml_ser_raw.index.duplicated(keep=False)
        _n_pml_dup = int(_pml_ser_raw.index.duplicated(keep='first').sum())
        if _n_pml_dup > 0:
            for _ts_d, _grp in _pml_ser_raw[_pml_dup_any].groupby(level=0):
                if not np.allclose(_grp.values, _grp.values[0]):
                    raise ValueError(
                        f'PML tiene duplicados con valores distintos en {_ts_d}: '
                        f'{_grp.values}. Deduplica manualmente antes de llamar.')
        _pml_ser = _pml_ser_raw[~_pml_ser_raw.index.duplicated(keep='first')]

        # Normalizar timestamps de viento a UTC naive; floor al inicio de hora
        _ts_pd = pd.DatetimeIndex(ts_v)
        if _ts_pd.tz is not None:
            _ts_pd = _ts_pd.tz_convert('UTC').tz_localize(None)
        _ts_floored = _ts_pd.floor('h')

        # Rechazar duplicados en serie de viento post-floor
        _n_wind_dup = int(_ts_floored.duplicated().sum())
        if _n_wind_dup > 0:
            raise ValueError(
                f'{_n_wind_dup} timestamp(s) de viento caen en la misma hora UTC '
                'después de floor("h"). Deduplica la serie de viento.')

        _prices_all = _pml_ser.reindex(_ts_floored).values
        has_price = np.isfinite(_prices_all)
        if not has_price.any():
            raise ValueError(
                'Ninguna muestra de viento coincide con una hora PML. '
                'Verifica que ambas series estén en UTC y en el mismo rango temporal.')

        ws_f         = ws_v[has_price]
        wd_f         = wd_v[has_price]
        ts_floored_f = _ts_floored[has_price]
        prices_f     = _prices_all[has_price]
        T            = len(ws_f)
        _valid_hp    = valid.copy()
        _valid_hp[valid] = has_price
    else:
        ws_f  = ws_v
        wd_f  = wd_v
        _hoy  = _hoy[valid]
        T     = len(ws_f)
        _valid_hp  = None
        _n_pml_dup = 0

    # ── Densidad del aire ─────────────────────────────────────────────────
    if rho_override is not None and math.isfinite(float(rho_override)) and float(rho_override) > 0:
        rho_mean = float(rho_override)
    elif temperature_c is not None and pressure_pa is not None:
        _tc = np.asarray(temperature_c, dtype=float).reshape(-1)
        _pp = np.asarray(pressure_pa, dtype=float).reshape(-1)
        _mask = _valid_hp if use_dated else valid
        rho_mean = float(np.mean(air_density(_tc[_mask], _pp[_mask], humidity_pct)))
    else:
        rho_mean = 1.225

    # ── Multiplicadores espaciales ────────────────────────────────────────
    if local_wind_multipliers is not None:
        _lm = np.asarray(local_wind_multipliers, dtype=float).reshape(-1)
        if _lm.shape[0] != n:
            _lm = np.ones(n)
        ws_free = ws_f[:, None] * _lm[None, :]
    else:
        ws_free = np.repeat(ws_f[:, None], n, axis=1)

    # ── Razón de estela por sector ────────────────────────────────────────
    sector_deg = 360.0 / n_sectors
    sector_idx = np.floor(wd_f / sector_deg).astype(int) % n_sectors
    rotor_d    = float(turbine.rotor_diameter_m or 80.0)
    _rbs_precomputed = ratio_by_sector is not None
    if _rbs_precomputed:
        _rbs = np.asarray(ratio_by_sector, dtype=float)
        if _rbs.shape != (n_sectors, n):
            raise ValueError(
                f'ratio_by_sector debe tener forma ({n_sectors}, {n}); '
                f'recibido {_rbs.shape}.')
    else:
        _rbs = np.ones((n_sectors, n), dtype=float)
        if n > 1:
            for s in range(n_sectors):
                center = (s + 0.5) * sector_deg
                _rbs[s] = effective_wind_per_turbine(
                    layout, np.ones(n), center, rotor_d,
                    k=k_wake, ct=ct, wake_model=wake_model)

    # ── Potencia y energía ────────────────────────────────────────────────
    v_eff    = ws_free * _rbs[sector_idx]
    power_kw = turbine.power_kw_density_corrected(v_eff, rho_mean)
    survival = float(availability) * (1.0 - float(electrical_losses))
    power_kw = power_kw * survival
    energy_kwh = power_kw.sum(axis=1) * dt_h

    # ── CAMINO LEGADO (hour_of_year) — retorno idéntico al original ───────
    if not use_dated:
        price_at_hour    = price_hourly_usd_kwh[_hoy]
        total_revenue_usd = float(np.sum(energy_kwh * price_at_hour))
        total_energy_kwh  = float(np.sum(energy_kwh))
        total_hours       = T * dt_h
        annualization_factor = 8760.0 / total_hours if total_hours > 0 else 1.0
        aep_mwh      = total_energy_kwh * annualization_factor / 1000.0
        revenue_year1 = total_revenue_usd * annualization_factor
        avg_price    = total_revenue_usd / total_energy_kwh if total_energy_kwh > 0 else None
        naive_revenue = total_energy_kwh * float(np.mean(price_hourly_usd_kwh))
        capture_ratio = (total_revenue_usd / naive_revenue) if naive_revenue > 0 else None
        return {
            'revenue_usd_year1': round(revenue_year1, 2),
            'aep_mwh': round(aep_mwh, 2),
            'avg_realized_price_usd_kwh': round(avg_price, 6) if avg_price is not None else None,
            'avg_flat_price_usd_kwh': round(float(np.mean(price_hourly_usd_kwh)), 6),
            'capture_ratio': round(capture_ratio, 4) if capture_ratio is not None else None,
            'n_hours': T,
            'rho_mean': round(rho_mean, 4),
            'method': 'chronological_hourly_generation_times_hourly_price',
            'calendar_assumption': 'price_hourly_usd_kwh[0] = 1 de enero hora 0, mismo huso que la serie de viento',
        }

    # ── CAMINO DATED: métricas por año calendario ─────────────────────────
    years_arr    = ts_floored_f.year.to_numpy()
    unique_years = sorted(int(y) for y in np.unique(years_arr))

    by_year: Dict[int, dict] = {}
    rev_ann_complete:        List[float] = []
    ann_energies_complete:   List[float] = []
    capture_ratios_complete: List[float] = []
    realized_vals_complete:  List[float] = []
    flat_vals_complete:      List[float] = []

    for y in unique_years:
        mask_y     = years_arr == y
        energy_y   = energy_kwh[mask_y]
        price_y    = prices_f[mask_y]
        n_h        = int(mask_y.sum())
        expected_h = 8784 if _cal.isleap(y) else 8760
        coverage   = n_h / expected_h

        rev_y          = float(np.sum(energy_y * price_y))
        energy_y_kwh   = float(np.sum(energy_y))
        avg_realized_y = rev_y / energy_y_kwh if energy_y_kwh > 0 else None
        avg_flat_y     = float(np.mean(price_y)) if n_h > 0 else None
        capture_y      = (avg_realized_y / avg_flat_y
                          if avg_realized_y is not None
                          and avg_flat_y is not None and avg_flat_y > 0
                          else None)
        ann_y          = 8760.0 / n_h if n_h > 0 else 1.0
        energy_mwh_ann = energy_y_kwh * ann_y / 1000.0
        rev_y_ann      = rev_y * ann_y

        complete = coverage >= float(min_coverage)
        by_year[y] = {
            'revenue_usd':            round(rev_y, 2),
            'revenue_usd_annualized': round(rev_y_ann, 2),
            'energy_mwh':             round(energy_y_kwh / 1000.0, 4),
            'energy_mwh_annualized':  round(energy_mwh_ann, 4),
            'n_hours':                n_h,
            'expected_hours':         expected_h,
            'coverage':               round(coverage, 4),
            'complete':               complete,
            'avg_realized_price_usd_kwh': (round(avg_realized_y, 6)
                                           if avg_realized_y is not None else None),
            'avg_flat_price_usd_kwh':     (round(avg_flat_y, 6)
                                           if avg_flat_y is not None else None),
            'capture_ratio': round(capture_y, 4) if capture_y is not None else None,
        }
        if complete:
            rev_ann_complete.append(rev_y_ann)
            ann_energies_complete.append(energy_mwh_ann)
            if capture_y is not None:
                capture_ratios_complete.append(capture_y)
            if avg_realized_y is not None:
                realized_vals_complete.append(avg_realized_y)
            if avg_flat_y is not None:
                flat_vals_complete.append(avg_flat_y)

    if not rev_ann_complete:
        raise ValueError(
            f'Ningún año alcanza la cobertura mínima de {min_coverage:.0%}. '
            f'Años disponibles: {sorted(by_year)}. '
            'Usa min_coverage=0 para incluir años parciales.')

    n_complete        = len(rev_ann_complete)
    mean_rev_ann      = float(np.mean(rev_ann_complete))
    mean_aep_mwh      = float(np.mean(ann_energies_complete))
    mean_capture      = float(np.mean(capture_ratios_complete)) if capture_ratios_complete else None
    mean_avg_realized = float(np.mean(realized_vals_complete)) if realized_vals_complete else None
    mean_avg_flat     = float(np.mean(flat_vals_complete))     if flat_vals_complete     else None

    return {
        # Medias de años completos, anual. a 8760 h/año. NO son insumos de la economía.
        # §7 paso 4 usa by_year[y]['avg_realized_price_usd_kwh'] (2017-2023) y aep_p50.
        'revenue_usd_mean_complete_years': round(mean_rev_ann, 2),
        'aep_mwh_overlap_mean':            round(mean_aep_mwh, 2),
        'avg_realized_price_usd_kwh': (round(mean_avg_realized, 6)
                                       if mean_avg_realized is not None else None),
        'avg_flat_price_usd_kwh':     (round(mean_avg_flat, 6)
                                       if mean_avg_flat is not None else None),
        'capture_ratio': round(mean_capture, 4) if mean_capture is not None else None,
        'n_hours':       T,
        'rho_mean':      round(rho_mean, 4),
        'pml_duplicates_dropped': _n_pml_dup,
        'method':               'chronological_dated_generation_times_dated_price',
        'calendar_assumption':  (
            'timestamps_utc = inicio de intervalo, UTC naive (datetime64[ns]); '
            'emparejado por floor(ts, "1h"); '
            'avg_flat = media del PML sobre horas de viento del mismo año'
        ),
        'by_year':        by_year,
        'years_complete': sorted(y for y, d in by_year.items() if d['complete']),
        'n_years_complete': n_complete,
        'ratio_by_sector': _rbs.tolist(),
        'ratio_by_sector_params': {
            'n_sectors': n_sectors,
            'k_wake':    k_wake    if not _rbs_precomputed else None,
            'ct':        ct        if not _rbs_precomputed else None,
            'wake_model': wake_model if not _rbs_precomputed else None,
            'precomputed': _rbs_precomputed,
        },
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


def build_slope_mask_fn(lats_p: List[float], lons_p: List[float],
                        max_slope_deg: Optional[float],
                        n_grid: int = 15) -> Optional[Callable[[float, float], bool]]:
    """Construye un filtro de factibilidad por pendiente máxima, en
    términos de lat/lon (no xy local, porque la pendiente se muestrea del
    DEM en esas coordenadas -- ver core.terrain.sample_slope_grid_deg).

    Devuelve None si `max_slope_deg` no está configurado (comportamiento
    idéntico al anterior: sin filtro de pendiente). Muestra una rejilla
    `n_grid`×`n_grid` sobre el cuadro delimitador del polígono UNA sola vez
    (el DEM ya cachea por tile en core.terrain) y resuelve cada consulta de
    punto candidato por vecino más cercano -- suficiente para una máscara de
    factibilidad de prefactibilidad, no para un mapa de pendiente de precisión.
    """
    if max_slope_deg is None:
        return None
    grid_lats = list(np.linspace(min(lats_p), max(lats_p), n_grid))
    grid_lons = list(np.linspace(min(lons_p), max(lons_p), n_grid))
    slope_grid = sample_slope_grid_deg(grid_lats, grid_lons)
    limit = float(max_slope_deg)

    def slope_mask_fn(lat: float, lon: float) -> bool:
        j = min(range(len(grid_lats)), key=lambda idx: abs(grid_lats[idx] - lat))
        i = min(range(len(grid_lons)), key=lambda idx: abs(grid_lons[idx] - lon))
        return slope_grid[j][i] <= limit

    return slope_mask_fn


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


def optimize_layout_greedy_vertex(
        polygon_latlon,
        turbine: PowerCurve,
        ws: Optional[np.ndarray] = None,
        wd: Optional[np.ndarray] = None,
        wind_grid: Optional[Dict[str, Any]] = None,
        fallback_wind_ms: float = 9.0,
        min_spacing_d: float = 3.0,
        grid_step_d: float = 1.0,
        k_wake: float = 0.075,
        ct: float = 0.8,
        n_rose_sectors: int = 16,
        rho: float = 1.225,
        exclusion_polygons=None,
        boundary_setback_m: float = 0.0,
        wake_model: str = 'jensen',
        max_slope_deg: Optional[float] = None,
        axis_tolerance_deg: float = 20.0,
        n_max_cap: Optional[int] = None) -> dict:
    """
    Greedy vertex-seeded, wind-axis turbine placement.

    A third, structurally different family from the regular
    aligned/staggered grids of optimize_layout_structured: instead of
    testing a single rigid (orientation, spacing, stagger) pattern across
    the whole polygon, it grows the layout one turbine at a time from an
    upwind polygon vertex, always searching the next position along the
    wind-aligned axes (parallel/antiparallel to the dominant wind, and
    cross-wind) radiating from each already-placed turbine. This adapts
    locally to an irregular boundary the way a single regular grid cannot,
    at the cost of an irregular (non-row/column) result — a packing-
    maximising complement to the energy-maximising regular families, not a
    replacement for them.

    Algorithm
    ---------
    1. Dominant direction: same energy-weighted wind rose as
       optimize_layout_structured (never the previous hardcoded westerly
       placeholder of optimize_layout_greedy).
    2. Seed vertex: the polygon vertex with the smallest projection onto
       the downwind axis (i.e. the most upwind corner) — placement starts
       there so growth proceeds across the full downwind depth of the
       site, matching standard practice of filling a site from its upwind
       edge.
    3. Candidate universe: a fine regular grid (grid_step_d × D) inside the
       polygon/exclusions/slope mask, same feasibility primitives as the
       rest of this module (_point_allowed_xy, build_slope_mask_fn).
    4. Growth loop: at each step, for every placed turbine and each of the
       4 wind-aligned ray directions (±downwind, ±crosswind), find the
       nearest remaining candidate within axis_tolerance_deg of that ray —
       this is the "search every axis of every turbine" shortlist. Score
       each shortlisted candidate by wake-discounted power at the real
       dominant direction and place the best one; candidates within
       min_spacing_d × D of it are then excluded, exactly as in
       optimize_layout_greedy. If the axis shortlist is empty (can happen
       near a re-entrant boundary corner) the search falls back to scoring
       every remaining candidate, so growth never dead-ends while free
       space remains — same guarantee as the original global GSP.

    Because growth only ever adds turbines, the placement order is a
    single nested sequence valid for every N from 1 up to the returned
    max_n_fit: sequence_xy_m[:n] is a complete, feasible n-turbine layout.
    Callers needing several N should slice this one sequence instead of
    re-running the search per N (unlike optimize_layout_structured, whose
    regular-grid Nmax and per-N energy pass are genuinely different
    searches).

    References
    ----------
    - Feng J, Shen WZ (2015). Solving the wind farm layout optimization
      problem using random search algorithm. Renewable Energy 78:182-192.
      (base greedy sequential placement, reused from optimize_layout_greedy)
    - Standard wind farm design practice of siting the first row(s) at the
      upwind edge of a site to preserve downwind depth for later rows.
    """
    D         = float(turbine.rotor_diameter_m or 80.0)
    min_sep_m = min_spacing_d * D
    step_m    = max(0.1, float(grid_step_d)) * D

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
    slope_mask_fn = build_slope_mask_fn(lats_p, lons_p, max_slope_deg)

    exclusion_polys_xy = []
    for exclusion in exclusion_polygons or []:
        ex_latlon = [
            (float(p['lat']), float(p['lon'])) if isinstance(p, dict)
            else (float(p[0]), float(p[1])) for p in exclusion
        ]
        if len(ex_latlon) >= 3:
            exclusion_polys_xy.append(_latlon_to_xy(
                [p[0] for p in ex_latlon], [p[1] for p in ex_latlon], lat0, lon0))

    rho_val = float(rho) if rho and math.isfinite(float(rho)) and rho > 0 else 1.225
    if ws is not None and wd is not None and len(ws) > 0:
        dom_dir, _, _ = energy_weighted_dominant_direction(
            ws, wd, turbine, n_sectors=n_rose_sectors, rho=rho_val)
        ws_valid = ws[np.isfinite(ws) & (ws > 0)]
        v_mean = float(np.mean(ws_valid)) if len(ws_valid) > 0 else fallback_wind_ms
    else:
        dom_dir = 270.0
        v_mean = fallback_wind_ms

    toward_deg = (dom_dir + 180.0) % 360.0
    tr = math.radians(toward_deg)
    u_down  = np.array([ math.sin(tr),  math.cos(tr)])   # downwind (E,N)
    u_cross = np.array([ math.cos(tr), -math.sin(tr)])   # cross-wind
    axis_dirs = [u_down, -u_down, u_cross, -u_cross]

    xmin = min(poly_xs); xmax = max(poly_xs)
    ymin = min(poly_ys); ymax = max(poly_ys)

    candidates: List[Dict[str, float]] = []
    x = xmin + step_m * 0.5
    while x <= xmax + step_m * 0.1:
        y = ymin + step_m * 0.5
        while y <= ymax + step_m * 0.1:
            if _point_allowed_xy(x, y, poly_xs, poly_ys, exclusion_polys_xy,
                                 max(0.0, float(boundary_setback_m))):
                lats_c, lons_c = _xy_to_latlon([x], [y], lat0, lon0)
                if slope_mask_fn is None or slope_mask_fn(lats_c[0], lons_c[0]):
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

    # Seed at the most upwind polygon vertex: smallest projection onto the
    # downwind axis. Growth starts there and proceeds across the site's
    # downwind depth, instead of an arbitrary highest-wind starting point.
    vertex_proj = [float(np.dot([vx, vy], u_down)) for vx, vy in zip(poly_xs, poly_ys)]
    seed_vertex_idx = int(np.argmin(vertex_proj))
    seed_vx, seed_vy = poly_xs[seed_vertex_idx], poly_ys[seed_vertex_idx]
    seed = min(candidates, key=lambda c: math.hypot(c['x'] - seed_vx, c['y'] - seed_vy))
    seed_vertex_lat, seed_vertex_lon = _xy_to_latlon([seed_vx], [seed_vy], lat0, lon0)

    def wake_discounted_score(c: Dict[str, float], placed_arr: Optional[np.ndarray]) -> float:
        sq_sum = 0.0
        if placed_arr is not None and len(placed_arr):
            cand_pt  = np.array([[c['x'], c['y']]])
            pts_rot  = _rotate_to_wind_axis(placed_arr, dom_dir)
            cand_rot = _rotate_to_wind_axis(cand_pt,   dom_dir)[0]
            deficit_fn = gaussian_deficit if wake_model == 'gaussian' else jensen_deficit
            for pr in pts_rot:
                dx = float(cand_rot[0]) - float(pr[0])
                dy = float(cand_rot[1]) - float(pr[1])
                sq_sum += deficit_fn(dx, dy, D, k=k_wake, ct=ct) ** 2
        v_eff = max(0.0, c['v'] * (1.0 - math.sqrt(sq_sum)))
        return float(turbine.power_kw(v_eff))

    placed: List[Dict[str, float]] = [seed]
    remaining = [
        c for c in candidates
        if c is not seed and math.hypot(c['x'] - seed['x'], c['y'] - seed['y']) >= min_sep_m
    ]
    tol_rad = math.radians(max(1.0, float(axis_tolerance_deg)))

    cap = n_max_cap if n_max_cap and n_max_cap > 0 else len(candidates)
    while remaining and len(placed) < cap:
        placed_arr = np.array([[p['x'], p['y']] for p in placed])

        # Axis shortlist: nearest remaining candidate to each placed
        # turbine along each of the 4 wind-aligned rays, within tolerance.
        shortlist_idx = set()
        for p in placed:
            for axis in axis_dirs:
                best_j, best_d = None, math.inf
                for j, c in enumerate(remaining):
                    dx, dy = c['x'] - p['x'], c['y'] - p['y']
                    dist = math.hypot(dx, dy)
                    if dist < 1e-6:
                        continue
                    cosang = max(-1.0, min(1.0, (dx * axis[0] + dy * axis[1]) / dist))
                    if math.acos(cosang) <= tol_rad and dist < best_d:
                        best_d, best_j = dist, j
                if best_j is not None:
                    shortlist_idx.add(best_j)
        # Near a re-entrant boundary corner the axis rays from every placed
        # turbine can miss all remaining free space even though it exists —
        # fall back to the full remaining set so growth never dead-ends.
        step_candidates = [remaining[j] for j in shortlist_idx] if shortlist_idx else remaining

        best_c, best_score = None, -math.inf
        for c in step_candidates:
            score = wake_discounted_score(c, placed_arr)
            if score > best_score:
                best_score, best_c = score, c
        placed.append(best_c)
        remaining = [
            c for c in remaining
            if c is not best_c and math.hypot(c['x'] - best_c['x'], c['y'] - best_c['y']) >= min_sep_m
        ]

    sequence_latlon = [[p['lat'], p['lon']] for p in placed]
    sequence_xy_m   = [[p['x'],  p['y']]   for p in placed]

    n_p = len(poly_xs)
    area_m2 = abs(sum(
        poly_xs[i] * poly_ys[(i + 1) % n_p] -
        poly_xs[(i + 1) % n_p] * poly_ys[i]
        for i in range(n_p)
    )) / 2.0

    return {
        'sequence_latlon':   sequence_latlon,
        'sequence_xy_m':     sequence_xy_m,
        'wind_at_sequence':  [round(p['v'], 3) for p in placed],
        'max_n_fit':         len(placed),
        'n_candidates':      len(candidates),
        'dominant_dir_deg':  round(dom_dir, 1),
        'seed_vertex_latlon': [round(seed_vertex_lat[0], 6), round(seed_vertex_lon[0], 6)],
        'origin_lat':        lat0,
        'origin_lon':        lon0,
        'D_m':               D,
        'min_sep_m':         round(min_sep_m, 1),
        'area_km2':          round(area_m2 / 1e6, 4),
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
        along_offset_fraction: float = 0.0,
        cross_offset_fraction: float = 0.0,
        slope_mask_fn: Optional[Callable[[float, float], bool]] = None,
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

    # The grid phase matters near an irregular boundary.  A single fixed
    # origin can discard an otherwise feasible complete row/column, so the
    # optimiser explores fractional translations of both grid axes.
    along_min = (along_proj.min() - s_para_m
                 + (float(along_offset_fraction) % 1.0) * s_para_m)
    along_max = along_proj.max() + s_para_m
    cross_min = (cross_proj.min() - s_perp_m
                 + (float(cross_offset_fraction) % 1.0) * s_perp_m)
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
                if slope_mask_fn is None or slope_mask_fn(lats_c[0], lons_c[0]):
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
    active_weights = []
    effective_speeds = []
    for s_dir, s_w in zip(sector_dirs.tolist(), sector_weights.tolist()):
        if s_w <= 0.0:
            continue
        v_eff = effective_wind_per_turbine(arr, v_mean, float(s_dir), D,
                                           k=k_wake, ct=ct,
                                           wake_model=wake_model)
        effective_speeds.append(v_eff)
        active_weights.append(s_w)
    # One curve interpolation for the 16 directions instead of one per
    # direction, while summing their contributions in the same order.
    powers_kw = turbine.power_kw_density_corrected(np.asarray(effective_speeds), rho)
    for p_kw, s_w in zip(powers_kw, active_weights):
        score += float(np.sum(p_kw)) * s_w
    return score


def _select_spread_candidates(candidates: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """Choose an exact-count, spatially spread subset; wind speed breaks ties."""
    if n <= 0 or len(candidates) <= n:
        return list(candidates)
    remaining = list(candidates)
    selected = [max(remaining, key=lambda c: c['v'])]
    remaining.remove(selected[0])
    while remaining and len(selected) < n:
        def merit(candidate):
            min_dist_sq = min(
                (candidate['x'] - placed['x']) ** 2
                + (candidate['y'] - placed['y']) ** 2
                for placed in selected
            )
            return min_dist_sq, candidate['v']
        chosen = max(remaining, key=merit)
        selected.append(chosen)
        remaining.remove(chosen)
    return selected


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
        orientation_step_deg: float = 15.0,
        grid_offset_fractions: Tuple = (0.0, 0.25, 0.5, 0.75),
        candidate_limit: int = 0,
        layout_evaluator=None,
        max_wake_loss_pct: Optional[float] = None,
        max_slope_deg: Optional[float] = None,
        grid_cache: Optional[dict] = None,
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

      orientation — geometric rotations over 0–180° plus the dominant wind
      grid phase  — fractional translations along both grid axes

    Nmax is found first with the minimum permitted row/column spacing across
    all rotations, translations and stagger options.  A second pass optimises
    energy without ever reducing the requested maximum-capacity turbine count.

    If the exact requested count cannot be built, the function reports the
    infeasibility instead of silently returning a smaller layout.

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
    slope_mask_fn = build_slope_mask_fn(lats_p, lons_p, max_slope_deg)
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

    # ── Stage 1: geometric packing capacity (Nmax) ─────────────────────────
    para_values = tuple(float(v) for v in s_para_range)
    perp_values = tuple(float(v) for v in s_perp_range)
    stagger_values = tuple(float(v) for v in stagger_options)
    offset_values = tuple(dict.fromkeys(
        round(float(v) % 1.0, 6) for v in grid_offset_fractions))
    if not para_values or not perp_values or not stagger_values or not offset_values:
        raise ValueError('El espacio de búsqueda del layout está vacío.')

    step = float(orientation_step_deg)
    if not math.isfinite(step) or step <= 0.0 or step > 90.0:
        step = 15.0
    raw_orientations = list(np.arange(0.0, 180.0, step)) + [dom_dir % 180.0]
    orientations: List[float] = []
    for angle in raw_orientations:
        normalized = round(float(angle) % 180.0, 6)
        if all(abs(normalized - existing) > 1e-5 for existing in orientations):
            orientations.append(normalized)

    valid_para_values = [v for v in para_values if v >= min_spacing_d]
    valid_perp_values = [v for v in perp_values if v >= min_spacing_d]
    if not valid_para_values or not valid_perp_values:
        raise ValueError(
            'Las separaciones configuradas son menores que la separación mínima.')
    min_para_d = min(valid_para_values)
    min_perp_d = min(valid_perp_values)
    setback_m = max(0.0, float(boundary_setback_m))
    capacity_seeds: List[Dict[str, Any]] = []

    def build_grid(orientation, para, perp, stagger, along, cross):
        args = (poly_xs, poly_ys, lat0, lon0, D, orientation, para, perp,
                stagger, wind_grid, v_mean, exclusion_polys_xy, setback_m,
                along, cross, slope_mask_fn)
        if grid_cache is None:
            return _build_structured_grid(*args)
        key = (tuple(poly_xs), tuple(poly_ys), lat0, lon0, D,
               orientation, para, perp, stagger, id(wind_grid), v_mean,
               tuple((tuple(xs), tuple(ys)) for xs, ys in exclusion_polys_xy),
               setback_m, along, cross, slope_mask_fn)
        if key not in grid_cache:
            grid_cache[key] = _build_structured_grid(*args)
        return grid_cache[key]

    for orientation_deg in orientations:
        for along_offset in offset_values:
            for cross_offset in offset_values:
                for stagger in stagger_values:
                    min_diag_d = (math.hypot(stagger * min_perp_d, min_para_d)
                                  if stagger > 0 else min_para_d)
                    if min(min_perp_d, min_diag_d) < min_spacing_d:
                        continue
                    cands = build_grid(orientation_deg, min_para_d, min_perp_d,
                                       stagger, along_offset, cross_offset)
                    if cands:
                        capacity_seeds.append({
                            'orientation_deg': orientation_deg,
                            'along_offset_fraction': along_offset,
                            'cross_offset_fraction': cross_offset,
                            'stagger': stagger,
                            'n_fit': len(cands),
                            '_candidates': cands,
                        })

    if not capacity_seeds:
        raise ValueError(
            'No se encontró ninguna posición factible con las separaciones y retiros indicados.')

    capacity_seed = max(capacity_seeds, key=lambda c: c['n_fit'])
    max_n_fit = int(capacity_seed['n_fit'])
    target_n = int(n_turbines) if n_turbines > 0 else max_n_fit
    if target_n > max_n_fit:
        raise ValueError(
            f'El polígono admite como máximo {max_n_fit} aerogeneradores; '
            f'se solicitaron {target_n}.')

    # ── Stage 2: best energy layout while preserving the exact target ─────
    def half_turn_delta(angle: float, reference: float) -> float:
        return abs(((angle - (reference % 180.0) + 90.0) % 180.0) - 90.0)

    viable_seeds = [c for c in capacity_seeds if c['n_fit'] >= target_n]
    viable_seeds.sort(key=lambda c: (
        -c['n_fit'], half_turn_delta(c['orientation_deg'], dom_dir)))
    if candidate_limit > 0:
        # Retain phases from every viable rotation for the refinement shortlist.
        groups = {}
        for seed in viable_seeds:
            groups.setdefault(seed['orientation_deg'], []).append(seed)
        viable_seeds = [group[i] for i in range(max(map(len, groups.values()), default=0))
                        for group in groups.values() if i < len(group)]
    # Bound runtime while retaining the densest phases and wind-aligned ones.
    if layout_evaluator is None:
        viable_seeds = viable_seeds[:48]
    all_configs: List[Dict[str, Any]] = []

    for seed in viable_seeds:
        for s_para_d in para_values:
            for s_perp_d in perp_values:
                stagger = seed['stagger']
                min_diag_d = (math.hypot(stagger * s_perp_d, s_para_d)
                              if stagger > 0 else s_para_d)
                if min(s_perp_d, min_diag_d) < min_spacing_d:
                    continue
                cands = build_grid(seed['orientation_deg'], s_para_d, s_perp_d,
                                   stagger, seed['along_offset_fraction'],
                                   seed['cross_offset_fraction'])
                if len(cands) < target_n:
                    continue
                # When evaluating fewer turbines than available grid points,
                # spread them across the usable polygon instead of taking a
                # clustered highest-wind prefix. This reduces wake exposure;
                # the subsequent energy score still decides among grids.
                sel = _select_spread_candidates(cands, target_n)
                layout_xy = [[c['x'], c['y']] for c in sel]
                score = _score_layout_fast(
                    layout_xy, D, sector_dirs, sector_weights,
                    turbine, k_wake, ct, v_mean, rho_val,
                    wake_model=wake_model,
                )
                all_configs.append({
                    's_para_d': float(s_para_d),
                    's_perp_d': float(s_perp_d),
                    'stagger': stagger,
                    'orientation_deg': seed['orientation_deg'],
                    'along_offset_fraction': seed['along_offset_fraction'],
                    'cross_offset_fraction': seed['cross_offset_fraction'],
                    'n_fit': len(cands),
                    'n_used': len(sel),
                    'score_kw': round(score, 1),
                    '_layout_xy': layout_xy,
                    '_candidates': sel,
                })

    # Do not silently return a partial greedy layout: exact target capacity is
    # a hard contract for the alternatives sent to TOPSIS.
    if not all_configs:
        raise ValueError(
            f'No se pudo construir un layout exacto de {target_n}/{target_n} '
            'con las restricciones indicadas.')

    best = max(all_configs, key=lambda c: c['score_kw'])
    # A fast-score winner failing the wake constraint does not make this
    # turbine count infeasible. Check distinct generated layouts before
    # allowing the caller to decrement the count.
    evaluation = None
    evaluated_layouts = 0
    if layout_evaluator is not None:
        seen_layouts = set()
        for config in sorted(all_configs, key=lambda c: -c['score_kw']):
            signature = tuple(sorted(tuple(point) for point in config['_layout_xy']))
            if signature in seen_layouts:
                continue
            seen_layouts.add(signature)
            result = layout_evaluator(config['_layout_xy'])
            evaluated_layouts += 1
            loss = result.get('wake_loss_pct')
            if result.get('error') or loss is None or not math.isfinite(float(loss)):
                continue
            if evaluation is None or float(loss) < float(evaluation['wake_loss_pct']):
                best, evaluation = config, result
            if max_wake_loss_pct is not None and float(loss) <= max_wake_loss_pct + 1e-9:
                best, evaluation = config, result
                break
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

    candidate_layouts = []
    if candidate_limit > 0:
        seen = set()
        for config in sorted(all_configs, key=lambda c: -c['score_kw']):
            signature = tuple(sorted(tuple(round(v, 3) for v in p)
                                     for p in config['_layout_xy']))
            if signature in seen:
                continue
            seen.add(signature)
            candidate_layouts.append({
                'layout_xy_m': config['_layout_xy'],
                'layout_latlon': [[p['lat'], p['lon']] for p in config['_candidates']],
                'wind_at_turbines': [p['v'] for p in config['_candidates']],
                'layout_config': {k: v for k, v in config.items() if not k.startswith('_')},
            })
            if len(candidate_layouts) >= candidate_limit:
                break

    return {
        'candidate_layouts': candidate_layouts,
        'n_placed':         n_placed,
        'n_requested':      int(n_turbines),
        'n_candidates':     len(placed),
        'max_n_fit':        max_n_fit,
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
            'orientation_deg':  best['orientation_deg'],
            'along_offset_fraction': best['along_offset_fraction'],
            'cross_offset_fraction': best['cross_offset_fraction'],
            'dominant_dir_deg': round(dom_dir, 1),
            'dominant_energy_dir_deg': round(dom_dir, 1),
            'direction_convention': 'meteorological_from_north_clockwise_centered_sectors',
            'energy_rose': {'directions_deg': sector_dirs.tolist(), 'energy_pct': sector_energy_pct.tolist()},
            'layout_type':      'tresbolillo' if best['stagger'] >= 0.25 else 'alineado',
            'n_configs_tried':  len(all_configs),
        },
        'capacity_config': {
            's_para_d': min_para_d,
            's_perp_d': min_perp_d,
            'stagger': capacity_seed['stagger'],
            'orientation_deg': capacity_seed['orientation_deg'],
            'along_offset_fraction': capacity_seed['along_offset_fraction'],
            'cross_offset_fraction': capacity_seed['cross_offset_fraction'],
            'n_fit': max_n_fit,
        },
        'boundary_setback_m': round(max(0.0, float(boundary_setback_m)), 2),
        'exclusion_count': len(exclusion_polys_xy),
        'wake_model': wake_model,
        'top_configs':          top5_clean,
        'constraint_evaluation': evaluation,
        'n_layouts_evaluated': evaluated_layouts,
        'search_method': 'structured_candidates_first_feasible' if layout_evaluator else 'fast_energy_score',
        'sector_energy_pct':    [round(v, 2) for v in sector_energy_pct.tolist()],
        'sector_dirs_deg':      [round(v, 1) for v in sector_dirs.tolist()],
    }

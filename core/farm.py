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
from typing import List, Tuple, Optional

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


def effective_wind_per_turbine(layout_xy: np.ndarray, v_free: float,
                               wind_dir_deg: float, rotor_d_m: float,
                               k: float = 0.075, ct: float = 0.8) -> np.ndarray:
    """
    Compute effective free-stream-equivalent wind speed at each turbine
    accounting for upstream wake superposition (Katic — sum-of-squares).

    layout_xy: (N, 2) array of turbine positions in metres.
    v_free:    free-stream wind speed (m/s) at hub height.
    wind_dir_deg: direction wind comes from (met convention).
    """
    rotated = _rotate_to_wind_axis(layout_xy, wind_dir_deg)
    n = len(rotated)
    v_eff = np.full(n, v_free, dtype=float)
    if v_free <= 0 or n <= 1:
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
        v_eff[i] = v_free * (1.0 - math.sqrt(sq_sum))
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
                       humidity_pct=0.0) -> dict:
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
    p_free_per_bin = turbine.power_kw_density_corrected(ws_centres, rho_mean)  # kW/turbina por bin
    e_free_per_turbine_kwh = float(np.sum(freq.sum(axis=0) * p_free_per_bin)) * total_hours
    e_free_total_kwh = e_free_per_turbine_kwh * n

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
            v_eff = effective_wind_per_turbine(
                layout, v_free, wind_dir, rotor_d, k=k_wake, ct=ct)
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
            'aep_no_wake_mwh': round(e_no_wake_per_turbine / 1000.0, 3),
            'wake_loss_pct': round(
                100.0 * (1 - e_i / e_no_wake_per_turbine) if e_no_wake_per_turbine > 0 else 0.0,
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

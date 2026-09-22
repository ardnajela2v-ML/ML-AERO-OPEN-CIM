"""Restricción de factibilidad por sombra intermitente (shadow flicker).

Filtro de factibilidad (paso 6 del framework de tesis), NO criterio TOPSIS
-- mismo principio que la estela y el ruido (core.acoustic_noise): un
layout que excede el límite de horas anuales de flicker en algún receptor
se descarta o se repara, independientemente de su desempeño económico.

Geometría solar: algoritmo simplificado de Meeus (1998, *Astronomical
Algorithms*, 2nd ed., Willmann-Bell), en la formulación de referencia
difundida por NOAA (precisión ~0.01°, válida 1800-2200) -- sin dependencia
externa nueva (no se usa pvlib ni llamadas a internet).

Modo por defecto 'astronomical_worst_case': asume cielo siempre despejado y
sol siempre visible cuando está sobre el horizonte (no requiere datos de
nubosidad, que el repo no tiene disponibles hoy) -- es el modo conservador
que da el límite estándar de 30 h/año de la industria. El modo 'realistic'
(ponderado por probabilidad estadística de sol) queda como extensión futura.

Referencias:
- Meeus, J. (1998), *Astronomical Algorithms*, 2nd ed., Willmann-Bell.
- LAI (Länderausschuss für Immissionsschutz), "Hinweise zur Ermittlung und
  Beurteilung der optischen Immissionen von Windenergieanlagen"
  (WEA-Schattenwurf-Hinweise), 2002 -- guía alemana que de facto implementan
  WindPRO/openWind: 30 h/año y 30 min/día como máximo astronómico, 8 h/año
  como "realista" (ponderado por probabilidad de sol).
- El límite práctico de ~10 diámetros de rotor como distancia máxima de
  influencia (más allá, el tamaño angular del rotor es imperceptible) es el
  mismo que usan WindPRO/openWind para acotar el cálculo.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ASTRONOMICAL_WORST_CASE_LIMIT_HOURS_YEAR = 30.0
REALISTIC_LIMIT_HOURS_YEAR = 8.0
EARTH_AXIAL_TILT_DEG = 23.44
DEFAULT_MAX_FLICKER_DISTANCE_D = 10.0  # diámetros de rotor


def _julian_day(timestamp_utc: datetime) -> float:
    if timestamp_utc.tzinfo is None:
        raise ValueError('timestamp_utc debe ser timezone-aware (UTC).')
    ts = timestamp_utc.astimezone(timezone.utc)
    a = (14 - ts.month) // 12
    y = ts.year + 4800 - a
    m = ts.month + 12 * a - 3
    jdn = ts.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    day_fraction = (ts.hour + ts.minute / 60.0 + ts.second / 3600.0) / 24.0 - 0.5
    return jdn + day_fraction


def solar_declination_deg(timestamp_utc: datetime) -> float:
    """Declinación solar (grados) -- expuesta por separado para poder
    validarla contra la cota física conocida (inclinación axial terrestre,
    ~23.44°) sin depender de una tabla de referencia externa."""
    declination, _eq_time, _l0 = _solar_declination_and_equation_of_time(timestamp_utc)
    return declination


def _solar_declination_and_equation_of_time(timestamp_utc: datetime) -> Tuple[float, float, float]:
    """(declinación_deg, ecuación_del_tiempo_min, longitud_media_L0_deg)
    vía el algoritmo solar de Meeus (1998) en la formulación NOAA."""
    jd = _julian_day(timestamp_utc)
    T = (jd - 2451545.0) / 36525.0
    L0 = (280.46646 + T * (36000.76983 + T * 0.0003032)) % 360.0
    M = math.radians(357.52911 + T * (35999.05029 - 0.0001537 * T))
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    C = (math.sin(M) * (1.914602 - T * (0.004817 + 0.000014 * T))
         + math.sin(2 * M) * (0.019993 - 0.000101 * T)
         + math.sin(3 * M) * 0.000289)
    true_long = L0 + C
    omega = math.radians(125.04 - 1934.136 * T)
    apparent_long = math.radians(true_long - 0.00569 - 0.00478 * math.sin(omega))
    eps0 = 23.0 + (26.0 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60.0) / 60.0
    eps = math.radians(eps0 + 0.00256 * math.cos(omega))
    declination = math.degrees(math.asin(math.sin(eps) * math.sin(apparent_long)))
    y = math.tan(eps / 2.0) ** 2
    l0r = math.radians(L0)
    eq_time = 4.0 * math.degrees(
        y * math.sin(2 * l0r) - 2 * e * math.sin(M) + 4 * e * y * math.sin(M) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * M))
    return declination, eq_time, L0


def solar_position(lat_deg: float, lon_deg: float, timestamp_utc: datetime) -> Tuple[float, float]:
    """(azimut_deg, elevación_deg) del sol para una latitud/longitud e
    instante UTC dados. Azimut en convención meteorológica (0°=Norte,
    sentido horario), igual que el resto del código (ver
    core/farm.py:layout_config['direction_convention'])."""
    declination_deg, eq_time_min, _ = _solar_declination_and_equation_of_time(timestamp_utc)
    ts = timestamp_utc.astimezone(timezone.utc)
    utc_minutes = ts.hour * 60.0 + ts.minute + ts.second / 60.0
    true_solar_time = (utc_minutes + eq_time_min + 4.0 * lon_deg) % 1440.0
    hour_angle_deg = true_solar_time / 4.0 - 180.0
    lat = math.radians(lat_deg)
    dec = math.radians(declination_deg)
    ha = math.radians(hour_angle_deg)
    cos_zenith = max(-1.0, min(1.0,
        math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)))
    zenith_deg = math.degrees(math.acos(cos_zenith))
    elevation_deg = 90.0 - zenith_deg
    sin_zenith = math.sin(math.radians(zenith_deg))
    if abs(sin_zenith) < 1e-9:
        azimuth_deg = 180.0
    else:
        base = (math.sin(lat) * math.cos(math.radians(zenith_deg)) - math.sin(dec)) / (math.cos(lat) * sin_zenith)
        base_deg = math.degrees(math.acos(max(-1.0, min(1.0, base))))
        azimuth_deg = ((base_deg + 180.0) % 360.0 if hour_angle_deg > 0
                      else (540.0 - base_deg) % 360.0)
    return azimuth_deg, elevation_deg


def is_receptor_in_flicker_zone(turbine_xy: Sequence[float], rotor_diameter_m: float,
                                hub_height_m: float, receptor_xy: Sequence[float],
                                sun_azimuth_deg: float, sun_elevation_deg: float,
                                max_flicker_distance_m: Optional[float] = None) -> bool:
    """True si, en el instante dado (sol ya ubicado), la silueta del rotor
    proyecta sombra sobre el receptor: el receptor debe estar alineado con
    la dirección anti-solar desde la turbina (dentro de la mitad del ángulo
    que subtiende el rotor a esa distancia) y a una distancia dentro del
    radio de influencia (~10D por defecto, ver DEFAULT_MAX_FLICKER_DISTANCE_D).
    `hub_height_m` se acepta por completitud de la firma (relevante para
    extensiones que consideren la altura aparente del rotor); el modelo
    simplificado actual trata la turbina como fuente puntual a nivel del
    terreno para el cálculo del acimut de sombra.
    """
    if sun_elevation_deg <= 0:
        return False
    if max_flicker_distance_m is None:
        max_flicker_distance_m = DEFAULT_MAX_FLICKER_DISTANCE_D * rotor_diameter_m
    dx = float(receptor_xy[0]) - float(turbine_xy[0])
    dy = float(receptor_xy[1]) - float(turbine_xy[1])
    distance = math.hypot(dx, dy)
    if distance < 1e-6 or distance > max_flicker_distance_m:
        return False
    bearing_to_receptor = math.degrees(math.atan2(dx, dy)) % 360.0
    shadow_bearing = (sun_azimuth_deg + 180.0) % 360.0
    angular_diff = abs(bearing_to_receptor - shadow_bearing)
    angular_diff = min(angular_diff, 360.0 - angular_diff)
    tolerance_deg = math.degrees(math.atan2(rotor_diameter_m / 2.0, distance))
    return angular_diff <= tolerance_deg


def annual_flicker_hours(turbine_xy: Sequence[float], rotor_diameter_m: float, hub_height_m: float,
                         receptor_xy: Sequence[float], lat0: float, lon0: float, *,
                         year: int = 2026, mode: str = 'astronomical_worst_case',
                         timestep_minutes: float = 10.0,
                         max_flicker_distance_m: Optional[float] = None) -> float:
    """Horas anuales de flicker de UNA turbina sobre UN receptor, barrido
    anual a paso fijo. Modo 'astronomical_worst_case' (único implementado):
    cuenta cada paso de tiempo con sol sobre el horizonte y geometría de
    sombra alineada, sin ponderar por probabilidad de cielo despejado.
    """
    if mode != 'astronomical_worst_case':
        raise NotImplementedError(
            "Solo 'astronomical_worst_case' está implementado; 'realistic' "
            'requiere datos de nubosidad/radiación no disponibles hoy en el repo.')
    dt_hours = timestep_minutes / 60.0
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    n_steps = int(round(365 * 24 * 60 / timestep_minutes))
    total_hours = 0.0
    for i in range(n_steps):
        ts = start + timedelta(minutes=i * timestep_minutes)
        azimuth, elevation = solar_position(lat0, lon0, ts)
        if elevation <= 0:
            continue
        if is_receptor_in_flicker_zone(turbine_xy, rotor_diameter_m, hub_height_m,
                                       receptor_xy, azimuth, elevation, max_flicker_distance_m):
            total_hours += dt_hours
    return total_hours


def evaluate_shadow_flicker_constraint(layout_xy_m: Sequence[Sequence[float]], turbine,
                                       receptor_points_xy: Sequence[Dict],
                                       lat0: float, lon0: float,
                                       limit_hours_year: float,
                                       mode: str = 'astronomical_worst_case',
                                       timestep_minutes: float = 10.0,
                                       year: int = 2026) -> Dict:
    """Evalúa el efecto acumulado real de shadow flicker en cada receptor,
    considerando TODAS las turbinas del layout a la vez.

    Importante: NO suma `annual_flicker_hours` turbina por turbina -- eso
    contaría dos veces un instante en que dos turbinas flickean el mismo
    receptor simultáneamente. En su lugar, para cada paso de tiempo se
    revisa si ALGUNA turbina produce flicker sobre el receptor en ese
    instante (unión, no suma), que es la definición físicamente correcta de
    "horas con flicker" en el receptor.
    """
    if mode != 'astronomical_worst_case':
        raise NotImplementedError(
            "Solo 'astronomical_worst_case' está implementado; 'realistic' "
            'requiere datos de nubosidad/radiación no disponibles hoy en el repo.')
    D = float(turbine.rotor_diameter_m)
    hub_h = float(turbine.hub_height_m or 0.0)
    max_flicker_distance_m = DEFAULT_MAX_FLICKER_DISTANCE_D * D
    dt_hours = timestep_minutes / 60.0
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    n_steps = int(round(365 * 24 * 60 / timestep_minutes))
    hours_per_receptor = [0.0 for _ in receptor_points_xy]
    for i in range(n_steps):
        ts = start + timedelta(minutes=i * timestep_minutes)
        azimuth, elevation = solar_position(lat0, lon0, ts)
        if elevation <= 0:
            continue
        for r_idx, receptor in enumerate(receptor_points_xy):
            receptor_xy = (receptor['x'], receptor['y'])
            if any(is_receptor_in_flicker_zone(turbine_xy, D, hub_h, receptor_xy,
                                               azimuth, elevation, max_flicker_distance_m)
                  for turbine_xy in layout_xy_m):
                hours_per_receptor[r_idx] += dt_hours
    per_receptor = []
    max_hours = 0.0
    all_ok = True
    for receptor, hours in zip(receptor_points_xy, hours_per_receptor):
        ok = hours <= float(limit_hours_year) + 1e-9
        all_ok = all_ok and ok
        max_hours = max(max_hours, hours)
        per_receptor.append({
            'lat': receptor.get('lat'), 'lon': receptor.get('lon'),
            'type': receptor.get('type'), 'hours_year': round(hours, 3),
            'limit_hours_year': float(limit_hours_year), 'ok': ok,
        })
    return {'ok': all_ok, 'max_receptor_hours': round(max_hours, 3), 'per_receptor': per_receptor}


def repair_shadow_flicker_violation(layout_xy_m: List[List[float]], turbine,
                                    receptor_points_xy: Sequence[Dict],
                                    lat0: float, lon0: float, limit_hours_year: float,
                                    position_allowed_fn: Callable[[float, float], bool],
                                    min_spacing_m: float,
                                    step_m: float = 20.0, max_steps: int = 25,
                                    timestep_minutes: float = 30.0, year: int = 2026) -> Dict:
    """Repara localmente un layout que excede el límite de flicker, en vez
    de descartarlo -- mismo principio que
    core.acoustic_noise.repair_noise_violation y
    core.layout_refinement._local_wake_refinement: mover la turbina que más
    horas de flicker aporta al receptor peor evaluado, un paso corto a la
    vez, revalidando el efecto real (no una distancia fija) tras cada
    movimiento. `timestep_minutes` por defecto es más grueso que en
    evaluate_shadow_flicker_constraint (30 vs 10 min) porque este bucle
    puede recalcular el barrido anual varias veces (una por paso de
    reparación) -- precisión suficiente para decidir mejora relativa, no
    para el reporte final (que debe re-evaluarse con el timestep fino).
    """
    layout = [list(point) for point in layout_xy_m]

    def _evaluate(points):
        return evaluate_shadow_flicker_constraint(
            points, turbine, receptor_points_xy, lat0, lon0, limit_hours_year,
            timestep_minutes=timestep_minutes, year=year)

    def _dominant_turbine_index(points, receptor_xy):
        D = float(turbine.rotor_diameter_m)
        hub_h = float(turbine.hub_height_m or 0.0)
        contributions = []
        for turbine_xy in points:
            hours = annual_flicker_hours(turbine_xy, D, hub_h, receptor_xy, lat0, lon0,
                                         year=year, timestep_minutes=timestep_minutes)
            contributions.append(hours)
        return max(range(len(contributions)), key=lambda i: contributions[i])

    evaluation = _evaluate(layout)
    steps_used = 0
    while not evaluation['ok'] and steps_used < max_steps:
        worst = max((r for r in evaluation['per_receptor'] if not r['ok']),
                    key=lambda r: r['hours_year'] - r['limit_hours_year'])
        receptor = next(r for r in receptor_points_xy
                        if r.get('lat') == worst['lat'] and r.get('lon') == worst['lon'])
        receptor_xy = (receptor['x'], receptor['y'])
        idx = _dominant_turbine_index(layout, receptor_xy)
        tx, ty = layout[idx]
        rx, ry = receptor_xy
        direction = math.atan2(ty - ry, tx - rx) if (tx, ty) != (rx, ry) else 0.0
        candidate_x = tx + step_m * math.cos(direction)
        candidate_y = ty + step_m * math.sin(direction)
        if not position_allowed_fn(candidate_x, candidate_y):
            break
        others = [p for j, p in enumerate(layout) if j != idx]
        if any(math.hypot(candidate_x - ox, candidate_y - oy) < min_spacing_m for ox, oy in others):
            break
        candidate_layout = [list(p) for p in layout]
        candidate_layout[idx] = [candidate_x, candidate_y]
        candidate_evaluation = _evaluate(candidate_layout)
        if candidate_evaluation['max_receptor_hours'] >= evaluation['max_receptor_hours']:
            break
        layout = candidate_layout
        evaluation = candidate_evaluation
        steps_used += 1
    if steps_used > 0:
        # Reporte final con la precisión fina por defecto, no la usada durante la reparación.
        evaluation = evaluate_shadow_flicker_constraint(
            layout, turbine, receptor_points_xy, lat0, lon0, limit_hours_year, year=year)
    return {'layout_xy_m': layout, 'repaired': evaluation['ok'],
           'steps_used': steps_used, 'evaluation': evaluation}

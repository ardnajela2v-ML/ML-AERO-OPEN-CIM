"""Restricción de factibilidad por ruido para layouts de parque eólico.

Filtro de factibilidad (paso 6 del framework de tesis), NO criterio TOPSIS
-- igual que la pérdida por estela ya trata core.layout_generation. Un
layout que excede el límite de ruido en cualquier receptor sensible se
descarta (o se repara, ver repair_noise_violation), independientemente de
qué tan bueno sea económicamente.

Modelo de propagación: esférica de banda ancha + absorción atmosférica
lineal con la distancia + suma logarítmica de fuentes múltiples --
simplificación de screening estándar en la industria eólica frente a
ISO 9613-2 completo (que requiere datos por banda de octava, efecto de
suelo Agr y apantallamiento Abar no disponibles a nivel de prefactibilidad).
Se asume Agr=0 (suelo duro), hipótesis conservadora explícita.

Referencias:
- ISO 9613-2:1996, "Acoustics -- Attenuation of sound during propagation
  outdoors -- Part 2: General method of calculation".
- IEC 61400-11:2012, "Wind turbine generator systems -- Part 11: Acoustic
  noise measurement techniques" (fuente estándar del dato de entrada Lwa).
- ETSU-R-97 (1996), "The Assessment and Rating of Noise from Wind Farms"
  (UK, DTI) y IFC/World Bank "Environmental, Health, and Safety Guidelines
  for Wind Energy" (2015) -- umbrales típicos de referencia en receptores
  (35-45 dB(A) nocturno, 40-55 dB(A) diurno; configurables por el usuario,
  no hardcodeados aquí).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

DEFAULT_ATMOSPHERIC_ALPHA_DB_KM = 1.5
_GEOMETRIC_SPREADING_CONSTANT_DB = 11.0  # ISO 9613-2 simplificado, fuente puntual


def sound_pressure_from_source_dba(lw_dba: float, distance_m: float,
                                   atmospheric_alpha_db_km: float = DEFAULT_ATMOSPHERIC_ALPHA_DB_KM) -> float:
    """Nivel de presión sonora Lp (dB(A)) en un receptor a `distance_m` de
    UNA fuente puntual de potencia sonora `lw_dba`, por propagación esférica
    con absorción atmosférica lineal (ISO 9613-2 simplificado):

        Lp(r) = Lw - 20*log10(r) - 11 - alpha*(r/1000)

    `distance_m` debe ser > 0 (una turbina exactamente sobre el receptor no
    es una configuración físicamente sensata; se trata como caso degenerado
    devolviendo Lw sin atenuación).
    """
    r = max(float(distance_m), 1.0)
    return (float(lw_dba) - 20.0 * math.log10(r) - _GEOMETRIC_SPREADING_CONSTANT_DB
            - float(atmospheric_alpha_db_km) * (r / 1000.0))


def sound_power_at_receptor_dba(turbine_positions_xy: Sequence[Sequence[float]],
                                lw_per_turbine_dba: Sequence[float],
                                receptor_xy: Sequence[float],
                                atmospheric_alpha_db_km: float = DEFAULT_ATMOSPHERIC_ALPHA_DB_KM) -> float:
    """Lp total (dB(A)) en un receptor por suma logarítmica (energética) de
    la contribución de N turbinas -- ISO 9613-2 exige sumar así, no en dB
    lineal: duplicar el número de fuentes iguales suma +3 dB, no +100%.
    """
    if len(turbine_positions_xy) != len(lw_per_turbine_dba):
        raise ValueError('turbine_positions_xy y lw_per_turbine_dba deben tener la misma longitud.')
    if not turbine_positions_xy:
        return -math.inf
    rx, ry = float(receptor_xy[0]), float(receptor_xy[1])
    energy_sum = 0.0
    for (tx, ty), lw in zip(turbine_positions_xy, lw_per_turbine_dba):
        distance = math.hypot(float(tx) - rx, float(ty) - ry)
        lp = sound_pressure_from_source_dba(lw, distance, atmospheric_alpha_db_km)
        energy_sum += 10.0 ** (lp / 10.0)
    return 10.0 * math.log10(energy_sum) if energy_sum > 0 else -math.inf


def noise_screening_radius_m(lw_max_catalog_dba: float, strictest_limit_dba: float,
                             safety_margin_db: float = 6.0,
                             atmospheric_alpha_db_km: float = DEFAULT_ATMOSPHERIC_ALPHA_DB_KM) -> float:
    """Distancia de screening (filtro de cómputo, NO criterio de
    factibilidad): a partir de qué distancia una sola turbina -- la más
    ruidosa del catálogo -- ya está por debajo del límite más estricto
    configurado, con un margen de seguridad para cubrir el efecto acumulado
    de varias turbinas (la suma logarítmica de N fuentes iguales añade
    ~10*log10(N) dB frente a una sola; `safety_margin_db=6` cubre hasta ~4
    turbinas suficientemente juntas antes de requerir revisión explícita).

    Se resuelve numéricamente (bisección) porque el término de absorción
    atmosférica es lineal en r mientras el de propagación geométrica es
    logarítmico -- no hay forma cerrada simple para r.

    Por diseño, este radio NUNCA debe usarse como exclusión de colocación:
    solo decide qué pares turbina-receptor requieren el cálculo acumulado
    real (sound_power_at_receptor_dba). Ver discusión de diseño en el plan
    de la Fase 3 (core/layout_generation.py, docstring del módulo).
    """
    target_lp = float(strictest_limit_dba) - float(safety_margin_db)
    lo, hi = 1.0, 50_000.0
    if sound_pressure_from_source_dba(lw_max_catalog_dba, lo, atmospheric_alpha_db_km) <= target_lp:
        return lo
    if sound_pressure_from_source_dba(lw_max_catalog_dba, hi, atmospheric_alpha_db_km) > target_lp:
        return hi  # incluso a 50 km no cae bajo el límite: cota superior de seguridad
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sound_pressure_from_source_dba(lw_max_catalog_dba, mid, atmospheric_alpha_db_km) > target_lp:
            lo = mid
        else:
            hi = mid
    return hi


def evaluate_noise_constraint(layout_xy_m: Sequence[Sequence[float]], turbine,
                              receptor_points_xy: Sequence[Dict],
                              noise_limit_dba: Dict[str, float],
                              atmospheric_alpha_db_km: float = DEFAULT_ATMOSPHERIC_ALPHA_DB_KM) -> Dict:
    """Evalúa el efecto acumulado real de ruido en cada receptor.

    `receptor_points_xy`: lista de dicts {'x','y','type','period'} (period
    'day'|'night', indexa `noise_limit_dba`). `noise_limit_dba`: p. ej.
    {'day': 55.0, 'night': 45.0} (valores de referencia ETSU-R-97/IFC-EHS,
    configurables por el usuario -- no hardcodeados con un default aquí).

    Devuelve {'ok', 'max_receptor_dba', 'per_receptor': [...]}.
    """
    lw = float(turbine.sound_power_lwa_db) if getattr(turbine, 'sound_power_lwa_db', None) else 103.0
    lw_per_turbine = [lw] * len(layout_xy_m)
    per_receptor = []
    max_dba = -math.inf
    all_ok = True
    for receptor in receptor_points_xy:
        period = receptor.get('period', 'night')
        limit = float(noise_limit_dba.get(period, min(noise_limit_dba.values())))
        lp_total = sound_power_at_receptor_dba(
            layout_xy_m, lw_per_turbine, (receptor['x'], receptor['y']), atmospheric_alpha_db_km)
        ok = lp_total <= limit + 1e-9
        all_ok = all_ok and ok
        max_dba = max(max_dba, lp_total)
        per_receptor.append({
            'lat': receptor.get('lat'), 'lon': receptor.get('lon'),
            'type': receptor.get('type'), 'period': period,
            'lp_dba': round(lp_total, 2), 'limit_dba': limit, 'ok': ok,
        })
    return {'ok': all_ok, 'max_receptor_dba': (None if max_dba == -math.inf else round(max_dba, 2)),
           'per_receptor': per_receptor, 'sound_power_lwa_db': lw,
           'sound_power_source': getattr(turbine, 'sound_power_source', None)}


def _dominant_contributor_index(layout_xy_m: Sequence[Sequence[float]], lw: float,
                                receptor_xy: Tuple[float, float],
                                atmospheric_alpha_db_km: float) -> int:
    """Índice de la turbina que más Lp individual aporta a un receptor --
    con suma logarítmica, la fuente más cercana domina casi siempre, pero se
    calcula explícitamente para no asumirlo quando hay turbinas a distancias
    similares."""
    rx, ry = receptor_xy
    contributions = [
        sound_pressure_from_source_dba(lw, math.hypot(x - rx, y - ry), atmospheric_alpha_db_km)
        for x, y in layout_xy_m
    ]
    return max(range(len(contributions)), key=lambda i: contributions[i])


def repair_noise_violation(layout_xy_m: List[List[float]], turbine,
                           receptor_points_xy: Sequence[Dict],
                           noise_limit_dba: Dict[str, float],
                           position_allowed_fn: Callable[[float, float], bool],
                           min_spacing_m: float,
                           step_m: float = 20.0, max_steps: int = 25,
                           atmospheric_alpha_db_km: float = DEFAULT_ATMOSPHERIC_ALPHA_DB_KM) -> Dict:
    """Repara localmente un layout que excede el límite de ruido, en vez de
    descartarlo de inmediato -- mismo principio que
    core.layout_refinement._local_wake_refinement: mover la turbina que más
    contribuye al receptor excedido, un paso corto a la vez, alejándola
    en línea recta desde el receptor, aceptando el movimiento solo si:
    (a) sigue dentro de una posición permitida (polígono/exclusión/pendiente,
    vía `position_allowed_fn`), (b) respeta la separación mínima con las
    demás turbinas, y (c) el Lp recalculado en TODOS los receptores mejora.
    Nunca se mueve a una distancia fija ("zona de influencia"): se revalida
    el efecto acumulado real en cada paso.

    Devuelve {'layout_xy_m', 'repaired': bool, 'steps_used': int,
    'evaluation': <última evaluate_noise_constraint>}.
    """
    layout = [list(point) for point in layout_xy_m]
    lw = float(turbine.sound_power_lwa_db) if getattr(turbine, 'sound_power_lwa_db', None) else 103.0

    def _evaluate(points):
        return evaluate_noise_constraint(points, turbine, receptor_points_xy,
                                         noise_limit_dba, atmospheric_alpha_db_km)

    evaluation = _evaluate(layout)
    steps_used = 0
    while not evaluation['ok'] and steps_used < max_steps:
        worst = max((r for r in evaluation['per_receptor'] if not r['ok']),
                    key=lambda r: r['lp_dba'] - r['limit_dba'])
        receptor = next(r for r in receptor_points_xy
                        if r.get('lat') == worst['lat'] and r.get('lon') == worst['lon'])
        idx = _dominant_contributor_index(layout, lw, (receptor['x'], receptor['y']), atmospheric_alpha_db_km)
        tx, ty = layout[idx]
        rx, ry = receptor['x'], receptor['y']
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
        if candidate_evaluation['max_receptor_dba'] is not None and evaluation['max_receptor_dba'] is not None \
                and candidate_evaluation['max_receptor_dba'] >= evaluation['max_receptor_dba']:
            break  # el movimiento no mejora el peor receptor: no seguir por este camino
        layout = candidate_layout
        evaluation = candidate_evaluation
        steps_used += 1
    return {'layout_xy_m': layout, 'repaired': evaluation['ok'],
           'steps_used': steps_used, 'evaluation': evaluation}

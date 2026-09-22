"""Layout candidate generation, separated from champion selection and from
TOPSIS ranking (core.multicriteria.rank_layout_alternatives).

Framework de tesis: paso 6 (generación de layouts factibles dentro del
polígono) debe preceder al paso 7 (selección multicriterio AHP+TOPSIS).
Antes de este módulo, core.farm_mcda.analyze_park_layouts mezclaba ambos
pasos: para cada tecnología, iteraba N de mayor a menor y se detenía
("break") en el primer N cuya pérdida por estela cumplía el límite
configurado, colapsando el espacio de búsqueda a una única configuración
por tecnología antes de que TOPSIS pudiera comparar nada.

Este módulo resuelve el paso 6 de forma explícita:

- generate_feasible_layouts_grid(): para una tecnología dada, genera TODOS
  los layouts factibles (o no, con motivo) que resultan de variar N sobre
  todo el rango físicamente posible y, dentro de cada N, el patrón
  geométrico (orientación/fase/tresbolillo), reutilizando
  core.farm.optimize_layout_structured con candidate_limit>0.
- select_technology_champion(): elige, con una regla explícita y auditable,
  un único representante por tecnología para alimentar el TOPSIS final
  (core.multicriteria.rank_layout_alternatives) -- sin adelantar ahí la
  ponderación económica que es el objeto propio del paso 7.

max_slope_deg en LayoutConstraints es un hook declarado para una fase
futura (pendiente); ruido (noise_limit_dba) y shadow flicker
(shadow_flicker_limit_hours_year), ambos sobre receptor_points, ya se
evalúan como gates reales cuando el llamador los provee (ver
core.acoustic_noise y core.shadow_flicker) -- si no se proveen, el
comportamiento es idéntico al anterior (solo estela).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.acoustic_noise import (
    DEFAULT_ATMOSPHERIC_ALPHA_DB_KM, evaluate_noise_constraint,
    repair_noise_violation, sound_pressure_from_source_dba,
)
from core.shadow_flicker import (
    DEFAULT_MAX_FLICKER_DISTANCE_D, evaluate_shadow_flicker_constraint,
    repair_shadow_flicker_violation,
)
from core.energy import PowerCurve
from core.farm import (
    _latlon_to_xy, _point_allowed_xy, _xy_to_latlon, build_slope_mask_fn,
    optimize_layout_greedy_vertex, optimize_layout_structured,
)
from core.osm_infrastructure import feature_to_exclusion_polygon

# Presupuesto de pasos de reparación DENTRO del embudo automático -- más
# bajo que el default de las funciones de reparación (25), porque aquí se
# paga por cada candidato del pool, no una vez por una acción manual del
# usuario (ver mcdaUseMitigationAlternative en el frontend, que sí puede
# permitirse un presupuesto mayor si se expone como acción explícita).
_AUTO_REPAIR_MAX_STEPS = 20


@dataclass
class LayoutConstraints:
    """Restricciones de factibilidad para la generación de layouts."""
    max_wake_loss_pct: float = 5.0
    max_slope_deg: Optional[float] = None
    receptor_points: Optional[List[Dict]] = None
    noise_limit_dba: Optional[Dict[str, float]] = None
    shadow_flicker_limit_hours_year: Optional[float] = None
    # Si True (y hay receptor_points), receptor_exclusion_buffers() se resta
    # del área buscable ANTES de generar candidatos -- zona de exclusión
    # real, no solo el gate reactivo (evaluate_*_constraint + repair) que ya
    # corre siempre que hay receptores configurados. Los dos mecanismos
    # coexisten a propósito: el buffer es preventivo pero solo modela una
    # turbina aislada; el gate reactivo sigue como red de seguridad para el
    # efecto acumulado de varias turbinas cerca del mismo receptor.
    include_environmental: bool = False


def _layout_signature(layout_xy_m: Sequence[Sequence[float]]) -> tuple:
    return tuple(sorted(tuple(round(float(v), 3) for v in point) for point in layout_xy_m))


def _local_wake_refinement(points, evaluate, valid_geometry, *, wake_limit,
                            max_radius_m, passes=2, n_angles=8, n_radii=3,
                            max_evals=250):
    """Bounded coordinate-descent around a fixed layout.

    Nudges the most wake-affected turbines a short distance (<= max_radius_m,
    roughly 0.5-1 rotor diameter) from their current position to cut estela
    and, once compliant, lift AEP — without the row/column spacing rule that
    only ever stood in as a proxy for limiting wake. Each accepted move keeps
    every other turbine fixed, so the seed pattern (aligned/tresbolillo/
    TOPSIS) stays recognisable; it is refined, not replaced by a free search.
    Accept-if-better against a single objective that prioritises meeting
    wake_limit and only then rewards extra AEP, so it usefully serves both
    "rescue an infeasible family" and "improve an already-feasible layout".

    Compartida entre core.layout_generation (paso 6, rescata el finalista con
    menos estela de una tecnología cuando ninguno de los candidatos de
    rejilla cumple el límite) y core.layout_refinement (paso 8, mismo rescate
    por familia sobre el ganador de TOPSIS) -- antes solo vivía en el paso 8,
    así que el paso 6 comparaba tecnologías con layouts sistemáticamente
    menos pulidos que los que Diseño Optimizado era capaz de producir.
    """
    points = [list(map(float, p)) for p in points]
    current_sim = evaluate(points)
    if current_sim.get('error'):
        return points, current_sim

    def score(sim):
        return float(sim['aep_mwh']) - 1e6 * max(0.0, float(sim['wake_loss_pct']) - wake_limit)

    best_score = score(current_sim)
    radii = [max_radius_m * f for f in np.linspace(1.0 / n_radii, 1.0, n_radii)]
    angles = np.linspace(0.0, 2 * np.pi, n_angles, endpoint=False)
    n = len(points)
    evals = 0
    for _ in range(passes):
        if evals >= max_evals:
            break
        moved = False
        order_idx = sorted(range(n), key=lambda i: -current_sim['turbines'][i]['wake_loss_pct'])
        budget = max(1, int(np.ceil(n * 0.6)))
        for i in order_idx[:budget]:
            if evals >= max_evals:
                break
            origin = points[i]
            local_best = None
            for r in radii:
                if evals >= max_evals:
                    break
                for a in angles:
                    if evals >= max_evals:
                        break
                    trial_point = [origin[0] + r * float(np.cos(a)), origin[1] + r * float(np.sin(a))]
                    trial_points = points[:i] + [trial_point] + points[i + 1:]
                    if not valid_geometry(trial_points):
                        continue
                    sim = evaluate(trial_points)
                    evals += 1
                    if sim.get('error'):
                        continue
                    s = score(sim)
                    if s > best_score + 1e-6 and (local_best is None or s > local_best[2]):
                        local_best = (trial_point, sim, s)
            if local_best is not None:
                points[i] = local_best[0]
                current_sim = local_best[1]
                best_score = local_best[2]
                moved = True
        if not moved:
            break
    return points, current_sim


# Familias de layout estructurado -- 'stagger' es el mismo parámetro de
# core.farm.optimize_layout_structured (0.0 = alineado, 0.5 = tresbolillo/
# quincunx). Compartido entre generate_feasible_layouts_grid (paso 6, ahora
# corre cada familia por separado) y core.layout_refinement.refine_topsis_layout
# (paso 8, herramienta manual opcional) para que ambos hablen de "aligned"/
# "staggered" con las mismas claves.
FAMILY_STAGGER = {'aligned': 0.0, 'staggered': 0.5}

# Tercera familia: crecimiento greedy sembrado en un vértice, ejes de viento
# (core.farm.optimize_layout_greedy_vertex) -- no es "stagger-based" como las
# dos anteriores (no tiene orientación/fase de rejilla fija), así que se
# maneja por separado en generate_feasible_layouts_grid, pero comparte la
# misma clave 'family' en layout_config y el mismo formato de pool que
# 'aligned'/'staggered' para que select_technology_champion y family_summary
# no necesiten distinguir su origen.
GREEDY_FAMILY = 'greedy'


def _invert_noise_distance_m(lw_dba: float, limit_dba: float,
                             atmospheric_alpha_db_km: float = DEFAULT_ATMOSPHERIC_ALPHA_DB_KM) -> float:
    """Distancia (m) a la que UNA turbina de nivel de potencia sonora
    `lw_dba` cae exactamente a `limit_dba` en el receptor -- bisección sobre
    sound_pressure_from_source_dba, misma técnica numérica que
    core.acoustic_noise.noise_screening_radius_m pero invertida sobre el
    Lwa real de la turbina y el límite real del receptor, no sobre el peor
    caso de catálogo con margen de seguridad (esa función es un filtro de
    cómputo, no una exclusión geométrica -- ver su docstring)."""
    lo, hi = 1.0, 50_000.0
    if sound_pressure_from_source_dba(lw_dba, lo, atmospheric_alpha_db_km) <= limit_dba:
        return 0.0
    if sound_pressure_from_source_dba(lw_dba, hi, atmospheric_alpha_db_km) > limit_dba:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sound_pressure_from_source_dba(lw_dba, mid, atmospheric_alpha_db_km) > limit_dba:
            lo = mid
        else:
            hi = mid
    return hi


def receptor_exclusion_buffers(receptor_points, turbine, noise_limit_dba=None,
                               shadow_flicker_limit_hours_year=None):
    """Radio de resguardo por receptor -- zona de exclusión geométrica real,
    usada tanto en paso 6 (generate_feasible_layouts_grid, cuando
    constraints.include_environmental) como en paso 8
    (core.layout_refinement.refine_topsis_layout, herramienta manual): la
    búsqueda evita el círculo desde el inicio, en vez de generar un layout y
    detectar después que viola el límite.

    A diferencia de core.acoustic_noise.noise_screening_radius_m (filtro de
    cómputo interno, nunca exclusión de colocación -- ver su docstring),
    este radio SÍ se resta del área buscable.

    Ruido: distancia a la que UNA sola turbina de este modelo, aislada, ya
    cae bajo el límite del periodo del receptor (bisección exacta, sin
    margen de catálogo). Es una aproximación de screening explícita: si el
    layout final coloca más de una turbina cerca del mismo receptor, la suma
    logarítmica acumulada puede seguir excediendo el límite aun respetando
    este radio individual -- por eso el gate reactivo
    (evaluate_noise_constraint/repair_noise_violation, ya integrado en
    generate_feasible_layouts_grid) sigue corriendo como red de seguridad.
    Sombra: DEFAULT_MAX_FLICKER_DISTANCE_D (~10D), el mismo radio práctico
    de influencia que ya cita core.shadow_flicker como criterio de
    screening estándar (WindPRO/openWind) -- invertir la geometría solar
    real por receptor no es práctico a este nivel.

    Devuelve [{'lat','lon','radius_m','reason'}, ...] -- 'reason' documenta
    si el radio lo determina 'noise', 'shadow_flicker' o ambos.
    """
    if not receptor_points:
        return []
    lw = float(turbine.sound_power_lwa_db) if getattr(turbine, 'sound_power_lwa_db', None) else 103.0
    D = float(turbine.rotor_diameter_m or 80.0)
    buffers = []
    for receptor in receptor_points:
        radius_noise = 0.0
        radius_flicker = 0.0
        reasons = []
        if noise_limit_dba:
            period = receptor.get('period', 'night')
            limit = float(noise_limit_dba.get(period, min(noise_limit_dba.values())))
            radius_noise = _invert_noise_distance_m(lw, limit)
            if radius_noise > 0:
                reasons.append('noise')
        if shadow_flicker_limit_hours_year is not None:
            radius_flicker = DEFAULT_MAX_FLICKER_DISTANCE_D * D
            reasons.append('shadow_flicker')
        radius_m = max(radius_noise, radius_flicker)
        if radius_m > 0:
            buffers.append({
                'lat': float(receptor['lat']), 'lon': float(receptor['lon']),
                'radius_m': radius_m, 'reason': '+'.join(reasons),
            })
    return buffers


def generate_feasible_layouts_grid(
        polygon, turbine: PowerCurve, *, ws, wd, wind_grid, fallback_wind_ms,
        rho: float, exclusions, boundary_setback_m: float, min_spacing_d: float,
        s_para_range: Tuple, s_perp_range: Tuple, wake_model: str, k_wake: float,
        ct: float, evaluate_layout: Callable, constraints: LayoutConstraints,
        n_range: Optional[Tuple[int, int]] = None,
        n_window: int = 5,
        candidate_limit_per_n: int = 4,
        grid_limit_mw: Optional[float] = None,
        local_refinement_max_evals: int = 60) -> Dict:
    """Genera el pool de layouts factibles de una tecnología, variando N.

    A diferencia del comportamiento anterior (romper en el primer N, de
    mayor a menor, que cumple el límite de estela), aquí se evalúan TODOS
    los N del rango -- factibles o no -- para que select_technology_champion
    pueda elegir con una regla explícita entre un conjunto real de
    alternativas, en vez de heredar una decisión ya tomada por el orden de
    iteración.

    Familias explícitas (Alineado/Tresbolillo/Greedy-vértice): el barrido de
    N se corre UNA VEZ POR FAMILIA, no mezclado -- antes
    optimize_layout_structured recibía `stagger_options=(0.0, 0.5)` juntos y
    los candidate_limit_per_n finalistas que sobrevivían se elegían solo por
    score rápido, así que una familia podía quedar fuera del pool sin que
    nadie lo notara si otra puntuaba mejor para esa N. Con el barrido
    separado, cada familia siempre aporta sus propios candidatos al pool
    (tageados en layout_config['family']), y select_technology_champion elige
    el máximo VAN factible entre las tres -- la comparación queda explícita
    y auditable (ver family_summary en el resultado), en vez de una decisión
    implícita del ranking por score. Esto es lo que antes solo hacía
    core.layout_refinement.refine_topsis_layout (paso 8) para el ganador de
    TOPSIS; ahora corre para cada tecnología ANTES de TOPSIS, para que el
    ranking compare layouts igual de profundos entre tecnologías.

    Las dos familias estructuradas (FAMILY_STAGGER: aligned/staggered) son
    regulares y energy-maximising -- optimize_layout_structured prueba
    varias orientaciones/fases/separaciones sobre una rejilla rígida. La
    tercera familia (GREEDY_FAMILY) es distinta en naturaleza: crece un
    layout irregular turbina por turbina desde el vértice más a barlovento,
    buscando la siguiente posición sobre los ejes alineados al viento de
    cada turbina ya colocada (optimize_layout_greedy_vertex) -- packing-
    maximising, útil cuando la forma del polígono deja espacio que una
    rejilla única no puede aprovechar. No sustituye a las regulares, se
    compara junto a ellas.

    Cada N de las familias estructuradas se evalúa con la geometría de mejor
    score rápido (igual que antes) más, si candidate_limit_per_n>1, hasta esa
    cantidad de patrones geométricos adicionales (orientación/fase
    distintas) del mismo N, también validados con la simulación de estela
    real -- no solo con el proxy de score rápido. La familia greedy no tiene
    ese concepto de patrones alternativos por N: una sola pasada de
    crecimiento ya da una secuencia anidada válida para todo N (ver
    docstring de optimize_layout_greedy_vertex), así que es más barata pese
    a sumar una tercera vía de comparación.

    Por costo computacional (cada N de una familia estructurada cuesta una
    simulación real de estela, multiplicada por candidate_limit_per_n, ahora
    ×2 familias estructuradas + la pasada única de greedy), el rango por
    defecto NO barre 1..Nmax completo: explora una ventana de `n_window`
    valores de N hacia abajo desde el Nmax de CADA familia (donde vive casi
    siempre el óptimo real). Pasar `n_range` explícito permite un barrido
    más amplio (o el 1..Nmax completo) cuando se necesite para un análisis
    de sensibilidad puntual -- se aplica igual a las tres familias.
    """
    # Receptores -> zona de exclusión geométrica real (opt-in vía
    # constraints.include_environmental), ANTES de construir common_kwargs:
    # se resta del área buscable exactamente como una exclusión dibujada a
    # mano u OSM (reutiliza feature_to_exclusion_polygon, mismo formato que
    # ya consume optimize_layout_structured). El gate reactivo de abajo
    # (evaluate_noise_constraint/repair) sigue como red de seguridad para el
    # efecto acumulado de varias turbinas que un buffer de una sola no
    # puede prever -- ver receptor_exclusion_buffers.
    exclusions = list(exclusions or [])
    if constraints.include_environmental and constraints.receptor_points:
        for buf in receptor_exclusion_buffers(
                constraints.receptor_points, turbine, constraints.noise_limit_dba,
                constraints.shadow_flicker_limit_hours_year):
            exclusions.append(feature_to_exclusion_polygon(
                [[buf['lat'], buf['lon']]], 'point', buf['radius_m']))

    common_kwargs = dict(
        ws=ws, wd=wd, wind_grid=wind_grid, fallback_wind_ms=fallback_wind_ms,
        k_wake=k_wake, ct=ct, min_spacing_d=min_spacing_d,
        s_para_range=tuple(s_para_range), s_perp_range=tuple(s_perp_range),
        rho=rho, exclusion_polygons=exclusions,
        boundary_setback_m=boundary_setback_m, wake_model=wake_model,
        max_slope_deg=constraints.max_slope_deg,
    )

    # Geometría compartida entre familias (no depende de stagger): polígono/
    # exclusiones (ya con los buffers de receptores si aplica) proyectados
    # una sola vez, para position_allowed_fn/valid_geometry (reparación y
    # rescate por ajuste local) y para receptor_points_xy (evaluación real
    # de ruido/flicker). Mismo origen local (centroide del polígono) que usa
    # optimize_layout_structured internamente, para que xy<->lat/lon sea
    # consistente en todo el módulo.
    receptor_points_xy = None
    min_spacing_m = min_spacing_d * float(turbine.rotor_diameter_m or 80.0)
    lats_p = [float(p['lat']) for p in polygon]
    lons_p = [float(p['lon']) for p in polygon]
    site_lat0 = sum(lats_p) / len(lats_p)
    site_lon0 = sum(lons_p) / len(lons_p)
    poly_xs, poly_ys = _latlon_to_xy(lats_p, lons_p, site_lat0, site_lon0)
    exclusion_polys_xy = []
    for exclusion in exclusions:
        ex_lats = [float(p['lat']) for p in exclusion]
        ex_lons = [float(p['lon']) for p in exclusion]
        if len(ex_lats) >= 3:
            ex_xs, ex_ys = _latlon_to_xy(ex_lats, ex_lons, site_lat0, site_lon0)
            exclusion_polys_xy.append((ex_xs, ex_ys))
    slope_mask_fn = build_slope_mask_fn(lats_p, lons_p, constraints.max_slope_deg)

    def position_allowed_fn(x, y):
        if not _point_allowed_xy(x, y, poly_xs, poly_ys, exclusion_polys_xy, boundary_setback_m):
            return False
        if slope_mask_fn is not None:
            lat_pt, lon_pt = _xy_to_latlon([x], [y], site_lat0, site_lon0)
            return slope_mask_fn(lat_pt[0], lon_pt[0])
        return True

    def valid_geometry(points):
        if not all(position_allowed_fn(x, y) for x, y in points):
            return False
        for i, (xi, yi) in enumerate(points):
            for xj, yj in points[i + 1:]:
                if math.hypot(xi - xj, yi - yj) < min_spacing_m - 1e-6:
                    return False
        return True

    if constraints.receptor_points and (constraints.noise_limit_dba
                                        or constraints.shadow_flicker_limit_hours_year):
        receptor_points_xy = []
        for receptor in constraints.receptor_points:
            rx, ry = _latlon_to_xy([float(receptor['lat'])], [float(receptor['lon'])], site_lat0, site_lon0)
            receptor_points_xy.append({**receptor, 'x': rx[0], 'y': ry[0]})

    pool: List[Dict] = []
    max_n_fit_by_family: Dict[str, int] = {}
    max_n_technical_by_family: Dict[str, int] = {}
    n_range_explored_by_family: Dict[str, List[int]] = {}

    # Rescate por ajuste local + embudo ruido/sombra + volcado a `pool`,
    # compartido por las 3 familias (aligned/staggered vía
    # optimize_layout_structured, greedy vía optimize_layout_greedy_vertex):
    # antes de la familia greedy esta lógica solo se recorría una vez por
    # familia estructurada; factorizarla evita mantener dos copias del
    # embudo de reparación de ruido/sombra, que es la parte más delicada de
    # este módulo (ver el caveat en receptor_exclusion_buffers sobre por qué
    # el gate reactivo debe seguir corriendo aunque ya exista la exclusión
    # geométrica proactiva).
    def process_geometries_for_n(n: int, geometries: List[Dict],
                                  min_sep_m_val: float, area_km2_val: float,
                                  source_method: str) -> bool:
        # Rescate por ajuste local: si ninguno de los finalistas de esta
        # N/familia cumple el límite de estela, se ajusta el de menor estela
        # con _local_wake_refinement -- mismo mecanismo que ya usaba solo
        # core.layout_refinement.refine_topsis_layout para el ganador de
        # TOPSIS. Ahora corre por familia y por tecnología, no solo para el
        # ganador final, así que su presupuesto (local_refinement_max_evals,
        # default 60 -- bastante menor que los 250 que sigue usando paso 8
        # para el único ganador) se acota explícitamente: puede dispararse
        # hasta n_window × 3 familias × N_tecnologías veces en una sola
        # corrida de comparación, y a 250 evals cada uno eso multiplica el
        # tiempo más allá de lo razonable para explorar varias tecnologías
        # (medido en producción: 8-13 tecnologías tardaban 20-30 min).
        if not any(float(g['sim'].get('wake_loss_pct') or 0.0)
                   <= float(constraints.max_wake_loss_pct) + 1e-9 for g in geometries):
            seed = min(geometries, key=lambda g: float(g['sim'].get('wake_loss_pct') or math.inf))
            refined_xy, refined_sim = _local_wake_refinement(
                seed['layout_xy_m'], evaluate_layout, valid_geometry,
                wake_limit=float(constraints.max_wake_loss_pct),
                max_radius_m=float(turbine.rotor_diameter_m or 80.0),
                max_evals=local_refinement_max_evals)
            if (not refined_sim.get('error')
                    and float(refined_sim.get('wake_loss_pct') or 0.0)
                    <= float(constraints.max_wake_loss_pct) + 1e-9):
                refined_lats, refined_lons = _xy_to_latlon(
                    [p[0] for p in refined_xy], [p[1] for p in refined_xy], site_lat0, site_lon0)
                geometries.append({
                    'layout_xy_m': refined_xy,
                    'layout_latlon': [[la, lo] for la, lo in zip(refined_lats, refined_lons)],
                    'layout_config': {**seed['layout_config'], 'refined_locally': True},
                    'sim': refined_sim, 'is_primary': False, 'n_layouts_evaluated': None,
                })
        n_has_feasible = False
        for geometry in geometries:
            wake_loss_pct = float(geometry['sim'].get('wake_loss_pct') or 0.0)
            wake_loss_ok = wake_loss_pct <= float(constraints.max_wake_loss_pct) + 1e-9
            # Embudo de costo creciente: el ruido (suma logarítmica por
            # receptor) solo se evalúa sobre geometrías que YA pasaron
            # estela -- no tiene sentido pagar ese cálculo si el candidato
            # ya es infactible por otra razón.
            repaired_for = []
            noise_eval = None
            noise_ok = True
            if receptor_points_xy is not None and constraints.noise_limit_dba and wake_loss_ok:
                noise_eval = evaluate_noise_constraint(
                    geometry['layout_xy_m'], turbine, receptor_points_xy,
                    constraints.noise_limit_dba)
                noise_ok = noise_eval['ok']
                if not noise_ok:
                    # Reparación en vez de descarte inmediato: mover la
                    # turbina que más contribuye al receptor excedido, y
                    # SOLO aceptar el resultado si la estela sigue dentro
                    # del límite tras el movimiento -- nunca arreglar un
                    # problema causando otro en silencio.
                    # noise_eval NO se sobreescribe salvo éxito confirmado
                    # (estela + ruido tras el movimiento): si la reparación
                    # falla o queda a medias, layout_xy_m y noise_evaluation
                    # deben seguir describiendo la MISMA posición (la
                    # original) -- nunca reportar el Lp de un intento que no
                    # se adoptó.
                    repair = repair_noise_violation(
                        geometry['layout_xy_m'], turbine, receptor_points_xy,
                        constraints.noise_limit_dba, position_allowed_fn=position_allowed_fn,
                        min_spacing_m=min_spacing_m, max_steps=_AUTO_REPAIR_MAX_STEPS)
                    if repair['repaired']:
                        repaired_sim = evaluate_layout(repair['layout_xy_m'])
                        repaired_wake = float(repaired_sim.get('wake_loss_pct') or 0.0)
                        if (not repaired_sim.get('error')
                                and repaired_wake <= float(constraints.max_wake_loss_pct) + 1e-9):
                            xs, ys = zip(*repair['layout_xy_m'])
                            lats_r, lons_r = _xy_to_latlon(list(xs), list(ys), site_lat0, site_lon0)
                            geometry['layout_xy_m'] = repair['layout_xy_m']
                            geometry['layout_latlon'] = [[la, lo] for la, lo in zip(lats_r, lons_r)]
                            geometry['sim'] = repaired_sim
                            wake_loss_pct = repaired_wake
                            noise_eval = repair['evaluation']
                            noise_ok = True
                            repaired_for.append('noise')
            # Shadow flicker es el chequeo más caro del embudo (barrido
            # anual de posición solar): solo se paga si estela Y ruido ya
            # pasaron (incluyendo, si aplicó, tras la reparación de ruido).
            flicker_eval = None
            flicker_ok = True
            if (receptor_points_xy is not None and constraints.shadow_flicker_limit_hours_year
                    and wake_loss_ok and noise_ok):
                flicker_eval = evaluate_shadow_flicker_constraint(
                    geometry['layout_xy_m'], turbine, receptor_points_xy, site_lat0, site_lon0,
                    constraints.shadow_flicker_limit_hours_year)
                flicker_ok = flicker_eval['ok']
                if not flicker_ok:
                    # Mismo principio que la reparación de ruido: flicker_eval
                    # (y noise_eval, si se revalida) solo se actualizan si el
                    # movimiento se ADOPTA -- nunca reportar la evaluación de
                    # una posición descartada.
                    repair = repair_shadow_flicker_violation(
                        geometry['layout_xy_m'], turbine, receptor_points_xy, site_lat0, site_lon0,
                        constraints.shadow_flicker_limit_hours_year,
                        position_allowed_fn=position_allowed_fn, min_spacing_m=min_spacing_m,
                        max_steps=_AUTO_REPAIR_MAX_STEPS)
                    if repair['repaired']:
                        repaired_sim = evaluate_layout(repair['layout_xy_m'])
                        repaired_wake = float(repaired_sim.get('wake_loss_pct') or 0.0)
                        repaired_noise_ok = True
                        repaired_noise_eval = noise_eval
                        if constraints.noise_limit_dba:
                            repaired_noise_eval = evaluate_noise_constraint(
                                repair['layout_xy_m'], turbine, receptor_points_xy,
                                constraints.noise_limit_dba)
                            repaired_noise_ok = repaired_noise_eval['ok']
                        if (not repaired_sim.get('error')
                                and repaired_wake <= float(constraints.max_wake_loss_pct) + 1e-9
                                and repaired_noise_ok):
                            xs, ys = zip(*repair['layout_xy_m'])
                            lats_r, lons_r = _xy_to_latlon(list(xs), list(ys), site_lat0, site_lon0)
                            geometry['layout_xy_m'] = repair['layout_xy_m']
                            geometry['layout_latlon'] = [[la, lo] for la, lo in zip(lats_r, lons_r)]
                            geometry['sim'] = repaired_sim
                            wake_loss_pct = repaired_wake
                            noise_eval = repaired_noise_eval
                            flicker_eval = repair['evaluation']
                            flicker_ok = True
                            repaired_for.append('shadow_flicker')
            feasible = wake_loss_ok and noise_ok and flicker_ok
            n_has_feasible = n_has_feasible or feasible
            reasons = []
            if not wake_loss_ok:
                reasons.append('wake_loss_excede_limite')
            if not noise_ok:
                reasons.append('ruido_excede_limite')
            if not flicker_ok:
                reasons.append('shadow_flicker_excede_limite')
            pool.append({
                'source_method': source_method,
                'n_turbines': n,
                'is_primary': geometry['is_primary'],
                'n_layouts_evaluated': geometry['n_layouts_evaluated'],
                'layout_xy_m': geometry['layout_xy_m'],
                'layout_latlon': geometry['layout_latlon'],
                'layout_config': geometry['layout_config'],
                'min_sep_m': min_sep_m_val,
                'area_km2': area_km2_val,
                'sim': geometry['sim'],
                'wake_loss_pct': wake_loss_pct,
                'noise_evaluation': noise_eval,
                'shadow_flicker_evaluation': flicker_eval,
                'repaired_for': repaired_for,
                'feasibility': {
                    'wake_loss_ok': wake_loss_ok,
                    'slope_ok': True, 'noise_ok': noise_ok, 'shadow_flicker_ok': flicker_ok,
                    'feasible': feasible,
                    'infeasibility_reasons': reasons,
                },
            })
        return n_has_feasible

    for family, stagger in FAMILY_STAGGER.items():
        family_kwargs = {**common_kwargs, 'stagger_options': (stagger,)}
        try:
            capacity = optimize_layout_structured(polygon, turbine, 0, **family_kwargs)
        except ValueError:
            # Esta familia no cabe en absoluto con las restricciones dadas
            # (p. ej. la zona de receptores la deja sin espacio) -- no
            # bloquea a las otras familias, se reporta con capacidad 0.
            max_n_fit_by_family[family] = 0
            max_n_technical_by_family[family] = 0
            n_range_explored_by_family[family] = [0, 0]
            continue
        physical_max_n = int(capacity.get('max_n_fit') or capacity.get('n_placed') or 0)
        max_n_fit_by_family[family] = physical_max_n
        max_n = physical_max_n
        if grid_limit_mw and grid_limit_mw > 0 and turbine.rated_power_kw:
            max_n = min(max_n, int(math.floor(grid_limit_mw * 1000.0 / turbine.rated_power_kw)))
        max_n_technical_by_family[family] = max_n
        auto_stop = n_range is None
        if auto_stop:
            n_min, n_max = 1, max_n
        else:
            n_min, n_max = max(1, int(n_range[0])), min(int(n_range[1]), max_n)

        first_feasible_n: Optional[int] = None
        n_seen: List[int] = []
        for n in range(n_max, n_min - 1, -1):
            if auto_stop and first_feasible_n is not None and (first_feasible_n - n) >= n_window:
                break
            try:
                result = optimize_layout_structured(
                    polygon, turbine, n, layout_evaluator=evaluate_layout,
                    max_wake_loss_pct=float(constraints.max_wake_loss_pct),
                    candidate_limit=candidate_limit_per_n, **family_kwargs)
            except ValueError:
                continue
            if int(result.get('n_placed') or 0) != n:
                continue
            primary_sim = result.get('constraint_evaluation')
            if primary_sim is None or primary_sim.get('error'):
                continue
            n_seen.append(n)
            geometries = [{
                'layout_xy_m': result['layout_xy_m'], 'layout_latlon': result['layout_latlon'],
                'layout_config': {**result['layout_config'], 'family': family}, 'sim': primary_sim,
                'is_primary': True, 'n_layouts_evaluated': result.get('n_layouts_evaluated'),
            }]
            seen_signatures = {_layout_signature(result['layout_xy_m'])}
            for candidate in result.get('candidate_layouts', []):
                signature = _layout_signature(candidate['layout_xy_m'])
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                sim = evaluate_layout(candidate['layout_xy_m'])
                if sim.get('error'):
                    continue
                geometries.append({
                    'layout_xy_m': candidate['layout_xy_m'],
                    'layout_latlon': candidate['layout_latlon'],
                    'layout_config': {**candidate['layout_config'], 'family': family}, 'sim': sim,
                    'is_primary': False, 'n_layouts_evaluated': None,
                })
            n_has_feasible = process_geometries_for_n(
                n, geometries, result['min_sep_m'], result['area_km2'], 'grid_structured')
            if n_has_feasible and first_feasible_n is None:
                first_feasible_n = n
        n_range_explored_by_family[family] = [min(n_seen), max_n] if n_seen else [max_n, max_n]

    # Tercera familia: crecimiento greedy sembrado en el vértice más a
    # barlovento (core.farm.optimize_layout_greedy_vertex). A diferencia de
    # las dos familias estructuradas, una sola pasada de crecimiento ya
    # genera una secuencia anidada válida para TODO N de 1 a su capacidad
    # física -- no hace falta repetir la búsqueda por N (ver docstring de
    # optimize_layout_greedy_vertex), así que esta familia es más barata que
    # las otras dos pese a agregar una tercera vía de comparación.
    try:
        greedy = optimize_layout_greedy_vertex(
            polygon, turbine, ws=ws, wd=wd, wind_grid=wind_grid,
            fallback_wind_ms=fallback_wind_ms, min_spacing_d=min_spacing_d,
            k_wake=k_wake, ct=ct, exclusion_polygons=exclusions,
            boundary_setback_m=boundary_setback_m, wake_model=wake_model,
            max_slope_deg=constraints.max_slope_deg)
    except ValueError:
        max_n_fit_by_family[GREEDY_FAMILY] = 0
        max_n_technical_by_family[GREEDY_FAMILY] = 0
        n_range_explored_by_family[GREEDY_FAMILY] = [0, 0]
    else:
        physical_max_n = int(greedy['max_n_fit'])
        max_n_fit_by_family[GREEDY_FAMILY] = physical_max_n
        max_n = physical_max_n
        if grid_limit_mw and grid_limit_mw > 0 and turbine.rated_power_kw:
            max_n = min(max_n, int(math.floor(grid_limit_mw * 1000.0 / turbine.rated_power_kw)))
        max_n_technical_by_family[GREEDY_FAMILY] = max_n
        auto_stop = n_range is None
        if auto_stop:
            n_min, n_max = 1, max_n
        else:
            n_min, n_max = max(1, int(n_range[0])), min(int(n_range[1]), max_n)

        greedy_layout_config_base = {
            'dominant_dir_deg': greedy['dominant_dir_deg'],
            'dominant_energy_dir_deg': greedy['dominant_dir_deg'],
            'direction_convention': 'meteorological_from_north_clockwise_centered_sectors',
            'seed_vertex_latlon': greedy['seed_vertex_latlon'],
            'layout_type': 'greedy',
            'n_configs_tried': 1,
        }
        first_feasible_n = None
        n_seen = []
        for n in range(n_max, n_min - 1, -1):
            if auto_stop and first_feasible_n is not None and (first_feasible_n - n) >= n_window:
                break
            layout_xy = greedy['sequence_xy_m'][:n]
            layout_ll = greedy['sequence_latlon'][:n]
            primary_sim = evaluate_layout(layout_xy)
            if primary_sim.get('error'):
                continue
            n_seen.append(n)
            geometries = [{
                'layout_xy_m': layout_xy, 'layout_latlon': layout_ll,
                'layout_config': {**greedy_layout_config_base, 'family': GREEDY_FAMILY},
                'sim': primary_sim, 'is_primary': True, 'n_layouts_evaluated': 1,
            }]
            n_has_feasible = process_geometries_for_n(
                n, geometries, greedy['min_sep_m'], greedy['area_km2'], 'greedy_vertex')
            if n_has_feasible and first_feasible_n is None:
                first_feasible_n = n
        n_range_explored_by_family[GREEDY_FAMILY] = [min(n_seen), max_n] if n_seen else [max_n, max_n]

    physical_max_n = max(max_n_fit_by_family.values(), default=0)
    if physical_max_n <= 0:
        # Ninguna familia cupo en absoluto -- no es "esta familia falló, la
        # otra sigue" (eso ya se maneja por familia arriba, sin propagar el
        # ValueError individual): es que el polígono/restricciones no
        # admiten ningún layout, sin importar el patrón. Mismo mensaje que
        # optimize_layout_structured para el caso equivalente de una sola
        # familia, para no romper el contrato de la función.
        raise ValueError(
            'No se encontró ninguna posición factible con las separaciones y '
            'retiros indicados.')
    max_n_technical = max(max_n_technical_by_family.values(), default=0)
    n_min_explored = min((c['n_turbines'] for c in pool), default=max_n_technical)
    n_max_explored = max((c['n_turbines'] for c in pool), default=max_n_technical)
    return {
        'pool': pool,
        'max_n_fit': physical_max_n,
        'max_n_fit_by_family': max_n_fit_by_family,
        'max_n_technical': max_n_technical,
        'max_n_technical_by_family': max_n_technical_by_family,
        'n_range_explored': [n_min_explored, n_max_explored],
        'n_range_explored_by_family': n_range_explored_by_family,
    }


_REASON_BY_FEASIBILITY_KEY = {
    'wake_loss_ok': 'wake_loss_excede_limite',
    'noise_ok': 'ruido_excede_limite',
    'shadow_flicker_ok': 'shadow_flicker_excede_limite',
    'slope_ok': 'pendiente_excede_limite',
}


def select_technology_champion(
        pool: List[Dict], rule: str = 'max_van_feasible',
        value_key: str = 'van_usd',
        tie_breakers: Tuple[str, ...] = ('wake_loss_pct',),
        require_feasibility_keys: Tuple[str, ...] = (
            'wake_loss_ok', 'noise_ok', 'shadow_flicker_ok', 'slope_ok')) -> Dict:
    """Elige un único layout representante de la tecnología, entre los
    candidatos del pool que cumplen `require_feasibility_keys`.

    `require_feasibility_keys` permite pedir DOS lecturas distintas del
    mismo pool sin recalcular nada, para el flujo de decisión de ruido/
    shadow flicker (discutido con el usuario): con
    `('wake_loss_ok',)` se obtiene el campeón "estela + VAN" (ignora ruido/
    flicker -- la selección primaria que pide el flujo); con el default
    completo se obtiene el campeón que además cumple ruido/flicker/
    pendiente, útil como "alternativa que mitiga" cuando el primero no es
    ambientalmente viable. Ver core.farm_mcda.analyze_park_layouts, que
    calcula ambos a partir del mismo pool y expone la diferencia.

    Regla ('max_van_feasible'): el campeón es el de mayor VAN neto (ingresos
    menos CAPEX/OPEX/desmantelamiento, descontados sobre la vida del
    proyecto) entre los candidatos factibles -- es decir, "de las
    configuraciones que ESTA tecnología puede construir aquí sin violar
    ninguna restricción, ¿cuál genera más dinero neto?". El llamador debe
    poblar `van_usd` en cada candidato del pool antes de invocar esta
    función (core.farm_mcda calcula economía completa -- CAPEX, OPEX,
    cashflow -- por candidato; ver _economics()).

    Esto NO es un mini-TOPSIS ni duplica el paso 7 (AHP+TOPSIS) del
    framework de tesis: aquí se maximiza UN solo número (VAN) dentro del
    espacio de configuraciones de UNA sola tecnología -- un argmax de una
    variable, no una comparación multicriterio ponderada. TOPSIS sigue
    siendo indispensable después, porque compara los campeones de
    tecnologías DISTINTAS usando VARIOS criterios a la vez (VAN, LCOE,
    CAPEX/kW, Cp efectivo, factor de capacidad, horas cerca de Cp máximo...)
    -- una comparación que un solo número no puede resolver. Preferir AEP en
    vez de VAN aquí sería peor: maximizar energía sin mirar el costo puede
    escoger un N con muchas turbinas marginales (CAPEX/colector crecientes,
    estela creciente) que un inversionista racional no elegiría -- literatura
    de optimización de layout (p. ej. funciones de costo tipo Mosetti et al.
    1994) también prefiere objetivos económicos sobre AEP puro por esta
    razón.

    `value_key` es configurable (no solo VAN) para permitir análisis de
    sensibilidad de esta propia regla -- p. ej. comparar el campeón elegido
    por VAN contra el que elegiría AEP puro, como evidencia de robustez del
    método en la tesis -- y para las pruebas unitarias que no necesitan
    correr el modelo económico completo. Si `value_key` no está en el nivel
    superior del candidato, se busca dentro de candidate['sim'] (p. ej.
    'aep_mwh').
    """
    rejected_by_reason: Counter = Counter()
    feasible = []
    for candidate in pool:
        flags = candidate['feasibility']
        if all(flags.get(key, True) for key in require_feasibility_keys):
            feasible.append(candidate)
        else:
            for key in require_feasibility_keys:
                if not flags.get(key, True):
                    rejected_by_reason[_REASON_BY_FEASIBILITY_KEY.get(key, key)] += 1
    if not feasible:
        return {'champion': None, 'pool_size': len(pool), 'n_feasible': 0,
                'rejected_by_reason': dict(rejected_by_reason), 'rule': rule}

    def _value(candidate):
        if candidate.get(value_key) is not None:
            return float(candidate[value_key])
        return float(candidate.get('sim', {}).get(value_key) or 0.0)

    def sort_key(candidate):
        tie_values = tuple(float(candidate.get(key, 0.0) or 0.0) for key in tie_breakers)
        return (-_value(candidate),) + tie_values

    champion = min(feasible, key=sort_key)
    return {'champion': champion, 'pool_size': len(pool), 'n_feasible': len(feasible),
            'rejected_by_reason': dict(rejected_by_reason), 'rule': rule}

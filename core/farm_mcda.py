"""Farm-layout alternatives for scientific MCDA pre-feasibility studies."""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

from core.project_economics import (project_cashflows, irr_bisection as _irr, DEFAULT_DEGRADATION_PCT,
                                    DEFAULT_DECOMMISSIONING_PCT)
from core.cost_model import CostModel
from core.layout_refinement import build_energy_context
from core.layout_generation import (
    FAMILY_STAGGER,
    GREEDY_FAMILY,
    LayoutConstraints,
    generate_feasible_layouts_grid,
    select_technology_champion,
)
from core.energy import PowerCurve, compute_site_cp_fit, extrapolate_wind_hellmann
from core.farm import (
    TECHNICAL_MIN_SPACING_D,
    _latlon_to_xy,
    gaussian_min_valid_distance_d,
    simulate_wind_farm,
)
from core.multicriteria import rank_layout_alternatives
from core.historical_resource import prepare_historical_wind, historical_layout_simulation
from core.energy import air_density


def effective_park_area(polygon, exclusions=None):
    """Common land area: polygon minus union of permanent exclusions.

    Use the layout's local projection. Overlapping exclusions count once;
    portions outside the property do not reduce its area. Rotor setbacks
    affect placement, not this common comparison denominator.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    lat0 = float(np.mean([p['lat'] for p in polygon]))
    lon0 = float(np.mean([p['lon'] for p in polygon]))

    def shape(points):
        if len(points) < 3:
            raise ValueError('Cada polígono de área requiere al menos tres vértices.')
        xs, ys = _latlon_to_xy([p['lat'] for p in points],
                              [p['lon'] for p in points], lat0, lon0)
        geom = Polygon(zip(xs, ys))
        if not geom.is_valid or geom.area <= 0:
            raise ValueError('Polígono inválido para calcular el área efectiva.')
        return geom

    land = shape(polygon)
    excluded = unary_union([shape(points) for points in (exclusions or [])])
    usable = land.difference(excluded)
    if usable.area <= 0:
        raise ValueError('Las exclusiones no dejan área efectiva disponible.')
    return {
        'gross_area_km2': land.area / 1e6,
        'excluded_area_km2': (land.area - usable.area) / 1e6,
        'effective_area_km2': usable.area / 1e6,
        'method': 'property_minus_union_of_permanent_exclusions',
    }


def add_effective_area_metrics(alternatives, polygon, exclusions=None):
    area = effective_park_area(polygon, exclusions)
    rows = []
    for alternative in alternatives:
        row = dict(alternative, effective_area_km2=area['effective_area_km2'],
                   area_provenance=area)
        for source, target in [('aep_p90_mwh', 'aep_p90_mwh_per_effective_km2'),
                               ('van_usd', 'van_usd_per_effective_km2')]:
            value = row.get(source)
            row[target] = (float(value) / area['effective_area_km2']
                           if value is not None and np.isfinite(float(value)) else None)
        rows.append(row)
    return rows


def _joint_synthetic_wind(sites: List[Dict], n_hours: int = 8760):
    mean_speed = float(np.mean([float(s.get('v_mean') or 0.0) for s in sites]))
    weibull_k = float(np.mean([float(s.get('weibull_K') or 2.0) for s in sites]))
    rho = float(np.mean([float(s.get('rho_mean') or 1.225) for s in sites]))
    freq_rows = [s.get('sectorial', {}).get('frequency_pct') for s in sites]
    freq_rows = [row for row in freq_rows if isinstance(row, list) and len(row) > 0]
    n_sectors = len(freq_rows[0]) if freq_rows else 16
    frequencies = np.mean(np.asarray(freq_rows, dtype=float), axis=0) if freq_rows else np.ones(n_sectors)
    frequencies = np.maximum(frequencies, 0.0)
    frequencies = frequencies / frequencies.sum() if frequencies.sum() else np.ones(n_sectors) / n_sectors
    k = max(0.45, weibull_k)
    scale = mean_speed / math.gamma(1.0 + 1.0 / k) if mean_speed > 0 else 0.0
    probs = (np.arange(n_hours, dtype=float) + 0.5) / n_hours
    ws = scale * np.power(-np.log1p(-probs), 1.0 / k)
    counts = np.floor(frequencies * n_hours).astype(int)
    counts[np.argmax(frequencies)] += n_hours - int(counts.sum())
    directions = np.concatenate([
        np.full(int(count), idx * 360.0 / n_sectors)
        for idx, count in enumerate(counts) if count > 0
    ])
    # Deterministic permutation prevents an artificial speed-direction trend.
    rng = np.random.default_rng(6140015)
    rng.shuffle(directions)
    return ws, directions[:n_hours], mean_speed, weibull_k, rho


def _collection_length_m(layout_xy: List[List[float]]) -> float:
    """Euclidean minimum-spanning-tree length as collection-system proxy."""
    if len(layout_xy) < 2:
        return 0.0
    points = np.asarray(layout_xy, dtype=float)
    connected = {0}
    remaining = set(range(1, len(points)))
    length = 0.0
    while remaining:
        distance, chosen = min(
            (float(np.linalg.norm(points[i] - points[j])), j)
            for i in connected for j in remaining
        )
        length += distance
        connected.add(chosen)
        remaining.remove(chosen)
    return length


def _economics(turbine: PowerCurve, n_turbines: int, aep_p50_mwh: float,
               cost_model: CostModel, discount_rate: float, project_years: int,
               price_usd_kwh: float, collection_length_m: float,
               cable_usd_per_m: float = 75.0, degradation_pct: float = DEFAULT_DEGRADATION_PCT,
               decommissioning_pct: float = DEFAULT_DECOMMISSIONING_PCT,
               revenue_year1_override: Optional[float] = None):
    cap = cost_model.capex_breakdown(turbine.rated_power_kw, n_turbines)
    cable_cost = max(0.0, collection_length_m * cable_usd_per_m)
    foundation_cost = cost_model.foundation_cost_usd(
        turbine.rotor_diameter_m, turbine.hub_height_m, n_turbines)
    capex = float(cap['total_capex_usd']) + cable_cost + foundation_cost
    annual_opex = cost_model.opex_annual_usd(capex)
    # revenue_year1_override: ingreso real del año 1 cruzando generación×precio
    # hora por hora (core.farm.chronological_revenue), en vez de
    # aep_p50_mwh×1000×price_usd_kwh. Solo el año 1 — los años 2-25 siguen
    # degradando/escalando desde ese punto de partida real, no desde el
    # promedio plano (ver project_cashflows.year1_revenue_override).
    lifecycle = project_cashflows(
        capex, annual_opex, aep_p50_mwh * 1000.0, price_usd_kwh,
        discount_rate, project_years, degradation_pct=degradation_pct,
        decommissioning_pct=decommissioning_pct,
        year1_revenue_override=revenue_year1_override)
    decommissioning = lifecycle['decommissioning_usd']
    cashflows = lifecycle['cashflows']
    cashflow_table = lifecycle['cashflow_table']
    van = lifecycle['van_usd']
    pv_costs, pv_energy = lifecycle['pv_costs'], lifecycle['pv_energy_kwh']
    pv_revenue = lifecycle['pv_revenue']
    pv_opex = lifecycle['opex_present_value_usd']
    pv_decommissioning = lifecycle['decommissioning_present_value_usd']
    payback = lifecycle['payback_years']
    discounted_payback = lifecycle['discounted_payback_years']
    installed_kw = float(turbine.rated_power_kw) * int(n_turbines)
    return {
        'capex_usd': capex,
        'base_capex_usd': float(cap['total_capex_usd']),
        'capex_usd_per_installed_kw': capex / installed_kw if installed_kw > 0 else None,
        'opex_usd_anual': annual_opex,
        'opex_usd_per_installed_kw_year': annual_opex / installed_kw if installed_kw > 0 else None,
        'opex_lifetime_usd': annual_opex * project_years,
        'opex_present_value_usd': pv_opex,
        'van_usd': van,
        'van_usd_per_installed_mw': (
            van / (installed_kw / 1000.0) if installed_kw > 0 else None
        ),
        'benefit_cost_ratio': pv_revenue / pv_costs if pv_costs > 0 else None,
        'roi': van / capex if capex > 0 else None,
        'irr': _irr(cashflows),
        'payback_years': payback,
        'discounted_payback_years': discounted_payback,
        'lcoe_usd_kwh': pv_costs / pv_energy if pv_energy > 0 else None,
        'collection_cable_cost_usd': cable_cost,
        'foundation_cost_usd': foundation_cost,
        'decommissioning_usd': decommissioning,
        'decommissioning_present_value_usd': pv_decommissioning,
        'annual_revenue_year1_usd': aep_p50_mwh * 1000.0 * price_usd_kwh,
        'cashflow_table': cashflow_table,
        'capex_breakdown': cap,
        'economic_formulas': {
            'turbines_total': 'rated_power_kw × n_turbines × turbine_usd_per_kw',
            'base_capex': 'turbines_total_usd ÷ turbines_pct',
            'capex': 'base_capex_usd + collection_cable_cost_usd + foundation_cost_usd',
            'annual_opex': 'capex_usd × opex_pct_capex',
            'annual_revenue': 'aep_p50_mwh × 1000 × price_usd_kwh',
            'npv': 'Σ net_cashflow_t ÷ (1 + discount_rate)^t, incluyendo CAPEX en t=0',
            'benefit_cost_ratio': 'PV(ingresos) ÷ PV(CAPEX + OPEX + desmantelamiento)',
            'payback': 'año anterior + saldo pendiente ÷ flujo neto del año',
            'discounted_payback': 'primer cruce de CAPEX + Σ flujo_neto_t ÷ (1 + tasa)^t',
            'lcoe': 'PV(CAPEX + OPEX + desmantelamiento) ÷ PV(energía neta)',
        },
    }


def generate_park_layouts(sites: List[Dict], polygon: List[Dict], turbines_folder: str,
                         turbine_filenames: List[str], wind_grid=None,
                         exclusions=None, boundary_setback_m: float = 0.0,
                         min_spacing_d: float = 3.0,
                         s_para_range=(3, 4, 5, 6, 7, 8, 9, 10),
                         s_perp_range=(3, 4, 5, 6),
                         max_wake_loss_pct: float = 5.0,
                         wake_model: str = 'gaussian', k_wake: float = 0.04,
                         ct: float = 0.8, availability: float = 0.97,
                         electrical_losses: float = 0.03,
                         curtailment_loss: float = 0.0,
                         environmental_loss: float = 0.0,
                         discount_rate: float = 0.08, project_years: int = None,
                         price_usd_kwh: float = None, demand_mwh: float = 0.0,
                         commercialization_mode: str = 'total_sale',
                         grid_limit_mw: Optional[float] = None,
                         target_height_m: Optional[float] = None,
                         cost_model: Optional[CostModel] = None,
                         cost_model_origin: Optional[str] = None,
                         uncertainty_components_pct: Optional[Dict[str, float]] = None,
                         n_range: Optional[Tuple[int, int]] = None,
                         n_range_by_turbine: Optional[Dict[str, Tuple[int, int]]] = None,
                         candidate_limit_per_n: int = 0,
                         historical_wind: Optional[Dict] = None,
                         price_hourly_usd_kwh: Optional[List[float]] = None,
                         receptor_points: Optional[List[Dict]] = None,
                         noise_limit_dba: Optional[Dict[str, float]] = None,
                         shadow_flicker_limit_hours_year: Optional[float] = None,
                         max_slope_deg: Optional[float] = None,
                         row_sep_reference_d: Optional[float] = None,
                         col_sep_reference_d: Optional[float] = None,
                         include_environmental: bool = False,
                         local_refinement_max_evals: int = 60):
    if not sites:
        raise ValueError('No hay recurso eólico interpolado para optimizar el parque.')
    if len(polygon or []) < 3:
        raise ValueError('El parque requiere un polígono con al menos tres vértices.')
    if wake_model not in {'gaussian', 'jensen'}:
        raise ValueError('wake_model debe ser gaussian o jensen.')
    if min_spacing_d < TECHNICAL_MIN_SPACING_D:
        raise ValueError(
            f'La separación mínima no puede ser menor que {TECHNICAL_MIN_SPACING_D}D '
            '(piso técnico: validez de Jensen en campo cercano y cargas por '
            'turbulencia IEC 61400-1).')
    if not 0.0 <= float(max_wake_loss_pct) < 100.0:
        raise ValueError('El límite de pérdida por estela debe estar en [0, 100).')
    # s_para_range/s_perp_range ahora incluyen valores por debajo de la
    # referencia del usuario (hasta TECHNICAL_MIN_SPACING_D), así que
    # s_para_range[0]/s_perp_range[0] ya no son la referencia que el usuario
    # escribió -- se guarda aparte para que la UI pueda mostrarla/compararla
    # sin tener que reconstruirla del rango explorado. Sin valor explícito,
    # se asume el mínimo del rango (compatibilidad con llamadores directos).
    row_sep_reference_d = (float(row_sep_reference_d) if row_sep_reference_d is not None
                            else float(min(s_para_range)))
    col_sep_reference_d = (float(col_sep_reference_d) if col_sep_reference_d is not None
                            else float(min(s_perp_range)))
    if project_years is None or price_usd_kwh is None:
        raise ValueError(
            'project_years y price_usd_kwh deben venir de la configuración del proyecto.')
    project_years = int(project_years)
    price_usd_kwh = float(price_usd_kwh)
    if not 5 <= project_years <= 40 or discount_rate <= -1.0 or price_usd_kwh < 0:
        raise ValueError('Horizonte, tasa de descuento o precio de energía inválidos.')
    for name, value in {
        'availability': availability, 'electrical_losses': electrical_losses,
        'curtailment_loss': curtailment_loss, 'environmental_loss': environmental_loss,
    }.items():
        if not 0.0 <= float(value) < 1.0:
            raise ValueError(f'{name} debe estar en el intervalo [0, 1).')
    ws, wd, mean_speed, weibull_k, rho = _joint_synthetic_wind(sites) if historical_wind is None else (None, None, None, None, None)
    lat0 = float(np.mean([point['lat'] for point in polygon]))
    lon0 = float(np.mean([point['lon'] for point in polygon]))
    if cost_model_origin is None:
        cost_model_origin = ('request' if cost_model is not None
                             else 'irena_onshore_2024_global_weighted_average')
    cost_model = cost_model or CostModel.from_design_benchmark()
    alternatives = []
    errors = []
    unique_filenames = []
    seen_models = set()
    for raw_filename in turbine_filenames:
        filename = os.path.basename(str(raw_filename))
        model_key = filename.casefold()
        if model_key not in seen_models:
            seen_models.add(model_key)
            unique_filenames.append(filename)
    for filename in unique_filenames:
        try:
            turbine = PowerCurve.from_file(os.path.join(turbines_folder, os.path.basename(filename)))
            if target_height_m:
                turbine.hub_height_m = float(target_height_m)
            resource = None
            if historical_wind is not None:
                resource = prepare_historical_wind(historical_wind, turbine.hub_height_m)
                ws, wd, mean_speed = resource['ws'], resource['wd'], resource['mean']
                rho = float(np.mean([s.get('rho_mean') or 1.225 for s in sites]))
                if resource.get('temperature_c') is not None and resource.get('pressure_pa') is not None:
                    densities = air_density(resource['temperature_c'], resource['pressure_pa'])
                    good_density = np.isfinite(densities) & (densities > 0)
                    if good_density.any():
                        rho = float(np.mean(densities[good_density]))
                resource['provenance']['rho_used_kg_m3'] = rho
            setback = max(float(boundary_setback_m), turbine.rotor_diameter_m / 2.0)
            def evaluate_layout(points):
                options = dict(k_wake=k_wake, ct=ct, n_sectors=36,
                    availability=availability, electrical_losses=electrical_losses,
                    wake_model=wake_model, rho_override=rho)
                if resource is not None:
                    return historical_layout_simulation(points, turbine, resource,
                        wind_grid, (lat0, lon0), price_hourly_usd_kwh=price_hourly_usd_kwh,
                        **options)
                result = simulate_wind_farm(points, turbine, ws, wd, **options)
                result['production_source'] = {'method': 'synthetic_weibull', 'source_id': None}
                return result
            # Paso 6 del framework de tesis (generación de layouts factibles)
            # separado del paso 7 (AHP+TOPSIS): se genera el pool completo de
            # candidatos (variando N, y dentro de cada N el patrón geométrico)
            # y select_technology_champion elige, con una regla explícita, un
            # único representante por tecnología -- en vez de detenerse en el
            # primer N decreciente que cumple el límite de estela.
            generation = generate_feasible_layouts_grid(
                polygon, turbine, ws=ws, wd=wd, wind_grid=wind_grid,
                fallback_wind_ms=mean_speed, rho=rho, exclusions=exclusions,
                boundary_setback_m=setback, min_spacing_d=min_spacing_d,
                s_para_range=s_para_range, s_perp_range=s_perp_range,
                wake_model=wake_model, k_wake=k_wake, ct=ct,
                evaluate_layout=evaluate_layout,
                constraints=LayoutConstraints(
                    max_wake_loss_pct=float(max_wake_loss_pct),
                    receptor_points=receptor_points,
                    noise_limit_dba=noise_limit_dba,
                    shadow_flicker_limit_hours_year=shadow_flicker_limit_hours_year,
                    max_slope_deg=(float(max_slope_deg) if max_slope_deg is not None else None),
                    include_environmental=include_environmental,
                ),
                grid_limit_mw=grid_limit_mw,
                # n_range_by_turbine tiene prioridad sobre n_range global --
                # lo usa el botón "usar esta alternativa" del diagnóstico
                # ambiental para forzar el N de la mitigación en ESTA
                # tecnología, sin restringir a las demás de la misma corrida.
                n_range=(n_range_by_turbine or {}).get(filename, n_range),
                # candidate_limit_per_n=0 por defecto: en esta fase solo se
                # varía N (la causa raíz del acoplamiento generación/TOPSIS).
                # Explorar además varios patrones geométricos por N
                # multiplica el costo (una simulación real de estela por
                # patrón); expuesto como parámetro para análisis puntuales o
                # para cuando el GA (Fase 5) alimente el mismo pool con
                # geometrías genuinamente distintas.
                candidate_limit_per_n=candidate_limit_per_n,
                local_refinement_max_evals=local_refinement_max_evals,
            )
            physical_max_n = generation['max_n_fit']
            max_n = generation['max_n_technical']
            if max_n <= 0:
                errors.append({'turbine': filename, 'reason': 'sin_layout_factible'})
                continue
            # El campeón se elige por VAN neto, no por AEP: entre las
            # configuraciones factibles de ESTA tecnología, la que más
            # dinero neto genera (ver select_technology_champion). Eso
            # exige economía completa (CAPEX/OPEX/cashflow) por candidato,
            # no solo para el ganador -- barato frente al costo ya pagado
            # en la simulación de estela de cada candidato.
            other_survival = (1.0 - curtailment_loss) * (1.0 - environmental_loss)
            for candidate in generation['pool']:
                # Economía calculada para todo candidato que cumple estela,
                # sin exigir además ruido/flicker -- necesario para poder
                # elegir el campeón "estela + VAN" (ver más abajo) y, si no
                # es ambientalmente viable, seguir teniendo VAN disponible
                # para buscar una alternativa que sí lo sea, del mismo pool.
                if not candidate['feasibility']['wake_loss_ok']:
                    continue
                candidate_sim = candidate['sim']
                candidate_aep_p50 = float(candidate_sim['aep_mwh']) * other_survival
                candidate_chron = candidate_sim.get('chronological_revenue')
                candidate_revenue_override = (
                    candidate_chron['revenue_usd_year1'] * other_survival
                    if candidate_chron and candidate_chron.get('revenue_usd_year1') is not None else None)
                candidate_collection_length = _collection_length_m(candidate['layout_xy_m'])
                candidate_econ = _economics(
                    turbine, candidate['n_turbines'], candidate_aep_p50, cost_model,
                    discount_rate, project_years, price_usd_kwh, candidate_collection_length,
                    revenue_year1_override=candidate_revenue_override,
                )
                candidate['van_usd'] = candidate_econ['van_usd']
                candidate['_precomputed'] = {
                    'aep_p50': candidate_aep_p50, 'collection_length': candidate_collection_length,
                    'econ': candidate_econ, 'revenue_year1_override': candidate_revenue_override,
                }
            # Flujo de decisión de ruido/shadow flicker (acordado con el
            # usuario): la selección PRIMARIA es "estela + VAN" -- ignora
            # ruido/flicker/pendiente a propósito, igual que se generaron
            # las configuraciones respetando solo el límite de estela. El
            # resultado ambiental de ESE campeón se reporta después como
            # información, no como filtro previo -- y si no es viable, se
            # ofrece (del mismo pool, sin recalcular nada) la mejor
            # alternativa que sí cumple todo, para que el investigador
            # decida: mitigar (usar la alternativa) o aceptar el riesgo
            # (ignorar ruido/sombra para la decisión final).
            champion_result = select_technology_champion(
                generation['pool'], require_feasibility_keys=('wake_loss_ok',))
            champion = champion_result['champion']
            if champion is None:
                errors.append({
                    'turbine': filename,
                    'reason': 'sin_layout_que_cumpla_limite_estela',
                    'max_wake_loss_pct': float(max_wake_loss_pct),
                    'pool_size': champion_result['pool_size'],
                    'rejected_by_reason': champion_result['rejected_by_reason'],
                })
                continue
            # Auditoría por familia (paso 6 ahora corre Alineado, Tresbolillo
            # y Greedy-vértice explícitamente, no un barrido mezclado -- ver
            # generate_feasible_layouts_grid): aunque solo una familia gane
            # el campeonato de esta tecnología, la comparación entre las tres
            # queda trazable aquí, reusando select_technology_champion sobre
            # el sub-pool de cada familia -- sin recalcular nada.
            family_summary = {}
            for family in list(FAMILY_STAGGER) + [GREEDY_FAMILY]:
                family_pool = [c for c in generation['pool']
                              if c['layout_config'].get('family') == family]
                family_champion = select_technology_champion(
                    family_pool, require_feasibility_keys=('wake_loss_ok',))['champion']
                family_summary[family] = {
                    'max_n_fit': generation.get('max_n_fit_by_family', {}).get(family),
                    'n_turbines': family_champion['n_turbines'] if family_champion else None,
                    'van_usd': family_champion.get('van_usd') if family_champion else None,
                    'wake_loss_pct': family_champion['wake_loss_pct'] if family_champion else None,
                    'feasible': family_champion is not None,
                }
            environmental_ok = (champion['feasibility'].get('noise_ok', True)
                                and champion['feasibility'].get('shadow_flicker_ok', True))
            mitigation_alternative = None
            if not environmental_ok:
                mitigation_result = select_technology_champion(generation['pool'])
                mitigation_champion = mitigation_result['champion']
                if mitigation_champion is not None and mitigation_champion is not champion:
                    mitigation_alternative = {
                        'n_turbines': mitigation_champion['n_turbines'],
                        'van_usd': mitigation_champion.get('van_usd'),
                        'van_delta_usd': (
                            (mitigation_champion.get('van_usd') or 0.0) - (champion.get('van_usd') or 0.0)),
                        'wake_loss_pct': mitigation_champion['wake_loss_pct'],
                        'noise_evaluation': mitigation_champion.get('noise_evaluation'),
                        'shadow_flicker_evaluation': mitigation_champion.get('shadow_flicker_evaluation'),
                        'layout_latlon': mitigation_champion['layout_latlon'],
                    }
            selected_n = champion['n_turbines']
            layout = {
                'layout_latlon': champion['layout_latlon'],
                'layout_xy_m': champion['layout_xy_m'],
                'layout_config': champion['layout_config'],
                'min_sep_m': champion['min_sep_m'],
                'area_km2': champion['area_km2'],
            }
            sim = champion['sim']
            # wake_trials conserva la forma histórica (un registro por N,
            # de mayor a menor, terminando en el N elegido) para diagnóstico
            # y trazabilidad -- aunque el pool completo explora más allá del
            # campeón, aquí solo se reporta el tramo relevante a su elección.
            wake_trials = [
                {'n': c['n_turbines'], 'wake_loss_pct': c['wake_loss_pct'],
                 'n_layouts_evaluated': c.get('n_layouts_evaluated')}
                for c in generation['pool']
                if c.get('is_primary') and c['n_turbines'] >= selected_n
            ]
            precomputed = champion['_precomputed']
            aep_p50 = precomputed['aep_p50']
            collection_length = precomputed['collection_length']
            econ = precomputed['econ']
            revenue_year1_override = precomputed['revenue_year1_override']
            # Ingreso real (Fase 1/2): cruza generación×precio hora por hora
            # en vez de AEP×precio promedio (ver core.farm.chronological_revenue).
            # Solo activo si hay PML horario cargado en la sesión Y fuente
            # histórica con hora del año — si no, revenue_year1_override queda
            # None y _economics usa aep_p50×precio como siempre.
            chron = sim.get('chronological_revenue')
            uncertainty_components = dict(uncertainty_components_pct or {
                'resource': 6.0, **({'mcp': 4.0} if historical_wind is None or historical_wind.get('source_kind') == 'mcp' else {}), 'wake': 3.0,
                'power_curve': 2.0, 'losses': 2.0,
            })
            if any(float(value) < 0 for value in uncertainty_components.values()):
                raise ValueError('Las incertidumbres no pueden ser negativas.')
            uncertainty_components = {key: float(value) for key, value in uncertainty_components.items()}
            uncertainty_pct = math.sqrt(sum(value ** 2 for value in uncertainty_components.values()))
            aep_p90 = max(0.0, aep_p50 * (1.0 - 1.28155 * uncertainty_pct / 100.0))
            installed_mw = selected_n * turbine.rated_power_kw / 1000.0
            factor_capacity = aep_p50 / (installed_mw * 8760.0) if installed_mw > 0 else 0.0
            area_km2 = max(float(layout.get('area_km2') or 0.0), 1e-9)
            curve_points = list(turbine._raw_points)
            cp_ws = ws
            cp_dt_hours = 1.0
            cp_source = {
                'method': 'weibull_8760_fallback',
                'source_id': None,
                'rows': int(len(ws)),
                'measurement_height_m': float(turbine.hub_height_m),
                'analysis_height_m': float(turbine.hub_height_m),
            }
            if historical_wind and historical_wind.get('ws') is not None:
                historical_ws = np.asarray(historical_wind.get('ws'), dtype=float)
                if np.isfinite(historical_ws).any():
                    source_height = float(
                        historical_wind.get('measurement_height_m')
                        or turbine.hub_height_m
                    )
                    analysis_height = float(turbine.hub_height_m)
                    cp_ws = extrapolate_wind_hellmann(
                        historical_ws, source_height, analysis_height)
                    cp_dt_hours = float(historical_wind.get('dt_hours') or 1.0)
                    cp_source = {
                        'method': 'complete_historical_series',
                        'source_id': historical_wind.get('source_id'),
                        'source_kind': historical_wind.get('source_kind'),
                        'source_label': historical_wind.get('source_label'),
                        'rows': int(len(historical_ws)),
                        'measurement_height_m': source_height,
                        'analysis_height_m': analysis_height,
                        'dt_hours': cp_dt_hours,
                    }
            cp_fit = compute_site_cp_fit(
                cp_ws,
                [point[0] for point in curve_points],
                [point[1] for point in curve_points],
                turbine.rotor_diameter_m,
                rho=rho, dt_hours=cp_dt_hours, max_w=turbine.cut_out,
                near_cp_fraction=0.95,
            )
            cp_fit['site_fit_source'] = cp_source
            alternatives.append({
                'label': f'layout::{filename}::wake-optimal',
                'site': f'Layout seleccionado por VAN ({selected_n}/{physical_max_n} aeros)',
                'turbina': filename,
                'n_aerogeneradores': selected_n,
                'n_optimo_estela': selected_n,
                'max_n_fisico': physical_max_n,
                'max_n_tecnico': max_n,
                'reducido_por_estela': selected_n < max_n,
                'max_wake_loss_pct': float(max_wake_loss_pct),
                'wake_trials': wake_trials,
                'is_champion': True,
                'champion_selection': {
                    'rule': champion_result['rule'],
                    'pool_size': champion_result['pool_size'],
                    'n_feasible': champion_result['n_feasible'],
                    'rejected_by_reason': champion_result['rejected_by_reason'],
                    'n_range_explored': generation['n_range_explored'],
                    'family_summary': family_summary,
                },
                # Diagnóstico ambiental del campeón "estela + VAN": no filtra
                # nada por sí solo (ver comentario arriba de
                # select_technology_champion) -- reporta el efecto real de
                # ruido/shadow flicker y, si no es viable, la mejor
                # alternativa del mismo pool que sí cumple, para que el
                # investigador decida (mitigar vs. aceptar el riesgo
                # explícitamente).
                'environmental_feasibility': {
                    'ok': environmental_ok,
                    'noise_ok': champion['feasibility'].get('noise_ok', True),
                    'shadow_flicker_ok': champion['feasibility'].get('shadow_flicker_ok', True),
                    'noise_evaluation': champion.get('noise_evaluation'),
                    'shadow_flicker_evaluation': champion.get('shadow_flicker_evaluation'),
                },
                'mitigation_alternative': mitigation_alternative,
                'feasible': True,
                'layout_latlon': layout['layout_latlon'],
                'layout_xy_m': layout['layout_xy_m'],
                'layout_config': layout['layout_config'],
                'min_sep_m': layout['min_sep_m'],
                'area_km2': area_km2,
                'installed_mw': installed_mw,
                'aep_gross_mwh': sim['aep_gross_mwh'],
                'aep_mwh': aep_p50,
                'production_source': sim.get('production_source'),
                'chronological_revenue': chron,
                'turbine_production': sim.get('turbines', []),
                'record_energy_mwh_before_additional_losses': sim.get('record_energy_mwh'),
                'aep_p50_mwh': aep_p50,
                'aep_p90_mwh': aep_p90,
                'aep_p90_mwh_per_installed_mw': (
                    aep_p90 / installed_mw if installed_mw > 0 else None
                ),
                'aep_uncertainty_pct': uncertainty_pct,
                'uncertainty_components_pct': uncertainty_components,
                'wake_loss_pct': sim['wake_loss_pct'],
                'curtailment_loss_pct': curtailment_loss * 100.0,
                'environmental_loss_pct': environmental_loss * 100.0,
                'factor_capacidad': factor_capacity,
                **cp_fit,
                'collection_length_m': collection_length,
                'energy_density_mwh_km2': aep_p50 / area_km2,
                'civil_complexity_index': collection_length / max(selected_n, 1),
                'iec_suitability_score': 0.5,
                'quality_level': 'prefeasibility',
                'quality_warnings': ['Ct genérico; falta clase IEC y turbulencia extrema del fabricante'],
                'bankable_ready': False,
                'bankable_missing': ['ct_curve', 'iec_class', 'extreme_wind',
                                     'ambient_turbulence', 'certified_power_curve'],
                'wake_model': wake_model,
                'wake_model_validity': sim.get('wake_model_validity'),
                'turbine_specification': {
                    'rated_power_kw': float(turbine.rated_power_kw),
                    'rotor_diameter_m': float(turbine.rotor_diameter_m),
                    'hub_height_m': float(turbine.hub_height_m or 0.0),
                },
                'energy_context': build_energy_context(
                    sites, turbine, wake_model=wake_model, k_wake=k_wake, ct=ct,
                    availability=availability, electrical_losses=electrical_losses,
                    curtailment_loss=curtailment_loss, environmental_loss=environmental_loss,
                    historical_resource=resource, wind_grid=wind_grid, origin=(lat0, lon0), rho=rho),
                'layout_constraints': {
                    'polygon': polygon, 'exclusions': exclusions or [],
                    'boundary_setback_m': setback, 'min_spacing_d': min_spacing_d,
                    's_para_range': list(s_para_range), 's_perp_range': list(s_perp_range),
                    'row_sep_reference_d': row_sep_reference_d,
                    'col_sep_reference_d': col_sep_reference_d,
                    'max_wake_loss_pct': float(max_wake_loss_pct),
                    'max_slope_deg': max_slope_deg,
                    'grid_limit_mw': grid_limit_mw,
                    'receptor_points': receptor_points or [],
                    'noise_limit_dba': noise_limit_dba,
                    'shadow_flicker_limit_hours_year': shadow_flicker_limit_hours_year,
                },
                'aep_per_turbine_mwh': aep_p50 / selected_n,
                'mean_power_per_turbine_kw': aep_p50 / selected_n * 1000 / 8760,
                'energy_balance': {
                    'aep_gross_theoretical_mwh': sim['aep_gross_mwh'],
                    'aep_net_pre_wake_mwh': sim.get('aep_net_pre_wake_mwh'),
                    'aep_after_wake_operational_mwh': sim['aep_mwh'],
                    'aep_p50_after_all_losses_mwh': aep_p50,
                    'aep_p90_mwh': aep_p90,
                },
                'loss_assumptions': {
                    'wake_loss_pct': sim['wake_loss_pct'],
                    'availability_loss_pct': (1.0 - availability) * 100.0,
                    'electrical_loss_pct': electrical_losses * 100.0,
                    'curtailment_loss_pct': curtailment_loss * 100.0,
                    'environmental_loss_pct': environmental_loss * 100.0,
                    'loss_combination': 'multiplicativa, no suma aritmética',
                },
                'economic_assumptions': {
                    **cost_model.to_dict(),
                    'discount_rate': float(discount_rate),
                    'project_years': int(project_years),
                    'price_usd_kwh': float(price_usd_kwh),
                    'degradation_pct_per_year': DEFAULT_DEGRADATION_PCT,
                    'decommissioning_pct_capex': DEFAULT_DECOMMISSIONING_PCT,
                    'collection_cable_usd_per_m': 75.0,
                    'cost_model_origin': cost_model_origin,
                },
                **econ,
            })
        except Exception as exc:
            errors.append({'turbine': filename, 'reason': str(exc)})
    alternatives = add_effective_area_metrics(alternatives, polygon, exclusions)
    return {
        'analysis_kind': 'farm_layout_candidates_v1',
        'comparison_polygon': polygon,
        'comparison_exclusions': exclusions or [],
        'alternatives': alternatives,
        'n_layout_alternatives': len(alternatives),
        'n_requested_models': len(unique_filenames),
        'n_valid_models': len(alternatives),
        'wind_summary': {'mean_speed_ms': mean_speed, 'weibull_k': weibull_k, 'rho': rho},
        'assumptions': {
            'wake_model': wake_model, 'k_wake': k_wake, 'ct': ct,
            'wake_model_validity': {
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
            },
            'max_wake_loss_pct': float(max_wake_loss_pct),
            'min_spacing_d': float(min_spacing_d),
            's_para_range': list(s_para_range), 's_perp_range': list(s_perp_range),
            'availability': availability, 'electrical_losses': electrical_losses,
            'commercialization_mode': commercialization_mode,
            'count_policy': 'max_van_feasible_per_technology',
        },
        'methodology_references': [
            {'id': 'IEC-61400-1:2019', 'url': 'https://webstore.iec.ch/en/publication/26423'},
            {'id': 'IEC-61400-12-1:2022', 'url': 'https://webstore.iec.ch/en/publication/68499'},
            {'id': 'IEC-61400-15-1:2025', 'url': 'https://webstore.iec.ch/en/publication/29169'},
            {'id': 'Bastankhah-Porte-Agel-2014',
             'doi': '10.1016/j.renene.2014.01.002'},
            {'id': 'Hwang-Yoon-TOPSIS-1981',
             'doi': '10.1007/978-3-642-48318-9_3'},
            {'id': 'NREL-ATB-2024-Land-Based-Wind',
             'url': 'https://atb.nrel.gov/electricity/2024b/land-based_wind'},
            {'id': 'IRENA-Renewable-Power-Generation-Costs-2024',
             'url': ('https://www.irena.org/Publications/2025/Jun/'
                     'Renewable-Power-Generation-Costs-in-2024')},
        ],
        'errors': errors,
    }


def analyze_park_layouts(*args, weights=None, **kwargs):
    """Compatibility adapter for clients of the original combined analysis.

    The application uses generate_park_layouts followed by the independent
    /api/multicriteria/rank action. Generation never depends on TOPSIS weights.
    """
    result = generate_park_layouts(*args, **kwargs)
    result['analysis_kind'] = 'farm_layout_mcda_v2'
    result['ranking'] = rank_layout_alternatives(result['alternatives'], weights=weights)
    return result

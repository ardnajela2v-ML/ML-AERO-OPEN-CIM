"""Farm-layout alternatives for scientific MCDA pre-feasibility studies."""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional

import numpy as np

from core.cost_model import CostModel
from core.energy import PowerCurve
from core.farm import (
    gaussian_min_valid_distance_d,
    optimize_layout_structured,
    simulate_wind_farm,
)
from core.multicriteria import rank_layout_alternatives


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
        np.full(int(count), (idx + 0.5) * 360.0 / n_sectors)
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


def _irr(cashflows: Iterable[float]) -> Optional[float]:
    cashflows = list(float(v) for v in cashflows)
    if not cashflows or not (min(cashflows) < 0 < max(cashflows)):
        return None
    low, high = -0.99, 10.0
    def npv(rate):
        return sum(value / ((1.0 + rate) ** year) for year, value in enumerate(cashflows))
    if npv(low) * npv(high) > 0:
        return None
    for _ in range(100):
        mid = (low + high) / 2.0
        if npv(low) * npv(mid) <= 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2.0


def _economics(turbine: PowerCurve, n_turbines: int, aep_p50_mwh: float,
               cost_model: CostModel, discount_rate: float, project_years: int,
               price_usd_kwh: float, collection_length_m: float,
               cable_usd_per_m: float = 75.0, degradation_pct: float = 0.3,
               decommissioning_pct: float = 3.0):
    cap = cost_model.capex_breakdown(turbine.rated_power_kw, n_turbines)
    cable_cost = max(0.0, collection_length_m * cable_usd_per_m)
    capex = float(cap['total_capex_usd']) + cable_cost
    annual_opex = cost_model.opex_annual_usd(capex)
    decommissioning = capex * max(0.0, decommissioning_pct) / 100.0
    cashflows = [-capex]
    pv_costs, pv_energy = capex, 0.0
    cumulative = -capex
    payback = None
    cashflow_table = []
    pv_opex = 0.0
    pv_decommissioning = 0.0
    for year in range(1, project_years + 1):
        energy = aep_p50_mwh * ((1.0 - degradation_pct / 100.0) ** (year - 1))
        revenue = energy * 1000.0 * price_usd_kwh
        terminal = decommissioning if year == project_years else 0.0
        net = revenue - annual_opex - terminal
        cashflows.append(net)
        cumulative += net
        if payback is None and cumulative >= 0:
            payback = year
        factor = (1.0 + discount_rate) ** year
        discounted_opex = annual_opex / factor
        discounted_terminal = terminal / factor
        pv_opex += discounted_opex
        pv_decommissioning += discounted_terminal
        pv_costs += discounted_opex + discounted_terminal
        pv_energy += energy * 1000.0 / factor
        cashflow_table.append({
            'year': year,
            'energy_mwh': energy,
            'revenue_usd': revenue,
            'opex_usd': annual_opex,
            'decommissioning_usd': terminal,
            'net_cashflow_usd': net,
            'discount_factor': factor,
            'pv_net_cashflow_usd': net / factor,
            'cumulative_undiscounted_usd': cumulative,
        })
    van = sum(value / ((1.0 + discount_rate) ** year) for year, value in enumerate(cashflows))
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
        'roi': van / capex if capex > 0 else None,
        'irr': _irr(cashflows),
        'payback_years': payback,
        'lcoe_usd_kwh': pv_costs / pv_energy if pv_energy > 0 else None,
        'collection_cable_cost_usd': cable_cost,
        'decommissioning_usd': decommissioning,
        'decommissioning_present_value_usd': pv_decommissioning,
        'annual_revenue_year1_usd': aep_p50_mwh * 1000.0 * price_usd_kwh,
        'cashflow_table': cashflow_table,
        'capex_breakdown': cap,
        'economic_formulas': {
            'turbines_total': 'rated_power_kw × n_turbines × turbine_usd_per_kw',
            'base_capex': 'turbines_total_usd ÷ turbines_pct',
            'capex': 'base_capex_usd + collection_cable_cost_usd',
            'annual_opex': 'capex_usd × opex_pct_capex',
            'annual_revenue': 'aep_p50_mwh × 1000 × price_usd_kwh',
            'npv': 'Σ net_cashflow_t ÷ (1 + discount_rate)^t, incluyendo CAPEX en t=0',
            'lcoe': 'PV(CAPEX + OPEX + desmantelamiento) ÷ PV(energía neta)',
        },
    }


def analyze_park_layouts(sites: List[Dict], polygon: List[Dict], turbines_folder: str,
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
                         weights: Optional[Dict[str, float]] = None,
                         unistmo_weights: Optional[Dict[str, float]] = None,
                         _parallel_models: bool = True,
                         _capacity_witness: Optional[Dict] = None):
    if not sites:
        raise ValueError('No hay recurso eólico interpolado para optimizar el parque.')
    if len(polygon or []) < 3:
        raise ValueError('El parque requiere un polígono con al menos tres vértices.')
    if wake_model not in {'gaussian', 'jensen'}:
        raise ValueError('wake_model debe ser gaussian o jensen.')
    if min_spacing_d < 3.0:
        raise ValueError('La separación mínima no puede ser menor que 3D.')
    if not 0.0 <= float(max_wake_loss_pct) < 100.0:
        raise ValueError('El límite de pérdida por estela debe estar en [0, 100).')
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
    ws, wd, mean_speed, weibull_k, rho = _joint_synthetic_wind(sites)
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
    if len(unique_filenames) > 1:
        single_kwargs = {
            'wind_grid': wind_grid,
            'exclusions': exclusions,
            'boundary_setback_m': boundary_setback_m,
            'min_spacing_d': min_spacing_d,
            's_para_range': s_para_range,
            's_perp_range': s_perp_range,
            'max_wake_loss_pct': max_wake_loss_pct,
            'wake_model': wake_model,
            'k_wake': k_wake,
            'ct': ct,
            'availability': availability,
            'electrical_losses': electrical_losses,
            'curtailment_loss': curtailment_loss,
            'environmental_loss': environmental_loss,
            'discount_rate': discount_rate,
            'project_years': project_years,
            'price_usd_kwh': price_usd_kwh,
            'demand_mwh': demand_mwh,
            'commercialization_mode': commercialization_mode,
            'grid_limit_mw': grid_limit_mw,
            'target_height_m': target_height_m,
            'cost_model': cost_model,
            'cost_model_origin': cost_model_origin,
            'uncertainty_components_pct': uncertainty_components_pct,
            'weights': weights,
            'unistmo_weights': unistmo_weights,
            '_parallel_models': False,
        }

        def analyze_one(filename):
            return analyze_park_layouts(
                sites, polygon, turbines_folder, [filename], **single_kwargs)

        if _parallel_models:
            worker_count = min(4, len(unique_filenames))
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                per_model = list(pool.map(analyze_one, unique_filenames))
        else:
            per_model = [analyze_one(filename) for filename in unique_filenames]

        # Enforce the physical invariant constructively. If the grid search for
        # a smaller rotor found fewer positions, rerun it with the larger-rotor
        # layout as an additional, already-feasible capacity witness.
        ordered = []
        for idx, result in enumerate(per_model):
            rows = result.get('alternatives') or []
            if rows:
                ordered.append((
                    -float(rows[0]['turbine_specification']['rotor_diameter_m']),
                    idx,
                ))
        inherited_witness = None
        inherited_count = 0
        for _, idx in sorted(ordered):
            row = per_model[idx]['alternatives'][0]
            if inherited_witness and int(row['max_n_fisico']) < inherited_count:
                repaired_kwargs = dict(single_kwargs)
                repaired_kwargs['_capacity_witness'] = inherited_witness
                repaired_result = analyze_park_layouts(
                    sites, polygon, turbines_folder, [unique_filenames[idx]],
                    **repaired_kwargs)
                repaired_rows = repaired_result.get('alternatives') or []
                if repaired_rows:
                    per_model[idx] = repaired_result
                    row = repaired_rows[0]
            current_count = int(row['max_n_fisico'])
            if current_count >= inherited_count:
                inherited_count = current_count
                inherited_witness = {
                    'layout_xy_m': row.get('capacity_layout_xy_m') or [],
                    'layout_latlon': row.get('capacity_layout_latlon') or [],
                    'wind_at_turbines': row.get('capacity_wind_at_turbines') or [],
                    'capacity_config': row.get('capacity_config') or {},
                    'source_rotor_m': float(
                        row['turbine_specification']['rotor_diameter_m']),
                }
        alternatives = [
            alternative
            for result in per_model
            for alternative in result.get('alternatives', [])
        ]
        errors = [
            error
            for result in per_model
            for error in result.get('errors', [])
        ]
        ranking = rank_layout_alternatives(alternatives, weights=weights)
        unistmo_ranking = rank_layout_alternatives(
            alternatives, weights=unistmo_weights, profile='unistmo_v1')
        combined = dict(per_model[0])
        combined.update({
            'alternatives': alternatives,
            'ranking': ranking,
            'ranking_scenarios': {
                'base': ranking,
                'unistmo': unistmo_ranking,
            },
            'n_layout_alternatives': len(alternatives),
            'n_requested_models': len(unique_filenames),
            'n_valid_models': len(alternatives),
            'errors': errors,
        })
        return combined
    for filename in unique_filenames:
        try:
            turbine = PowerCurve.from_file(os.path.join(turbines_folder, os.path.basename(filename)))
            if target_height_m:
                turbine.hub_height_m = float(target_height_m)
            setback = max(float(boundary_setback_m), turbine.rotor_diameter_m / 2.0)
            # Candidate grids depend on the turbine geometry and project area,
            # but not on the requested turbine count. Reuse them while finding
            # the largest layout that meets the wake-loss limit.
            layout_search_cache = {}
            capacity = optimize_layout_structured(
                polygon, turbine, 0, ws=ws, wd=wd, wind_grid=wind_grid,
                fallback_wind_ms=mean_speed, k_wake=k_wake, ct=ct,
                min_spacing_d=min_spacing_d, s_para_range=tuple(s_para_range),
                s_perp_range=tuple(s_perp_range), rho=rho,
                exclusion_polygons=exclusions, boundary_setback_m=setback,
                wake_model=wake_model,
                _search_cache=layout_search_cache,
                _capacity_witness=_capacity_witness,
            )
            physical_max_n = int(capacity.get('max_n_fit') or capacity.get('n_placed') or 0)
            max_n = physical_max_n
            if grid_limit_mw and grid_limit_mw > 0:
                max_n = min(max_n, int(math.floor(grid_limit_mw * 1000.0 / turbine.rated_power_kw)))
            if max_n <= 0:
                errors.append({'turbine': filename, 'reason': 'sin_layout_factible'})
                continue
            # Select the largest exact-count layout whose whole-farm wake loss
            # satisfies the configured limit. Every decrement is reoptimised;
            # a subset of the denser layout is not assumed to be optimal.
            layout = None
            sim = None
            selected_n = 0
            wake_trials = []
            for candidate_n in range(max_n, 0, -1):
                candidate_layout = (
                    capacity
                    if candidate_n == physical_max_n and max_n == physical_max_n
                    else optimize_layout_structured(
                        polygon, turbine, candidate_n, ws=ws, wd=wd,
                        wind_grid=wind_grid, fallback_wind_ms=mean_speed,
                        k_wake=k_wake, ct=ct, min_spacing_d=min_spacing_d,
                        s_para_range=tuple(s_para_range),
                        s_perp_range=tuple(s_perp_range), rho=rho,
                        exclusion_polygons=exclusions,
                        boundary_setback_m=setback, wake_model=wake_model,
                        _search_cache=layout_search_cache,
                        _capacity_witness=_capacity_witness,
                    )
                )
                if int(candidate_layout['n_placed']) != candidate_n:
                    continue
                candidate_sim = simulate_wind_farm(
                    candidate_layout['layout_xy_m'], turbine, ws, wd,
                    k_wake=k_wake, ct=ct, n_sectors=36,
                    availability=availability,
                    electrical_losses=electrical_losses,
                    wake_model=wake_model, rho_override=rho,
                )
                if candidate_sim.get('error'):
                    continue
                trial_wake = float(candidate_sim.get('wake_loss_pct') or 0.0)
                wake_trials.append({'n': candidate_n, 'wake_loss_pct': trial_wake})
                if trial_wake <= float(max_wake_loss_pct) + 1e-9:
                    layout = candidate_layout
                    sim = candidate_sim
                    selected_n = candidate_n
                    break
            if layout is None or sim is None or selected_n <= 0:
                errors.append({
                    'turbine': filename,
                    'reason': 'sin_layout_que_cumpla_limite_estela',
                    'max_wake_loss_pct': float(max_wake_loss_pct),
                    'wake_trials': wake_trials,
                })
                continue
            other_survival = (1.0 - curtailment_loss) * (1.0 - environmental_loss)
            aep_p50 = float(sim['aep_mwh']) * other_survival
            uncertainty_components = dict(uncertainty_components_pct or {
                'resource': 6.0, 'mcp': 4.0, 'wake': 3.0,
                'power_curve': 2.0, 'losses': 2.0,
            })
            if any(float(value) < 0 for value in uncertainty_components.values()):
                raise ValueError('Las incertidumbres no pueden ser negativas.')
            uncertainty_components = {key: float(value) for key, value in uncertainty_components.items()}
            uncertainty_pct = math.sqrt(sum(value ** 2 for value in uncertainty_components.values()))
            aep_p90 = max(0.0, aep_p50 * (1.0 - 1.28155 * uncertainty_pct / 100.0))
            collection_length = _collection_length_m(layout['layout_xy_m'])
            econ = _economics(
                turbine, selected_n, aep_p50, cost_model, discount_rate,
                project_years, price_usd_kwh, collection_length,
            )
            installed_mw = selected_n * turbine.rated_power_kw / 1000.0
            factor_capacity = aep_p50 / (installed_mw * 8760.0) if installed_mw > 0 else 0.0
            area_km2 = max(float(layout.get('area_km2') or 0.0), 1e-9)
            alternatives.append({
                'label': f'layout::{filename}::wake-optimal',
                'site': f'Parque óptimo por estela ({selected_n}/{physical_max_n} aeros)',
                'turbina': filename,
                'n_aerogeneradores': selected_n,
                'n_optimo_estela': selected_n,
                'max_n_fisico': physical_max_n,
                'max_n_tecnico': max_n,
                'max_n_post_red': max_n,
                'reducido_por_estela': selected_n < max_n,
                'max_wake_loss_pct': float(max_wake_loss_pct),
                'wake_trials': wake_trials,
                'feasible': True,
                'layout_latlon': layout['layout_latlon'],
                'layout_xy_m': layout['layout_xy_m'],
                'layout_config': layout['layout_config'],
                'capacity_config': capacity.get('capacity_config') or {},
                'capacity_method': (capacity.get('capacity_config') or {}).get(
                    'method', 'own_geometric_grid_search'),
                'capacity_source_rotor_m': (capacity.get('capacity_config') or {}).get(
                    'source_rotor_m', float(turbine.rotor_diameter_m)),
                'capacity_layout_latlon': capacity.get('layout_latlon') or [],
                'capacity_layout_xy_m': capacity.get('layout_xy_m') or [],
                'capacity_wind_at_turbines': capacity.get('wind_at_turbines') or [],
                'min_sep_m': layout['min_sep_m'],
                'area_km2': area_km2,
                'installed_mw': installed_mw,
                'aep_gross_mwh': sim['aep_gross_mwh'],
                'aep_mwh': aep_p50,
                'aep_p50_mwh': aep_p50,
                'aep_p90_mwh': aep_p90,
                'aep_uncertainty_pct': uncertainty_pct,
                'uncertainty_components_pct': uncertainty_components,
                'wake_loss_pct': sim['wake_loss_pct'],
                'curtailment_loss_pct': curtailment_loss * 100.0,
                'environmental_loss_pct': environmental_loss * 100.0,
                'factor_capacidad': factor_capacity,
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
                    'degradation_pct_per_year': 0.3,
                    'decommissioning_pct_capex': 3.0,
                    'collection_cable_usd_per_m': 75.0,
                    'cost_model_origin': cost_model_origin,
                },
                **econ,
            })
        except Exception as exc:
            errors.append({'turbine': filename, 'reason': str(exc)})
    ranking = rank_layout_alternatives(alternatives, weights=weights)
    unistmo_ranking = rank_layout_alternatives(
        alternatives, weights=unistmo_weights, profile='unistmo_v1'
    )
    return {
        'analysis_kind': 'farm_layout_mcda_v2',
        'alternatives': alternatives,
        'ranking': ranking,
        'ranking_scenarios': {
            'base': ranking,
            'unistmo': unistmo_ranking,
        },
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
            'count_policy': 'largest_exact_count_with_wake_loss_at_or_below_limit',
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

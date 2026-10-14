"""Farm-layout alternatives for scientific MCDA pre-feasibility studies."""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
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
    bin_wind_to_sectors,
    chronological_revenue,
    dominant_direction,
    energy_weighted_dominant_direction,
    gaussian_min_valid_distance_d,
    simulate_wind_farm,
)
from core.multicriteria import rank_layout_alternatives
from core.historical_resource import prepare_historical_wind, historical_layout_simulation
from core.energy import air_density


SCENARIO_YEARS = list(range(2017, 2024))


def derive_price_scenarios(by_year: Dict[int, dict],
                           years: List[int] = None) -> Dict:
    """Derive bajo/base/alto price scenarios from chronological_revenue by_year.

    Raises ValueError if any required year is missing or incomplete.
    """
    years = years or SCENARIO_YEARS
    missing = [y for y in years
               if y not in by_year or not by_year[y].get('complete')]
    if missing:
        raise ValueError(
            f'Año(s) {missing} ausente(s) o incompleto(s) en by_year; '
            f'se requieren los {len(years)} años {years[0]}-{years[-1]}.')
    prices = [by_year[y]['avg_realized_price_usd_kwh'] for y in years]
    return {
        'bajo': min(prices),
        'base': float(np.mean(prices)),
        'alto': max(prices),
        'by_year_detail': {y: by_year[y]['avg_realized_price_usd_kwh']
                           for y in years},
    }


def extract_candidate_pool(generation: dict, turbine_filename: str,
                           hub_height_m: float) -> List[Dict]:
    """Extract minimal data per wake-feasible candidate for session cache."""
    pool_cache = []
    for candidate in generation['pool']:
        if not candidate['feasibility']['wake_loss_ok']:
            continue
        precomputed = candidate.get('_precomputed')
        if precomputed is None:
            continue
        chron = candidate['sim'].get('chronological_revenue')
        pool_cache.append({
            'layout_xy_m': candidate['layout_xy_m'],
            'n_turbines': candidate['n_turbines'],
            'turbine_filename': turbine_filename,
            'hub_height_m': float(hub_height_m),
            'aep_p50_mwh': precomputed['aep_p50'],
            'collection_length_m': precomputed['collection_length'],
            'ratio_by_sector': chron.get('ratio_by_sector') if chron else None,
            'scenarios': precomputed.get('scenarios'),
            'van_usd': candidate.get('van_usd'),
        })
    return pool_cache


def rerun_economics_from_pool(
    pool_caches: Dict[str, List[Dict]],
    turbines_folder: str,
    scenario: str,
    cost_model: CostModel,
    discount_rate: float,
    project_years: int,
    annual_inflation_pct: Optional[float] = None,
    price_escalation_pct: Optional[float] = None,
    uncertainty_components_pct: Optional[Dict[str, float]] = None,
    interannual_variability_pct: float = 6.4,
) -> dict:
    """Re-run economics from cached pool with a different price scenario.

    Returns {'alternatives': [...], 'warnings': [...]}. If all candidates
    lack scenarios, returns empty alternatives + warning SCE001.
    """
    import os
    warnings_out: List[str] = []
    alternatives: List[Dict] = []

    any_has_scenarios = False
    for turbine_fn, candidates in pool_caches.items():
        turbine = PowerCurve.from_file(
            os.path.join(turbines_folder, os.path.basename(turbine_fn)))
        feasible = []
        for c in candidates:
            if c.get('scenarios') is None:
                continue
            any_has_scenarios = True
            price = c['scenarios'].get(scenario)
            if price is None:
                continue
            econ = _economics(
                turbine, c['n_turbines'], c['aep_p50_mwh'], cost_model,
                discount_rate, project_years, price, c['collection_length_m'],
                revenue_year1_override=None,
                annual_inflation_pct=annual_inflation_pct,
                price_escalation_pct=price_escalation_pct,
                discount_rate_basis='nominal',
            )
            feasible.append({**c, 'van_usd': econ['van_usd'], '_econ': econ})
        if not feasible:
            continue
        champion = max(feasible, key=lambda x: x['van_usd'])
        econ = champion['_econ']

        uncertainty_components = dict(uncertainty_components_pct or {
            'resource': 6.0,
            'mcp': 4.0,
            'wake': 3.0,
            'power_curve': 2.0,
            'losses': 2.0,
        })
        uncertainty_model_pct = math.sqrt(
            sum(v ** 2 for v in uncertainty_components.values()))
        _iav_1yr = float(interannual_variability_pct)
        _n_yrs = max(project_years or 25, 1)
        _combined_1yr = math.sqrt(uncertainty_model_pct ** 2 + _iav_1yr ** 2)
        _combined_nyrs = math.sqrt(
            uncertainty_model_pct ** 2 + (_iav_1yr / math.sqrt(_n_yrs)) ** 2)
        aep_p50 = champion['aep_p50_mwh']
        aep_p90 = max(0.0, aep_p50 * (1.0 - 1.28155 * _combined_1yr / 100.0))
        installed_mw = champion['n_turbines'] * turbine.rated_power_kw / 1000.0
        factor_cap = aep_p50 / (installed_mw * 8760.0) if installed_mw > 0 else 0.0

        alt = {
            'label': f'layout::{turbine_fn}::wake-optimal',
            'turbina': turbine_fn,
            'is_champion': True,
            'n_aerogeneradores': champion['n_turbines'],
            'n_optimo_estela': champion['n_turbines'],
            'max_n_fisico': champion['n_turbines'],
            'aep_p50_mwh': aep_p50,
            'aep_p90_mwh': aep_p90,
            'aep_p90_mwh_1yr': aep_p90,
            'aep_p90_mwh_25yr': max(0.0, aep_p50 * (1.0 - 1.28155 * _combined_nyrs / 100.0)),
            'aep_uncertainty_pct': uncertainty_model_pct,
            'aep_uncertainty_pct_1yr': _combined_1yr,
            'aep_uncertainty_pct_25yr': _combined_nyrs,
            'interannual_variability_pct': _iav_1yr,
            'factor_capacidad': factor_cap,
            'installed_mw': installed_mw,
            'capex_usd_per_installed_kw': (
                econ['capex_usd'] / (installed_mw * 1000) if installed_mw > 0 else None),
            'scenario': scenario,
            'scenario_price_usd_kwh': champion['scenarios'][scenario],
            **{k: econ[k] for k in (
                'van_usd', 'van_real_usd', 'irr', 'lcoe_usd_kwh',
                'lcoe_nom_usd_kwh', 'lcoe_real_usd_kwh',
                'benefit_cost_ratio', 'roi', 'payback_years',
                'annual_revenue_year1_flat_usd', 'economics_warnings',
            ) if k in econ},
        }
        alternatives.append(alt)

    if not any_has_scenarios:
        warnings_out.append('SCE001: Pool sin escenarios de precio; se requiere pml_series.')
    return {'alternatives': alternatives, 'warnings': warnings_out}


def _rerun_with_overrides(
    pool_caches: Dict[str, List[Dict]],
    turbines_folder: str,
    cost_model: CostModel,
    discount_rate: float,
    project_years: int,
    price_usd_kwh: float,
    annual_inflation_pct: Optional[float] = None,
    price_escalation_pct: Optional[float] = None,
    aep_factor: float = 1.0,
) -> dict:
    """Like rerun_economics_from_pool but accepts explicit parameter overrides.

    ``price_usd_kwh`` is the flat price used for all candidates.
    ``aep_factor`` multiplies the P50 AEP before running economics (e.g. 0.90
    simulates a 10 % resource under-estimate).
    """
    alternatives: List[Dict] = []
    for turbine_fn, candidates in pool_caches.items():
        turbine = PowerCurve.from_file(
            os.path.join(turbines_folder, os.path.basename(turbine_fn)))
        feasible = []
        for c in candidates:
            aep = float(c.get('aep_p50_mwh', 0) or 0) * aep_factor
            if aep <= 0:
                continue
            econ = _economics(
                turbine, c['n_turbines'], aep, cost_model,
                discount_rate, project_years, price_usd_kwh,
                c['collection_length_m'],
                revenue_year1_override=None,
                annual_inflation_pct=annual_inflation_pct,
                price_escalation_pct=price_escalation_pct,
                discount_rate_basis='nominal',
            )
            feasible.append({**c, 'aep_p50_mwh': aep, 'van_usd': econ['van_usd'], '_econ': econ})
        if not feasible:
            continue
        champion = max(feasible, key=lambda x: x['van_usd'])
        econ = champion['_econ']
        installed_mw = champion['n_turbines'] * turbine.rated_power_kw / 1000.0
        factor_cap = champion['aep_p50_mwh'] / (installed_mw * 8760.0) if installed_mw > 0 else 0.0
        alt = {
            'label': f'layout::{turbine_fn}::wake-optimal',
            'turbina': turbine_fn,
            'is_champion': True,
            'n_aerogeneradores': champion['n_turbines'],
            'n_optimo_estela': champion['n_turbines'],
            'max_n_fisico': champion['n_turbines'],
            'aep_p50_mwh': champion['aep_p50_mwh'],
            'factor_capacidad': factor_cap,
            'installed_mw': installed_mw,
            'capex_usd_per_installed_kw': (
                econ['capex_usd'] / (installed_mw * 1000) if installed_mw > 0 else None),
            **{k: econ[k] for k in (
                'van_usd', 'van_real_usd', 'irr', 'lcoe_usd_kwh',
                'lcoe_nom_usd_kwh', 'lcoe_real_usd_kwh',
                'benefit_cost_ratio', 'roi', 'payback_years',
                'annual_revenue_year1_flat_usd', 'economics_warnings',
            ) if k in econ},
        }
        alternatives.append(alt)
    return {'alternatives': alternatives}


def topsis_scenario_sensitivity(
    pool_caches: Dict[str, List[Dict]],
    turbines_folder: str,
    cost_model: CostModel,
    base_discount_rate: float,
    project_years: int,
    base_annual_inflation_pct: Optional[float] = None,
    base_price_escalation_pct: Optional[float] = None,
    base_price_usd_kwh: float = 0.07,
) -> Dict:
    """Evaluate TOPSIS winner stability across AEP, rate, and inflation scenarios.

    Returns a dict with per-dimension scenario tables and an overall stability
    summary. All dimensions are evaluated independently against the base case.
    Combined extremes (stress / optimista) are also included.
    """
    from core.multicriteria import rank_layout_alternatives as _rank

    inf = base_annual_inflation_pct if base_annual_inflation_pct is not None else 0.0
    rate = float(base_discount_rate)
    price = float(base_price_usd_kwh)

    # Base winner
    base_run = _rerun_with_overrides(
        pool_caches, turbines_folder, cost_model, rate, project_years,
        price, inf, base_price_escalation_pct, aep_factor=1.0)
    base_alts = base_run['alternatives']
    if not base_alts:
        return {'error': 'No hay alternativas en el pool para el escenario base.'}
    base_ranking = _rank(base_alts) if len(base_alts) >= 2 else {'ranking': [{'label': base_alts[0].get('turbina', ''), 'turbina': base_alts[0].get('turbina', '')}]}
    base_winner = base_ranking['ranking'][0]['turbina'] if base_ranking.get('ranking') else None

    def _run_scenario(label: str, dim: str, aep_f: float, rate_v: float,
                      inf_v: float, price_v: float, params: dict) -> dict:
        r = _rerun_with_overrides(
            pool_caches, turbines_folder, cost_model, rate_v, project_years,
            price_v, inf_v, base_price_escalation_pct, aep_factor=aep_f)
        alts = r['alternatives']
        if not alts:
            return {'label': label, 'dim': dim, 'params': params, 'winner': None, 'stable': False, 'ranking': []}
        ranking = _rank(alts) if len(alts) >= 2 else {'ranking': [{'label': alts[0].get('turbina', ''), 'turbina': alts[0].get('turbina', '')}]}
        winner = ranking['ranking'][0]['turbina'] if ranking.get('ranking') else None
        return {
            'label': label, 'dim': dim, 'params': params,
            'winner': winner,
            'stable': winner == base_winner,
            'ranking': ranking.get('ranking', [])[:6],
        }

    aep_scenarios = [
        _run_scenario('AEP −20%', 'aep', 0.80, rate, inf, price, {'aep_factor': 0.80}),
        _run_scenario('AEP −10%', 'aep', 0.90, rate, inf, price, {'aep_factor': 0.90}),
        _run_scenario('AEP base',  'aep', 1.00, rate, inf, price, {'aep_factor': 1.00}),
        _run_scenario('AEP +10%', 'aep', 1.10, rate, inf, price, {'aep_factor': 1.10}),
    ]

    rate_delta = [(-0.03, 'WACC −3 pp'), (-0.015, 'WACC −1.5 pp'),
                  (0.0, 'WACC base'), (0.015, 'WACC +1.5 pp'), (0.03, 'WACC +3 pp')]
    rate_scenarios = [
        _run_scenario(lbl, 'rate', 1.0, max(0.01, rate + d), inf, price, {'discount_rate': round(rate + d, 4)})
        for d, lbl in rate_delta
    ]

    if inf > 0:
        inf_cases = [(0.0, 'Inflación 0%'), (inf * 0.5, 'Inflación ½× base'),
                     (inf, 'Inflación base'), (inf * 1.5, 'Inflación 1.5× base'), (inf * 2.0, 'Inflación 2× base')]
    else:
        inf_cases = [(0.0, 'Inflación 0% (base)'), (0.01, 'Inflación 1%'),
                     (0.02, 'Inflación 2%'), (0.03, 'Inflación 3%'), (0.05, 'Inflación 5%')]
    inflation_scenarios = [
        _run_scenario(lbl, 'inflation', 1.0, rate, iv, price, {'annual_inflation_pct': round(iv, 4)})
        for iv, lbl in inf_cases
    ]

    combined_scenarios = [
        _run_scenario('Estrés máximo', 'combined', 0.80, max(0.01, rate + 0.03), inf * 2.0 if inf > 0 else 0.05, price, {
            'aep_factor': 0.80, 'discount_rate': round(rate + 0.03, 4), 'annual_inflation_pct': round(inf * 2 if inf > 0 else 0.05, 4)}),
        _run_scenario('Escenario optimista', 'combined', 1.10, max(0.01, rate - 0.03), inf * 0.5 if inf > 0 else 0.0, price, {
            'aep_factor': 1.10, 'discount_rate': round(max(0.01, rate - 0.03), 4), 'annual_inflation_pct': round(inf * 0.5 if inf > 0 else 0.0, 4)}),
    ]

    all_scenarios = aep_scenarios + rate_scenarios + inflation_scenarios + combined_scenarios
    stable_count = sum(1 for s in all_scenarios if s.get('stable'))
    total = len(all_scenarios)

    return {
        'base_winner': base_winner,
        'base_winner_label': base_ranking['ranking'][0].get('label', base_winner) if base_ranking.get('ranking') else base_winner,
        'aep_scenarios': aep_scenarios,
        'rate_scenarios': rate_scenarios,
        'inflation_scenarios': inflation_scenarios,
        'combined_scenarios': combined_scenarios,
        'stability_summary': {
            'total_scenarios': total,
            'winner_stable_count': stable_count,
            'winner_changes_count': total - stable_count,
            'stability_rate_pct': round(stable_count / total * 100, 1) if total else 0,
        },
    }


def compute_annual_energy_breakdown(
    layout_xy_m, turbine, resource, ratio_by_sector,
    availability=0.97, electrical_losses=0.03,
    curtailment_loss=0.0, environmental_loss=0.0,
    rho_override=None,
):
    """Thin wrapper: hourly energy with pre-computed wake, then AEPCalculator."""
    import pandas as pd
    from core.energy import AEPCalculator, air_density

    layout = np.asarray(layout_xy_m, dtype=float)
    n = len(layout)
    ws = np.asarray(resource['ws'], dtype=float).reshape(-1)
    wd = np.asarray(resource['wd'], dtype=float).reshape(-1) % 360.0
    valid = np.isfinite(ws) & np.isfinite(wd) & (ws >= 0)
    ws_v = ws[valid]
    wd_v = wd[valid]
    ts_full = np.asarray(resource['timestamps_utc']).reshape(-1)
    ts_v = ts_full[valid]
    dt_h = float(resource.get('dt_hours', 1.0))

    if rho_override is not None and np.isfinite(float(rho_override)) and float(rho_override) > 0:
        rho = float(rho_override)
    elif resource.get('temperature_c') is not None and resource.get('pressure_pa') is not None:
        tc = np.asarray(resource['temperature_c'], dtype=float).reshape(-1)
        pp = np.asarray(resource['pressure_pa'], dtype=float).reshape(-1)
        rho = float(np.mean(air_density(tc[valid], pp[valid])))
    else:
        rho = 1.225

    lm = np.ones(n)
    _rbs = np.asarray(ratio_by_sector, dtype=float)
    n_sectors = _rbs.shape[0]
    sector_deg = 360.0 / n_sectors
    sector_idx = np.floor(wd_v / sector_deg).astype(int) % n_sectors

    ws_free = ws_v[:, None] * lm[None, :]
    v_eff = ws_free * _rbs[sector_idx]
    power_kw = turbine.power_kw_density_corrected(v_eff, rho)
    survival = (float(availability) * (1.0 - float(electrical_losses))
                * (1.0 - float(curtailment_loss)) * (1.0 - float(environmental_loss)))
    energy_kwh = power_kw.sum(axis=1) * dt_h * survival

    ts_idx = pd.DatetimeIndex(ts_v)
    df = pd.DataFrame({'energy_net_kwh': energy_kwh}, index=ts_idx)
    calc = AEPCalculator(turbine)
    return calc.compute_from_timeseries(df, energy_col='energy_net_kwh')


def robustness_matrix(
    finalists: List[Dict],
    turbines_folder: str,
    resources_by_height: Dict[float, Dict],
    cost_model: CostModel,
    discount_rate: float,
    project_years: int,
    annual_inflation_pct: Optional[float] = None,
    price_escalation_pct: Optional[float] = None,
    discount_rate_basis: str = 'nominal',
    availability: float = 0.97,
    electrical_losses: float = 0.03,
    curtailment_loss: float = 0.0,
    environmental_loss: float = 0.0,
) -> dict:
    """Energy (P50/P75/P90 empirical) x price (bajo/base/alto) matrix."""
    import os
    results = []
    for f in finalists:
        h = f.get('hub_height_m')
        resource = resources_by_height.get(h)
        if resource is None or resource.get('timestamps_utc') is None:
            continue
        rbs = f.get('ratio_by_sector')
        if rbs is None:
            continue
        turbine = PowerCurve.from_file(
            os.path.join(turbines_folder, os.path.basename(f['turbine_filename'])))
        breakdown = compute_annual_energy_breakdown(
            f['layout_xy_m'], turbine, resource, rbs,
            availability=availability, electrical_losses=electrical_losses,
            curtailment_loss=curtailment_loss, environmental_loss=environmental_loss)
        p50_mwh = breakdown.get('p50_mwh', breakdown.get('aep_p50_mwh', f['aep_p50_mwh']))
        p75_mwh = breakdown.get('p75_mwh', breakdown.get('aep_p75_mwh', p50_mwh * 0.95))
        p90_mwh = breakdown.get('p90_mwh', breakdown.get('aep_p90_mwh', p50_mwh * 0.90))
        mean_mwh = breakdown.get('mean_mwh', breakdown.get('aep_mean_mwh', p50_mwh))
        std_mwh = breakdown.get('std_mwh', breakdown.get('aep_std_mwh', 0.0))
        iav_real = (std_mwh / mean_mwh * 100.0) if mean_mwh > 0 else 0.0

        scenarios = f.get('scenarios', {})
        energy_levels = {'p50': p50_mwh, 'p75': p75_mwh, 'p90': p90_mwh}
        price_levels = {k: scenarios[k] for k in ('bajo', 'base', 'alto') if k in scenarios}
        matrix_cells = []
        for e_label, e_val in energy_levels.items():
            for p_label, p_val in price_levels.items():
                econ = _economics(
                    turbine, f['n_turbines'], e_val, cost_model,
                    discount_rate, project_years, p_val, f['collection_length_m'],
                    revenue_year1_override=None,
                    annual_inflation_pct=annual_inflation_pct,
                    price_escalation_pct=price_escalation_pct,
                    discount_rate_basis=discount_rate_basis,
                )
                matrix_cells.append({
                    'energy': e_label, 'price': p_label,
                    'van_usd': econ.get('van_usd'),
                    'irr': econ.get('irr'),
                    'lcoe_usd_kwh': econ.get('lcoe_usd_kwh'),
                })
        results.append({
            'label': f.get('label', f.get('turbine_filename')),
            'turbine': f.get('turbine_filename'),
            'energy_empirical': {
                'p50_mwh': p50_mwh, 'p75_mwh': p75_mwh,
                'p90_mwh_empirical': p90_mwh, 'mean_mwh': mean_mwh,
                'std_mwh': std_mwh,
                'interannual_variability_pct': round(iav_real, 4),
                'n_years': breakdown.get('n_years', 0),
            },
            'scenarios': {k: scenarios.get(k) for k in ('bajo', 'base', 'alto')},
            'matrix': matrix_cells,
        })
    return {'finalists': results}


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
               revenue_year1_override: Optional[float] = None,
               annual_inflation_pct: Optional[float] = None,
               price_escalation_pct: Optional[float] = None,
               discount_rate_basis: Optional[str] = None):
    cap = cost_model.capex_breakdown(turbine.rated_power_kw, n_turbines)
    cable_cost = max(0.0, collection_length_m * cable_usd_per_m)
    foundation_cost = cost_model.foundation_cost_usd(
        turbine.rotor_diameter_m, turbine.hub_height_m, n_turbines)
    capex = float(cap['total_capex_usd']) + cable_cost + foundation_cost
    annual_opex = cost_model.opex_annual_usd(capex)

    economics_warnings: list = []

    if annual_inflation_pct is None:
        economics_warnings.append(
            'INF001: annual_inflation_pct no proporcionado; se asume 0.0')
        annual_inflation_pct = 0.0
    if discount_rate_basis is None:
        economics_warnings.append(
            'BASIS001: discount_rate_basis ausente; se asume nominal')
        discount_rate_basis = 'nominal'
    if annual_inflation_pct == 0.0 and discount_rate_basis == 'nominal':
        economics_warnings.append(
            'INF002: inflacion 0.0 con base nominal; OPEX no escala')
    # price_escalation_pct=None → igual a annual_inflation_pct ya resuelto
    if price_escalation_pct is None:
        price_escalation_pct = annual_inflation_pct

    r_nom = float(discount_rate)
    inf_factor = 1.0 + annual_inflation_pct / 100.0
    r_real = r_nom if annual_inflation_pct == 0.0 else (1.0 + r_nom) / inf_factor - 1.0
    if r_real <= 0:
        economics_warnings.append(
            f'BASIS002: tasa real {r_real:.4f} <= 0')

    # Nominal run: OPEX crece con inflación, precio escala con price_escalation_pct
    price_series_nom = None
    if price_escalation_pct != 0.0 or annual_inflation_pct != 0.0:
        price_series_nom = [
            price_usd_kwh * (1.0 + price_escalation_pct / 100.0) ** t
            for t in range(project_years)
        ]
    lifecycle = project_cashflows(
        capex, annual_opex, aep_p50_mwh * 1000.0, price_usd_kwh,
        r_nom, project_years, degradation_pct=degradation_pct,
        decommissioning_pct=decommissioning_pct,
        annual_inflation_pct=annual_inflation_pct, price_series=price_series_nom,
        year1_revenue_override=revenue_year1_override)

    # Real run (Fisher equivalente): deflaciona OPEX y desmantelamiento;
    # precio real = precio_nominal_año1 / (1+inf). Con escal=inf → escal_real=0.
    # VAN_real = VAN_nom exactamente (propiedad Fisher); LCOE_real ≠ LCOE_nom.
    if annual_inflation_pct != 0.0:
        opex_real = annual_opex / inf_factor
        decomm_pct_real = decommissioning_pct / (inf_factor ** project_years)
        price_real = price_usd_kwh / inf_factor
        escal_real_pct = ((1.0 + price_escalation_pct / 100.0) / inf_factor - 1.0) * 100.0
        price_series_real = None
        if abs(escal_real_pct) > 1e-12:
            price_series_real = [
                price_real * (1.0 + escal_real_pct / 100.0) ** t
                for t in range(project_years)
            ]
        lifecycle_real = project_cashflows(
            capex, opex_real, aep_p50_mwh * 1000.0, price_real,
            r_real, project_years, degradation_pct=degradation_pct,
            decommissioning_pct=decomm_pct_real,
            annual_inflation_pct=0.0, price_series=price_series_real)
    else:
        lifecycle_real = lifecycle

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
    pv_opex_real = lifecycle_real['opex_present_value_usd']
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
        'lcoe_nom_usd_kwh': pv_costs / pv_energy if pv_energy > 0 else None,
        'lcoe_real_usd_kwh': (lifecycle_real['pv_costs'] / lifecycle_real['pv_energy_kwh']
                               if lifecycle_real['pv_energy_kwh'] > 0 else None),
        'van_real_usd': lifecycle_real['van_usd'],
        'economics_warnings': economics_warnings,
        'opex_present_value_ratio': (pv_opex_real / pv_opex if pv_opex > 0 else None),
        'collection_cable_cost_usd': cable_cost,
        'foundation_cost_usd': foundation_cost,
        'decommissioning_usd': decommissioning,
        'decommissioning_present_value_usd': pv_decommissioning,
        'annual_revenue_year1_usd': (
            float(revenue_year1_override) if revenue_year1_override is not None
            else aep_p50_mwh * 1000.0 * price_usd_kwh
        ),
        'annual_revenue_year1_flat_usd': aep_p50_mwh * 1000.0 * price_usd_kwh,
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
                         annual_inflation_pct: Optional[float] = None,
                         price_escalation_pct: Optional[float] = None,
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
                         local_refinement_max_evals: int = 60,
                         pml_series_values: Optional[np.ndarray] = None,
                         pml_series_timestamps_utc=None,
                         interannual_variability_pct: float = 6.4):
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
    def _process_turbine(filename: str, shared_layouts=None, prior_generation=None,
                         ws=ws, wd=wd, mean_speed=mean_speed, rho=rho) -> Dict:
        """Pipeline completo (paso 6 + economía) para UNA tecnología.

        Aislado en su propio scope a propósito: todas las variables que
        antes vivían reasignadas en el cuerpo del for (ws/wd/mean_speed/rho
        cuando hay historical_wind, turbine, resource, setback,
        evaluate_layout...) quedan locales a esta función en vez de
        pisar las del generate_park_layouts que la encierra -- eso es lo
        que hace seguro correr tecnologías en paralelo (ThreadPoolExecutor
        más abajo): sin este aislamiento, dos tecnologías concurrentes se
        pisarían el ws/wd/rho una a la otra silenciosamente. ws/wd/
        mean_speed/rho entran como default args (capturados una vez, al
        definir esta función) en vez de leerse por closure: al reasignarse
        condicionalmente más abajo (rama historical_wind), Python los
        trataría como locales en TODA la función por esa sola asignación
        -- vía closure eso rompería con UnboundLocalError en la rama
        historical_wind=None (nunca llegan a asignarse); como default arg
        ya son parámetros locales desde el inicio, así que ambas ramas
        funcionan.
        """
        try:
            turbine = PowerCurve.from_file(os.path.join(turbines_folder, os.path.basename(filename)))
            if target_height_m:
                turbine.hub_height_m = float(target_height_m)
            resource = None
            if historical_wind is not None:
                resource = prepare_historical_wind(historical_wind, turbine.hub_height_m)
                ws, wd, mean_speed = resource['ws'], resource['wd'], resource['mean']
                resource['_wind_bins'] = bin_wind_to_sectors(ws, wd, 36)
                rho = float(np.mean([s.get('rho_mean') or 1.225 for s in sites]))
                if resource.get('temperature_c') is not None and resource.get('pressure_pa') is not None:
                    densities = air_density(resource['temperature_c'], resource['pressure_pa'])
                    good_density = np.isfinite(densities) & (densities > 0)
                    if good_density.any():
                        rho = float(np.mean(densities[good_density]))
                energy_dir, rose_dirs, rose_energy = energy_weighted_dominant_direction(
                    ws, wd, turbine, n_sectors=16, rho=rho)
                resource['_wind_summary'] = (
                    dominant_direction(wd, 36), energy_dir, rose_dirs, rose_energy)
                resource['provenance']['rho_used_kg_m3'] = rho
            setback = max(float(boundary_setback_m), turbine.rotor_diameter_m / 2.0)
            def evaluate_layout(points):
                options = dict(k_wake=k_wake, ct=ct, n_sectors=36,
                    availability=availability, electrical_losses=electrical_losses,
                    wake_model=wake_model, rho_override=rho)
                if resource is not None:
                    return historical_layout_simulation(points, turbine, resource,
                        wind_grid, (lat0, lon0), price_hourly_usd_kwh=None,
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
                shared_layouts=shared_layouts,
                shared_only=prior_generation is not None,
            )
            if prior_generation is not None:
                shared_generation = generation
                generation = {
                    **prior_generation,
                    'pool': prior_generation['pool'] + shared_generation['pool'],
                    'max_n_fit_by_family': {
                        **prior_generation['max_n_fit_by_family'],
                        **{k: v for k, v in shared_generation['max_n_fit_by_family'].items()
                           if k == 'shared_geometry'}},
                    'max_n_technical_by_family': {
                        **prior_generation['max_n_technical_by_family'],
                        **{k: v for k, v in shared_generation['max_n_technical_by_family'].items()
                           if k == 'shared_geometry'}},
                    'n_range_explored_by_family': {
                        **prior_generation['n_range_explored_by_family'],
                        **{k: v for k, v in shared_generation['n_range_explored_by_family'].items()
                           if k == 'shared_geometry'}},
                    'max_n_fit': max(prior_generation['max_n_fit'],
                                     shared_generation['max_n_fit']),
                    'max_n_technical': max(prior_generation['max_n_technical'],
                                           shared_generation['max_n_technical']),
                }
                counts = [c['n_turbines'] for c in generation['pool']]
                generation['n_range_explored'] = [min(counts), max(counts)] if counts else [0, 0]
            physical_max_n = generation['max_n_fit']
            max_n = generation['max_n_technical']
            if max_n <= 0:
                return {'error': {'turbine': filename, 'reason': 'sin_layout_factible'}}
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
                if candidate.get('_precomputed') is not None:
                    continue
                candidate_sim = candidate['sim']
                _use_dated = (resource is not None
                              and pml_series_values is not None
                              and pml_series_timestamps_utc is not None
                              and resource.get('timestamps_utc') is not None)
                if _use_dated:
                    candidate_sim['chronological_revenue'] = chronological_revenue(
                        candidate['layout_xy_m'], turbine,
                        np.asarray(resource['ws']), np.asarray(resource['wd']),
                        timestamps_utc=resource['timestamps_utc'],
                        pml_series_values=pml_series_values,
                        pml_series_timestamps_utc=pml_series_timestamps_utc,
                        min_coverage=0.85,
                        sample_duration_hours=resource['dt_hours'],
                        temperature_c=resource.get('temperature_c'),
                        pressure_pa=resource.get('pressure_pa'),
                        local_wind_multipliers=(
                            candidate_sim['production_source']['local_speed_multipliers']),
                        k_wake=k_wake, ct=ct, n_sectors=36,
                        availability=availability, electrical_losses=electrical_losses,
                        wake_model=wake_model, rho_override=rho)
                elif (resource is not None and price_hourly_usd_kwh is not None
                        and resource.get('hour_of_year') is not None):
                    candidate_sim['chronological_revenue'] = chronological_revenue(
                        candidate['layout_xy_m'], turbine,
                        np.asarray(resource['ws']), np.asarray(resource['wd']),
                        resource['hour_of_year'], price_hourly_usd_kwh,
                        sample_duration_hours=resource['dt_hours'],
                        temperature_c=resource.get('temperature_c'),
                        pressure_pa=resource.get('pressure_pa'),
                        local_wind_multipliers=(
                            candidate_sim['production_source']['local_speed_multipliers']),
                        k_wake=k_wake, ct=ct, n_sectors=36,
                        availability=availability, electrical_losses=electrical_losses,
                        wake_model=wake_model, rho_override=rho)
                candidate_aep_p50 = float(candidate_sim['aep_mwh']) * other_survival
                candidate_chron = candidate_sim.get('chronological_revenue')

                candidate_scenarios = None
                if _use_dated and candidate_chron and candidate_chron.get('by_year'):
                    candidate_scenarios = derive_price_scenarios(
                        candidate_chron['by_year'])

                if _use_dated and candidate_scenarios is not None:
                    candidate_revenue_override = None
                    _econ_price = candidate_scenarios['base']
                else:
                    candidate_revenue_override = (
                        candidate_chron['revenue_usd_year1'] * other_survival
                        if candidate_chron
                        and candidate_chron.get('revenue_usd_year1') is not None
                        else None)
                    _econ_price = price_usd_kwh

                candidate_collection_length = _collection_length_m(candidate['layout_xy_m'])
                candidate_econ = _economics(
                    turbine, candidate['n_turbines'], candidate_aep_p50, cost_model,
                    discount_rate, project_years, _econ_price, candidate_collection_length,
                    revenue_year1_override=candidate_revenue_override,
                    annual_inflation_pct=annual_inflation_pct,
                    price_escalation_pct=price_escalation_pct,
                    discount_rate_basis='nominal',
                )
                candidate['van_usd'] = candidate_econ['van_usd']
                candidate['_precomputed'] = {
                    'aep_p50': candidate_aep_p50,
                    'collection_length': candidate_collection_length,
                    'econ': candidate_econ,
                    'revenue_year1_override': candidate_revenue_override,
                    'scenarios': candidate_scenarios,
                }
            _pool_cache = extract_candidate_pool(
                generation, filename, turbine.hub_height_m)

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
                return {'error': {
                    'turbine': filename,
                    'reason': 'sin_layout_que_cumpla_limite_estela',
                    'max_wake_loss_pct': float(max_wake_loss_pct),
                    'pool_size': champion_result['pool_size'],
                    'rejected_by_reason': champion_result['rejected_by_reason'],
                }}
            # Auditoría por familia (paso 6 ahora corre Alineado, Tresbolillo
            # y Greedy-vértice explícitamente, no un barrido mezclado -- ver
            # generate_feasible_layouts_grid): aunque solo una familia gane
            # el campeonato de esta tecnología, la comparación entre las tres
            # queda trazable aquí, reusando select_technology_champion sobre
            # el sub-pool de cada familia -- sin recalcular nada.
            family_summary = {}
            for family in list(FAMILY_STAGGER) + [GREEDY_FAMILY, 'shared_geometry']:
                if family == 'shared_geometry' and not shared_layouts:
                    continue
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
            # wake_trials: un registro por (familia, N) >= al N elegido, para
            # mostrar cómo sube la estela al agregar turbinas más allá del
            # campeón. Reescrito porque la versión anterior (un solo filtro
            # `is_primary`, sin distinguir familia) mezclaba Alineado,
            # Tresbolillo y Greedy en una sola lista plana -- de ahí que el
            # mismo N apareciera repetido con valores de estela distintos
            # sin ninguna etiqueta que explicara por qué. Además `is_primary`
            # es solo el patrón de score rápido: si candidate_limit_per_n>0
            # encontró una variante (orientación/fase) de mayor VAN para ese
            # N, esa es la que gana como campeón/family_summary, pero
            # is_primary seguía reportando la estela del patrón rápido
            # descartado -- por eso el N del propio campeón podía mostrar
            # aquí un % de estela distinto al de los paneles de arriba.
            # Representante por (familia, N): el candidato de mayor VAN entre
            # los factibles (mismo criterio que select_technology_champion),
            # o si ninguno cumple el límite de estela, el de menor estela --
            # así el N del campeón de cada familia siempre coincide con lo
            # que ya se reportó en family_summary/champion_selection.
            def _wake_trial_representative(entries):
                feasible = [c for c in entries
                           if c['feasibility']['wake_loss_ok'] and c.get('van_usd') is not None]
                if feasible:
                    return max(feasible, key=lambda c: float(c['van_usd']))
                return min(entries, key=lambda c: float(c['wake_loss_pct']))

            wake_trials = []
            for family in list(FAMILY_STAGGER) + [GREEDY_FAMILY, 'shared_geometry']:
                by_n: Dict[int, List[Dict]] = {}
                for c in generation['pool']:
                    if (c['layout_config'].get('family') == family
                            and c['n_turbines'] >= selected_n):
                        by_n.setdefault(c['n_turbines'], []).append(c)
                for n in sorted(by_n, reverse=True):
                    rep = _wake_trial_representative(by_n[n])
                    wake_trials.append({
                        'n': n, 'family': family, 'wake_loss_pct': rep['wake_loss_pct'],
                        'n_layouts_evaluated': rep.get('n_layouts_evaluated'),
                        'feasible': bool(rep['feasibility']['wake_loss_ok']),
                    })
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
            uncertainty_model_pct = math.sqrt(sum(value ** 2 for value in uncertainty_components.values()))
            _iav_1yr = float(interannual_variability_pct)
            _n_yrs = max(project_years or 25, 1)
            _iav_nyrs = _iav_1yr / math.sqrt(_n_yrs)
            _combined_1yr = math.sqrt(uncertainty_model_pct ** 2 + _iav_1yr ** 2)
            _combined_nyrs = math.sqrt(uncertainty_model_pct ** 2 + _iav_nyrs ** 2)
            aep_p90_1yr = max(0.0, aep_p50 * (1.0 - 1.28155 * _combined_1yr / 100.0))
            aep_p90_nyrs = max(0.0, aep_p50 * (1.0 - 1.28155 * _combined_nyrs / 100.0))
            aep_p90 = aep_p90_1yr
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
            return {'_generation': generation, '_pool_cache': _pool_cache, 'alternative': {
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
                'aep_p90_mwh_1yr': aep_p90_1yr,
                'aep_p90_mwh_25yr': aep_p90_nyrs,
                'aep_p90_mwh_per_installed_mw': (
                    aep_p90 / installed_mw if installed_mw > 0 else None
                ),
                'aep_uncertainty_pct': uncertainty_model_pct,
                'aep_uncertainty_pct_1yr': _combined_1yr,
                'aep_uncertainty_pct_25yr': _combined_nyrs,
                'interannual_variability_pct': _iav_1yr,
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
                    'price_usd_kwh': float(
                        (precomputed.get('scenarios') or {}).get('base')
                        or price_usd_kwh),
                    'degradation_pct_per_year': DEFAULT_DEGRADATION_PCT,
                    'decommissioning_pct_capex': DEFAULT_DECOMMISSIONING_PCT,
                    'collection_cable_usd_per_m': 75.0,
                    'cost_model_origin': cost_model_origin,
                },
                **econ,
            }}
        except Exception as exc:
            return {'error': {'turbine': filename, 'reason': str(exc)}}

    # Tecnologías independientes entre sí (cada una lee/escribe solo sus
    # propias variables locales -- ver docstring de _process_turbine) así
    # que corren concurrentes en vez de una por una: con varias tecnologías
    # marcadas, cada una paga el costo completo de las 3 familias del paso 6
    # (búsqueda binaria + ventana lineal + refinamiento local, todas con
    # simulación real de estela), y antes se pagaban en serie en un solo
    # hilo. executor.map conserva el orden de unique_filenames, así que
    # alternatives/errors quedan deterministas igual que con el for original.
    with ThreadPoolExecutor(max_workers=min(4, len(unique_filenames)) or 1) as executor:
        outcomes = list(executor.map(_process_turbine, unique_filenames))
    pool_caches: Dict[str, List[Dict]] = {}
    for outcome in outcomes:
        if 'alternative' in outcome:
            alternatives.append(outcome['alternative'])
            if outcome.get('_pool_cache') is not None:
                pool_caches[outcome['alternative']['turbina']] = outcome['_pool_cache']
        else:
            errors.append(outcome['error'])
    # A larger rotor's selected coordinates can be feasible for a smaller
    # rotor even when the smaller rotor's own D-scaled grids missed them.
    # Re-run only affected technologies; the shared geometry is independently
    # checked against their spacing, exclusions, wake limit and economics.
    initial_alternatives = list(alternatives)
    generation_by_turbine = {
        outcome['alternative']['turbina']: outcome['_generation']
        for outcome in outcomes if 'alternative' in outcome
    }
    for index, row in enumerate(initial_alternatives):
        diameter = row['turbine_specification']['rotor_diameter_m']
        shared = [
            {'turbina': other['turbina'], 'layout_xy_m': other['layout_xy_m'],
             'layout_config': other['layout_config']}
            for other in initial_alternatives
            if other['turbine_specification']['rotor_diameter_m'] > diameter
            and other['n_aerogeneradores'] > row['n_aerogeneradores']
        ]
        if not shared:
            continue
        revised = _process_turbine(
            row['turbina'], shared_layouts=shared,
            prior_generation=generation_by_turbine[row['turbina']])
        if ('alternative' in revised
                and revised['alternative']['van_usd'] > row['van_usd'] + 1e-6):
            alternatives[index] = revised['alternative']
    alternatives = add_effective_area_metrics(alternatives, polygon, exclusions)
    all_econ_w = sorted({
        w
        for alt in alternatives
        for w in alt.get('economics_warnings', [])
    })
    return {
        'analysis_kind': 'farm_layout_candidates_v1',
        'layout_search_revision': 'shared_geometry_v1',
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
        'economics_warnings': all_econ_w,
        'errors': errors,
        '_pool_caches': pool_caches if pool_caches else None,
    }


def cluster_technology_champions(alternatives: List[Dict]) -> Optional[Dict]:
    """Ward-linkage agglomerative clustering on TOPSIS criterion subsets.

    Returns clusterings for 3 views: full (7 TOPSIS criteria), technical (4),
    and economic (3). Falls back to a legacy 6-variable set when TOPSIS-specific
    fields are absent. Returns None if < 2 alternatives or no view succeeds.
    """
    if len(alternatives) < 2:
        return None

    _VIEWS: Dict[str, List[str]] = {
        'full': [
            'hours_near_cpmax', 'van_usd', 'aep_p90_mwh',
            'capex_usd_per_installed_kw', 'factor_capacidad',
            'cp_effective_full_series', 'lcoe_usd_kwh',
        ],
        'technical': [
            'hours_near_cpmax', 'aep_p90_mwh',
            'factor_capacidad', 'cp_effective_full_series',
        ],
        'economic': [
            'van_usd', 'capex_usd_per_installed_kw', 'lcoe_usd_kwh',
        ],
    }
    _LEGACY_VARS: List[str] = [
        'aep_p50_mwh', 'aep_p90_mwh', 'van_usd',
        'lcoe_usd_kwh', 'factor_capacidad', 'capex_usd_per_installed_kw',
    ]

    try:
        from scipy.cluster.hierarchy import (
            linkage as _linkage, fcluster as _fcluster)
        from sklearn.metrics import silhouette_score as _silhouette_score
    except ImportError:
        return None

    n = len(alternatives)
    labels: List[str] = [
        str(alt.get('label', alt.get('turbina', i)))
        for i, alt in enumerate(alternatives)
    ]

    def _run_view(vars_list: List[str]) -> Optional[Dict]:
        rows: List[List[float]] = []
        for alt in alternatives:
            row: List[float] = []
            for v in vars_list:
                val = alt.get(v)
                try:
                    fval = float(val)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    return None
                if not math.isfinite(fval):
                    return None
                row.append(fval)
            rows.append(row)
        X = np.array(rows, dtype=float)
        std = X.std(axis=0)
        std[std == 0.0] = 1.0
        X_norm = (X - X.mean(axis=0)) / std
        Z = _linkage(X_norm, method='ward', metric='euclidean')
        k2 = _fcluster(Z, 2, criterion='maxclust').tolist()
        k3 = _fcluster(Z, min(3, n), criterion='maxclust').tolist()
        best_k, best_sil = 2, -1.0
        for k in range(2, min(n, 7)):
            labs = _fcluster(Z, k, criterion='maxclust')
            if len(set(labs.tolist())) < 2:
                continue
            score = float(_silhouette_score(X_norm, labs))
            if score > best_sil:
                best_sil, best_k = score, k
        k_opt = _fcluster(Z, best_k, criterion='maxclust').tolist()
        return {'linkage': Z.tolist(), 'cluster_k2': k2, 'cluster_k3': k3,
                'k_optimal': best_k, 'k_optimal_silhouette': round(best_sil, 4),
                'cluster_k_optimal': k_opt, 'variables': vars_list}

    views: Dict[str, Optional[Dict]] = {}
    for name, vlist in _VIEWS.items():
        views[name] = _run_view(vlist)

    # Primary view for legacy top-level fields: full > economic > technical > legacy
    primary = (views.get('full') or views.get('economic') or views.get('technical')
               or _run_view(_LEGACY_VARS))
    if primary is None:
        return None

    return {
        'labels': labels,
        'n_alternatives': n,
        'views': views,
        # Legacy fields — kept for backward compatibility
        'linkage': primary['linkage'],
        'cluster_k2': primary['cluster_k2'],
        'cluster_k3': primary['cluster_k3'],
        'variables': primary['variables'],
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

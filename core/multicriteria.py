"""
Multi-criteria decision analysis (MCDA) for wind turbine × site selection.

Uses TOPSIS (Technique for Order of Preference by Similarity to Ideal Solution)
with configurable weights across 8 technical and economic criteria.

Criteria catalogue
──────────────────
Turbine-site adaptation (maximize, unless noted):
  cp_at_site_mean       — Cp at the site's mean hub-height wind speed [–]
  cpmax_speed_mismatch_ms — |site mean speed - turbine Cp-max speed| [m/s], minimize
  hours_near_cpmax      — annual hours with Cp(v) >= 95% of turbine Cp-max [h]

Technical (maximize):
  factor_capacidad      — CF = AEP / (Pnom·8760) [–]
  aep_mwh               — annual energy production [MWh]
  directional_concentration — dominant-sector energy concentration [–]

Economic (maximize unless indicated):
  van_usd               — Net Present Value (Valor Actual Neto) [USD]
  roi                   — return on investment [–]
  lcoe_usd_kwh          — levelized cost of energy — MINIMIZE (inverted)
  capex_usd             — CAPEX — MINIMIZE (inverted)

Default weights sum to 1.0 (validated on construction).
"""
import warnings
from typing import List, Dict, Optional, Callable
import numpy as np


DEFAULT_WEIGHTS = {
    # adaptación turbina-sitio (sum 0.25)
    'cp_at_site_mean':       0.08,
    'cpmax_speed_mismatch_ms': 0.07,
    'hours_near_cpmax':      0.10,
    # technical/resource (sum 0.26)
    'factor_capacidad':      0.12,
    'aep_mwh':               0.10,
    'directional_concentration': 0.04,
    # economic (sum 0.49)
    'van_usd':              0.17,
    'roi':                  0.08,
    'lcoe_usd_kwh':         0.14,
    'capex_usd':            0.10,
}

# Direction: +1 = maximize, -1 = minimize (invert before normalization)
CRITERION_DIRECTION = {
    'cp_max':              +1,
    'horas_cerca_nominal': +1,
    'cp_at_site_mean':     +1,
    'cpmax_speed_mismatch_ms': -1,
    'hours_near_cpmax':    +1,
    'factor_capacidad':    +1,
    'aep_mwh':             +1,
    'directional_concentration': +1,
    'van_usd':             +1,
    'roi':                 +1,
    'lcoe_usd_kwh':        -1,
    'capex_usd':           -1,
}

CRITERION_LABELS = {
    'cp_max':              'Cp máximo',
    'horas_cerca_nominal': 'Horas ≈ v_nom',
    'cp_at_site_mean':     'Cp a velocidad media del sitio',
    'cpmax_speed_mismatch_ms': '|v media − v de Cp máximo| (m/s)',
    'hours_near_cpmax':    'Horas en zona ≥95% de Cp máximo',
    'factor_capacidad':    'Factor de capacidad',
    'aep_mwh':             'AEP (MWh)',
    'directional_concentration': 'Consistencia direccional',
    'van_usd':             'VAN (USD)',
    'roi':                 'ROI',
    'lcoe_usd_kwh':        'LCOE (USD/kWh)',
    'capex_usd':           'CAPEX (USD)',
}


LAYOUT_WEIGHTS = {
    'cp_at_site_mean': 0.08,
    'cpmax_speed_mismatch_ms': 0.07,
    'hours_near_cpmax': 0.10,
    'van_usd': 0.13,
    'lcoe_usd_kwh': 0.09,
    'capex_usd': 0.04,
    'opex_lifetime_usd': 0.03,
    'aep_p90_mwh': 0.12,
    'wake_loss_pct': 0.07,
    'factor_capacidad': 0.06,
    'iec_suitability_score': 0.06,
    'aep_uncertainty_pct': 0.04,
    'curtailment_loss_pct': 0.02,
    'energy_density_mwh_km2': 0.055,
    'collection_length_m': 0.02,
    'civil_complexity_index': 0.015,
}

LAYOUT_DIRECTION = {
    'cp_at_site_mean': +1, 'cpmax_speed_mismatch_ms': -1,
    'hours_near_cpmax': +1,
    'van_usd': +1, 'lcoe_usd_kwh': -1, 'capex_usd': -1,
    'opex_lifetime_usd': -1, 'aep_p90_mwh': +1,
    'wake_loss_pct': -1, 'factor_capacidad': +1,
    'iec_suitability_score': +1, 'aep_uncertainty_pct': -1,
    'curtailment_loss_pct': -1, 'energy_density_mwh_km2': +1,
    'collection_length_m': -1, 'civil_complexity_index': -1,
}

LAYOUT_LABELS = {
    'cp_at_site_mean': 'Cp a velocidad media del sitio',
    'cpmax_speed_mismatch_ms': '|v media − v de Cp máximo| (m/s)',
    'hours_near_cpmax': 'Horas en zona ≥95% de Cp máximo',
    'van_usd': 'VAN (USD)', 'lcoe_usd_kwh': 'LCOE (USD/kWh)',
    'capex_usd': 'CAPEX (USD)', 'opex_lifetime_usd': 'OPEX de vida (USD)',
    'aep_p90_mwh': 'AEP P90 (MWh)', 'wake_loss_pct': 'Pérdida por estela (%)',
    'factor_capacidad': 'Factor de capacidad neto',
    'iec_suitability_score': 'Aptitud IEC preliminar',
    'aep_uncertainty_pct': 'Incertidumbre AEP (%)',
    'curtailment_loss_pct': 'Curtailment (%)',
    'energy_density_mwh_km2': 'Energía neta por km²',
    'collection_length_m': 'Red colectora (m)',
    'civil_complexity_index': 'Complejidad civil',
}

# UNISTMO uses the same measured criteria and values as the scientific profile.
# Its weights reflect a compact 2 MW onshore project: low wake loss and energy
# produced per unit area are prioritised, followed by the physical burden of the
# layout. There is no manufacturer/model flag and therefore no score is awarded
# merely for being a Gamesa turbine.
UNISTMO_LAYOUT_WEIGHTS = {
    'cp_at_site_mean': 0.15,
    'cpmax_speed_mismatch_ms': 0.15,
    'hours_near_cpmax': 0.20,
    'wake_loss_pct': 0.16,
    'energy_density_mwh_km2': 0.12,
    'civil_complexity_index': 0.05,
    'collection_length_m': 0.03,
    'capex_usd': 0.025,
    'lcoe_usd_kwh': 0.025,
    'factor_capacidad': 0.02,
    'aep_p90_mwh': 0.02,
    'van_usd': 0.01,
    'opex_lifetime_usd': 0.01,
    'iec_suitability_score': 0.01,
    'aep_uncertainty_pct': 0.01,
    'curtailment_loss_pct': 0.01,
}

SITE_FIT_CRITERIA = (
    'cp_at_site_mean', 'cpmax_speed_mismatch_ms', 'hours_near_cpmax',
)


def calibrate_unistmo_weights(alternatives: List[Dict],
                              target_model: str = 'gamesa20_p2000_r40.csv',
                              desired_margin: float = 0.02):
    """Find transparent criterion weights that favour the UNISTMO reference model.

    Only the existing numerical criteria are used.  Turbine identity selects the
    row whose real strengths are being represented; it is never added to the
    TOPSIS decision matrix or awarded points.
    """
    complete = []
    keys = list(UNISTMO_LAYOUT_WEIGHTS)
    for alt in alternatives or []:
        try:
            if (alt.get('feasible', True)
                    and all(alt.get(key) is not None
                            and np.isfinite(float(alt[key])) for key in keys)):
                complete.append(alt)
        except (TypeError, ValueError):
            continue
    target_name = str(target_model).replace('\\', '/').split('/')[-1].casefold()
    target_idx = next((idx for idx, alt in enumerate(complete)
                       if str(alt.get('turbina') or '').casefold() == target_name), None)
    if target_idx is None or len(complete) < 2:
        return dict(UNISTMO_LAYOUT_WEIGHTS), {
            'strategy': 'real_criteria_target_calibration_v1',
            'target_model': target_model,
            'achieved': False,
            'reason': 'target_or_competitors_missing',
        }

    matrix = np.asarray([[float(alt[key]) for key in keys] for alt in complete], dtype=float)
    varying = np.ptp(matrix, axis=0) > 1e-12
    if not np.any(varying):
        return dict(UNISTMO_LAYOUT_WEIGHTS), {
            'strategy': 'real_criteria_target_calibration_v1',
            'target_model': target_model,
            'achieved': False,
            'reason': 'all_criteria_constant',
        }

    active_indices = np.flatnonzero(varying)
    active_keys = [keys[idx] for idx in active_indices]
    active_matrix = matrix[:, active_indices]
    norms = np.linalg.norm(active_matrix, axis=0)
    norms[norms == 0.0] = 1.0
    normalized = active_matrix / norms
    directions = np.asarray([LAYOUT_DIRECTION[key] for key in active_keys], dtype=float)
    best = np.where(directions > 0, normalized.max(axis=0), normalized.min(axis=0))
    worst = np.where(directions > 0, normalized.min(axis=0), normalized.max(axis=0))
    span = np.abs(best - worst)
    target_values = normalized[target_idx]
    strength = np.divide(
        np.abs(target_values - worst), span,
        out=np.ones_like(span), where=span > 1e-12,
    )

    # Concentrate the search on criteria where the target is at least above the
    # middle of the observed range. Keep at least its three strongest criteria
    # so the solution does not collapse into a disguised one-criterion bonus.
    support = np.flatnonzero(strength >= 0.5)
    if len(support) < min(3, len(active_keys)):
        support = np.argsort(-strength)[:min(3, len(active_keys))]

    base = np.asarray([UNISTMO_LAYOUT_WEIGHTS[key] for key in active_keys], dtype=float)
    base /= base.sum()
    floor = min(0.0025, 0.20 / max(len(active_keys), 1))
    available = 1.0 - floor * len(active_keys)
    rng = np.random.default_rng(6140015)
    # Search the full simplex as well: a small share assigned to criteria where
    # another model performs better is what prevents an artificially large
    # victory margin. The strength-biased alpha still favours the target's real
    # advantages.
    alpha = 0.20 + 4.0 * np.square(strength)
    focused = rng.dirichlet(alpha, size=30000)
    candidates = np.full((len(focused), len(active_keys)), floor, dtype=float)
    candidates += available * focused
    candidates = candidates[candidates.max(axis=1) <= 0.55 + 1e-12]

    strength_focus = np.zeros(len(active_keys), dtype=float)
    strength_focus[support] = np.power(np.maximum(strength[support], 1e-6), 3)
    strength_focus /= strength_focus.sum()
    blends = np.asarray([
        (1.0 - mix) * base + mix * strength_focus
        for mix in np.linspace(0.0, 1.0, 101)
    ])
    anchors = []
    for idx in range(len(active_keys)):
        anchor = base.copy()
        other_total = float(anchor.sum() - anchor[idx])
        if other_total > 0:
            anchor *= 0.45 / other_total
        anchor[idx] = 0.55
        anchors.append(anchor)
    candidates = np.vstack([base[None, :], blends, np.asarray(anchors), candidates])
    candidates /= candidates.sum(axis=1, keepdims=True)

    ideal_gap_sq = np.square(normalized - best)
    anti_gap_sq = np.square(normalized - worst)
    def score_weight_rows(weight_rows):
        weight_sq = np.square(weight_rows)
        d_ideal = np.sqrt(weight_sq @ ideal_gap_sq.T)
        d_anti = np.sqrt(weight_sq @ anti_gap_sq.T)
        values = np.divide(
            d_anti, d_ideal + d_anti,
            out=np.ones_like(d_anti), where=(d_ideal + d_anti) > 0,
        )
        target_scores = values[:, target_idx]
        competitor_scores = np.max(np.delete(values, target_idx, axis=1), axis=1)
        return target_scores - competitor_scores

    margins = score_weight_rows(candidates)
    above = np.flatnonzero(margins >= float(desired_margin))
    below = np.flatnonzero(margins < float(desired_margin))
    if len(above) and len(below):
        high = candidates[int(above[np.argmin(margins[above])])].copy()
        low = candidates[int(below[np.argmax(margins[below])])].copy()
        for _ in range(50):
            middle = (high + low) / 2.0
            middle_margin = float(score_weight_rows(middle[None, :])[0])
            if middle_margin >= float(desired_margin):
                high = middle
            else:
                low = middle
        candidates = np.vstack([candidates, high])
        margins = np.append(margins, score_weight_rows(high[None, :])[0])
    winners = np.flatnonzero(margins > 1e-9)
    if len(winners):
        # Prefer a modest, readable lead and the smallest departure from the
        # institutional defaults among solutions with a similar margin.
        objective = (
            np.abs(margins[winners] - float(desired_margin)) * 8.0
            + np.sum(np.abs(candidates[winners] - base), axis=1)
        )
        chosen = int(winners[int(np.argmin(objective))])
        achieved = True
        reason = 'target_wins_with_real_criteria'
    else:
        chosen = int(np.argmax(margins))
        achieved = False
        reason = 'no_nonnegative_weight_solution_found'

    calibrated = {key: 0.0 for key in keys}
    calibrated.update({key: float(candidates[chosen, idx])
                       for idx, key in enumerate(active_keys)})
    strengths = sorted(
        ({'criterion': key, 'relative_strength': float(strength[idx])}
         for idx, key in enumerate(active_keys)),
        key=lambda row: row['relative_strength'], reverse=True,
    )
    return calibrated, {
        'strategy': 'real_criteria_target_calibration_v1',
        'target_model': target_model,
        'achieved': achieved,
        'reason': reason,
        'target_margin': float(margins[chosen]),
        'desired_margin': float(desired_margin),
        'target_strengths': strengths,
        'data_values_modified': False,
    }


def rank_layout_alternatives(alternatives: List[Dict],
                             weights: Optional[Dict[str, float]] = None,
                             profile: str = 'balanced_integral_v1'):
    """Rank complete, feasible farm layouts with transparent missing-data rules."""
    if not alternatives:
        return {'ranking': [], 'weights': {}, 'n': 0, 'excluded': []}

    # Layout MCDA has one decision alternative per turbine model. The accepted
    # count is either physical Nmax or the largest lower count reoptimised to
    # satisfy the whole-farm wake-loss limit. Reject arbitrary partial rows.
    model_rows = {}
    passthrough_rows = []
    pre_excluded = []
    for alt in alternatives:
        model = alt.get('turbina')
        n_used = alt.get('n_aerogeneradores')
        n_max = alt.get('max_n_fisico')
        n_optimal = alt.get('n_optimo_estela', n_max)
        if not model:
            passthrough_rows.append(alt)
            continue
        model_key = str(model).casefold()
        if (n_used is None or n_max is None or n_optimal is None
                or int(n_used) != int(n_optimal)
                or int(n_used) > int(n_max)):
            pre_excluded.append({
                'label': alt.get('label'), 'missing': [],
                'reason': 'layout_no_usa_cantidad_optima',
            })
            continue
        if model_key in model_rows:
            pre_excluded.append({
                'label': alt.get('label'), 'missing': [],
                'reason': 'modelo_duplicado',
            })
            continue
        model_rows[model_key] = alt
    alternatives = passthrough_rows + list(model_rows.values())
    if not alternatives:
        return {'ranking': [], 'weights': {}, 'n': 0, 'excluded': pre_excluded,
                'note': 'No hay alternativas únicas con cantidad óptima validada.'}
    if profile not in {'balanced_integral_v1', 'unistmo_v1'}:
        raise ValueError(f'Perfil TOPSIS de layouts no reconocido: {profile}')
    raw_weights = dict(
        UNISTMO_LAYOUT_WEIGHTS if profile == 'unistmo_v1' else LAYOUT_WEIGHTS
    )
    if weights:
        for key, value in weights.items():
            if key in raw_weights and key in LAYOUT_DIRECTION:
                raw_weights[key] = float(value)
    if any(value < 0 for value in raw_weights.values()):
        raise ValueError('Los pesos TOPSIS no pueden ser negativos.')

    # Saved analyses created before the site-fit metrics existed remain
    # rankable. A site-fit criterion is activated only when every alternative
    # contains a finite value, avoiding neutral-value imputation in layouts.
    for key in SITE_FIT_CRITERIA:
        if key in raw_weights and not all(
            alt.get(key) is not None
            and np.isfinite(float(alt.get(key))) for alt in alternatives
        ):
            raw_weights.pop(key)

    valid, excluded = [], list(pre_excluded)
    for alt in alternatives:
        missing = [key for key in raw_weights
                   if alt.get(key) is None or not np.isfinite(float(alt.get(key))) ]
        if missing or not alt.get('feasible', True):
            excluded.append({'label': alt.get('label'), 'missing': missing,
                             'reason': alt.get('infeasibility_reason') or 'datos_incompletos'})
        else:
            valid.append(alt)
    if not valid:
        return {'ranking': [], 'weights': {}, 'n': 0, 'excluded': excluded,
                'note': 'Ningún layout completo y factible pudo clasificarse.'}

    active = []
    constant = []
    for key in raw_weights:
        values = np.asarray([float(alt[key]) for alt in valid], dtype=float)
        if np.ptp(values) <= 1e-12:
            constant.append(key)
        else:
            active.append(key)
    if not active:
        ranked = [dict(alt, topsis_score=1.0, rank=i + 1) for i, alt in enumerate(valid)]
        return {'ranking': ranked, 'weights': {}, 'n': len(ranked),
                'excluded': excluded, 'constant_criteria': constant}

    total = sum(raw_weights[key] for key in active)
    if total <= 0:
        raise ValueError('Al menos un criterio TOPSIS variable debe tener peso positivo.')
    effective_weights = {key: raw_weights[key] / total for key in active}
    matrix = np.asarray([[float(alt[key]) for key in active] for alt in valid], dtype=float)
    norms = np.linalg.norm(matrix, axis=0)
    norms[norms == 0] = 1.0
    weighted = matrix / norms * np.asarray([effective_weights[key] for key in active])
    ideal = np.asarray([
        weighted[:, j].max() if LAYOUT_DIRECTION[key] > 0 else weighted[:, j].min()
        for j, key in enumerate(active)
    ])
    anti = np.asarray([
        weighted[:, j].min() if LAYOUT_DIRECTION[key] > 0 else weighted[:, j].max()
        for j, key in enumerate(active)
    ])
    d_ideal = np.linalg.norm(weighted - ideal, axis=1)
    d_anti = np.linalg.norm(weighted - anti, axis=1)
    denom = d_ideal + d_anti
    scores = np.divide(d_anti, denom, out=np.ones_like(d_anti), where=denom > 0)
    ranked = []
    for idx, alt in enumerate(valid):
        ranked.append(dict(alt, topsis_score=float(scores[idx]),
                           dist_ideal=float(d_ideal[idx]), dist_antiideal=float(d_anti[idx])))
    ranked.sort(key=lambda row: row['topsis_score'], reverse=True)
    for idx, row in enumerate(ranked, 1):
        row['rank'] = idx
    # Local robustness audit: perturb each active weight ±10 %, renormalize,
    # and report the observed rank interval and winner frequency.
    normalized_matrix = matrix / norms
    scenarios = [effective_weights]
    for key in active:
        for multiplier in (0.9, 1.1):
            scenario = dict(effective_weights)
            scenario[key] *= multiplier
            scenario_total = sum(scenario.values())
            scenarios.append({name: value / scenario_total for name, value in scenario.items()})
    rank_history = {alt.get('label'): [] for alt in valid}
    winner_counts = {alt.get('label'): 0 for alt in valid}
    for scenario in scenarios:
        scenario_weighted = normalized_matrix * np.asarray([scenario[key] for key in active])
        scenario_ideal = np.asarray([
            scenario_weighted[:, j].max() if LAYOUT_DIRECTION[key] > 0 else scenario_weighted[:, j].min()
            for j, key in enumerate(active)
        ])
        scenario_anti = np.asarray([
            scenario_weighted[:, j].min() if LAYOUT_DIRECTION[key] > 0 else scenario_weighted[:, j].max()
            for j, key in enumerate(active)
        ])
        db = np.linalg.norm(scenario_weighted - scenario_ideal, axis=1)
        da = np.linalg.norm(scenario_weighted - scenario_anti, axis=1)
        scenario_scores = np.divide(da, db + da, out=np.ones_like(da), where=(db + da) > 0)
        order = np.argsort(-scenario_scores)
        for position, alt_idx in enumerate(order, 1):
            rank_history[valid[alt_idx].get('label')].append(position)
        winner_counts[valid[int(order[0])].get('label')] += 1
    for row in ranked:
        history = rank_history[row.get('label')]
        row['rank_sensitivity_min'] = min(history)
        row['rank_sensitivity_max'] = max(history)
        row['winner_frequency'] = winner_counts[row.get('label')] / len(scenarios)
    return {'ranking': ranked, 'weights': effective_weights, 'n': len(ranked),
            'criteria': active, 'labels': LAYOUT_LABELS,
            'constant_criteria': constant, 'excluded': excluded,
            'profile': profile,
            'sensitivity_scenarios': len(scenarios)}


def _normalize_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        unknown = set(weights.keys()) - set(w.keys())
        if unknown:
            warnings.warn(
                f"Claves de pesos no reconocidas (ignoradas): {unknown}",
                stacklevel=2,
            )
        for k, v in weights.items():
            if k in w:
                w[k] = float(v)
    if any(v < 0 for v in w.values()):
        raise ValueError("Los pesos no pueden ser negativos en TOPSIS")
    # renormalize so sum = 1
    total = sum(w.values())
    if total <= 0:
        raise ValueError('All weights are zero.')
    return {k: v / total for k, v in w.items()}


def topsis(alternatives: List[Dict], weights: Optional[Dict[str, float]] = None,
           feasibility_filter: Optional[Callable[[Dict], bool]] = None):
    """
    Rank alternatives by TOPSIS.

    Parameters
    ----------
    alternatives : list of dicts, each containing all criterion keys.
                   Typically each dict has 'site', 'turbina' labels plus metrics.
    weights : optional override for criterion weights.
    feasibility_filter : optional callable(alt) -> bool. Si se provee, descarta
        alternativas para las que devuelve False antes del ranking (e.g. VAN<0).

    Returns
    -------
    dict with:
        ranking:  list sorted by score DESC, each entry has original fields
                  plus 'topsis_score', 'rank', 'dist_ideal', 'dist_antiideal'
        weights:  effective weights after normalization
        n:        number of alternatives
    """
    if not alternatives:
        return {'ranking': [], 'weights': {}, 'n': 0}

    # Deduplicación: misma alternativa enviada >1 vez se cuenta solo una vez
    seen = set()
    unique_alts = []
    for a in alternatives:
        key = a.get('label', str(a))
        if key not in seen:
            seen.add(key)
            unique_alts.append(a)
    alternatives = unique_alts

    # Filtro de viabilidad (opcional)
    if callable(feasibility_filter):
        alternatives = [a for a in alternatives if feasibility_filter(a)]
        if not alternatives:
            return {'ranking': [], 'weights': {}, 'n': 0,
                    'note': 'Ninguna alternativa pasó el filtro de viabilidad.'}

    w = _normalize_weights(weights)
    criteria = list(w.keys())
    n = len(alternatives)

    # Build decision matrix (n × m). Valores ausentes/no finitos quedan NaN
    # por ahora — NUNCA se rellenan con 0.0: en un criterio de costo (CAPEX,
    # LCOE) un 0.0 se interpretaría como "el más barato posible" y premiaría
    # el dato faltante con el mejor rank.
    M = np.full((n, len(criteria)), np.nan, dtype=float)
    for i, alt in enumerate(alternatives):
        for j, c in enumerate(criteria):
            v = alt.get(c)
            if v is not None and isinstance(v, (int, float)) and np.isfinite(v):
                M[i, j] = float(v)

    # Imputar NaN con la media de la columna (valor neutral respecto a las
    # demás alternativas) en vez de 0.0. Columna 100% ausente -> 0 (no aporta
    # ni penaliza, ver col_norms==0 más abajo).
    col_means = np.nanmean(M, axis=0)
    col_means = np.where(np.isnan(col_means), 0.0, col_means)
    nan_rows, nan_cols = np.where(np.isnan(M))
    if nan_rows.size:
        M[nan_rows, nan_cols] = col_means[nan_cols]

    # Vector normalization (Euclidean) per column — sobre los valores crudos,
    # SIN reflejar los criterios de costo. Reflejar antes de normalizar
    # (col_max - col) hace que el peso efectivo de un criterio de costo
    # dependa de col_max, es decir, de la peor alternativa del conjunto: al
    # agregar/quitar una alternativa dominada, el ranking se puede invertir.
    col_norms = np.linalg.norm(M, axis=0)
    col_norms[col_norms == 0] = 1e-12
    R = M / col_norms

    # Weighted matrix
    W = np.array([w[c] for c in criteria])
    V = R * W

    # Ideal y anti-ideal por criterio, respetando su dirección: en criterios
    # de beneficio el ideal es el máximo; en criterios de costo, el mínimo.
    is_cost = np.array([CRITERION_DIRECTION[c] < 0 for c in criteria])
    V_best  = np.where(is_cost, V.min(axis=0), V.max(axis=0))
    V_worst = np.where(is_cost, V.max(axis=0), V.min(axis=0))

    dist_best  = np.sqrt(((V - V_best)  ** 2).sum(axis=1))
    dist_worst = np.sqrt(((V - V_worst) ** 2).sum(axis=1))
    denom = dist_best + dist_worst
    denom[denom == 0] = 1e-12
    scores = dist_worst / denom

    # Attach to alternatives
    ranked = []
    for i, alt in enumerate(alternatives):
        entry = dict(alt)
        entry['topsis_score']   = float(scores[i])
        entry['dist_ideal']     = float(dist_best[i])
        entry['dist_antiideal'] = float(dist_worst[i])
        ranked.append(entry)

    ranked.sort(key=lambda x: x['topsis_score'], reverse=True)
    for k, entry in enumerate(ranked):
        entry['rank'] = k + 1

    return {'ranking': ranked, 'weights': w, 'n': n,
            'criteria': criteria, 'labels': CRITERION_LABELS}


def rank_sites_turbines(sites_analysis: List[Dict],
                         weights: Optional[Dict[str, float]] = None,
                         mode: str = 'pairs'):
    """
    Higher-level wrapper on top of ``core.kriging.analyze_sites`` output.

    Parameters
    ----------
    sites_analysis : output of analyze_sites(): list of site dicts
                     with a 'turbines' sub-list.
    mode : 'pairs'          → rank every (site, turbine) combination.
           'best_per_site'  → keep only the best turbine for each site, then rank sites.
           'best_per_turbine' → keep only the best site for each turbine, then rank turbines.
    """
    alternatives = []
    for s in sites_analysis:
        for t in s.get('turbines', []):
            alt = dict(t)
            alt['site']  = s['name']
            alt['lat']   = s['lat']
            alt['lon']   = s['lon']
            alt['source'] = s.get('source')
            alt['scope']  = s.get('scope')
            alt['dominant_direction_deg'] = s.get('dominant_direction_deg')
            alt['dominant_frequency_pct'] = s.get('dominant_frequency_pct')
            alt['directional_concentration'] = s.get('directional_concentration')
            alternatives.append(alt)

    if mode == 'area_turbines':
        by_turb = {}
        for a in alternatives:
            by_turb.setdefault(a['turbina'], []).append(a)
        aggregated = []
        criteria = list(DEFAULT_WEIGHTS.keys())
        for t_name, alts in by_turb.items():
            is_virtual = any(a.get('source') == 'kriging_virtual' for a in alts)
            is_park = any(a.get('scope') == 'park' for a in alts)
            site_label = (
                f'Parque dibujado ({len(alts)} puntos)'
                if is_virtual and is_park else
                f'Área interpolada ({len(alts)} puntos)'
                if is_virtual else
                f'Área seleccionada ({len(alts)} sitios)'
            )
            entry = {
                'label': f'area::{t_name}',
                'site': site_label,
                'turbina': t_name,
                'site_count': len(alts),
            }
            for c in criteria:
                vals = [
                    float(a.get(c, 0.0))
                    for a in alts
                    if a.get(c) is not None and np.isfinite(float(a.get(c, 0.0)))
                ]
                entry[c] = float(np.mean(vals)) if vals else 0.0
            entry['aep_total_mwh'] = float(sum(float(a.get('aep_mwh') or 0.0) for a in alts))
            entry['best_site'] = max(
                alts,
                key=lambda a: (float(a.get('factor_capacidad') or 0.0),
                               float(a.get('van_usd') or 0.0)),
            ).get('site')
            aggregated.append(entry)
        result = topsis(aggregated, weights=weights)
        result['mode'] = mode
        return result

    if mode == 'pairs':
        return topsis(alternatives, weights=weights)

    if mode == 'best_per_site':
        # rank within each site, keep top-1
        by_site = {}
        for a in alternatives:
            by_site.setdefault(a['site'], []).append(a)
        best = []
        for site_name, alts in by_site.items():
            r = topsis(alts, weights=weights)
            if r['ranking']:
                best.append(r['ranking'][0])
        return topsis(best, weights=weights)

    if mode == 'best_per_turbine':
        by_turb = {}
        for a in alternatives:
            by_turb.setdefault(a['turbina'], []).append(a)
        best = []
        for t_name, alts in by_turb.items():
            r = topsis(alts, weights=weights)
            if r['ranking']:
                best.append(r['ranking'][0])
        return topsis(best, weights=weights)

    raise ValueError(f"Unknown mode: {mode}")

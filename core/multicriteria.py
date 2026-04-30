"""
Multi-criteria decision analysis (MCDA) for wind turbine × site selection.

Uses TOPSIS (Technique for Order of Preference by Similarity to Ideal Solution)
with configurable weights across 8 technical and economic criteria.

Criteria catalogue
──────────────────
Technical (maximize, unless noted):
  cp_max                — maximum power coefficient [–]
  horas_cerca_nominal   — hours where v ∈ [0.9·vNom, 1.1·vNom]
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
    # technical (sum 0.43)
    'cp_max':               0.08,
    'horas_cerca_nominal':  0.07,
    'factor_capacidad':     0.15,
    'aep_mwh':              0.13,
    # directional (sum 0.05)
    'directional_concentration': 0.05,
    # economic (sum 0.52)
    'van_usd':              0.18,
    'roi':                  0.10,
    'lcoe_usd_kwh':         0.14,
    'capex_usd':            0.10,
}

# Direction: +1 = maximize, -1 = minimize (invert before normalization)
CRITERION_DIRECTION = {
    'cp_max':              +1,
    'horas_cerca_nominal': +1,
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
    'factor_capacidad':    'Factor de capacidad',
    'aep_mwh':             'AEP (MWh)',
    'directional_concentration': 'Consistencia direccional',
    'van_usd':             'VAN (USD)',
    'roi':                 'ROI',
    'lcoe_usd_kwh':        'LCOE (USD/kWh)',
    'capex_usd':           'CAPEX (USD)',
}


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

    # Build decision matrix (n × m). NaN/Inf y None → 0.0 (neutral).
    M = np.zeros((n, len(criteria)), dtype=float)
    for i, alt in enumerate(alternatives):
        for j, c in enumerate(criteria):
            v = alt.get(c)
            v = 0.0 if (v is None or (isinstance(v, float) and not np.isfinite(v))) else float(v)
            M[i, j] = v

    # Apply direction: invert minimization columns so all are "higher is better"
    for j, c in enumerate(criteria):
        if CRITERION_DIRECTION[c] < 0:
            col = M[:, j]
            col_max = col.max() if col.size else 0
            M[:, j] = (col_max + 1e-9) - col   # simple reflection

    # Vector normalization (Euclidean) per column
    col_norms = np.linalg.norm(M, axis=0)
    col_norms[col_norms == 0] = 1e-12
    R = M / col_norms

    # Weighted matrix
    W = np.array([w[c] for c in criteria])
    V = R * W

    # Ideal and anti-ideal solutions (all columns are now max-is-best)
    V_best  = V.max(axis=0)
    V_worst = V.min(axis=0)

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

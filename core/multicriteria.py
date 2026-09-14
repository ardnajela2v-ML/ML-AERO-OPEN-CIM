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
  cp_effective_full_series — mean Cp over every valid record in the series [–]
  resource_operational_availability_pct — share of valid time with P(v) > 0 [%]

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
    # Pesos de referencia del estudio, sin elicitación AHP documentada. Las métricas se exponen para
    # auditoría y análisis de sensibilidad, pero no alteran el ranking base.
    'cp_max':                0.08,
    'horas_cerca_nominal':   0.07,
    'cp_at_site_mean':       0.00,
    'cpmax_speed_mismatch_ms': 0.00,
    'hours_near_cpmax':      0.00,
    'cp_effective_full_series': 0.00,
    'resource_operational_availability_pct': 0.00,
    'hours_at_or_above_90pct_rated': 0.00,
    'factor_capacidad':      0.15,
    'aep_mwh':               0.13,
    'directional_concentration': 0.05,
    'van_usd':               0.18,
    'roi':                   0.10,
    'lcoe_usd_kwh':          0.14,
    'capex_usd':             0.10,
}

# Direction: +1 = maximize, -1 = minimize (invert before normalization)
CRITERION_DIRECTION = {
    'cp_max':              +1,
    'horas_cerca_nominal': +1,
    'cp_at_site_mean':     +1,
    'cpmax_speed_mismatch_ms': -1,
    'hours_near_cpmax':    +1,
    'cp_effective_full_series': +1,
    'resource_operational_availability_pct': +1,
    'hours_at_or_above_90pct_rated': +1,
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
    'cp_effective_full_series': 'Cp efectivo sobre la serie completa',
    'resource_operational_availability_pct': 'Disponibilidad operativa del recurso (%)',
    'hours_at_or_above_90pct_rated': 'Horas con potencia ≥90% de la nominal',
    'factor_capacidad':    'Factor de capacidad',
    'aep_mwh':             'AEP (MWh)',
    'directional_concentration': 'Consistencia direccional',
    'van_usd':             'VAN (USD)',
    'roi':                 'ROI',
    'lcoe_usd_kwh':        'LCOE (USD/kWh)',
    'capex_usd':           'CAPEX (USD)',
}


LAYOUT_WEIGHTS = {
    # Agreed reference: technical 60%, economic 40%.
    # aep_p90_mwh (bruto) y van_usd (bruto) en vez de sus versiones /área o
    # /MW: el área efectiva es la misma constante para todas las
    # alternativas de un mismo ranking (ver effective_park_area), así que
    # dividir entre ella no cambiaba la normalización vectorial de TOPSIS.
    # Se probó (2026-09-13) que, a diferencia del área, el MW instalado SÍ
    # varía por alternativa — pero normalizar por él tiene el efecto no
    # deseado de aplanar VAN a una constante cuando solo cambia la escala
    # (misma turbina, más unidades): con área de sitio fija, lo que importa
    # es cuánto valor total se extrae de ESE terreno, igual que con AEP. Por
    # eso VAN pasa a bruto, igual que AEP.
    #
    # 2026-09-13: se retiraron benefit_cost_ratio y discounted_payback_years
    # de los pesos (bajan a LAYOUT_AUDIT_ONLY, se siguen mostrando). Verificado
    # en código (rank_layout_alternatives, precio/tasa/OPEX% constantes entre
    # alternativas de una corrida): LCOE, B/C, TIR y recuperación descontada
    # están perfectamente rank-correlacionados (ρ=±1.0000) — son la misma
    # información bajo transformaciones distintas (ver docs/ahp-topsis.md).
    # LCOE queda como único representante (estándar de industria, con
    # respaldo de importancia creciente frente a payback/TIR — Delapedra-Silva
    # et al. 2022). El peso liberado se reparte con el criterio nuevo
    # capex_usd_per_installed_kw (antes constante por precio único de
    # turbina; ahora varía por geometría vía costo de cimentación — ver
    # core/cost_model.py). CAPEX se queda normalizado por kW (a diferencia de
    # VAN/AEP): es un criterio de costo, y dejarlo bruto premiaría trivialmente
    # instalar menos capacidad, en contradicción directa con AEP/VAN.
    #
    # 2026-09-13 (segunda decisión, en la misma fecha): el investigador
    # decidió reincorporar discounted_payback_years JUNTO a LCOE, aceptando
    # a propósito la redundancia parcial documentada arriba — no por
    # descuido. Queda anotado explícitamente: LCOE y recuperación descontada
    # SIGUEN siendo ρ=±1.0000 en este modelo (misma información bajo dos
    # transformaciones), así que sus pesos AHP individuales no se traducen en
    # dos aportes independientes de información al ranking — ver CRITIC
    # (Diakoulaki et al. 1995) y MAUT (Keeney & Raiffa 1976) ya citados en
    # docs/ahp-topsis.md §1 y §6. Se documenta como decisión informada del
    # investigador (p. ej. por expectativa del comité de tesis de ver PRI
    # explícito), no como hallazgo científico de independencia.
    #
    # 2026-09-14: se reemplaza el placeholder anterior (60/40 fijo, 0.10/0.20
    # parejo dentro de cada bloque) por un expediente AHP real de 28
    # comparaciones para estos 8 criterios, calculado con `core.ahp.
    # calculate_ahp` (no reconstruido desde los pesos previos — cada
    # comparación se decidió por separado, ver docs/ahp-topsis.md §6 "Tercera
    # actividad"). CR=0.0065 (‹0.10, matriz consistente), RI=1.41 para n=8
    # (Saaty 1987). Autoría: investigador de esta tesis, con asistencia de
    # Claude Sonnet 5 para sistematizar los 28 juicios a partir de la
    # revisión bibliográfica ya citada en §1/§5/§6 — es una construcción de
    # UN solo juicio informado (el método adoptado excluye Delphi, ver
    # encabezado del documento), no un panel de expertos ni un resultado
    # estadístico. Cada criterio recibió antes un "nivel de prioridad"
    # razonado (ver tabla en docs/ahp-topsis.md) y las 28 comparaciones por
    # pares se derivaron de esos niveles, redondeando cada razón al valor más
    # cercano de la escala verbal 1-9 de Saaty (no valores intermedios como
    # 1.5): esto es visible y auditable en la matriz publicada en el
    # documento, no un ajuste oculto para llegar a un número concreto.
    # Sigue pendiente de validación por el investigador antes de citarse en
    # la tesis como AHP definitivo — puede y debe recalcularse desde la
    # calculadora de la plataforma con autor/objetivo/justificación propios
    # si el investigador no está de acuerdo con algún juicio individual.
    #
    # 2026-09-14 (revisión con el investigador, ver docs/ahp-topsis.md §6
    # "Cuarta actividad"): confirmado por qué se prefiere payback sobre
    # benefit_cost_ratio y sobre DSCR (ambos evaluados y descartados, DSCR
    # con ρ=-1.0000 verificado en código frente a LCOE); sin literatura que
    # respalde el 95% de near_cp_fraction en hours_near_cpmax (búsqueda con
    # resultado negativo, documentada). Hallazgo SIN resolver: la redundancia
    # se probó en código para el bloque económico pero no para el técnico
    # (cp_effective_full_series/hours_near_cpmax vs. aep_p90_mwh/
    # factor_capacidad, ver §1) — los niveles de prioridad de ambos bloques
    # no se construyeron con la misma regla. Ver el documento para el método
    # propuesto (todavía no ejecutado) antes de dar este expediente por
    # cerrado.
    'cp_effective_full_series': 0.1041868085680245,
    'hours_near_cpmax': 0.10960883502133105,
    'factor_capacidad': 0.2137956435893556,
    'aep_p90_mwh': 0.19329532545925715,
    'van_usd': 0.10960883502133101,
    'lcoe_usd_kwh': 0.12159368182163764,
    'capex_usd_per_installed_kw': 0.10418680856802454,
    'discounted_payback_years': 0.04372406195103838,
}

# These values remain available in the alternative audit, but are no longer
# eligible for park ranking, including when loading weights from older projects.
LAYOUT_AUDIT_ONLY = (
    'aep_p90_mwh_per_effective_km2', 'van_usd_per_effective_km2',
    'aep_p90_mwh_per_installed_mw', 'van_usd_per_installed_mw',
    'cp_at_site_mean', 'hours_at_or_above_90pct_rated', 'capex_usd',
    'wake_loss_pct', 'energy_density_mwh_km2',
    'collection_length_m', 'cpmax_speed_mismatch_ms',
    'resource_operational_availability_pct',
    'opex_usd_per_installed_kw_year',
    'opex_lifetime_usd', 'iec_suitability_score', 'aep_uncertainty_pct',
    'curtailment_loss_pct', 'civil_complexity_index',
    'roi', 'irr', 'payback_years', 'benefit_cost_ratio',
)

LAYOUT_DIRECTION = {
    'aep_p90_mwh_per_effective_km2': +1,
    'van_usd_per_effective_km2': +1,
    'aep_p90_mwh_per_installed_mw': +1,
    'cp_at_site_mean': +1, 'cpmax_speed_mismatch_ms': -1,
    'hours_near_cpmax': +1,
    'cp_effective_full_series': +1,
    'resource_operational_availability_pct': +1,
    'hours_at_or_above_90pct_rated': +1,
    'benefit_cost_ratio': +1, 'payback_years': -1,
    'irr': +1, 'roi': +1,
    'capex_usd_per_installed_kw': -1,
    'opex_usd_per_installed_kw_year': -1,
    'van_usd_per_installed_mw': +1,
    'discounted_payback_years': -1,
    'van_usd': +1, 'lcoe_usd_kwh': -1, 'capex_usd': -1,
    'opex_lifetime_usd': -1, 'aep_p90_mwh': +1,
    'wake_loss_pct': -1, 'factor_capacidad': +1,
    'iec_suitability_score': +1, 'aep_uncertainty_pct': -1,
    'curtailment_loss_pct': -1, 'energy_density_mwh_km2': +1,
    'collection_length_m': -1, 'civil_complexity_index': -1,
}

LAYOUT_LABELS = {
    'aep_p90_mwh_per_effective_km2': 'AEP P90 por área efectiva (MWh/km²·año)',
    'van_usd_per_effective_km2': 'VAN por área efectiva (USD/km²)',
    'aep_p90_mwh_per_installed_mw': 'AEP P90 específico (MWh/MW instalado·año)',
    'cp_at_site_mean': 'Cp a velocidad media del sitio',
    'cpmax_speed_mismatch_ms': '|v media − v de Cp máximo| (m/s)',
    'hours_near_cpmax': 'Horas en zona ≥95% de Cp máximo',
    'cp_effective_full_series': 'Cp efectivo sobre la serie completa',
    'resource_operational_availability_pct': 'Disponibilidad operativa del recurso (%)',
    'hours_at_or_above_90pct_rated': 'Horas con potencia ≥90% de la nominal',
    'benefit_cost_ratio': 'Relación beneficio/costo (B/C)',
    'payback_years': 'PRI — periodo de recuperación (años)',
    'irr': 'TIR (%)', 'roi': 'ROI (VAN/CAPEX)',
    'capex_usd_per_installed_kw': 'CAPEX específico (USD/kW)',
    'opex_usd_per_installed_kw_year': 'OPEX específico (USD/kW-año)',
    'van_usd_per_installed_mw': 'VAN normalizado (USD/MW instalado)',
    'discounted_payback_years': 'Periodo de recuperación descontado (años)',
    'van_usd': 'VAN / VPN (USD)', 'lcoe_usd_kwh': 'LCOE (USD/kWh)',
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

LAYOUT_DIRECTION = {key: value for key, value in LAYOUT_DIRECTION.items()
                    if key in LAYOUT_WEIGHTS}
LAYOUT_LABELS = {key: value for key, value in LAYOUT_LABELS.items()
                 if key in LAYOUT_WEIGHTS}

SITE_FIT_CRITERIA = (
    'cp_at_site_mean', 'cpmax_speed_mismatch_ms', 'hours_near_cpmax',
    'cp_effective_full_series', 'resource_operational_availability_pct',
    'hours_at_or_above_90pct_rated',
)


def rank_layout_alternatives(alternatives: List[Dict],
                             weights: Optional[Dict[str, float]] = None):
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
    raw_weights = dict(LAYOUT_WEIGHTS)
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

    zero_weight_criteria = [key for key, value in raw_weights.items() if value == 0]
    scored_weights = {key: value for key, value in raw_weights.items() if value > 0}

    valid, excluded = [], list(pre_excluded)
    for alt in alternatives:
        missing = [key for key in scored_weights
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
    for key in scored_weights:
        values = np.asarray([float(alt[key]) for alt in valid], dtype=float)
        if np.ptp(values) <= 1e-12:
            constant.append(key)
        else:
            active.append(key)
    if not active:
        ranked = [dict(alt, topsis_score=1.0, rank=i + 1) for i, alt in enumerate(valid)]
        return {'ranking': ranked, 'weights': {}, 'n': len(ranked),
                'excluded': excluded, 'constant_criteria': constant}

    total = sum(scored_weights[key] for key in active)
    if total <= 0:
        raise ValueError('Al menos un criterio TOPSIS variable debe tener peso positivo.')
    effective_weights = {key: scored_weights[key] / total for key in active}
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
            'zero_weight_criteria': zero_weight_criteria,
            'profile': 'balanced_integral_v1',
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
    zero_weight_criteria = [key for key, value in w.items() if value == 0]
    w = {key: value for key, value in w.items() if value > 0}
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
            'criteria': criteria, 'labels': CRITERION_LABELS,
            'zero_weight_criteria': zero_weight_criteria}


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

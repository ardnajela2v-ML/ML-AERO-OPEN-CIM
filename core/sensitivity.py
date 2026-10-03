"""Explicit scenario assumptions for economic sensitivity analysis."""
import math

from core.project_economics import project_cashflows


LAYOUT_SCENARIOS = {
    'base': (1.0, 1.0, 1.0, 0.0),
    'precio_menos_20': (0.8, 1.0, 1.0, 0.0),
    'aep_menos_10': (1.0, 0.9, 1.0, 0.0),
    'capex_mas_15': (1.0, 1.0, 1.15, 0.0),
    'tasa_mas_2pp': (1.0, 1.0, 1.0, 0.02),
    'estres_combinado': (0.8, 0.9, 1.15, 0.02),
}


def compare_layout_scenarios(alternatives):
    """Paired stress tests using each layout's ranking cash-flow inputs.

    The shocks are illustrative and carry no statistical probabilities. Refuse
    stale/incomplete ranking rows when their reported VAN cannot be reproduced.
    """
    if not isinstance(alternatives, list) or not alternatives:
        raise ValueError('Se requiere un ranking no vacío de layouts.')
    if len(alternatives) > 100:
        raise ValueError('El ranking excede 100 layouts.')

    def finite(row, key, positive=False):
        value = float(row[key])
        if not math.isfinite(value) or (positive and value <= 0):
            raise ValueError(f'{key} debe ser finito y positivo.')
        return value

    def npv(alt, price=1.0, aep=1.0, capex=1.0, rate=0.0):
        eco = alt['economic_assumptions']
        chron = alt.get('chronological_revenue') or {}
        revenue = chron.get('revenue_usd_year1')
        if revenue is not None:
            revenue = float(revenue) * price * aep
            if not math.isfinite(revenue) or revenue < 0:
                raise ValueError('Ingreso horario del año 1 inválido.')
        return project_cashflows(
            finite(alt, 'capex_usd', True) * capex,
            finite(alt, 'opex_usd_anual') * capex,
            finite(alt, 'aep_mwh', True) * 1000.0 * aep,
            finite(eco, 'price_usd_kwh') * price,
            finite(eco, 'discount_rate') + rate,
            int(eco['project_years']),
            degradation_pct=finite(eco, 'degradation_pct_per_year'),
            decommissioning_pct=finite(eco, 'decommissioning_pct_capex'),
            year1_revenue_override=revenue,
        )['van_usd']

    def threshold(alt, axis):
        base = npv(alt)
        if base <= 0:
            return None
        if axis == 'capex':
            lo, hi = 1.0, 5.0
            if npv(alt, capex=hi) > 0:
                return None
            for _ in range(55):
                mid = (lo + hi) / 2
                if npv(alt, capex=mid) > 0:
                    lo = mid
                else:
                    hi = mid
        else:
            lo, hi = 0.0, 1.0
            if npv(alt, **{axis: lo}) > 0:
                return None
            for _ in range(55):
                mid = (lo + hi) / 2
                if npv(alt, **{axis: mid}) < 0:
                    lo = mid
                else:
                    hi = mid
        return (lo + hi) / 2

    rows = []
    for alt in alternatives:
        try:
            label = str(alt['turbina'])
            values = {name: npv(alt, *factors) for name, factors in LAYOUT_SCENARIOS.items()}
            reported = finite(alt, 'van_usd')
            if abs(values['base'] - reported) > max(0.01, abs(reported) * 1e-8):
                raise ValueError(f'VAN base distinto al ranking ({values["base"]:.2f} vs {reported:.2f} USD).')
            capex = finite(alt, 'capex_usd', True)
            rows.append({
                'turbina': label,
                'n_aerogeneradores': int(alt['n_aerogeneradores']),
                'capex_usd': capex,
                'van_por_capex_base': reported / capex,
                'van_usd': values,
                'umbral_precio_factor': threshold(alt, 'price'),
                'umbral_aep_factor': threshold(alt, 'aep'),
                'umbral_capex_factor': threshold(alt, 'capex'),
            })
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f'Layout {alt.get("turbina", "sin nombre")}: {exc}') from exc
    for scenario in LAYOUT_SCENARIOS:
        best = max(row['van_usd'][scenario] for row in rows)
        for row in rows:
            row.setdefault('arrepentimiento_usd', {})[scenario] = best - row['van_usd'][scenario]
    for row in rows:
        row['peor_van_usd'] = min(row['van_usd'].values())
        row['mayor_arrepentimiento_usd'] = max(row['arrepentimiento_usd'].values())
    return {
        'method': 'paired_deterministic_stress_tests_same_park_cashflows',
        'probabilities_calibrated': False,
        'scenario_factors': {name: dict(zip(('price_factor', 'aep_factor', 'capex_factor',
                                             'discount_rate_delta'), factors))
                             for name, factors in LAYOUT_SCENARIOS.items()},
        'rows': rows,
    }


def wake_loss_range(base_loss_pct):
    """Uniform ±30% relative scenario, bounded by 100% energy loss.

    This is a sensitivity assumption, not a calibrated confidence interval.
    None omits wake uncertainty; zero retains a constant zero-loss scenario.
    """
    if base_loss_pct is None:
        return None
    try:
        base = float(base_loss_pct)
    except (TypeError, ValueError):
        raise ValueError('La pérdida por estela debe ser un número entre 0 y menos de 100%.') from None
    if not math.isfinite(base) or not 0 <= base < 100:
        raise ValueError('La pérdida por estela debe ser finita, entre 0 y menos de 100%.')
    return [base * 0.7, min(100.0, base * 1.3)]

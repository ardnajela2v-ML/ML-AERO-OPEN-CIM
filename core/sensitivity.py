"""Explicit scenario assumptions for economic sensitivity analysis."""
import math

from core.project_economics import project_cashflows


# Each scenario specifies multiplicative factors for price, aep, capex, opex
# and additive deltas for discount_rate (pp) and annual_inflation (pp), plus
# a relative wake-loss increase (fraction: 0.30 → +30% of base wake loss).
LAYOUT_SCENARIOS = {
    'base':              dict(price=1.0,  aep=1.0,  capex=1.0,  rate_delta=0.0,  opex=1.0,  inflation_delta=0.0, wake_relative=0.0),
    'precio_menos_20':   dict(price=0.8,  aep=1.0,  capex=1.0,  rate_delta=0.0,  opex=1.0,  inflation_delta=0.0, wake_relative=0.0),
    'aep_menos_10':      dict(price=1.0,  aep=0.9,  capex=1.0,  rate_delta=0.0,  opex=1.0,  inflation_delta=0.0, wake_relative=0.0),
    'capex_mas_15':      dict(price=1.0,  aep=1.0,  capex=1.15, rate_delta=0.0,  opex=1.0,  inflation_delta=0.0, wake_relative=0.0),
    'tasa_mas_2pp':      dict(price=1.0,  aep=1.0,  capex=1.0,  rate_delta=0.02, opex=1.0,  inflation_delta=0.0, wake_relative=0.0),
    'opex_mas_20':       dict(price=1.0,  aep=1.0,  capex=1.0,  rate_delta=0.0,  opex=1.20, inflation_delta=0.0, wake_relative=0.0),
    'inflacion_mas_2pp': dict(price=1.0,  aep=1.0,  capex=1.0,  rate_delta=0.0,  opex=1.0,  inflation_delta=2.0, wake_relative=0.0),
    'estela_mas_30pct':  dict(price=1.0,  aep=1.0,  capex=1.0,  rate_delta=0.0,  opex=1.0,  inflation_delta=0.0, wake_relative=0.30),
    'estres_combinado':  dict(price=0.8,  aep=0.9,  capex=1.15, rate_delta=0.02, opex=1.20, inflation_delta=2.0, wake_relative=0.0),
}


def _compute_metrics(alt, *, price=1.0, aep=1.0, capex=1.0, rate_delta=0.0,
                     opex=1.0, inflation_delta=0.0, wake_relative=0.0):
    """Compute VAN, LCOE, B/C and VAE for one alternative under given shocks.

    Returns a dict with van_usd, lcoe_usd_kwh, b_c_ratio, vae_usd.
    Raises ValueError for missing/invalid inputs.
    """
    def finite(row, key, positive=False):
        value = float(row[key])
        if not math.isfinite(value) or (positive and value <= 0):
            raise ValueError(f'{key} debe ser finito y positivo.')
        return value

    eco = alt['economic_assumptions']

    # Resolve effective AEP factor including wake-loss scenario
    aep_factor = aep
    if wake_relative > 0:
        wake_base_pct = float(alt.get('wake_loss_pct') or 0.0)
        if wake_base_pct > 0:
            wake_new_pct = min(wake_base_pct * (1.0 + wake_relative), 99.9)
            # aep_net_base = aep_gross × (1 - wake_base/100)
            # aep_net_new  = aep_gross × (1 - wake_new/100)
            aep_factor = aep * (1.0 - wake_new_pct / 100.0) / (1.0 - wake_base_pct / 100.0)

    # Year-1 revenue override (chronological flow if available)
    chron = alt.get('chronological_revenue') or {}
    revenue_y1 = chron.get('revenue_usd_year1')
    if revenue_y1 is not None:
        revenue_y1 = float(revenue_y1) * price * aep_factor
        if not math.isfinite(revenue_y1) or revenue_y1 < 0:
            revenue_y1 = None  # fall back to formula-based

    base_inflation = float(eco.get('annual_inflation_pct') or 0.0)
    rate_used = finite(eco, 'discount_rate') + rate_delta
    n = int(eco['project_years'])

    result = project_cashflows(
        finite(alt, 'capex_usd', True) * capex,
        finite(alt, 'opex_usd_anual') * opex,
        finite(alt, 'aep_mwh', True) * 1000.0 * aep_factor,
        finite(eco, 'price_usd_kwh') * price,
        rate_used,
        n,
        degradation_pct=finite(eco, 'degradation_pct_per_year'),
        decommissioning_pct=finite(eco, 'decommissioning_pct_capex'),
        annual_inflation_pct=base_inflation + inflation_delta,
        year1_revenue_override=revenue_y1,
    )

    van = result['van_usd']
    pv_costs = result['pv_costs']
    pv_energy = result['pv_energy_kwh']
    pv_revenue = result['pv_revenue']

    lcoe = pv_costs / pv_energy if pv_energy > 0 else None
    bc = pv_revenue / pv_costs if pv_costs > 0 else None

    # VAE = VAN × CRF   (Capital Recovery Factor)
    if rate_used > 0 and n > 0:
        crf = rate_used * (1.0 + rate_used) ** n / ((1.0 + rate_used) ** n - 1.0)
    elif n > 0:
        crf = 1.0 / n
    else:
        crf = 0.0
    vae = van * crf

    return {'van_usd': van, 'lcoe_usd_kwh': lcoe, 'b_c_ratio': bc, 'vae_usd': vae}


def compare_layout_scenarios(alternatives):
    """Paired stress tests using each layout's ranking cash-flow inputs.

    The shocks are illustrative and carry no statistical probabilities. Refuses
    stale/incomplete ranking rows when their reported VAN cannot be reproduced.

    Returns per-scenario VAN, LCOE, B/C and VAE for every layout, plus
    thresholds (break-even price/AEP/CAPEX factors) and regret matrices.
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

    def van_only(alt, price=1.0, aep=1.0, capex=1.0, rate=0.0, opex=1.0,
                 inflation_delta=0.0, wake_relative=0.0):
        return _compute_metrics(
            alt, price=price, aep=aep, capex=capex, rate_delta=rate,
            opex=opex, inflation_delta=inflation_delta, wake_relative=wake_relative,
        )['van_usd']

    def threshold(alt, axis):
        base = van_only(alt)
        if base <= 0:
            return None
        if axis == 'capex':
            lo, hi = 1.0, 5.0
            if van_only(alt, capex=hi) > 0:
                return None
            for _ in range(55):
                mid = (lo + hi) / 2
                if van_only(alt, capex=mid) > 0:
                    lo = mid
                else:
                    hi = mid
        else:
            lo, hi = 0.0, 1.0
            if van_only(alt, **{axis: lo}) > 0:
                return None
            for _ in range(55):
                mid = (lo + hi) / 2
                if van_only(alt, **{axis: mid}) < 0:
                    lo = mid
                else:
                    hi = mid
        return (lo + hi) / 2

    rows = []
    for alt in alternatives:
        try:
            label = str(alt['turbina'])
            all_metrics = {
                name: _compute_metrics(alt, **factors)
                for name, factors in LAYOUT_SCENARIOS.items()
            }
            reported = finite(alt, 'van_usd')
            base_van = all_metrics['base']['van_usd']
            if abs(base_van - reported) > max(0.01, abs(reported) * 1e-8):
                raise ValueError(
                    f'VAN base distinto al ranking ({base_van:.2f} vs {reported:.2f} USD).')
            capex = finite(alt, 'capex_usd', True)
            rows.append({
                'turbina': label,
                'n_aerogeneradores': int(alt['n_aerogeneradores']),
                'capex_usd': capex,
                'van_por_capex_base': reported / capex,
                # Per-scenario metrics (dict keyed by scenario name)
                'van_usd':     {k: v['van_usd']      for k, v in all_metrics.items()},
                'lcoe_usd_kwh':{k: v['lcoe_usd_kwh'] for k, v in all_metrics.items()},
                'b_c_ratio':   {k: v['b_c_ratio']    for k, v in all_metrics.items()},
                'vae_usd':     {k: v['vae_usd']      for k, v in all_metrics.items()},
                'umbral_precio_factor': threshold(alt, 'price'),
                'umbral_aep_factor':    threshold(alt, 'aep'),
                'umbral_capex_factor':  threshold(alt, 'capex'),
            })
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f'Layout {alt.get("turbina", "sin nombre")}: {exc}') from exc

    # Regret matrix (VAN-based, consistent with existing interpretation)
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
        'scenario_factors': {
            name: factors for name, factors in LAYOUT_SCENARIOS.items()
        },
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

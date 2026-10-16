"""Shared project cash flows: year-1 net AEP, degradation and terminal cost.

Defaults preserve the existing TOPSIS study assumptions; they are not a
literature calibration. Decommissioning is a fixed share of initial CAPEX,
paid in the final year, without inflation or salvage credit. Inflation applies
to OPEX; prices change only when an explicit yearly series is supplied.
All monetary inputs must use the same currency.
"""
import math

# 0.5 %/año — WindEurope (2019), DNV-GL project finance practice, IRENA RE Cost 2023.
# Rango empírico: 0.5–1.5 %/año según antigüedad y fabricante.
DEFAULT_DEGRADATION_PCT = 0.5
DEFAULT_DECOMMISSIONING_PCT = 3.0


def project_cashflows(capex, annual_opex, annual_energy_kwh, price_usd_kwh,
                      discount_rate, project_years, *,
                      degradation_pct=DEFAULT_DEGRADATION_PCT,
                      decommissioning_pct=DEFAULT_DECOMMISSIONING_PCT,
                      annual_inflation_pct=0.0, price_series=None,
                      year1_revenue_override=None):
    for name, value, upper in [('degradation_pct', degradation_pct, 100.0),
                               ('decommissioning_pct', decommissioning_pct, None)]:
        if not math.isfinite(value) or value < 0 or (upper is not None and value >= upper):
            raise ValueError(f'{name} fuera de rango')
    if not math.isfinite(discount_rate) or discount_rate <= -1:
        raise ValueError('La tasa de descuento debe ser finita y mayor que -100%.')
    if project_years < 1:
        raise ValueError('La vida del proyecto debe ser positiva.')
    decommissioning = capex * decommissioning_pct / 100.0
    cashflows = [-capex]
    pv_costs, pv_energy, pv_revenue = capex, 0.0, 0.0
    cumulative = -capex
    discounted_cumulative = -capex
    payback = None
    discounted_payback = None
    cashflow_table = []
    pv_opex = 0.0
    pv_decommissioning = 0.0
    for year in range(1, project_years + 1):
        energy = annual_energy_kwh / 1000.0 * ((1.0 - degradation_pct / 100.0) ** (year - 1))
        price = price_usd_kwh
        if price_series is not None and year <= len(price_series):
            try:
                candidate = float(price_series[year - 1])
                if math.isfinite(candidate):
                    price = candidate
            except (TypeError, ValueError):
                pass
        revenue = energy * 1000.0 * price
        if year == 1 and year1_revenue_override is not None:
            revenue = float(year1_revenue_override)
        opex = annual_opex * (1.0 + annual_inflation_pct / 100.0) ** (year - 1)
        terminal = decommissioning if year == project_years else 0.0
        net = revenue - opex - terminal
        cashflows.append(net)
        previous_cumulative = cumulative
        cumulative += net
        if payback is None and cumulative >= 0:
            payback = (
                year - 1 + (-previous_cumulative) / net
                if net > 0 else float(year)
            )
        factor = (1.0 + discount_rate) ** year
        discounted_net = net / factor
        previous_discounted_cumulative = discounted_cumulative
        discounted_cumulative += discounted_net
        if discounted_payback is None and discounted_cumulative >= 0:
            discounted_payback = (
                year - 1 + (-previous_discounted_cumulative) / discounted_net
                if discounted_net > 0 else float(year)
            )
        discounted_opex = opex / factor
        discounted_terminal = terminal / factor
        pv_opex += discounted_opex
        pv_decommissioning += discounted_terminal
        pv_revenue += revenue / factor
        pv_costs += discounted_opex + discounted_terminal
        pv_energy += energy * 1000.0 / factor
        cashflow_table.append({
            'year': year,
            'energy_mwh': energy,
            'revenue_usd': revenue,
            'opex_usd': opex,
            'price_usd_kwh': price,
            'decommissioning_usd': terminal,
            'net_cashflow_usd': net,
            'discount_factor': factor,
            'pv_net_cashflow_usd': net / factor,
            'cumulative_undiscounted_usd': cumulative,
            'cumulative_discounted_usd': discounted_cumulative,
        })
    van = sum(value / ((1.0 + discount_rate) ** year) for year, value in enumerate(cashflows))
    return {
        'cashflows': cashflows, 'cashflow_table': cashflow_table,
        'van_usd': van, 'pv_costs': pv_costs, 'pv_energy_kwh': pv_energy,
        'pv_revenue': pv_revenue, 'opex_present_value_usd': pv_opex,
        'opex_lifetime_usd': sum(row['opex_usd'] for row in cashflow_table),
        'decommissioning_usd': decommissioning,
        'decommissioning_present_value_usd': pv_decommissioning,
        'payback_years': payback, 'discounted_payback_years': discounted_payback,
        'degradation_pct_per_year': degradation_pct,
        'decommissioning_pct_capex': decommissioning_pct,
    }


def irr_bisection(cashflows):
    """TIR anual (fracción, no %) de un flujo [t=0, t=1, ...] por bisección.
    None si el flujo no cambia de signo (no converge). Mismo método que el IRR
    apalancado de /api/energy/leveraged en api.py — cambio de signo + bisección."""
    def _npv_r(r):
        return sum(cf / (1.0 + r) ** t for t, cf in enumerate(cashflows))
    lo, hi = -0.9999, 9.99
    f_lo, f_hi = _npv_r(lo), _npv_r(hi)
    if f_lo * f_hi >= 0:
        return None
    mid = lo
    for _ in range(256):
        mid = (lo + hi) / 2.0
        if abs(hi - lo) < 1e-9:
            break
        if _npv_r(mid) * f_lo > 0:
            lo = mid
        else:
            hi = mid
    return mid

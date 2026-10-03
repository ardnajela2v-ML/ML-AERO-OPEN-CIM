"""Paired, illustrative scenario comparison using the park's exact cash-flow engine."""
from pathlib import Path
import csv
import json

from core.project_economics import project_cashflows

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'exports/benchmark_horario_20260924/deferred_pipeline.json'
alternatives = json.loads(BASE.read_text())['alternatives']

# Identical shocks for every alternative. These are sensitivity assumptions,
# not calibrated probabilities or confidence limits.
SCENARIOS = {
    'base': (1., 1., 1., 0.),
    'pml_menos_20pct': (.8, 1., 1., 0.),
    'aep_menos_10pct': (1., .9, 1., 0.),
    'capex_mas_15pct': (1., 1., 1.15, 0.),
    'descuento_mas_2pp': (1., 1., 1., .02),
    'estres_combinado': (.8, .9, 1.15, .02),
}

def evaluate(alt, price_factor=1., aep_factor=1., capex_factor=1., rate_delta=0.):
    eco = alt['economic_assumptions']
    result = project_cashflows(
        alt['capex_usd'] * capex_factor,
        alt['opex_usd_anual'] * capex_factor,
        alt['aep_mwh'] * 1000. * aep_factor,
        eco['price_usd_kwh'] * price_factor,
        eco['discount_rate'] + rate_delta,
        eco['project_years'],
        degradation_pct=eco['degradation_pct_per_year'],
        decommissioning_pct=eco['decommissioning_pct_capex'],
        year1_revenue_override=(
            alt['chronological_revenue']['revenue_usd_year1'] * price_factor * aep_factor),
    )
    return result['van_usd']

def break_even(alt, axis):
    low, high = 0., 5.
    for _ in range(70):
        mid = (low + high) / 2.
        args = {'price_factor': 1., 'aep_factor': 1., 'capex_factor': 1.}
        args[axis] = mid
        van = evaluate(alt, **args)
        if (van < 0) == (axis != 'capex_factor'):
            low = mid
        else:
            high = mid
    return (low + high) / 2.

rows = []
for alt in alternatives:
    values = {name: evaluate(alt, *params) for name, params in SCENARIOS.items()}
    assert abs(values['base'] - alt['van_usd']) < .01, alt['turbina']
    row = {
        'turbina': alt['turbina'], 'n_aerogeneradores': alt['n_aerogeneradores'],
        'capex_usd': alt['capex_usd'],
        'van_por_capex_base': values['base'] / alt['capex_usd'],
        'pml_factor_equilibrio': break_even(alt, 'price_factor'),
        'aep_factor_equilibrio': break_even(alt, 'aep_factor'),
        'capex_factor_equilibrio': break_even(alt, 'capex_factor'),
        **{f'van_{name}_usd': value for name, value in values.items()},
    }
    rows.append(row)

for name in SCENARIOS:
    key = f'van_{name}_usd'
    best = max(row[key] for row in rows)
    for row in rows:
        row[f'arrepentimiento_{name}_usd'] = best - row[key]

for row in rows:
    row['peor_van_escenarios_usd'] = min(row[f'van_{name}_usd'] for name in SCENARIOS)
    row['mayor_arrepentimiento_usd'] = max(
        row[f'arrepentimiento_{name}_usd'] for name in SCENARIOS)

report = {
    'project': '/Users/ale/Downloads/nuevoeste.aeroproj',
    'method': 'same park cash-flow model, common illustrative shocks, no probabilities',
    'scenario_factors': {name: dict(zip(('price_factor', 'aep_factor',
        'capex_factor', 'rate_delta'), factors)) for name, factors in SCENARIOS.items()},
    'rows': rows,
}
out = Path(__file__).with_name('compare_layout_scenarios_20260924')
out.with_suffix('.json').write_text(json.dumps(report, indent=2))
with out.with_suffix('.csv').open('w', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
for row in sorted(rows, key=lambda item: -item['van_base_usd']):
    print(row['turbina'], 'VAN_base_M', round(row['van_base_usd']/1e6, 2),
          'VAN_stress_M', round(row['van_estres_combinado_usd']/1e6, 2),
          'max_regret_M', round(row['mayor_arrepentimiento_usd']/1e6, 2),
          'PML_break_even', round(row['pml_factor_equilibrio'], 3))

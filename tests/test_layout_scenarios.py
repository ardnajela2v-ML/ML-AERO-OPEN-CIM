"""The paired comparison must reproduce the park ranking before stressing it."""
import copy
import json
from pathlib import Path

import pytest

from core.sensitivity import compare_layout_scenarios


EXPORT = Path(__file__).resolve().parents[1] / 'exports/benchmark_horario_20260924/deferred_pipeline.json'


def alternatives():
    return json.loads(EXPORT.read_text())['alternatives']


def test_exported_polygon_and_pml_cashflows_match_exactly():
    source = alternatives()
    result = compare_layout_scenarios(source)
    assert len(result['rows']) == len(source) == 8
    assert result['probabilities_calibrated'] is False
    for alt, row in zip(source, result['rows']):
        assert row['van_usd']['base'] == pytest.approx(alt['van_usd'], abs=.01)
        assert row['van_usd']['precio_menos_20'] < row['van_usd']['base']
        assert row['van_usd']['aep_menos_10'] < row['van_usd']['base']
        assert row['van_usd']['estres_combinado'] <= row['peor_van_usd'] + .01
        assert row['umbral_precio_factor'] < 1
        assert row['umbral_aep_factor'] < 1
        assert row['umbral_capex_factor'] > 1
    for scenario in result['scenario_factors']:
        assert min(row['arrepentimiento_usd'][scenario] for row in result['rows']) == pytest.approx(0)


def test_stale_or_incomplete_ranking_is_rejected():
    changed = copy.deepcopy(alternatives()[:1])
    changed[0]['van_usd'] += 1000
    with pytest.raises(ValueError, match='VAN base distinto'):
        compare_layout_scenarios(changed)
    del changed[0]['economic_assumptions']
    with pytest.raises(ValueError, match='economic_assumptions'):
        compare_layout_scenarios(changed)


def test_flat_price_layout_without_pml_is_supported():
    changed = copy.deepcopy(alternatives()[:1])
    changed[0]['chronological_revenue'] = None
    from core.project_economics import project_cashflows
    alt = changed[0]
    eco = alt['economic_assumptions']
    alt['van_usd'] = project_cashflows(
        alt['capex_usd'], alt['opex_usd_anual'], alt['aep_mwh'] * 1000,
        eco['price_usd_kwh'], eco['discount_rate'], eco['project_years'],
        degradation_pct=eco['degradation_pct_per_year'],
        decommissioning_pct=eco['decommissioning_pct_capex'])['van_usd']
    assert compare_layout_scenarios(changed)['rows'][0]['van_usd']['base'] == pytest.approx(alt['van_usd'])


def test_api_uses_ranked_layout_fields():
    from api import create_app
    app = create_app()
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    with app.test_client() as client:
        response = client.post('/api/energy/layout_scenarios', json={'alternatives': alternatives()})
        assert response.status_code == 200, response.get_json()
        assert len(response.get_json()['rows']) == 8
        stale = alternatives()[:1]
        stale[0]['van_usd'] += 10_000
        response = client.post('/api/energy/layout_scenarios', json={'alternatives': stale})
        assert response.status_code == 400

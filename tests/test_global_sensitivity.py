"""Regression coverage for wake scenarios in both Monte Carlo endpoints."""
import pytest

from core.sensitivity import wake_loss_range


@pytest.mark.parametrize('base, expected', [
    (None, None), (0, [0, 0]), (10, [7, 13]), (80, [56, 100]),
    (99, [69.3, 100]),
])
def test_wake_scenario_bounds(base, expected):
    result = wake_loss_range(base)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
        assert result[0] <= base <= result[1]


@pytest.mark.parametrize('base', [-1, 100, 101, float('nan'), float('inf'), 'bad', []])
def test_invalid_wake_loss_is_rejected(base):
    with pytest.raises(ValueError):
        wake_loss_range(base)


@pytest.fixture(scope='module')
def client():
    from api import create_app
    app = create_app()
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    return app.test_client()


def payload():
    return dict(session_id='sensitivity-regression', n_samples=50,
                turbine={'builtin_model': 'V150_4.5'}, n_turbines=1,
                base_annual_kwh=15_000_000, base_wake_loss_pct=10,
                financial={'energy_price_usd_kwh': 0.06, 'project_years': 25})


def test_single_and_batch_use_same_scenario_and_reproduce(client):
    body = payload()
    single = client.post('/api/energy/global_sensitivity', json=body)
    assert single.status_code == 200, single.get_json()
    single = single.get_json()
    body['turbines'] = [dict(label='reference', builtin_model='V150_4.5',
                             annual_energy_kwh=15_000_000, wake_loss_pct=10)]
    batch = client.post('/api/energy/global_sensitivity_batch', json=body)
    assert batch.status_code == 200, batch.get_json()
    row = batch.get_json()['turbines'][0]
    assert single['ranges_used']['wake_loss_pct'] == pytest.approx([7, 13])
    assert row['ranges_used'] == single['ranges_used']
    assert row['robustness'] == single['robustness']
    assert row['base_case'] == single['base_case']
    repeated = client.post('/api/energy/global_sensitivity_batch', json=body)
    assert repeated.get_json() == batch.get_json()


def test_importance_is_out_of_sample_validated(client):
    """Point 4 of the ML audit: RandomForestRegressor importance must be
    reported alongside a held-out R², never as a bare in-sample number."""
    body = payload()
    body['n_samples'] = 200
    response = client.post('/api/energy/global_sensitivity', json=body)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    for metric in data['importance']:
        imp, val = data['importance'][metric], data['validation'][metric]
        assert (imp is None) == (val is None)
        if val is not None:
            assert val['method'] == 'kfold_cv_permutation_importance'
            assert val['n_splits'] >= 2
            assert 'r2_mean' in val and 'r2_std' in val
            assert val['linear_model'] == 'LinearRegression'
            assert 'linear_model_r2_mean' in val
            assert sum(row['importance_pct'] for row in imp) == pytest.approx(100, abs=1)
    # price and AEP drive the technoeconomic metrics; the model should find
    # held-out signal, not just memorize the 200 training rows.
    assert data['validation']['b_c_ratio']['r2_mean'] > 0.5


def test_risk_clusters_are_real_kmeans_ordered_by_van_not_decorative(client):
    """Objetivo 3 de la tesis: a diferencia de los K-means decorativos de
    régimen de precio (api.py) o fases de flujo de caja (aero.js), estos
    clústeres deben venir de las propias muestras Monte Carlo de la
    alternativa, con k elegido por silhouette y ordenados por riesgo real."""
    body = payload()
    body['n_samples'] = 200
    body['turbines'] = [dict(label='reference', builtin_model='V150_4.5',
                             annual_energy_kwh=15_000_000, wake_loss_pct=10)]
    response = client.post('/api/energy/global_sensitivity_batch', json=body)
    assert response.status_code == 200, response.get_json()
    rc = response.get_json()['turbines'][0]['risk_clusters']
    assert 2 <= rc['k'] <= 4
    assert rc['k'] in rc['k_candidates_tested']
    assert len(rc['clusters']) == rc['k']
    assert rc['n_valid_samples'] <= 200
    assert sum(c['share_pct'] for c in rc['clusters']) == pytest.approx(100.0, abs=0.5)
    van_means = [c['metrics']['van_capex_ratio']['mean'] for c in rc['clusters']]
    assert van_means == sorted(van_means)  # "Mayor riesgo" -> "Menor riesgo"
    assert rc['clusters'][0]['label'] == 'Mayor riesgo'
    assert rc['clusters'][-1]['label'] == 'Menor riesgo'
    assert 0.0 <= rc['overall_prob_van_negative_pct'] <= 100.0
    for c in rc['clusters']:
        assert 0.0 <= c['prob_van_negative_pct'] <= 100.0
        assert set(c['metrics']) == set(rc['features'])


def test_invalid_wake_returns_client_error(client):
    body = payload()
    body['base_wake_loss_pct'] = 100
    response = client.post('/api/energy/global_sensitivity', json=body)
    assert response.status_code == 400
    body['turbines'] = [dict(label='invalid', annual_energy_kwh=15_000_000,
                             wake_loss_pct=-5)]
    response = client.post('/api/energy/global_sensitivity_batch', json=body)
    assert response.status_code == 200
    assert 'error' in response.get_json()['turbines'][0]

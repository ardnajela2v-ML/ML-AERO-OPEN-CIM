import numpy as np
import pytest

from core.ahp import calculate_ahp
from core.multicriteria import LAYOUT_WEIGHTS, LAYOUT_DIRECTION


def test_exact_consistent_priorities_and_scale_invariance():
    weights = np.array([4., 2., 1.])
    result = calculate_ahp(['a', 'b', 'c'], weights[:, None] / weights[None, :])
    assert list(result['weights'].values()) == pytest.approx(weights / weights.sum())
    assert result['lambda_max'] == pytest.approx(3)
    assert result['cr'] == pytest.approx(0, abs=1e-12)
    assert result['consistent']


def test_strong_cyclic_preferences_require_review():
    result = calculate_ahp(['a', 'b', 'c'], [[1, 9, 1/9], [1/9, 1, 9], [9, 1/9, 1]])
    assert result['cr'] > .1
    assert not result['consistent']
    assert result['review_triads'][0]['criteria'] == ['a', 'b', 'c']


@pytest.mark.parametrize('matrix', [
    [[1, None], [None, 1]], [[1, float('inf')], [0, 1]],
    [[1, 2], [2, 1]], [[2, 1], [1, 1]], [[1, 10], [.1, 1]],
    [[1, 0], [0, 1]], [[1]], [['bad', 1], [1, 1]],
])
def test_invalid_matrices_rejected(matrix):
    with pytest.raises(ValueError):
        calculate_ahp(['a', 'b'], matrix)


def test_duplicate_criteria_rejected():
    with pytest.raises(ValueError):
        calculate_ahp(['a', 'a'], [[1, 1], [1, 1]])


@pytest.fixture(scope='module')
def client():
    from api import create_app
    app = create_app()
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    return app.test_client()


def assessment():
    w = np.array([9.] + [1.] * 7)
    return {'criteria': list(LAYOUT_WEIGHTS), 'matrix': (w[:, None] / w[None, :]).tolist(),
            'author': 'Synthetic test only', 'objective': 'Test decision',
            'rationale': 'Synthetic ratios for regression, not research judgments.'}


def alternatives():
    rows = []
    for i in [1, 2]:
        row = {'label': f'test-{i}', 'feasible': True}
        row.update({key: float(i) if LAYOUT_DIRECTION[key] > 0 else 1. / i
                    for key in LAYOUT_WEIGHTS})
        rows.append(row)
    return rows


def test_endpoint_exports_traceable_assessment(client):
    result = client.post('/api/multicriteria/ahp', json=assessment())
    assert result.status_code == 200
    data = result.get_json()
    assert data['weights'][list(LAYOUT_WEIGHTS)[0]] == pytest.approx(9 / 16)
    assert data['ri'] == 1.41
    assert data['author'] == assessment()['author']
    assert data['criteria_basis_status'] == 'bibliographic_review_pending'
    assert data['schema'] == 'layout-ahp-v1'
    assert data['assessed_at']


def test_rank_recomputes_weights_instead_of_trusting_payload(client):
    ahp = assessment()
    ahp['weights'] = dict.fromkeys(LAYOUT_WEIGHTS, 0)
    response = client.post('/api/multicriteria/rank', json={
        'mode': 'layout_alternatives', 'alternatives': alternatives(),
        'weights': dict.fromkeys(LAYOUT_WEIGHTS, 0), 'ahp_assessment': ahp})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['ranking'][0]['label'] == 'test-2'
    assert data['ahp_assessment']['weights'][list(LAYOUT_WEIGHTS)[0]] == pytest.approx(9 / 16)
    assert data['weights'][list(LAYOUT_WEIGHTS)[0]] == pytest.approx(9 / 16)


def test_inconsistent_assessment_cannot_rank_even_with_forged_status(client):
    ahp = assessment()
    ahp['matrix'] = np.ones((8, 8)).tolist()
    for i, j in [(0, 1), (1, 2), (2, 0)]:
        ahp['matrix'][i][j], ahp['matrix'][j][i] = 9, 1/9
    ahp['consistent'] = True
    preview = client.post('/api/multicriteria/ahp', json=ahp)
    assert not preview.get_json()['consistent']
    for endpoint, extras in [('rank', {}), ('weight_robustness', {'n_samples': 50})]:
        result = client.post('/api/multicriteria/' + endpoint, json={
            'mode': 'layout_alternatives', 'alternatives': alternatives(),
            'ahp_assessment': ahp, **extras})
        assert result.status_code == 400


def test_robustness_uses_and_reports_ahp_center(client):
    response = client.post('/api/multicriteria/weight_robustness', json={
        'mode': 'layout_alternatives', 'alternatives': alternatives(),
        'ahp_assessment': assessment(), 'n_samples': 50})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['base_weights'][list(LAYOUT_WEIGHTS)[0]] == pytest.approx(9/16)
    assert data['ahp_assessment']['author'] == assessment()['author']
    assert data['alternatives'][0]['win_rate_pct'] == 100


@pytest.mark.parametrize('field', ['author', 'objective', 'rationale', 'criteria', 'matrix'])
def test_missing_assessment_fields_return_400(client, field):
    payload = assessment()
    del payload[field]
    assert client.post('/api/multicriteria/ahp', json=payload).status_code == 400

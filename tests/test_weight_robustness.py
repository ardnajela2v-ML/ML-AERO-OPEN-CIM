"""Point 4 of the ML audit: the SMAA RandomForestClassifier in
weight_robustness must validate criteria_importance out-of-sample instead of
scoring permutation_importance on its own training rows.
"""
import pytest

from core.multicriteria import LAYOUT_DIRECTION, LAYOUT_WEIGHTS


@pytest.fixture(scope='module')
def client():
    from api import create_app
    app = create_app()
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    return app.test_client()


def _tied_alternatives():
    """Two alternatives, each dominant on a disjoint half of the criteria —
    a near-tie under uniform weights, so different Dirichlet weight draws
    flip the winner instead of one alternative sweeping every sample (which
    would leave a single class and make cross-validation impossible)."""
    keys = list(LAYOUT_WEIGHTS)
    group_a, group_b = keys[:4], keys[4:]

    def value(direction, wins):
        if wins:
            return 10.0 if direction > 0 else 1.0
        return 1.0 if direction > 0 else 10.0

    def row(label, strong_group):
        r = {'label': label, 'feasible': True}
        for k in keys:
            r[k] = value(LAYOUT_DIRECTION[k], k in strong_group)
        return r

    return [row('A', group_a), row('B', group_b)]


def test_criteria_importance_beats_majority_baseline_out_of_sample(client):
    response = client.post('/api/multicriteria/weight_robustness', json={
        'mode': 'layout_alternatives', 'alternatives': _tied_alternatives(),
        'n_samples': 300, 'concentration': 8})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    win_rates = {row['label']: row['win_rate_pct'] for row in data['alternatives']}
    assert min(win_rates.values()) > 5  # both alternatives actually won some samples

    val = data['classifier_validation']
    assert val is not None
    assert val['method'] == 'kfold_cv_permutation_importance'
    assert val['n_splits'] >= 2
    assert val['balanced_accuracy_mean'] > val['baseline_accuracy_mean']
    assert val['linear_model'] == 'LogisticRegression'
    assert 'linear_model_balanced_accuracy_mean' in val

    imp = data['criteria_importance']
    assert imp is not None
    assert sum(row['importance_pct'] for row in imp) == pytest.approx(100, abs=1)


def test_single_dominant_alternative_reports_none_not_in_sample_fallback(client):
    """When one alternative wins 100% of weight draws there is only one
    class — no fold can be held out honestly, so both fields must be None
    rather than a RandomForest importance computed on its own training data."""
    keys = list(LAYOUT_WEIGHTS)
    dominant = {'label': 'A', 'feasible': True,
                **{k: (10.0 if LAYOUT_DIRECTION[k] > 0 else 1.0) for k in keys}}
    dominated = {'label': 'B', 'feasible': True,
                 **{k: (1.0 if LAYOUT_DIRECTION[k] > 0 else 10.0) for k in keys}}
    response = client.post('/api/multicriteria/weight_robustness', json={
        'mode': 'layout_alternatives', 'alternatives': [dominant, dominated],
        'n_samples': 100})
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data['alternatives'][0]['win_rate_pct'] == 100
    assert data['criteria_importance'] is None
    assert data['classifier_validation'] is None

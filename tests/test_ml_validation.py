"""Unit coverage for out-of-sample validation of Random Forest importance.

Point 4 of the ML audit: weight_robustness and global_sensitivity trained
and scored permutation_importance on the same rows used to fit the model
(in-sample), so a reported importance_pct could not be trusted as signal
versus overfit noise. rf_cv_feature_importance must never do that — it holds
out a fold, and returns None instead of a number when there isn't enough
data (or class balance) to hold out honestly.
"""
import numpy as np
import pytest

from core.ml_validation import rf_cv_feature_importance


def test_regression_recovers_dominant_feature_and_reports_held_out_r2():
    rng = np.random.default_rng(0)
    X = rng.uniform(size=(200, 3))
    y = X[:, 0] * 3 + rng.normal(scale=0.05, size=200)
    result = rf_cv_feature_importance(X, y, ['a', 'b', 'c'], task='regression')
    assert result['method'] == 'kfold_cv_permutation_importance'
    assert result['r2_mean'] > 0.8
    assert result['importance'][0]['feature'] == 'a'
    assert result['importance'][0]['importance_pct'] > 90
    assert sum(row['importance_pct'] for row in result['importance']) == pytest.approx(100, abs=1)


def test_regression_with_no_signal_reports_low_r2():
    rng = np.random.default_rng(1)
    X = rng.uniform(size=(200, 3))
    y = rng.normal(size=200)  # independent of X
    result = rf_cv_feature_importance(X, y, ['a', 'b', 'c'], task='regression')
    assert result['r2_mean'] < 0.3  # no held-out predictive power


def test_regression_too_few_samples_returns_none_not_a_fabricated_number():
    rng = np.random.default_rng(2)
    X = rng.uniform(size=(15, 3))
    y = X[:, 0]
    assert rf_cv_feature_importance(X, y, ['a', 'b', 'c'], task='regression') is None


def test_classification_recovers_dominant_feature_and_beats_baseline():
    rng = np.random.default_rng(3)
    X = rng.uniform(size=(200, 3))
    labels = np.where(X[:, 0] > 0.5, 'A', 'B')
    result = rf_cv_feature_importance(X, labels, ['a', 'b', 'c'], task='classification')
    assert result['balanced_accuracy_mean'] > result['baseline_accuracy_mean']
    assert result['balanced_accuracy_mean'] > 0.85
    assert result['baseline_accuracy_mean'] == pytest.approx(0.5, abs=0.05)
    assert result['importance'][0]['feature'] == 'a'


def test_classification_single_class_returns_none():
    X = np.random.default_rng(4).uniform(size=(50, 3))
    labels = ['A'] * 50
    assert rf_cv_feature_importance(X, labels, ['a', 'b', 'c'], task='classification') is None


def test_classification_singleton_class_returns_none_not_in_sample_fallback():
    """A class with a single member can't be held out honestly — must not
    silently fall back to in-sample importance (the exact flaw the audit
    flagged for weight_robustness with few, imbalanced winners)."""
    rng = np.random.default_rng(5)
    X = rng.uniform(size=(40, 3))
    labels = ['A'] * 39 + ['B']
    assert rf_cv_feature_importance(X, labels, ['a', 'b', 'c'], task='classification') is None


def test_regression_reports_linear_baseline_on_same_folds():
    """RF's added complexity should be a testable finding, not an assumption:
    on a linear relationship, LinearRegression should match RF almost exactly."""
    rng = np.random.default_rng(7)
    X = rng.uniform(size=(200, 3))
    y = X[:, 0] * 3 + rng.normal(scale=0.05, size=200)
    result = rf_cv_feature_importance(X, y, ['a', 'b', 'c'], task='regression')
    assert result['linear_model'] == 'LinearRegression'
    assert result['linear_model_r2_mean'] == pytest.approx(result['r2_mean'], abs=0.1)


def test_regression_rf_beats_linear_baseline_on_nonlinear_signal():
    """On a strongly nonlinear relationship, RF should clearly outperform the
    linear baseline — this is the empirical case for using ML at all."""
    rng = np.random.default_rng(8)
    X = rng.uniform(size=(200, 3))
    y = np.sin(X[:, 0] * 6) * 3 + rng.normal(scale=0.1, size=200)
    result = rf_cv_feature_importance(X, y, ['a', 'b', 'c'], task='regression')
    assert result['r2_mean'] > result['linear_model_r2_mean'] + 0.2


def test_classification_reports_linear_baseline_on_same_folds():
    rng = np.random.default_rng(9)
    X = rng.uniform(size=(200, 3))
    labels = np.where(X[:, 0] > 0.5, 'A', 'B')
    result = rf_cv_feature_importance(X, labels, ['a', 'b', 'c'], task='classification')
    assert result['linear_model'] == 'LogisticRegression'
    assert result['linear_model_balanced_accuracy_mean'] > result['baseline_accuracy_mean']


def test_reproducible_with_fixed_random_state():
    rng = np.random.default_rng(6)
    X = rng.uniform(size=(120, 3))
    y = X[:, 1] * 2 + rng.normal(scale=0.1, size=120)
    r1 = rf_cv_feature_importance(X, y, ['a', 'b', 'c'], task='regression')
    r2 = rf_cv_feature_importance(X, y, ['a', 'b', 'c'], task='regression')
    assert r1 == r2

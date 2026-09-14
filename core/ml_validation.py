"""Out-of-sample validation for Random Forest feature-importance reports.

Computing permutation_importance on the same rows used to fit the model can
reward memorization instead of real signal (Molnar, "Interpretable Machine
Learning", 2022, sec. 8.5 — permutation importance is defined w.r.t. a
dataset, and using the training set answers "what did the model memorized?",
not "what generalizes?"). This module fits and scores across K folds the
model never saw during that fold's training, so both the reported
performance metric (R² or balanced accuracy) and the feature ranking are
held-out. It never falls back to in-sample importance: when there isn't
enough data (or class balance, for classification) to hold out a fold
honestly, it returns None rather than a number that looks validated but
isn't.

It also fits a classical linear model (LinearRegression / LogisticRegression)
on the exact same folds, so "does the added complexity of a Random Forest
pay for itself here, versus a linear model" is a reported, testable result —
not an assumption baked into the choice of estimator.
"""
from collections import Counter

import numpy as np


def rf_cv_feature_importance(X, y, feature_names, *, task, n_splits=5,
                              min_test_size=10, n_repeats=10, random_state=42):
    """Cross-validated permutation importance for a Random Forest.

    task: 'regression' (RandomForestRegressor, scored by R²) or
    'classification' (RandomForestClassifier, scored by balanced accuracy —
    robust to the class imbalance that a dominant TOPSIS alternative causes).
    """
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LinearRegression, LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, r2_score
    from sklearn.model_selection import KFold, StratifiedKFold

    X = np.asarray(X, dtype=float)
    n = len(X)
    if task == 'classification':
        y = np.asarray(y)
        class_counts = Counter(y)
        if len(class_counts) < 2:
            return None
        splits = min(n_splits, min(class_counts.values()))
        if splits < 2:
            return None
        splitter = StratifiedKFold(n_splits=splits, shuffle=True, random_state=random_state)
        split_iter = splitter.split(X, y)
    else:
        y = np.asarray(y, dtype=float)
        splits = min(n_splits, n // min_test_size)
        if splits < 2:
            return None
        splitter = KFold(n_splits=splits, shuffle=True, random_state=random_state)
        split_iter = splitter.split(X)

    fold_scores, fold_baseline, fold_linear, fold_importances = [], [], [], []
    for train_idx, test_idx in split_iter:
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        if task == 'classification':
            model = RandomForestClassifier(n_estimators=200, random_state=random_state, n_jobs=-1)
            model.fit(X_train, y_train)
            fold_scores.append(balanced_accuracy_score(y_test, model.predict(X_test)))
            majority = Counter(y_train).most_common(1)[0][0]
            fold_baseline.append(balanced_accuracy_score(y_test, [majority] * len(y_test)))
            linear = LogisticRegression(max_iter=1000)
            linear.fit(X_train, y_train)
            fold_linear.append(balanced_accuracy_score(y_test, linear.predict(X_test)))
            scoring = 'balanced_accuracy'
        else:
            model = RandomForestRegressor(n_estimators=200, random_state=random_state, n_jobs=-1)
            model.fit(X_train, y_train)
            fold_scores.append(r2_score(y_test, model.predict(X_test)))
            linear = LinearRegression()
            linear.fit(X_train, y_train)
            fold_linear.append(r2_score(y_test, linear.predict(X_test)))
            scoring = None
        perm = permutation_importance(model, X_test, y_test, n_repeats=n_repeats,
                                       random_state=random_state, scoring=scoring)
        fold_importances.append(np.clip(perm.importances_mean, 0, None))

    mean_imp = np.mean(fold_importances, axis=0)
    total = mean_imp.sum()
    pct_imp = (mean_imp / total * 100.0) if total > 0 else mean_imp
    ranked = sorted([
        {'feature': feature_names[k], 'importance_pct': round(float(pct_imp[k]), 2)}
        for k in range(len(feature_names))
    ], key=lambda row: -row['importance_pct'])

    metric_name = 'balanced_accuracy' if task == 'classification' else 'r2'
    out = {
        'method': 'kfold_cv_permutation_importance', 'n_splits': splits, 'n_samples': n,
        'importance': ranked,
        metric_name + '_mean': round(float(np.mean(fold_scores)), 4),
        metric_name + '_std': round(float(np.std(fold_scores)), 4),
        'linear_model': 'LogisticRegression' if task == 'classification' else 'LinearRegression',
        'linear_model_' + metric_name + '_mean': round(float(np.mean(fold_linear)), 4),
        'linear_model_' + metric_name + '_std': round(float(np.std(fold_linear)), 4),
    }
    if task == 'classification':
        out['baseline_accuracy_mean'] = round(float(np.mean(fold_baseline)), 4)
    return out

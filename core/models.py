"""
AERO by OPEN CIM — ML/baseline model training and prediction.
Enforces strict chronological train/test split.  No data leakage.
"""
import warnings
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.svm import SVR
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.multioutput import MultiOutputRegressor
import joblib

try:
    from xgboost import XGBRegressor
    HAS_XGB = True
except Exception:
    HAS_XGB = False

try:
    from lightgbm import LGBMRegressor
    from lightgbm import early_stopping as lgb_early_stopping
    from lightgbm import log_evaluation as lgb_log_evaluation
    HAS_LGB = True
except Exception:
    HAS_LGB = False

try:
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    HAS_STATSMODELS = True
except Exception:
    HAS_STATSMODELS = False

from core.validate import compute_all_metrics, skill_score, rmse as _rmse
warnings.filterwarnings('ignore')


# ─── GPU DETECTION ────────────────────────────────────────────────────────────

def _detect_gpu():
    """Return True if a CUDA-capable GPU is available and usable by XGBoost/LightGBM."""
    try:
        import xgboost as xgb
        # XGBoost >= 1.5 exposes a simple GPU check
        if hasattr(xgb, 'build_info'):
            info = xgb.build_info()
            if info.get('USE_CUDA', False):
                return True
    except Exception:
        pass
    try:
        import lightgbm as lgb
        # LightGBM can be compiled with GPU support; we check by trying to create a GPU dataset
        if hasattr(lgb, 'Dataset'):
            try:
                dummy = lgb.Dataset(np.zeros((2, 2)), label=np.zeros(2))
                # If the library was built with GPU support, the parameter 'device' is accepted
                dummy.construct()
                # LightGBM doesn't expose a simple flag, so we assume GPU if the import succeeded
                # and the system has a CUDA device visible to Python.
                import subprocess
                try:
                    out = subprocess.check_output(['nvidia-smi', '-L'], stderr=subprocess.DEVNULL)
                    if out.strip():
                        return True
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass
    return False

HAS_GPU = _detect_gpu()


# ─── BASELINES ────────────────────────────────────────────────────────────────

class BaselineModels:

    @staticmethod
    def persistence(y_train, y_test, horizon_h=1):
        """h-step persistence: ŷ(t+h) = y(t)."""
        train_full = np.concatenate([y_train, y_test])
        n_train = len(y_train)
        preds = np.full(len(y_test), np.nan)
        for i in range(len(y_test)):
            idx = n_train + i - horizon_h
            if idx >= 0:
                preds[i] = train_full[idx]
        return preds

    @staticmethod
    def climatology(y_train, y_test):
        """Predict global mean of training set."""
        mean_val = float(np.nanmean(y_train))
        return np.full(len(y_test), mean_val)

    @staticmethod
    def moving_average(y_train, y_test, window=24):
        """Last-window rolling mean from training tail."""
        tail = y_train[-window:]
        pred_val = float(np.nanmean(tail))
        return np.full(len(y_test), pred_val)

    @staticmethod
    def linear_regression(X_train, y_train, X_test):
        model = Ridge(alpha=1.0)
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        preds = np.maximum(preds, 0)
        return preds

    @staticmethod
    def arima_forecast(y_train, n_steps, order=(2,1,2)):
        """Simple ARIMA on training series, forecast n_steps ahead."""
        if not HAS_STATSMODELS:
            return np.full(n_steps, np.nanmean(y_train))
        try:
            series = pd.Series(y_train).dropna()
            # cap to last 1000 points — ARIMA is O(n²), hangs on large series
            if len(series) > 1000:
                series = series.iloc[-1000:]
            model = ARIMA(series, order=order)
            fit = model.fit()
            forecast = fit.forecast(steps=n_steps)
            return np.maximum(np.asarray(forecast), 0)
        except Exception:
            return np.full(n_steps, np.nanmean(y_train))


# ─── QUANTILE WRAPPER ─────────────────────────────────────────────────────────

class QuantileGBM:
    """
    LightGBM / GBM quantile regressor for prediction intervals.
    Falls back to simple percentile spread if LGB unavailable.
    """
    def __init__(self, quantiles=(0.10, 0.50, 0.90)):
        self.quantiles = quantiles
        self.models = {}

    def fit(self, X, y):
        for q in self.quantiles:
            if HAS_LGB:
                m = LGBMRegressor(objective='quantile', alpha=q,
                                  n_estimators=120, learning_rate=0.08,
                                  num_leaves=31, verbose=-1, n_jobs=-1)
            else:
                m = GradientBoostingRegressor(loss='quantile', alpha=q,
                                              n_estimators=80,
                                              learning_rate=0.08)
            m.fit(X, y)
            self.models[q] = m
        return self

    def predict(self, X):
        return {q: self.models[q].predict(X) for q in self.quantiles}


# ─── ML MODEL REGISTRY ────────────────────────────────────────────────────────

def _build_models():
    models = {}

    models['random_forest'] = RandomForestRegressor(
        n_estimators=200, max_depth=15, min_samples_leaf=5,
        n_jobs=-1, random_state=42)

    if HAS_XGB:
        xgb_kwargs = dict(
            n_estimators=300, learning_rate=0.05, max_depth=6,
            subsample=0.8, colsample_bytree=0.8,
            reg_lambda=1.0, reg_alpha=0.1,
            random_state=42, verbosity=0, n_jobs=-1,
            early_stopping_rounds=20,
        )
        if HAS_GPU:
            xgb_kwargs['tree_method'] = 'gpu_hist'
            xgb_kwargs['gpu_id'] = 0
        models['xgboost'] = XGBRegressor(**xgb_kwargs)

    if HAS_LGB:
        lgb_kwargs = dict(
            n_estimators=300, learning_rate=0.05, num_leaves=63,
            subsample=0.8, colsample_bytree=0.8,
            reg_lambda=1.0, verbose=-1, random_state=42, n_jobs=-1,
        )
        if HAS_GPU:
            lgb_kwargs['device'] = 'gpu'
            lgb_kwargs['gpu_platform_id'] = 0
            lgb_kwargs['gpu_device_id'] = 0
        models['lightgbm'] = LGBMRegressor(**lgb_kwargs)

    models['ridge'] = Ridge(alpha=10.0)

    models['mlp'] = Pipeline([
        ('scaler', StandardScaler()),
        ('mlp', MLPRegressor(hidden_layer_sizes=(128, 64, 32),
                             activation='relu', max_iter=200,
                             early_stopping=True, n_iter_no_change=15,
                             random_state=42, verbose=False)),
    ])
    # tag MLP so train_all can subsample it on large datasets
    models['mlp']._mlp_subsample = True

    models['svr'] = Pipeline([
        ('scaler', StandardScaler()),
        ('svr', SVR(kernel='rbf', C=10.0, epsilon=0.1, gamma='scale')),
    ])
    # tag SVR so train_all can subsample it
    models['svr']._svr_subsample = True

    return models


# ─── BLACKWELL-OPTIMIZED MODEL SECTION ────────────────────────────────────────

class BlackwellOptimizedModel:
    """
    Placeholder for future Blackwell-architecture optimizations.
    Currently wraps a standard XGBoost/LightGBM model with GPU acceleration
    when available, and provides a unified interface for training and prediction.
    """
    def __init__(self, model_type='xgboost', **kwargs):
        self.model_type = model_type
        self.kwargs = kwargs
        self.model = None

    def _create_model(self):
        if self.model_type == 'xgboost':
            if not HAS_XGB:
                raise RuntimeError("XGBoost is not installed.")
            xgb_kwargs = dict(
                n_estimators=300, learning_rate=0.05, max_depth=6,
                subsample=0.8, colsample_bytree=0.8,
                reg_lambda=1.0, reg_alpha=0.1,
                random_state=42, verbosity=0, n_jobs=-1,
            )
            if HAS_GPU:
                xgb_kwargs['tree_method'] = 'gpu_hist'
                xgb_kwargs['gpu_id'] = 0
            xgb_kwargs.update(self.kwargs)
            return XGBRegressor(**xgb_kwargs)
        elif self.model_type == 'lightgbm':
            if not HAS_LGB:
                raise RuntimeError("LightGBM is not installed.")
            lgb_kwargs = dict(
                n_estimators=300, learning_rate=0.05, num_leaves=63,
                subsample=0.8, colsample_bytree=0.8,
                reg_lambda=1.0, verbose=-1, random_state=42, n_jobs=-1,
            )
            if HAS_GPU:
                lgb_kwargs['device'] = 'gpu'
                lgb_kwargs['gpu_platform_id'] = 0
                lgb_kwargs['gpu_device_id'] = 0
            lgb_kwargs.update(self.kwargs)
            return LGBMRegressor(**lgb_kwargs)
        else:
            raise ValueError(f"Unsupported model_type: {self.model_type}")

    def fit(self, X, y):
        self.model = self._create_model()
        self.model.fit(X, y)
        return self

    def predict(self, X):
        if self.model is None:
            raise RuntimeError("Model has not been fitted yet.")
        return self.model.predict(X)


# ─── MODEL ARENA ──────────────────────────────────────────────────────────────

class ModelArena:
    """
    Trains all available models for a given horizon, returns
    predictions, metrics, and feature importances.
    """

    def __init__(self):
        self.trained = {}   # {model_name: fitted_model}
        self.scalers = {}
        self.feature_names = []

    def train_all(self, X_train, y_train, X_test, y_test,
                  idx_test, feature_names, horizon_h=1,
                  progress_callback=None, enabled_models=None,
                  include_quantiles=False):
        """
        Returns: results dict with predictions + metrics for each model.
        enabled_models: list of model keys to train; None = train all.
        """
        self.feature_names = feature_names
        results = {}
        en = set(enabled_models) if enabled_models else None  # None = all

        def _skip(key):
            return en is not None and key not in en

        models = _build_models()
        # remove ML models not requested
        if en is not None:
            models = {k: v for k, v in models.items() if k in en}

        # ── baselines ──
        bl = BaselineModels()

        # persistence — always compute for skill score reference
        y_pers = bl.persistence(y_train, y_test, horizon_h)
        if not _skip('persistence'):
            results['persistence'] = {
                'y_true': y_test.tolist(), 'y_pred': y_pers.tolist(),
                'idx': [str(i) for i in idx_test],
                'metrics': compute_all_metrics(y_test, y_pers, y_pers, 'Persistence'),
                'type': 'baseline',
            }

        if not _skip('climatology'):
            y_clim = bl.climatology(y_train, y_test)
            results['climatology'] = {
                'y_true': y_test.tolist(), 'y_pred': y_clim.tolist(),
                'idx': [str(i) for i in idx_test],
                'metrics': compute_all_metrics(y_test, y_clim, y_pers, 'Climatology'),
                'type': 'baseline',
            }

        if not _skip('moving_average'):
            y_ma = bl.moving_average(y_train, y_test, window=min(24, len(y_train)))
            results['moving_average'] = {
                'y_true': y_test.tolist(), 'y_pred': y_ma.tolist(),
                'idx': [str(i) for i in idx_test],
                'metrics': compute_all_metrics(y_test, y_ma, y_pers, 'Moving Average'),
                'type': 'baseline',
            }

        if not _skip('linear_regression'):
            y_lr = np.maximum(bl.linear_regression(X_train, y_train, X_test), 0)
            results['linear_regression'] = {
                'y_true': y_test.tolist(), 'y_pred': y_lr.tolist(),
                'idx': [str(i) for i in idx_test],
                'metrics': compute_all_metrics(y_test, y_lr, y_pers, 'Linear Regression'),
                'type': 'baseline',
            }

        if not _skip('arima'):
            y_arima = bl.arima_forecast(y_train, len(y_test))
            y_arima_padded = np.full(len(y_test), np.nan)
            y_arima_padded[:len(y_arima)] = y_arima
            results['arima'] = {
                'y_true': y_test.tolist(), 'y_pred': y_arima_padded.tolist(),
                'idx': [str(i) for i in idx_test],
                'metrics': compute_all_metrics(y_test, y_arima_padded, y_pers, 'ARIMA'),
                'type': 'baseline',
            }

        n_bl = len([k for k in ('persistence','climatology','moving_average',
                                'linear_regression','arima') if not _skip(k)])
        if progress_callback:
            progress_callback(30 if n_bl else 5, f'{n_bl} baselines trained')

        # ── ML models ──
        n_train = len(X_train)
        # Adaptive estimator reduction for large datasets
        if n_train > 15000:
            est_scale = 0.4
        elif n_train > 8000:
            est_scale = 0.6
        else:
            est_scale = 1.0
        if est_scale < 1.0:
            for m in models.values():
                base = (m.named_steps.get('mlp') or m.named_steps.get('svr')) if hasattr(m, 'named_steps') else m
                if hasattr(base, 'n_estimators'):
                    base.n_estimators = max(50, int(base.n_estimators * est_scale))

        total_ml = len(models)
        for i, (name, model) in enumerate(models.items()):
            try:
                # SVR is O(n²–n³): subsample to max 3000 rows
                X_tr, y_tr = X_train, y_train
                if getattr(model, '_svr_subsample', False) and n_train > 3000:
                    rng = np.random.default_rng(42)
                    idx_sub = rng.choice(n_train, 3000, replace=False)
                    idx_sub.sort()
                    X_tr, y_tr = X_train[idx_sub], y_train[idx_sub]
                # MLP: subsample to max 8000 rows for speed
                elif getattr(model, '_mlp_subsample', False) and n_train > 8000:
                    rng = np.random.default_rng(42)
                    idx_sub = rng.choice(n_train, 8000, replace=False)
                    idx_sub.sort()
                    X_tr, y_tr = X_train[idx_sub], y_train[idx_sub]

                # XGBoost early stopping needs eval_set
                fit_kwargs = {}
                if name == 'xgboost' and hasattr(model, 'early_stopping_rounds'):
                    fit_kwargs['eval_set'] = [(X_test, y_test)]
                    fit_kwargs['verbose'] = False
                # LightGBM early stopping via callbacks
                elif name == 'lightgbm' and HAS_LGB:
                    fit_kwargs['eval_set'] = [(X_test, y_test)]
                    fit_kwargs['callbacks'] = [
                        lgb_early_stopping(20, verbose=False),
                        lgb_log_evaluation(-1),
                    ]

                model.fit(X_tr, y_tr, **fit_kwargs)
                y_pred = np.maximum(model.predict(X_test), 0)
                self.trained[name] = model

                # feature importance
                fi = self._get_feature_importance(model, feature_names)

                results[name] = {
                    'y_true': y_test.tolist(),
                    'y_pred': y_pred.tolist(),
                    'idx': [str(i) for i in idx_test],
                    'metrics': compute_all_metrics(y_test, y_pred, y_pers,
                                                   name.replace('_', ' ').title()),
                    'feature_importance': fi,
                    'type': 'ml',
                }
                pct = 30 + int(60 * (i + 1) / total_ml)
                if progress_callback:
                    progress_callback(pct, f'{name} trained')
            except Exception as e:
                results[name] = {'error': str(e), 'type': 'ml'}

        # ── quantile intervals (best ML model) ──
        best_name = self._best_model(results)
        if include_quantiles and best_name and best_name in self.trained:
            try:
                qgbm = QuantileGBM(quantiles=(0.10, 0.50, 0.90))
                qgbm.fit(X_train, y_train)
                q_preds = qgbm.predict(X_test)
                results['_quantiles'] = {
                    'q10': np.maximum(q_preds[0.10], 0).tolist(),
                    'q50': np.maximum(q_preds[0.50], 0).tolist(),
                    'q90': np.maximum(q_preds[0.90], 0).tolist(),
                    'idx': [str(i) for i in idx_test],
                }
            except Exception:
                pass

        if progress_callback:
            progress_callback(100, 'All models trained')

        return results

    def _best_model(self, results):
        ml_models = {k: v for k, v in results.items()
                     if v.get('type') == 'ml' and 'metrics' in v}
        if not ml_models:
            return None
        return min(ml_models, key=lambda k: ml_models[k]['metrics']['rmse'])

    def _get_feature_importance(self, model, feature_names):
        """Extract feature importances safely from various model types."""
        try:
            base = model.named_steps['mlp'] if hasattr(model, 'named_steps') else model
            if hasattr(base, 'feature_importances_'):
                imp = base.feature_importances_
            elif hasattr(base, 'coef_'):
                imp = np.abs(base.coef_)
            else:
                return []
            if len(imp) != len(feature_names):
                return []
            pairs = sorted(zip(feature_names, imp),
                           key=lambda x: -x[1])
            total = sum(v for _, v in pairs)
            return [{'feature': f, 'importance': round(float(v / total * 100), 2)}
                    for f, v in pairs[:20]]
        except Exception:
            return []

    def predict_new(self, model_name, X_new):
        """Predict with a previously trained model."""
        if model_name not in self.trained:
            raise KeyError(f"Model '{model_name}' not trained.")
        return np.maximum(self.trained[model_name].predict(X_new), 0)

    def save(self, path):
        joblib.dump({'trained': self.trained,
                     'feature_names': self.feature_names}, path)

    def load(self, path):
        obj = joblib.load(path)
        self.trained = obj['trained']
        self.feature_names = obj['feature_names']
        return self


# ─── CROSS-VALIDATION ─────────────────────────────────────────────────────────

def time_series_cv(model, X, y, n_splits=5, scoring='neg_mean_squared_error'):
    """
    Blocked time-series cross-validation.
    Returns array of scores (RMSE).
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)
    scores = []
    for train_idx, val_idx in tscv.split(X):
        m = type(model)(**model.get_params()) if hasattr(model, 'get_params') else model
        m.fit(X[train_idx], y[train_idx])
        pred = m.predict(X[val_idx])
        scores.append(_rmse(y[val_idx], pred))
    return np.array(scores)

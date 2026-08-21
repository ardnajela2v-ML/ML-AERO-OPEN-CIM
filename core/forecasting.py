"""Modelos de planeación para demanda y variables macroeconómicas.

El PML no se pronostica en este módulo: el flujo vigente conserva el perfil
horario de 2018 y lo replica sin escalamiento durante la vida del proyecto.
USD/MXN y tasa de interés sí se estiman con ventanas históricas recientes y
validación cronológica.
"""
import os
import warnings
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import PolynomialFeatures
from sklearn.pipeline import make_pipeline

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    from statsmodels.tsa.arima.model import ARIMA
    HAS_STATSMODELS = True
except Exception:
    HAS_STATSMODELS = False

warnings.filterwarnings('ignore')


# ─── LECTURA DE SERIES ────────────────────────────────────────────────────────

def read_one_column(path):
    """Lee archivo de texto con un valor por línea (LOAD, JUCHITANDEMANDA)."""
    vals = []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                vals.append(float(line.split(',')[0]))
            except ValueError:
                continue
    return np.array(vals, dtype=float)


def read_pml(path):
    """Lee PML_IPO.txt (compra,venta). Devuelve dict con ambas columnas."""
    compra, venta = [], []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) < 2:
                continue
            try:
                compra.append(float(parts[0]))
                venta.append(float(parts[1]))
            except ValueError:
                continue
    return {'compra': np.array(compra), 'venta': np.array(venta)}


def normalize_fixed_hourly_profile(values, expected_hours=8760):
    """Normaliza un perfil anual sin inventar una tendencia temporal.

    Los archivos PML heredados no incluyen timestamp. Si falta únicamente una
    hora (caso actual: 8759 registros), se completa con la media anual y se
    deja constancia explícita. Archivos mucho más incompletos se rechazan para
    evitar que un perfil parcial se replique como si fuera un año entero.
    """
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) > expected_hours:
        return values[:expected_hours], {
            'input_hours': int(len(values)),
            'output_hours': int(expected_hours),
            'method': 'truncate_after_expected_year',
            'filled_hours': 0,
        }
    missing = expected_hours - len(values)
    if missing < 0:
        missing = 0
    if missing > 24 or len(values) < expected_hours - 24:
        raise ValueError(
            f'El PML base requiere al menos {expected_hours - 24} horas; '
            f'el archivo contiene {len(values)}')
    if missing:
        fill = float(np.nanmean(values))
        values = np.concatenate([values, np.full(missing, fill)])
        method = 'annual_mean_fill_without_timestamp'
    else:
        method = 'none'
    return values, {
        'input_hours': int(expected_hours - missing),
        'output_hours': int(len(values)),
        'method': method,
        'filled_hours': int(missing),
    }


def hourly_to_dataframe(values, start='2018-01-01'):
    """Empaqueta vector horario en un DataFrame indexado en el tiempo."""
    n = len(values)
    idx = pd.date_range(start=start, periods=n, freq='h')
    return pd.DataFrame({'value': values}, index=idx)


# ─── ESTADÍSTICOS DESCRIPTIVOS ────────────────────────────────────────────────

def base_year_stats(values):
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return {}
    df = hourly_to_dataframe(v)
    return {
        'n_hours':     int(len(v)),
        'mean':        float(np.mean(v)),
        'median':      float(np.median(v)),
        'std':         float(np.std(v, ddof=1)),
        'min':         float(np.min(v)),
        'max':         float(np.max(v)),
        'p10':         float(np.percentile(v, 10)),
        'p90':         float(np.percentile(v, 90)),
        'annual_sum':  float(np.sum(v)),
        'load_factor': float(np.mean(v) / np.max(v)) if np.max(v) > 0 else None,
        'monthly':     [float(x) for x in df.resample('ME')['value'].mean().values],
        'diurnal':     [float(x) for x in df.groupby(df.index.hour)['value'].mean().values],
        'weekly':      [float(x) for x in df.groupby(df.index.dayofweek)['value'].mean().values],
        'duration_curve': [float(x) for x in sorted(v, reverse=True)[::max(1, len(v) // 200)]],
    }


# ─── MODELOS DE TENDENCIA ANUAL ───────────────────────────────────────────────

def _r2(y, yhat):
    y, yhat = np.asarray(y), np.asarray(yhat)
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def _rmse(y, yhat):
    return float(np.sqrt(np.mean((np.asarray(y) - np.asarray(yhat)) ** 2)))


def _mae(y, yhat):
    return float(np.mean(np.abs(np.asarray(y) - np.asarray(yhat))))


def _mape(y, yhat):
    y, yhat = np.asarray(y), np.asarray(yhat)
    mask = np.abs(y) > 1e-9
    if not mask.any():
        return None
    return float(np.mean(np.abs((y[mask] - yhat[mask]) / y[mask])) * 100)


def fit_linear(t, y):
    coef = np.polyfit(t, y, 1)
    yhat = np.polyval(coef, t)
    return {'kind': 'linear',
            'params': {'slope': float(coef[0]), 'intercept': float(coef[1])},
            'predict': lambda tt, c=coef: np.polyval(c, tt),
            'fitted': yhat}


def fit_polynomial(t, y, degree=2):
    coef = np.polyfit(t, y, degree)
    yhat = np.polyval(coef, t)
    return {'kind': f'polynomial_d{degree}',
            'params': {'coefficients': [float(c) for c in coef]},
            'predict': lambda tt, c=coef: np.polyval(c, tt),
            'fitted': yhat}


def fit_exponential(t, y):
    """y = a * exp(b * t).  Ajuste por log-lineal si y > 0, si no fall-back lineal."""
    y = np.asarray(y, dtype=float)
    if (y <= 0).any():
        return fit_linear(t, y)
    log_y = np.log(y)
    coef = np.polyfit(t, log_y, 1)
    b, log_a = coef[0], coef[1]
    a = np.exp(log_a)
    cagr = float(np.exp(b) - 1)
    yhat = a * np.exp(b * t)
    return {'kind': 'exponential',
            'params': {'a': float(a), 'b': float(b), 'cagr': cagr},
            'predict': lambda tt, a=a, b=b: a * np.exp(b * tt),
            'fitted': yhat}


def fit_logarithmic(t, y):
    """y = a + b * log(t+1) — saturación lenta."""
    log_t = np.log(np.asarray(t) + 1)
    coef = np.polyfit(log_t, y, 1)
    yhat = coef[0] * log_t + coef[1]
    return {'kind': 'logarithmic',
            'params': {'a': float(coef[1]), 'b': float(coef[0])},
            'predict': lambda tt, c=coef: c[0] * np.log(np.asarray(tt) + 1) + c[1],
            'fitted': yhat}


def fit_logistic(t, y):
    """Crecimiento logístico: y = K / (1 + exp(-r*(t-t0))).
    Ajuste no lineal con scipy.optimize si está disponible; si no, fallback.
    """
    try:
        from scipy.optimize import curve_fit
        def logistic(tt, K, r, t0):
            return K / (1.0 + np.exp(-r * (tt - t0)))
        K0 = float(np.max(y) * 1.5)
        p0 = [K0, 0.1, float(np.median(t))]
        popt, _ = curve_fit(logistic, t, y, p0=p0, maxfev=4000)
        K, r, t0 = popt
        yhat = logistic(t, *popt)
        return {'kind': 'logistic',
                'params': {'K': float(K), 'r': float(r), 't0': float(t0)},
                'predict': lambda tt, p=popt: p[0] / (1.0 + np.exp(-p[1] * (np.asarray(tt) - p[2]))),
                'fitted': yhat}
    except Exception:
        return fit_exponential(t, y)


def fit_holt_winters(t, y):
    """Suavizado exponencial con tendencia amortiguada (sin estacionalidad anual)."""
    if not HAS_STATSMODELS or len(y) < 4:
        return fit_linear(t, y)
    try:
        model = ExponentialSmoothing(np.asarray(y, dtype=float),
                                     trend='add', damped_trend=True,
                                     seasonal=None,
                                     initialization_method='estimated')
        fit = model.fit(optimized=True)
        yhat = fit.fittedvalues
        params = {k: float(v) for k, v in fit.params.items()
                  if not isinstance(v, np.ndarray)}
        def predictor(tt, fit=fit, last_t=t[-1]):
            tt = np.asarray(tt)
            future_steps = tt - last_t
            preds = np.zeros_like(tt, dtype=float)
            for i, step in enumerate(future_steps):
                if step <= 0:
                    idx = int(round(step + len(t) - 1))
                    preds[i] = yhat[max(0, min(idx, len(yhat) - 1))]
                else:
                    fc = fit.forecast(int(step))
                    preds[i] = float(fc[-1])
            return preds
        return {'kind': 'holt_winters',
                'params': params,
                'predict': predictor,
                'fitted': yhat}
    except Exception:
        return fit_linear(t, y)


def fit_mean_reversion(t, y, target_b=None):
    """Modelo de reversión a la media tipo Vasicek: y(t) tiende asintóticamente a b."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(y) < 3:
        return fit_linear(t, y)
    
    # Si no se provee target_b, estimamos la media histórica
    b = float(target_b) if target_b is not None else float(np.mean(y))
    
    try:
        from scipy.optimize import curve_fit
        def vasicek_mean(tt, a, r0):
            # tt es tiempo relativo, asumimos t[0] = 0
            t_rel = tt - t[0]
            return r0 * np.exp(-a * t_rel) + b * (1 - np.exp(-a * t_rel))
        
        p0 = [0.1, y[0]]  # a=0.1 (velocidad de reversión), r0=y[0]
        # Restringimos a > 0 para forzar reversión, r0 > 0
        popt, _ = curve_fit(vasicek_mean, t, y, p0=p0, bounds=([1e-5, 0], [np.inf, np.inf]), maxfev=2000)
        a, r0 = popt
        yhat = vasicek_mean(t, *popt)
        
        last_t = t[-1]
        last_y = y[-1]
        
        def predictor(tt, a_fit=a, r0_fit=r0, last_y=last_y, last_t=last_t, target_b=b):
            tt = np.asarray(tt)
            preds = np.zeros_like(tt, dtype=float)
            for i, val in enumerate(tt):
                if val <= last_t:
                    preds[i] = r0_fit * np.exp(-a_fit * (val - t[0])) + target_b * (1 - np.exp(-a_fit * (val - t[0])))
                else:
                    dt = val - last_t
                    preds[i] = last_y * np.exp(-a_fit * dt) + target_b * (1 - np.exp(-a_fit * dt))
            return preds
            
        return {'kind': 'mean_reversion',
                'params': {'a': float(a), 'b': float(b), 'r0': float(r0)},
                'predict': predictor,
                'fitted': yhat}
    except Exception:
        return fit_linear(t, y)

def fit_arima(t, y, order=(1, 1, 1)):
    """ARIMA simple para extrapolación anual."""
    if not HAS_STATSMODELS or len(y) < 5:
        return fit_linear(t, y)
    try:
        model = ARIMA(np.asarray(y, dtype=float), order=order)
        fit = model.fit()
        yhat = fit.fittedvalues
        params = {'order': list(order),
                  'aic': float(fit.aic), 'bic': float(fit.bic)}
        last_t = t[-1]
        n_hist = len(t)
        def predictor(tt, fit=fit, last_t=last_t, n_hist=n_hist, yhat=yhat):
            tt = np.asarray(tt)
            preds = np.zeros_like(tt, dtype=float)
            future_mask = tt > last_t
            if future_mask.any():
                steps = int(np.max(tt[future_mask]) - last_t)
                fc = np.asarray(fit.forecast(steps))
                for i, val in enumerate(tt):
                    if val > last_t:
                        idx = int(round(val - last_t)) - 1
                        preds[i] = float(fc[max(0, min(idx, len(fc) - 1))])
                    else:
                        idx = int(round(val))
                        preds[i] = float(yhat[max(0, min(idx, len(yhat) - 1))])
            else:
                for i, val in enumerate(tt):
                    idx = int(round(val))
                    preds[i] = float(yhat[max(0, min(idx, len(yhat) - 1))])
            return preds
        return {'kind': 'arima',
                'params': params,
                'predict': predictor,
                'fitted': yhat}
    except Exception:
        return fit_linear(t, y)


# ─── MODELOS DE MACHINE LEARNING (detrend + ML residual + retrend) ────────────
# Los árboles no extrapolan: convergen a constante fuera del rango entrenado.
# Solución: separamos tendencia (regresión polinomial que extrapola) y residuos
# (lo que el ML aprende). Así la proyección sigue la tendencia histórica y el
# ML añade variabilidad/patrón realista por encima.

def _build_lag_only(y, n_lags):
    """Matriz X = [lag_1..lag_n] (sin t) y vector y_target."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n <= n_lags:
        return None, None
    rows = n - n_lags
    X = np.zeros((rows, n_lags))
    tgt = np.zeros(rows)
    for i in range(rows):
        for k in range(n_lags):
            X[i, k] = y[i + n_lags - 1 - k]
        tgt[i] = y[i + n_lags]
    return X, tgt


def _fit_ml_trend_residual(t, y, build_model, kind, params,
                            n_lags=3, trend_degree=1, scale_x=False):
    """Patrón general:
        1. Ajusta tendencia polinomial sobre y → y_trend(t)
        2. Calcula residuos r = y - y_trend
        3. Entrena ML sobre lags de residuos
        4. Predicción: y_trend_futuro(t) + ML_recursivo_residuos + ruido
    Esto evita la convergencia a constante porque la tendencia extrapola y el
    ML solo aporta el patrón residual con variabilidad."""
    from sklearn.preprocessing import StandardScaler
    t_arr = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)

    # 1) Tendencia
    coef = np.polyfit(t_arr, y, trend_degree)
    y_trend = np.polyval(coef, t_arr)
    residuals = y - y_trend

    # 2) Lags sobre residuos
    n_lags = max(1, min(n_lags, len(residuals) // 4))
    X, r_tgt = _build_lag_only(residuals, n_lags)
    if X is None or len(X) < 4:
        return fit_linear(t_arr, y)

    # 3) Entrenar (con escalado opcional para MLP)
    if scale_x:
        scaler = StandardScaler().fit(X)
        Xs = scaler.transform(X)
        r_std = float(np.std(r_tgt) + 1e-9)
        r_mean = float(np.mean(r_tgt))
        rs = (r_tgt - r_mean) / r_std
        model = build_model()
        model.fit(Xs, rs)
        r_fit = model.predict(Xs) * r_std + r_mean
    else:
        scaler = None
        model = build_model()
        model.fit(X, r_tgt)
        r_fit = model.predict(X)

    fitted = np.concatenate([y[:n_lags], y_trend[n_lags:] + r_fit])

    def predictor(tt, m=model, sc=scaler, c=coef,
                  hist_r=residuals.tolist(), nl=n_lags,
                  sx=scale_x,
                  rmean=(float(np.mean(r_tgt)) if scale_x else 0.0),
                  rstd=(float(np.std(r_tgt) + 1e-9) if scale_x else 1.0)):
        tt = np.asarray(tt, dtype=float)
        y_trend_f = np.polyval(c, tt)
        history_r = list(hist_r)
        r_future = []
        for _ in tt:
            feats = np.array([[history_r[-(k + 1)] for k in range(nl)]])
            if sx:
                feats = sc.transform(feats)
                rp = float(m.predict(feats)[0]) * rstd + rmean
            else:
                rp = float(m.predict(feats)[0])
            r_future.append(rp)
            history_r.append(rp)
        return y_trend_f + np.array(r_future)

    return {'kind': kind, 'params': params, 'predict': predictor, 'fitted': fitted}


def fit_random_forest_ts(t, y, n_lags=3):
    """RF sobre residuos de tendencia lineal. Extrapola por tendencia + variación residual."""
    from sklearn.ensemble import RandomForestRegressor
    return _fit_ml_trend_residual(
        t, y,
        build_model=lambda: RandomForestRegressor(
            n_estimators=200, max_depth=5, min_samples_leaf=2, random_state=42),
        kind='ml_random_forest',
        params={'n_estimators': 200, 'max_depth': 5, 'n_lags': int(min(n_lags, len(y) // 4 or 1)),
                'detrend': 'linear'},
        n_lags=n_lags, trend_degree=1, scale_x=False,
    )


def fit_gradient_boost_ts(t, y, n_lags=3):
    """Gradient Boosting sobre residuos de tendencia lineal."""
    from sklearn.ensemble import GradientBoostingRegressor
    return _fit_ml_trend_residual(
        t, y,
        build_model=lambda: GradientBoostingRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42),
        kind='ml_gradient_boost',
        params={'n_estimators': 200, 'max_depth': 3, 'learning_rate': 0.05,
                'n_lags': int(min(n_lags, len(y) // 4 or 1)), 'detrend': 'linear'},
        n_lags=n_lags, trend_degree=1, scale_x=False,
    )


def fit_mlp_ts(t, y, n_lags=3):
    """MLP sobre residuos de tendencia lineal con escalado. La red aprende patrones
    no-lineales del residuo y la tendencia se aplica por encima."""
    from sklearn.neural_network import MLPRegressor
    return _fit_ml_trend_residual(
        t, y,
        build_model=lambda: MLPRegressor(
            hidden_layer_sizes=(32, 16), activation='tanh', solver='lbfgs',
            max_iter=800, random_state=42, alpha=1e-2),
        kind='ml_mlp',
        params={'hidden': '(32,16)', 'activation': 'tanh', 'alpha': 0.01,
                'n_lags': int(min(n_lags, len(y) // 4 or 1)), 'detrend': 'linear'},
        n_lags=n_lags, trend_degree=1, scale_x=True,
    )


TREND_FITTERS = {
    'linear':         fit_linear,
    'polynomial':     lambda t, y: fit_polynomial(t, y, degree=2),
    'exponential':    fit_exponential,
    'logarithmic':    fit_logarithmic,
    'logistic':       fit_logistic,
    'holt_winters':   fit_holt_winters,
    'arima':          fit_arima,
    'mean_reversion': fit_mean_reversion,
    'random_forest':  fit_random_forest_ts,
    'gradient_boost': fit_gradient_boost_ts,
    'mlp':            fit_mlp_ts,
}


# ─── ARENA DE MODELOS DE PROYECCIÓN ───────────────────────────────────────────

def synthesize_annual_series(hourly_values, n_years_history=10, growth_seed=0.025,
                              noise_pct=0.015):
    """
    A partir de un único año horario observado, construye una historia anual
    sintética asumiendo una tasa media de crecimiento y un ruido moderado.
    Devuelve totales anuales para alimentar los ajustes de tendencia.

    growth_seed: tasa anual histórica supuesta (CAGR base).
    noise_pct  : desviación estándar relativa del ruido año a año.
    """
    hourly_values = np.asarray(hourly_values, dtype=float)
    base_total = float(np.nansum(hourly_values))
    rng = np.random.default_rng(42)
    years = np.arange(n_years_history + 1)  # 0..N (último = año observado)
    # Crecimiento hacia adelante desde un punto inicial menor
    factors = np.array([(1.0 + growth_seed) ** (y - n_years_history) for y in years])
    noise = rng.normal(0, noise_pct, size=len(years))
    annuals = base_total * factors * (1.0 + noise)
    return years, annuals  # años relativos: t=0 es N años atrás, t=N el observado


def fit_all_trend_models(t, y, models=None):
    """Ajusta varios modelos de tendencia y devuelve métricas comparativas."""
    keys = list(TREND_FITTERS.keys()) if models is None else models
    out = {}
    for k in keys:
        fitter = TREND_FITTERS.get(k)
        if fitter is None:
            continue
        try:
            res = fitter(t, y)
            yhat = res['fitted']
            out[k] = {
                'kind':    res['kind'],
                'params':  res['params'],
                'metrics': {
                    'r2':   _r2(y, yhat),
                    'rmse': _rmse(y, yhat),
                    'mape': _mape(y, yhat),
                    # Desviación de los errores del ajuste, no de la propia
                    # tendencia. Se reutiliza para las bandas futuras.
                    'residual_sigma': float(np.std(
                        np.asarray(y, dtype=float) - np.asarray(yhat, dtype=float),
                        ddof=1,
                    )) if len(yhat) > 1 else 0.0,
                },
                'fitted_values': [float(x) for x in yhat],
                '_predict': res['predict'],
                'fitted':   [float(x) for x in yhat],
            }
        except Exception as e:
            out[k] = {'error': str(e)}
    return out


def chronological_trend_validation(t, y, models=None, holdout_fraction=0.20,
                                   min_train=6, min_test=2):
    """Valida tendencias sin fuga temporal usando el bloque final como prueba.

    Los modelos se ajustan únicamente con los primeros años y se evalúan sobre
    los años más recientes. Esta métrica fuera de muestra debe usarse para
    elegir la trayectoria que después se reajusta con todo el histórico.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = np.isfinite(t) & np.isfinite(y)
    t, y = t[valid], y[valid]
    n = len(y)
    if n < min_train + 1:
        return {
            'available': False,
            'reason': f'Se requieren al menos {min_train + 1} observaciones anuales',
            'n_total': int(n),
            'models': {},
        }

    n_test = max(min_test, int(np.ceil(n * float(holdout_fraction))))
    n_test = min(n_test, max(1, n - min_train))
    split = n - n_test
    train_t, test_t = t[:split], t[split:]
    train_y, test_y = y[:split], y[split:]
    keys = list(TREND_FITTERS.keys()) if models is None else list(models)
    rows = {}
    scale = max(float(np.mean(np.abs(test_y))), 1e-9)

    for key in keys:
        fitter = TREND_FITTERS.get(key)
        if fitter is None:
            continue
        try:
            result = fitter(train_t, train_y)
            predicted = np.asarray(result['predict'](test_t), dtype=float)
            if predicted.shape != test_y.shape or not np.isfinite(predicted).all():
                raise ValueError('la proyección fuera de muestra no es finita')
            rows[key] = {
                'r2': _r2(test_y, predicted),
                'rmse': _rmse(test_y, predicted),
                'mae': _mae(test_y, predicted),
                'bias': float(np.mean(predicted - test_y)),
                'mape': _mape(test_y, predicted),
                'nrmse': _rmse(test_y, predicted) / scale,
                'actual': [float(v) for v in test_y],
                'predicted': [float(v) for v in predicted],
            }
        except Exception as error:
            rows[key] = {'error': str(error)}

    return {
        'available': bool(any('error' not in row for row in rows.values())),
        'method': 'chronological_holdout',
        'holdout_fraction': float(holdout_fraction),
        'train_t': [float(v) for v in train_t],
        'test_t': [float(v) for v in test_t],
        'n_train': int(len(train_y)),
        'n_test': int(len(test_y)),
        'models': rows,
    }


def best_validated_trend_model(fitted_models, validation):
    """Elige por nRMSE cronológico; sólo cae a R² in-sample sin validación."""
    rows = (validation or {}).get('models', {})
    best_key, best_score = None, np.inf
    for key, model in fitted_models.items():
        if 'error' in model:
            continue
        row = rows.get(key, {})
        nrmse = row.get('nrmse')
        if nrmse is None or not np.isfinite(nrmse):
            continue
        complexity_penalty = 0.02 if key in ('polynomial', 'logistic', 'mlp') else 0.0
        score = float(nrmse) + complexity_penalty
        if score < best_score:
            best_key, best_score = key, score
    return best_key or best_trend_model(fitted_models)


def _forecast_fx_candidate(train_values, n_future, model_name):
    """Pronostica niveles USD/MXN en logaritmos para un candidato estable."""
    values = np.asarray(train_values, dtype=float)
    values = values[np.isfinite(values) & (values > 0)]
    if len(values) < 24:
        raise ValueError('Se requieren al menos 24 meses para modelar USD/MXN')
    logs = np.log(values)
    n_future = int(n_future)

    if model_name == 'holt_damped':
        if not HAS_STATSMODELS:
            raise RuntimeError('statsmodels no disponible')
        fit = ExponentialSmoothing(
            logs, trend='add', damped_trend=True, seasonal=None,
            initialization_method='estimated',
        ).fit(optimized=True)
        future_logs = np.asarray(fit.forecast(n_future), dtype=float)
    elif model_name == 'arima_log_returns':
        if not HAS_STATSMODELS:
            raise RuntimeError('statsmodels no disponible')
        returns = np.diff(logs)
        order = (1, 0, 1) if len(returns) >= 48 else (1, 0, 0)
        fit = ARIMA(returns, order=order).fit()
        future_returns = np.asarray(fit.forecast(n_future), dtype=float)
        recent = returns[-min(60, len(returns)):]
        lo = max(float(np.percentile(recent, 2)), -0.06)
        hi = min(float(np.percentile(recent, 98)), 0.06)
        future_returns = np.clip(future_returns, lo, hi)
        future_logs = logs[-1] + np.cumsum(future_returns)
    elif model_name == 'robust_damped_drift':
        returns = np.diff(logs)
        recent = returns[-min(60, len(returns)):]
        lo, hi = np.percentile(recent, [10, 90])
        clipped = np.clip(recent, lo, hi)
        drift = float(np.median(clipped))
        # La deriva reciente se extingue gradualmente; no se extrapola durante
        # 25 años como una pendiente lineal ilimitada.
        damping = np.exp(-np.arange(1, n_future + 1) / 48.0)
        future_logs = logs[-1] + np.cumsum(drift * damping)
    else:
        raise ValueError(f'Modelo FX desconocido: {model_name}')

    forecast = np.exp(future_logs)
    if len(forecast) != n_future or not np.isfinite(forecast).all():
        raise ValueError('El pronóstico FX no produjo valores finitos')
    return forecast


def forecast_fx_recent(monthly_series, horizon_years=25,
                       window_years_options=(5, 8, 10),
                       model_names=('holt_damped', 'arima_log_returns',
                                    'robust_damped_drift')):
    """Selecciona y proyecta USD/MXN usando sólo el régimen reciente.

    La selección se realiza con los 24 meses finales como bloque fuera de
    muestra. Se comparan ventanas recientes y modelos en escala logarítmica;
    después se reajusta el ganador con su ventana completa. La salida anual se
    suaviza con cambios observados recientemente, evitando que crisis de los
    años noventa gobiernen el horizonte del proyecto.
    """
    if not isinstance(monthly_series, pd.Series):
        raise TypeError('monthly_series debe ser pandas.Series')
    series = pd.to_numeric(monthly_series, errors='coerce').dropna()
    series.index = pd.to_datetime(series.index)
    series = series.sort_index().resample('MS').mean().dropna()
    series = series[series > 0]
    if len(series) < 60:
        raise ValueError('Se requieren al menos cinco años mensuales de USD/MXN')

    # No tratar un mes parcial como observación mensual completa.
    source_last_date = pd.Timestamp(monthly_series.dropna().index.max())
    if source_last_date.normalize() < (source_last_date + pd.offsets.MonthEnd(0)).normalize():
        incomplete_month = source_last_date.to_period('M')
        series = series[series.index.to_period('M') < incomplete_month]
    if len(series) < 60:
        raise ValueError('Histórico mensual completo insuficiente')

    holdout_months = 24
    validation_rows = []
    for window_years in window_years_options:
        train_months = int(window_years) * 12
        needed = train_months + holdout_months
        if len(series) < needed:
            continue
        train = series.iloc[-needed:-holdout_months]
        test = series.iloc[-holdout_months:]
        actual = test.to_numpy(dtype=float)
        scale = max(float(np.mean(np.abs(actual))), 1e-9)
        for model_name in model_names:
            try:
                predicted = _forecast_fx_candidate(
                    train.to_numpy(dtype=float), len(test), model_name)
                error = predicted - actual
                rmse = float(np.sqrt(np.mean(error ** 2)))
                mae = float(np.mean(np.abs(error)))
                bias = float(np.mean(error))
                nrmse = rmse / scale
                # Penalización pequeña para evitar elegir una trayectoria con
                # buen RMSE pero sesgo persistente en el bloque final.
                score = nrmse + 0.20 * abs(bias) / scale
                validation_rows.append({
                    'model': model_name,
                    'window_years': int(window_years),
                    'train_start': train.index[0].strftime('%Y-%m'),
                    'train_end': train.index[-1].strftime('%Y-%m'),
                    'test_start': test.index[0].strftime('%Y-%m'),
                    'test_end': test.index[-1].strftime('%Y-%m'),
                    'rmse': rmse,
                    'mae': mae,
                    'bias': bias,
                    'nrmse': nrmse,
                    'score': score,
                })
            except Exception as error:
                validation_rows.append({
                    'model': model_name,
                    'window_years': int(window_years),
                    'error': str(error),
                })

    valid_rows = [row for row in validation_rows if 'error' not in row]
    if not valid_rows:
        raise ValueError('Ningún modelo FX superó la validación temporal')
    best = min(valid_rows, key=lambda row: row['score'])
    window_months = int(best['window_years']) * 12
    final_train = series.iloc[-window_months:]

    project_start_year = int(series.index[-1].year) + 1
    project_end_year = project_start_year + int(horizon_years) - 1
    future_index = pd.date_range(
        series.index[-1] + pd.offsets.MonthBegin(1),
        f'{project_end_year}-12-01', freq='MS')
    raw_monthly = _forecast_fx_candidate(
        final_train.to_numpy(dtype=float), len(future_index), best['model'])
    raw_series = pd.Series(raw_monthly, index=future_index)
    raw_annual = raw_series.resample('YE').mean()
    raw_annual = raw_annual[raw_annual.index.year >= project_start_year].iloc[:int(horizon_years)]

    # Cambios anuales de años completos recientes. El año parcial permanece
    # visible en el histórico, pero no define una variación anual.
    observed_annual = series.resample('YE').mean()
    observed_annual = observed_annual[observed_annual.index.year < project_start_year]
    recent_annual = observed_annual.iloc[-min(11, len(observed_annual)):]
    annual_changes = np.diff(np.log(recent_annual.to_numpy(dtype=float)))
    if len(annual_changes):
        lo = max(float(np.percentile(annual_changes, 10)), -0.12)
        hi = min(float(np.percentile(annual_changes, 90)), 0.12)
        if lo > 0: lo = 0.0
        if hi < 0: hi = 0.0
        recent_drift = float(np.clip(np.median(annual_changes), -0.03, 0.04))
    else:
        lo, hi, recent_drift = -0.05, 0.08, 0.0

    anchor = float(series.iloc[-12:].mean())
    annual = []
    prev = anchor
    previous_change = recent_drift
    for i, raw in enumerate(raw_annual.to_numpy(dtype=float)):
        raw_change = float(np.log(max(raw, 1e-9) / max(prev, 1e-9)))
        # La influencia del modelo mensual y de la deriva reciente se extingue
        # en el horizonte largo. Así el valor central converge gradualmente a
        # una meseta en vez de perpetuar durante 25 años una depreciación corta.
        long_target = recent_drift * np.exp(-i / 5.0)
        raw_weight = 0.65 * np.exp(-i / 7.0)
        desired = raw_weight * raw_change + (1.0 - raw_weight) * long_target
        change = float(np.clip(0.50 * previous_change + 0.50 * desired, lo, hi))
        prev = float(prev * np.exp(change))
        annual.append(prev)
        previous_change = change

    years = list(range(project_start_year, project_start_year + len(annual)))
    relative_error = max(float(best['nrmse']), 0.025)
    band = np.minimum(
        0.20,
        relative_error * np.sqrt(np.arange(1, len(annual) + 1) / 3.0))
    annual_arr = np.asarray(annual, dtype=float)
    ci_low = annual_arr * (1.0 - band)
    ci_high = annual_arr * (1.0 + band)

    model_key = f"{best['model']}_w{best['window_years']}"
    projection = {
        model_key: {
            'years': years,
            'annual': [float(v) for v in annual_arr],
            'ci_low': [float(v) for v in ci_low],
            'ci_high': [float(v) for v in ci_high],
            'cagr': float((annual_arr[-1] / anchor) ** (1.0 / len(annual_arr)) - 1.0),
            'kind': best['model'],
            'params': {
                'window_years': int(best['window_years']),
                'training_start': final_train.index[0].strftime('%Y-%m'),
                'training_end': final_train.index[-1].strftime('%Y-%m'),
            },
            'metrics': {
                'validation_rmse': float(best['rmse']),
                'validation_mae': float(best['mae']),
                'validation_bias': float(best['bias']),
                'validation_nrmse': float(best['nrmse']),
            },
        }
    }
    return {
        'best_model': model_key,
        'projection': projection,
        'validation': {
            'available': True,
            'method': 'rolling_recent_window_holdout_24m',
            'n_test': holdout_months,
            'selected': best,
            'candidates': validation_rows,
        },
        'projection_policy': {
            'applied': True,
            'basis': 'recent_complete_year_log_changes',
            'training_window_years': int(best['window_years']),
            'annual_change_floor': float(np.expm1(lo)),
            'annual_change_ceiling': float(np.expm1(hi)),
            'recent_median_change': float(np.expm1(recent_drift)),
            'long_horizon_damping': True,
            'planning_band_max_relative': 0.20,
        },
        'monthly_history': series,
        'project_start_year': project_start_year,
        'training_start': final_train.index[0].strftime('%Y-%m'),
        'training_end': final_train.index[-1].strftime('%Y-%m'),
    }


def constrain_market_projection(projection, history, category,
                                anchor_rate_pct=None):
    """Impone límites aprendidos del histórico a trayectorias de mercado.

    No reemplaza al modelo seleccionado: limita saltos anuales fuera del rango
    robusto observado y conserva positividad. Para FX se puede mezclar una
    pequeña ancla macroeconómica explícita, sin ocultarla como dato entrenado.
    """
    history = np.asarray(history, dtype=float)
    history = history[np.isfinite(history)]
    if len(history) < 2:
        return projection, {'applied': False, 'reason': 'histórico insuficiente'}

    category = str(category or '').lower()
    recent = history[-min(20, len(history)):]
    policy = {
        'applied': True,
        'category': category,
        'basis': 'robust_historical_annual_changes',
        'history_points': int(len(history)),
    }

    if category in ('fx', 'pml'):
        positive = recent[recent > 0]
        changes = np.diff(np.log(positive)) if len(positive) >= 2 else np.array([0.0])
        lo = max(float(np.percentile(changes, 5)), -0.20 if category == 'fx' else -0.35)
        hi = min(float(np.percentile(changes, 95)), 0.25 if category == 'fx' else 0.45)
        if lo > hi:
            lo, hi = min(lo, hi), max(lo, hi)
        trained_drift = float(np.median(changes))
        anchor_weight = 0.0
        target_drift = trained_drift
        if category == 'fx' and anchor_rate_pct is not None:
            anchor = np.log1p(float(anchor_rate_pct) / 100.0)
            anchor_weight = 0.15
            target_drift = (1.0 - anchor_weight) * trained_drift + anchor_weight * anchor
        policy.update({
            'change_space': 'log_return',
            'annual_change_floor': float(np.expm1(lo)),
            'annual_change_ceiling': float(np.expm1(hi)),
            'trained_median_change': float(np.expm1(trained_drift)),
            'anchor_weight': anchor_weight,
        })

        lower_bound, upper_bound = 1e-6, np.inf
        def stabilize(raw_values):
            prev = max(float(history[-1]), lower_bound)
            out = []
            for raw in raw_values:
                raw = max(float(raw), lower_bound) if np.isfinite(raw) else prev
                raw_change = np.log(raw / prev)
                change = np.clip(0.85 * raw_change + 0.15 * target_drift, lo, hi)
                prev = float(np.clip(prev * np.exp(change), lower_bound, upper_bound))
                out.append(prev)
            return np.asarray(out)
    elif category == 'interest':
        changes = np.diff(recent)
        lo = max(float(np.percentile(changes, 5)), -5.0)
        hi = min(float(np.percentile(changes, 95)), 5.0)
        if lo > hi:
            lo, hi = min(lo, hi), max(lo, hi)
        lower_bound = 0.0
        upper_bound = max(30.0, float(np.percentile(history, 99)) * 1.25)
        policy.update({
            'change_space': 'percentage_points',
            'annual_change_floor': lo,
            'annual_change_ceiling': hi,
            'value_floor_pct': lower_bound,
            'value_ceiling_pct': upper_bound,
        })

        def stabilize(raw_values):
            prev = float(np.clip(history[-1], lower_bound, upper_bound))
            out = []
            for raw in raw_values:
                raw = float(raw) if np.isfinite(raw) else prev
                change = float(np.clip(raw - prev, lo, hi))
                prev = float(np.clip(prev + change, lower_bound, upper_bound))
                out.append(prev)
            return np.asarray(out)
    else:
        return projection, {'applied': False, 'reason': 'categoría sin política'}

    for model in projection.values():
        if not isinstance(model, dict) or not model.get('annual'):
            continue
        raw = np.asarray(model['annual'], dtype=float)
        annual = stabilize(raw)
        original_low = np.asarray(model.get('ci_low', raw), dtype=float)
        original_high = np.asarray(model.get('ci_high', raw), dtype=float)
        half_width = np.maximum((original_high - original_low) / 2.0, 0.0)
        model['annual_unconstrained'] = [float(v) for v in raw]
        model['annual'] = [float(v) for v in annual]
        model['ci_low'] = [float(v) for v in np.maximum(annual - half_width, lower_bound)]
        model['ci_high'] = [float(v) for v in np.minimum(annual + half_width, upper_bound)]
        if len(annual) and history[-1] > 0:
            model['cagr'] = float((annual[-1] / history[-1]) ** (1.0 / len(annual)) - 1.0)

    return projection, policy


def project_25_years(hourly_values, fitted_models, horizon_years=25,
                      base_year_label=2024, ci_pct=0.10,
                      include_hourly=False):
    """
    Para cada modelo ajustado, proyecta el total anual a `horizon_years` y
    opcionalmente distribuye en una serie horaria escalando el año base.
    Devuelve dict { model_key: { 'annual': [...], 'years': [...],
                                  'ci_low':, 'ci_high':, 'cagr': } }
    Set include_hourly=True to also get the full hourly time-series (large).
    """
    hourly_values = np.asarray(hourly_values, dtype=float)
    base_total = float(np.nansum(hourly_values))
    base_profile = (hourly_values / base_total) if (base_total > 0 and include_hourly) else None

    out = {}
    for key, m in fitted_models.items():
        if 'error' in m or '_predict' not in m:
            continue
        fn = m['_predict']
        # historia: 0..n; t_obs (n) corresponde al año base
        n_hist = len(m['fitted'])
        t_obs = n_hist - 1
        future_t = np.arange(t_obs + 1, t_obs + 1 + horizon_years)
        annual = np.asarray(fn(future_t), dtype=float)
        annual = np.maximum(annual, 0)

        # CAGR efectivo entre observado y último año proyectado
        first = float(m['fitted'][-1])
        last = float(annual[-1])
        if first > 0 and horizon_years > 0:
            cagr = float((last / first) ** (1.0 / horizon_years) - 1.0)
        else:
            cagr = None

        # Bandas de incertidumbre: error residual histórico. La versión
        # anterior usaba la dispersión de la tendencia ajustada y confundía
        # crecimiento con incertidumbre.
        sigma = float((m.get('metrics') or {}).get('residual_sigma') or 0.0)
        sigma_growing = sigma * np.sqrt(np.arange(1, horizon_years + 1))
        ci_low  = np.maximum(annual - 1.96 * sigma_growing, 0)
        ci_high = annual + 1.96 * sigma_growing

        entry = {
            'years':       [int(base_year_label + i + 1) for i in range(horizon_years)],
            'annual':      [float(x) for x in annual],
            'ci_low':      [float(x) for x in ci_low],
            'ci_high':     [float(x) for x in ci_high],
            'cagr':        cagr,
            'kind':        m.get('kind'),
            'params':      m.get('params'),
            'metrics':     m.get('metrics'),
        }

        # Serie horaria proyectada: only if requested (very large)
        if include_hourly and base_profile is not None:
            hourly_proj = np.concatenate([base_profile * a for a in annual])
            entry['hourly'] = [float(x) for x in hourly_proj]

        out[key] = entry
    return out


def best_trend_model(fitted_models):
    """Selecciona el modelo de tendencia con mejor R² (penalizando complejidad)."""
    best_key, best_score = None, -np.inf
    for k, m in fitted_models.items():
        if 'error' in m:
            continue
        r2 = m.get('metrics', {}).get('r2')
        if r2 is None:
            continue
        # ligero descuento a polinomio y logístico para evitar sobreajuste
        penalty = 0.02 if k in ('polynomial', 'logistic') else 0.0
        score = r2 - penalty
        if score > best_score:
            best_key, best_score = k, score
    return best_key


def fit_all_hourly_models(t, y, models=None):
    """Ajusta modelos de serie de tiempo horaria, optimizado para 8760 puntos."""
    keys = ['linear', 'holt_winters', 'mean_reversion'] if models is None else models
    out = {}
    for k in keys:
        fitter = TREND_FITTERS.get(k)
        if fitter is None:
            continue
        try:
            res = fitter(t, y)
            yhat = res['fitted']
            out[k] = {
                'kind':    res['kind'],
                'params':  res['params'],
                'metrics': {
                    'r2':   _r2(y, yhat),
                    'rmse': _rmse(y, yhat),
                    'mae':  _mae(y, yhat),
                    'mape': _mape(y, yhat),
                },
                'fitted': yhat.tolist(),
            }
        except Exception:
            pass
    return out

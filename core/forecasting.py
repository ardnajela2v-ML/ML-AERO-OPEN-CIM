"""
AERO by OPEN CIM — Pronóstico de demanda eléctrica y precios PML a 25 años.

Series horarias (8760 h) usadas como año base; sobre ellas se ajustan modelos
de tendencia anual y se proyecta el crecimiento agregado a 25 años, preservando
la firma horaria/estacional del año base.
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


def hourly_to_dataframe(values, start='2024-01-01'):
    """Empaqueta vector horario en un DataFrame indexado en el tiempo."""
    n = len(values)
    idx = pd.date_range(start=start, periods=n, freq='H')
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
        'monthly':     [float(x) for x in df.resample('M')['value'].mean().values],
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
        popt, _ = curve_fit(vasicek_mean, t, y, p0=p0, maxfev=2000)
        a, r0 = popt
        yhat = vasicek_mean(t, *popt)
        
        def predictor(tt, popt=popt):
            return vasicek_mean(tt, *popt)
            
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


TREND_FITTERS = {
    'linear':         fit_linear,
    'polynomial':     lambda t, y: fit_polynomial(t, y, degree=2),
    'exponential':    fit_exponential,
    'logarithmic':    fit_logarithmic,
    'logistic':       fit_logistic,
    'holt_winters':   fit_holt_winters,
    'arima':          fit_arima,
    'mean_reversion': fit_mean_reversion,
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
                },
                '_predict': res['predict'],
                'fitted':   [float(x) for x in yhat],
            }
        except Exception as e:
            out[k] = {'error': str(e)}
    return out


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

        # bandas de incertidumbre: residuos del ajuste histórico
        resid = np.asarray(m['fitted']) - np.mean(m['fitted'])
        sigma = float(np.std(resid)) if len(resid) > 1 else 0.0
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

"""
AERO by OPEN CIM — Scientific validation metrics and model comparison.
All metrics are computed without data leakage.
"""
import numpy as np
import pandas as pd
from scipy import stats
import warnings
warnings.filterwarnings('ignore')


# ─── HELPER ───────────────────────────────────────────────────────────────────

def _clean_pair(yt, yp):
    """Coerce to float ndarray and drop NaN/Inf in either side. Returns (yt, yp)."""
    yt = np.asarray(yt, dtype=float)
    yp = np.asarray(yp, dtype=float)
    mask = np.isfinite(yt) & np.isfinite(yp)
    return yt[mask], yp[mask]


# ─── CORE METRICS ─────────────────────────────────────────────────────────────

def mae(y_true, y_pred):
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    return float(np.mean(np.abs(yt - yp)))

def rmse(y_true, y_pred):
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    return float(np.sqrt(np.mean((yt - yp)**2)))

def mse(y_true, y_pred):
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    return float(np.mean((yt - yp)**2))

def r2(y_true, y_pred):
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    ss_res = np.sum((yt - yp)**2)
    ss_tot = np.sum((yt - np.mean(yt))**2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else float('nan')

def bias(y_true, y_pred):
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    return float(np.mean(yp - yt))

def nrmse(y_true, y_pred):
    """Normalised RMSE as fraction of observed mean (|mean| guard)."""
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    mean_obs = np.mean(yt)
    if abs(mean_obs) < 1e-6:
        return float('nan')
    return float(np.sqrt(np.mean((yt - yp)**2)) / mean_obs)

def nmae(y_true, y_pred):
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    mean_obs = np.mean(yt)
    if abs(mean_obs) < 1e-6:
        return float('nan')
    return float(np.mean(np.abs(yt - yp)) / mean_obs)

def mape(y_true, y_pred):
    """Mean absolute percentage error clásico (%). Excluye |y_true|<1e-6."""
    yt, yp = _clean_pair(y_true, y_pred)
    mask = np.abs(yt) > 1e-6
    if mask.sum() == 0:
        return float('nan')
    return float(np.mean(np.abs((yt[mask] - yp[mask]) / yt[mask])) * 100)

def smape(y_true, y_pred):
    """Symmetric mean absolute percentage error.

    Rango [0, 200%] según convención Makridakis. Divide por 2 para acotar a [0,100%].
    """
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    denom = (np.abs(yt) + np.abs(yp)) / 2
    valid = denom > 1e-6
    if valid.sum() == 0:
        return float('nan')
    return float(np.mean(np.abs(yt[valid] - yp[valid]) / denom[valid]) * 100)

def skill_score(y_true, y_pred, y_baseline):
    """
    Murphy skill score: 1 - MSE(model)/MSE(baseline).
    Positive = model beats baseline; 0 = same; negative = worse.
    """
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    yb = np.asarray(y_baseline, dtype=float)
    mask = np.isfinite(yt) & np.isfinite(yp) & np.isfinite(yb)
    yt, yp, yb = yt[mask], yp[mask], yb[mask]
    if len(yt) == 0:
        return float('nan')
    mse_model    = np.mean((yt - yp)**2)
    mse_baseline = np.mean((yt - yb)**2)
    if mse_baseline < 1e-12:
        return float('nan')
    return float(1 - mse_model / mse_baseline)

def skill_score_vs_climatology(y_true, y_pred):
    """Skill score con climatología (media del propio y_true) como baseline.

    Equivalente al "1 - NSE/2" en hidrología o al MSSS clásico vs media histórica.
    """
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) == 0:
        return float('nan')
    mse_model = np.mean((yt - yp)**2)
    mse_clim = np.mean((yt - np.mean(yt))**2)
    if mse_clim < 1e-12:
        return float('nan')
    return float(1 - mse_model / mse_clim)

def pinball_loss(y_true, y_quantile, alpha):
    """Pinball loss para evaluar un cuantil predicho `y_quantile` al nivel α∈(0,1).

    L_α(y, q) = max(α·(y-q), (α-1)·(y-q)). Promediado sobre la muestra.
    """
    yt, yq = _clean_pair(y_true, y_quantile)
    if len(yt) == 0:
        return float('nan')
    errors = yt - yq
    return float(np.mean(np.where(errors >= 0, alpha * errors, (alpha - 1) * errors)))

def mbe(y_true, y_pred):
    """Mean bias error."""
    return bias(y_true, y_pred)

def pearson_r(y_true, y_pred):
    yt, yp = _clean_pair(y_true, y_pred)
    if len(yt) < 3:
        return float('nan')
    r, _ = stats.pearsonr(yt, yp)
    return float(r)


def compute_all_metrics(y_true, y_pred, y_persistence=None, label='model'):
    """Return a dict with all standard metrics."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    n_pairs = int((np.isfinite(yt) & np.isfinite(yp)).sum())
    out = {
        'label':  label,
        'n':      n_pairs,
        'mae':    round(mae(yt, yp), 4),
        'rmse':   round(rmse(yt, yp), 4),
        'mse':    round(mse(yt, yp), 4),
        'r2':     round(r2(yt, yp), 4),
        'bias':   round(bias(yt, yp), 4),
        'nrmse':  round(nrmse(yt, yp), 4),
        'nmae':   round(nmae(yt, yp), 4),
        'mape':   round(mape(yt, yp), 2),
        'smape':  round(smape(yt, yp), 2),
        'pearson_r': round(pearson_r(yt, yp), 4),
        'skill_vs_climatology': round(skill_score_vs_climatology(yt, yp), 4),
    }
    if y_persistence is not None:
        yb = np.asarray(y_persistence, dtype=float)
        out['skill_vs_persistence'] = round(skill_score(yt, yp, yb), 4)
    return out


# ─── CALIBRATION ──────────────────────────────────────────────────────────────

def reliability_diagram(y_true, y_pred_q_lo, y_pred_q_hi,
                         nominal_coverage=0.80):
    """
    Compute empirical coverage for a prediction interval.
    Returns: {'nominal': float, 'empirical': float, 'sharpness': float}
    """
    yt = np.asarray(y_true)
    lo = np.asarray(y_pred_q_lo)
    hi = np.asarray(y_pred_q_hi)
    mask = ~(np.isnan(yt) | np.isnan(lo) | np.isnan(hi))
    covered = ((yt[mask] >= lo[mask]) & (yt[mask] <= hi[mask])).mean()
    sharpness = np.mean(hi[mask] - lo[mask])
    return {
        'nominal_coverage': nominal_coverage,
        'empirical_coverage': round(float(covered), 4),
        'sharpness': round(float(sharpness), 4),
        'calibration_error': round(float(abs(covered - nominal_coverage)), 4),
    }


# ─── TEMPORAL STABILITY ───────────────────────────────────────────────────────

def temporal_stability(y_true, y_pred, index, freq=None):
    """
    Compute RMSE per calendar period (month / week).
    Returns: list of {period, rmse, mae, n}
    """
    if freq is None:
        # 'ME' (Month End) requiere pandas ≥ 2.2; 'M' es la forma legacy.
        freq = 'ME' if pd.__version__ >= '2.2' else 'M'
    df = pd.DataFrame({'y': np.asarray(y_true), 'yp': np.asarray(y_pred)},
                      index=index)
    df = df.dropna()
    groups = df.groupby(pd.Grouper(freq=freq))
    rows = []
    for period, g in groups:
        if len(g) < 5:
            continue
        rows.append({
            'period': str(period.date()) if hasattr(period, 'date') else str(period),
            'rmse':   round(rmse(g['y'].values, g['yp'].values), 4),
            'mae':    round(mae(g['y'].values, g['yp'].values), 4),
            'n':      len(g),
        })
    return rows


# ─── RESIDUAL ANALYSIS ────────────────────────────────────────────────────────

def residual_analysis(y_true, y_pred, n_bins=20):
    """Analyse residuals for bias, heteroscedasticity, normality."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    mask = ~(np.isnan(yt) | np.isnan(yp))
    res = yp[mask] - yt[mask]

    # Shapiro-Wilk normality (random subsample con seed fija si es muy grande)
    if len(res) > 5000:
        sw_sample = np.random.default_rng(42).choice(res, size=5000, replace=False)
    else:
        sw_sample = res
    try:
        _, p_normal = stats.shapiro(sw_sample)
    except Exception:
        p_normal = None

    # Ljung-Box-like autocorrelation check (lag-1 correlation of residuals)
    if len(res) > 2:
        r_lag1, _ = stats.pearsonr(res[:-1], res[1:])
    else:
        r_lag1 = float('nan')

    # Binned bias by wind speed
    bins = np.percentile(yt[mask], np.linspace(0, 100, n_bins + 1))
    bins = np.unique(bins)
    bin_stats = []
    for i in range(len(bins) - 1):
        sel = (yt[mask] >= bins[i]) & (yt[mask] < bins[i+1])
        if sel.sum() < 3:
            continue
        bin_stats.append({
            'ws_center': round(float((bins[i] + bins[i+1]) / 2), 2),
            'bias': round(float(res[sel].mean()), 4),
            'rmse': round(float(np.sqrt(np.mean(res[sel]**2))), 4),
            'n': int(sel.sum()),
        })

    return {
        'mean_residual': round(float(res.mean()), 4),
        'std_residual':  round(float(res.std()), 4),
        'p_normality':   round(float(p_normal), 4) if p_normal is not None else None,
        'is_normal_p05': bool(p_normal > 0.05) if p_normal is not None else None,
        # Pearson lag-1, no test Ljung-Box formal
        'autocorr_lag1': round(float(r_lag1), 4) if not np.isnan(r_lag1) else None,
        'binned_bias':   bin_stats,
    }


# ─── MODEL COMPARISON TABLE ───────────────────────────────────────────────────

def build_comparison_table(results_dict):
    """
    results_dict: {model_name: {'y_true':..., 'y_pred':..., 'y_persistence':...}}
    Returns: sorted list of metric dicts, best model name.
    """
    rows = []
    persistence_key = None
    for name, res in results_dict.items():
        if 'persistence' in name.lower():
            persistence_key = name
            break

    for name, res in results_dict.items():
        yt = np.asarray(res['y_true'], dtype=float)
        yp = np.asarray(res['y_pred'], dtype=float)
        yb = np.asarray(results_dict[persistence_key]['y_pred'], dtype=float) \
            if persistence_key and persistence_key != name else None
        m = compute_all_metrics(yt, yp, yb, label=name)
        rows.append(m)

    rows.sort(key=lambda x: x['rmse'])
    best = rows[0]['label'] if rows else None
    return rows, best

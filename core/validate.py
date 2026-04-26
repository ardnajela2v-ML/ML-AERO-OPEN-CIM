"""
WindPro Platform — Scientific validation metrics and model comparison.
All metrics are computed without data leakage.
"""
import numpy as np
import pandas as pd
from scipy import stats
import warnings
warnings.filterwarnings('ignore')


# ─── CORE METRICS ─────────────────────────────────────────────────────────────

def mae(y_true, y_pred):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    return float(np.mean(np.abs(y_true[mask] - y_pred[mask])))

def rmse(y_true, y_pred):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    return float(np.sqrt(np.mean((y_true[mask] - y_pred[mask])**2)))

def r2(y_true, y_pred):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    yt, yp = y_true[mask], y_pred[mask]
    ss_res = np.sum((yt - yp)**2)
    ss_tot = np.sum((yt - np.mean(yt))**2)
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else float('nan')

def bias(y_true, y_pred):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    return float(np.mean(y_pred[mask] - y_true[mask]))

def nrmse(y_true, y_pred):
    """Normalised RMSE as fraction of observed mean."""
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    yt = y_true[mask]
    mean_obs = np.mean(yt)
    if mean_obs < 1e-6:
        return float('nan')
    return float(rmse(y_true, y_pred) / mean_obs)

def nmae(y_true, y_pred):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    mean_obs = np.mean(y_true[mask])
    if mean_obs < 1e-6:
        return float('nan')
    return float(mae(y_true, y_pred) / mean_obs)

def smape(y_true, y_pred):
    """Symmetric mean absolute percentage error."""
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    yt, yp = y_true[mask], y_pred[mask]
    denom = (np.abs(yt) + np.abs(yp)) / 2
    valid = denom > 1e-6
    return float(np.mean(np.abs(yt[valid] - yp[valid]) / denom[valid]) * 100)

def skill_score(y_true, y_pred, y_baseline):
    """
    Murphy skill score: 1 - MSE(model)/MSE(baseline).
    Positive = model beats baseline; 0 = same; negative = worse.
    """
    mask = ~(np.isnan(y_true) | np.isnan(y_pred) | np.isnan(y_baseline))
    yt = y_true[mask]
    yp = y_pred[mask]
    yb = y_baseline[mask]
    mse_model    = np.mean((yt - yp)**2)
    mse_baseline = np.mean((yt - yb)**2)
    if mse_baseline < 1e-12:
        return float('nan')
    return float(1 - mse_model / mse_baseline)

def mbe(y_true, y_pred):
    """Mean bias error."""
    return bias(y_true, y_pred)

def pearson_r(y_true, y_pred):
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    if mask.sum() < 3:
        return float('nan')
    r, _ = stats.pearsonr(y_true[mask], y_pred[mask])
    return float(r)


def compute_all_metrics(y_true, y_pred, y_persistence=None, label='model'):
    """Return a dict with all standard metrics."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    out = {
        'label':  label,
        'n':      int((~np.isnan(yt)).sum()),
        'mae':    round(mae(yt, yp), 4),
        'rmse':   round(rmse(yt, yp), 4),
        'r2':     round(r2(yt, yp), 4),
        'bias':   round(bias(yt, yp), 4),
        'nrmse':  round(nrmse(yt, yp), 4),
        'nmae':   round(nmae(yt, yp), 4),
        'smape':  round(smape(yt, yp), 2),
        'pearson_r': round(pearson_r(yt, yp), 4),
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

def temporal_stability(y_true, y_pred, index, freq='ME'):
    """
    Compute RMSE per calendar period (month / week).
    Returns: list of {period, rmse, mae, n}
    """
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

    # Shapiro-Wilk normality (subsample if too large)
    sw_sample = res[:5000] if len(res) > 5000 else res
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

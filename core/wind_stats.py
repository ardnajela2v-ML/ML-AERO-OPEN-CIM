"""
WindPro Platform — Wind statistical analysis module.
Wind rose, diurnal profile, monthly climatology, extreme events.
Based on methodology from PhD research (ajusteMultiplesArchivosConMediaMovil).
"""
import numpy as np
import pandas as pd
from scipy import stats
import warnings

warnings.filterwarnings('ignore')

# ─── WIND ROSE ────────────────────────────────────────────────────────────────

def compute_wind_rose(df, ws_col='wind_speed', wd_col='wind_dir',
                      n_sectors=16, speed_bins=None):
    """
    Compute wind rose: frequency and mean speed per directional sector.
    Returns dict suitable for Chart.js polar/radar chart.
    """
    if wd_col not in df.columns or ws_col not in df.columns:
        return None

    if speed_bins is None:
        speed_bins = [0, 3, 6, 9, 12, 15, 50]
    bin_labels = ['0-3', '3-6', '6-9', '9-12', '12-15', '>15']

    sector_deg  = 360.0 / n_sectors
    half_sector = sector_deg / 2.0

    valid = df[[ws_col, wd_col]].dropna()
    ws    = valid[ws_col].values
    wd    = valid[wd_col].values % 360

    sector_idx = ((wd + half_sector) % 360 / sector_deg).astype(int) % n_sectors
    sector_labels = [f'{int(i * sector_deg)}°' for i in range(n_sectors)]

    frequency = np.zeros(n_sectors)
    mean_speed = np.zeros(n_sectors)
    calm_pct   = float((ws < 1.0).mean() * 100)

    # per-speed-bin frequency matrix [sector × bin]
    bin_matrix = np.zeros((n_sectors, len(bin_labels)))

    for i in range(n_sectors):
        mask = sector_idx == i
        frequency[i] = mask.sum()
        if mask.sum() > 0:
            mean_speed[i] = ws[mask].mean()
        for b, (lo, hi) in enumerate(zip(speed_bins[:-1], speed_bins[1:])):
            bin_matrix[i, b] = ((ws[mask] >= lo) & (ws[mask] < hi)).sum()

    n_total = len(ws)
    freq_pct = (frequency / n_total * 100).tolist()

    # dominant sector
    dom_idx   = int(np.argmax(frequency))
    dom_label = sector_labels[dom_idx]
    dom_pct   = round(freq_pct[dom_idx], 1)

    return {
        'labels':       sector_labels,
        'frequency_pct': [round(f, 2) for f in freq_pct],
        'mean_speed':   [round(float(s), 2) for s in mean_speed.tolist()],
        'bin_labels':   bin_labels,
        'bin_matrix':   [[round(float(bin_matrix[i, b] / n_total * 100), 3)
                          for b in range(len(bin_labels))]
                         for i in range(n_sectors)],
        'dominant_direction': dom_label,
        'dominant_pct':       dom_pct,
        'calm_pct':           round(calm_pct, 2),
        'n_obs':              n_total,
    }


# ─── DIURNAL PROFILE ──────────────────────────────────────────────────────────

def compute_diurnal_profile(df, col='wind_speed'):
    """
    Hourly mean ± σ across all years.
    Returns 24 mean values and 24 std values.
    """
    if col not in df.columns or not isinstance(df.index, pd.DatetimeIndex):
        return None

    s = df[col].dropna()
    grouped = s.groupby(s.index.hour)
    means = grouped.mean()
    stds  = grouped.std()
    p25   = grouped.quantile(0.25)
    p75   = grouped.quantile(0.75)

    hours = list(range(24))
    return {
        'hours':  hours,
        'mean':   [round(float(means.get(h, 0)), 3) for h in hours],
        'std':    [round(float(stds.get(h, 0)),  3) for h in hours],
        'p25':    [round(float(p25.get(h, 0)),   3) for h in hours],
        'p75':    [round(float(p75.get(h, 0)),   3) for h in hours],
        'mean_plus_std':  [round(float(means.get(h,0) + stds.get(h,0)), 3) for h in hours],
        'mean_minus_std': [round(float(max(0, means.get(h,0) - stds.get(h,0))), 3) for h in hours],
    }


# ─── MONTHLY CLIMATOLOGY ──────────────────────────────────────────────────────

def compute_monthly_climatology(df, col='wind_speed'):
    """
    Monthly mean ± σ and percentiles.
    """
    if col not in df.columns or not isinstance(df.index, pd.DatetimeIndex):
        return None

    s = df[col].dropna()
    grouped = s.groupby(s.index.month)
    means = grouped.mean()
    stds  = grouped.std()
    p10   = grouped.quantile(0.10)
    p90   = grouped.quantile(0.90)

    months     = list(range(1, 13))
    mon_labels = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    return {
        'months':  months,
        'labels':  mon_labels,
        'mean':    [round(float(means.get(m, 0)), 3) for m in months],
        'std':     [round(float(stds.get(m, 0)),  3) for m in months],
        'p10':     [round(float(p10.get(m, 0)),   3) for m in months],
        'p90':     [round(float(p90.get(m, 0)),   3) for m in months],
        'mean_plus_std':  [round(float(means.get(m,0) + stds.get(m,0)), 3) for m in months],
        'mean_minus_std': [round(float(max(0, means.get(m,0) - stds.get(m,0))), 3) for m in months],
    }


# ─── MONTH × HOUR HEATMAP (calendar view) ─────────────────────────────────────

def compute_month_hour_heatmap(df, col='wind_speed'):
    """
    12 months × 24 hours matrix of mean wind speed.
    Useful to visualise seasonal + diurnal interaction at a glance.
    """
    if col not in df.columns or not isinstance(df.index, pd.DatetimeIndex):
        return None
    s = df[col].dropna()
    if s.empty:
        return None
    grouped = s.groupby([s.index.month, s.index.hour]).mean()
    matrix = [[0.0] * 24 for _ in range(12)]
    for (m, h), v in grouped.items():
        if 1 <= m <= 12 and 0 <= h <= 23:
            matrix[m - 1][h] = round(float(v), 3)
    mon_labels  = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    hour_labels = [f'{h:02d}' for h in range(24)]
    flat = [v for row in matrix for v in row]
    return {
        'matrix':       matrix,
        'months':       mon_labels,
        'hours':        hour_labels,
        'vmin':         round(min(flat), 2) if flat else 0.0,
        'vmax':         round(max(flat), 2) if flat else 0.0,
        'overall_mean': round(float(s.mean()), 3),
    }


# ─── EXTREME EVENTS ───────────────────────────────────────────────────────────

def compute_extreme_events(df, col='wind_speed', thresholds=None):
    """
    Count hours per month above each threshold (e.g. 15, 20 m/s).
    From PhD code: 'Eventos extremos por mes (≥15 m/s)'.
    """
    if thresholds is None:
        thresholds = [10, 15, 20]

    if col not in df.columns or not isinstance(df.index, pd.DatetimeIndex):
        return None

    s = df[col].dropna()
    months     = list(range(1, 13))
    mon_labels = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

    result = {'months': months, 'labels': mon_labels, 'series': []}
    for thr in thresholds:
        above = s[s >= thr]
        counts = above.groupby(above.index.month).count()
        result['series'].append({
            'threshold': thr,
            'label':     f'≥{thr} m/s',
            'counts':    [int(counts.get(m, 0)) for m in months],
            'total':     int(len(above)),
            'pct':       round(float(len(above) / len(s) * 100), 2),
        })
    return result


# ─── WIND SPEED HISTOGRAM ─────────────────────────────────────────────────────

def compute_histogram(df, col='wind_speed', bins=None):
    """Compute wind speed histogram and Weibull overlay."""
    if col not in df.columns:
        return None

    s = df[col].dropna()
    s = s[s >= 0]

    if bins is None:
        bin_width = 0.5
        bins = np.arange(0, min(s.max() + bin_width, 35), bin_width)

    counts, edges = np.histogram(s.values, bins=bins)
    centers = ((edges[:-1] + edges[1:]) / 2).tolist()
    freq    = (counts / counts.sum()).tolist()

    # Weibull fit
    weibull = None
    if len(s) > 50:
        try:
            shape, loc, scale = stats.weibull_min.fit(s[s > 0], floc=0)
            ws_range = np.linspace(0, bins[-1], 200)
            pdf = stats.weibull_min.pdf(ws_range, shape, loc=loc, scale=scale) * (bins[1] - bins[0])
            weibull = {
                'k': round(shape, 3), 'lambda': round(scale, 3),
                'ws':  [round(float(v), 3) for v in ws_range.tolist()],
                'pdf': [round(float(v), 5) for v in pdf.tolist()],
            }
        except Exception:
            pass

    return {
        'centers':   [round(float(c), 2) for c in centers],
        'frequency': [round(float(f), 5) for f in freq],
        'weibull':   weibull,
    }


# ─── FULL SITE OVERVIEW ───────────────────────────────────────────────────────

def compute_site_overview(df, ws_col='wind_speed', wd_col='wind_dir',
                           temp_col='temperature', pres_col='pressure'):
    """
    Compute all key statistics for the overview dashboard (KPI cards).
    Mirrors the overview section of WindPro_Dashbkoard.html.
    """
    result = {}
    if ws_col not in df.columns:
        return result

    s = df[ws_col].dropna()

    # Basic stats
    result['mean']    = round(float(s.mean()), 3)
    result['median']  = round(float(s.median()), 3)
    result['std']     = round(float(s.std()), 3)
    result['min']     = round(float(s.min()), 3)
    result['max']     = round(float(s.max()), 3)
    result['p10']     = round(float(s.quantile(0.10)), 3)
    result['p25']     = round(float(s.quantile(0.25)), 3)
    result['p75']     = round(float(s.quantile(0.75)), 3)
    result['p90']     = round(float(s.quantile(0.90)), 3)
    result['n_obs']   = int(len(df))
    result['n_valid'] = int(len(s))

    # Date range
    if isinstance(df.index, pd.DatetimeIndex):
        result['date_start'] = str(df.index.min())[:10]
        result['date_end']   = str(df.index.max())[:10]
        result['n_years']    = round(float((df.index.max() - df.index.min()).days / 365.25), 2)

    # Weibull
    s_pos = s[s > 0]
    if len(s_pos) > 50:
        try:
            shape, loc, scale = stats.weibull_min.fit(s_pos, floc=0)
            result['weibull'] = {'k': round(shape, 3), 'lambda': round(scale, 3)}
        except Exception:
            result['weibull'] = None
    else:
        result['weibull'] = None

    # Power density  W/m² at 1.225 kg/m³ — independent of rotor area
    result['power_density_w_m2'] = round(float(0.5 * 1.225 * (s**3).mean()), 1)

    # Hours above thresholds
    for thr in [10, 15, 20, 25]:
        result[f'h_above_{thr}ms'] = int((s >= thr).sum())
        result[f'pct_above_{thr}ms'] = round(float((s >= thr).mean() * 100), 2)

    # Temperature stats
    if temp_col in df.columns:
        t = df[temp_col].dropna()
        result['temp_mean'] = round(float(t.mean()), 2)
        result['temp_std']  = round(float(t.std()),  2)

    # Pressure stats
    if pres_col in df.columns:
        p = df[pres_col].dropna()
        result['pressure_mean'] = round(float(p.mean()), 2)

    # Daily mean time series (for overview chart)
    if isinstance(df.index, pd.DatetimeIndex):
        daily = s.resample('D').mean().dropna()
        # downsample to max 500 pts for chart
        step = max(1, len(daily) // 500)
        result['daily_series'] = {
            'labels': [str(d)[:10] for d in daily.index[::step]],
            'values': [round(float(v), 3) for v in daily.values[::step]],
        }

    return result


# ─── TURBULENCE INTENSITY ─────────────────────────────────────────────────────

def compute_turbulence_intensity(df, ws_col='wind_speed', ws_bins=None):
    """
    TI = σ_v / V_mean per wind-speed bin (Windographer-style).
    Uses rolling 1-h std as σ proxy when measured σ is unavailable.
    IEC 61400-1 Ed.3 class assessment at 15 m/s.
    """
    if ws_col not in df.columns:
        return None

    ws = df[ws_col].dropna()
    rolling_std = ws.rolling(window=6, min_periods=3, center=True).std().reindex(ws.index)
    rolling_std = rolling_std.fillna(rolling_std.median())

    if ws_bins is None:
        ws_bins = np.arange(0, 27, 1.0)

    bins_out = []
    for lo, hi in zip(ws_bins[:-1], ws_bins[1:]):
        mask = (ws >= lo) & (ws < hi)
        n = int(mask.sum())
        if n < 5:
            continue
        v_mean = float(ws[mask].mean())
        sigma  = float(rolling_std[mask].mean())
        ti     = sigma / v_mean if v_mean > 0.5 else np.nan
        ti_p90 = float(rolling_std[mask].quantile(0.90)) / v_mean if v_mean > 0.5 else np.nan
        bins_out.append({
            'ws_center': round((lo + hi) / 2, 1),
            'ws_bin':    f'{lo:.0f}–{hi:.0f}',
            'n':         n,
            'ti_mean':   round(ti,    4) if not np.isnan(ti)     else None,
            'ti_p90':    round(ti_p90,4) if not np.isnan(ti_p90) else None,
        })

    # Representative TI at 15 m/s
    ti_15 = None
    for b in bins_out:
        if b['ti_mean'] and abs(b['ws_center'] - 15.0) <= 1.0:
            ti_15 = b['ti_mean']
            break
    if ti_15 is None:
        vals = [b['ti_mean'] for b in bins_out if b['ti_mean'] and 8 < b['ws_center'] <= 16]
        ti_15 = float(np.mean(vals)) if vals else None

    iec_class = 'C'
    if ti_15:
        if   ti_15 >= 0.16: iec_class = 'A'
        elif ti_15 >= 0.14: iec_class = 'B'

    ws_clip    = ws.clip(lower=0.5)
    ti_overall = float((rolling_std / ws_clip).replace([np.inf, -np.inf], np.nan).dropna().mean())

    return {
        'bins':       bins_out,
        'ti_15ms':    round(ti_15, 4) if ti_15 else None,
        'iec_class':  iec_class,
        'iec_ref':    {'A': 0.16, 'B': 0.14, 'C': 0.12},
        'ti_overall': round(ti_overall, 4),
    }


# ─── WIND SHEAR ───────────────────────────────────────────────────────────────

def compute_wind_shear(df, ws_col='wind_speed', ws_height=50.0):
    """
    Vertical wind profile: Hellmann power law + log profile.
    Auto-detects multi-height columns (ws_10, ws_80, wind_speed_100…)
    and fits α; falls back to α = 0.143 (open flat terrain).
    """
    import re as _re
    if ws_col not in df.columns:
        return None

    ws_ref = df[ws_col].dropna()
    V_ref  = float(ws_ref.mean())

    multi = {}
    for c in df.columns:
        m = _re.match(r'(?:ws|wind_speed)_?(\d+)$', c)
        if m and c != ws_col:
            multi[float(m.group(1))] = float(df[c].dropna().mean())
    multi[ws_height] = V_ref

    alpha, z0 = 0.143, 0.03
    if len(multi) >= 2:
        pairs = sorted(multi.items())
        H_arr = np.array([p[0] for p in pairs])
        V_arr = np.array([p[1] for p in pairs])
        try:
            fit   = np.polyfit(np.log(H_arr), np.log(V_arr), 1)
            alpha = float(np.clip(fit[0], 0.05, 0.50))
        except Exception:
            pass

    heights      = [10, 20, 30, 40, 50, 60, 80, 100, 120, 140, 160, 200]
    z0_eff       = max(z0, 1e-4)
    profile_hell = [round(V_ref * (H / ws_height) ** alpha, 3) for H in heights]
    profile_log  = [round(max(0.0, V_ref *
                              np.log(max(H, z0_eff * 1.01) / z0_eff) /
                              np.log(max(ws_height, z0_eff * 1.01) / z0_eff)), 3)
                    for H in heights]

    return {
        'alpha':            round(alpha, 4),
        'z0':               round(z0_eff, 5),
        'ref_height':       ws_height,
        'ref_speed':        round(V_ref, 3),
        'heights':          heights,
        'profile_hellmann': profile_hell,
        'profile_log':      profile_log,
        'multi_height':     {str(int(k)): round(v, 3) for k, v in sorted(multi.items())},
    }


# ─── SECTOR ANALYSIS ──────────────────────────────────────────────────────────

def compute_sector_analysis(df, ws_col='wind_speed', wd_col='wind_dir',
                             n_sectors=16):
    """
    Sector-wise statistics (Windographer-style):
    frequency, mean WS, power density, energy contribution, Weibull k/λ.
    """
    if ws_col not in df.columns or wd_col not in df.columns:
        return None

    valid = df[[ws_col, wd_col]].dropna()
    ws  = valid[ws_col].values
    wd  = valid[wd_col].values % 360

    sector_deg = 360.0 / n_sectors
    sector_idx = ((wd + sector_deg / 2) % 360 / sector_deg).astype(int) % n_sectors

    dir16 = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
             'S','SSO','SO','OSO','O','ONO','NO','NNO']
    dir8  = ['N','NE','E','SE','S','SO','O','NO']
    labels = dir16 if n_sectors == 16 else dir8

    n_total   = len(ws)
    total_pwr = float(0.5 * 1.225 * (ws**3).mean()) * n_total if n_total > 0 else 1.0

    sectors = []
    for i in range(n_sectors):
        mask = sector_idx == i
        n_s  = int(mask.sum())
        ws_s = ws[mask]
        freq = n_s / n_total * 100 if n_total > 0 else 0.0
        lbl  = labels[i % len(labels)]

        if n_s < 3:
            sectors.append({'sector': i, 'label': lbl,
                            'center_deg': round(i * sector_deg, 1),
                            'freq_pct': 0, 'mean_ws': 0,
                            'power_density_wm2': 0, 'energy_pct': 0,
                            'weibull_k': None, 'weibull_lambda': None, 'n': 0})
            continue

        mean_ws  = float(ws_s.mean())
        pwr_dens = float(0.5 * 1.225 * (ws_s**3).mean())
        energy_pct = pwr_dens * n_s / total_pwr * 100 if total_pwr > 0 else 0.0

        wb_k = wb_lam = None
        ws_pos = ws_s[ws_s > 0]
        if len(ws_pos) > 20:
            try:
                k, _, lam = stats.weibull_min.fit(ws_pos, floc=0)
                wb_k, wb_lam = round(float(k), 3), round(float(lam), 3)
            except Exception:
                pass

        sectors.append({'sector': i, 'label': lbl,
                        'center_deg':        round(i * sector_deg, 1),
                        'freq_pct':          round(freq, 2),
                        'mean_ws':           round(mean_ws, 3),
                        'power_density_wm2': round(pwr_dens, 1),
                        'energy_pct':        round(energy_pct, 2),
                        'weibull_k':         wb_k,
                        'weibull_lambda':    wb_lam,
                        'n':                 n_s})

    dominant = sorted(sectors, key=lambda x: -x['energy_pct'])[:3]
    return {
        'sectors':          sectors,
        'n_sectors':        n_sectors,
        'dominant_sectors': [s['label'] for s in dominant],
        'total_obs':        n_total,
    }


# ─── EXTREME RETURN PERIODS ───────────────────────────────────────────────────

def compute_return_periods(df, col='wind_speed', return_periods=None):
    """
    Gumbel (MoM) + GEV (MLE) on annual maxima.
    Returns V for T = 2, 5, 10, 25, 50, 100 years.
    V_e50 = 1.4 × V_50 (IEC 61400-1 extreme operating gust reference).
    """
    if return_periods is None:
        return_periods = [2, 5, 10, 25, 50, 100]

    if col not in df.columns or not isinstance(df.index, pd.DatetimeIndex):
        return None

    s = df[col].dropna()
    try:
        annual_max = s.resample('YE').max().dropna()
    except Exception:
        annual_max = s.resample('A').max().dropna()
    n_years = len(annual_max)

    v_ann = annual_max.values.astype(float)

    if n_years < 3:
        v_max = float(s.max())
        v_ret = [round(v_max * (1 + 0.08 * np.log(T)), 2) for T in return_periods]
        return {
            'n_years': n_years,
            'annual_max': [round(float(v), 2) for v in v_ann],
            'annual_max_years': [str(d)[:4] for d in annual_max.index],
            'return_periods': return_periods,
            'v_gumbel': v_ret, 'v_gev': v_ret,
            'v_50year': v_ret[return_periods.index(50)] if 50 in return_periods else None,
            'v_e50':    round(v_ret[return_periods.index(50)] * 1.4, 2) if 50 in return_periods else None,
            'method': 'extrapolation (< 3 years)',
            'plot': {'obs': [], 'reduced': []},
        }

    mu_g   = float(np.mean(v_ann))
    sig_g  = float(np.std(v_ann, ddof=1))
    beta_g = sig_g * np.sqrt(6) / np.pi
    u_g    = mu_g - 0.5772 * beta_g

    def gumbel_T(T):
        return u_g + beta_g * (-np.log(-np.log(1.0 - 1.0 / T)))

    v_gumbel = [round(float(gumbel_T(T)), 2) for T in return_periods]

    v_gev = v_gumbel[:]
    try:
        c_g, loc_g, sc_g = stats.genextreme.fit(v_ann)
        v_gev = [round(float(stats.genextreme.ppf(1 - 1.0/T, c_g, loc_g, sc_g)), 2)
                 for T in return_periods]
    except Exception:
        pass

    idx_50 = return_periods.index(50) if 50 in return_periods else -1
    v_50   = v_gumbel[idx_50] if idx_50 >= 0 else None

    sorted_v = sorted(v_ann)
    pp       = [(i + 0.5) / n_years for i in range(n_years)]
    reduced  = [round(-np.log(-np.log(p)), 4) for p in pp]

    return {
        'n_years':          n_years,
        'annual_max':       [round(float(v), 2) for v in v_ann],
        'annual_max_years': [str(d)[:4] for d in annual_max.index],
        'return_periods':   return_periods,
        'v_gumbel':         v_gumbel,
        'v_gev':            v_gev,
        'v_50year':         v_50,
        'v_e50':            round(v_50 * 1.4, 2) if v_50 else None,
        'method':           f'Gumbel MoM + GEV MLE ({n_years} años)',
        'plot':             {'obs': [round(float(v), 2) for v in sorted_v],
                             'reduced': reduced},
    }

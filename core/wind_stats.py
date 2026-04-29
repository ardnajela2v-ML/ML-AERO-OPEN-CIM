"""
AERO by OPEN CIM — Wind statistical analysis module.
Wind rose, diurnal profile, monthly climatology, extreme events.
Based on methodology from PhD research (ajusteMultiplesArchivosConMediaMovil).
"""
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import kstest
import warnings

warnings.filterwarnings('ignore')

# ─── CIRCULAR STATISTICS ──────────────────────────────────────────────────────

def circular_mean(angles_deg):
    """Vector mean direction (°) for circular variable. Maneja NaN."""
    rad = np.deg2rad(np.asarray(angles_deg, dtype=float))
    s = np.nanmean(np.sin(rad))
    c = np.nanmean(np.cos(rad))
    return float(np.rad2deg(np.arctan2(s, c)) % 360)


def circular_std(angles_deg):
    """Desviación angular (°) basada en longitud del vector resultante R."""
    rad = np.deg2rad(np.asarray(angles_deg, dtype=float))
    s = np.nanmean(np.sin(rad))
    c = np.nanmean(np.cos(rad))
    R = np.sqrt(s**2 + c**2)
    return float(np.rad2deg(np.sqrt(-2 * np.log(R + 1e-12))))


def rayleigh_test(angles_deg):
    """Test de Rayleigh para uniformidad direccional.

    H0: direcciones uniformes en [0, 360°]. p < 0.05 ⇒ unimodal/concentrada.
    """
    rad = np.deg2rad(np.asarray(angles_deg, dtype=float))
    n = int(np.sum(np.isfinite(rad)))
    if n < 3:
        return {'R': float('nan'), 'p_value': float('nan'), 'unimodal': False}
    s = np.nanmean(np.sin(rad))
    c = np.nanmean(np.cos(rad))
    R = float(np.sqrt(s**2 + c**2))
    p_value = float(np.exp(-n * R**2))
    return {'R': round(R, 4), 'p_value': round(p_value, 4),
            'unimodal': p_value < 0.05}


# ─── WIND ROSE ────────────────────────────────────────────────────────────────

def compute_wind_rose(df, ws_col='wind_speed', wd_col='wind_dir',
                      n_sectors=16, speed_bins=None):
    """
    Compute wind rose: frequency and mean speed per directional sector.
    Returns dict suitable for Chart.js polar/radar chart.
    Acepta DataFrame o, posicionalmente, dos arrays (ws, wd).
    """
    # Compatibilidad: permite compute_wind_rose(ws_array, wd_array)
    if not isinstance(df, pd.DataFrame):
        ws_arr = np.asarray(df, dtype=float)
        wd_arr = np.asarray(ws_col, dtype=float)
        df = pd.DataFrame({'wind_speed': ws_arr, 'wind_dir': wd_arr})
        ws_col, wd_col = 'wind_speed', 'wind_dir'

    if wd_col not in df.columns or ws_col not in df.columns:
        return None

    if speed_bins is None:
        speed_bins = [0, 3, 6, 9, 12, 15, 50]
    # Labels dinámicos derivados de speed_bins (antes hardcoded → inconsistencia
    # si el caller cambiaba speed_bins).
    bin_labels = [f'{speed_bins[i]}-{speed_bins[i+1]}'
                  for i in range(len(speed_bins) - 1)]

    sector_deg  = 360.0 / n_sectors
    half_sector = sector_deg / 2.0

    valid = df[[ws_col, wd_col]].dropna()
    ws    = valid[ws_col].values
    wd    = valid[wd_col].values % 360

    n_total = len(ws)
    if n_total == 0:
        return {}

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
        'dominant_direction':  dom_label,
        'dominant_pct':        dom_pct,
        'calm_pct':            round(calm_pct, 2),
        'wd_mean_circular':    round(circular_mean(wd), 2),
        'wd_std_circular':     round(circular_std(wd), 2),
        'n_obs':               n_total,
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

    def _g(d, k):
        v = d.get(k, np.nan)
        return float(v) if pd.notna(v) else float('nan')

    hours = list(range(24))
    return {
        'hours':  hours,
        'mean':   [round(_g(means, h), 3) for h in hours],
        'std':    [round(_g(stds,  h), 3) for h in hours],
        'p25':    [round(_g(p25,   h), 3) for h in hours],
        'p75':    [round(_g(p75,   h), 3) for h in hours],
        'mean_plus_std':  [round(_g(means, h) + _g(stds, h), 3) for h in hours],
        'mean_minus_std': [round(max(0, _g(means, h) - _g(stds, h)), 3) for h in hours],
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

    def _g(d, k):
        v = d.get(k, np.nan)
        return float(v) if pd.notna(v) else float('nan')

    months     = list(range(1, 13))
    mon_labels = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    return {
        'months':  months,
        'labels':  mon_labels,
        'mean':    [round(_g(means, m), 3) for m in months],
        'std':     [round(_g(stds,  m), 3) for m in months],
        'p10':     [round(_g(p10,   m), 3) for m in months],
        'p90':     [round(_g(p90,   m), 3) for m in months],
        'mean_plus_std':  [round(_g(means, m) + _g(stds, m), 3) for m in months],
        'mean_minus_std': [round(max(0, _g(means, m) - _g(stds, m)), 3) for m in months],
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
    if len(s) == 0:
        return {}
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

def compute_histogram(df, col='wind_speed', bins=None, rho=None):
    """Compute wind speed histogram and Weibull overlay."""
    if col not in df.columns:
        return None

    s = df[col].dropna()
    s = s[s >= 0]

    if bins is None:
        bin_width = 0.5
        # Tope 50 m/s: el Istmo de Tehuantepec registra ráfagas >35 m/s
        bins = np.arange(0, min(s.max() + bin_width, 50), bin_width)

    counts, edges = np.histogram(s.values, bins=bins)
    if counts.sum() == 0:
        return {}
    centers = ((edges[:-1] + edges[1:]) / 2).tolist()
    freq    = (counts / counts.sum()).tolist()

    # Weibull fit
    weibull = None
    if len(s) > 50:
        try:
            s_pos = s[s > 0].values
            shape, loc, scale = stats.weibull_min.fit(s_pos, floc=0)
            ws_range = np.linspace(0, bins[-1], 200)
            pdf = stats.weibull_min.pdf(ws_range, shape, loc=loc, scale=scale) * (bins[1] - bins[0])
            ks_stat, ks_pvalue = kstest(s_pos, 'weibull_min', args=(shape, 0, scale))
            weibull = {
                'k': round(shape, 3), 'lambda': round(scale, 3),
                'ks_pvalue': round(float(ks_pvalue), 4),
                'ws':  [round(float(v), 3) for v in ws_range.tolist()],
                'pdf': [round(float(v), 5) for v in pdf.tolist()],
            }
            if ks_pvalue < 0.05:
                weibull['warning'] = 'Ajuste Weibull rechazado KS p<0.05'
        except Exception:
            pass

    return {
        'centers':   [round(float(c), 2) for c in centers],
        'frequency': [round(float(f), 5) for f in freq],
        'weibull':   weibull,
    }


def _resolve_rho(df, rho, temp_col='temperature', pres_col='pressure'):
    """Devuelve ρ media (kg/m³). Si rho is None y hay T+P, usa air_density_from_df.
    Si no, fallback 1.225 con warning.
    """
    if rho is not None:
        return float(rho)
    if temp_col in df.columns and pres_col in df.columns:
        try:
            from core.data import air_density_from_df
            return float(np.nanmean(air_density_from_df(df,
                                                        temp_col=temp_col,
                                                        pressure_col=pres_col)))
        except Exception:
            pass
    warnings.warn(
        "Usando rho=1.225 kg/m³. Para sitios de altitud pasar temp_col y pres_col "
        "o el parámetro rho explícito.",
        stacklevel=3,
    )
    return 1.225


# ─── FULL SITE OVERVIEW ───────────────────────────────────────────────────────

def compute_site_overview(df, ws_col='wind_speed', wd_col='wind_dir',
                           temp_col='temperature', pres_col='pressure',
                           rho=None):
    """
    Compute all key statistics for the overview dashboard (KPI cards).
    Mirrors the overview section of AERO dashboard.

    rho: densidad del aire (kg/m³). Si None, se deriva de T y P del df cuando
    estén disponibles vía air_density_from_df, o se usa 1.225 con warning.
    """
    result = {}
    if ws_col not in df.columns:
        return result

    s = df[ws_col].dropna()
    if s.empty:
        return result

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

    # Weibull con KS goodness-of-fit
    s_pos = s[s > 0]
    if len(s_pos) > 50:
        try:
            shape, loc, scale = stats.weibull_min.fit(s_pos.values, floc=0)
            ks_stat, ks_pvalue = kstest(s_pos.values, 'weibull_min',
                                        args=(shape, 0, scale))
            wb = {'k': round(float(shape), 3), 'lambda': round(float(scale), 3),
                  'ks_pvalue': round(float(ks_pvalue), 4)}
            if ks_pvalue < 0.05:
                wb['warning'] = 'Ajuste Weibull rechazado KS p<0.05'
            result['weibull'] = wb
        except Exception:
            result['weibull'] = None
    else:
        result['weibull'] = None

    # Power density W/m² con ρ real (T,P del df) o 1.225 fallback
    rho_used = _resolve_rho(df, rho, temp_col=temp_col, pres_col=pres_col)
    pwr_dens = float(0.5 * rho_used * (s**3).mean())
    result['rho_used'] = round(rho_used, 4)
    result['power_density_w_m2'] = round(pwr_dens, 1)
    result['energy_density_kwh_m2_year'] = round(pwr_dens * 8760 / 1000.0, 1)

    # Velocidad ponderada por potencia (E[v³])^(1/3)
    result['v_power_weighted_ms'] = round(float(np.cbrt(np.mean(s.values**3))), 3)

    # Clase de recurso eólico (NREL/IEC ascendente)
    if   pwr_dens < 100: rc = 'Pobre'
    elif pwr_dens < 200: rc = 'Marginal'
    elif pwr_dens < 300: rc = 'Moderado'
    elif pwr_dens < 400: rc = 'Bueno'
    elif pwr_dens < 500: rc = 'Muy bueno'
    else:                rc = 'Excelente'
    result['resource_class'] = rc

    # Dirección media circular si hay wind_dir
    if wd_col in df.columns:
        wd_vals = df[wd_col].dropna().values
        if len(wd_vals) > 0:
            result['wd_mean_circular'] = round(circular_mean(wd_vals), 2)
            result['wd_std_circular']  = round(circular_std(wd_vals), 2)

    # Hours above thresholds
    for thr in [10, 15, 20, 25]:
        result[f'h_above_{thr}ms'] = int((s >= thr).sum())
        result[f'pct_above_{thr}ms'] = round(float((s >= thr).mean() * 100), 2)

    # % time near mean ± 1 m/s band
    band = 1.0
    v_mean = float(s.mean())
    result['pct_near_mean']    = round(float((abs(s - v_mean) <= band).mean() * 100), 1)
    result['near_mean_band_ms'] = band

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
        step = max(1, len(daily) // 2000)
        result['daily_series'] = {
            'labels': [str(d)[:10] for d in daily.index[::step]],
            'values': [round(float(v), 3) for v in daily.values[::step]],
        }

        # Monthly mean power density W/m²
        pwr_series = 0.5 * rho_used * s ** 3
        monthly_pd = pwr_series.groupby(pwr_series.index.month).mean()
        MES = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
        result['monthly_power_density'] = {
            'labels': MES,
            'values': [round(float(monthly_pd.get(m, np.nan)), 1) for m in range(1, 13)],
        }

    return result


# ─── TURBULENCE INTENSITY ─────────────────────────────────────────────────────

def compute_wind_speed_variability(df, ws_col='wind_speed', ws_bins=None):
    """
    Variabilidad sub-diaria del viento por bin de velocidad (proxy de TI).

    NOTA: Con datos horarios (MERRA-2/ERA5) este valor NO es TI según IEC 61400-1.
    La TI real requiere datos a 1 Hz con ventanas de 10 min. Este es un proxy de
    variabilidad sub-diaria. La clasificación IEC A/B/C NO aplica aquí.
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

    ws_clip    = ws.clip(lower=0.5)
    ti_overall = float((rolling_std / ws_clip).replace([np.inf, -np.inf], np.nan).dropna().mean())

    # Clase IEC proxy — asignada por umbral sobre ti_15ms (IEC 61400-1 Ed.4)
    # ADVERTENCIA: dato horario reanálisis ≠ TI IEC (requiere 1 Hz / 10-min).
    # Úsese solo como referencia indicativa con los umbrales normativos.
    if ti_15 is not None:
        if   ti_15 <= 0.12: iec_class_proxy = 'C'
        elif ti_15 <= 0.14: iec_class_proxy = 'B'
        elif ti_15 <= 0.16: iec_class_proxy = 'A'
        else:               iec_class_proxy = 'S'   # por encima de clase A
    else:
        iec_class_proxy = None

    return {
        'bins':             bins_out,
        'ti_15ms':          round(ti_15, 4) if ti_15 else None,
        'ti_overall':       round(ti_overall, 4),
        'iec_class':        iec_class_proxy,
        'iec_is_proxy':     True,
        'note':             (
            'Proxy de variabilidad inter-horaria (rolling σ 6 h / V̄). '
            'NO equivale a TI IEC 61400-1 (requiere ≥1 Hz, ventanas 10 min). '
            'Clase IEC indicativa — supuesto sobre umbrales normativos.'
        ),
    }


# Alias backward-compatible (la función fue renombrada para honestidad física)
compute_turbulence_intensity = compute_wind_speed_variability


# ─── WIND SHEAR ───────────────────────────────────────────────────────────────

def compute_wind_shear(df, ws_col='wind_speed', ws_height=None, meta=None,
                       orog_factor: float = 0.15, z_scale: float = 80.0,
                       z_hub: float = None):
    """
    Vertical wind profile: Hellmann power law + log profile + orographic variants.

    orog_factor: fractional speed-up at surface due to ridge/escarpment
                 (Jackson-Hunt simplified: ΔS(z) = orog_factor * exp(-z/z_scale)).
                 0 = no orographic effect.
    z_scale:     characteristic decay height (m) for orographic effect.
    """
    import re as _re
    if ws_col not in df.columns:
        return None

    if ws_height is None:
        if meta and 'wind_height_m' in meta:
            ws_height = float(meta['wind_height_m'])
        else:
            warnings.warn(
                "ws_height no provisto y meta['wind_height_m'] ausente; usando 50.0 m.",
                stacklevel=2,
            )
            ws_height = 50.0

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
            alpha = float(np.clip(fit[0], 0.05, 0.40))
        except Exception:
            pass

    heights  = [10, 20, 30, 40, 50, 60, 80, 100, 120, 140, 160, 200]
    z0_eff   = max(z0, 1e-4)

    def _hell(a):
        return [round(V_ref * (H / ws_height) ** a, 3) for H in heights]

    def _log(z0v):
        return [round(max(0.0, V_ref *
                          np.log(max(H, z0v * 1.01) / z0v) /
                          np.log(max(ws_height, z0v * 1.01) / z0v)), 3)
                for H in heights]

    profile_hell   = _hell(alpha)
    profile_log    = _log(z0_eff)
    profile_smooth = _hell(0.10)   # α IEC offshore / terreno muy liso
    profile_iec    = _hell(0.143)  # α IEC abierto (referencia normativa)
    profile_rough  = _hell(0.25)   # α terreno rugoso / forestal

    # Orografía — Jackson-Hunt simplificado: ΔS(z) = orog_factor · exp(-z / z_scale)
    # Basado en velocidad logarítmica del sitio (perfil más representativo del flujo)
    orog_factor = float(orog_factor)
    z_scale     = max(float(z_scale), 1.0)
    if orog_factor > 0:
        profile_orog = [round(max(0.0, profile_log[i] *
                                  (1.0 + orog_factor * float(np.exp(-H / z_scale)))), 3)
                        for i, H in enumerate(heights)]
    else:
        profile_orog = None

    return {
        'alpha':            round(alpha, 4),
        'z0':               round(z0_eff, 5),
        'ref_height':       ws_height,
        'ref_speed':        round(V_ref, 3),
        'heights':          heights,
        'profile_hellmann': profile_hell,
        'profile_log':      profile_log,
        'profile_smooth':   profile_smooth,
        'profile_iec':      profile_iec,
        'profile_rough':    profile_rough,
        'profile_orog':     profile_orog,
        'orog_factor':      round(orog_factor, 3),
        'z_scale':          round(z_scale, 1),
        'multi_height':     {str(int(k)): round(v, 3) for k, v in sorted(multi.items())},
        'hub':              _calc_hub(z_hub, ws_height, V_ref, alpha, z0_eff, df[ws_col]),
    }


def _calc_hub(z_hub, z_ref, V_ref, alpha, z0_eff, ws_series):
    """Extrapolate wind statistics to hub height using power law and log law."""
    if z_hub is None or z_hub <= 0:
        return None

    # Scale factors
    sf_hell = (z_hub / z_ref) ** alpha
    log_num = np.log(max(z_hub,  z0_eff * 1.01) / z0_eff)
    log_den = np.log(max(z_ref,  z0_eff * 1.01) / z0_eff)
    sf_log  = log_num / log_den if log_den > 0 else sf_hell

    v_hell = V_ref * sf_hell
    v_log  = V_ref * sf_log

    rho = 1.225  # kg/m³ — ISA sea level
    pd_hell = 0.5 * rho * v_hell ** 3
    pd_log  = 0.5 * rho * v_log  ** 3

    # Scale the full series to hub height (power law — α fitted from site data)
    ws_hub = ws_series.dropna() * sf_hell
    hub_mean  = float(ws_hub.mean())
    hub_above = float((ws_hub >= 3.0).mean() * 100)   # capacity factor proxy: % ≥ 3 m/s cut-in

    return {
        'z_hub':           round(z_hub, 1),
        'v_hub_hellmann':  round(v_hell, 3),
        'v_hub_log':       round(v_log,  3),
        'pd_hub_hellmann': round(pd_hell, 1),
        'pd_hub_log':      round(pd_log,  1),
        'gain_pct_hell':   round((sf_hell - 1.0) * 100, 1),
        'gain_pct_log':    round((sf_log  - 1.0) * 100, 1),
        'sf_hellmann':     round(sf_hell, 4),
        'sf_log':          round(sf_log,  4),
        'pct_above_cutin': round(hub_above, 1),
    }


# ─── SECTOR ANALYSIS ──────────────────────────────────────────────────────────

def compute_sector_analysis(df, ws_col='wind_speed', wd_col='wind_dir',
                             n_sectors=16, rho=None,
                             temp_col='temperature', pres_col='pressure'):
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

    n_total = len(ws)
    if n_total == 0:
        return {}

    rho_used = _resolve_rho(df, rho, temp_col=temp_col, pres_col=pres_col)
    total_pwr = float(0.5 * rho_used * (ws**3).mean()) * n_total

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
                            'freq_pct': 0, 'mean_ws': None,
                            'power_density_wm2': None, 'energy_pct': 0,
                            'weibull_k': None, 'weibull_lambda': None, 'n': 0})
            continue

        mean_ws  = float(ws_s.mean())
        pwr_dens = float(0.5 * rho_used * (ws_s**3).mean())
        energy_pct = pwr_dens * n_s / total_pwr * 100 if total_pwr > 0 else 0.0

        wb_k = wb_lam = None
        wb_warning = None
        ws_pos = ws_s[ws_s > 0]
        if len(ws_pos) > 100:
            try:
                k, _, lam = stats.weibull_min.fit(ws_pos, floc=0)
                wb_k, wb_lam = round(float(k), 3), round(float(lam), 3)
            except Exception:
                pass
        elif len(ws_pos) > 0:
            wb_warning = f'Solo {n_s} muestras, Weibull poco confiable'

        entry = {'sector': i, 'label': lbl,
                 'center_deg':        round(i * sector_deg, 1),
                 'freq_pct':          round(freq, 2),
                 'mean_ws':           round(mean_ws, 3),
                 'power_density_wm2': round(pwr_dens, 1),
                 'energy_pct':        round(energy_pct, 2),
                 'weibull_k':         wb_k,
                 'weibull_lambda':    wb_lam,
                 'n':                 n_s}
        if wb_warning:
            entry['weibull_warning'] = wb_warning
        sectors.append(entry)

    dominant = sorted(sectors, key=lambda x: -x['energy_pct'])[:3]
    return {
        'sectors':          sectors,
        'n_sectors':        n_sectors,
        'dominant_sectors': [s['label'] for s in dominant],
        'total_obs':        n_total,
        'rho_used':         round(rho_used, 4),
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

    if n_years < 10:
        warnings.warn(
            f"Solo {n_years} años de datos. V_T con alta incertidumbre (±20-30%).",
            stacklevel=2,
        )

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

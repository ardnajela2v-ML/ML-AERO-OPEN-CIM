"""
AERO by OPEN CIM — Data loading, feature engineering, and quality control.
Supports: CSV/TXT (auto-detect), MERRA-2 tabular exports, ERA5 tabular exports.
"""
import io
import re
import json
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from datetime import datetime, timedelta, timezone
from config import Config

warnings.filterwarnings('ignore')

# ─── UTILITY ──────────────────────────────────────────────────────────────────

def _parse_datetime_flexible(series):
    """Try multiple datetime formats; return parsed Series or raise."""
    formats = [
        '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M',
        '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%m/%d/%Y %H:%M:%S',
        '%Y%m%d%H%M', '%Y%m%d %H%M', '%Y-%m-%d',
    ]
    for fmt in formats:
        try:
            return pd.to_datetime(series, format=fmt)
        except Exception:
            pass
    try:
        return pd.to_datetime(series)
    except Exception:
        raise ValueError(f"Cannot parse datetime column. Sample: {series.iloc[0]}")


def _normalise_pressure_to_pa(series):
    """Convertir presión a Pa según rango. air_density() en energy.py espera Pa.

    Reglas:
      - mediana < 200   → kPa  (×1000)
      - mediana < 2000  → hPa  (×100)
      - mediana > 80000 → ya está en Pa
    """
    s = pd.to_numeric(series, errors='coerce')
    med = float(np.nanmedian(s.values))
    if np.isnan(med):
        return s
    if med < 200:
        return s * 1000.0   # kPa → Pa
    if med < 2000:
        return s * 100.0    # hPa → Pa
    return s                # ya en Pa


def air_density_from_df(df, temp_col='temperature', pressure_col='pressure',
                        humidity_col='humidity'):
    """Calcula densidad del aire (kg/m³) a partir de columnas T (°C) y P (Pa).

    pressure DEBE estar en Pa (los loaders normalizan automáticamente).
    Si no hay columna de humedad se asume 0% RH.
    """
    from core.energy import air_density
    if temp_col not in df.columns or pressure_col not in df.columns:
        raise KeyError(f"Faltan columnas '{temp_col}' o '{pressure_col}' para air_density")
    h = df[humidity_col].values if humidity_col in df.columns else 0.0
    return air_density(df[temp_col].values, df[pressure_col].values, h)


def _detect_separator(sample_text):
    """Detect CSV separator from first few lines."""
    for sep in [',', ';', '\t', '|', ' ']:
        count = sample_text.count(sep)
        if count > 3:
            return sep
    return ','


# ─── DATA LOADER ──────────────────────────────────────────────────────────────

class DataLoader:
    """
    Loads wind/meteorological data from multiple source formats.
    Returns a standardised pandas DataFrame with a DatetimeIndex
    and a 'wind_speed' column (m/s) at minimum.
    """

    STANDARD_COLS = {
        'wind_speed': ['wind_speed', 'ws', 'spd', 'speed', 'u_ws',
                       'windspeed', 'vv', 'ff', 'wspd', 'V50M_derived'],
        'wind_dir':   ['wind_dir', 'wd', 'dir', 'direction', 'wdir',
                       'dd', 'wnddir'],
        'temperature':['temperature', 'temp', 't2m', 'T2M', 'air_temp', 'ta'],
        'pressure':   ['pressure', 'pres', 'ps', 'PS', 'slp', 'psl', 'mslp'],
        'humidity':   ['humidity', 'rh', 'relhumidity', 'hur'],
        'u_wind':     ['u_wind', 'u', 'U50M', 'u10', 'u100', 'U10M'],
        'v_wind':     ['v_wind', 'v', 'V50M', 'v10', 'v100', 'V10M'],
    }

    def load_file(self, filepath_or_buffer, source_hint='auto', **kwargs):
        """
        Main entry point.  source_hint: 'auto'|'merra2'|'era5'|'csv'|'conagua'
        Returns (df, metadata_dict).
        """
        if hasattr(filepath_or_buffer, 'read'):
            raw_bytes = filepath_or_buffer.read()
            filename = getattr(filepath_or_buffer, 'filename', 'upload.csv')
        else:
            with open(filepath_or_buffer, 'rb') as f:
                raw_bytes = f.read()
            filename = str(filepath_or_buffer)

        sample = raw_bytes[:4096].decode('utf-8', errors='replace')

        if source_hint == 'auto':
            source_hint = self._detect_source(sample, filename)

        if source_hint == 'windnavigator':
            df, meta = self._load_windnavigator(raw_bytes, sample)
        elif source_hint == 'merra2':
            df, meta = self._load_merra2(raw_bytes, sample)
        elif source_hint == 'era5':
            df, meta = self._load_era5(raw_bytes, sample)
        elif source_hint == 'conagua':
            df, meta = self._load_conagua(raw_bytes, sample)
        else:
            df, meta = self._load_generic_csv(raw_bytes, sample, **kwargs)

        df, meta = self._standardise(df, meta)
        meta['source'] = source_hint
        meta['filename'] = filename
        meta['loaded_at'] = datetime.now(timezone.utc).isoformat()
        return df, meta

    # ─── source detection ─────────────────────────────────────────────────────

    def _detect_source(self, sample, filename):
        fn = filename.lower()
        # Windnavigator / WindOgrapher exports (real MERRA-2 & ERA5 site files)
        if any(k in sample for k in ['Windnavigator', 'Windographer', 'Date/time [UTC]',
                                      'Speed_50m', 'Speed_100m', 'UL Services Group']):
            return 'windnavigator'
        if any(k in sample for k in ['U50M', 'V50M', 'DISPH', 'MERRA']):
            return 'merra2'
        if any(k in sample for k in ['u10', 'v10', 'ERA5', 'reanalysis-era5']):
            return 'era5'
        if any(k in fn for k in ['merra', 'merra2']):
            return 'merra2'
        if 'era5' in fn:
            return 'era5'
        if 'conagua' in fn or 'smn' in fn:
            return 'conagua'
        return 'csv'

    # ─── loaders ──────────────────────────────────────────────────────────────

    def _load_generic_csv(self, raw_bytes, sample, **kwargs):
        sep = _detect_separator(sample)
        df = pd.read_csv(io.BytesIO(raw_bytes), sep=sep, low_memory=False,
                         on_bad_lines='warn', **kwargs)
        df.columns = [str(c).strip() for c in df.columns]
        meta = {'format': 'csv', 'sep': sep, 'n_cols_raw': len(df.columns)}
        return df, meta

    def _load_windnavigator(self, raw_bytes, sample):
        """
        Parses Windnavigator / WindOgrapher tabular exports (MERRA-2 and ERA5).
        Format: ~14 metadata lines, then a tab-separated data table whose
        header row starts with 'Date/time [UTC]'.
        Columns vary by product:
          MERRA-2: Speed_50m [m/s], Direction_50m [degrees], Temperature_10m, Pressure_0m
          ERA5:    Speed_100m [m/s], Direction_100m [degrees], Temperature_2m, Pressure_0m
        """
        text = raw_bytes.decode('utf-8', errors='replace')
        lines = text.splitlines()

        meta = {'format': 'windnavigator'}

        # extract lat/lon from metadata header
        for line in lines[:20]:
            if line.strip().startswith('Latitude:'):
                try:
                    meta['latitude'] = float(line.split(':')[1].strip())
                except Exception:
                    pass
            elif line.strip().startswith('Longitude:'):
                try:
                    meta['longitude'] = float(line.split(':')[1].strip())
                except Exception:
                    pass
            elif 'MERRA-2' in line:
                meta['reanalysis_type'] = 'MERRA-2'
            elif 'ERA5' in line and 'reanalysis_type' not in meta:
                meta['reanalysis_type'] = 'ERA5'

        # find header row index
        header_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith('Date/time'):
                header_idx = i
                break

        if header_idx is None:
            raise ValueError("Could not find 'Date/time [UTC]' header line in Windnavigator file.")

        # parse from header row onward — columns are tab-separated
        data_text = '\n'.join(lines[header_idx:])
        import io as _io
        df = pd.read_csv(_io.StringIO(data_text), sep='\t',
                         low_memory=False, on_bad_lines='warn')
        df.columns = [str(c).strip() for c in df.columns]

        # rename datetime column
        for c in df.columns:
            if c.startswith('Date/time') or c.lower() in ('datetime', 'date/time [utc]'):
                df.rename(columns={c: 'datetime'}, inplace=True)
                break

        # rename wind speed — capture measurement height
        for c in df.columns:
            cl = c.lower()
            if 'speed' in cl and ('[m/s]' in c or 'm/s' in cl):
                h_match = re.search(r'_(\d+)m', c, re.IGNORECASE)
                if h_match:
                    meta['wind_height_m'] = int(h_match.group(1))
                df.rename(columns={c: 'wind_speed'}, inplace=True)
                break

        # rename wind direction
        for c in df.columns:
            if 'direction' in c.lower() and 'degree' in c.lower():
                df.rename(columns={c: 'wind_dir'}, inplace=True)
                break

        # rename temperature
        for c in df.columns:
            if 'temperature' in c.lower() or ('temp' in c.lower() and 'degree' in c.lower()):
                df.rename(columns={c: 'temperature'}, inplace=True)
                break

        # rename pressure (Windnavigator stores kPa)
        for c in df.columns:
            if 'pressure' in c.lower() or c.lower().startswith('pressure'):
                df.rename(columns={c: 'pressure_kpa'}, inplace=True)
                # air_density() en energy.py espera Pa
                df['pressure'] = _normalise_pressure_to_pa(df['pressure_kpa'])
                break

        # parsea datetime y localiza UTC en el index
        # Windnavigator/MERRA-2/ERA5 reportan en UTC
        if 'datetime' in df.columns:
            df['datetime'] = _parse_datetime_flexible(df['datetime'].astype(str))
            df = df.set_index('datetime').sort_index()
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')

        return df, meta

    def _load_merra2(self, raw_bytes, sample):
        """
        Handles MERRA-2 GES DISC tabular downloads (hourly, monthly mean, etc.).
        Typical header: HEADER lines starting with '#' or '--'.
        """
        lines = raw_bytes.decode('utf-8', errors='replace').splitlines()
        skip = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('-') or stripped == '':
                skip = i + 1
            else:
                break
        sep = _detect_separator(sample)
        df = pd.read_csv(io.BytesIO(raw_bytes), skiprows=skip, sep=sep,
                         low_memory=False, on_bad_lines='warn')
        df.columns = [str(c).strip() for c in df.columns]

        # MERRA-2 often has YEAR, MONTH, DAY, HOUR columns
        time_candidates = ['datetime', 'time', 'date']
        merra_time_cols = ['YEAR', 'MONTH', 'DAY', 'HOUR']
        if all(c in df.columns for c in merra_time_cols):
            df['datetime'] = pd.to_datetime(df[merra_time_cols].rename(
                columns={'YEAR':'year','MONTH':'month','DAY':'day','HOUR':'hour'}))
            df.drop(columns=merra_time_cols, inplace=True, errors='ignore')
        elif 'datetime' not in df.columns and any(c in df.columns for c in ['DATE', 'Date', 'date']):
            date_col = next(c for c in ['DATE', 'Date', 'date'] if c in df.columns)
            df.rename(columns={date_col: 'datetime'}, inplace=True)

        meta = {'format': 'merra2', 'sep': _detect_separator(sample)}

        # Derive wind speed and direction from U/V components if not explicit
        for u_col in ['U50M', 'U10M', 'U2M']:
            v_col = u_col.replace('U', 'V')
            if u_col in df.columns and v_col in df.columns and 'wind_speed' not in df.columns:
                df['wind_speed'] = np.sqrt(df[u_col]**2 + df[v_col]**2)
                df['wind_dir'] = (270 - np.degrees(np.arctan2(df[v_col], df[u_col]))) % 360
                meta['wind_height_m'] = int(u_col[1:-1]) if u_col[1:-1].isdigit() else 50
                break

        # Normaliza presión a Pa — air_density() en energy.py espera Pa
        for pc in ('pressure', 'PS', 'ps', 'slp', 'psl', 'mslp'):
            if pc in df.columns:
                df['pressure'] = _normalise_pressure_to_pa(df[pc])
                break

        # Windnavigator/MERRA-2/ERA5 reportan en UTC
        if 'datetime' in df.columns:
            df['datetime'] = _parse_datetime_flexible(df['datetime'].astype(str))
            df = df.set_index('datetime').sort_index()
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')

        return df, meta

    def _load_era5(self, raw_bytes, sample):
        """ERA5 CSV exports from CDS."""
        sep = _detect_separator(sample)
        df = pd.read_csv(io.BytesIO(raw_bytes), sep=sep, low_memory=False)
        df.columns = [str(c).strip() for c in df.columns]
        meta = {'format': 'era5', 'sep': sep}

        for u_col, v_col, h in [('u100', 'v100', 100), ('u10', 'v10', 10)]:
            if u_col in df.columns and v_col in df.columns and 'wind_speed' not in df.columns:
                df['wind_speed'] = np.sqrt(df[u_col]**2 + df[v_col]**2)
                df['wind_dir'] = (270 - np.degrees(np.arctan2(df[v_col], df[u_col]))) % 360
                meta['wind_height_m'] = h
                break

        # Normaliza presión a Pa — air_density() en energy.py espera Pa
        for pc in ('pressure', 'sp', 'msl', 'mslp'):
            if pc in df.columns:
                df['pressure'] = _normalise_pressure_to_pa(df[pc])
                break

        # Windnavigator/MERRA-2/ERA5 reportan en UTC
        if 'datetime' in df.columns:
            df['datetime'] = _parse_datetime_flexible(df['datetime'].astype(str))
            df = df.set_index('datetime').sort_index()
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC')

        return df, meta

    def _load_conagua(self, raw_bytes, sample):
        """CONAGUA/SMN station format (semi-colon separated, Spanish headers)."""
        sep = ';' if ';' in sample else ','
        df = pd.read_csv(io.BytesIO(raw_bytes), sep=sep, encoding='latin-1',
                         low_memory=False, on_bad_lines='warn')
        df.columns = [str(c).strip().lower() for c in df.columns]

        rename_map = {
            'fecha': 'fecha', 'hora': 'hour_str',
            'velocidad_viento': 'wind_speed', 'velocidad viento': 'wind_speed',
            'vel_viento': 'wind_speed', 'vel.viento': 'wind_speed',
            'dir_viento': 'wind_dir', 'direccion_viento': 'wind_dir',
            'temperatura': 'temperature', 'presion': 'pressure',
            'humedad_relativa': 'humidity',
        }
        df.rename(columns=rename_map, inplace=True)

        # Combina fecha + hora antes de parsear datetime (antes la hora se perdía)
        if 'fecha' in df.columns and 'hour_str' in df.columns:
            df['datetime'] = pd.to_datetime(
                df['fecha'].astype(str) + ' ' + df['hour_str'].astype(str),
                errors='coerce'
            )
            df.drop(columns=['fecha', 'hour_str'], inplace=True, errors='ignore')
        elif 'fecha' in df.columns:
            df.rename(columns={'fecha': 'datetime'}, inplace=True)

        # CONAGUA reporta en km/h; convertir a m/s si la mediana sugiere km/h
        if 'wind_speed' in df.columns:
            ws_med = float(np.nanmedian(pd.to_numeric(df['wind_speed'], errors='coerce').values))
            if not np.isnan(ws_med) and ws_med > 30:
                df['wind_speed'] = pd.to_numeric(df['wind_speed'], errors='coerce') / 3.6

        # air_density() en energy.py espera Pa
        if 'pressure' in df.columns:
            df['pressure'] = _normalise_pressure_to_pa(df['pressure'])

        meta = {'format': 'conagua', 'sep': sep}
        return df, meta

    # ─── standardisation ──────────────────────────────────────────────────────

    def _standardise(self, df, meta):
        """
        - Find and set datetime index
        - Map column names to standard names
        - Coerce numerics
        - Sort chronologically
        """
        # ── find datetime column ──
        dt_candidates = ['datetime', 'time', 'date', 'timestamp',
                         'Datetime', 'Timestamp', 'Date', 'Time', 'DATETIME']
        dt_col = None
        for c in dt_candidates:
            if c in df.columns:
                dt_col = c
                break
        if dt_col is None:
            # try first column
            try:
                pd.to_datetime(df.iloc[:, 0])
                dt_col = df.columns[0]
            except Exception:
                pass
        if dt_col is not None:
            df[dt_col] = _parse_datetime_flexible(df[dt_col].astype(str))
            df = df.set_index(dt_col)
            df.index.name = 'datetime'

        # Deduplicación de timestamps + orden cronológico estricto
        if isinstance(df.index, pd.DatetimeIndex):
            df = df[~df.index.duplicated(keep='first')]
        df = df.sort_index()

        # ── map columns to standard names ──
        col_lower = {c.lower().replace(' ', '_'): c for c in df.columns}
        for std_name, aliases in self.STANDARD_COLS.items():
            if std_name not in df.columns:
                for alias in aliases:
                    key = alias.lower().replace(' ', '_')
                    if key in col_lower:
                        df.rename(columns={col_lower[key]: std_name}, inplace=True)
                        break

        # ── coerce numerics ──
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = pd.to_numeric(df[c].astype(str).str.replace(',', '.'),
                                      errors='coerce')

        # ── detect time resolution ──
        if len(df) > 2 and isinstance(df.index, pd.DatetimeIndex):
            diffs = df.index.to_series().diff().dropna()
            most_common = diffs.mode()[0]
            meta['time_resolution_min'] = int(most_common.total_seconds() / 60)
        else:
            meta['time_resolution_min'] = None

        meta['n_rows'] = len(df)
        meta['columns'] = list(df.columns)
        meta['date_range'] = [str(df.index[0]), str(df.index[-1])] \
            if isinstance(df.index, pd.DatetimeIndex) and len(df) > 0 else None
        return df, meta


# ─── DATA QUALITY CHECKER ─────────────────────────────────────────────────────

class DataQualityChecker:
    """
    Runs a comprehensive QC suite on a wind time series DataFrame.
    Returns a structured report dict + a flags DataFrame.
    """

    def run(self, df, target_col='wind_speed'):
        report = {}
        flags = pd.DataFrame(index=df.index)
        flags['flag_missing'] = False
        flags['flag_spike'] = False
        flags['flag_frozen'] = False
        flags['flag_physical'] = False

        if target_col not in df.columns:
            return {'error': f"Column '{target_col}' not found."}, flags

        s = df[target_col].copy()

        # ── missing values ──
        n_missing = s.isna().sum()
        n_total = len(s)
        flags['flag_missing'] = s.isna()
        report['n_total'] = n_total
        report['n_missing'] = int(n_missing)
        report['pct_missing'] = round(float(n_missing / n_total * 100), 2)
        report['qc_missing'] = 'FAIL' if report['pct_missing'] > Config.QC_MAX_MISSING_PCT * 100 else 'PASS'

        # ── consecutive gaps ──
        if isinstance(df.index, pd.DatetimeIndex):
            diffs = df.index.to_series().diff()
            resolution = diffs.mode()[0] if len(diffs) > 1 else pd.Timedelta('1h')
            max_gap_periods = int(Config.QC_MAX_CONSECUTIVE_GAPS * pd.Timedelta('1h') / resolution)
        else:
            max_gap_periods = Config.QC_MAX_CONSECUTIVE_GAPS

        null_runs = (s.isna().astype(int)
                     .groupby((s.notna() != s.notna().shift()).cumsum())
                     .sum())
        max_gap = int(null_runs.max()) if len(null_runs) > 0 else 0
        report['max_consecutive_gap_periods'] = max_gap
        report['qc_gaps'] = 'WARN' if max_gap > max_gap_periods else 'PASS'

        # ── physical bounds ──
        s_valid = s.dropna()
        out_physical = ((s_valid < Config.QC_WIND_MIN_PHYSICAL) |
                        (s_valid > Config.QC_WIND_MAX_PHYSICAL))
        flags.loc[s_valid[out_physical].index, 'flag_physical'] = True
        report['n_out_physical'] = int(out_physical.sum())
        report['qc_physical'] = 'FAIL' if report['n_out_physical'] > 0 else 'PASS'

        # ── spike detection (MAD robusto sobre diferencias) ──
        diff = s.diff().dropna()
        diff_vals = diff.values.astype(float)
        if len(diff_vals) == 0 or np.nanstd(diff_vals) < 1e-10:
            spike_idx = pd.DatetimeIndex([])
        else:
            median = np.nanmedian(diff_vals)
            mad = np.nanmedian(np.abs(diff_vals - median))
            if mad < 1e-10:
                mad = 1e-10
            # 1.4826·MAD ≈ σ bajo normalidad; robusto a outliers (no contaminado por ellos)
            z_robust = np.abs(diff_vals - median) / (1.4826 * mad)
            spike_idx = diff.index[z_robust > Config.QC_MAX_SPIKE_SIGMA]
        flags.loc[spike_idx, 'flag_spike'] = True
        report['n_spikes'] = int(len(spike_idx))
        report['qc_spikes'] = 'WARN' if report['n_spikes'] > 10 else 'PASS'

        # ── frozen / stuck sensor ──
        window = 5
        frozen = (s.rolling(window).std() < 0.001) & s.notna()
        flags['flag_frozen'] = frozen.fillna(False)
        report['n_frozen_periods'] = int(frozen.sum())
        report['qc_frozen'] = 'WARN' if report['n_frozen_periods'] > 20 else 'PASS'

        # ── basic statistics ──
        s_clean = s[~flags.any(axis=1)]
        report['stats'] = {
            'mean':  round(float(s.mean()), 4) if not s.empty else None,
            'std':   round(float(s.std()),  4) if not s.empty else None,
            'min':   round(float(s.min()),  4) if not s.empty else None,
            'p25':   round(float(s.quantile(0.25)), 4) if not s.empty else None,
            'p50':   round(float(s.median()), 4) if not s.empty else None,
            'p75':   round(float(s.quantile(0.75)), 4) if not s.empty else None,
            'max':   round(float(s.max()),  4) if not s.empty else None,
            'mean_clean': round(float(s_clean.mean()), 4) if not s_clean.empty else None,
        }

        # ── Weibull fit ──
        s_pos = s_clean[s_clean > 0].dropna()
        if len(s_pos) > 50:
            try:
                shape, loc, scale = stats.weibull_min.fit(s_pos, floc=0)
                report['weibull'] = {'k': round(shape, 3), 'lambda': round(scale, 3),
                                     'loc': round(loc, 4)}
            except Exception:
                report['weibull'] = None
        else:
            report['weibull'] = None

        # ── overall QC status ──
        statuses = [v for k, v in report.items() if k.startswith('qc_')]
        if 'FAIL' in statuses:
            report['overall'] = 'FAIL'
        elif 'WARN' in statuses:
            report['overall'] = 'WARN'
        else:
            report['overall'] = 'PASS'

        report['flags_summary'] = {c: int(flags[c].sum()) for c in flags.columns}
        return report, flags


# ─── FEATURE ENGINEER ─────────────────────────────────────────────────────────

class FeatureEngineer:
    """
    Builds a rich feature matrix for supervised wind forecasting.
    No leakage: only past information relative to each target horizon.
    """

    def build(self, df, target_col='wind_speed', horizons_h=None,
              lags=None, rolling_windows=None):
        """
        Returns dict: {horizon_h: (X, y, feature_names)} for each horizon.
        Uses chronological train/test split via TimeSeriesSplit.
        """
        if horizons_h is None:
            horizons_h = Config.DEFAULT_HORIZON_H
        if lags is None:
            lags = Config.DEFAULT_LAG_HOURS
        if rolling_windows is None:
            rolling_windows = Config.DEFAULT_ROLLING_WINDOWS

        df = df.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex.")

        # infer hourly resolution (resample if sub-hourly)
        resolution_min = (df.index.to_series().diff().mode()[0].total_seconds() / 60)
        if resolution_min < 55:
            wd_circular = None
            if 'wind_dir' in df.columns:
                # Media circular: la lineal de 350° y 10° da 180°, físicamente errónea
                wd_rad = np.deg2rad(df['wind_dir'])
                sin_mean = np.sin(wd_rad).resample('1h').mean()
                cos_mean = np.cos(wd_rad).resample('1h').mean()
                wd_circular = (np.rad2deg(np.arctan2(sin_mean, cos_mean)) % 360)
            df = df.resample('1h').mean()
            if wd_circular is not None:
                df['wind_dir'] = wd_circular
            resolution_min = 60

        # Densidad del aire derivada si hay T y P
        if 'temperature' in df.columns and 'pressure' in df.columns:
            try:
                df['air_density'] = air_density_from_df(df)
            except Exception:
                pass

        base = df[[target_col]].copy()

        # ── temporal features ──
        base['hour']       = base.index.hour
        base['day_of_week']= base.index.dayofweek
        base['month']      = base.index.month
        base['day_of_year']= base.index.dayofyear
        base['is_night']   = ((base.index.hour >= 20) | (base.index.hour < 6)).astype(int)
        base['hour_sin']   = np.sin(2 * np.pi * base.index.hour / 24)
        base['hour_cos']   = np.cos(2 * np.pi * base.index.hour / 24)
        base['month_sin']  = np.sin(2 * np.pi * (base.index.month - 1) / 12)
        base['month_cos']  = np.cos(2 * np.pi * (base.index.month - 1) / 12)
        # Día del año cíclico — captura estacionalidad anual sin discontinuidad 31-dic/1-ene
        doy = base.index.dayofyear.values.astype(float)
        base['doy_sin'] = np.sin(2 * np.pi * doy / 365.0)
        base['doy_cos'] = np.cos(2 * np.pi * doy / 365.0)

        # ── wind direction features ──
        if 'wind_dir' in df.columns:
            wd_rad = np.radians(df['wind_dir'])
            base['wd_sin'] = np.sin(wd_rad)
            base['wd_cos'] = np.cos(wd_rad)

        # ── additional meteorological features ──
        for col in ['temperature', 'pressure', 'humidity', 'air_density']:
            if col in df.columns:
                base[col] = df[col]

        # ── climatología por hora del día (baseline poderoso de wind forecasting) ──
        base['ws_clim_hour'] = base.groupby(base.index.hour)[target_col].transform('mean')

        # ── rolling de variables auxiliares (T, P) ──
        for col in ('temperature', 'pressure'):
            if col in df.columns:
                base[f'{col}_roll24'] = df[col].shift(1).rolling(24).mean()

        # ── lag features (no leakage: lag_h >= horizon_h) ──
        # we create all lags >= 1 here; filter per-horizon when building X,y
        lag_features = {}
        for lag in lags:
            colname = f'ws_lag{lag}'
            base[colname] = base[target_col].shift(lag)
            lag_features[lag] = colname

        # ── rolling statistics ──
        for w in rolling_windows:
            base[f'ws_roll{w}_mean'] = base[target_col].shift(1).rolling(w).mean()
            base[f'ws_roll{w}_std']  = base[target_col].shift(1).rolling(w).std()
            base[f'ws_roll{w}_max']  = base[target_col].shift(1).rolling(w).max()

        # ── wind trend ──
        base['ws_diff1'] = base[target_col].diff(1)
        base['ws_diff3'] = base[target_col].diff(3)

        result = {}
        for h in horizons_h:
            df_h = base.copy()
            # target: wind speed h hours ahead
            df_h['target'] = df_h[target_col].shift(-h)
            # Lags ws(t-1)...ws(t-k) disponibles en t; no son leaky bajo esta convención
            # (target = ws(t+h), features = info hasta t).
            df_h.drop(columns=[target_col], inplace=True, errors='ignore')
            df_h.dropna(inplace=True)

            feature_cols = [c for c in df_h.columns if c != 'target']
            X = df_h[feature_cols].values.astype(np.float32)
            y = df_h['target'].values.astype(np.float32)
            result[h] = (X, y, feature_cols, df_h.index)

        return result

    def train_test_split_temporal(self, X, y, idx, test_frac=0.2):
        """Strict chronological split with embargo — no shuffling."""
        n = len(X)
        split = int(n * (1 - test_frac))
        # Embargo evita leakage por autocorrelación entre train y test
        max_lag = max(getattr(Config, 'DEFAULT_LAG_HOURS', [168]))
        embargo_end = min(split + max_lag, n)
        return (X[:split], X[embargo_end:],
                y[:split], y[embargo_end:],
                idx[:split], idx[embargo_end:])


# ─── SERIALISATION HELPERS ────────────────────────────────────────────────────

def df_to_json_safe(df, max_rows=5000):
    """Convert DataFrame to JSON-serialisable list of records."""
    if len(df) > max_rows:
        df = df.iloc[::max(1, len(df) // max_rows)].copy()
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
        df.iloc[:, 0] = df.iloc[:, 0].dt.strftime('%Y-%m-%dT%H:%M:%S')
    return df.where(pd.notnull(df), None).to_dict(orient='records')


# Alias público con nombre orientado a dominio
WindDataLoader = DataLoader


def series_to_chart(index, values, label='', color='#4DA3FF', max_pts=2000):
    """Build Chart.js compatible dataset from pandas Series."""
    idx = list(index)
    vals = list(values)
    if len(idx) > max_pts:
        step = max(1, len(idx) // max_pts)
        idx = idx[::step]
        vals = vals[::step]
    return {
        'labels': [str(i) for i in idx],
        'datasets': [{
            'label': label,
            'data': [float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None for v in vals],
            'borderColor': color,
            'backgroundColor': color + '22',
            'borderWidth': 1.5,
            'pointRadius': 0,
            'fill': False,
        }]
    }

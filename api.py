"""
AERO by OPEN CIM — Flask application and REST API.
All scientific computation is delegated to core/ modules.
"""
import os
import io
import gzip
import copy
import json
import re
import uuid
import time
import logging
import threading
import zipfile
import requests
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    HAS_LIMITER = True
except Exception:
    HAS_LIMITER = False


@lru_cache(maxsize=32)
def _cached_power_curve(filepath):
    """Cachea PowerCurve.from_file para evitar releer CSVs idénticos."""
    from core.energy import PowerCurve
    return PowerCurve.from_file(filepath)


def _power_curve_at_hub_height(turbine, requested_height):
    """Return a request-local turbine honoring an explicit hub-height override."""

    if requested_height in (None, ""):
        return turbine
    adjusted = copy.copy(turbine)
    adjusted.hub_height_m = float(requested_height)
    return adjusted


@lru_cache(maxsize=4)
def _cached_list_files(folder):
    """Cachea el listado del folder. Devuelve tupla (immutable, hashable)."""
    if not os.path.isdir(folder):
        return tuple()
    return tuple(sorted(
        f for f in os.listdir(folder)
        if not f.startswith('.') and not f.startswith('~')
    ))

from config import Config
from core.cache import bytes_hash, file_signature, get_or_compute, stable_hash, timed
from core.data import DataLoader, DataQualityChecker, df_to_json_safe, series_to_chart
from core.energy import (PowerCurve, WindToPowerEngine, AEPCalculator,
                          TurbineComparator, TechnoEconomicAnalyzer,
                          air_density, extrapolate_wind_hellmann,
                          extrapolate_wind_log,
                          load_turbines_from_folder, list_turbine_info)
from core.cost_model import (CostModel, get_default_model_dict,
                             get_design_cost_benchmark)
from core.wind_stats import (compute_wind_rose, compute_diurnal_profile,
                              compute_monthly_climatology, compute_extreme_events,
                              compute_histogram, compute_site_overview,
                              compute_turbulence_intensity, compute_wind_shear,
                              compute_sector_analysis, compute_return_periods,
                              compute_month_hour_heatmap)
from core.hybrid import (SolarPVModel, BatteryStorage, DieselGenerator,
                          HybridDispatch, HybridEconomics,
                          build_load_profile, estimate_clearsky_ghi)
from core.site_catalog import get_site_info
from core.mcp_catalog import (M04_LATITUDE, M04_LONGITUDE, M04_STATION_ID,
                              build_mcp_catalog, public_catalog, catalog_index,
                              classify_file)
from core.kriging import (parse_coords_from_filename, read_wind_file,
                          site_wind_stats, kriging_grid, analyze_sites,
                          analyze_spatial_topsis_maps, analyze_spatial_topsis_sites,
                          legacy_three_maps, parallel_read_sites,
                          _compute_site_stats_cached, clear_site_stats_cache,
                          _haversine_km)
from core.spatial_calibration import (METHOD as SPATIAL_CALIBRATION_METHOD,
                                      build_calibrated_maps,
                                      calibrate_spatial_nodes,
                                      resolve_sector_roughness)
from core.farm import (simulate_wind_farm, jensen_deficit, dominant_direction,
                       recommend_n_turbines, optimize_layout_greedy,
                       optimize_layout_structured, energy_weighted_dominant_direction)
from core.farm_mcda import analyze_park_layouts
from core.multicriteria import (topsis, rank_sites_turbines,
                                 DEFAULT_WEIGHTS, CRITERION_LABELS,
                                 CRITERION_DIRECTION, rank_layout_alternatives,
                                 LAYOUT_WEIGHTS, LAYOUT_LABELS, LAYOUT_DIRECTION,
                                 UNISTMO_LAYOUT_WEIGHTS)
from core.forecasting import (read_one_column, read_pml, base_year_stats,
                              normalize_fixed_hourly_profile)
from windmcp.data import read_certe_files, read_reference_files, resample_hourly
from windmcp.service import run_mcp_validation


# ─── APP FACTORY ──────────────────────────────────────────────────────────────

def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')
    app.config.from_object(Config)
    # Tope de upload: 100 MB
    app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

    # CORS con whitelist desde Config (ALLOWED_ORIGINS) o fallback localhost
    CORS(app, origins=getattr(Config, 'ALLOWED_ORIGINS',
                              ['http://localhost:5051']))

    logger = logging.getLogger('aero')
    if not logger.handlers:
        logging.basicConfig(
            level=logging.ERROR,
            format='%(asctime)s %(levelname)s %(message)s',
        )

    # ── in-memory session store (replace with Redis/DB for multi-user prod) ──
    sessions = {}        # session_id -> estado activo del proyecto
    tasks    = {}        # task_id    -> { status, progress, message, result }
    _sessions_lock = threading.Lock()
    _executor = ThreadPoolExecutor(max_workers=4)
    mcp_base_catalog = build_mcp_catalog(Config.DATA_FOLDER, Config.CERTE_FOLDER)
    SESSION_TTL_SEC = 4 * 3600   # 4 h sin actividad
    TASK_TTL_SEC    = 4 * 3600   # 4 h tras la última lectura

    def _touch(sid):
        with _sessions_lock:
            sessions.setdefault(sid, {})['_last_access'] = time.time()

    def _touch_task(tid):
        if tid in tasks and isinstance(tasks[tid], dict):
            tasks[tid]['_last_access'] = time.time()

    def _cleanup_sessions():
        now = time.time()
        with _sessions_lock:
            stale = [sid for sid, s in sessions.items()
                     if isinstance(s, dict)
                     and now - s.get('_last_access', now) > SESSION_TTL_SEC]
            for sid in stale:
                sessions.pop(sid, None)
            stale_t = [tid for tid, t in tasks.items()
                       if isinstance(t, dict)
                       and now - t.get('_last_access', now) > TASK_TTL_SEC]
            for tid in stale_t:
                tasks.pop(tid, None)

    _last_cleanup = [0.0]  # mutable container for closure

    @app.after_request
    def _after(resp):
        # Cleanup solo cada ~60 s en vez de cada request
        now = time.time()
        if now - _last_cleanup[0] > 60:
            _last_cleanup[0] = now
            try:
                _cleanup_sessions()
            except Exception:
                pass
        return resp

    @app.errorhandler(400)
    def _bad_request(e):
        return jsonify({'error': str(getattr(e, 'description', 'Bad Request'))}), 400

    def _require_sid(body):
        """Devuelve session_id válido o aborta 400 con JSON."""
        from flask import abort
        sid = body.get('session_id') if hasattr(body, 'get') else None
        if not sid:
            abort(400, description='session_id requerido')
        return sid

    def _resolve_sid_or_new(body):
        """Devuelve session_id si existe; si no, genera uno nuevo (entry points)."""
        sid = body.get('session_id') if hasattr(body, 'get') else None
        return sid or str(uuid.uuid4())

    # ─── ENVELOPE HELPERS ─────────────────────────────────────────────────────

    def _ok(data):
        return jsonify({'success': True, 'data': data})

    def _err(msg, code=400):
        return jsonify({'success': False, 'error': msg}), code

    def _sess_ws_h(src: dict, sess: dict, key: str = 'ws_height_m', default: float = 50.0) -> float:
        """Return measurement height: explicit body param > session reference > default.
        Ensures ALL calculations use the session reference height when set."""
        val = src.get(key)
        if val is None and key != 'measurement_height_m':
            val = src.get('measurement_height_m')
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        return float(sess.get('ws_height_m', default))

    def _validate_ranges(*, discount_rate=None, n_years=None,
                         rated_power_kw=None, hub_height_m=None):
        """Valida rangos físicos/financieros. Devuelve None o (jsonify, code)."""
        if discount_rate is not None and not (0 < discount_rate < 1):
            return _err('discount_rate debe estar entre 0 y 1')
        if n_years is not None and not (1 <= n_years <= 50):
            return _err('n_years debe estar entre 1 y 50')
        if rated_power_kw is not None and rated_power_kw <= 0:
            return _err('rated_power_kw debe ser positivo')
        if hub_height_m is not None and not (10 <= hub_height_m <= 300):
            return _err('hub_height_m fuera de rango (10-300 m)')
        return None

    # ─── RATE LIMITER (opcional, requiere flask-limiter instalado) ────────────

    if HAS_LIMITER:
        limiter = Limiter(get_remote_address, app=app,
                          default_limits=['100 per hour'])
    else:
        # Decorador no-op si flask-limiter no está disponible
        class _NoOpLimiter:
            def limit(self, *_a, **_kw):
                def deco(f): return f
                return deco
        limiter = _NoOpLimiter()
        logger.warning(
            "flask-limiter no instalado: rate-limiting deshabilitado. "
            "Instalar con `pip install flask-limiter` para activarlo."
        )

    # ─── HELPERS ──────────────────────────────────────────────────────────────

    def get_session(sid):
        with _sessions_lock:
            if sid not in sessions:
                sessions[sid] = {}
            sessions[sid]['_last_access'] = time.time()
            return sessions[sid]

    def _load_dataframe_from_path_cached(path, source_hint='auto'):
        """Parse meteorological files once per file signature + source hint."""
        loader = DataLoader()
        sig = file_signature(path)
        parts = ('dataframe-v1', sig, source_hint)

        def compute():
            with timed(f'parse {os.path.basename(path)}', logger):
                return loader.load_file(path, source_hint=source_hint)

        (df, meta), hit = get_or_compute('dataframe', parts, compute)
        meta = dict(meta)
        meta['cache_hit'] = bool(hit)
        meta['cache_key'] = stable_hash(parts)
        return df.copy(), meta

    def _load_dataframe_from_upload_cached(file_storage, source_hint='auto'):
        """Cache uploaded parse results by file content hash + source hint."""
        raw = file_storage.read()
        digest = bytes_hash(raw)
        filename = getattr(file_storage, 'filename', 'upload.csv')
        loader = DataLoader()
        parts = ('upload-dataframe-v1', digest, filename, source_hint)

        def compute():
            bio = io.BytesIO(raw)
            bio.filename = filename
            with timed(f'parse upload {filename}', logger):
                return loader.load_file(bio, source_hint=source_hint)

        (df, meta), hit = get_or_compute('dataframe', parts, compute)
        meta = dict(meta)
        meta['cache_hit'] = bool(hit)
        meta['cache_key'] = stable_hash(parts)
        meta['upload_sha256'] = digest
        return df.copy(), meta

    def _df_cache_signature(sess, extra=None):
        extra = extra or {}
        source_id = extra.get('data_source') or sess.get('active_data_source') or 'observed'
        source = (sess.get('data_sources') or {}).get(source_id)
        active_df = source.get('df') if source is not None else sess.get('df')
        meta = sess.get('meta') or {}
        if source_id == 'observed' and meta.get('cache_key'):
            base = meta['cache_key']
        elif active_df is not None:
            df = active_df
            base = stable_hash({
                'shape': getattr(df, 'shape', None),
                'columns': list(getattr(df, 'columns', [])),
                'index_start': str(df.index[0]) if len(df.index) else None,
                'index_end': str(df.index[-1]) if len(df.index) else None,
                'source_id': source_id,
                'run_id': (source or {}).get('run_id'),
                'study_revision': (source or {}).get('study_revision'),
            })
        else:
            base = 'no-dataframe'
        return stable_hash({'base': base, 'source_id': source_id, 'extra': extra})

    def _array_cache_signature(values, extra=None):
        arr = np.asarray(values, dtype=np.float64)
        raw = np.ascontiguousarray(arr).view(np.uint8).tobytes()
        return stable_hash({
            'sha256': bytes_hash(raw),
            'shape': arr.shape,
            'extra': extra or {},
        })

    def allowed_file(filename):
        return ('.' in filename and
                filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS)

    def safe_float_list(lst):
        return [float(x) if x is not None and not (isinstance(x, float) and np.isnan(x)) else None
                for x in lst]

    def numpy_to_python(obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return None if not np.isfinite(obj) else float(obj)
        if isinstance(obj, np.ndarray): return numpy_to_python(obj.tolist())
        if isinstance(obj, float):
            if obj != obj or obj == float('inf') or obj == float('-inf'):
                return None
            return obj
        if isinstance(obj, dict): return {k: numpy_to_python(v) for k, v in obj.items()}
        if isinstance(obj, list): return [numpy_to_python(i) for i in obj]
        return obj

    PROJECT_FORMAT = 'aero-open-cim-project'
    PROJECT_FORMAT_VERSION = 1
    PROJECT_SESSION_KEYS = {
        'df', 'meta', 'wind_speed_col', 'wd_col', 'ws_height_m',
        'extrapolation_method', 'qc_report', 'park_configuration',
        'mcp_study', 'reanalysis_study', 'active_study_mode',
        'mcp_uploads', 'mcp_runs', 'data_sources',
        'active_data_source', 'wind_stats', 'wind_advanced',
        'hybrid_result', 'energy_summary', 'market', 'demand', 'pml',
        'kriging_calibrations', 'active_kriging_calibration_id',
    }

    def _project_encode(value, archive, path='root', key_hint=''):
        """Encode trusted session state as JSON plus compressed frame/file entries."""
        safe_path = re.sub(r'[^A-Za-z0-9_.-]+', '_', path).strip('_') or 'value'
        if isinstance(value, pd.DataFrame):
            entry = f'frames/{safe_path}_{uuid.uuid4().hex[:8]}.json'
            archive.writestr(entry, value.to_json(orient='table', date_format='iso'))
            return {'__type__': 'dataframe', 'entry': entry}
        if isinstance(value, pd.Series):
            entry = f'frames/{safe_path}_{uuid.uuid4().hex[:8]}.json'
            archive.writestr(entry, value.to_frame(name=value.name or 'value').to_json(
                orient='table', date_format='iso'))
            return {'__type__': 'series', 'entry': entry, 'name': value.name}
        if isinstance(value, np.ndarray):
            return {'__type__': 'ndarray', 'value': numpy_to_python(value.tolist())}
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, (np.integer, np.floating)):
            return numpy_to_python(value)
        if isinstance(value, (pd.Timestamp, datetime)):
            return {'__type__': 'datetime', 'value': value.isoformat()}
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str) and (
                key_hint == '_path' or key_hint == 'result_path'
                or key_hint.endswith(('_artifact', '_csv', '_json'))) and os.path.isfile(value):
            filename = secure_filename(Path(value).name) or 'artifact.bin'
            entry = f'files/{uuid.uuid4().hex[:12]}_{filename}'
            archive.write(value, entry)
            return {'__type__': 'project_file', 'entry': entry, 'name': filename}
        if isinstance(value, dict):
            return {
                str(key): _project_encode(
                    item, archive, f'{path}.{key}',
                    '_path' if key == '_paths' else str(key),
                )
                for key, item in value.items()
                if str(key) != '_last_access'
            }
        if isinstance(value, (list, tuple, set)):
            item_hint = '_path' if key_hint == '_path' else ''
            return [
                _project_encode(item, archive, f'{path}.{index}', item_hint)
                for index, item in enumerate(value)
            ]
        if value is None or isinstance(value, (bool, int, float, str)):
            return numpy_to_python(value)
        # Derived runtime objects (e.g. PowerCurve instances) are recomputed.
        return {'__type__': 'omitted_runtime_object', 'class': type(value).__name__}

    def _project_decode(value, archive, restore_dir: Path):
        if isinstance(value, list):
            return [_project_decode(item, archive, restore_dir) for item in value]
        if not isinstance(value, dict):
            return value
        marker = value.get('__type__')
        if marker in {'dataframe', 'series'}:
            entry = str(value.get('entry', ''))
            if not entry.startswith('frames/'):
                raise ValueError('Referencia de tabla inválida en el proyecto.')
            frame = pd.read_json(io.StringIO(archive.read(entry).decode('utf-8')), orient='table')
            return frame.iloc[:, 0].rename(value.get('name')) if marker == 'series' else frame
        if marker == 'ndarray':
            return np.asarray(value.get('value', []))
        if marker == 'datetime':
            return pd.Timestamp(value.get('value'))
        if marker == 'project_file':
            entry = str(value.get('entry', ''))
            if not entry.startswith('files/'):
                raise ValueError('Referencia de archivo inválida en el proyecto.')
            filename = secure_filename(str(value.get('name') or Path(entry).name)) or 'artifact.bin'
            destination = restore_dir / (uuid.uuid4().hex[:10] + '_' + filename)
            destination.write_bytes(archive.read(entry))
            return str(destination.resolve())
        if marker == 'omitted_runtime_object':
            return None
        return {key: _project_decode(item, archive, restore_dir) for key, item in value.items()}

    def _auto_detect_wd_col(df):
        """Return the best wind-direction column from df, or None."""
        _DIR_KEYWORDS = ['dir', 'wd', 'direction', 'wdir', 'bearing', 'azimuth']
        _DIR_EXCLUDES = ['std', 'stdev', 'dev', 'sigma', 'gust', 'speed', 'vel']
        best = None
        for col in df.columns:
            cl = col.lower()
            if not any(k in cl for k in _DIR_KEYWORDS):
                continue
            if any(x in cl for x in _DIR_EXCLUDES):
                continue
            vals = pd.to_numeric(df[col], errors='coerce').dropna()
            if len(vals) == 0:
                continue
            if vals.min() >= 0 and vals.max() <= 360:
                best = col
                break  # first match wins; columns are usually ordered wind_speed, wind_dir
        return best

    def _auto_detect_ws_col(df):
        """Return the best physical wind-speed column from df, or None."""
        preferred = ['wind_speed', 'ws', 'windspeed', 'wind speed', 'velocidad_viento', 'vel_viento']
        excludes = ['dir', 'direction', 'wd', 'pml', 'price', 'precio', 'load', 'demand', 'demanda',
                    'temperature', 'temp', 'pressure', 'pres', 'humidity', 'humedad', 'power', 'energy']
        for pref in preferred:
            for col in df.columns:
                if col.lower() == pref:
                    return col
        for col in df.columns:
            cl = col.lower()
            if any(x in cl for x in excludes):
                continue
            if not any(k in cl for k in ['wind', 'viento', 'ws', 'speed', 'vel']):
                continue
            vals = pd.to_numeric(df[col], errors='coerce').dropna()
            if len(vals) == 0:
                continue
            vmin, vmax, vmean = float(vals.min()), float(vals.max()), float(vals.mean())
            if vmin >= 0 and 0 < vmean < 40 and vmax <= 80:
                return col
        return 'wind_speed' if 'wind_speed' in df.columns else None

    def _analysis_df(sess, body=None):
        """Resolve the requested/published source without mutating observations."""
        body = body or {}
        source_id = body.get('data_source') or sess.get('active_data_source')
        if source_id and source_id != 'observed':
            source = (sess.get('data_sources') or {}).get(source_id)
            # Nunca degradar silenciosamente a la medición cuando se pidió un
            # hindcast concreto: una fuente ausente debe detener el cálculo.
            return source.get('df') if source is not None else None
        observed_source = (sess.get('data_sources') or {}).get('observed')
        if source_id == 'observed' and observed_source is not None:
            return observed_source.get('df')
        return sess.get('df')

    def _analysis_source_record(sess, body=None):
        """Return metadata for the active/requested source, when registered."""
        body = body or {}
        source_id = body.get('data_source') or sess.get('active_data_source') or 'observed'
        return source_id, (sess.get('data_sources') or {}).get(source_id)

    def _analysis_source_info(sess, body=None):
        """Compact, serializable provenance for every wind-consuming result."""
        source_id, source = _analysis_source_record(sess, body)
        frame = _analysis_df(sess, body)
        return {
            'source_id': source_id,
            'kind': (source or {}).get('kind') or ('observed' if source_id == 'observed' else 'unknown'),
            'label': (source or {}).get('label') or ('Datos observados/manuales' if source_id == 'observed' else source_id),
            'run_id': (source or {}).get('run_id'),
            'study_revision': (source or {}).get('study_revision'),
            'rows': int(len(frame)) if frame is not None else 0,
        }

    def _long_term_wind_summary(df, speed_col='wind_speed'):
        """Climatology, IAV, anomalies, extremes and Weibull for a published source."""
        if df is None or speed_col not in df.columns or not isinstance(df.index, pd.DatetimeIndex):
            return {}
        speed = pd.to_numeric(df[speed_col], errors='coerce').dropna()
        if speed.empty:
            return {}
        annual = speed.resample('YE').agg(['mean', 'count']).dropna(subset=['mean'])
        expected = np.asarray([8784 if stamp.is_leap_year else 8760 for stamp in annual.index], dtype=float)
        annual['coverage_pct'] = np.where(expected > 0, annual['count'].to_numpy() / expected * 100.0, 0.0)
        stable = annual.loc[annual['coverage_pct'] >= 90.0]
        sample = stable if len(stable) >= 2 else annual
        long_mean = float(sample['mean'].mean())
        q25 = float(sample['mean'].quantile(0.25))
        q75 = float(sample['mean'].quantile(0.75))
        annual_rows = []
        for timestamp, row in annual.iterrows():
            value = float(row['mean'])
            annual_rows.append({
                'year': int(timestamp.year),
                'mean_wind_speed': round(value, 4),
                'hours': int(row['count']),
                'coverage_pct': round(float(row['coverage_pct']), 2),
                'anomaly_ms': round(value - long_mean, 4),
                'anomaly_pct': round((value / long_mean - 1.0) * 100.0, 3) if long_mean else None,
                'classification': 'low' if value <= q25 else ('high' if value >= q75 else 'normal'),
            })
        monthly = speed.groupby(speed.index.month).mean()
        weibull = None
        try:
            from scipy.stats import weibull_min
            positive = speed.loc[speed > 0].to_numpy(dtype=float)
            if len(positive) >= 20:
                shape, _, scale = weibull_min.fit(positive, floc=0)
                weibull = {'k': round(float(shape), 5), 'lambda': round(float(scale), 5), 'N': int(len(positive))}
        except Exception:
            logger.exception('No se pudo ajustar Weibull para la fuente publicada')
        return {
            'mean_wind_speed': round(long_mean, 5),
            'iav_pct': round(float(sample['mean'].std(ddof=1) / long_mean * 100.0), 4) if len(sample) > 1 and long_mean else 0.0,
            'annual': annual_rows,
            'monthly_climatology': [
                {'month': month, 'mean_wind_speed': round(float(monthly.get(month)), 4) if month in monthly else None}
                for month in range(1, 13)
            ],
            'extremes': {
                'p95_ms': round(float(speed.quantile(0.95)), 4),
                'p99_ms': round(float(speed.quantile(0.99)), 4),
                'maximum_ms': round(float(speed.max()), 4),
            },
            'weibull': weibull,
            'year_class_thresholds': {'low_max_ms': round(q25, 4), 'high_min_ms': round(q75, 4)},
        }

    def _energy_ws_col(sess, body=None):
        """Wind-energy endpoints must not follow arbitrary ML target_col changes."""
        body = body or {}
        df = _analysis_df(sess, body)
        if df is None:
            return body.get('ws_col') or sess.get('wind_speed_col') or 'wind_speed'
        for candidate in [body.get('ws_col'), sess.get('wind_speed_col')]:
            if candidate in df.columns:
                return candidate
        detected = _auto_detect_ws_col(df)
        if detected:
            sess['wind_speed_col'] = detected
            return detected
        target = sess.get('target_col')
        return target if target in df.columns else 'wind_speed'

    def _farm_wind_from_session(df, ws_col, wd_col, measurement_h, hub_height_m):
        # If requested wd_col is missing, auto-detect from the dataframe
        if wd_col not in df.columns:
            detected = _auto_detect_wd_col(df)
            if detected:
                wd_col = detected
        info = {
            'source': 'session',
            'ws_col': ws_col,
            'wd_col': wd_col,
            'n_rows': int(len(df)),
        }
        if ws_col not in df.columns:
            info.update({'valid': False, 'reason': 'missing_ws_col'})
            return None, None, None, None, None, info

        ws_num = pd.to_numeric(df[ws_col], errors='coerce').to_numpy(dtype=float)
        finite_raw = np.isfinite(ws_num)
        positive_raw = finite_raw & (ws_num > 0)
        info.update({
            'raw_finite_count': int(finite_raw.sum()),
            'raw_positive_count': int(positive_raw.sum()),
            'raw_valid_pct': round(100.0 * float(finite_raw.sum()) / max(len(ws_num), 1), 2),
        })
        if not finite_raw.any():
            info.update({'valid': False, 'reason': 'no_finite_ws_raw'})
            return None, None, None, None, None, info
        if not positive_raw.any():
            info.update({'valid': False, 'reason': 'no_positive_ws_raw'})
            return None, None, None, None, None, info

        fill_ws = float(np.nanmedian(ws_num[finite_raw]))
        ws_raw = np.where(finite_raw, ws_num, fill_ws)
        reference_wind_mean = float(np.mean(ws_raw))
        ws_hub = np.asarray(
            extrapolate_wind_hellmann(ws_raw, measurement_h, hub_height_m),
            dtype=float,
        )
        finite_hub = np.isfinite(ws_hub)
        positive_hub = finite_hub & (ws_hub > 0)
        info.update({
            'raw_mean_ms': round(reference_wind_mean, 4),
            'hub_finite_count': int(finite_hub.sum()),
            'hub_positive_count': int(positive_hub.sum()),
            'hub_mean_ms': round(float(np.mean(ws_hub[finite_hub])), 4) if finite_hub.any() else None,
        })
        if not finite_hub.any():
            info.update({'valid': False, 'reason': 'no_finite_ws_hub'})
            return None, None, None, None, reference_wind_mean, info
        if not positive_hub.any():
            info.update({'valid': False, 'reason': 'no_positive_ws_hub'})
            return None, None, None, None, reference_wind_mean, info

        if wd_col in df.columns:
            wd_num = pd.to_numeric(df[wd_col], errors='coerce').to_numpy(dtype=float)
            wd = np.where(np.isfinite(wd_num), wd_num, 0.0)
        else:
            wd = np.zeros_like(ws_hub)
            info['wd_fallback'] = 'constant_0'

        temp_c = None
        pres_p = None
        if 'temperature' in df.columns and 'pressure' in df.columns:
            temp_num = pd.to_numeric(df['temperature'], errors='coerce').to_numpy(dtype=float)
            pres_num = pd.to_numeric(df['pressure'], errors='coerce').to_numpy(dtype=float)
            finite_temp = np.isfinite(temp_num)
            finite_pres = np.isfinite(pres_num)
            if finite_temp.any() and finite_pres.any():
                temp_c = np.where(finite_temp, temp_num, float(np.nanmedian(temp_num[finite_temp])))
                pres_p = np.where(finite_pres, pres_num, float(np.nanmedian(pres_num[finite_pres])))

        info['valid'] = True
        return ws_hub, wd, temp_c, pres_p, reference_wind_mean, info

    def _farm_weibull_fallback(v_mean_hub, weibull_k, n_hours=8760):
        from core.kriging import _virtual_weibull_series
        try:
            mean_in = float(v_mean_hub)
        except Exception:
            mean_in = 9.0
        try:
            k_in = float(weibull_k)
        except Exception:
            k_in = 2.0
        mean = mean_in if np.isfinite(mean_in) and mean_in > 0 else 9.0
        k = k_in if np.isfinite(k_in) and k_in > 0 else 2.0
        ws_hub = _virtual_weibull_series(mean, k, n_hours=int(n_hours))
        wd = np.full_like(ws_hub, 270.0)
        return ws_hub, wd

    def _infer_dt_h(index):
        if isinstance(index, pd.DatetimeIndex) and len(index) > 1:
            try:
                return float(index.to_series().diff().dropna().mode()[0].total_seconds() / 3600)
            except Exception:
                return 1.0
        return 1.0

    def _find_demand_column(df):
        priority_exact = [
            'Demanda Eléctrica (MW)', 'Demanda Electrica (MW)', 'LOAD', 'Load',
            'demand_mw', 'load_mw', 'Potencia Demandada (MW)', 'consumo_mw',
            'DEMANDA', 'Demanda', 'demanda', 'Carga (MW)', 'carga_mw',
            'demand_kw', 'load_kw', 'Carga (kW)', 'carga_kw',
        ]
        demand_kws = ['load', 'demand', 'demanda', 'consumo', 'carga']
        for col in priority_exact:
            if col in df.columns:
                return col
        for col in df.columns:
            if any(kw in str(col).lower() for kw in demand_kws):
                return col
        return None

    def _market_demand_profile(sess):
        """Devuelve la demanda comercial de la sesión como potencia en kW.

        La serie cargada en Configuración del Parque es independiente del
        DataFrame meteorológico principal. El valor anual se normaliza a un
        año de 8760 h a partir de la potencia media observada, de modo que una
        hora ausente no reduzca artificialmente la demanda de diseño.
        """
        record = (sess.get('market') or {}).get('demand') or {}
        data = record.get('data') or {}
        raw_values = data.get('values') or []
        if not raw_values:
            return None

        values = pd.to_numeric(pd.Series(raw_values), errors='coerce').to_numpy(dtype=float)
        finite = np.isfinite(values)
        values = values[finite]
        if not len(values):
            return None

        meta = record.get('meta') or {}
        source_dt_h = 1.0
        dates = data.get('dates') or []
        if len(dates) == len(raw_values):
            parsed = pd.to_datetime(pd.Series(dates)[finite], errors='coerce')
            diffs = parsed.diff().dt.total_seconds().div(3600)
            diffs = diffs[(diffs > 0) & np.isfinite(diffs)]
            if not diffs.empty:
                source_dt_h = float(diffs.median())
        else:
            try:
                candidate = float(meta.get('dt_h') or meta.get('time_step_hours') or 1.0)
                if np.isfinite(candidate) and candidate > 0:
                    source_dt_h = candidate
            except (TypeError, ValueError):
                pass

        unit = str(meta.get('unit') or 'MW').strip()
        unit_l = unit.lower().replace(' ', '')
        if 'kwh' in unit_l:
            profile_kw = values / max(source_dt_h, 1e-9)
            unit_interpretation = 'kWh/step'
        elif 'mwh' in unit_l:
            profile_kw = values * 1000.0 / max(source_dt_h, 1e-9)
            unit_interpretation = 'MWh/step'
        elif 'kw' in unit_l:
            profile_kw = values
            unit_interpretation = 'kW'
        else:
            profile_kw = values * 1000.0
            unit_interpretation = 'MW'
        profile_kw = np.clip(profile_kw, 0.0, None)
        if not len(profile_kw):
            return None

        mean_kw = float(np.mean(profile_kw))
        annual_mwh = mean_kw * 8760.0 / 1000.0
        info = {
            'source': 'market_series',
            'filename': record.get('filename') or meta.get('filename'),
            'column': meta.get('column') or 'serie de demanda',
            'unit': unit,
            'units_assumed': unit_interpretation,
            'n_records': int(len(profile_kw)),
            'dt_h': round(float(source_dt_h), 6),
            'coverage_hours': round(float(len(profile_kw) * source_dt_h), 3),
            'observed_energy_mwh': round(float(profile_kw.sum() * source_dt_h / 1000.0), 3),
            'annual_mwh': round(float(annual_mwh), 3),
            'mean_mw': round(mean_kw / 1000.0, 6),
            'max_mw': round(float(np.max(profile_kw)) / 1000.0, 6),
            'annualization': 'mean_power_x_8760',
        }
        return profile_kw, info

    def _align_market_demand_profile(profile_kw, source_dt_h, n_hours, target_dt_h):
        """Alinea y repite el perfil real sin alterar su potencia media."""
        profile_kw = np.asarray(profile_kw, dtype=float)
        if not len(profile_kw) or n_hours <= 0:
            return np.zeros(max(int(n_hours), 0), dtype=float)
        source_dt_h = max(float(source_dt_h), 1e-9)
        target_dt_h = max(float(target_dt_h), 1e-9)
        if not np.isclose(source_dt_h, target_dt_h):
            duration_h = len(profile_kw) * source_dt_h
            target_count = max(1, int(round(duration_h / target_dt_h)))
            source_pos = (np.arange(len(profile_kw), dtype=float) + 0.5) * source_dt_h
            target_pos = (np.arange(target_count, dtype=float) + 0.5) * target_dt_h
            aligned = np.interp(
                np.mod(target_pos, duration_h), source_pos, profile_kw,
                period=duration_h)
            if np.mean(aligned) > 0:
                aligned *= float(np.mean(profile_kw) / np.mean(aligned))
            profile_kw = aligned
        repeats = int(np.ceil(int(n_hours) / len(profile_kw)))
        return np.tile(profile_kw, max(repeats, 1))[:int(n_hours)]

    def _load_kw_for_dispatch(df, n_hours, dt_h, demand_mwh=0.0,
                              market_demand=None):
        if market_demand is not None:
            profile_kw, source_info = market_demand
            load_kw = _align_market_demand_profile(
                profile_kw, source_info.get('dt_h', 1.0), n_hours, dt_h)
            return load_kw, {
                **source_info,
                'alignment': 'repeat_real_profile',
                'n_records_aligned': int(len(load_kw)),
                'target_dt_h': round(float(dt_h), 6),
            }

        demand_col = _find_demand_column(df)
        if demand_col is not None:
            vals = pd.to_numeric(df[demand_col], errors='coerce').to_numpy(dtype=float)
            finite = np.isfinite(vals)
            positive = finite & (vals >= 0)
            if positive.any() and len(vals) == int(n_hours):
                fill_val = float(np.nanmedian(vals[positive]))
                load = np.where(finite, vals, fill_val)
                col_l = str(demand_col).lower()
                if 'kw' in col_l and 'kwh' not in col_l:
                    load_kw = load
                    units = 'kW'
                elif 'kwh' in col_l:
                    load_kw = load / max(float(dt_h), 1e-9)
                    units = 'kWh/step'
                else:
                    load_kw = load * 1000.0
                    units = 'MW'
                return np.clip(load_kw, 0.0, None), {
                    'source': 'session_column',
                    'column': demand_col,
                    'units_assumed': units,
                    'n_records': int(len(load_kw)),
                }

        annual_kwh = max(float(demand_mwh), 0.0) * 1000.0
        if annual_kwh <= 0:
            annual_kwh = 0.0
        peak_kw = annual_kwh / 4380.0 if annual_kwh > 0 else 0.0
        load_kw = build_load_profile(annual_kwh, peak_kw, 'industrial', int(n_hours)) if annual_kwh > 0 else np.zeros(int(n_hours))
        return load_kw, {
            'source': 'synthetic_profile',
            'profile': 'industrial',
            'annual_mwh_target': round(float(demand_mwh), 3),
            'n_records': int(n_hours),
        }

    def _techno_hourly_dispatch(wind_kw, load_kw, index, dt_h, n_turbines, turbine, source_info):
        n = min(len(wind_kw), len(load_kw))
        wind_kw = np.asarray(wind_kw[:n], dtype=float)
        load_kw = np.asarray(load_kw[:n], dtype=float)
        used_kw = np.minimum(wind_kw, load_kw)
        curtailed_kw = np.maximum(wind_kw - load_kw, 0.0)
        deficit_kw = np.maximum(load_kw - wind_kw, 0.0)
        total_load_kwh = float(load_kw.sum() * dt_h)
        wind_kwh = float(wind_kw.sum() * dt_h)
        served_kwh = float(used_kw.sum() * dt_h)
        curtailed_kwh = float(curtailed_kw.sum() * dt_h)
        deficit_kwh = float(deficit_kw.sum() * dt_h)
        installed_kw = float(turbine.rated_power_kw) * int(n_turbines)
        if isinstance(index, pd.DatetimeIndex) and len(index) >= n:
            idx = index[:n]
            labels_src = [str(t) for t in idx]
        else:
            idx = None
            labels_src = [str(i) for i in range(n)]
        step = max(1, n // 720)
        traces = {
            'labels': labels_src[::step],
            'load_kw': [round(float(v), 3) for v in load_kw[::step]],
            'wind_kw': [round(float(v), 3) for v in wind_kw[::step]],
            'served_kw': [round(float(v), 3) for v in used_kw[::step]],
            'curtailed_kw': [round(float(v), 3) for v in curtailed_kw[::step]],
            'deficit_kw': [round(float(v), 3) for v in deficit_kw[::step]],
            'step': int(step),
        }
        annual = []
        if idx is not None:
            years = sorted(set(idx.year))
            for year in years:
                mask = np.asarray(idx.year == year)
                h = float(mask.sum() * dt_h)
                l_kwh = float(load_kw[mask].sum() * dt_h)
                w_kwh = float(wind_kw[mask].sum() * dt_h)
                s_kwh = float(used_kw[mask].sum() * dt_h)
                c_kwh = float(curtailed_kw[mask].sum() * dt_h)
                d_kwh = float(deficit_kw[mask].sum() * dt_h)
                annual.append({
                    'year': int(year),
                    'hours': round(h, 1),
                    'load_mwh': round(l_kwh / 1000.0, 2),
                    'wind_mwh': round(w_kwh / 1000.0, 2),
                    'served_mwh': round(s_kwh / 1000.0, 2),
                    'curtailed_mwh': round(c_kwh / 1000.0, 2),
                    'deficit_mwh': round(d_kwh / 1000.0, 2),
                    'coverage_pct': round(100.0 * s_kwh / l_kwh, 2) if l_kwh > 0 else 100.0,
                    'curtailment_pct': round(100.0 * c_kwh / w_kwh, 2) if w_kwh > 0 else 0.0,
                    'cf_pct': round(100.0 * w_kwh / (installed_kw * h), 2) if installed_kw > 0 and h > 0 else 0.0,
                })
        if not annual:
            h = float(n * dt_h)
            annual.append({
                'year': 'serie',
                'hours': round(h, 1),
                'load_mwh': round(total_load_kwh / 1000.0, 2),
                'wind_mwh': round(wind_kwh / 1000.0, 2),
                'served_mwh': round(served_kwh / 1000.0, 2),
                'curtailed_mwh': round(curtailed_kwh / 1000.0, 2),
                'deficit_mwh': round(deficit_kwh / 1000.0, 2),
                'coverage_pct': round(100.0 * served_kwh / total_load_kwh, 2) if total_load_kwh > 0 else 100.0,
                'curtailment_pct': round(100.0 * curtailed_kwh / wind_kwh, 2) if wind_kwh > 0 else 0.0,
                'cf_pct': round(100.0 * wind_kwh / (installed_kw * h), 2) if installed_kw > 0 and h > 0 else 0.0,
            })
        return {
            'method': 'hourly_merit_order_wind_to_load',
            'n_records': int(n),
            'dt_h': round(float(dt_h), 4),
            'years_detected': len(annual),
            'load_source': source_info,
            'total_load_mwh': round(total_load_kwh / 1000.0, 2),
            'wind_available_mwh': round(wind_kwh / 1000.0, 2),
            'served_by_wind_mwh': round(served_kwh / 1000.0, 2),
            'curtailed_mwh': round(curtailed_kwh / 1000.0, 2),
            'deficit_mwh': round(deficit_kwh / 1000.0, 2),
            'coverage_pct': round(100.0 * served_kwh / total_load_kwh, 2) if total_load_kwh > 0 else 100.0,
            'curtailment_pct': round(100.0 * curtailed_kwh / wind_kwh, 2) if wind_kwh > 0 else 0.0,
            'capacity_factor_pct': round(100.0 * wind_kwh / (installed_kw * n * dt_h), 2) if installed_kw > 0 and n > 0 else 0.0,
            'annual': annual,
            'traces': traces,
        }

    # ─── MAIN PAGE ────────────────────────────────────────────────────────────

    @app.route('/health')
    def health():
        return jsonify({'status': 'ok', 'version': '1.0'}), 200

    @app.route('/api/version')
    def version():
        return jsonify({'version': '1.0', 'name': 'AERO OPEN CIM'}), 200

    @app.route('/api/cost_model/defaults', methods=['GET'])
    def cost_model_defaults():
        """Modelo económico central WindPro: valores y rangos para sensibilidad."""
        return jsonify(get_default_model_dict()), 200

    @app.route('/api/sessions/<sid>', methods=['DELETE'])
    def delete_session(sid):
        with _sessions_lock:
            sessions.pop(sid, None)
        return jsonify({'deleted': sid}), 200

    @app.route('/api/tasks/<task_id>', methods=['GET'])
    def get_task_status(task_id):
        task = tasks.get(task_id)
        if not task:
            return jsonify({'error': 'Task no encontrada'}), 404
        _touch_task(task_id)
        return jsonify(numpy_to_python(task)), 200

    @app.route('/api/tasks/<task_id>', methods=['DELETE'])
    def delete_task(task_id):
        tasks.pop(task_id, None)
        return jsonify({'deleted': task_id}), 200

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/ingenieria')
    def ingenieria_page():
        """Nested reference doc: OpenWAsP engineering & reproducibility blueprint."""
        return render_template('ingenieria.html')

    # /static/<path> es servido automáticamente por Flask (static_folder='static')
    # ─── SESSION ──────────────────────────────────────────────────────────────

    @app.route('/api/session/new', methods=['POST'])
    def new_session():
        sid = str(uuid.uuid4())
        with _sessions_lock:
            sessions[sid] = {'_last_access': time.time()}
        return jsonify({'session_id': sid})

    @app.route('/api/projects/export', methods=['POST'])
    def export_project():
        if request.headers.get('X-Aero-Content-Encoding', '').casefold() == 'gzip':
            try:
                raw_body = gzip.decompress(request.get_data(cache=False))
                if len(raw_body) > 500 * 1024 * 1024:
                    return _err('El estado descomprimido del proyecto excede 500 MB.', 413)
                body = json.loads(raw_body.decode('utf-8'))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                return _err(f'No se pudo leer el estado comprimido del proyecto: {exc}')
        else:
            body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return _err('El estado del proyecto debe ser un objeto JSON.')
        sid = body.get('session_id')
        if not sid or sid not in sessions:
            return _err('La sesión que se desea exportar no existe.', 404)
        sess = get_session(sid)
        project_name = str(body.get('project_name') or 'proyecto_eolico').strip()
        safe_project_name = secure_filename(project_name) or 'proyecto_eolico'
        client_state = body.get('client_state') or {}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            session_state = {
                key: _project_encode(sess[key], archive, f'session.{key}', key)
                for key in sorted(PROJECT_SESSION_KEYS)
                if key in sess
            }
            manifest = {
                'format': PROJECT_FORMAT,
                'format_version': PROJECT_FORMAT_VERSION,
                'application': 'AERO OPEN CIM',
                'exported_at': datetime.now(timezone.utc).isoformat(),
                'source_session_id': sid,
                'project_name': project_name,
                'session_state': session_state,
                'client_state': numpy_to_python(client_state),
            }
            archive.writestr(
                'manifest.json',
                json.dumps(manifest, ensure_ascii=False, separators=(',', ':')),
            )
        stream.seek(0)
        return send_file(
            stream, mimetype='application/zip', as_attachment=True,
            download_name=f'{safe_project_name}.aeroproj', max_age=0,
        )

    @app.route('/api/projects/import', methods=['POST'])
    def import_project():
        project_file = request.files.get('file')
        if not project_file or not project_file.filename:
            return _err('Selecciona un archivo .aeroproj.')
        raw = project_file.read()
        if not raw:
            return _err('El archivo de proyecto está vacío.')
        try:
            with zipfile.ZipFile(io.BytesIO(raw), 'r') as archive:
                members = archive.infolist()
                if len(members) > 5000:
                    raise ValueError('El proyecto contiene demasiados archivos.')
                if sum(member.file_size for member in members) > 500 * 1024 * 1024:
                    raise ValueError('El proyecto descomprimido excede 500 MB.')
                if archive.testzip() is not None:
                    raise ValueError('El archivo está dañado (CRC inválido).')
                manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
                if manifest.get('format') != PROJECT_FORMAT:
                    raise ValueError('El archivo no es un proyecto de AERO OPEN CIM.')
                if int(manifest.get('format_version', 0)) != PROJECT_FORMAT_VERSION:
                    raise ValueError(
                        f"Versión de proyecto no compatible: {manifest.get('format_version')}.")
                sid = str(uuid.uuid4())
                restore_dir = Path(Config.UPLOAD_FOLDER) / 'project_imports' / secure_filename(sid)
                restore_dir.mkdir(parents=True, exist_ok=True)
                restored = _project_decode(
                    manifest.get('session_state') or {}, archive, restore_dir)
        except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as error:
            return _err('No se pudo abrir el proyecto: ' + str(error))
        park = restored.get('park_configuration')
        if isinstance(park, dict):
            legacy_models = park.get('market_models') or {}
            park['market_models'] = {
                'pml': legacy_models['pml']
            } if isinstance(legacy_models, dict) and isinstance(
                legacy_models.get('pml'), dict) else {}
        restored['_last_access'] = time.time()
        with _sessions_lock:
            sessions[sid] = restored
        client_state = manifest.get('client_state') or {}
        if isinstance(client_state, dict) and isinstance(client_state.get('wp'), dict):
            active_wp_keys = {
                'windStats', 'technoResult', 'energyResult',
                'technoActiveScenario', 'technoScenarios', 'levParams',
                'technoFinalLoss', 'referenceHeight', 'referenceMethod',
                'mcpStudy', 'reanalysisStudy', 'studyMode', 'mcpRunId',
                'activeDataSource', 'parkConfiguration', 'meta',
            }
            client_state['wp'] = {
                key: value for key, value in client_state['wp'].items()
                if key in active_wp_keys
            }
        return jsonify({
            'session_id': sid,
            'project_name': manifest.get('project_name') or 'Proyecto restaurado',
            'exported_at': manifest.get('exported_at'),
            'format_version': PROJECT_FORMAT_VERSION,
            'restored_keys': sorted(key for key in restored if not key.startswith('_')),
            'client_state': client_state,
        }), 201

    # ─── MCP STUDY / CATALOG ─────────────────────────────────────────────────

    def _mcp_upload_records(sess):
        return list(sess.get('mcp_uploads', {}).values())

    def _mcp_index(sess):
        return catalog_index(mcp_base_catalog, _mcp_upload_records(sess))

    def _public_station(record):
        return {k: numpy_to_python(v) for k, v in record.items() if not str(k).startswith('_')}

    def _mcp_study_payload(sess):
        study = sess.get('mcp_study')
        if not study:
            return None
        index = _mcp_index(sess)
        result = dict(study)
        observation = index[study['observed_station_id']]
        result['observed_station'] = _public_station(observation)
        result['reference_stations'] = []
        for value in study['reference_station_ids']:
            if value not in index:
                continue
            reference = _public_station(index[value])
            reference['distance_km'] = round(_haversine_km(
                float(observation['latitude']), float(observation['longitude']),
                float(reference['latitude']), float(reference['longitude']),
            ), 3)
            result['reference_stations'].append(reference)
        return result

    def _reanalysis_study_payload(sess):
        study = sess.get('reanalysis_study')
        if not study:
            return None
        index = _mcp_index(sess)
        poi = study['poi']
        result = dict(study)
        result['reference_stations'] = []
        for value in study['reference_station_ids']:
            record = index.get(value)
            if not record:
                continue
            reference = _public_station(record)
            reference['distance_km'] = round(_haversine_km(
                float(poi['lat']), float(poi['lon']),
                float(reference['latitude']), float(reference['longitude']),
            ), 3)
            result['reference_stations'].append(reference)
        result['study_mode'] = 'reanalysis_only'
        source_id = study.get('source_id')
        source = (sess.get('data_sources') or {}).get(source_id) if source_id else None
        result['active_data_source'] = source_id
        if source:
            result['published_source'] = {
                k: numpy_to_python(v) for k, v in source.items()
                if k != 'df' and not str(k).startswith('_')
            }
        return result

    def _build_reanalysis_area_source(index, reference_ids, poi,
                                      target_height, study_revision):
        """Create one canonical hourly area resource without an MCP anchor.

        Providers receive equal total weight when both ERA5 and MERRA-2 are
        present. Nodes within a provider are weighted by distance to the POI.
        Missing timestamps are renormalized row by row.
        """
        nodes = []
        failures = []
        for station_id in reference_ids:
            record = index[station_id]
            paths = list(record.get('_paths') or [])
            try:
                if not paths:
                    raise ValueError('sin archivo de serie asociado')
                reference = read_reference_files(paths)[0]
                frame = resample_hourly(reference.frame)
                speed = pd.to_numeric(frame.get('wind_speed_ref'), errors='coerce')
                source_height = float(
                    reference.metadata.get('wind_height_m')
                    or record.get('measurement_height_m')
                    or (record.get('measurement_heights') or [50])[0]
                )
                if not np.isclose(source_height, target_height):
                    speed = pd.Series(
                        extrapolate_wind_hellmann(
                            speed.to_numpy(dtype=float), source_height, target_height),
                        index=speed.index,
                    )
                valid = speed.notna()
                if int(valid.sum()) < 24:
                    raise ValueError('menos de 24 registros horarios válidos')
                distance = _haversine_km(
                    poi['lat'], poi['lon'], float(record['latitude']),
                    float(record['longitude']))
                nodes.append({
                    'station_id': station_id,
                    'provider': record['provider'],
                    'display_name': record.get('display_name') or station_id,
                    'distance_km': float(distance),
                    'source_height_m': source_height,
                    'speed': speed,
                    'direction': pd.to_numeric(
                        frame.get('wind_direction_ref'), errors='coerce')
                        if 'wind_direction_ref' in frame else None,
                    'temperature': pd.to_numeric(
                        frame.get('temperature_ref'), errors='coerce')
                        if 'temperature_ref' in frame else None,
                    'pressure': pd.to_numeric(
                        frame.get('pressure_ref_kpa'), errors='coerce') * 10.0
                        if 'pressure_ref_kpa' in frame else None,
                })
            except Exception as error:
                failures.append({'station_id': station_id, 'error': str(error)})
        if len(nodes) < 3:
            detail = '; '.join(
                f"{row['station_id']}: {row['error']}" for row in failures[:3])
            raise ValueError(
                'No se pudieron construir tres series de reanálisis válidas'
                + (f': {detail}' if detail else ''))

        providers = sorted({node['provider'] for node in nodes})
        weights = {}
        for provider in providers:
            group = [node for node in nodes if node['provider'] == provider]
            raw = {
                node['station_id']: 1.0 / (25.0 + node['distance_km'])
                for node in group
            }
            total = sum(raw.values()) or 1.0
            provider_share = 1.0 / len(providers)
            for station_id, value in raw.items():
                weights[station_id] = provider_share * value / total

        def weighted_mean(field):
            series = {
                node['station_id']: node[field]
                for node in nodes if node.get(field) is not None
            }
            if not series:
                return None
            matrix = pd.concat(series, axis=1).sort_index()
            column_weights = pd.Series(
                {column: weights[column] for column in matrix.columns})
            numerator = matrix.mul(column_weights, axis=1).sum(axis=1, min_count=1)
            denominator = matrix.notna().mul(column_weights, axis=1).sum(axis=1)
            return numerator.div(denominator.where(denominator > 0))

        canonical = pd.DataFrame()
        canonical['wind_speed'] = weighted_mean('speed')
        direction_series = {
            node['station_id']: node['direction']
            for node in nodes if node.get('direction') is not None
        }
        if direction_series:
            radians = {
                key: np.deg2rad(value) for key, value in direction_series.items()
            }
            sin_matrix = pd.concat({
                key: np.sin(value) for key, value in radians.items()}, axis=1)
            cos_matrix = pd.concat({
                key: np.cos(value) for key, value in radians.items()}, axis=1)
            dir_weights = pd.Series({
                column: weights[column] for column in sin_matrix.columns})
            sin_den = sin_matrix.notna().mul(dir_weights, axis=1).sum(axis=1)
            cos_den = cos_matrix.notna().mul(dir_weights, axis=1).sum(axis=1)
            sin_mean = sin_matrix.mul(dir_weights, axis=1).sum(
                axis=1, min_count=1).div(sin_den.where(sin_den > 0))
            cos_mean = cos_matrix.mul(dir_weights, axis=1).sum(
                axis=1, min_count=1).div(cos_den.where(cos_den > 0))
            canonical['wind_direction'] = (
                np.rad2deg(np.arctan2(sin_mean, cos_mean)) % 360.0)
        temperature = weighted_mean('temperature')
        if temperature is not None:
            canonical['temperature'] = temperature
        pressure = weighted_mean('pressure')
        if pressure is not None:
            canonical['pressure'] = pressure
        canonical = canonical.replace([np.inf, -np.inf], np.nan)
        canonical = canonical.dropna(subset=['wind_speed']).sort_index()
        canonical.index.name = 'timestamp'
        if len(canonical) < 24:
            raise ValueError('La fuente combinada no alcanza 24 horas válidas')

        source_id = f'reanalysis-area:r{study_revision}'
        node_summary = [{
            'station_id': node['station_id'],
            'display_name': node['display_name'],
            'provider': node['provider'],
            'distance_km': round(node['distance_km'], 3),
            'source_height_m': node['source_height_m'],
            'weight': round(weights[node['station_id']], 8),
        } for node in nodes]
        return source_id, {
            'source_id': source_id,
            'label': 'Reanálisis representativo del área · ERA5/MERRA-2',
            'kind': 'reanalysis_area',
            'study_mode': 'reanalysis_only',
            'study_revision': study_revision,
            'rows': int(len(canonical)),
            'period_start': canonical.index.min().isoformat(),
            'period_end': canonical.index.max().isoformat(),
            'target_height_m': float(target_height),
            'poi': dict(poi),
            'providers': providers,
            'node_count': len(nodes),
            'nodes': node_summary,
            'failed_nodes': failures,
            'aggregation_method': 'provider_balanced_inverse_distance_hourly_v1',
            'is_mcp': False,
            'long_term_statistics': _long_term_wind_summary(canonical),
            'df': canonical,
        }

    @app.route('/api/mcp/catalog', methods=['GET'])
    def mcp_catalog():
        sid = request.args.get('session_id')
        sess = get_session(sid) if sid else {}
        return jsonify(public_catalog(mcp_base_catalog, _mcp_upload_records(sess)))

    @app.route('/api/mcp/uploads', methods=['POST'])
    def mcp_upload():
        sid = request.form.get('session_id')
        if not sid:
            return _err('session_id requerido')
        file_storage = request.files.get('file')
        if not file_storage or not file_storage.filename:
            return _err('file requerido')
        if not allowed_file(file_storage.filename):
            return _err('Tipo de archivo no soportado')
        data_kind = str(request.form.get('data_kind', '')).strip().lower()
        if data_kind not in {'observed', 'reanalysis'}:
            return _err('data_kind debe ser observed o reanalysis')
        provider = str(request.form.get('provider', 'USER')).strip().upper()
        if data_kind == 'reanalysis' and provider not in {'ERA5', 'MERRA2'}:
            return _err('Una referencia debe declarar provider ERA5 o MERRA2')
        try:
            latitude = float(request.form.get('latitude'))
            longitude = float(request.form.get('longitude'))
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError
            measurement_height = float(request.form.get('measurement_height_m', 80 if data_kind == 'observed' else 50))
            if not 1 <= measurement_height <= 300:
                return _err('measurement_height_m fuera de rango')
        except (TypeError, ValueError):
            return _err('Coordenadas o altura inválidas')

        station_id = 'upload-' + uuid.uuid4().hex[:16]
        upload_dir = Path(Config.MCP_UPLOAD_FOLDER) / secure_filename(sid) / station_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = secure_filename(file_storage.filename) or 'station.txt'
        path = upload_dir / filename
        file_storage.save(path)
        signature = classify_file(path)
        # An explicit user role wins, but contradictions are surfaced for audit.
        warning = None
        if signature['data_kind'] not in {'unknown', data_kind}:
            warning = 'La firma del archivo no coincide con el tipo declarado por el usuario.'
        target_column = str(request.form.get('target_column', 'wind_speed')).strip() or 'wind_speed'
        declared_timezone = str(request.form.get('timezone', 'UTC')).strip() or 'UTC'
        try:
            if data_kind == 'observed':
                checked, _ = read_certe_files(
                    [str(path)], target_column=target_column, timezone=declared_timezone
                )
                if checked.empty or checked['wind_speed_obs'].notna().sum() == 0:
                    raise ValueError('no contiene velocidades válidas')
            else:
                checked_refs = read_reference_files([str(path)])
                if not checked_refs or checked_refs[0].frame['wind_speed_ref'].notna().sum() == 0:
                    raise ValueError('no contiene velocidades válidas')
        except Exception as error:
            return _err('El archivo no pasó la validación de timestamp/velocidad: ' + str(error))
        record = {
            'station_id': station_id,
            'display_name': str(request.form.get('display_name') or Path(filename).stem),
            'data_kind': data_kind,
            'provider': provider if data_kind == 'reanalysis' else str(request.form.get('provider', 'USER')).upper(),
            'allowed_role': 'observation' if data_kind == 'observed' else 'reference',
            'latitude': latitude,
            'longitude': longitude,
            'measurement_heights': [measurement_height],
            'measurement_height_m': measurement_height,
            'timezone': declared_timezone,
            'target_column': target_column,
            'file_ids': [filename],
            'legacy_aliases': [],
            'source_confidence': 'user',
            'signature_detected': signature,
            'warning': warning,
            '_paths': [str(path.resolve())],
        }
        sess = get_session(sid)
        sess.setdefault('mcp_uploads', {})[station_id] = record
        return jsonify({'station': _public_station(record), 'warning': warning}), 201

    @app.route('/api/sessions/<sid>/mcp-study', methods=['GET', 'PUT'])
    def mcp_study(sid):
        sess = get_session(sid)
        if request.method == 'GET':
            payload = _mcp_study_payload(sess)
            if payload is None:
                return jsonify({'error': 'No hay estudio MCP en la sesión'}), 404
            return jsonify(payload)

        body = request.get_json(silent=True) or {}
        observed_id = str(body.get('observed_station_id', '')).strip()
        reference_ids = list(dict.fromkeys(str(value).strip() for value in body.get('reference_station_ids', []) if str(value).strip()))
        index = _mcp_index(sess)
        observed = index.get(observed_id)
        if not observed or observed.get('allowed_role') != 'observation':
            return _err('Debe seleccionarse una estación observada válida')
        if not reference_ids:
            return _err('Debe seleccionarse al menos una referencia ERA5/MERRA-2')
        invalid = [value for value in reference_ids if value not in index or index[value].get('allowed_role') != 'reference']
        if invalid:
            return _err('Referencias inválidas: ' + ', '.join(invalid))
        poi = body.get('poi') or {'lat': observed.get('latitude'), 'lon': observed.get('longitude')}
        try:
            target_height = float(body.get('target_height_m', 80))
            radius_km = float(body.get('radius_km', 200))
        except (TypeError, ValueError):
            return _err('target_height_m o radius_km inválidos')
        if not 1 <= target_height <= 300:
            return _err('target_height_m fuera de rango (1-300 m)')
        if radius_km <= 0:
            return _err('radius_km debe ser positivo')
        prior = sess.get('mcp_study') or {}
        selection_key = (observed_id, tuple(reference_ids), round(target_height, 4))
        prior_key = (prior.get('observed_station_id'), tuple(prior.get('reference_station_ids', [])), prior.get('target_height_m'))
        revision = int(prior.get('study_revision', 0)) + (0 if prior and selection_key == prior_key else 1)
        study = {
            'study_revision': max(revision, 1),
            'observed_station_id': observed_id,
            'reference_station_ids': reference_ids,
            'poi': {'lat': float(poi.get('lat')), 'lon': float(poi.get('lon'))},
            'radius_km': radius_km,
            'target_height_m': target_height,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        sess['mcp_study'] = study
        sess['active_study_mode'] = 'mcp_observed'
        for run in (sess.get('mcp_runs') or {}).values():
            run['is_current_revision'] = run.get('study_revision') == study['study_revision']
        return jsonify(_mcp_study_payload(sess))

    @app.route('/api/sessions/<sid>/reanalysis-study', methods=['GET', 'PUT'])
    def reanalysis_study(sid):
        """Área sin estación observada: conserva únicamente nodos ERA5/MERRA-2.

        Es deliberadamente independiente de MCP: no fabrica una observación ni
        publica un hindcast calibrado cuando no existe medición local.
        """
        sess = get_session(sid)
        if request.method == 'GET':
            payload = _reanalysis_study_payload(sess)
            if payload is None:
                return jsonify({'error': 'No hay estudio de reanálisis en la sesión'}), 404
            return jsonify(payload)

        body = request.get_json(silent=True) or {}
        reference_ids = list(dict.fromkeys(
            str(value).strip() for value in body.get('reference_station_ids', [])
            if str(value).strip()
        ))
        if len(reference_ids) < 3:
            return _err('Se requieren al menos tres referencias ERA5/MERRA-2')
        index = _mcp_index(sess)
        invalid = [
            value for value in reference_ids
            if value not in index
            or index[value].get('data_kind') != 'reanalysis'
            or index[value].get('provider') not in {'ERA5', 'MERRA2'}
        ]
        if invalid:
            return _err('Referencias de reanálisis inválidas: ' + ', '.join(invalid))
        poi = body.get('poi') or {}
        try:
            poi = {'lat': float(poi['lat']), 'lon': float(poi['lon'])}
            radius_km = float(body.get('radius_km', 200))
            target_height = float(body.get('target_height_m', 80))
        except (KeyError, TypeError, ValueError):
            return _err('POI, radius_km o target_height_m inválido')
        if not all(np.isfinite(value) for value in (
                poi['lat'], poi['lon'], radius_km, target_height)):
            return _err('POI, radius_km o target_height_m debe ser finito')
        if not (-90 <= poi['lat'] <= 90 and -180 <= poi['lon'] <= 180):
            return _err('Coordenadas del POI fuera de rango')
        if radius_km <= 0:
            return _err('radius_km debe ser positivo')
        if not 1 <= target_height <= 300:
            return _err('target_height_m fuera de rango (1-300 m)')
        outside = [
            value for value in reference_ids
            if _haversine_km(
                poi['lat'], poi['lon'], float(index[value]['latitude']),
                float(index[value]['longitude'])
            ) > radius_km + 1e-6
        ]
        if outside:
            return _err('Hay referencias fuera del radio del estudio: ' + ', '.join(outside))

        prior = sess.get('reanalysis_study') or {}
        selection_key = (
            tuple(reference_ids), round(poi['lat'], 6), round(poi['lon'], 6),
            round(radius_km, 4), round(target_height, 4),
        )
        prior_poi = prior.get('poi') or {}
        prior_key = (
            tuple(prior.get('reference_station_ids', [])),
            round(float(prior_poi.get('lat', 999)), 6),
            round(float(prior_poi.get('lon', 999)), 6),
            round(float(prior.get('radius_km', 0)), 4),
            round(float(prior.get('target_height_m', 0)), 4),
        )
        revision = int(prior.get('study_revision', 0)) + (
            0 if prior and selection_key == prior_key else 1)
        revision = max(revision, 1)
        source_id = f'reanalysis-area:r{revision}'
        source_record = (sess.get('data_sources') or {}).get(source_id)
        if not prior or selection_key != prior_key or source_record is None:
            try:
                source_id, source_record = _build_reanalysis_area_source(
                    index, reference_ids, poi, target_height, revision)
            except Exception as error:
                return _err('No se pudo preparar el recurso del área: ' + str(error))
        study = {
            'study_revision': revision,
            'study_mode': 'reanalysis_only',
            'reference_station_ids': reference_ids,
            'poi': poi,
            'radius_km': radius_km,
            'target_height_m': target_height,
            'source_id': source_id,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        sess.setdefault('data_sources', {})[source_id] = source_record
        sess['reanalysis_study'] = study
        sess['active_study_mode'] = 'reanalysis_only'
        sess['active_data_source'] = source_id
        sess['ws_height_m'] = target_height
        sess['meta'] = {
            **(sess.get('meta') or {}),
            'display_name': 'Recurso de reanálisis del área',
            'format': 'reanalysis_area',
            'reanalysis_type': 'ERA5 + MERRA-2',
            'latitude': poi['lat'],
            'longitude': poi['lon'],
            'wind_height_m': target_height,
            'station_count': source_record['node_count'],
            'aggregation_method': source_record['aggregation_method'],
            'data_source': source_id,
        }
        for stale_key in (
            'qc_report', 'qc_flags', 'wind_stats', 'wind_advanced',
            'df_energy', 'energy_summary', 'hybrid_result',
        ):
            sess.pop(stale_key, None)
        return jsonify(_reanalysis_study_payload(sess))

    def _default_park_configuration():
        return {
            'configuration_revision': 0,
            'commercialization_mode': 'total_sale',
            'demand_source': 'none',
            'annual_demand_mwh': 0.0,
            'sale_scheme': 'spot_pml',
            'pml_side': 'venta',
            'pml_node': '',
            'project_horizon_years': 25,
            'reporting_currency': 'USD',
            'base_fx_mxn_usd': None,
            'base_interest_rate_pct': None,
            'market_models': {},
            'updated_at': None,
        }

    def _park_demand_series_info(sess):
        market_demand = _market_demand_profile(sess)
        if market_demand is None:
            return None
        _, demand_info = market_demand
        record = (sess.get('market') or {}).get('demand') or {}
        meta = record.get('meta') or {}
        return {
            'filename': demand_info['filename'],
            'n_rows': demand_info['n_records'],
            'unit': demand_info['unit'],
            'date_range': meta.get('date_range'),
            'dt_h': demand_info['dt_h'],
            'coverage_hours': demand_info['coverage_hours'],
            'observed_energy_mwh': demand_info['observed_energy_mwh'],
            'annual_mwh': demand_info['annual_mwh'],
            'mean_mw': demand_info['mean_mw'],
            'max_mw': demand_info['max_mw'],
            'annualization': demand_info['annualization'],
        }

    def _park_configuration_payload(sess):
        payload = _default_park_configuration()
        payload.update(sess.get('park_configuration') or {})
        models = payload.get('market_models') or {}
        models = {'pml': models['pml']} if isinstance(
            models, dict) and isinstance(models.get('pml'), dict) else {}
        payload['market_models'] = models
        demand_series = _park_demand_series_info(sess)
        demand_required = payload['commercialization_mode'] in {
            'self_consumption', 'self_consumption_surplus'}
        demand_ready = (not demand_required) or demand_series is not None
        fx_value = payload.get('base_fx_mxn_usd')
        interest_value = payload.get('base_interest_rate_pct')
        readiness = {
            'demand': demand_ready,
            'pml': bool(models.get('pml', {}).get('annual')),
            'fx': fx_value is not None and np.isfinite(float(fx_value)) and float(fx_value) > 0,
            'interest': interest_value is not None and np.isfinite(float(interest_value)) and float(interest_value) >= 0,
        }
        payload['demand_series'] = demand_series
        payload['demand_series_required'] = demand_required
        payload['annual_demand_mwh'] = (
            demand_series['annual_mwh'] if demand_required and demand_series else 0.0)
        payload['readiness'] = readiness
        payload['ready_for_design'] = bool(all(readiness.values()))
        return payload

    def _store_fixed_pml_profile(sess, *, source_key, meta, profile,
                                 normalization, base_year=2018):
        """Publica el PML observado de 2018 como perfil fijo, sin modelos."""
        park = sess.setdefault('park_configuration', _default_park_configuration())
        horizon = int(park.get('project_horizon_years', 25))
        start_year = datetime.now(timezone.utc).year
        mean_price = float(np.mean(profile))
        park.setdefault('market_models', {})['pml'] = {
            'category': 'pml',
            'mode': 'fixed_2018_profile',
            'source_key': source_key,
            'unit': (meta or {}).get('unit', '$/kWh'),
            'best_model': None,
            'base_year': int(base_year),
            'side': 'venta',
            'years': list(range(start_year, start_year + horizon)),
            'annual': [mean_price] * horizon,
            'ci_low': [],
            'ci_high': [],
            'hourly_profile_2018': [float(value) for value in profile],
            'n_hours': int(len(profile)),
            'normalization': normalization,
            'validation': None,
            'validation_method': None,
            'projection_policy': {
                'mode': 'fixed_replication',
                'base_year': int(base_year),
                'price_escalation_pct': 0.0,
                'leap_day_policy': 'interpolate_feb28_mar01',
            },
            'provenance': {
                'source': (meta or {}).get('label', source_key),
                'file': (meta or {}).get('file'),
                'history_basis': 'observed_base_year',
                'base_year': int(base_year),
                'side': 'venta',
                'modeling': 'none',
            },
            'published_at': datetime.now(timezone.utc).isoformat(),
        }
        park['configuration_revision'] = int(park.get('configuration_revision', 0)) + 1
        park['updated_at'] = datetime.now(timezone.utc).isoformat()

    @app.route('/api/sessions/<sid>/park-configuration', methods=['GET', 'PUT'])
    def park_configuration(sid):
        sess = get_session(sid)
        if request.method == 'GET':
            return jsonify(numpy_to_python(_park_configuration_payload(sess)))

        body = request.get_json(silent=True) or {}
        commercial = str(body.get('commercialization_mode', 'total_sale'))
        if commercial not in {
                'total_sale', 'self_consumption', 'self_consumption_surplus'}:
            return _err('Modo de comercialización inválido')
        sale_scheme = str(body.get('sale_scheme', 'spot_pml'))
        if sale_scheme not in {'spot_pml', 'ppa', 'mixed'}:
            return _err('Esquema de venta inválido')
        prior = sess.get('park_configuration') or _default_park_configuration()
        prior_models = prior.get('market_models') or {}
        prior = {
            **prior,
            'market_models': {
                'pml': prior_models['pml']
            } if isinstance(prior_models, dict) and isinstance(
                prior_models.get('pml'), dict) else {},
        }

        def _optional_number(name, fallback):
            value = body.get(name, fallback)
            if value in (None, ''):
                return None
            return float(value)

        try:
            horizon = int(body.get('project_horizon_years', 25))
            base_fx = _optional_number('base_fx_mxn_usd', prior.get('base_fx_mxn_usd'))
            base_interest = _optional_number(
                'base_interest_rate_pct', prior.get('base_interest_rate_pct'))
        except (TypeError, ValueError):
            return _err('Horizonte, tipo de cambio o tasa de interés inválidos')
        if not 5 <= horizon <= 40:
            return _err('El horizonte debe estar entre 5 y 40 años')
        if base_fx is not None and (not np.isfinite(base_fx) or base_fx <= 0):
            return _err('El tipo de cambio base debe ser mayor que cero')
        if base_interest is not None and (
                not np.isfinite(base_interest) or base_interest < 0):
            return _err('La tasa de interés base no puede ser negativa')

        if commercial == 'total_sale':
            demand_source = 'none'
            annual_demand_mwh = 0.0
        else:
            demand_source = 'market_series'
            demand_series = _park_demand_series_info(sess)
            if demand_series is None:
                return _err(
                    'Autoconsumo requiere cargar una serie histórica de demanda')
            annual_demand_mwh = demand_series['annual_mwh']

        revision = int(prior.get('configuration_revision', 0)) + 1
        sess['park_configuration'] = {
            **prior,
            'configuration_revision': revision,
            'commercialization_mode': commercial,
            'demand_source': demand_source,
            'annual_demand_mwh': annual_demand_mwh,
            'sale_scheme': sale_scheme,
            'pml_side': 'venta',
            'pml_node': str(body.get('pml_node') or '').strip(),
            'project_horizon_years': horizon,
            'base_fx_mxn_usd': base_fx,
            'base_interest_rate_pct': base_interest,
            'reporting_currency': str(body.get('reporting_currency', 'USD')).upper()
                if str(body.get('reporting_currency', 'USD')).upper() in {'USD', 'MXN'} else 'USD',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }
        fixed_pml = sess['park_configuration']['market_models'].get('pml')
        if isinstance(fixed_pml, dict) and fixed_pml.get('mode') == 'fixed_2018_profile':
            annual = fixed_pml.get('annual') or []
            if annual:
                fixed_price = float(annual[0])
                start_year = datetime.now(timezone.utc).year
                fixed_pml['years'] = list(range(start_year, start_year + horizon))
                fixed_pml['annual'] = [fixed_price] * horizon
        return jsonify(numpy_to_python(_park_configuration_payload(sess)))

    @app.route('/api/mcp/runs', methods=['POST'])
    def mcp_start_run():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        sess = get_session(sid)
        study = sess.get('mcp_study')
        if not study:
            return _err('Primero guarda la selección de Globe')
        index = _mcp_index(sess)
        observed = index.get(study['observed_station_id'])
        references = [index[value] for value in study['reference_station_ids'] if value in index]
        if not observed or not references:
            return _err('La selección MCP ya no es válida')
        try:
            target_height = float(body.get('target_height_m', study.get('target_height_m', 80)))
        except (TypeError, ValueError):
            return _err('target_height_m inválido')
        if not 1 <= target_height <= 300:
            return _err('target_height_m fuera de rango (1-300 m)')
        if not np.isclose(float(study.get('target_height_m', 80)), target_height):
            study = {
                **study,
                'study_revision': int(study.get('study_revision', 0)) + 1,
                'target_height_m': target_height,
                'updated_at': datetime.now(timezone.utc).isoformat(),
            }
            sess['mcp_study'] = study
            for prior_run in (sess.get('mcp_runs') or {}).values():
                prior_run['is_current_revision'] = False
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8]
        run_dir = Path(Config.MCP_RUNS_FOLDER) / run_id
        power_curve_file = None
        if body.get('power_curve_file'):
            candidate = (Path(Config.TURBINES_FOLDER) / Path(str(body['power_curve_file'])).name).resolve()
            turbine_root = Path(Config.TURBINES_FOLDER).resolve()
            if turbine_root not in candidate.parents or not candidate.is_file():
                return _err('Curva de potencia MCP no encontrada')
            power_curve_file = str(candidate)
        task_id = str(uuid.uuid4())
        task = {
            'status': 'queued', 'progress': 0, 'message': 'Corrida MCP en cola',
            'result': None, 'run_id': run_id, 'session_id': sid,
            '_last_access': time.time(),
        }
        tasks[task_id] = task
        study_snapshot = json.loads(json.dumps(study))
        is_official_m04 = observed.get('station_id') == M04_STATION_ID
        train_year = 2018 if is_official_m04 else int(body.get('train_year', 2018))
        test_year = 2019 if is_official_m04 else int(body.get('test_year', 2019))
        request_config = {
            'observation_files': list(observed.get('_paths', [])),
            'reference_files': [path for row in references for path in row.get('_paths', [])],
            'target_column': observed.get('target_column', body.get('target_column', 'WS_80mA_mean')),
            'observation_height_m': observed.get('measurement_height_m'),
            'observation_timezone': observed.get('timezone', 'UTC'),
            'target_height_m': target_height,
            'train_year': train_year,
            'test_year': test_year,
            'ranking_year': train_year,
            # Fixed thesis policy: sectors reconstruct direction/rose only.
            # They never replace the winning wind-speed model.
            'sector_count': 16,
            'run_id': run_id,
            'output_dir': str(run_dir),
            'max_wind_speed': float(body.get('max_wind_speed', 70)),
            'max_jump': float(body.get('max_jump', 15)),
            'max_persistence': int(body.get('max_persistence', 6)),
            'power_curve_file': power_curve_file,
            'turbine_count': int(body.get('turbine_count', 1)),
            'losses_pct': float(body.get('losses_pct', 0)),
        }

        def _run_mcp_thread():
            try:
                task.update(status='running', progress=8, message='Leyendo y controlando calidad de los datos')
                result = run_mcp_validation(request_config)
                task.update(progress=92, message='Registrando artefactos y trazabilidad')
                result['study'] = study_snapshot
                result['study']['observed_station'] = _public_station(observed)
                result['study']['reference_stations'] = []
                for row in references:
                    public = _public_station(row)
                    public['distance_km'] = round(_haversine_km(
                        float(observed['latitude']), float(observed['longitude']),
                        float(row['latitude']), float(row['longitude']),
                    ), 3)
                    result['study']['reference_stations'].append(public)
                nearest = min(result['study']['reference_stations'], key=lambda row: row.get('distance_km', float('inf')))
                result['reference_strategies'] = {
                    'nearest': {
                        'station_id': nearest.get('station_id'),
                        'display_name': nearest.get('display_name'),
                        'distance_km': nearest.get('distance_km'),
                    },
                    'best_reference': result.get('selected_reference'),
                    'multi_reference': result.get('multi_reference', {}),
                }
                result_path = run_dir / 'result.json'
                result_path.write_text(json.dumps(numpy_to_python(result), ensure_ascii=False, indent=2), encoding='utf-8')
                manifest_path = run_dir / 'run_manifest.json'
                if manifest_path.is_file():
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    manifest['study'] = study_snapshot
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
                sess.setdefault('mcp_runs', {})[run_id] = {
                    'run_id': run_id,
                    'study_revision': study_snapshot['study_revision'],
                    'result_path': str(result_path),
                    'created_at': result.get('created_at'),
                    'selected_reference': result.get('selected_reference'),
                    'selected_method': result.get('speed_model_selection', {}).get('selected_method'),
                    'status': 'done',
                    'is_current_revision': sess.get('mcp_study', {}).get('study_revision') == study_snapshot['study_revision'],
                }
                task.update(
                    status='done', progress=100, message='Corrida MCP terminada',
                    result={
                        'run_id': run_id,
                        'selected_reference': result.get('selected_reference'),
                        'selected_method': result.get('speed_model_selection', {}).get('selected_method'),
                        'study_revision': study_snapshot['study_revision'],
                    },
                )
            except Exception as error:
                logger.exception('Corrida MCP fallida')
                task.update(status='error', progress=100, message=str(error))

        _executor.submit(_run_mcp_thread)
        return jsonify({
            'run_id': run_id,
            'task_id': task_id,
            'status': 'queued',
            'study_revision': study_snapshot['study_revision'],
            'target_height_m': target_height,
        }), 202

    def _mcp_result_for_session(sess, run_id):
        record = (sess.get('mcp_runs') or {}).get(run_id)
        if not record:
            return None, None
        result_path = Path(record['result_path']).resolve()
        root = Path(Config.MCP_RUNS_FOLDER).resolve()
        if root not in result_path.parents or not result_path.is_file():
            return record, None
        return record, json.loads(result_path.read_text(encoding='utf-8'))

    @app.route('/api/mcp/runs/<run_id>', methods=['GET'])
    def mcp_get_run(run_id):
        sid = request.args.get('session_id')
        if not sid:
            return _err('session_id requerido')
        record, result = _mcp_result_for_session(get_session(sid), run_id)
        if not record:
            return jsonify({'error': 'Corrida no encontrada'}), 404
        if result is None:
            return jsonify({'error': 'Resultado no disponible'}), 409
        result['is_current_revision'] = record.get('study_revision') == get_session(sid).get('mcp_study', {}).get('study_revision')
        return jsonify(numpy_to_python(result))

    @app.route('/api/mcp/runs/<run_id>/artifacts/<artifact_key>', methods=['GET'])
    def mcp_download_artifact(run_id, artifact_key):
        sid = request.args.get('session_id')
        if not sid:
            return _err('session_id requerido')
        record, result = _mcp_result_for_session(get_session(sid), run_id)
        if not record or not result:
            return jsonify({'error': 'Corrida no encontrada'}), 404
        value = (result.get('artifacts') or {}).get(artifact_key)
        if not value:
            return jsonify({'error': 'Artefacto no encontrado'}), 404
        path = Path(value).resolve()
        run_root = (Path(Config.MCP_RUNS_FOLDER) / run_id).resolve()
        if run_root not in path.parents or not path.is_file():
            return jsonify({'error': 'Ruta de artefacto inválida'}), 403
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.route('/api/mcp/runs/<run_id>/publish', methods=['POST'])
    def mcp_publish_run(run_id):
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        sess = get_session(sid)
        record, result = _mcp_result_for_session(sess, run_id)
        if not record or not result:
            return jsonify({'error': 'Corrida no encontrada'}), 404
        artifact = (result.get('artifacts') or {}).get('historical_reconstruction_csv')
        if not artifact or not Path(artifact).is_file():
            return jsonify({'error': 'La corrida no contiene hindcast publicable'}), 409
        reconstructed = pd.read_csv(artifact)
        timestamp = pd.to_datetime(reconstructed['timestamp'], errors='coerce', utc=True)
        canonical = pd.DataFrame(index=pd.DatetimeIndex(timestamp, name='timestamp'))
        canonical['wind_speed'] = pd.to_numeric(reconstructed['long_term_wind_speed'], errors='coerce').to_numpy()
        if 'model_wind_speed_p05' in reconstructed:
            canonical['wind_speed_p05'] = pd.to_numeric(reconstructed['model_wind_speed_p05'], errors='coerce').to_numpy()
        if 'model_wind_speed_p95' in reconstructed:
            canonical['wind_speed_p95'] = pd.to_numeric(reconstructed['model_wind_speed_p95'], errors='coerce').to_numpy()
        if 'wind_direction' in reconstructed:
            canonical['wind_direction'] = pd.to_numeric(reconstructed['wind_direction'], errors='coerce').to_numpy()
        for turbulence_column in (
            'turbulence_intensity_p50', 'turbulence_intensity_p90',
            'sigma_u_p50', 'sigma_u_p90',
        ):
            if turbulence_column in reconstructed:
                canonical[turbulence_column] = pd.to_numeric(
                    reconstructed[turbulence_column], errors='coerce'
                ).to_numpy()
        for turbulence_label_column in ('turbulence_model_level', 'turbulence_data_source'):
            if turbulence_label_column in reconstructed:
                canonical[turbulence_label_column] = reconstructed[turbulence_label_column].astype(str).to_numpy()
        if 'direction_data_source' in reconstructed:
            canonical['direction_data_source'] = reconstructed['direction_data_source'].astype(str).to_numpy()
        canonical['data_source'] = reconstructed['data_source'].astype(str).to_numpy()
        canonical = canonical.loc[~canonical.index.isna()].sort_index()
        source_id = 'mcp:' + run_id
        target_height_m = result.get('config', {}).get('target_height_m')
        study_revision = result.get('study', {}).get('study_revision')
        canonical['target_height_m'] = target_height_m
        canonical['run_id'] = run_id
        canonical['study_revision'] = study_revision
        source_record = {
            'source_id': source_id,
            'label': 'Hindcast MCP ' + run_id,
            'kind': 'mcp',
            'run_id': run_id,
            'study_revision': study_revision,
            'target_height_m': target_height_m,
            'selected_reference': result.get('selected_reference'),
            'selected_direction_reference': result.get('selected_direction_reference'),
            'selected_method': result.get('speed_model_selection', {}).get('selected_method'),
            'speed_seasonality': result.get('historical_reconstruction_model', {}).get('seasonality'),
            'direction_correction': result.get('direction_correction'),
            'rows': int(len(canonical)),
            'period_start': canonical.index.min().isoformat() if len(canonical) else None,
            'period_end': canonical.index.max().isoformat() if len(canonical) else None,
            'long_term_statistics': _long_term_wind_summary(canonical),
            'turbulence_summary': {
                'measured_10min_available': bool(
                    result.get('turbulence', {}).get('measured_10min', {}).get('available')
                ),
                'historical_model_available': bool(
                    result.get('turbulence', {}).get('historical_model', {}).get('available')
                ),
                'training_year': result.get('turbulence', {}).get('historical_model', {}).get('training_year'),
            },
            '_turbulence': result.get('turbulence', {}),
            'df': canonical,
        }
        sess.setdefault('data_sources', {})[source_id] = source_record
        try:
            observed_frame, _ = read_certe_files(
                result.get('config', {}).get('observation_files', []),
                target_column=result.get('config', {}).get('target_column', 'WS_80mA_mean'),
                timezone=result.get('config', {}).get('observation_timezone', 'UTC'),
            )
            observed_hourly = resample_hourly(observed_frame)
            observed_canonical = pd.DataFrame(index=observed_hourly.index)
            observed_canonical.index.name = 'timestamp'
            observed_canonical['wind_speed'] = observed_hourly['wind_speed_obs']
            if 'wind_direction_obs' in observed_hourly:
                observed_canonical['wind_direction'] = observed_hourly['wind_direction_obs']
            sess['data_sources']['observed'] = {
                'source_id': 'observed',
                'label': result.get('study', {}).get('observed_station', {}).get('display_name', 'Observación'),
                'kind': 'observed',
                'rows': int(len(observed_canonical)),
                'df': observed_canonical,
                'target_height_m': result.get('config', {}).get('target_height_m'),
                'turbulence_summary': source_record['turbulence_summary'],
                '_turbulence': result.get('turbulence', {}),
            }
        except Exception:
            logger.exception('No se pudo registrar la fuente observada de la corrida %s', run_id)
        sess['active_data_source'] = source_id
        sess['ws_height_m'] = source_record['target_height_m'] or sess.get('ws_height_m', 80)
        # Todo derivado eólico previo queda obsoleto al cambiar la fuente base.
        # Se conserva la medición y la reconstrucción registradas, pero se exige
        # recalcular QC, energía y resultados posteriores.
        for stale_key in (
            'qc_report', 'qc_flags', 'wind_stats', 'wind_advanced',
            'df_energy', 'energy_summary', 'hybrid_result',
        ):
            sess.pop(stale_key, None)
        record['published_source_id'] = source_id
        return jsonify({
            k: numpy_to_python(v) for k, v in source_record.items()
            if k != 'df' and not str(k).startswith('_')
        })

    @app.route('/api/sessions/<sid>/data-sources', methods=['GET'])
    def mcp_data_sources(sid):
        sess = get_session(sid)
        rows = []
        if 'df' in sess and 'observed' not in (sess.get('data_sources') or {}):
            rows.append({
                'source_id': 'observed', 'label': 'Datos observados/manuales', 'kind': 'observed',
                'rows': int(len(sess['df'])), 'active': sess.get('active_data_source') in {None, 'observed'},
            })
        for source_id, source in (sess.get('data_sources') or {}).items():
            row = {
                k: numpy_to_python(v) for k, v in source.items()
                if k != 'df' and not str(k).startswith('_')
            }
            row['active'] = source_id == sess.get('active_data_source')
            rows.append(row)
        return jsonify({'active': sess.get('active_data_source') or 'observed', 'sources': rows})

    # ─── PRESET FILE BROWSER ──────────────────────────────────────────────────

    @app.route('/api/data/list_files', methods=['GET'])
    def list_preset_files():
        """List available meteorological data files from the configured DATA_FOLDER."""
        folder = Config.DATA_FOLDER
        if not os.path.isdir(folder):
            return jsonify({'files': [], 'folder': folder,
                            'error': 'DATA_FOLDER not found'})
        files = []
        for fname in _cached_list_files(folder):
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            if ext in ('txt', 'csv', 'tsv', 'dat'):
                fpath = os.path.join(folder, fname)
                size_mb = round(os.path.getsize(fpath) / 1_048_576, 1)
                source = 'merra2' if 'MERRA' in fname.upper() or 'MERRA2' in fname.upper() else \
                         'era5'   if 'ERA5' in fname.upper() else 'windnavigator'
                info = get_site_info(fname)
                files.append({
                    'filename':     fname,
                    'size_mb':      size_mb,
                    'source':       source,
                    'display_name': info['display_name'],
                    'state':        info['state'],
                    'municipality': info['municipality'],
                })
        return jsonify({'files': files, 'folder': folder, 'count': len(files)})

    @app.route('/api/data/load_preset', methods=['POST'])
    def load_preset_file():
        """Load a meteorological file from DATA_FOLDER by filename."""
        body     = request.get_json(silent=True) or {}
        sid      = _resolve_sid_or_new(body)
        filename = body.get('filename', '')
        source   = body.get('source', 'auto')

        if not filename:
            return jsonify({'error': 'filename required'}), 400

        # security: no path traversal
        safe_name = os.path.basename(filename)
        fpath = os.path.join(Config.DATA_FOLDER, safe_name)
        if not os.path.isfile(fpath):
            return jsonify({'error': f'File not found: {safe_name}'}), 404

        try:
            df, meta = _load_dataframe_from_path_cached(fpath, source_hint=source)
            sess = get_session(sid)
            sess['df']   = df.copy()
            sess['meta'] = meta
            if 'wind_speed' in sess['df'].columns:
                sess['wind_speed_col'] = 'wind_speed'
            else:
                detected_ws = _auto_detect_ws_col(sess['df'])
                if detected_ws:
                    sess['wind_speed_col'] = detected_ws

            # ── Height normalization ──────────────────────────────────────────
            target_h  = body.get('target_height_m')
            extr_meth = body.get('extrapolation_method', 'hellmann')
            native_h  = float(meta.get('wind_height_m') or 50.0)
            height_normalized = False

            if target_h is not None:
                target_h = float(target_h)
                ws_col = 'wind_speed' if 'wind_speed' in sess['df'].columns else None
                if ws_col and abs(target_h - native_h) > 0.5:
                    ws_raw = sess['df'][ws_col].values.astype(float)
                    if extr_meth == 'log':
                        ws_norm = extrapolate_wind_log(ws_raw, native_h, target_h)
                    else:
                        ws_norm = extrapolate_wind_hellmann(ws_raw, native_h, target_h)
                    sess['df'] = sess['df'].copy()
                    sess['df'][ws_col] = ws_norm
                    height_normalized = True
                sess['ws_height_m']        = target_h
                sess['extrapolation_method'] = extr_meth
                meta['wind_height_m']       = target_h
            else:
                sess['ws_height_m'] = native_h

            chart_col = 'wind_speed' if 'wind_speed' in sess['df'].columns else sess['df'].columns[0]
            chart_data = None
            if isinstance(sess['df'].index, pd.DatetimeIndex) and chart_col in sess['df'].columns:
                s = sess['df'][chart_col].dropna()
                chart_data = series_to_chart(s.index, s.values, label=chart_col)

            site_info = get_site_info(safe_name)
            return jsonify({
                'session_id':        sid,
                'meta':              numpy_to_python(meta),
                'columns':           list(sess['df'].columns),
                'chart':             chart_data,
                'n_rows':            len(sess['df']),
                'filename':          safe_name,
                'display_name':      site_info['display_name'],
                'state':             site_info['state'],
                'municipality':      site_info['municipality'],
                'reference_height_m': sess['ws_height_m'],
                'height_normalized': height_normalized,
                'native_height_m':   native_h,
            })
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    # ─── DATA UPLOAD ──────────────────────────────────────────────────────────

    @app.route('/api/data/upload', methods=['POST'])
    def upload_data():
        sid = request.form.get('session_id') or str(uuid.uuid4())
        source_hint = request.form.get('source', 'auto')

        if 'file' not in request.files:
            return _err('No file provided')

        f = request.files['file']
        if not f.filename or not allowed_file(f.filename):
            return _err(f'Unsupported file type: {f.filename}')

        # secure_filename anti path traversal y caracteres peligrosos
        f.filename = secure_filename(f.filename) or 'upload.csv'

        try:
            df, meta = _load_dataframe_from_upload_cached(f, source_hint=source_hint)
            sess = get_session(sid)
            sess['df'] = df
            sess['meta'] = meta
            if 'wind_speed' in df.columns:
                sess['wind_speed_col'] = 'wind_speed'
            else:
                detected_ws = _auto_detect_ws_col(df)
                if detected_ws:
                    sess['wind_speed_col'] = detected_ws
            # Auto-detect and store wind direction column for later use
            auto_wd = _auto_detect_wd_col(df)
            if auto_wd:
                sess['wd_col'] = auto_wd

            # quick preview
            preview_rows = df_to_json_safe(df, max_rows=200)
            chart_col = 'wind_speed' if 'wind_speed' in df.columns else df.columns[0]
            chart_data = None
            if isinstance(df.index, pd.DatetimeIndex) and chart_col in df.columns:
                s = df[chart_col].dropna()
                chart_data = series_to_chart(s.index, s.values, label=chart_col)

            return jsonify({
                'session_id': sid,
                'meta': numpy_to_python(meta),
                'preview': preview_rows[:50],
                'columns': list(df.columns),
                'chart': chart_data,
                'n_rows': len(df),
            })
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return _err(str(e), 500)

    # ─── DATA QUALITY ─────────────────────────────────────────────────────────

    @app.route('/api/data/quality', methods=['POST'])
    def data_quality():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        target_col = body.get('target_col', 'wind_speed')
        sess = get_session(sid)
        df = _analysis_df(sess, body)
        if df is None:
            return jsonify({'error': 'No data loaded. Upload a file first.'}), 400

        try:
            cache_parts = (
                'quality-v1',
                _df_cache_signature(sess, {'target_col': target_col}),
            )

            def compute_quality():
                checker = DataQualityChecker()
                return checker.run(df, target_col=target_col)

            (report, flags), cache_hit = get_or_compute('quality', cache_parts, compute_quality)
            report = dict(report)
            report['cache_hit'] = bool(cache_hit)
            sess['qc_report'] = report
            sess['qc_flags']  = flags

            # time series with flags highlighted
            s  = df[target_col] if target_col in df.columns else None
            ts_chart = None
            if s is not None and isinstance(df.index, pd.DatetimeIndex):
                step = max(1, len(s) // 2000)
                ts_chart = {
                    'labels': [str(t) for t in s.index[::step]],
                    'clean':  safe_float_list(s.values[::step].tolist()),
                    'flagged_idx': [str(t) for t in flags.index[flags.any(axis=1)][::max(1, int(flags.any(axis=1).sum()) // 200)]]
                    if flags.any(axis=1).sum() > 0 else [],
                }

            # per-column statistics for quality table
            numeric_cols = df.select_dtypes(include='number').columns.tolist()
            columns_report = {}
            for col in numeric_cols:
                s_col = df[col]
                n = len(s_col)
                n_miss = int(s_col.isna().sum())
                columns_report[col] = {
                    'mean':        round(float(s_col.mean()), 4) if s_col.notna().any() else None,
                    'std':         round(float(s_col.std()),  4) if s_col.notna().any() else None,
                    'min':         round(float(s_col.min()),  4) if s_col.notna().any() else None,
                    'max':         round(float(s_col.max()),  4) if s_col.notna().any() else None,
                    'missing_pct': round(n_miss / n * 100, 2) if n > 0 else 0,
                }

            return jsonify({'report': numpy_to_python(report), 'chart': ts_chart,
                            'columns_report': columns_report,
                            'data_source': _analysis_source_info(sess, body)})
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    # ─── ENERGY MODULE ────────────────────────────────────────────────────────

    @app.route('/api/energy/compute', methods=['POST'])
    def energy_compute():
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        sess = get_session(sid)
        source_body = {**body, **(body.get('simulation') or {})}
        df = _analysis_df(sess, source_body)
        if df is None:
            return jsonify({'error': 'No data loaded.'}), 400

        turbine_cfg = body.get('turbine', {})
        sim_cfg     = body.get('simulation', {})

        try:
            # ── turbine setup ──
            builtin = turbine_cfg.get('builtin_model')
            if builtin and builtin in PowerCurve.BUILTIN_CURVES:
                turbine = PowerCurve.from_builtin(builtin)
            elif builtin and builtin.endswith('.csv'):
                # file-based turbine from aerogeneradores folder
                fpath = os.path.join(Config.TURBINES_FOLDER, os.path.basename(builtin))
                turbine = _cached_power_curve(fpath)
            else:
                curve_pts = turbine_cfg.get('curve_points')
                if curve_pts:
                    pts = [(p['ws'], p['power']) for p in curve_pts]
                else:
                    pts = None
                turbine = PowerCurve(
                    name=turbine_cfg.get('name', 'Custom'),
                    rated_power_kw=float(turbine_cfg.get('rated_power_kw', 2000)),
                    cut_in=float(turbine_cfg.get('cut_in', 3.0)),
                    rated_speed=float(turbine_cfg.get('rated_speed', 12.0)),
                    cut_out=float(turbine_cfg.get('cut_out', 25.0)),
                    hub_height_m=float(turbine_cfg.get('hub_height_m', 80)),
                    rotor_diameter_m=float(turbine_cfg.get('rotor_diameter_m', 80)),
                    curve_points=pts,
                )
            turbine = _power_curve_at_hub_height(
                turbine, turbine_cfg.get('hub_height_m')
            )

            n_turbines = int(sim_cfg.get('n_turbines', 1))
            availability = float(sim_cfg.get('availability', 1.0))
            elec_losses  = float(sim_cfg.get('electrical_losses', 0.0))
            wake_losses  = float(sim_cfg.get('wake_losses', 0.0))
            curt_losses  = float(sim_cfg.get('curtailment_losses', 0.0))
            ws_height_m  = _sess_ws_h(sim_cfg, sess)
            altitude_m   = sim_cfg.get('altitude_m')
            extr_method  = sim_cfg.get('extrapolation', 'hellmann')
            hellmann_alpha = sim_cfg.get('hellmann_alpha')

            engine = WindToPowerEngine(
                turbine, n_turbines=n_turbines,
                availability=availability,
                electrical_losses=elec_losses,
                wake_losses=wake_losses,
                curtailment_losses=curt_losses,
            )

            ws_col = _energy_ws_col(sess, source_body)
            df_sim, summary = engine.run(
                df, ws_col=ws_col,
                ws_height_m=ws_height_m,
                altitude_m=altitude_m,
                extrapolation=extr_method,
                hellmann_alpha=hellmann_alpha,
            )
            sess['df_energy'] = df_sim
            sess['turbine']   = turbine
            sess['energy_summary'] = summary

            # ── AEP ──
            aep_calc = AEPCalculator()
            try:
                aep = aep_calc.compute_from_timeseries(df_sim, 'energy_net_kwh')
            except Exception:
                aep = {}

            monthly = aep_calc.monthly_distribution(df_sim, 'energy_net_kwh')
            sensitivity = aep_calc.sensitivity_table(
                aep.get('aep_p50_mwh', summary['energy_net_mwh']))

            # Weibull-based AEP if QC ran
            weibull_aep = None
            qc = sess.get('qc_report', {})
            wb = qc.get('weibull')
            if wb:
                try:
                    rho_val = air_density()
                    if altitude_m:
                        rho_val = air_density(pressure_pa=101325 * (1 - 2.2557e-5 * altitude_m) ** 5.2559)
                    weibull_aep = aep_calc.compute_from_weibull(
                        turbine, wb['k'], wb['lambda'], rho=rho_val,
                        n_turbines=n_turbines,
                        losses=1 - (availability * (1 - elec_losses) *
                                    (1 - wake_losses) * (1 - curt_losses)))
                except Exception:
                    pass

            # power curve chart
            t_dict = turbine.to_dict()
            step = max(1, len(df_sim) // 2000)
            ts_labels = [str(t) for t in df_sim.index[::step]]

            return jsonify(numpy_to_python({
                'data_source': _analysis_source_info(sess, source_body),
                'summary': summary,
                'aep': aep,
                'monthly': monthly,
                'sensitivity': sensitivity,
                'weibull_aep': weibull_aep,
                'turbine': t_dict,
                'timeseries': {
                    'labels': ts_labels,
                    'ws_hub': safe_float_list(df_sim['ws_hub'].values[::step].tolist()),
                    'power_kw': safe_float_list(df_sim['power_net_kw'].values[::step].tolist()),
                    'energy_kwh': safe_float_list(df_sim['energy_net_kwh'].values[::step].tolist()),
                },
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/energy/turbines', methods=['GET'])
    def list_turbines():
        """Return built-in turbines + real turbines from aerogeneradores folder."""
        builtin = [{
            'key': k, 'name': k, 'source': 'builtin',
            'rated_power_kw':  v['rated_power_kw'],
            'hub_height_m':    v['hub_height_m'],
            'rotor_diameter_m':v['rotor_diameter_m'],
            'cut_in': v['cut_in'], 'rated_speed': v['rated_speed'], 'cut_out': v['cut_out'],
        } for k, v in PowerCurve.BUILTIN_CURVES.items()]

        folder_turbines = list_turbine_info(Config.TURBINES_FOLDER)
        for t in folder_turbines:
            t['key']    = t['filename']
            t['source'] = 'file'

        return jsonify({'turbines': builtin + folder_turbines,
                        'folder':   Config.TURBINES_FOLDER})

    @app.route('/api/energy/compare_turbines', methods=['POST'])
    def compare_turbines():
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        sess = get_session(sid)
        df = _analysis_df(sess, body)
        if df is None:
            return jsonify({'error': 'No data loaded.'}), 400

        turbine_keys = body.get('turbine_keys', None)
        ws_col = _energy_ws_col(sess, body)
        ws_height_m = _sess_ws_h(body, sess)

        try:
            # load all available turbines (built-in + folder) if no explicit list given
            all_turbines = [PowerCurve.from_builtin(k) for k in PowerCurve.BUILTIN_CURVES]
            all_turbines += load_turbines_from_folder(Config.TURBINES_FOLDER)
            if turbine_keys:
                all_turbines = [t for t in all_turbines if t.name in turbine_keys]
            turbines = all_turbines if all_turbines else [PowerCurve.from_builtin('V150_4.5')]
            ws = df[ws_col].fillna(df[ws_col].median()).values

            # extrapolate to each turbine's hub height
            comp = TurbineComparator()
            results = comp.compare(turbines, ws, dt_h=1.0)
            return jsonify({
                'comparison': numpy_to_python(results),
                'data_source': _analysis_source_info(sess, body),
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # ─── WIND STATISTICS ──────────────────────────────────────────────────────────

    @app.route('/api/wind/stats', methods=['POST'])
    def wind_stats():
        """Compute full wind analysis: overview KPIs, rose, diurnal, climatology, extremes."""
        body    = request.get_json(silent=True) or {}
        sid     = _require_sid(body)
        ws_col  = body.get('ws_col',  'wind_speed')
        wd_col  = body.get('wd_col',  'wind_dir')
        temp_col= body.get('temp_col','temperature')
        pres_col= body.get('pres_col','pressure')
        sess = get_session(sid)
        df = _analysis_df(sess, body)
        if df is None:
            return jsonify({'error': 'No data loaded.'}), 400
        if wd_col not in df.columns:
            wd_col = _auto_detect_wd_col(df) or wd_col

        try:
            params = {
                'ws_col': ws_col, 'wd_col': wd_col,
                'temp_col': temp_col, 'pres_col': pres_col,
                'data_source': body.get('data_source') or sess.get('active_data_source'),
            }
            cache_parts = ('wind-stats-v1', _df_cache_signature(sess, params))

            def compute_stats():
                with timed('wind stats', logger):
                    return {
                        'overview':     numpy_to_python(compute_site_overview(
                                            df, ws_col, wd_col, temp_col, pres_col)),
                        'rose':         numpy_to_python(compute_wind_rose(df, ws_col, wd_col)),
                        'diurnal':      numpy_to_python(compute_diurnal_profile(df, ws_col)),
                        'climatology':  numpy_to_python(compute_monthly_climatology(df, ws_col)),
                        'extremes':     numpy_to_python(compute_extreme_events(df, ws_col)),
                        'histogram':    numpy_to_python(compute_histogram(df, ws_col)),
                        'sectors':      numpy_to_python(compute_sector_analysis(df, ws_col, wd_col)),
                        'month_hour_heatmap': numpy_to_python(compute_month_hour_heatmap(df, col=ws_col)),
                    }

            result, cache_hit = get_or_compute('wind_stats', cache_parts, compute_stats)
            result = dict(result)
            result['cache_hit'] = bool(cache_hit)
            result['data_source'] = _analysis_source_info(sess, body)
            sess['wind_stats'] = result
            return jsonify(result)
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    # ─── ADVANCED WIND ANALYSIS (Windographer-style) ──────────────────────────

    @app.route('/api/wind/advanced', methods=['POST'])
    def wind_advanced():
        """Measured/logger TI, hourly variability, shear and extremes."""
        body    = request.get_json(silent=True) or {}
        sid          = _require_sid(body)
        ws_col       = body.get('ws_col', 'wind_speed')
        wd_col       = body.get('wd_col', 'wind_dir')
        orog_factor  = float(body.get('orog_factor', 0.15))
        z_scale      = float(body.get('z_scale',     80.0))
        z_hub        = body.get('z_hub')
        z_hub        = float(z_hub) if z_hub is not None else None
        sess         = get_session(sid)
        ws_h         = _sess_ws_h(body, sess, 'ws_height')
        df = _analysis_df(sess, body)
        if df is None:
            return jsonify({'error': 'No data loaded.'}), 400
        if wd_col not in df.columns:
            wd_col = _auto_detect_wd_col(df) or wd_col

        try:
            source_id, source_record = _analysis_source_record(sess, body)
            turbulence_products = dict((source_record or {}).get('_turbulence') or {})
            hourly_variability = numpy_to_python(compute_turbulence_intensity(df, ws_col))
            measured_turbulence = turbulence_products.get('measured_10min') or {
                'available': False,
                'source_kind': 'observed_native',
                'reason': 'La fuente activa no conserva estadísticas nativas de desviación estándar.',
            }
            historical_turbulence = turbulence_products.get('historical_model') or {
                'available': False,
                'source_kind': 'modelled_not_measured',
                'reason': 'No existe una reconstrucción histórica de turbulencia publicada para esta fuente.',
            }
            if (source_record or {}).get('kind') == 'reanalysis_area':
                ti_warning = (
                    'La fuente ERA5/MERRA-2 del área es horaria y no contiene '
                    'desviación estándar nativa de 10 minutos; se reporta sólo '
                    'variabilidad interhoraria, no TI medida.')
            elif (source_record or {}).get('kind') == 'mcp':
                ti_warning = (
                    'TI medida sólo desde CERTE nativo de 10 min. La serie MCP '
                    'horaria se muestra separadamente como variabilidad interhoraria.')
            else:
                ti_warning = (
                    'La TI sólo se considera medida cuando la fuente conserva '
                    'media y desviación estándar nativas de 10 minutos.')
            result = {
                'data_source': _analysis_source_info(sess, body),
                # Backward-compatible key. It remains explicitly a variability
                # proxy and is never used as measured TI when CERTE is present.
                'wind_speed_variability': hourly_variability,
                'turbulence': {
                    'active_source_id': source_id,
                    'display_product': (
                        'measured_10min' if measured_turbulence.get('available')
                        else 'hourly_variability'
                    ),
                    'measured_10min': numpy_to_python(measured_turbulence),
                    'historical_model': numpy_to_python(historical_turbulence),
                    'hourly_variability': hourly_variability,
                },
                'ti_warning': ti_warning,
                'shear':                  numpy_to_python(compute_wind_shear(
                                              df, ws_col, ws_h,
                                              orog_factor=orog_factor, z_scale=z_scale,
                                              z_hub=z_hub)),
                'sectors':                numpy_to_python(compute_sector_analysis(df, ws_col, wd_col)),
                'return_periods':         numpy_to_python(compute_return_periods(df, ws_col)),
            }
            sess['wind_advanced'] = result
            return jsonify(result)
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    # ─── HYBRID SYSTEM (HOMER Pro-style) ──────────────────────────────────────

    @app.route('/api/hybrid/simulate', methods=['POST'])
    def hybrid_simulate():
        """Simulate Wind + Solar + Battery + Diesel dispatch for a given load."""
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        sess = get_session(sid)
        df = _analysis_df(sess, body)
        if df is None:
            return jsonify({'error': 'No data loaded.'}), 400

        try:
            ws_col = _energy_ws_col(sess, body)
            ws_h   = _sess_ws_h(body, sess, 'ws_height')
            lat    = float(body.get('lat', 20.0))

            # --- Wind power ---
            wind_cfg    = body.get('wind', {})
            wind_kw_cap = float(wind_cfg.get('capacity_kw', 500))
            hub_h       = float(wind_cfg.get('hub_height_m', 80))
            alpha_exp   = float(wind_cfg.get('alpha', 0.143))
            ws_arr      = df[ws_col].fillna(df[ws_col].median()).values if ws_col in df.columns else np.zeros(len(df))
            ws_hub      = ws_arr * (hub_h / ws_h) ** alpha_exp

            # Simple power curve: cubic between cut-in/rated/cut-out
            cut_in  = float(wind_cfg.get('cut_in', 3.0))
            rated   = float(wind_cfg.get('rated_speed', 12.0))
            cut_out = float(wind_cfg.get('cut_out', 25.0))
            wind_kw = np.where(
                (ws_hub >= cut_in) & (ws_hub < rated),
                wind_kw_cap * ((ws_hub - cut_in) / (rated - cut_in)) ** 3,
                np.where((ws_hub >= rated) & (ws_hub <= cut_out), wind_kw_cap, 0.0)
            )

            # --- Solar PV ---
            solar_cfg = body.get('solar', {})
            solar_kwp = float(solar_cfg.get('capacity_kwp', 0))
            solar_kw  = np.zeros(len(df))
            if solar_kwp > 0:
                ghi = estimate_clearsky_ghi(df.index if isinstance(df.index, pd.DatetimeIndex)
                                            else pd.date_range('2020-01-01', periods=len(df), freq='h'),
                                            lat=lat,
                                            cloud_factor=float(solar_cfg.get('cloud_factor', 0.65)))
                pv  = SolarPVModel(capacity_kwp=solar_kwp,
                                   pr=float(solar_cfg.get('pr', 0.80)))
                solar_kw = pv.power_kw(ghi)

            # --- Battery ---
            batt_cfg = body.get('battery', {})
            batt_kwh = float(batt_cfg.get('capacity_kwh', 0))
            battery  = None
            if batt_kwh > 0:
                battery = BatteryStorage(
                    capacity_kwh=batt_kwh,
                    c_rate=float(batt_cfg.get('c_rate', 0.5)),
                    eta_rt=float(batt_cfg.get('eta_rt', 0.92)))

            # --- Diesel ---
            diesel_cfg = body.get('diesel', {})
            diesel_kw  = float(diesel_cfg.get('capacity_kw', 0))
            diesel     = None
            if diesel_kw > 0:
                diesel = DieselGenerator(
                    capacity_kw=diesel_kw,
                    fuel_price_usd_l=float(diesel_cfg.get('fuel_price', 0.90)))

            # --- Load ---
            load_cfg = body.get('load', {})
            annual_kwh = float(load_cfg.get('annual_kwh', 1_000_000))
            peak_kw    = float(load_cfg.get('peak_kw', annual_kwh / 4380))
            profile_t  = load_cfg.get('profile_type', 'residential')
            load_kw    = build_load_profile(annual_kwh, peak_kw, profile_t, len(df))

            # --- Dispatch ---
            sim = HybridDispatch()
            result = sim.simulate(load_kw, wind_kw, solar_kw, battery, diesel)

            # --- Economics ---
            econ_cfg = body.get('economics', {})
            eco = HybridEconomics()
            economics = eco.compute(
                wind_kw=wind_kw_cap, solar_kw=solar_kwp,
                battery_kwh=batt_kwh, diesel_kw=diesel_kw,
                dispatch=result,
                discount_rate=float(econ_cfg.get('discount_rate', 0.08)),
                lifetime=int(econ_cfg.get('lifetime', 25)))

            result['economics'] = economics
            result['config'] = {
                'wind_kw': wind_kw_cap, 'solar_kwp': solar_kwp,
                'battery_kwh': batt_kwh, 'diesel_kw': diesel_kw,
                'load_annual_kwh': annual_kwh, 'profile': profile_t,
            }
            result['data_source'] = _analysis_source_info(sess, body)
            sess['hybrid_result'] = result
            return jsonify(numpy_to_python(result))

        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/hybrid/load_profile', methods=['POST'])
    def hybrid_load_profile():
        """Return synthetic load profile for preview."""
        body       = request.get_json(silent=True) or {}
        annual_kwh = float(body.get('annual_kwh', 1_000_000))
        peak_kw    = float(body.get('peak_kw', annual_kwh / 4380))
        ptype      = body.get('profile_type', 'residential')
        n          = int(body.get('n_hours', 8760))
        profile    = build_load_profile(annual_kwh, peak_kw, ptype, n)
        step       = max(1, n // 720)
        return jsonify({
            'load_kw': [round(float(v), 2) for v in profile[::step]],
            'peak_kw': round(float(profile.max()), 2),
            'mean_kw': round(float(profile.mean()), 2),
            'annual_kwh': round(float(profile.sum()), 0),
            'step': step,
        })

    # ─── TECHNO-ECONOMIC ──────────────────────────────────────────────────────────

    @app.route('/api/energy/techno_economic', methods=['POST'])
    def techno_economic():
        """Full techno-economic analysis: CAPEX, NPV/VAN, LCOE, VAE, payback, Cp."""
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        sess = get_session(sid)

        turbine_cfg = body.get('turbine', {})
        fin_cfg     = body.get('financial', {})

        if fin_cfg.get('energy_price_usd_kwh') in (None, ''):
            return _err('energy_price_usd_kwh debe venir de la configuración del proyecto')
        if fin_cfg.get('project_years') in (None, ''):
            return _err('project_years debe venir de la configuración del proyecto')
        try:
            energy_price_usd_kwh = float(fin_cfg['energy_price_usd_kwh'])
            n_yrs = int(fin_cfg['project_years'])
        except (TypeError, ValueError):
            return _err('Precio de energía o vida del proyecto inválidos')
        if not np.isfinite(energy_price_usd_kwh) or energy_price_usd_kwh < 0:
            return _err('El precio de energía debe ser un número no negativo')

        # Validación de rangos físicos/financieros (antes del df check)
        rated_kw = float(turbine_cfg.get('rated_power_kw', 2000))
        hub_h    = float(turbine_cfg.get('hub_height_m', 80))
        d_rate   = float(fin_cfg.get('discount_rate_pct', 8.0)) / 100.0
        err = _validate_ranges(discount_rate=d_rate, n_years=n_yrs,
                               rated_power_kw=rated_kw, hub_height_m=hub_h)
        if err is not None:
            return err
        analysis_df = _analysis_df(sess, body)
        if analysis_df is None:
            return _err('No data loaded.')

        try:
            # resolve turbine
            builtin = turbine_cfg.get('builtin_model')
            if builtin and builtin in PowerCurve.BUILTIN_CURVES:
                turbine = PowerCurve.from_builtin(builtin)
            elif builtin and builtin.endswith('.csv'):
                fpath = os.path.join(Config.TURBINES_FOLDER, os.path.basename(builtin))
                turbine = _cached_power_curve(fpath)
            else:
                turbine = PowerCurve(
                    name=turbine_cfg.get('name', 'Custom'),
                    rated_power_kw=float(turbine_cfg.get('rated_power_kw', 2000)),
                    cut_in=float(turbine_cfg.get('cut_in', 3.0)),
                    rated_speed=float(turbine_cfg.get('rated_speed', 12.0)),
                    cut_out=float(turbine_cfg.get('cut_out', 25.0)),
                    hub_height_m=float(turbine_cfg.get('hub_height_m', 80)),
                    rotor_diameter_m=float(turbine_cfg.get('rotor_diameter_m', 80)),
                )
            turbine = _power_curve_at_hub_height(
                turbine, turbine_cfg.get('hub_height_m')
            )

            ws_col      = _energy_ws_col(sess, body)
            ws_height_m = _sess_ws_h(body, sess)
            df          = analysis_df
            requested_n_turbines = int(body.get('n_turbines') or 1)
            _scenario = str(body.get('scenario', '')).lower()
            annual_energy_override_mwh = body.get('annual_energy_mwh')
            dt_h = _infer_dt_h(df.index)
            dispatch_index = df.index
            ws_hub, _, _, _, _, wind_info = _farm_wind_from_session(
                df, ws_col, body.get('wd_col', 'wind_dir'), ws_height_m, turbine.hub_height_m
            )
            if ws_hub is None:
                return _err(
                    'La serie horaria de viento no tiene datos válidos para el análisis tecno-económico '
                    f'({wind_info.get("reason", "invalid_session_wind")}).'
                )
            wind_info['scenario'] = _scenario
            wind_info['data_source'] = _analysis_source_info(sess, body)

            # air density — pressure ya viene en Pa (data.py normaliza)
            rho = air_density()
            if 'pressure' in df.columns and 'temperature' in df.columns:
                try:
                    p_pa = float(df['pressure'].dropna().mean())
                    t_c  = float(df['temperature'].dropna().mean())
                    rho  = float(air_density(temperature_c=t_c, pressure_pa=p_pa))
                except Exception:
                    pass

            if annual_energy_override_mwh is not None and np.isfinite(float(annual_energy_override_mwh)) and float(annual_energy_override_mwh) > 0:
                annual_kwh_per_turbine = float(annual_energy_override_mwh) * 1000 / max(requested_n_turbines, 1)
            else:
                # annual energy per turbine
                p_kw  = turbine.power_kw_density_corrected(ws_hub, rho)
                hours_in_data = len(ws_hub) * dt_h
                years_in_data = hours_in_data / 8760 if hours_in_data > 0 else 1
                annual_kwh_per_turbine = float((p_kw * dt_h).sum() / years_in_data)

            # sizing + financial — modelo de costos central WindPro
            demand_mwh    = float(fin_cfg.get('demand_mwh', 0))
            n_turbines_req= 0
            cost_model = CostModel.from_dict(fin_cfg.get('cost_model'))
            tea = TechnoEconomicAnalyzer(cost_model=cost_model)
            if demand_mwh > 0:
                n_turbines_req = tea.turbines_for_demand(annual_kwh_per_turbine, demand_mwh)
            n_turbines = int(body.get('n_turbines') or n_turbines_req or 1)
            if n_turbines < 1:
                return _err('n_turbines debe ser >= 1')

            # Análisis siempre en USD puros; FX (MXN/USD) lo aplica el frontend
            # como display equivalente. Antes el FX multiplicaba los flujos
            # internamente y mezclaba unidades en las llaves *_usd.
            fx_mxn_per_usd = float(fin_cfg.get('usd_to_mxn', 1.0))
            _project_years = n_yrs
            # ── Precio anual fijo y perfil horario PML 8760 ──
            def _clean_series(seq, ny):
                if not isinstance(seq, list):
                    return None
                out = []
                for v in seq[:ny]:
                    try:
                        fv = float(v)
                        out.append(fv if np.isfinite(fv) else None)
                    except (TypeError, ValueError):
                        out.append(None)
                # Replace None with last valid
                last = None
                for i, v in enumerate(out):
                    if v is not None and np.isfinite(v):
                        last = v
                    else:
                        out[i] = last
                # Drop trailing Nones if no valid was found
                if all(v is None for v in out):
                    return None
                # Pad to project_years if shorter
                if out and len(out) < ny:
                    fill = out[-1] if out[-1] is not None else None
                    out += [fill] * (ny - len(out))
                return out

            _price_series = _clean_series(fin_cfg.get('energy_price_series_usd_kwh'), _project_years)

            # ── Year-1 revenue ponderado horario (PML × FX × despacho 8760) ──
            _year1_rev_override = None
            try:
                price_hourly = fin_cfg.get('price_hourly_year1_usd_kwh')
                if isinstance(price_hourly, list) and len(price_hourly) > 0:
                    _ws_y1 = ws_hub if (ws_hub is not None and len(ws_hub) > 0) else None
                    if _ws_y1 is not None:
                        _p_kw_y1 = turbine.power_kw_density_corrected(_ws_y1, rho) * n_turbines
                        # Energy per hour (kWh). dt_h might not be 1h; if dt != 1h we time-resample to 8760 by simple slicing/stretch.
                        _energy_per_hour = _p_kw_y1 * dt_h
                        # Use the first 8760 hours; if data <8760, pad with mean
                        n_h = 8760
                        if len(_energy_per_hour) >= n_h:
                            e_arr = np.asarray(_energy_per_hour[:n_h], dtype=float)
                        else:
                            e_arr = np.full(n_h, float(np.mean(_energy_per_hour)))
                            e_arr[:len(_energy_per_hour)] = np.asarray(_energy_per_hour, dtype=float)
                        # Scale energy to match expected annual energy (year 1)
                        _y1_energy_target_kwh = annual_kwh_per_turbine * n_turbines
                        _e_sum = float(e_arr.sum())
                        if _e_sum > 0 and _y1_energy_target_kwh > 0:
                            e_arr = e_arr * (_y1_energy_target_kwh / _e_sum)
                        # Price array (USD/kWh); pad/truncate to 8760
                        p_arr_raw = np.asarray(price_hourly, dtype=float)
                        if len(p_arr_raw) >= n_h:
                            p_arr = p_arr_raw[:n_h]
                        else:
                            p_arr = np.full(n_h, float(np.nanmean(p_arr_raw)))
                            p_arr[:len(p_arr_raw)] = p_arr_raw
                        p_arr = np.where(np.isfinite(p_arr), p_arr, float(np.nanmean(p_arr_raw)))
                        revenue_usd_y1 = float((e_arr * p_arr).sum())
                        if np.isfinite(revenue_usd_y1) and revenue_usd_y1 > 0:
                            _year1_rev_override = revenue_usd_y1
                            # El perfil PML 2018 se replica en todos los años.
                            # Si la forma horaria de producción se conserva y
                            # sólo cambia su escala anual, el precio ponderado
                            # por energía es constante durante el proyecto.
                            weighted_price = revenue_usd_y1 / _y1_energy_target_kwh
                            if np.isfinite(weighted_price) and weighted_price > 0:
                                _price_series = [float(weighted_price)] * _project_years
            except Exception as _e_y1:
                logger.warning('Year-1 hourly revenue override failed: %s', _e_y1)

            analysis = tea.analyze(
                turbine, n_turbines, annual_kwh_per_turbine * n_turbines,
                energy_price_usd_kwh=energy_price_usd_kwh,
                discount_rate_pct=float(fin_cfg.get('discount_rate_pct', 8.0)),
                project_years=_project_years,
                usd_to_mxn=1.0,
                energy_price_series_usd_kwh=_price_series,
                year1_revenue_override_usd=_year1_rev_override,
                annual_inflation_pct=float(fin_cfg.get('annual_inflation_pct', 0.0)),
            )
            analysis['fx_mxn_per_usd'] = fx_mxn_per_usd
            if _price_series:
                analysis['energy_price_series_usd_kwh'] = _price_series

            # Cp analysis
            cp = tea.cp_analysis(turbine, ws_hub, rho=rho)
            p_kw_dispatch = turbine.power_kw_density_corrected(ws_hub, rho) * n_turbines
            hours_in_data = len(ws_hub) * dt_h
            years_in_data = hours_in_data / 8760 if hours_in_data > 0 else 1
            dispatch_annual_kwh = float((p_kw_dispatch * dt_h).sum() / years_in_data) if years_in_data > 0 else 0.0
            target_annual_kwh = annual_kwh_per_turbine * n_turbines
            if dispatch_annual_kwh > 0 and target_annual_kwh > 0:
                p_kw_dispatch = p_kw_dispatch * (target_annual_kwh / dispatch_annual_kwh)
            load_kw, load_info = _load_kw_for_dispatch(
                df, len(ws_hub), dt_h,
                demand_mwh=float(fin_cfg.get('demand_mwh', 0)),
                market_demand=_market_demand_profile(sess),
            )
            hourly_dispatch = _techno_hourly_dispatch(
                p_kw_dispatch, load_kw, dispatch_index, dt_h, n_turbines, turbine, load_info
            )

            # ── Energy Lab integration ────────────────────────────────────────
            energy_lab = None
            try:
                aep_calc = AEPCalculator()
                p_kw_el = turbine.power_kw_density_corrected(ws_hub, rho) * n_turbines

                if dispatch_index is not None and len(dispatch_index) == len(ws_hub):
                    idx_el = dispatch_index
                else:
                    freq_str = f'{int(dt_h * 60)}min' if dt_h < 1 else f'{int(dt_h)}h'
                    idx_el = pd.date_range('2020-01-01', periods=len(ws_hub), freq=freq_str)

                df_el = pd.DataFrame({
                    'ws_hub': ws_hub,
                    'power_net_kw': p_kw_el,
                    'energy_net_kwh': p_kw_el * dt_h,
                }, index=idx_el)

                hours_el = len(ws_hub) * dt_h
                years_el = hours_el / 8760 if hours_el > 0 else 1.0
                annual_net_mwh = float((p_kw_el * dt_h).sum() / years_el / 1000)
                rated_total_kw = turbine.rated_power_kw * n_turbines
                cf_pct_el = round(annual_net_mwh * 1000 / (rated_total_kw * 8760) * 100, 1) if rated_total_kw > 0 else 0
                flh_el = round(annual_net_mwh * 1000 / rated_total_kw) if rated_total_kw > 0 else 0

                aep_el = {}
                try:
                    aep_el = aep_calc.compute_from_timeseries(df_el, 'energy_net_kwh')
                except Exception:
                    pass

                monthly_el = aep_calc.monthly_distribution(df_el, 'energy_net_kwh')
                sensitivity_el = aep_calc.sensitivity_table(aep_el.get('aep_p50_mwh', annual_net_mwh))

                step_el = max(1, len(df_el) // 1500)
                ts_labels_el = [str(t) for t in df_el.index[::step_el]]

                energy_lab = {
                    'summary': {
                        'capacity_factor_pct': cf_pct_el,
                        'energy_net_mwh': round(annual_net_mwh, 1),
                        'full_load_hours': flh_el,
                    },
                    'aep': aep_el,
                    'monthly': monthly_el,
                    'sensitivity': sensitivity_el,
                    'turbine': turbine.to_dict(),
                    'timeseries': {
                        'labels': ts_labels_el,
                        'power_kw': safe_float_list(df_el['power_net_kw'].values[::step_el].tolist()),
                    },
                }
            except Exception as _e_lab:
                logger.warning('Energy Lab integration in techno_economic failed: %s', _e_lab)

            return jsonify(numpy_to_python({
                'analysis': analysis,
                'cp': cp,
                'hourly_dispatch': hourly_dispatch,
                'wind_source': wind_info,
                'turbine': turbine.to_dict(),
                'n_turbines_needed': n_turbines_req,
                'n_turbines_used':   n_turbines,
                'annual_kwh_per_turbine': round(annual_kwh_per_turbine),
                'rho': round(rho, 4),
                'energy_lab': energy_lab,
                # Altura de medición y hub usado — para auditoría / debug en UI
                'ws_height_m_used':  round(ws_height_m, 1),
                'hub_height_m_used': round(turbine.hub_height_m, 1),
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return _err(str(e), 500)

    # ─── LEVERAGED FINANCIAL ANALYSIS ────────────────────────────────────────
    @app.route('/api/energy/leveraged', methods=['POST'])
    def leveraged_analysis():
        """Pure financial calculation — no session required.
        Inputs: capex_usd, annual_revenue_usd, annual_opex_usd, project_years,
                equity_discount_rate_pct, debt_ratio_pct, interest_rate_pct,
                loan_years, usd_to_mxn (optional).
        """
        body = request.json or {}
        if body.get('project_years') in (None, ''):
            return _err('project_years debe venir de la configuración del proyecto')
        try:
            capex          = float(body['capex_usd'])
            annual_revenue = float(body['annual_revenue_usd'])
            annual_opex    = float(body['annual_opex_usd'])
            project_years  = int(body['project_years'])
            equity_rate    = float(body.get('equity_discount_rate_pct', 12)) / 100.0
            debt_ratio     = float(body.get('debt_ratio_pct', 70)) / 100.0
            interest_rate  = float(body.get('interest_rate_pct', 8)) / 100.0
            loan_years     = int(body.get('loan_years', 15))
            fx             = float(body.get('usd_to_mxn', 1.0))

            equity_usd = capex * (1.0 - debt_ratio)
            debt_usd   = capex * debt_ratio

            # PMT (annuity formula)
            if debt_usd > 0 and interest_rate > 0:
                pmt = debt_usd * interest_rate / (1.0 - (1.0 + interest_rate) ** (-loan_years))
            elif debt_usd > 0:
                pmt = debt_usd / loan_years
            else:
                pmt = 0.0

            noi = annual_revenue - annual_opex  # net operating income

            # WACC (simplified, no tax shield)
            wacc = equity_rate * (1.0 - debt_ratio) + interest_rate * debt_ratio

            # Build year-by-year table
            rows = []
            balance = debt_usd
            cum_ecf = -equity_usd  # year-0 equity outflow

            for yr in range(1, project_years + 1):
                in_debt = yr <= loan_years and debt_usd > 0
                if in_debt:
                    interest   = balance * interest_rate
                    principal  = pmt - interest
                    balance_end = max(balance - principal, 0.0)
                    dscr       = round(noi / pmt, 3) if pmt > 0 else None
                else:
                    interest = principal = 0.0
                    balance_end = 0.0
                    dscr = None

                equity_cf = noi - (pmt if in_debt else 0.0)
                cum_ecf  += equity_cf

                rows.append({
                    'year':          yr,
                    'balance_start': round(balance),
                    'interest':      round(interest),
                    'principal':     round(principal),
                    'balance_end':   round(balance_end),
                    'dscr':          dscr,
                    'equity_cf':     round(equity_cf),
                    'cum_equity_cf': round(cum_ecf),
                })
                balance = balance_end

            # Leveraged NPV: equity CFs discounted at equity_rate
            ecfs = [-equity_usd] + [r['equity_cf'] for r in rows]
            lev_npv = sum(cf / (1.0 + equity_rate) ** t for t, cf in enumerate(ecfs))

            # Leveraged IRR (bisection)
            def _npv_r(r):
                return sum(cf / (1.0 + r) ** t for t, cf in enumerate(ecfs))

            lev_irr = None
            try:
                lo, hi = -0.9999, 9.99
                f_lo, f_hi = _npv_r(lo), _npv_r(hi)
                if f_lo * f_hi < 0:
                    for _ in range(256):
                        mid = (lo + hi) / 2.0
                        if abs(hi - lo) < 1e-9:
                            break
                        if _npv_r(mid) * f_lo > 0:
                            lo = mid
                        else:
                            hi = mid
                    lev_irr = round(mid * 100.0, 2)
            except Exception:
                pass

            dscr_vals  = [r['dscr'] for r in rows if r['dscr'] is not None]
            dscr_mean  = round(sum(dscr_vals) / len(dscr_vals), 3) if dscr_vals else None
            dscr_min   = round(min(dscr_vals), 3) if dscr_vals else None

            # Simple equity payback — interpola dentro del año para ser comparable
            # con el DPBP (que también interpola). Sin esto, payback se redondea
            # al siguiente año entero y puede aparecer ≥ que el DPBP, que es
            # imposible matemáticamente con flujos positivos.
            equity_payback = None
            prev_cum = -equity_usd
            for r in rows:
                cf = r['equity_cf']
                cum_now = r['cum_equity_cf']
                if cum_now >= 0 and prev_cum < 0 and cf > 0:
                    equity_payback = round(r['year'] - 1 + (-prev_cum) / cf, 1)
                    break
                if cum_now >= 0:
                    equity_payback = float(r['year'])
                    break
                prev_cum = cum_now

            return jsonify({
                'equity_usd':           round(equity_usd),
                'debt_usd':             round(debt_usd),
                'annual_pmt_usd':       round(pmt),
                'noi_usd':              round(noi),
                'wacc_pct':             round(wacc * 100.0, 2),
                'leveraged_npv_usd':    round(lev_npv),
                'leveraged_irr_pct':    lev_irr,
                'equity_payback_years': equity_payback,
                'dscr_mean':            dscr_mean,
                'dscr_min':             dscr_min,
                'is_viable':            lev_npv > 0,
                'cashflow_table':       rows,
                'inputs': {
                    'capex_usd':       round(capex),
                    'equity_usd':      round(equity_usd),
                    'debt_usd':        round(debt_usd),
                    'debt_ratio_pct':  round(debt_ratio * 100.0, 1),
                    'equity_ratio_pct': round((1.0 - debt_ratio) * 100.0, 1),
                    'project_years':   project_years,
                    'loan_years':      loan_years,
                    'fx_mxn_per_usd':  fx,
                },
            })
        except Exception as e:
            logger.exception('Error en leveraged_analysis')
            return _err(str(e), 500)

    # ─── FINANCIAL SENSITIVITY & RISK ────────────────────────────────────────
    @app.route('/api/energy/sensitivity', methods=['POST'])
    def energy_sensitivity():
        """
        Tornado + correlation heatmap for financial risk analysis.
        Runs TechnoEconomicAnalyzer.analyze() for ±variations of each param.
        Input: session_id, turbine config, financial params, base_annual_kwh,
               n_turbines. Also returns price-regime ML clustering if session
               has a price column.
        """
        body        = request.get_json(silent=True) or {}
        sid         = _require_sid(body)
        sess        = get_session(sid)
        turbine_cfg = body.get('turbine', {})
        fin_cfg     = body.get('financial', {})

        if fin_cfg.get('energy_price_usd_kwh') in (None, ''):
            return _err('energy_price_usd_kwh debe venir de la configuración del proyecto')
        if fin_cfg.get('project_years') in (None, ''):
            return _err('project_years debe venir de la configuración del proyecto')
        try:
            base_price = float(fin_cfg['energy_price_usd_kwh'])
            base_years = int(fin_cfg['project_years'])
        except (TypeError, ValueError):
            return _err('Precio de energía o vida del proyecto inválidos')
        if not np.isfinite(base_price) or base_price < 0 or not 5 <= base_years <= 40:
            return _err('Precio de energía o vida del proyecto fuera de rango')

        # ── Resolve turbine ───────────────────────────────────────────────────
        builtin = turbine_cfg.get('builtin_model')
        if builtin and builtin in PowerCurve.BUILTIN_CURVES:
            turbine = PowerCurve.from_builtin(builtin)
        elif builtin and builtin.endswith('.csv'):
            fpath   = os.path.join(Config.TURBINES_FOLDER, os.path.basename(builtin))
            turbine = _cached_power_curve(fpath)
        else:
            turbine = PowerCurve(
                name='Custom',
                rated_power_kw=float(turbine_cfg.get('rated_power_kw', 2000)),
                cut_in=3.0, rated_speed=12.0, cut_out=25.0,
                hub_height_m=float(turbine_cfg.get('hub_height_m', 80)),
                rotor_diameter_m=float(turbine_cfg.get('rotor_diameter_m', 80)),
            )

        base_kwh   = float(body.get('base_annual_kwh', 0))
        n_turb     = int(body.get('n_turbines', 1))
        base_rate  = float(fin_cfg.get('discount_rate_pct', 8.0))
        base_escal = float(fin_cfg.get('price_escalation_pct', 0.0))
        base_infl  = float(fin_cfg.get('annual_inflation_pct', 0.0))
        # Optional leveraged config — when present, the heatmap/tornado return
        # equity NPV (apalancado) instead of project NPV, and the rate axis
        # represents the equity discount rate.
        lev_cfg = body.get('leveraged') or None
        if lev_cfg:
            base_rate     = float(lev_cfg.get('equity_discount_rate_pct', base_rate))
            lev_debt      = float(lev_cfg.get('debt_ratio_pct', 70)) / 100.0
            lev_interest  = float(lev_cfg.get('interest_rate_pct', 8)) / 100.0
            lev_loan_yrs  = int(lev_cfg.get('loan_years', 15))

        if base_kwh <= 0:
            return _err('base_annual_kwh must be > 0')

        def _run(price=None, rate=None, aep_scale=1.0, escal=None, capex_mult=1.0):
            cm = CostModel.from_dict(fin_cfg.get('cost_model'))
            # El CAPEX es lineal respecto al costo USD/kW del aerogenerador.
            # Aplicar aquí el multiplicador hace que el analizador recalcule de
            # forma coherente CAPEX, OPEX, flujos, VAN, payback y LCOE.
            if capex_mult != 1.0:
                cm.turbine_usd_per_kw *= float(capex_mult)
            tea = TechnoEconomicAnalyzer(cost_model=cm)
            p   = base_price if price is None else price
            r   = base_rate  if rate  is None else rate
            e   = base_escal if escal is None else escal
            kwh = base_kwh * aep_scale

            # build price escalation series
            price_series = None
            if e and e != 0:
                price_series = [p * ((1 + e/100) ** yr) for yr in range(base_years)]

            # Para modo apalancado, el VAN proyecto se descuenta a la tasa
            # del proyecto base (no al equity rate del eje r). Para modo
            # no apalancado el descuento sí es r.
            project_disc = float(fin_cfg.get('discount_rate_pct', 8.0)) if lev_cfg else r

            res = tea.analyze(
                turbine, n_turb, kwh,
                energy_price_usd_kwh=p,
                discount_rate_pct=project_disc,
                project_years=base_years,
                # Mantener las métricas en USD, igual que el cálculo principal.
                # El FX se conserva como parámetro de visualización equivalente.
                usd_to_mxn=1.0,
                energy_price_series_usd_kwh=price_series,
                annual_inflation_pct=base_infl,
            )
            npv     = float(res.get('npv_usd', 0) or 0)
            lcoe    = float(res.get('lcoe_usd_kwh', 0) or 0)
            payback = res.get('payback_years')
            payback = float(payback) if payback is not None and np.isfinite(float(payback)) else base_years * 2

            capex_total = float((res.get('capex') or {}).get('total_capex_usd', 0) or 0)
            annual_revenue = float(res.get('annual_revenue_usd', 0) or 0)
            annual_opex    = float(res.get('annual_opex_usd', 0) or 0)

            # ── Modo apalancado: reemplazar npv por VAN equity ───────────
            if lev_cfg and capex_total > 0:
                eq_rate = r / 100.0
                equity  = capex_total * (1.0 - lev_debt)
                debt    = capex_total * lev_debt
                if debt > 0 and lev_interest > 0:
                    pmt = debt * lev_interest / (1.0 - (1.0 + lev_interest) ** (-lev_loan_yrs))
                elif debt > 0:
                    pmt = debt / lev_loan_yrs
                else:
                    pmt = 0.0
                ecfs = [-equity]
                for yr in range(1, base_years + 1):
                    yr_price = price_series[yr - 1] if price_series else p
                    yr_revenue = (annual_revenue / p) * yr_price if p > 0 else annual_revenue
                    yr_cf = yr_revenue - annual_opex - (pmt if (yr <= lev_loan_yrs and debt > 0) else 0.0)
                    ecfs.append(yr_cf)
                lev_npv = sum(cf / (1.0 + eq_rate) ** t for t, cf in enumerate(ecfs))
                npv = lev_npv

            return npv, lcoe, payback

        base_npv, base_lcoe, base_pb = _run()

        def _delta(npv, lcoe, payback):
            dnpv = (npv - base_npv) / max(abs(base_npv), 1) * 100 if base_npv != 0 else 0
            dlcoe = (lcoe - base_lcoe) / max(base_lcoe, 1e-9) * 100
            dpb  = payback - base_pb
            return round(dnpv, 2), round(dlcoe, 2), round(dpb, 2)

        # ── Tornado variations ────────────────────────────────────────────────
        tornado = []
        cases = [
            ('Precio energía',    'price',   base_price*0.8,   base_price*1.2,   '-20%', '+20%'),
            ('Tasa de descuento', 'rate',    base_rate-2.0,    base_rate+2.0,    '-2pp', '+2pp'),
            ('CAPEX',             'capex',   0.85,             1.15,             '-15%', '+15%'),
            ('Producción (AEP)',  'aep',     0.9,              1.1,              '-10%', '+10%'),
            ('Escalamiento prec.','escal',   0.0,              3.0,              '0%/a', '+3%/a'),
        ]
        for label, kind, lo_v, hi_v, lo_lbl, hi_lbl in cases:
            if kind == 'price':
                lo_r = _run(price=lo_v);   hi_r = _run(price=hi_v)
            elif kind == 'rate':
                lo_r = _run(rate=lo_v);    hi_r = _run(rate=hi_v)
            elif kind == 'capex':
                lo_r = _run(capex_mult=lo_v); hi_r = _run(capex_mult=hi_v)
            elif kind == 'aep':
                lo_r = _run(aep_scale=lo_v);  hi_r = _run(aep_scale=hi_v)
            elif kind == 'escal':
                lo_r = _run(escal=lo_v);   hi_r = _run(escal=hi_v)
            else:
                continue
            lo_dn, lo_dl, lo_dp = _delta(*lo_r)
            hi_dn, hi_dl, hi_dp = _delta(*hi_r)
            tornado.append({
                'label': label,
                'low_npv_pct':  lo_dn, 'high_npv_pct':  hi_dn,
                'low_lcoe_pct': lo_dl, 'high_lcoe_pct': hi_dl,
                'low_label': lo_lbl,   'high_label': hi_lbl,
                'swing': round(abs(hi_dn - lo_dn), 2),
            })
        tornado.sort(key=lambda x: -x['swing'])

        # ── Heatmap: price × rate grid ────────────────────────────────────────
        price_steps = [round(base_price * m, 4) for m in [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4]]
        rate_steps  = [round(base_rate + d, 2)  for d in [-3, -2, -1, 0, 1, 2, 3]]
        heatmap = []
        for p in price_steps:
            row = []
            for r in rate_steps:
                npv_v, _, _ = _run(price=p, rate=r)
                row.append(round(npv_v / 1e6, 3))
            heatmap.append(row)

        # ── Price-regime ML clustering ────────────────────────────────────────
        regimes = None
        try:
            df = sess.get('df')
            price_col = next(
                (c for c in (df.columns if df is not None else [])
                 if any(k in c.lower() for k in ['precio','price','pml','costo','tarifa'])),
                None)
            if price_col is not None and df is not None:
                import warnings
                from sklearn.cluster import KMeans
                from sklearn.preprocessing import StandardScaler
                pdata = df[[price_col]].dropna()
                if len(pdata) >= 6:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        sc   = StandardScaler()
                        X    = sc.fit_transform(pdata.values)
                        km   = KMeans(n_clusters=3, random_state=42, n_init=10)
                        lbls = km.fit_predict(X)
                    centers = sc.inverse_transform(km.cluster_centers_).flatten()
                    order   = np.argsort(centers)  # low→mid→high
                    rank    = {int(order[i]): i for i in range(3)}
                    names   = ['Bajo', 'Medio', 'Alto']
                    colors  = ['#22c55e', '#f59e0b', '#ef4444']
                    pdata   = pdata.copy()
                    pdata['regime'] = [rank[int(l)] for l in lbls]
                    regime_stats = []
                    for i in range(3):
                        mask = pdata['regime'] == i
                        vals = pdata.loc[mask, price_col]
                        regime_stats.append({
                            'name':   names[i],
                            'color':  colors[i],
                            'mean':   round(float(vals.mean()), 4),
                            'std':    round(float(vals.std()), 4),
                            'pct':    round(float(mask.mean()) * 100, 1),
                            'center': round(float(centers[order[i]]), 4),
                        })
                    step = max(1, len(pdata) // 600)
                    ts_labels = [str(t) for t in pdata.index[::step]]
                    ts_regimes= [int(r) for r in pdata['regime'].values[::step]]
                    ts_values = [round(float(v), 4) for v in pdata[price_col].values[::step]]
                    regimes = {
                        'column': price_col,
                        'stats':  regime_stats,
                        'ts_labels':  ts_labels,
                        'ts_regimes': ts_regimes,
                        'ts_values':  ts_values,
                    }
        except Exception as _re:
            logger.debug('Regime ML error: %s', _re)

        base_price_idx = next(
            (i for i, p in enumerate(price_steps) if abs(p - base_price) < 1e-6), 4)
        base_rate_idx  = next(
            (i for i, r in enumerate(rate_steps)  if abs(r - base_rate)  < 1e-4), 3)
        return jsonify({
            'tornado': tornado,
            'heatmap': {
                'data':          heatmap,
                'price_labels':  [str(round(p, 4)) for p in price_steps],
                'rate_labels':   [f'{r:.2f}%' for r in rate_steps],
                'base_price':    round(base_price, 4),
                'base_rate':     round(base_rate, 2),
                'base_price_idx': base_price_idx,
                'base_rate_idx':  base_rate_idx,
            },
            'base': {
                'npv_usd':               round(base_npv, 0),
                'lcoe_usd_kwh':          round(base_lcoe, 5),
                'payback_years':         round(base_pb, 1),
                'energy_price_usd_kwh':  round(base_price, 4),
                'discount_rate_pct':     round(base_rate, 2),
            },
            'regimes': regimes,
        })

    # ─── WIND FARM (Parque Eólico — Jensen NOJ wake) ─────────────────────────
    @app.route('/api/farm/simulate', methods=['POST'])
    def farm_simulate():
        """
        Simulate a wind farm layout with Jensen NOJ wakes.
        Body:
          layout:        [[x_m, y_m], ...]
          turbine:       { builtin_model? OR full PowerCurve fields }
          k_wake:        wake decay (default 0.05)
          ct:            thrust coefficient (default 0.8)
          ws_height_m:   measurement height for extrapolation
          availability:  0..1
          electrical_losses: 0..1
          n_sectors:     wind direction sectors (default 36)
        Uses the wind/direction time series from the active session.
        """
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        sess = get_session(sid)

        # Validación rangos de la turbina solicitada (antes del df check)
        t_cfg = body.get('turbine', {})
        rated_kw = float(t_cfg.get('rated_power_kw', 2000))
        hub_h    = float(t_cfg.get('hub_height_m', 80))
        err = _validate_ranges(rated_power_kw=rated_kw, hub_height_m=hub_h)
        if err is not None:
            return err
        analysis_df = _analysis_df(sess, body)
        if analysis_df is None:
            return _err('No data loaded.')

        layout = body.get('layout') or []
        if not layout or len(layout) < 1:
            return _err('Layout vacío. Coloca al menos una turbina.')

        try:
            builtin = t_cfg.get('builtin_model')
            if builtin and builtin in PowerCurve.BUILTIN_CURVES:
                turbine = PowerCurve.from_builtin(builtin)
            elif builtin and builtin.endswith('.csv'):
                fpath = os.path.join(Config.TURBINES_FOLDER, os.path.basename(builtin))
                turbine = _cached_power_curve(fpath)
            else:
                turbine = PowerCurve(
                    name=t_cfg.get('name', 'Custom'),
                    rated_power_kw=rated_kw,
                    cut_in=float(t_cfg.get('cut_in', 3.0)),
                    rated_speed=float(t_cfg.get('rated_speed', 12.0)),
                    cut_out=float(t_cfg.get('cut_out', 25.0)),
                    hub_height_m=hub_h,
                )
            turbine = _power_curve_at_hub_height(
                turbine, t_cfg.get('hub_height_m')
            )

            df = analysis_df
            ws_col = _energy_ws_col(sess, body)
            wd_col = body.get('wd_col') or sess.get('wd_col', 'wind_dir')
            ws_h = _sess_ws_h(body, sess)
            ws_hub, wd, temperature_c, pressure_pa, _, wind_info = _farm_wind_from_session(
                df, ws_col, wd_col, ws_h, turbine.hub_height_m
            )
            if ws_hub is None:
                v_mean = float(body.get('v_mean', 9.0))
                weibull_k = float(body.get('weibull_k', 2.0))
                v_mean_hub = float(extrapolate_wind_hellmann(
                    np.asarray([v_mean], dtype=float), ws_h, turbine.hub_height_m
                )[0])
                fallback_reason = wind_info.get('reason', 'invalid_session_wind')
                logger.warning(
                    'farm_simulate: viento inválido para sesión %s '
                    '(columna «%s», razón=%s). Usando síntesis Weibull v_mean=%.2f m/s.',
                    sid, ws_col, fallback_reason, v_mean_hub
                )
                ws_hub, wd = _farm_weibull_fallback(v_mean_hub, weibull_k, n_hours=8760)
                temperature_c = None
                pressure_pa = None
                wind_info = {
                    **wind_info,
                    'source': 'weibull_fallback',
                    'fallback_reason': fallback_reason,
                    'fallback_mean_hub_ms': round(float(v_mean_hub), 4),
                    'fallback_weibull_k': float(weibull_k),
                    'valid': True,
                }

            result = simulate_wind_farm(
                layout_xy=layout,
                turbine=turbine,
                ws=ws_hub, wd=wd,
                k_wake=float(body.get('k_wake', 0.05)),
                ct=float(body.get('ct', 0.8)),
                n_sectors=int(body.get('n_sectors', 36)),
                availability=float(body.get('availability', 1.0)),
                electrical_losses=float(body.get('electrical_losses', 0.0)),
                temperature_c=temperature_c,
                pressure_pa=pressure_pa,
            )
            result['turbine'] = turbine.to_dict()
            result['data_source'] = _analysis_source_info(sess, body)
            wind_info['data_source'] = result['data_source']
            result['wind_source'] = wind_info.get('source')
            result['wind_diagnostics'] = wind_info
            return jsonify(numpy_to_python(result))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return _err(str(e), 500)

    @app.route('/api/grid/build', methods=['POST'])
    def grid_build_resource():
        """
        Build a multi-variable resource grid centred on a session site (or an
        arbitrary lat/lon) using Ordinary Kriging across all sites within
        radius_km. Returns 4 grids in one shot (speed/power/AEP/CF) plus
        the per-site values.

        OPTIMIZADO: lee archivos en paralelo (ThreadPool, 6 workers) y usa
        cache mtime-aware. Segunda llamada ~instantanea (O(n) vs O(n*IO)).
        """
        from math import gamma as _gamma
        from scipy.stats import weibull_min as _wbl

        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        sess = sessions.get(sid, {})

        cen_lat = body.get('center_lat')
        cen_lon = body.get('center_lon')
        if cen_lat is None and 'meta' in sess:
            cen_lat = sess['meta'].get('latitude')
            cen_lon = sess['meta'].get('longitude')
        if cen_lat is None or cen_lon is None:
            return jsonify({'error': 'Sin centro: carga un sitio o pasa center_lat/center_lon'}), 400
        cen_lat, cen_lon = float(cen_lat), float(cen_lon)

        radius_km = float(body.get('radius_km', 200))
        n_grid    = int(body.get('n_grid', 60))
        model     = body.get('model', 'exponential')
        year      = body.get('year')
        if year is not None:
            year = int(year)
        fixed_bounds = body.get('bounds') or None
        if fixed_bounds:
            try:
                fixed_bounds = {
                    'lat_min': float(fixed_bounds['lat_min']),
                    'lat_max': float(fixed_bounds['lat_max']),
                    'lon_min': float(fixed_bounds['lon_min']),
                    'lon_max': float(fixed_bounds['lon_max']),
                }
                if fixed_bounds['lat_min'] > fixed_bounds['lat_max']:
                    fixed_bounds['lat_min'], fixed_bounds['lat_max'] = fixed_bounds['lat_max'], fixed_bounds['lat_min']
                if fixed_bounds['lon_min'] > fixed_bounds['lon_max']:
                    fixed_bounds['lon_min'], fixed_bounds['lon_max'] = fixed_bounds['lon_max'], fixed_bounds['lon_min']
            except Exception:
                return jsonify({'error': 'Bounds inválidos para el área seleccionada'}), 400
        p_nom_kw  = float(body.get('p_nom_kw', 2000))
        rotor_m   = float(body.get('rotor_m', 80))
        cp        = float(body.get('cp', 0.4))

        folder = Config.DATA_FOLDER
        if not os.path.isdir(folder):
            return jsonify({'error': f'DATA_FOLDER no existe: {folder}'}), 404

        v_arr   = np.linspace(0.1, 25, 200)
        A_rotor = np.pi * (rotor_m ** 2) / 4
        p_kw    = np.minimum(0.5 * 1.225 * A_rotor * cp * v_arr ** 3 / 1000.0, p_nom_kw)

        # 1. Filtrar por radio (solo parse de nombre, sin IO de disco)
        candidate_paths = []
        for fn in sorted(os.listdir(folder)):
            if fn.startswith('.') or not fn.lower().endswith('.txt'):
                continue
            coords = parse_coords_from_filename(fn)
            if not coords:
                continue
            lat, lon = coords
            if fixed_bounds:
                if not (fixed_bounds['lat_min'] <= lat <= fixed_bounds['lat_max'] and
                        fixed_bounds['lon_min'] <= lon <= fixed_bounds['lon_max']):
                    continue
            else:
                if _haversine_km(lat, lon, cen_lat, cen_lon) > radius_km:
                    continue
            candidate_paths.append(os.path.join(folder, fn))

        if not candidate_paths:
            msg = 'el área seleccionada' if fixed_bounds else f'radio {radius_km} km'
            return jsonify({'error': f'Sin archivos .txt en {msg}'}), 400

        # 2. Leer en paralelo con cache mtime-aware (I/O bound -> ThreadPool)
        stat_list = parallel_read_sites(candidate_paths, year=year)

        # 3. Calcular AEP/CF por sitio (vectorial, rapido)
        sites = []
        for path, stats in zip(candidate_paths, stat_list):
            if stats is None or stats['_n'] < 24:
                continue
            fn  = os.path.basename(path)
            C   = stats['weibull_C']
            K   = stats['weibull_K']
            rho = stats['rho_mean']
            try:
                pdf     = _wbl.pdf(v_arr, K, scale=C)
                aep_kwh = float(np.trapz(p_kw * pdf, v_arr) * 8760)
                pd_wm2  = 0.5 * rho * (C ** 3) * _gamma(1 + 3 / max(K, 0.1))
            except Exception:
                aep_kwh, pd_wm2 = 0.0, 0.0
            cf_pct = 100.0 * aep_kwh / (p_nom_kw * 8760) if p_nom_kw > 0 else 0.0
            ws_hub = np.asarray(stats.get('ws_hub', []), dtype=float)
            gust_ms = float(np.nanpercentile(ws_hub, 99.5)) if ws_hub.size else float(stats.get('v_max', 0.0))
            sites.append({
                'filename':  fn,
                'lat':       stats['_lat'],
                'lon':       stats['_lon'],
                'speed':     float(stats['v_mean']),
                'gust':      gust_ms,
                'power':     float(pd_wm2),
                'aep':       float(aep_kwh / 1000.0),
                'cf':        float(cf_pct),
                'weibull_C': float(C),
                'weibull_K': float(K),
            })

        if len(sites) < 3:
            msg = 'del área seleccionada' if fixed_bounds else f'en {radius_km} km del centro'
            return jsonify({'error':
                f'Necesitas ≥ 3 sitios {msg} (hay {len(sites)})'}), 400

        def _scaled(vals):
            arr = np.asarray(vals, dtype=float)
            lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
            if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
                return np.full(len(arr), 50.0)
            return 100.0 * (arr - lo) / (hi - lo)

        speed_s = _scaled([s['speed'] for s in sites])
        power_s = _scaled([s['power'] for s in sites])
        gust_s  = _scaled([s['gust'] for s in sites])
        for idx, s in enumerate(sites):
            s['resource_score'] = float(0.45 * speed_s[idx] + 0.35 * power_s[idx] + 0.20 * gust_s[idx])

        optimal_sites = sorted(sites, key=lambda s: s['resource_score'], reverse=True)[:12]
        optimal_sites = [
            {
                'rank': i + 1,
                'score': float(s['resource_score']),
                'filename': s['filename'],
                'lat': s['lat'],
                'lon': s['lon'],
                'speed': s['speed'],
                'gust': s['gust'],
                'power': s['power'],
                'aep': s['aep'],
                'cf': s['cf'],
            }
            for i, s in enumerate(optimal_sites)
        ]

        lats = [s['lat'] for s in sites]
        lons = [s['lon'] for s in sites]

        units  = {'speed': 'm/s', 'gust': 'm/s', 'power': 'W/m²', 'resource_score': 'pts'}
        titles = {
            'speed': 'Velocidad de viento @ hub (m/s)',
            'gust':  'Ráfaga/extremo anual P99.5 @ hub (m/s)',
            'power': 'Densidad de potencia del viento (W/m²)',
            'resource_score': 'KPI compuesto de recurso eólico (0-100)',
        }

        grids = {}
        for var in ('speed', 'gust', 'power', 'resource_score'):
            vals = [s[var] for s in sites]
            try:
                g = kriging_grid(lons, lats, vals, n=n_grid, model=model,
                                 bounds=fixed_bounds)
            except Exception as e:
                return jsonify({'error': f'Kriging falló para {var}: {e}'}), 500
            g['unit']  = units[var]
            g['title'] = titles[var]
            grids[var] = g

        bounds = grids['speed']['bounds']
        return jsonify(numpy_to_python({
            'center':    {'lat': cen_lat, 'lon': cen_lon},
            'radius_km': radius_km,
            'n_sites':   len(sites),
            'sites':     sites,
            'optimal_sites': optimal_sites,
            'bounds':    bounds,
            'grids':     grids,
            'p_nom_kw':  p_nom_kw,
            'rotor_m':   rotor_m,
            'area_bounds': fixed_bounds,
        }))

    @app.route('/api/farm/wake_profile', methods=['POST'])
    def farm_wake_profile():
        """Return Jensen NOJ deficit (%) along the centreline vs x/D — for the physics chart."""
        body = request.get_json(silent=True) or {}
        rotor_d = float(body.get('rotor_d_m', 80.0))
        k_wake  = float(body.get('k_wake', 0.05))
        ct      = float(body.get('ct', 0.8))
        xd_arr  = np.arange(0.5, 20.01, 0.25)
        deficit_pct = [
            100.0 * jensen_deficit(xd * rotor_d, 0.0, rotor_d, k=k_wake, ct=ct)
            for xd in xd_arr
        ]
        return jsonify({
            'x_over_d':     [round(float(x), 3) for x in xd_arr],
            'deficit_pct':  [round(float(v), 4) for v in deficit_pct],
            'k_wake':       k_wake,
            'ct':           ct,
            'rotor_d_m':    rotor_d,
        })

    @app.route('/api/farm/recommend', methods=['POST'])
    def farm_recommend():
        """
        Recommend turbine count for a given demand and mean wind speed.
        Body:
          turbine:            {builtin_model? OR full PowerCurve fields}
          demand_mwh:         float — annual energy demand
          v_mean:             float — mean wind speed at hub height (m/s)
          weibull_k:          float — Weibull shape parameter (default 2.0)
          rho:                float — air density kg/m³ (default 1.225)
          availability:       float 0..1 (default 0.97)
          electrical_losses:  float 0..1 (default 0.03)
          avg_wake_loss:      float 0..1 (default 0.08)
        """
        body     = request.get_json(silent=True) or {}
        sid      = body.get('session_id')
        sess     = get_session(sid) if sid else {}
        t_cfg    = body.get('turbine', {})
        rated_kw = float(t_cfg.get('rated_power_kw', 2000))
        hub_h    = float(t_cfg.get('hub_height_m', 80))
        err = _validate_ranges(rated_power_kw=rated_kw, hub_height_m=hub_h)
        if err is not None:
            return err
        try:
            builtin = t_cfg.get('builtin_model')
            if builtin and builtin in PowerCurve.BUILTIN_CURVES:
                turbine = PowerCurve.from_builtin(builtin)
            elif builtin and builtin.endswith('.csv'):
                fpath = os.path.join(Config.TURBINES_FOLDER, os.path.basename(builtin))
                turbine = _cached_power_curve(fpath)
            else:
                turbine = PowerCurve(
                    name=t_cfg.get('name', 'Custom'),
                    rated_power_kw=rated_kw,
                    cut_in=float(t_cfg.get('cut_in', 3.0)),
                    rated_speed=float(t_cfg.get('rated_speed', 12.0)),
                    cut_out=float(t_cfg.get('cut_out', 25.0)),
                    hub_height_m=hub_h,
                    rotor_diameter_m=float(t_cfg.get('rotor_diameter_m', 80)),
                )
            target_hub_h = body.get(
                'hub_height_m',
                t_cfg.get('hub_height_m') if 'hub_height_m' in t_cfg else None,
            )
            turbine = _power_curve_at_hub_height(turbine, target_hub_h)
            source_info = _analysis_source_info(sess, body) if sid else None
            requested_mean = body.get('v_mean')
            used_active_source = requested_mean is None and bool(sid)
            weibull_k_rec = float(body.get('weibull_k', 2.0))
            if requested_mean is None and sid:
                source_df = _analysis_df(sess, body)
                source_ws_col = _energy_ws_col(sess, body)
                source_values = (
                    pd.to_numeric(source_df[source_ws_col], errors='coerce').dropna()
                    if source_df is not None and source_ws_col in source_df.columns else pd.Series(dtype=float)
                )
                requested_mean = float(source_values.mean()) if not source_values.empty else 9.0
                _, source_record = _analysis_source_record(sess, body)
                source_weibull = ((source_record or {}).get('long_term_statistics') or {}).get('weibull') or {}
                if source_weibull.get('k') is not None:
                    weibull_k_rec = float(source_weibull['k'])
            v_mean_rec = float(requested_mean if requested_mean is not None else 9.0)
            measurement_h_rec = _sess_ws_h(body, sess, 'measurement_height_m')
            if measurement_h_rec != 50.0 or body.get('measurement_height_m') is not None:
                measurement_h = measurement_h_rec
                v_mean_rec = float(extrapolate_wind_hellmann(
                    np.asarray([v_mean_rec], dtype=float), measurement_h, turbine.hub_height_m
                )[0])
            result = recommend_n_turbines(
                turbine=turbine,
                demand_mwh=float(body.get('demand_mwh', 5000)),
                v_mean=v_mean_rec,
                weibull_k=weibull_k_rec,
                rho=float(body.get('rho', 1.225)),
                availability=float(body.get('availability', 0.97)),
                electrical_losses=float(body.get('electrical_losses', 0.03)),
                avg_wake_loss=float(body.get('avg_wake_loss', 0.08)),
            )
            result['turbine'] = turbine.to_dict()
            result['data_source'] = source_info
            result['wind_input'] = {
                'mean_ms_at_hub': round(v_mean_rec, 4),
                'weibull_k': round(weibull_k_rec, 4),
                'source': 'active_project_source' if used_active_source else 'provided_spatial_mean',
            }
            return jsonify(result)
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return _err(str(e), 500)

    @app.route('/api/farm/optimize', methods=['POST'])
    def farm_optimize():
        """
        Greedy Sequential Placement within a park polygon + AEP simulation.
        Body:
          polygon:           [{lat, lon}, ...] — park boundary (≥ 3 vertices)
          turbine:           {builtin_model? OR full PowerCurve fields}
          n_turbines:        int — 0/omitted → auto from demand_mwh
          demand_mwh:        float — annual energy demand for auto n_turbines
          wind_grid:         {z, bounds} — kriging wind field (optional)
          v_mean:            float — fallback mean wind speed (m/s)
          weibull_k:         float — Weibull k for recommendation (default 2.0)
          min_spacing_d:     float — min spacing in rotor diameters (default 5)
          grid_step_d:       float — candidate grid step in D (default 1.5)
          k_wake:            float — NOJ wake decay (default 0.075)
          ct:                float — thrust coefficient (default 0.8)
          availability:      float 0..1 (default 0.97)
          electrical_losses: float 0..1 (default 0.03)
          session_id:        str — for real wind-series AEP simulation
          ws_height_m:       float — measurement height for extrapolation
          n_sectors:         int  — direction sectors (default 36)
        """
        body    = request.get_json(silent=True) or {}
        polygon = body.get('polygon') or []
        if len(polygon) < 3:
            return _err('El polígono del parque necesita al menos 3 vértices.')

        t_cfg    = body.get('turbine', {})
        rated_kw = float(t_cfg.get('rated_power_kw', 2000))
        hub_h    = float(t_cfg.get('hub_height_m', 80))
        err = _validate_ranges(rated_power_kw=rated_kw, hub_height_m=hub_h)
        if err is not None:
            return err
        try:
            builtin = t_cfg.get('builtin_model')
            if builtin and builtin in PowerCurve.BUILTIN_CURVES:
                turbine = PowerCurve.from_builtin(builtin)
            elif builtin and builtin.endswith('.csv'):
                fpath = os.path.join(Config.TURBINES_FOLDER, os.path.basename(builtin))
                turbine = _cached_power_curve(fpath)
            else:
                turbine = PowerCurve(
                    name=t_cfg.get('name', 'Custom'),
                    rated_power_kw=rated_kw,
                    cut_in=float(t_cfg.get('cut_in', 3.0)),
                    rated_speed=float(t_cfg.get('rated_speed', 12.0)),
                    cut_out=float(t_cfg.get('cut_out', 25.0)),
                    hub_height_m=hub_h,
                    rotor_diameter_m=float(t_cfg.get('rotor_diameter_m', 80)),
                )
            target_hub_h = body.get(
                'hub_height_m',
                t_cfg.get('hub_height_m') if 'hub_height_m' in t_cfg else None,
            )
            turbine = _power_curve_at_hub_height(turbine, target_hub_h)

            wind_grid  = body.get('wind_grid')  or None
            v_mean     = float(body.get('v_mean',     9.0))
            weibull_k  = float(body.get('weibull_k',  2.0))
            avail      = float(body.get('availability',      0.97))
            elec_loss  = float(body.get('electrical_losses', 0.03))
            sid  = body.get('session_id')
            sess = sessions.get(sid, {}) if sid else {}
            measurement_h = _sess_ws_h(body, sess, 'measurement_height_m')
            v_mean_hub = float(extrapolate_wind_hellmann(
                np.asarray([v_mean], dtype=float), measurement_h, turbine.hub_height_m
            )[0])

            # ── Load real wind series FIRST so the structured optimizer
            # can use it for energy-weighted dominant direction ─────────────
            ws_hub = None
            wd = None
            temp_c = None
            pres_p = None
            reference_wind_mean = None
            wind_info = {'source': 'weibull_fallback', 'reason': 'no_session'}
            ws_col = None

            if sid:
                df = _analysis_df(sess, body)
                if df is not None:
                    ws_col = _energy_ws_col(sess, body)
                    wd_col = body.get('wd_col') or sess.get('wd_col', 'wind_dir')
                    ws_hub, wd, temp_c, pres_p, reference_wind_mean, wind_info = _farm_wind_from_session(
                        df, ws_col, wd_col, measurement_h, turbine.hub_height_m
                    )
                else:
                    wind_info = {'source': 'weibull_fallback', 'valid': False, 'reason': 'session_without_df'}

            ws_hub_arr   = np.asarray(ws_hub, dtype=float) if ws_hub is not None else np.array([], dtype=float)
            hub_finite   = np.isfinite(ws_hub_arr)
            hub_positive = hub_finite & (ws_hub_arr > 0)
            if ws_hub is None or not hub_finite.any() or not hub_positive.any():
                fallback_reason = wind_info.get('reason', 'invalid_session_wind')
                logger.warning(
                    'farm_optimize: viento inválido sesión %s (col «%s», razón=%s). '
                    'Usando Weibull v_mean=%.2f m/s.',
                    sid or '—', ws_col or '—', fallback_reason, v_mean_hub
                )
                reference_wind_mean = v_mean
                ws_hub, wd = _farm_weibull_fallback(v_mean_hub, weibull_k, n_hours=8760)
                temp_c = None
                pres_p = None
                wind_info = {
                    **wind_info,
                    'source': 'weibull_fallback',
                    'fallback_reason': fallback_reason,
                    'fallback_mean_hub_ms': round(float(v_mean_hub), 4),
                    'fallback_weibull_k': float(weibull_k),
                    'valid': True,
                }

            # Air density for scoring
            rho_opt = 1.225
            if temp_c is not None and pres_p is not None:
                try:
                    from core.energy import air_density as _air_density
                    rho_opt = float(np.mean(_air_density(temp_c, pres_p)))
                except Exception:
                    pass

            n_turbines = int(body.get('n_turbines', 0))
            demand_mwh = float(body.get('demand_mwh', 5000))
            if n_turbines <= 0:
                rec = recommend_n_turbines(
                    turbine=turbine, demand_mwh=demand_mwh,
                    v_mean=v_mean_hub, weibull_k=weibull_k,
                    availability=avail, electrical_losses=elec_loss,
                )
                n_turbines = rec['n_turbines']

            # ── Structured layout: filter stagger_options by layout_type ──
            layout_type = body.get('layout_type', 'auto')  # 'auto'|'aligned'|'staggered'
            if layout_type == 'aligned':
                stagger_opts = (0.0,)
            elif layout_type == 'staggered':
                stagger_opts = (0.5,)
            else:
                stagger_opts = (0.0, 0.5)

            opt = optimize_layout_structured(
                polygon_latlon=polygon,
                turbine=turbine,
                n_turbines=n_turbines,
                ws=np.asarray(ws_hub, dtype=float),
                wd=np.asarray(wd,     dtype=float),
                wind_grid=wind_grid,
                fallback_wind_ms=v_mean_hub,
                min_spacing_d=float(body.get('min_spacing_d', 5.0)),
                k_wake=float(body.get('k_wake', 0.075)),
                ct=float(body.get('ct', 0.8)),
                stagger_options=stagger_opts,
                rho=rho_opt,
            )

            sim_result = None
            if ws_hub is not None and len(ws_hub) > 0:
                mean_ws = float(np.nanmean(ws_hub))
                local_multipliers = None
                multiplier_ref = reference_wind_mean if reference_wind_mean and reference_wind_mean > 0 else mean_ws
                if wind_grid and opt.get('wind_at_turbines') and multiplier_ref > 0:
                    wind_at = np.asarray(opt['wind_at_turbines'], dtype=float)
                    if wind_at.shape[0] == len(opt['layout_xy_m']):
                        wind_at_valid = np.isfinite(wind_at) & (wind_at > 0)
                        wind_at = np.where(wind_at_valid, wind_at, multiplier_ref)
                        local_multipliers = np.nan_to_num(
                            wind_at / multiplier_ref,
                            nan=1.0, posinf=1.0, neginf=1.0,
                        )

                sim_result = simulate_wind_farm(
                    layout_xy=opt['layout_xy_m'],
                    turbine=turbine,
                    ws=ws_hub, wd=wd,
                    k_wake=float(body.get('k_wake', 0.075)),
                    ct=float(body.get('ct', 0.8)),
                    n_sectors=int(body.get('n_sectors', 36)),
                    availability=avail,
                    electrical_losses=elec_loss,
                    temperature_c=temp_c,
                    pressure_pa=pres_p,
                    local_wind_multipliers=local_multipliers,
                )
            if sim_result:
                sim_result['turbine'] = turbine.to_dict()
                sim_result['data_source'] = _analysis_source_info(sess, body)
                wind_info['data_source'] = sim_result['data_source']
                sim_result['wind_source'] = wind_info.get('source')
                sim_result['wind_diagnostics'] = wind_info

            result = {
                **opt,
                'turbine': turbine.to_dict(),
                'data_source': _analysis_source_info(sess, body),
            }
            if sim_result:
                result['simulation'] = sim_result
            return jsonify(numpy_to_python(result))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return _err(str(e), 500)

    @app.route('/api/farm/session_demand', methods=['POST'])
    def farm_session_demand():
        """
        Detect and compute annual energy demand from the session's load/demand column.
        Searches for columns containing: load, demand, demanda, consumo, carga.
        Returns annual_mwh, mean_mw, max_mw, column name, n_records.
        """
        body = request.get_json(silent=True) or {}
        sid  = body.get('session_id')
        if not sid:
            return _err('session_id required')
        sess = sessions.get(sid, {})
        market_demand = _market_demand_profile(sess)
        if market_demand is not None:
            _, info = market_demand
            return jsonify(numpy_to_python({
                'found': True,
                'source': info['source'],
                'filename': info['filename'],
                'column': info['column'],
                'annual_mwh': info['annual_mwh'],
                'mean_mw': info['mean_mw'],
                'max_mw': info['max_mw'],
                'n_records': info['n_records'],
                'dt_h': info['dt_h'],
                'coverage_hours': info['coverage_hours'],
                'annualization': info['annualization'],
            }))
        # La demanda es opcional y la fuente eólica activa puede ser un recurso
        # de área ERA5/MERRA-2 sin existir una serie observada en sess['df'].
        # En ese caso se inspecciona la fuente activa y se responde found=false,
        # sin bloquear la configuración del parque.
        df = _analysis_df(sess, body)
        if df is None:
            return jsonify({'found': False, 'columns': []})

        # Priority: exact column names common in Mexican energy datasets
        priority_exact = [
            'Demanda Eléctrica (MW)', 'Demanda Electrica (MW)', 'LOAD', 'Load',
            'demand_mw', 'load_mw', 'Potencia Demandada (MW)', 'consumo_mw',
            'DEMANDA', 'Demanda', 'demanda', 'Carga (MW)', 'carga_mw',
        ]
        demand_kws = ['load', 'demand', 'demanda', 'consumo', 'carga']

        demand_col = None
        for pc in priority_exact:
            if pc in df.columns:
                demand_col = pc; break
        if demand_col is None:
            for col in df.columns:
                if any(kw in col.lower() for kw in demand_kws):
                    demand_col = col; break

        if demand_col is None:
            return jsonify({'found': False, 'columns': list(df.columns)[:30]})

        # Infer time step
        dt_h = 1.0
        if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1:
            try:
                dt_h = float(df.index.to_series().diff().mode()[0].total_seconds() / 3600)
            except Exception:
                dt_h = 1.0

        vals = pd.to_numeric(df[demand_col], errors='coerce').dropna()
        if vals.empty:
            return jsonify({'found': False, 'columns': list(df.columns)[:30]})

        annual_mwh = round(float(vals.sum() * dt_h), 1)
        return jsonify({
            'found':      True,
            'column':     demand_col,
            'annual_mwh': annual_mwh,
            'mean_mw':    round(float(vals.mean()), 3),
            'max_mw':     round(float(vals.max()),  3),
            'n_records':  int(len(vals)),
            'dt_h':       round(dt_h, 4),
        })

    # ─── EXPORT ───────────────────────────────────────────────────────────────

    @app.route('/api/export/csv', methods=['GET', 'POST'])
    def export_csv():
        if request.method == 'GET':
            body = {}
            sid     = request.args.get('session_id') or _require_sid(request.args)
            dataset = request.args.get('type', 'raw')
        else:
            body    = request.get_json(silent=True) or {}
            sid     = _require_sid(body)
            dataset = body.get('dataset', 'energy')
        # sanitiza dataset antes de interpolar en filename
        dataset = secure_filename(dataset) or 'export'
        sess    = get_session(sid)

        key_map = {'energy': 'df_energy', 'raw': 'df'}
        df_key  = key_map.get(dataset, 'df')

        if dataset == 'raw':
            # "Raw" significa aquí la fuente eólica de trabajo publicada. En
            # modo sólo reanálisis no existe deliberadamente sess['df'].
            df = _analysis_df(sess, body)
        else:
            df = sess.get(df_key)
        if df is None:
            return jsonify({'error': f'Dataset "{dataset}" not available.'}), 400
        df = df.copy()
        if isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()

        ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        fname = f'{secure_filename(dataset)}_{ts}.csv'
        if not fname.endswith('.csv'):
            fname += '.csv'
        fpath = os.path.join(Config.EXPORT_FOLDER, fname)
        df.to_csv(fpath, index=False)

        from flask import send_file
        return send_file(fpath, as_attachment=True, download_name=fname,
                         mimetype='text/csv')

    # ─── AI ASSISTANT ─────────────────────────────────────────────────────────

    def _build_ai_context(sess):
        meta = sess.get('meta', {})
        qc = sess.get('qc_report', {})
        estats = sess.get('energy_summary', {})
        active_frame = _analysis_df(sess)
        brief = {
            'has_data': active_frame is not None,
            'data_source': _analysis_source_info(sess),
            'date_range': meta.get('date_range'),
            'n_rows': meta.get('n_rows'),
            'columns': (meta.get('columns') or [])[:20],
            'qc_overall': qc.get('overall'),
            'qc_missing_pct': qc.get('pct_missing'),
            'energy_net_mwh': estats.get('energy_net_mwh'),
            'capacity_factor_pct': estats.get('capacity_factor_pct'),
        }
        return json.dumps(brief, ensure_ascii=False)

    def _openai_answer(q, sess):
        api_key = (getattr(Config, 'OPENAI_API_KEY', '') or '').strip()
        if not api_key:
            return None

        model = (getattr(Config, 'OPENAI_MODEL', '') or 'gpt-4.1-mini').strip()
        timeout_sec = int(getattr(Config, 'OPENAI_TIMEOUT_SEC', 30) or 30)
        context_json = _build_ai_context(sess)
        payload = {
            'model': model,
            'input': [
                {
                    'role': 'system',
                    'content': (
                        'Eres un asistente tecnico de energia eolica en AERO OPEN CIM. '
                        'Responde en espanol, claro y breve. Si faltan datos, dilo.'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f'Contexto de sesion (JSON): {context_json}\n\n'
                        f'Pregunta del usuario: {q}'
                    ),
                },
            ],
            'temperature': 0.3,
        }
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        resp = requests.post(
            'https://api.openai.com/v1/responses',
            headers=headers,
            json=payload,
            timeout=timeout_sec,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f'OpenAI API error {resp.status_code}: {resp.text[:250]}')
        data = resp.json()
        answer = (data.get('output_text') or '').strip()
        if answer:
            return answer

        # fallback parse if output_text is absent
        output = data.get('output') or []
        texts = []
        for item in output:
            for part in (item.get('content') or []):
                txt = part.get('text')
                if txt:
                    texts.append(txt)
        return '\n'.join(texts).strip() or None

    @app.route('/api/ai/chat', methods=['POST'])
    def ai_chat():
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        q    = body.get('question', '').strip()
        sess = get_session(sid)

        if not q:
            return jsonify({'answer': 'Escribe una pregunta.'})

        try:
            answer = _openai_answer(q, sess)
            if not answer:
                answer = _ai_answer(q, sess)
            return jsonify({'answer': answer})
        except Exception as e:
            logger.exception('Error en OpenAI (%s), usando fallback local.', request.path)
            try:
                return jsonify({'answer': _ai_answer(q, sess)})
            except Exception:
                logger.exception('Error en %s', request.path)
                return jsonify({'error': str(e)}), 500

    def _ai_answer(q, sess):
        ql = q.lower()
        meta = sess.get('meta', {})
        qc   = sess.get('qc_report', {})
        estats = sess.get('energy_summary', {})

        # ── contextual responses based on loaded data ──
        has_data   = _analysis_df(sess) is not None
        has_energy = 'energy_summary' in sess

        if any(w in ql for w in ['datos', 'cargado', 'dataset', 'filas', 'columnas']):
            if has_data:
                n = meta.get('n_rows', '?')
                cols = meta.get('columns', [])
                dr = meta.get('date_range', ['?', '?'])
                return (f"Se han cargado {n:,} registros desde {dr[0]} hasta {dr[1]}. "
                        f"Columnas: {', '.join(cols[:8])}{'...' if len(cols) > 8 else ''}.")
            return "No hay una fuente eólica activa. Importa datos o selecciona un área con reanálisis en Globe Map."

        if any(w in ql for w in ['calidad', 'qc', 'faltantes', 'missing', 'outlier']):
            if qc:
                overall = qc.get('overall', '?')
                pct_miss = qc.get('pct_missing', '?')
                spikes = qc.get('n_spikes', 0)
                stats_d = qc.get('stats', {})
                return (f"Control de calidad: {overall}. "
                        f"Datos faltantes: {pct_miss}%. "
                        f"Picos detectados: {spikes}. "
                        f"Velocidad media: {stats_d.get('mean','?')} m/s, "
                        f"máx: {stats_d.get('max','?')} m/s.")
            return "Ejecuta el control de calidad en Data Hub primero."

        if any(w in ql for w in ['weibull', 'distribución', 'distribucion', 'k ', 'lambda']):
            wb = qc.get('weibull')
            if wb:
                return (f"Ajuste Weibull: k={wb['k']} (factor de forma), "
                        f"λ={wb['lambda']} m/s (factor de escala). "
                        f"{'k < 2 indica distribución sesgada hacia vientos fuertes (régimen tipo Tehuantepecano).' if wb['k'] < 2 else 'k ≥ 2 indica distribución más simétrica (régimen estable).'}")
            return "Ejecuta QC primero para obtener el ajuste Weibull."

        if any(w in ql for w in ['energía', 'energia', 'aep', 'potencia', 'factor de capacidad', 'cf']):
            if has_energy:
                cf = estats.get('capacity_factor_pct', '?')
                e = estats.get('energy_net_mwh', '?')
                flh = estats.get('full_load_hours', '?')
                return (f"Producción estimada: {e} MWh netos en el periodo analizado. "
                        f"Factor de capacidad: {cf}%. "
                        f"Horas equivalentes de plena carga: {flh}.")
            return "Configura y ejecuta la simulación en Energy Lab."

        if any(w in ql for w in ['merra', 'era5', 'reanálisis', 'reanalisis']):
            return ("Para cargar datos MERRA-2: exporta el dataset tabular desde GES DISC "
                    "y selecciona fuente 'MERRA-2' al subir. "
                    "Para ERA5: exporta desde Copernicus CDS en CSV y selecciona 'ERA5'. "
                    "AERO detecta automáticamente columnas U50M/V50M y deriva velocidad y dirección.")

        if any(w in ql for w in ['limitaciones', 'advertencia', 'incertidumbre', 'sesgo']):
            return ("Limitaciones declaradas: (1) Los modelos son válidos para el sitio y periodo entrenado. "
                    "(2) La curva de potencia asume condiciones de operación nominales. "
                    "(3) El AEP de largo plazo incluye incertidumbre inter-anual representada en P50/P75/P90.")

        # fallback
        context = f"Sesión activa: {'datos cargados' if has_data else 'sin datos'}. "
        context += f"{'Simulación energética disponible.' if has_energy else ''}"
        return (f"No encontré una respuesta específica para '{q}'. {context} "
                f"Temas disponibles: calidad de datos, Weibull, recurso eólico, "
                f"energía, factor de capacidad y evaluación tecnoeconómica.")

    # ─── MONTH × HOUR HEATMAP ─────────────────────────────────────────────────
    @app.route('/api/wind_stats/month_hour_heatmap', methods=['POST'])
    def month_hour_heatmap():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        col = body.get('col', 'wind_speed')
        sess = get_session(sid)
        df = _analysis_df(sess, body)
        if df is None:
            return jsonify({'error': 'No data loaded'}), 400
        hm = compute_month_hour_heatmap(df, col=col)
        if hm is None:
            return jsonify({'error': 'Column not found or not datetime-indexed'}), 400
        return jsonify(numpy_to_python(hm))

    # ─── KRIGING & MULTI-SITE ─────────────────────────────────────────────────
    @app.route('/api/kriging/sites/light', methods=['GET'])
    def kriging_list_sites_light():
        """Versión rápida: solo metadatos derivables del nombre del archivo.
        No abre los .txt ni calcula stats — sirve para pintar pines en el globo
        antes de que llegue la versión completa con Weibull/v_mean.
        """
        folder = Config.DATA_FOLDER
        if not os.path.isdir(folder):
            return jsonify({'error': f'DATA_FOLDER not found: {folder}'}), 404

        sites = []
        for fname in _cached_list_files(folder):
            if not fname.lower().endswith('.txt'):
                continue
            coords = parse_coords_from_filename(fname)
            if coords is None:
                continue
            lat, lon = coords
            info = get_site_info(fname)
            sites.append({
                'filename':     fname,
                'display_name': info['display_name'],
                'state':        info['state'],
                'municipality': info['municipality'],
                'lat':          float(lat),
                'lon':          float(lon),
                'source': ('merra2' if 'MERRA' in fname.upper()
                           else 'era5' if 'ERA5' in fname.upper()
                           else 'other'),
            })

        return jsonify({
            'folder': folder,
            'sites':  sites,
            'count':  len(sites),
            'light':  True,
        })

    @app.route('/api/kriging/sites', methods=['GET'])
    def kriging_list_sites():
        """Escanea la carpeta meteorologica y devuelve sitios con stats Weibull.
        OPTIMIZADO: cache mtime-aware + lectura en paralelo (ThreadPool).
        Acepta target_height_m para normalizar todas las estaciones a esa altura.
        """
        folder = Config.DATA_FOLDER
        sid = request.args.get('session_id')
        data_source = request.args.get('data_source')
        if sid and data_source and data_source.startswith('mcp:'):
            sess = get_session(sid)
            source = (sess.get('data_sources') or {}).get(data_source)
            if not source or source.get('kind') != 'mcp':
                return jsonify({'error': 'Diseño requiere un hindcast MCP publicado en esta sesión'}), 409
            study = sess.get('mcp_study') or {}
            try:
                poi_lat = float(request.args.get('poi_lat', (study.get('poi') or {}).get('lat', M04_LATITUDE)))
                poi_lon = float(request.args.get('poi_lon', (study.get('poi') or {}).get('lon', M04_LONGITUDE)))
                radius_km = float(request.args.get('radius_km', study.get('radius_km', 200)))
            except (TypeError, ValueError):
                return jsonify({'error': 'POI o radius_km inválido'}), 400
            index = _mcp_index(sess)
            references = []
            for row in index.values():
                if row.get('data_kind') != 'reanalysis' or row.get('provider') not in {'ERA5', 'MERRA2'}:
                    continue
                distance = _haversine_km(poi_lat, poi_lon, float(row['latitude']), float(row['longitude']))
                if distance > radius_km:
                    continue
                references.append({
                    'station_id': row['station_id'],
                    # filename remains the stable UI selection key for legacy JS.
                    'filename': row['station_id'],
                    'file_ids': list(row.get('file_ids') or []),
                    'primary_file_id': row.get('primary_file_id'),
                    'legacy_aliases': list(row.get('legacy_aliases') or []),
                    'display_name': row.get('display_name') or row['station_id'],
                    'lat': float(row['latitude']), 'lon': float(row['longitude']),
                    'distance_km': round(float(distance), 3),
                    'source': str(row['provider']).lower(), 'provider': row['provider'],
                    'data_kind': 'reanalysis', 'allowed_role': 'spatial_reference',
                    'source_height_m': float((row.get('measurement_heights') or [0])[0]),
                    'coordinate_source': row.get('coordinate_source'),
                    'coordinate_status': row.get('coordinate_status'),
                    'maximum_coordinate_discrepancy_km': row.get('maximum_coordinate_discrepancy_km'),
                    'alias_count': int(row.get('alias_count') or 1),
                    'qc_status': 'pending_calibration', 'calibration_method': SPATIAL_CALIBRATION_METHOD,
                })
            anchor = {
                'station_id': M04_STATION_ID, 'filename': M04_STATION_ID,
                'display_name': 'M04 CERTE · hindcast MCP',
                'lat': M04_LATITUDE, 'lon': M04_LONGITUDE,
                'distance_km': round(_haversine_km(poi_lat, poi_lon, M04_LATITUDE, M04_LONGITUDE), 3),
                'source': 'mcp', 'provider': 'MCP', 'data_kind': 'spatial_anchor',
                'allowed_role': 'anchor', 'target_height_m': source.get('target_height_m'),
                'n_hours': int(source.get('rows') or len(source.get('df', []))),
                'v_mean': float(pd.to_numeric(source['df']['wind_speed'], errors='coerce').mean()),
                'qc_status': 'published_hindcast', 'selectable': False,
            }
            references.sort(key=lambda row: (row['provider'], row['distance_km'], row['station_id']))
            return jsonify(numpy_to_python({
                'sites': [anchor] + references, 'count': 1 + len(references),
                'anchor': anchor, 'reference_count': len(references),
                'poi': {'lat': poi_lat, 'lon': poi_lon}, 'radius_km': radius_km,
                'data_source': data_source, 'study_revision': source.get('study_revision'),
                'selection_namespace': 'KRIG.selected', 'mcp_selection_independent': True,
            }))
        year = request.args.get('year', type=int)
        target_h = request.args.get('target_height_m', type=float)
        if not os.path.isdir(folder):
            return jsonify({'error': f'DATA_FOLDER not found: {folder}'}), 404

        # 1. Listar y filtrar nombres validos (sin IO)
        candidate_paths = []
        skipped_names   = []
        for fname in _cached_list_files(folder):
            if not fname.lower().endswith('.txt'):
                continue
            if parse_coords_from_filename(fname) is None:
                skipped_names.append(fname)
                continue
            candidate_paths.append(os.path.join(folder, fname))

        # 2. Leer en paralelo con cache (hub_height_m = reference height si se proveyó)
        stat_list = parallel_read_sites(candidate_paths, year=year, hub_height_m=target_h)

        sites   = []
        skipped = list(skipped_names)
        for path, stats in zip(candidate_paths, stat_list):
            fname = os.path.basename(path)
            if stats is None or stats['_n'] < 24:
                skipped.append(fname)
                continue
            krig_info = get_site_info(fname)
            sites.append({
                'filename':     fname,
                'display_name': krig_info['display_name'],
                'state':        krig_info['state'],
                'municipality': krig_info['municipality'],
                'lat':          stats['_lat'],
                'lon':          stats['_lon'],
                'n_hours':      int(stats['_n']),
                'v_mean':       round(stats['v_mean'],     3),
                'v_mean_raw':   round(stats['v_mean_raw'], 3),
                'v_max':        round(stats['v_max'],      2),
                'gust_ms':      round(stats['gust_ms'],    2),
                'power_density_wm2': round(stats['power_density_wm2'], 2),
                'weibull_C':    round(stats['weibull_C'],  3),
                'weibull_K':    round(stats['weibull_K'],  3),
                'rho_mean':     round(stats['rho_mean'],   4),
                'source': ('merra2' if 'MERRA' in fname.upper()
                           else 'era5' if 'ERA5' in fname.upper()
                           else 'other'),
            })

        return jsonify(numpy_to_python({
            'folder':  folder,
            'year':    year,
            'sites':   sites,
            'count':   len(sites),
            'skipped': len(skipped),
        }))

    def _calibration_for_session(sess, calibration_id):
        record = (sess.get('kriging_calibrations') or {}).get(str(calibration_id))
        if not record:
            return None, None
        path = Path(str(record.get('result_path', ''))).resolve()
        root = (Path(Config.KRIGING_RUNS_FOLDER) / str(calibration_id)).resolve()
        if root not in path.parents or not path.is_file():
            return record, None
        return record, json.loads(path.read_text(encoding='utf-8'))

    @app.route('/api/kriging/calibrations', methods=['POST'])
    def kriging_start_calibration():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        sess = get_session(sid)
        data_source = str(body.get('data_source') or sess.get('active_data_source') or '')
        source = (sess.get('data_sources') or {}).get(data_source)
        if not data_source.startswith('mcp:') or not source or source.get('kind') != 'mcp':
            return _err('El flujo guiado de Diseño requiere publicar primero una corrida MCP', 409)
        method = str(body.get('method') or SPATIAL_CALIBRATION_METHOD)
        if method != SPATIAL_CALIBRATION_METHOD:
            return _err('Método de calibración espacial no soportado')
        study = sess.get('mcp_study') or {}
        if source.get('study_revision') != study.get('study_revision'):
            return _err('La fuente MCP pertenece a una revisión anterior del estudio; publícala de nuevo', 409)
        poi = body.get('poi') or study.get('poi') or {'lat': M04_LATITUDE, 'lon': M04_LONGITUDE}
        try:
            poi = {'lat': float(poi['lat']), 'lon': float(poi['lon'])}
            radius_km = float(body.get('radius_km', study.get('radius_km', 200)))
            target_height_m = float(body.get('target_height_m', source.get('target_height_m') or 80))
        except (KeyError, TypeError, ValueError):
            return _err('POI, radius_km o target_height_m inválido')
        if radius_km <= 0 or not 10 <= target_height_m <= 300:
            return _err('Radio o altura objetivo fuera de rango')
        excluded_ids = {str(value) for value in body.get('excluded_station_ids', [])}
        references = []
        for row in _mcp_index(sess).values():
            if row.get('station_id') in excluded_ids:
                continue
            if row.get('data_kind') != 'reanalysis' or row.get('provider') not in {'ERA5', 'MERRA2'}:
                continue
            if _haversine_km(poi['lat'], poi['lon'], float(row['latitude']), float(row['longitude'])) <= radius_km:
                references.append(row)
        provider_counts = {
            provider: sum(row.get('provider') == provider for row in references)
            for provider in ('ERA5', 'MERRA2')
        }
        if max(provider_counts.values(), default=0) < 3:
            return _err('Se requieren al menos tres nodos válidos de un mismo proveedor dentro del radio')
        calibration_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:8]
        run_dir = Path(Config.KRIGING_RUNS_FOLDER) / calibration_id
        task_id = str(uuid.uuid4())
        task = {
            'status': 'queued', 'progress': 0, 'message': 'Calibración espacial en cola',
            'result': None, 'calibration_id': calibration_id, 'session_id': sid,
            '_last_access': time.time(),
        }
        tasks[task_id] = task
        anchor_frame = source['df'].copy()
        observed_record = (sess.get('data_sources') or {}).get('observed')
        observed_frame = observed_record.get('df').copy() if observed_record and observed_record.get('df') is not None else None
        study_revision = source.get('study_revision')
        resolve_roughness = bool(body.get('resolve_roughness', True))

        def _run_spatial_calibration():
            try:
                run_dir.mkdir(parents=True, exist_ok=True)
                task.update(status='running', progress=4, message='Preparando hindcast MCP y nodos espaciales')

                def cb(progress, message):
                    task.update(progress=int(progress), message=str(message), _last_access=time.time())

                calibration = calibrate_spatial_nodes(
                    # Spatial anomalies are always evaluated at the physical
                    # M04 anchor, even when the design POI/radius is elsewhere.
                    anchor_frame, observed_frame, references,
                    poi={'lat': M04_LATITUDE, 'lon': M04_LONGITUDE},
                    target_height_m=target_height_m, train_year=2018, validation_year=2019,
                    roughness_resolver=resolve_sector_roughness if resolve_roughness else None,
                    progress_callback=cb,
                )
                calibration.update({
                    'calibration_id': calibration_id, 'session_id': sid,
                    'data_source': data_source, 'run_id': source.get('run_id'),
                    'study_revision': study_revision, 'poi': poi, 'radius_km': radius_km,
                    'excluded_station_ids': sorted(excluded_ids),
                    'created_at': datetime.now(timezone.utc).isoformat(),
                })
                nodes_path = run_dir / 'calibrated_nodes.csv'
                pd.DataFrame([
                    {key: value for key, value in node.items() if key not in {'sectorial', 'roughness'}}
                    for node in calibration.get('nodes', [])
                ]).to_csv(nodes_path, index=False)
                calibration['artifacts'] = {'calibrated_nodes_csv': str(nodes_path.resolve())}
                result_path = run_dir / 'calibration.json'
                result_path.write_text(json.dumps(numpy_to_python(calibration), ensure_ascii=False, indent=2), encoding='utf-8')
                record = {
                    'calibration_id': calibration_id, 'data_source': data_source,
                    'study_revision': study_revision, 'method': SPATIAL_CALIBRATION_METHOD,
                    'result_path': str(result_path.resolve()), 'created_at': calibration['created_at'],
                    'status': 'done', 'provider_weights': calibration.get('provider_weights'),
                    'common_period': calibration.get('common_period'),
                }
                sess.setdefault('kriging_calibrations', {})[calibration_id] = record
                sess['active_kriging_calibration_id'] = calibration_id
                task.update(
                    status='done', progress=100, message='Calibración espacial lista',
                    result={
                        'calibration_id': calibration_id,
                        'data_source': data_source,
                        'study_revision': study_revision,
                        'provider_weights': calibration.get('provider_weights'),
                        'provider_counts': calibration.get('provider_counts'),
                        'common_period': calibration.get('common_period'),
                        'qc': calibration.get('qc'),
                    },
                )
            except Exception as error:
                task.update(status='error', message=str(error), _last_access=time.time())
                logger.exception('Calibración espacial fallida')

        _executor.submit(_run_spatial_calibration)
        return jsonify({'task_id': task_id, 'calibration_id': calibration_id}), 202

    @app.route('/api/kriging/calibrations/<calibration_id>', methods=['GET'])
    def kriging_get_calibration(calibration_id):
        sid = request.args.get('session_id')
        if not sid:
            return _err('session_id requerido')
        record, calibration = _calibration_for_session(get_session(sid), calibration_id)
        if record is None:
            return jsonify({'error': 'Calibración no encontrada'}), 404
        if calibration is None:
            return jsonify({'error': 'Artefacto de calibración no disponible'}), 410
        return jsonify(numpy_to_python(calibration))

    @app.route('/api/kriging/grid', methods=['POST'])
    def kriging_compute_grid():
        """Interpolate a metric across the sites using Ordinary Kriging."""
        body = request.get_json(silent=True) or {}
        sites = body.get('sites') or []
        metric = body.get('metric', 'v_mean')
        n = int(body.get('n', 60))
        model = body.get('model', 'exponential')

        if len(sites) < 3:
            return jsonify({'error': 'Need at least 3 sites for Kriging'}), 400

        try:
            lats = [float(s['lat']) for s in sites]
            lons = [float(s['lon']) for s in sites]
            vals = [float(s[metric]) for s in sites]
        except (KeyError, TypeError, ValueError) as e:
            return jsonify({'error': f'Invalid site data: {e}'}), 400

        try:
            grid = kriging_grid(lons, lats, vals, n=n, model=model)
            grid['metric'] = metric
            grid['n_sites'] = len(sites)
            return jsonify(grid)
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/terrain/enrich', methods=['POST'])
    def terrain_enrich():
        """Enriquece una lista de sitios con altitud (DEM) y clase WorldCover → z₀.

        Body: {"sites": [{"filename":...,"lat":...,"lon":...}, ...]}
        Devuelve los mismos sitios con campos adicionales:
            elevation_m, landcover_class, landcover_label, landcover_purity, z0
        """
        body = request.get_json(silent=True) or {}
        sites = body.get('sites') or []
        if not isinstance(sites, list) or not sites:
            return jsonify({'error': 'sites required'}), 400
        try:
            from core.terrain import enrich_sites
            enriched = enrich_sites(sites)
            return jsonify({'sites': enriched, 'count': len(enriched)})
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    def _required_design_economics(body):
        """Read user-defined economics without inventing project defaults."""
        if body.get('price_usd_kwh') in (None, ''):
            raise ValueError('El precio de energía debe venir de la configuración del proyecto')
        if body.get('project_years') in (None, ''):
            raise ValueError('La vida del proyecto debe venir de la configuración del proyecto')
        price = float(body['price_usd_kwh'])
        years = int(body['project_years'])
        if not np.isfinite(price) or price < 0:
            raise ValueError('El precio de energía debe ser un número no negativo')
        if not 5 <= years <= 40:
            raise ValueError('La vida del proyecto debe estar entre 5 y 40 años')
        return price, years

    @app.route('/api/kriging/legacy_maps', methods=['POST'])
    def kriging_legacy_maps():
        """
        Replica exacta del script ajuste_aerogeneradores.py:
        lee todos los sitios, calcula técnica-económica con el último
        aerogenerador del catálogo y genera 3 grillas Kriging
        (viento, inversión, VAN).
        """
        body = request.get_json(silent=True) or {}
        try:
            price_usd_kwh, project_years = _required_design_economics(body)
        except (TypeError, ValueError) as exc:
            return _err(str(exc))
        source_info = None
        if body.get('session_id'):
            source_info = _analysis_source_info(
                get_session(str(body['session_id'])), body)
        calibration_payload = None
        if body.get('calibration_id'):
            sid = body.get('session_id')
            if not sid:
                return _err('session_id requerido para usar una calibración')
            sess = get_session(str(sid))
            record, calibration_payload = _calibration_for_session(sess, body['calibration_id'])
            if record is None or calibration_payload is None:
                return _err('Calibración espacial no encontrada', 404)
            requested_source = body.get('data_source') or sess.get('active_data_source')
            if requested_source != record.get('data_source'):
                return _err('La calibración no pertenece a la fuente MCP solicitada', 409)
        task_id = str(uuid.uuid4())
        tasks[task_id] = {'status': 'running', 'progress': 0,
                          'message': 'Procesando sitios…', 'result': None}

        def _run():
            try:
                # Optional: resolve center from a filename if provided
                cen_lat = body.get('center_lat')
                cen_lon = body.get('center_lon')
                if cen_lat is None and body.get('center_file'):
                    coords = parse_coords_from_filename(body['center_file'])
                    if coords:
                        cen_lat, cen_lon = coords
                grid_bounds = body.get('grid_bounds') or None
                if grid_bounds:
                    lat_a = float(grid_bounds['lat_min'])
                    lat_b = float(grid_bounds['lat_max'])
                    lon_a = float(grid_bounds['lon_min'])
                    lon_b = float(grid_bounds['lon_max'])
                    grid_bounds = {
                        'lat_min': min(lat_a, lat_b),
                        'lat_max': max(lat_a, lat_b),
                        'lon_min': min(lon_a, lon_b),
                        'lon_max': max(lon_a, lon_b),
                    }
                def cb(pct, msg):
                    tasks[task_id]['progress'] = pct
                    tasks[task_id]['message'] = msg
                if calibration_payload is not None:
                    result = build_calibrated_maps(
                        calibration_payload,
                        turbines_folder=Config.TURBINES_FOLDER,
                        n_grid=int(body.get('n_grid', 80)),
                        model=body.get('model', 'auto'),
                        bounds=grid_bounds,
                        target_height_m=float(body.get('target_height_m') or calibration_payload['anchor']['target_height_m']),
                        physical=bool(body.get('physical', True)),
                        demand_mwh=float(body.get('demand_mwh', 0)),
                        discount_rate=float(body.get('tasa', 0.08)),
                        price_usd_kwh=price_usd_kwh,
                        fx_mxn_per_usd=float(body.get('price_mxn_per_usd', 20)),
                        project_years=project_years,
                        cost_model=(CostModel.from_dict(body['cost_model'])
                                    if body.get('cost_model')
                                    else CostModel.from_design_benchmark()),
                        orographic_speedup_grid=body.get('orographic_speedup_grid'),
                        progress_callback=cb,
                    )
                else:
                    # Compatibility path for manual/legacy clients.
                    result = legacy_three_maps(
                        wind_folder=Config.DATA_FOLDER,
                        turbines_folder=Config.TURBINES_FOLDER,
                        year=int(body.get('year', 2018)),
                        demand_mwh=float(body.get('demand_mwh', 1000)),
                        tasa=float(body.get('tasa', 0.08)),
                        price_usd_kwh=price_usd_kwh,
                        price_mxn_per_usd=float(body.get('price_mxn_per_usd', 20)),
                        anios=project_years,
                        max_files=body.get('max_files'),
                        n_grid=int(body.get('n_grid', 80)),
                        model=body.get('model', 'exponential'),
                        center_lat=float(cen_lat) if cen_lat is not None else None,
                        center_lon=float(cen_lon) if cen_lon is not None else None,
                        radius_km=float(body['radius_km']) if body.get('radius_km') else None,
                        filter_filenames=body.get('filenames') or None,
                        physical=bool(body.get('physical', False)),
                        grid_bounds=grid_bounds,
                        cost_model=(CostModel.from_dict(body['cost_model'])
                                    if body.get('cost_model')
                                    else CostModel.from_design_benchmark()),
                        z_tgt=float(body['target_height_m']) if body.get('target_height_m') else None,
                        include_sectorial=bool(body.get('include_sectorial', body.get('physical', False))),
                        include_physical_economics=bool(body.get('include_physical_economics', body.get('physical', False))),
                        progress_callback=cb,
                    )
                result['map_method'] = (
                    'mcp_advanced' if calibration_payload is not None
                    else str(body.get('map_method') or 'windpro_classic')
                )
                if source_info is not None:
                    result['data_source'] = source_info
                if body.get('market_trajectories'):
                    result['market_configuration'] = {
                        'trajectories': body.get('market_trajectories'),
                        'equivalent_inputs': {
                            'demand_mwh': float(body.get('demand_mwh', 0)),
                            'discount_rate': float(body.get('tasa', 0.08)),
                            'price_usd_kwh': price_usd_kwh,
                            'project_years': project_years,
                            'fx_mxn_per_usd': float(body.get('price_mxn_per_usd', 20)),
                        },
                    }
                tasks[task_id]['result'] = numpy_to_python(result)
                tasks[task_id]['status'] = 'done'
                tasks[task_id]['progress'] = 100
                n_maps = sum(
                    1 for v in result.values()
                    if isinstance(v, dict) and 'z' in v
                )
                tasks[task_id]['message'] = f'{result["n_sites"]} sitios · {n_maps} mapas listos'
            except Exception as e:
                tasks[task_id]['status'] = 'error'
                tasks[task_id]['message'] = str(e)
                logger.exception('Task fallida')

        _executor.submit(_run)
        return jsonify({'task_id': task_id})

    @app.route('/api/kriging/analyze', methods=['POST'])
    @limiter.limit('5 per minute')
    def kriging_full_analysis():
        """Full multi-site × multi-turbine analysis (heavy). Runs async."""
        body = request.get_json(silent=True) or {}
        try:
            price_usd_kwh, project_years = _required_design_economics(body)
        except (TypeError, ValueError) as exc:
            return _err(str(exc))
        year = int(body.get('year', 2018))
        demand_mwh = float(body.get('demand_mwh', 1000))
        tasa = float(body.get('tasa', 0.08))
        price_mxn_per_usd = float(body.get('price_mxn_per_usd', 20))
        max_files = body.get('max_files')
        filter_files = body.get('filenames')
        turbine_files = body.get('turbine_filenames') or body.get('turbines') or None
        area_bounds = body.get('area_bounds') or body.get('grid_bounds') or None
        if area_bounds:
            lat_a = float(area_bounds['lat_min'])
            lat_b = float(area_bounds['lat_max'])
            lon_a = float(area_bounds['lon_min'])
            lon_b = float(area_bounds['lon_max'])
            area_bounds = {
                'lat_min': min(lat_a, lat_b),
                'lat_max': max(lat_a, lat_b),
                'lon_min': min(lon_a, lon_b),
                'lon_max': max(lon_a, lon_b),
            }
        task_id = str(uuid.uuid4())
        tasks[task_id] = {'status': 'running', 'progress': 0,
                          'message': 'Iniciando análisis multi-sitio…', 'result': None}

        cost_model_payload = body.get('cost_model')

        def _run():
            try:
                results = analyze_sites(
                    wind_folder=Config.DATA_FOLDER,
                    turbines_folder=Config.TURBINES_FOLDER,
                    year=year, demand_mwh=demand_mwh, tasa=tasa,
                    price_usd_kwh=price_usd_kwh,
                    price_mxn_per_usd=price_mxn_per_usd,
                    anios=project_years,
                    max_files=max_files, filter_filenames=filter_files,
                    bounds=area_bounds,
                    turbine_filenames=turbine_files,
                    cost_model=(CostModel.from_dict(cost_model_payload)
                                if cost_model_payload
                                else CostModel.from_design_benchmark()),
                )
                tasks[task_id]['result'] = numpy_to_python(results)
                tasks[task_id]['status'] = 'done'
                tasks[task_id]['progress'] = 100
                tasks[task_id]['message'] = f'{len(results)} sitios analizados'
            except Exception as e:
                tasks[task_id]['status'] = 'error'
                tasks[task_id]['message'] = str(e)
                logger.exception('Task fallida')

        _executor.submit(_run)
        return jsonify({'task_id': task_id})

    # ─── MULTI-CRITERIA (TOPSIS) ──────────────────────────────────────────────
    @app.route('/api/multicriteria/defaults', methods=['GET'])
    def mcda_defaults():
        return jsonify({
            'weights':    DEFAULT_WEIGHTS,
            'labels':     CRITERION_LABELS,
            'directions': CRITERION_DIRECTION,
            'layout_weights': LAYOUT_WEIGHTS,
            'layout_labels': LAYOUT_LABELS,
            'layout_directions': LAYOUT_DIRECTION,
            'design_cost_benchmark': get_design_cost_benchmark(),
            'layout_scenarios': {
                'base': {
                    'label': 'Escenario base científico',
                    'profile': 'balanced_integral_v1',
                    'weights': LAYOUT_WEIGHTS,
                },
                'unistmo': {
                    'label': 'Escenario UNISTMO',
                    'profile': 'unistmo_v1',
                    'weights': UNISTMO_LAYOUT_WEIGHTS,
                },
            },
        })

    @app.route('/api/multicriteria/rank', methods=['POST'])
    def mcda_rank():
        """
        Rank alternatives via TOPSIS. Accepts either:
          - 'alternatives': flat list of dicts (each with all criterion fields), or
          - 'sites_analysis': output of /api/kriging/analyze (nested), with 'mode'.
        """
        body = request.get_json(silent=True) or {}
        weights = body.get('weights')
        mode = body.get('mode', 'pairs')
        profile = body.get('profile', 'balanced_integral_v1')

        if 'alternatives' in body:
            if not isinstance(body['alternatives'], list):
                return jsonify({'error': 'alternatives must be a list'}), 400
            result = (rank_layout_alternatives(body['alternatives'], weights=weights,
                                               profile=profile)
                      if mode == 'layout_alternatives'
                      else topsis(body['alternatives'], weights=weights))
        elif 'sites_analysis' in body:
            if not isinstance(body['sites_analysis'], list):
                return jsonify({'error': 'sites_analysis must be a completed analysis list'}), 400
            result = rank_sites_turbines(body['sites_analysis'],
                                         weights=weights, mode=mode)
        elif 'task_id' in body:
            # pull results from a previous /api/kriging/analyze task
            tid = body['task_id']
            if tid not in tasks or tasks[tid].get('status') != 'done':
                return jsonify({'error': 'Task not found or not complete'}), 404
            result = rank_sites_turbines(tasks[tid]['result'] or [],
                                         weights=weights, mode=mode)
        else:
            return jsonify({'error': 'Provide alternatives, sites_analysis, or task_id'}), 400

        return jsonify(numpy_to_python(result))

    @app.route('/api/multicriteria/spatial', methods=['POST'])
    @limiter.limit('5 per minute')
    def mcda_spatial():
        """
        TOPSIS over virtual kriging coordinates.

        Stations are used only to build the geospatial field. The alternatives
        are sampled inside the drawn park polygon (scope=park) or the selected
        exploratory rectangle (scope=exploratory).
        """
        body = request.get_json(silent=True) or {}
        try:
            price_usd_kwh, project_years = _required_design_economics(body)
        except (TypeError, ValueError) as exc:
            return _err(str(exc))
        source_info = None
        if body.get('session_id'):
            source_info = _analysis_source_info(
                get_session(str(body['session_id'])), body)
        scope = body.get('scope', 'park')
        polygon = body.get('polygon') or None
        bounds = body.get('area_bounds') or body.get('bounds') or None
        if bounds:
            lat_a = float(bounds['lat_min'])
            lat_b = float(bounds['lat_max'])
            lon_a = float(bounds['lon_min'])
            lon_b = float(bounds['lon_max'])
            bounds = {
                'lat_min': min(lat_a, lat_b),
                'lat_max': max(lat_a, lat_b),
                'lon_min': min(lon_a, lon_b),
                'lon_max': max(lon_a, lon_b),
            }
        weights = body.get('weights')
        if (body.get('unistmo_layout_weights') is not None
                and not isinstance(body.get('unistmo_layout_weights'), dict)):
            return _err('unistmo_layout_weights debe ser un objeto de pesos')
        mode = body.get('mode') or ('area_turbines' if scope == 'park' else 'pairs')
        year = int(body.get('year', 2018))
        demand_mwh = float(body.get('demand_mwh', 1000))
        tasa = float(body.get('tasa', 0.08))
        price_mxn_per_usd = float(body.get('price_mxn_per_usd', 20))
        n_grid = int(body.get('n_grid', 80))
        model = body.get('model', 'exponential')
        max_points = int(body.get('max_points') or (48 if scope == 'park' else 60))
        filter_files = body.get('filenames')
        turbine_files = body.get('turbine_filenames') or body.get('turbines') or None
        calibration_payload = None
        calibration_record = None
        if body.get('calibration_id'):
            sid = body.get('session_id')
            if not sid:
                return _err('session_id requerido para el análisis MCP calibrado')
            sess = get_session(str(sid))
            calibration_record, calibration_payload = _calibration_for_session(
                sess, body['calibration_id']
            )
            if calibration_record is None or calibration_payload is None:
                return _err('Calibración espacial no encontrada', 404)
            requested_source = body.get('data_source') or sess.get('active_data_source')
            if requested_source != calibration_record.get('data_source'):
                return _err('La calibración no pertenece a la fuente MCP solicitada', 409)

        task_id = str(uuid.uuid4())
        tasks[task_id] = {'status': 'running', 'progress': 0,
                          'message': 'Construyendo análisis TOPSIS espacial…',
                          'result': None}

        def _run():
            try:
                requested_cost_model = body.get('cost_model')
                cost_model = (CostModel.from_dict(requested_cost_model)
                              if requested_cost_model
                              else CostModel.from_design_benchmark())
                if calibration_payload is not None:
                    tasks[task_id]['progress'] = 10
                    tasks[task_id]['message'] = 'Construyendo campos MCP calibrados para TOPSIS…'
                    if scope == 'park':
                        field_bounds = {
                            'lat_min': min(float(point['lat']) for point in polygon),
                            'lat_max': max(float(point['lat']) for point in polygon),
                            'lon_min': min(float(point['lon']) for point in polygon),
                            'lon_max': max(float(point['lon']) for point in polygon),
                        }
                    else:
                        field_bounds = bounds

                    def calibration_progress(percent, message):
                        tasks[task_id]['progress'] = min(70, 10 + int(float(percent) * 0.6))
                        tasks[task_id]['message'] = message

                    field_bundle = build_calibrated_maps(
                        calibration_payload,
                        turbines_folder=Config.TURBINES_FOLDER,
                        n_grid=n_grid, model=model, bounds=field_bounds,
                        target_height_m=float(
                            body.get('target_height_m')
                            or calibration_payload['anchor']['target_height_m']
                        ),
                        physical=bool(body.get('physical', True)),
                        demand_mwh=demand_mwh, discount_rate=tasa,
                        price_usd_kwh=price_usd_kwh,
                        fx_mxn_per_usd=price_mxn_per_usd,
                        project_years=project_years,
                        cost_model=cost_model,
                        orographic_speedup_grid=body.get('orographic_speedup_grid'),
                        progress_callback=calibration_progress,
                    )
                    tasks[task_id]['progress'] = 75
                    tasks[task_id]['message'] = 'Evaluando sitios virtuales sobre el campo MCP…'
                    spatial = analyze_spatial_topsis_maps(
                        field_bundle=field_bundle,
                        turbines_folder=Config.TURBINES_FOLDER,
                        scope=scope, polygon=polygon, bounds=bounds,
                        demand_mwh=demand_mwh, tasa=tasa,
                        price_usd_kwh=price_usd_kwh,
                        price_mxn_per_usd=price_mxn_per_usd,
                        anios=project_years,
                        max_points=max_points,
                        turbine_filenames=turbine_files,
                        cost_model=cost_model,
                    )
                else:
                    # Compatibility path for manual/legacy clients.
                    spatial = analyze_spatial_topsis_sites(
                        wind_folder=Config.DATA_FOLDER,
                        turbines_folder=Config.TURBINES_FOLDER,
                        year=year,
                        scope=scope,
                        polygon=polygon,
                        bounds=bounds,
                        demand_mwh=demand_mwh,
                        tasa=tasa,
                        price_usd_kwh=price_usd_kwh,
                        price_mxn_per_usd=price_mxn_per_usd,
                        anios=project_years,
                        n_grid=n_grid,
                        model=model,
                        max_points=max_points,
                        filter_filenames=filter_files,
                        turbine_filenames=turbine_files,
                        cost_model=cost_model,
                    )
                if scope == 'park':
                    tasks[task_id]['progress'] = 82
                    tasks[task_id]['message'] = 'Optimizando cantidades, separaciones y estelas del parque…'
                    layout_result = analyze_park_layouts(
                        sites=spatial['sites'], polygon=polygon,
                        turbines_folder=Config.TURBINES_FOLDER,
                        turbine_filenames=turbine_files or [],
                        wind_grid=(field_bundle.get('wind') if calibration_payload is not None else None),
                        exclusions=body.get('exclusion_polygons') or [],
                        boundary_setback_m=float(body.get('boundary_setback_m') or 0.0),
                        min_spacing_d=float(body.get('min_spacing_d') or 3.0),
                        s_para_range=tuple(body.get('s_para_range') or (3, 4, 5, 6, 7, 8, 9, 10)),
                        s_perp_range=tuple(body.get('s_perp_range') or (3, 4, 5, 6)),
                        max_wake_loss_pct=float(body.get('max_wake_loss_pct', 5.0)),
                        wake_model=body.get('wake_model') or 'gaussian',
                        k_wake=float(body.get('k_wake') or 0.04),
                        ct=float(body.get('ct') or 0.8),
                        availability=float(body.get('availability') or 0.97),
                        electrical_losses=float(body.get('electrical_losses') or 0.03),
                        curtailment_loss=float(body.get('curtailment_loss') or 0.0),
                        environmental_loss=float(body.get('environmental_loss') or 0.0),
                        discount_rate=tasa,
                        project_years=project_years,
                        price_usd_kwh=price_usd_kwh,
                        demand_mwh=demand_mwh,
                        commercialization_mode=body.get('commercialization_mode') or 'total_sale',
                        grid_limit_mw=(float(body['grid_limit_mw']) if body.get('grid_limit_mw') else None),
                        target_height_m=(float(body['target_height_m']) if body.get('target_height_m') else None),
                        cost_model=cost_model,
                        cost_model_origin=(
                            'request' if requested_cost_model
                            else 'irena_onshore_2024_global_weighted_average'),
                        uncertainty_components_pct=body.get('uncertainty_components_pct'),
                        weights=body.get('layout_weights'),
                        unistmo_weights=body.get('unistmo_layout_weights'),
                    )
                    ranking = layout_result['ranking']
                    ranking['mode'] = 'layout_alternatives'
                else:
                    layout_result = None
                    ranking = rank_sites_turbines(spatial['sites'], weights=weights, mode=mode)
                    ranking['mode'] = mode
                tasks[task_id]['result'] = numpy_to_python({
                    **spatial,
                    **({'data_source': source_info} if source_info is not None else {}),
                    'ranking': ranking,
                    **(layout_result or {}),
                    'design_cost_benchmark': get_design_cost_benchmark(),
                    'market_configuration': {
                        'trajectories': body.get('market_trajectories') or {},
                        'equivalent_inputs': {
                            'demand_mwh': demand_mwh,
                            'discount_rate': tasa,
                            'price_usd_kwh': price_usd_kwh,
                            'fx_mxn_per_usd': price_mxn_per_usd,
                        },
                    },
                })
                tasks[task_id]['status'] = 'done'
                tasks[task_id]['progress'] = 100
                tasks[task_id]['message'] = (
                    f"{spatial['n_virtual_sites']} sitios virtuales · "
                    f"{len(ranking.get('ranking') or [])} alternativas TOPSIS"
                )
            except Exception as e:
                tasks[task_id]['status'] = 'error'
                tasks[task_id]['message'] = str(e)
                logger.exception('Task TOPSIS espacial fallida')

        _executor.submit(_run)
        return jsonify({'task_id': task_id})

    @app.route('/api/kriging/status/<task_id>', methods=['GET'])
    def kriging_task_status(task_id):
        if task_id not in tasks:
            return jsonify({'error': 'Unknown task'}), 404
        t = tasks[task_id]
        return jsonify(numpy_to_python({
            'status':   t['status'],
            'progress': t['progress'],
            'message':  t['message'],
            'result':   t.get('result') if t['status'] == 'done' else None,
        }))

    # ─── MARKET DATA HUB — LISTAR / CARGAR / SUBIR ─────────────────────────

    MARKET_CATEGORIES = {
        'demand':   {'label': 'Demanda Eléctrica',   'unit': 'MW',    'keywords': ['demanda', 'load', 'carga']},
        'pml':      {'label': 'Precio Marginal Local','unit': '$/kWh', 'keywords': ['pml', 'precio', 'price']},
        'interest': {'label': 'Tasa de Interés',     'unit': '%',     'keywords': ['tiie', 'interes', 'interest', 'rate']},
        'fx':       {'label': 'Tipo de Cambio',      'unit': 'MXN',   'keywords': ['usdmxn', 'cambio', 'exchange', 'fx']},
    }

    def _detect_market_category(filename):
        """Detecta la categoría de un archivo de mercado por nombre."""
        fn = filename.lower()
        for cat, info in MARKET_CATEGORIES.items():
            for kw in info['keywords']:
                if kw in fn:
                    return cat
        return 'other'

    def _load_market_file(fpath, category):
        """Carga un archivo de mercado y devuelve (values_dict, meta).
        Para PML devuelve {'compra': [...], 'venta': [...]}.
        Para otros devuelve {'values': [...]}.
        """
        fname = os.path.basename(fpath)
        cat_info = MARKET_CATEGORIES.get(category, {})

        # Intentar TSV con header (formato TIIE/USDMXN)
        try:
            df = pd.read_csv(fpath, sep='\t')
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                col_name = df.columns[0]
                values = df[col_name].dropna().values.astype(float)
                meta = {
                    'format': 'tsv_dated',
                    'filename': fname,
                    'category': category,
                    'unit': cat_info.get('unit', ''),
                    'label': cat_info.get('label', fname),
                    'n_rows': len(values),
                    'date_range': [str(df.index.min()), str(df.index.max())],
                    'column': col_name,
                }
                return {'values': values.tolist(), 'dates': [str(d) for d in df.index]}, meta
        except Exception:
            pass

        # Intentar PML (dos columnas: compra, venta)
        if category == 'pml':
            try:
                data = read_pml(fpath)
                meta = {
                    'format': 'pml_two_col',
                    'filename': fname,
                    'category': 'pml',
                    'unit': '$/kWh',
                    'label': 'PML (compra/venta)',
                    'n_rows': len(data['compra']),
                }
                return {'compra': data['compra'].tolist(),
                        'venta': data['venta'].tolist()}, meta
            except Exception:
                pass

        # Fallback: una columna por línea
        values = read_one_column(fpath)
        meta = {
            'format': 'single_column',
            'filename': fname,
            'category': category,
            'unit': cat_info.get('unit', ''),
            'label': cat_info.get('label', fname),
            'n_rows': len(values),
        }
        return {'values': values.tolist()}, meta

    @app.route('/api/market/list_files', methods=['GET'])
    def market_list_files():
        """Lista todos los archivos en MARKET_DATA_FOLDER, agrupados por categoría."""
        folder = Config.MARKET_DATA_FOLDER
        if not os.path.isdir(folder):
            return jsonify({'files': [], 'folder': folder,
                            'error': 'MARKET_DATA_FOLDER not found'})
        files = []
        for fname in sorted(os.listdir(folder)):
            if fname.startswith('.') or fname.startswith('~'):
                continue
            ext = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
            if ext not in ('txt', 'csv', 'tsv', 'dat', 'xlsx'):
                continue
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath):
                continue
            size_kb = round(os.path.getsize(fpath) / 1024, 1)
            category = _detect_market_category(fname)
            cat_info = MARKET_CATEGORIES.get(category, {})
            files.append({
                'filename':  fname,
                'size_kb':   size_kb,
                'category':  category,
                'label':     cat_info.get('label', fname),
                'unit':      cat_info.get('unit', ''),
            })
        # Agrupar
        grouped = {cat: [] for cat in MARKET_CATEGORIES}
        grouped['other'] = []
        for f in files:
            grouped.setdefault(f['category'], []).append(f)
        return jsonify({'files': files, 'grouped': grouped,
                        'folder': folder, 'count': len(files)})

    @app.route('/api/market/load_file', methods=['POST'])
    def market_load_file():
        """Carga un archivo de mercado desde MARKET_DATA_FOLDER y lo almacena en sesión."""
        body = request.get_json(silent=True) or {}
        sid      = _resolve_sid_or_new(body)
        filename = body.get('filename', '')
        category = body.get('category', 'auto')

        if not filename:
            return jsonify({'error': 'filename required'}), 400

        safe_name = os.path.basename(filename)
        fpath = os.path.join(Config.MARKET_DATA_FOLDER, safe_name)
        if not os.path.isfile(fpath):
            return jsonify({'error': f'Archivo no encontrado: {safe_name}'}), 404

        if category == 'auto':
            category = _detect_market_category(safe_name)

        try:
            data, meta = _load_market_file(fpath, category)
            stats = None
            # Calcular estadísticos
            if 'compra' in data and 'venta' in data:
                stats = {
                    'compra': base_year_stats(np.array(data['compra'])),
                    'venta':  base_year_stats(np.array(data['venta'])),
                }
            elif 'values' in data:
                stats = base_year_stats(np.array(data['values']))

            # Guardar en sesión
            sess = get_session(sid)
            sess.setdefault('market', {})[category] = {
                'data': data, 'meta': meta, 'filename': safe_name,
            }

            # Chart preview (primera columna de valores, hasta 2000 pts)
            if 'values' in data:
                vals = data['values']
            elif 'venta' in data:
                vals = data['venta']
            else:
                vals = []
            step = max(1, len(vals) // 2000)
            chart_vals = vals[::step] if isinstance(vals, list) else vals

            return jsonify(numpy_to_python({
                'session_id': sid,
                'filename':   safe_name,
                'category':   category,
                'meta':       meta,
                'stats':      stats,
                'n_rows':     meta.get('n_rows', len(vals)),
                'chart': {
                    'labels': list(range(0, len(chart_vals))),
                    'values': chart_vals,
                    'label':  meta.get('label', safe_name),
                },
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/market/upload', methods=['POST'])
    def market_upload():
        """Sube un archivo de mercado nuevo y lo almacena en sesión + carpeta."""
        sid = request.form.get('session_id') or str(uuid.uuid4())
        category = request.form.get('category', 'auto')

        if 'file' not in request.files:
            return _err('No file provided')

        f = request.files['file']
        if not f.filename or not allowed_file(f.filename):
            return _err(f'Tipo de archivo no soportado: {f.filename}')

        safe_name = secure_filename(f.filename) or 'market_upload.csv'
        if category == 'auto':
            category = _detect_market_category(safe_name)

        # Guardar en MARKET_DATA_FOLDER
        dest = os.path.join(Config.MARKET_DATA_FOLDER, safe_name)
        f.save(dest)

        try:
            data, meta = _load_market_file(dest, category)
            stats = None
            if 'compra' in data and 'venta' in data:
                stats = {
                    'compra': base_year_stats(np.array(data['compra'])),
                    'venta':  base_year_stats(np.array(data['venta'])),
                }
            elif 'values' in data:
                stats = base_year_stats(np.array(data['values']))

            sess = get_session(sid)
            sess.setdefault('market', {})[category] = {
                'data': data, 'meta': meta, 'filename': safe_name,
            }

            vals = data.get('values', data.get('venta', []))
            step = max(1, len(vals) // 2000)
            chart_vals = vals[::step] if isinstance(vals, list) else vals

            return jsonify(numpy_to_python({
                'session_id': sid,
                'filename':   safe_name,
                'category':   category,
                'meta':       meta,
                'stats':      stats,
                'n_rows':     meta.get('n_rows', len(vals)),
                'chart': {
                    'labels': list(range(0, len(chart_vals))),
                    'values': chart_vals,
                    'label':  meta.get('label', safe_name),
                },
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return _err(str(e), 500)

    # ─── DEMANDA & PML — PRONÓSTICO A 25 AÑOS ────────────────────────────────

    # Carpetas dedicadas por categoría — cualquier archivo nuevo depositado se
    # descubre automáticamente como preset.
    MARKET_FOLDERS = {
        'demand':   Config.DEMAND_FOLDER,
        'pml':      Config.PML_FOLDER,
        'interest': Config.INTEREST_FOLDER,
        'fx':       Config.FX_FOLDER,
    }
    MARKET_UNITS_DEFAULT = {
        'demand':   'MW',
        'pml':      '$/kWh',
        'interest': '%',
        'fx':       'MXN',
    }
    MARKET_PRESET_EXTS = {'.txt', '.csv', '.tsv', '.dat'}

    # Etiquetas amigables para archivos conocidos. Si un archivo nuevo no está
    # listado aquí, se genera una etiqueta a partir del nombre.
    KNOWN_PRESET_LABELS = {
        'JUCHITANDEMANDA.txt':   ('juchitan',   'Juchitán — Demanda nodal',   'MW'),
        'LOAD.txt':              ('load',       'Carga del sistema',           'MW'),
        'LOAD50MWH.txt':         ('load50mwh',  'Carga del sistema (50 MWh)',  'MWh'),
        'PML_IPO.txt':           ('pml_ipo',    'PML horario (compra/venta)',  '$/kWh'),
        'PML_usd.txt':           ('pml_usd',    'PML en USD (compra/venta)',   'USD/kWh'),
        'GDMTH_USD.txt':         ('gdmth_usd',  'Tarifa GDMTH (USD/kWh)',      'USD/kWh'),
        'TIIE_28D_HISTORICO.txt':('tiie_28d',   'TIIE 28d · histórico aportado', '%'),
        'USDMXN_HISTORICO.txt':  ('usdmxn',     'USD/MXN · histórico aportado',  'MXN/USD'),
        'INFLACION_HISTORICO.txt':('inflacion', 'Inflación histórica (INEGI)', '%'),
    }

    def _slugify_preset_key(stem):
        """Genera un key estable a partir del nombre de archivo (sin extensión)."""
        slug = re.sub(r'[^a-z0-9]+', '_', stem.lower()).strip('_')
        return slug or 'preset'

    def _humanize_preset_label(stem):
        """Convierte 'PML_usd' en 'PML usd' para usuarios."""
        return re.sub(r'[_\-]+', ' ', stem).strip()

    def _scan_market_folder(category):
        """Escanea la carpeta dedicada de una categoría y la carpeta legado.
        Devuelve dict {key: meta} con file, path, label, unit, kind."""
        result = {}
        seen_files = set()
        folders = []
        # 1) Carpeta dedicada por categoría
        cat_folder = MARKET_FOLDERS.get(category)
        if cat_folder:
            folders.append(cat_folder)
        # 2) Fallback: carpeta legado (compat)
        if Config.MARKET_DATA_FOLDER and Config.MARKET_DATA_FOLDER not in folders:
            folders.append(Config.MARKET_DATA_FOLDER)

        unit_def = MARKET_UNITS_DEFAULT.get(category, '')
        cat_folder = MARKET_FOLDERS.get(category)
        for folder in folders:
            if not folder or not os.path.isdir(folder):
                continue
            is_dedicated = (folder == cat_folder)
            for fname in sorted(os.listdir(folder)):
                if fname.startswith('.') or fname.startswith('~'):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext not in MARKET_PRESET_EXTS:
                    continue
                # En la carpeta dedicada por categoría aceptamos cualquier
                # archivo (el usuario lo puso ahí intencionalmente). Sólo
                # filtramos por heurística cuando caemos a la carpeta legado
                # para evitar que se mezclen categorías.
                if not is_dedicated:
                    detected = _detect_market_category(fname)
                    if detected != category:
                        continue
                if fname in seen_files:
                    continue
                fpath = os.path.join(folder, fname)
                if not os.path.isfile(fpath):
                    continue
                seen_files.add(fname)
                if fname in KNOWN_PRESET_LABELS:
                    key, label, unit = KNOWN_PRESET_LABELS[fname]
                else:
                    stem = os.path.splitext(fname)[0]
                    key = _slugify_preset_key(stem)
                    label = _humanize_preset_label(stem)
                    unit = unit_def
                # Evitar colisión de keys
                base_key = key
                i = 2
                while key in result:
                    key = f'{base_key}_{i}'; i += 1
                result[key] = {
                    'file':  fname,
                    'path':  fpath,
                    'label': label,
                    'unit':  unit,
                    'kind':  category,
                }
        return result

    # Las funciones siguientes leen el sistema de archivos en cada llamada para
    # que cualquier archivo recién depositado aparezca sin reiniciar el server.
    def PML_PRESETS():       return _scan_market_folder('pml')

    def _market_path(filename, category=None):
        """Resuelve la ruta absoluta de un archivo de mercado.
        Busca primero en la carpeta de la categoría (si se pasa), luego en
        todas las carpetas dedicadas, y finalmente en la carpeta legado."""
        safe = os.path.basename(filename)
        candidates = []
        if category and MARKET_FOLDERS.get(category):
            candidates.append(MARKET_FOLDERS[category])
        for cat, folder in MARKET_FOLDERS.items():
            if folder and folder not in candidates:
                candidates.append(folder)
        if Config.MARKET_DATA_FOLDER and Config.MARKET_DATA_FOLDER not in candidates:
            candidates.append(Config.MARKET_DATA_FOLDER)
        for folder in candidates:
            p = os.path.join(folder, safe)
            if os.path.isfile(p):
                return p
        # Si no existe en ninguna, devolver path en carpeta categoría (o legado)
        # para que el caller emita el error 404 con un path significativo.
        fallback = (MARKET_FOLDERS.get(category) if category else None) or \
                   Config.MARKET_DATA_FOLDER
        return os.path.join(fallback, safe)

    def _get_market_values(sess, category, preset_key=None):
        """Obtiene valores de mercado: primero del Data Hub (market), luego de presets legacy."""
        # 1) Intentar Data Hub (market session store)
        market = sess.get('market', {}).get(category)
        if market and market.get('data'):
            data = market['data']
            if 'values' in data:
                return np.asarray(data['values'], dtype=float), market.get('meta', {})
            if 'compra' in data:
                return data, market.get('meta', {})
        # 2) Fallback: preset cache
        if category == 'pml' and preset_key:
            cache = (sess.get('pml') or {}).get(preset_key)
            if cache:
                return cache, cache.get('meta', {})
        return None, None

    @app.route('/api/pml/load', methods=['POST'])
    def pml_load():
        """Carga PML y devuelve estadísticos compra/venta."""
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        key  = body.get('preset', 'pml_ipo')
        sess = get_session(sid)

        # Intentar desde Data Hub primero
        pml_data, meta = _get_market_values(sess, 'pml', key)
        if pml_data is None:
            meta = PML_PRESETS().get(key)
            if not meta:
                return jsonify({'error': f'Preset desconocido: {key}'}), 400
            fpath = _market_path(meta['file'])
            if not os.path.isfile(fpath):
                return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
            data = read_pml(fpath)
            sess.setdefault('pml', {})
            sess['pml'][key] = {'compra': data['compra'].tolist(),
                                 'venta':  data['venta'].tolist(),
                                 'meta':   meta}
            pml_data = sess['pml'][key]

        try:
            if isinstance(pml_data, dict) and 'compra' in pml_data:
                compra_raw = np.asarray(pml_data['compra'], dtype=float)
                venta_raw  = np.asarray(pml_data['venta'], dtype=float)
            else:
                # Single-column fallback — treat as venta
                vals = np.asarray(pml_data if isinstance(pml_data, np.ndarray) else pml_data, dtype=float)
                compra_raw = venta_raw = vals

            compra, compra_norm = normalize_fixed_hourly_profile(compra_raw)
            venta, venta_norm = normalize_fixed_hourly_profile(venta_raw)

            stats_compra, hit_compra = get_or_compute(
                'market_stats',
                ('base-year-stats-v2-2018', _array_cache_signature(compra, {'category': 'pml', 'key': key, 'side': 'compra'})),
                lambda: base_year_stats(compra),
            )
            stats_venta, hit_venta = get_or_compute(
                'market_stats',
                ('base-year-stats-v2-2018', _array_cache_signature(venta, {'category': 'pml', 'key': key, 'side': 'venta'})),
                lambda: base_year_stats(venta),
            )
            stats_compra = dict(stats_compra)
            stats_venta = dict(stats_venta)
            stats_compra['cache_hit'] = bool(hit_compra)
            stats_venta['cache_hit'] = bool(hit_venta)
            _store_fixed_pml_profile(
                sess, source_key=key, meta=meta, profile=venta,
                normalization=venta_norm, base_year=2018)
            daily_venta = venta.reshape(365, 24).mean(axis=1)
            return jsonify(numpy_to_python({
                'preset': key, 'meta': meta,
                'mode': 'fixed_2018_profile',
                'base_year': 2018,
                'side_used_in_project': 'venta',
                'stats_compra': stats_compra,
                'stats_venta':  stats_venta,
                'normalization': {
                    'compra': compra_norm,
                    'venta': venta_norm,
                },
                'profile_2018': venta.tolist(),
                'daily_mean_venta': daily_venta.tolist(),
                'replication': {
                    'horizon_years': int((sess.get('park_configuration') or {}).get('project_horizon_years', 25)),
                    'price_escalation_pct': 0.0,
                    'modeling': 'none',
                },
                'sample_first_24h': {
                    'compra': compra[:24].tolist(),
                    'venta':  venta[:24].tolist(),
                },
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    return app

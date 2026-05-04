"""
AERO by OPEN CIM — Flask application and REST API.
All scientific computation is delegated to core/ modules.
"""
import os
import io
import json
import re
import uuid
import time
import logging
import threading
import traceback
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_from_directory, render_template
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
from core.cache import bytes_hash, file_signature, get as cache_get, get_or_compute, set as cache_set, stable_hash, timed
from core.data import DataLoader, DataQualityChecker, FeatureEngineer, df_to_json_safe, series_to_chart
from core.models import ModelArena
from core.validate import (compute_all_metrics, build_comparison_table,
                            temporal_stability, residual_analysis, reliability_diagram)
from core.energy import (PowerCurve, WindToPowerEngine, AEPCalculator,
                          TurbineComparator, TechnoEconomicAnalyzer,
                          air_density, extrapolate_wind_hellmann,
                          extrapolate_wind_log,
                          load_turbines_from_folder, list_turbine_info)
from core.cost_model import CostModel, get_default_model_dict
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
from core.kriging import (parse_coords_from_filename, read_wind_file,
                          site_wind_stats, kriging_grid, analyze_sites,
                          analyze_spatial_topsis_sites,
                          legacy_three_maps, parallel_read_sites,
                          _compute_site_stats_cached, clear_site_stats_cache,
                          _haversine_km)
from core.farm import (simulate_wind_farm, jensen_deficit, dominant_direction,
                       recommend_n_turbines, optimize_layout_greedy,
                       optimize_layout_structured, energy_weighted_dominant_direction)
from core.multicriteria import (topsis, rank_sites_turbines,
                                 DEFAULT_WEIGHTS, CRITERION_LABELS,
                                 CRITERION_DIRECTION)
from core.forecasting import (read_one_column, read_pml,
                               base_year_stats,
                               synthesize_annual_series,
                               fit_all_trend_models,
                               project_25_years,
                               best_trend_model)


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
    sessions = {}        # session_id -> { df, meta, features, results, ... }
    tasks    = {}        # task_id    -> { status, progress, message, result }
    arena_store = {}     # session_id -> ModelArena
    _sessions_lock = threading.Lock()
    _executor = ThreadPoolExecutor(max_workers=4)
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
                arena_store.pop(sid, None)
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
        meta = sess.get('meta') or {}
        if meta.get('cache_key'):
            base = meta['cache_key']
        elif 'df' in sess:
            df = sess['df']
            base = stable_hash({
                'shape': getattr(df, 'shape', None),
                'columns': list(getattr(df, 'columns', [])),
                'index_start': str(df.index[0]) if len(df.index) else None,
                'index_end': str(df.index[-1]) if len(df.index) else None,
            })
        else:
            base = 'no-dataframe'
        return stable_hash({'base': base, 'extra': extra or {}})

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

    def _farm_wind_from_session(df, ws_col, wd_col, measurement_h, hub_height_m):
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

    def _load_kw_for_dispatch(df, n_hours, dt_h, demand_mwh=0.0):
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
        return jsonify({'version': '1.0', 'name': 'AERO by OPEN CIM'}), 200

    @app.route('/api/cost_model/defaults', methods=['GET'])
    def cost_model_defaults():
        """Modelo económico central WindPro: valores y rangos para sensibilidad."""
        return jsonify(get_default_model_dict()), 200

    @app.route('/api/sessions/<sid>', methods=['DELETE'])
    def delete_session(sid):
        with _sessions_lock:
            sessions.pop(sid, None)
            arena_store.pop(sid, None)
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
        if 'df' not in sess:
            return jsonify({'error': 'No data loaded. Upload a file first.'}), 400

        try:
            cache_parts = (
                'quality-v1',
                _df_cache_signature(sess, {'target_col': target_col}),
            )

            def compute_quality():
                checker = DataQualityChecker()
                return checker.run(sess['df'], target_col=target_col)

            (report, flags), cache_hit = get_or_compute('quality', cache_parts, compute_quality)
            report = dict(report)
            report['cache_hit'] = bool(cache_hit)
            sess['qc_report'] = report
            sess['qc_flags']  = flags

            # time series with flags highlighted
            df = sess['df']
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
                            'columns_report': columns_report})
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    # ─── FEATURE ENGINEERING ──────────────────────────────────────────────────

    @app.route('/api/features/build', methods=['POST'])
    def build_features():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        target_col   = body.get('target_col', 'wind_speed')
        horizons_h   = body.get('horizons', [1, 6, 24])
        test_frac    = float(body.get('test_frac', 0.2))
        sess = get_session(sid)

        if 'df' not in sess:
            return jsonify({'error': 'No data loaded.'}), 400

        try:
            feature_params = {
                'target_col': target_col,
                'horizons_h': horizons_h,
                'lags': Config.DEFAULT_LAG_HOURS,
                'rolling_windows': Config.DEFAULT_ROLLING_WINDOWS,
            }
            cache_parts = (
                'features-v1',
                _df_cache_signature(sess, feature_params),
            )

            def compute_features():
                fe = FeatureEngineer()
                with timed('build features', logger):
                    return fe.build(sess['df'], target_col=target_col,
                                    horizons_h=horizons_h)

            feat_dict, cache_hit = get_or_compute('features', cache_parts, compute_features)
            sess['features']   = feat_dict
            sess['target_col'] = target_col
            sess['test_frac']  = test_frac
            sess['horizons']   = horizons_h
            sess['features_cache_hit'] = bool(cache_hit)
            sess['features_cache_key'] = stable_hash(cache_parts)

            # return summary
            summary = {}
            for h, (X, y, feat_names, idx) in feat_dict.items():
                split = int(len(X) * (1 - test_frac))
                summary[h] = {
                    'n_samples': len(X),
                    'n_features': len(feat_names),
                    'n_train': split,
                    'n_test': len(X) - split,
                    'feature_names': feat_names,
                    'target_mean_train': round(float(np.mean(y[:split])), 3),
                    'target_mean_test':  round(float(np.mean(y[split:])), 3),
                }
            return jsonify({'summary': summary, 'horizons': horizons_h,
                            'cache_hit': bool(cache_hit)})
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    # ─── MODEL TRAINING ───────────────────────────────────────────────────────

    @app.route('/api/models/train', methods=['POST'])
    @limiter.limit('5 per minute')
    def train_models():
        body   = request.get_json(silent=True) or {}
        sid    = _require_sid(body)
        horizon_h = int(body.get('horizon_h', 1))
        enabled_models = body.get('models', None)  # None = train all
        task_id = str(uuid.uuid4())
        tasks[task_id] = {'status': 'running', 'progress': 0,
                          'message': 'Starting...', 'result': None}

        sess = get_session(sid)
        if 'features' not in sess:
            return _err('Run /api/features/build first.')
        if horizon_h not in sess['features']:
            return _err(f'Horizon {horizon_h}h not in feature set.')

        def _train_thread():
            try:
                X, y, feat_names, idx = sess['features'][horizon_h]
                fe = FeatureEngineer()
                test_frac = sess.get('test_frac', 0.2)
                X_tr, X_te, y_tr, y_te, idx_tr, idx_te = \
                    fe.train_test_split_temporal(X, y, idx, test_frac)

                train_parts = (
                    'model-results-v1',
                    sess.get('features_cache_key') or _df_cache_signature(sess),
                    int(horizon_h),
                    float(test_frac),
                    tuple(sorted(enabled_models)) if enabled_models else 'all',
                )
                cached_results = cache_get('model_results', *train_parts)
                if cached_results is not None:
                    sess[f'results_h{horizon_h}'] = cached_results
                    sess[f'split_h{horizon_h}'] = {
                        'X_tr': X_tr, 'y_tr': y_tr, 'X_te': X_te, 'y_te': y_te,
                        'idx_tr': idx_tr, 'idx_te': idx_te,
                    }
                    out = {}
                    for name, res in cached_results.items():
                        if name.startswith('_'):
                            out[name] = res
                            continue
                        if 'error' in res:
                            out[name] = {'error': res['error']}
                            continue
                        out[name] = {
                            'metrics': numpy_to_python(res['metrics']),
                            'type': res.get('type', 'ml'),
                            'feature_importance': res.get('feature_importance', [])[:10],
                            'cache_hit': True,
                        }
                    tasks[task_id]['result'] = out
                    tasks[task_id]['status'] = 'done'
                    tasks[task_id]['progress'] = 100
                    tasks[task_id]['message'] = 'Training loaded from cache'
                    return

                def cb(pct, msg):
                    tasks[task_id]['progress'] = pct
                    tasks[task_id]['message'] = msg

                arena = ModelArena()
                results = arena.train_all(X_tr, y_tr, X_te, y_te,
                                          idx_te, feat_names,
                                          horizon_h=horizon_h,
                                          progress_callback=cb,
                                          enabled_models=enabled_models)
                arena_store[sid] = arena
                sess[f'results_h{horizon_h}'] = results
                cache_set('model_results', results, *train_parts)
                sess[f'split_h{horizon_h}'] = {
                    'X_tr': X_tr, 'y_tr': y_tr, 'X_te': X_te, 'y_te': y_te,
                    'idx_tr': idx_tr, 'idx_te': idx_te,
                }

                # serialise for API
                out = {}
                for name, res in results.items():
                    if name.startswith('_'):
                        out[name] = res
                        continue
                    if 'error' in res:
                        out[name] = {'error': res['error']}
                        continue
                    out[name] = {
                        'metrics': numpy_to_python(res['metrics']),
                        'type': res.get('type', 'ml'),
                        'feature_importance': res.get('feature_importance', [])[:10],
                    }
                tasks[task_id]['result'] = out
                tasks[task_id]['status'] = 'done'
                tasks[task_id]['progress'] = 100
                tasks[task_id]['message'] = 'Training complete'
            except Exception as e:
                tasks[task_id]['status'] = 'error'
                tasks[task_id]['message'] = str(e)
                logger.exception('Task fallida')

        _executor.submit(_train_thread)
        return jsonify({'task_id': task_id})

    @app.route('/api/models/status/<task_id>', methods=['GET'])
    def task_status(task_id):
        if task_id not in tasks:
            return jsonify({'error': 'Unknown task'}), 404
        t = tasks[task_id]
        return jsonify(numpy_to_python({
            'status': t['status'], 'progress': t['progress'],
            'message': t['message'],
            'result': t.get('result') if t['status'] == 'done' else None,
        }))

    # ─── FORECAST / PREDICTIONS ───────────────────────────────────────────────

    @app.route('/api/forecast/predictions', methods=['POST'])
    def get_predictions():
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        horizon_h = int(body.get('horizon_h', 1))
        model_name = body.get('model', 'lightgbm')
        sess = get_session(sid)
        res_key = f'results_h{horizon_h}'

        if res_key not in sess:
            return jsonify({'error': 'Models not trained for this horizon.'}), 400

        results = sess[res_key]
        if model_name not in results:
            model_name = next((k for k in results if results[k].get('type') == 'ml'
                               and 'error' not in results[k]), None)
            if model_name is None:
                return jsonify({'error': 'No trained ML model found.'}), 400

        res = results[model_name]
        y_true = np.array(res['y_true'])
        y_pred = np.array(res['y_pred'])
        idx    = res['idx']

        # quantile intervals if available
        q_data = results.get('_quantiles', {})

        # temporal stability
        try:
            stab_idx = pd.DatetimeIndex([pd.Timestamp(i) for i in idx])
            stability = temporal_stability(y_true, y_pred, stab_idx)
        except Exception:
            stability = []

        # residual analysis
        try:
            resid = residual_analysis(y_true, y_pred)
        except Exception:
            resid = {}

        # calibration
        calib = {}
        if '_quantiles' in results:
            q = results['_quantiles']
            try:
                calib = reliability_diagram(y_true, np.array(q['q10']),
                                            np.array(q['q90']), nominal_coverage=0.80)
            except Exception:
                pass

        step = max(1, len(idx) // 2000)
        return jsonify(numpy_to_python({
            'model': model_name,
            'horizon_h': horizon_h,
            'labels':  idx[::step],
            'y_true':  safe_float_list(y_true[::step].tolist()),
            'y_pred':  safe_float_list(y_pred[::step].tolist()),
            'q10':     safe_float_list(np.array(q_data.get('q10', []))[::step].tolist())
                       if q_data.get('q10') else None,
            'q90':     safe_float_list(np.array(q_data.get('q90', []))[::step].tolist())
                       if q_data.get('q90') else None,
            'metrics': res['metrics'],
            'stability': stability,
            'residuals': resid,
            'calibration': calib,
        }))

    @app.route('/api/forecast/comparison', methods=['POST'])
    def comparison_table():
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        horizon_h = int(body.get('horizon_h', 1))
        sess = get_session(sid)
        res_key = f'results_h{horizon_h}'

        if res_key not in sess:
            return jsonify({'error': 'Models not trained.'}), 400

        results = sess[res_key]
        rows = []
        pers_pred = np.array(results['persistence']['y_pred']) \
            if 'persistence' in results else None

        for name, res in results.items():
            if name.startswith('_') or 'error' in res or 'metrics' not in res:
                continue
            m = res['metrics'].copy()
            rows.append(m)

        rows.sort(key=lambda x: x['rmse'])
        return jsonify({'rows': numpy_to_python(rows), 'best': rows[0]['label'] if rows else None})

    # ─── ENERGY MODULE ────────────────────────────────────────────────────────

    @app.route('/api/energy/compute', methods=['POST'])
    def energy_compute():
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        sess = get_session(sid)

        if 'df' not in sess:
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

            ws_col = sess.get('target_col', 'wind_speed')
            df_sim, summary = engine.run(
                sess['df'], ws_col=ws_col,
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

        if 'df' not in sess:
            return jsonify({'error': 'No data loaded.'}), 400

        turbine_keys = body.get('turbine_keys', None)
        ws_col = sess.get('target_col', 'wind_speed')
        ws_height_m = _sess_ws_h(body, sess)

        try:
            # load all available turbines (built-in + folder) if no explicit list given
            all_turbines = [PowerCurve.from_builtin(k) for k in PowerCurve.BUILTIN_CURVES]
            all_turbines += load_turbines_from_folder(Config.TURBINES_FOLDER)
            if turbine_keys:
                all_turbines = [t for t in all_turbines if t.name in turbine_keys]
            turbines = all_turbines if all_turbines else [PowerCurve.from_builtin('V150_4.5')]
            df = sess['df']
            ws = df[ws_col].fillna(df[ws_col].median()).values

            # extrapolate to each turbine's hub height
            comp = TurbineComparator()
            results = comp.compare(turbines, ws, dt_h=1.0)
            return jsonify({'comparison': numpy_to_python(results)})
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

        if 'df' not in sess:
            return jsonify({'error': 'No data loaded.'}), 400

        try:
            df = sess['df']
            params = {
                'ws_col': ws_col, 'wd_col': wd_col,
                'temp_col': temp_col, 'pres_col': pres_col,
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
            sess['wind_stats'] = result
            return jsonify(result)
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    # ─── ADVANCED WIND ANALYSIS (Windographer-style) ──────────────────────────

    @app.route('/api/wind/advanced', methods=['POST'])
    def wind_advanced():
        """TI, wind shear, sector analysis, extreme return periods."""
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

        if 'df' not in sess:
            return jsonify({'error': 'No data loaded.'}), 400

        try:
            df = sess['df']
            result = {
                'wind_speed_variability': numpy_to_python(compute_turbulence_intensity(df, ws_col)),
                'ti_warning':             'Proxy horario, no TI IEC 61400-1',
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

        if 'df' not in sess:
            return jsonify({'error': 'No data loaded.'}), 400

        try:
            df     = sess['df']
            ws_col = sess.get('target_col', 'wind_speed')
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

        # Validación de rangos físicos/financieros (antes del df check)
        rated_kw = float(turbine_cfg.get('rated_power_kw', 2000))
        hub_h    = float(turbine_cfg.get('hub_height_m', 80))
        d_rate   = float(fin_cfg.get('discount_rate_pct', 8.0)) / 100.0
        n_yrs    = int(fin_cfg.get('project_years', 20))
        err = _validate_ranges(discount_rate=d_rate, n_years=n_yrs,
                               rated_power_kw=rated_kw, hub_height_m=hub_h)
        if err is not None:
            return err

        if 'df' not in sess:
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

            ws_col      = sess.get('target_col', 'wind_speed')
            ws_height_m = _sess_ws_h(body, sess)
            df          = sess['df']
            requested_n_turbines = int(body.get('n_turbines') or 1)
            annual_energy_override_mwh = body.get('annual_energy_mwh')
            annual_wind_mean_ms = body.get('annual_wind_mean_ms')
            annual_wind_series_ms = body.get('annual_wind_series_ms')
            _wind_series = None
            if annual_wind_series_ms and isinstance(annual_wind_series_ms, list):
                _clean = []
                for _sv in annual_wind_series_ms:
                    try:
                        _fv = float(_sv)
                        if np.isfinite(_fv) and _fv > 0:
                            _clean.append(_fv)
                    except (TypeError, ValueError):
                        pass
                if _clean:
                    _wind_series = _clean

            dt_h = _infer_dt_h(df.index)
            wind_info = {'source': 'session'}
            dispatch_index = df.index

            if _wind_series:
                # ML series: use mean speed for representative Weibull (Cp/dispatch display)
                _v_mean = float(np.mean(_wind_series))
                ws_hub, _ = _farm_weibull_fallback(_v_mean, 2.0, n_hours=8760)
                dt_h = 1.0
                dispatch_index = None
                wind_info = {
                    'source': 'forecast_ml_series',
                    'n_years': len(_wind_series),
                    'v_mean_ms': round(_v_mean, 3),
                }
            elif annual_wind_mean_ms is not None and np.isfinite(float(annual_wind_mean_ms)) and float(annual_wind_mean_ms) > 0:
                ws_hub, _ = _farm_weibull_fallback(float(annual_wind_mean_ms), 2.0, n_hours=8760)
                dt_h = 1.0
                dispatch_index = None
                wind_info = {
                    'source': 'forecast_weibull',
                    'annual_wind_mean_ms': float(annual_wind_mean_ms),
                    'n_records': int(len(ws_hub)),
                }
            else:
                ws_hub, _, _, _, _, wind_info = _farm_wind_from_session(
                    df, ws_col, body.get('wd_col', 'wind_dir'), ws_height_m, turbine.hub_height_m
                )
                if ws_hub is None:
                    return _err(
                        'La serie horaria de viento no tiene datos válidos para el análisis tecno-económico '
                        f'({wind_info.get("reason", "invalid_session_wind")}).'
                    )

            # air density — pressure ya viene en Pa (data.py normaliza)
            rho = air_density()
            if 'pressure' in df.columns and 'temperature' in df.columns:
                try:
                    p_pa = float(df['pressure'].dropna().mean())
                    t_c  = float(df['temperature'].dropna().mean())
                    rho  = float(air_density(temperature_c=t_c, pressure_pa=p_pa))
                except Exception:
                    pass

            # Per-year AEP per turbine for ML wind series (computed post-rho)
            _per_yr_kwh_per_turbine = None
            if _wind_series:
                _per_yr_kwh_per_turbine = []
                for _v in _wind_series:
                    _ws_yr, _ = _farm_weibull_fallback(_v, 2.0, n_hours=8760)
                    _p_kw_yr = turbine.power_kw_density_corrected(_ws_yr, rho)
                    _per_yr_kwh_per_turbine.append(float(_p_kw_yr.sum()))

            if annual_energy_override_mwh is not None and np.isfinite(float(annual_energy_override_mwh)) and float(annual_energy_override_mwh) > 0:
                annual_kwh_per_turbine = float(annual_energy_override_mwh) * 1000 / max(requested_n_turbines, 1)
            elif _per_yr_kwh_per_turbine:
                annual_kwh_per_turbine = float(np.mean(_per_yr_kwh_per_turbine))
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
            _project_years = int(fin_cfg.get('project_years', 20))
            _annual_energy_series_kwh = None
            if _per_yr_kwh_per_turbine:
                _annual_energy_series_kwh = [v * n_turbines for v in _per_yr_kwh_per_turbine]
                if len(_annual_energy_series_kwh) < _project_years:
                    _mean_kwh = annual_kwh_per_turbine * n_turbines
                    _annual_energy_series_kwh += [_mean_kwh] * (_project_years - len(_annual_energy_series_kwh))
                _annual_energy_series_kwh = _annual_energy_series_kwh[:_project_years]

            # ── Variable per-year params (Predicción ML / Histórico 8760) ──
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
            _rate_series  = _clean_series(fin_cfg.get('discount_rate_series_pct'),    _project_years)
            _fx_series    = _clean_series(fin_cfg.get('usd_to_mxn_series'),           _project_years)

            # ── Year-1 revenue ponderado horario (PML × FX × despacho 8760) ──
            _year1_rev_override = None
            try:
                price_hourly = fin_cfg.get('price_hourly_year1_usd_kwh')
                fx_hourly    = fin_cfg.get('fx_hourly_year1_mxn_per_usd')
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
                        _y1_energy_target_kwh = (
                            float(_annual_energy_series_kwh[0]) if _annual_energy_series_kwh
                            else annual_kwh_per_turbine * n_turbines
                        )
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
                        # FX hourly weight: convert hourly revenue to USD using a per-hour fx ratio
                        # If user provided FX hourly series, the year-1 fx weighting for revenue is
                        # implicitly the mean (since fx_series[0] is later applied to the override
                        # when used to convert to MXN). We keep override in USD using p_arr directly.
                        revenue_usd_y1 = float((e_arr * p_arr).sum())
                        if np.isfinite(revenue_usd_y1) and revenue_usd_y1 > 0:
                            _year1_rev_override = revenue_usd_y1
            except Exception as _e_y1:
                logger.warning('Year-1 hourly revenue override failed: %s', _e_y1)

            analysis = tea.analyze(
                turbine, n_turbines, annual_kwh_per_turbine * n_turbines,
                energy_price_usd_kwh=float(fin_cfg.get('energy_price_usd_kwh', 0.07)),
                discount_rate_pct=float(fin_cfg.get('discount_rate_pct', 8.0)),
                project_years=_project_years,
                usd_to_mxn=1.0,
                annual_energy_kwh_series=_annual_energy_series_kwh,
                energy_price_series_usd_kwh=_price_series,
                discount_rate_series_pct=_rate_series,
                usd_to_mxn_series=None,  # FX series only affects MXN display, not USD analysis
                year1_revenue_override_usd=_year1_rev_override,
            )
            analysis['fx_mxn_per_usd'] = fx_mxn_per_usd
            # Expose mean FX series for display (frontend may want average over years)
            if _fx_series:
                try:
                    _fx_mean = float(np.mean([v for v in _fx_series if v is not None and np.isfinite(v)]))
                    if np.isfinite(_fx_mean) and _fx_mean > 0:
                        analysis['fx_mxn_per_usd_mean_series'] = _fx_mean
                        analysis['fx_mxn_per_usd_series'] = _fx_series
                except Exception:
                    pass
            if _price_series:
                analysis['energy_price_series_usd_kwh'] = _price_series
            if _rate_series:
                analysis['discount_rate_series_pct'] = _rate_series

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
                df, len(ws_hub), dt_h, demand_mwh=float(fin_cfg.get('demand_mwh', 0))
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
        try:
            capex          = float(body['capex_usd'])
            annual_revenue = float(body['annual_revenue_usd'])
            annual_opex    = float(body['annual_opex_usd'])
            project_years  = int(body.get('project_years', 20))
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
        base_price = float(fin_cfg.get('energy_price_usd_kwh', 0.07))
        base_rate  = float(fin_cfg.get('discount_rate_pct', 8.0))
        base_years = int(fin_cfg.get('project_years', 20))
        base_fx    = float(fin_cfg.get('usd_to_mxn', 1.0))
        base_escal = float(fin_cfg.get('price_escalation_pct', 0.0))
        cost_mdl   = CostModel.from_dict(fin_cfg.get('cost_model'))

        if base_kwh <= 0:
            return _err('base_annual_kwh must be > 0')

        def _run(price=None, rate=None, aep_scale=1.0, escal=None, capex_mult=1.0):
            cm = CostModel.from_dict(fin_cfg.get('cost_model'))
            # apply capex multiplier via OPEX/CAPEX override
            if capex_mult != 1.0:
                cm2_dict = cm.to_dict() if hasattr(cm, 'to_dict') else {}
                # scale all percentage cost items
                for k in ['turbine_pct','civil_pct','electrical_pct','installation_pct','engineering_pct','misc_pct']:
                    if k in cm2_dict:
                        cm2_dict[k] = float(cm2_dict[k])
                try:
                    from core.cost_model import CostModel as CM2
                    cm = CM2.from_dict(cm2_dict)
                    cm._capex_mult = capex_mult
                except Exception:
                    pass
            tea = TechnoEconomicAnalyzer(cost_model=cm)
            p   = base_price if price is None else price
            r   = base_rate  if rate  is None else rate
            e   = base_escal if escal is None else escal
            kwh = base_kwh * aep_scale

            # build price escalation series
            price_series = None
            if e and e != 0:
                price_series = [p * ((1 + e/100) ** yr) for yr in range(base_years)]

            res = tea.analyze(
                turbine, n_turb, kwh,
                energy_price_usd_kwh=p,
                discount_rate_pct=r,
                project_years=base_years,
                usd_to_mxn=base_fx,
                energy_price_series_usd_kwh=price_series,
            )
            npv     = float(res.get('npv_usd', 0) or 0)
            lcoe    = float(res.get('lcoe_usd_kwh', 0) or 0)
            payback = res.get('payback_years')
            payback = float(payback) if payback is not None and np.isfinite(float(payback)) else base_years * 2
            # Correct for capex multiplier in CAPEX total
            if capex_mult != 1.0:
                capex_base = float(res.get('capex_total_usd', 0) or 0)
                delta_capex = capex_base * (capex_mult - 1.0)
                npv -= delta_capex  # higher capex ↓ NPV
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

        return jsonify({
            'tornado': tornado,
            'heatmap': {
                'data':         heatmap,
                'price_labels': [str(p) for p in price_steps],
                'rate_labels':  [f'{r:.1f}%' for r in rate_steps],
            },
            'base': {
                'npv_usd':      round(base_npv, 0),
                'lcoe_usd_kwh': round(base_lcoe, 5),
                'payback_years':round(base_pb, 1),
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

        if 'df' not in sess:
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
                    rotor_diameter_m=float(t_cfg.get('rotor_diameter_m', 80)),
                )

            df = sess['df']
            ws_col = sess.get('target_col', 'wind_speed')
            wd_col = body.get('wd_col', 'wind_dir')
            ws_h    = _sess_ws_h(body, sess)
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
            target_hub_h = body.get('hub_height_m', t_cfg.get('hub_height_m') if 'hub_height_m' in t_cfg else None)
            if target_hub_h is not None:
                turbine.hub_height_m = float(target_hub_h)
            v_mean_rec = float(body.get('v_mean', 9.0))
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
                weibull_k=float(body.get('weibull_k', 2.0)),
                rho=float(body.get('rho', 1.225)),
                availability=float(body.get('availability', 0.97)),
                electrical_losses=float(body.get('electrical_losses', 0.03)),
                avg_wake_loss=float(body.get('avg_wake_loss', 0.08)),
            )
            result['turbine'] = turbine.to_dict()
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
            target_hub_h = body.get('hub_height_m', t_cfg.get('hub_height_m') if 'hub_height_m' in t_cfg else None)
            if target_hub_h is not None:
                turbine.hub_height_m = float(target_hub_h)

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
                if 'df' in sess:
                    df     = sess['df']
                    ws_col = sess.get('target_col', 'wind_speed')
                    wd_col = body.get('wd_col', 'wind_dir')
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
                sim_result['wind_source'] = wind_info.get('source')
                sim_result['wind_diagnostics'] = wind_info

            result = {**opt, 'turbine': turbine.to_dict()}
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
        if 'df' not in sess:
            return _err('No hay datos en la sesión')
        df = sess['df']

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

        if df_key not in sess:
            return jsonify({'error': f'Dataset "{dataset}" not available.'}), 400

        df = sess[df_key].copy()
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
        brief = {
            'has_data': 'df' in sess,
            'has_models': any(k.startswith('results_h') for k in sess),
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
                        'Eres un asistente tecnico de energia eolica en AERO by OPEN CIM. '
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
        has_data   = 'df' in sess
        has_models = any(k.startswith('results_h') for k in sess)
        has_energy = 'energy_summary' in sess

        if any(w in ql for w in ['datos', 'cargado', 'dataset', 'filas', 'columnas']):
            if has_data:
                n = meta.get('n_rows', '?')
                cols = meta.get('columns', [])
                dr = meta.get('date_range', ['?', '?'])
                return (f"Se han cargado {n:,} registros desde {dr[0]} hasta {dr[1]}. "
                        f"Columnas: {', '.join(cols[:8])}{'...' if len(cols) > 8 else ''}.")
            return "No hay datos cargados aún. Ve a Data Hub y sube un archivo CSV/TXT."

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

        if any(w in ql for w in ['modelo', 'rmse', 'r2', 'mae', 'skill', 'mejor']):
            best_res = None
            for k, v in sess.items():
                if k.startswith('results_h') and v:
                    best_res = v
                    break
            if best_res:
                best_name = min(
                    {n: r for n, r in best_res.items()
                     if not n.startswith('_') and 'metrics' in r},
                    key=lambda n: best_res[n]['metrics']['rmse'],
                    default=None)
                if best_name:
                    m = best_res[best_name]['metrics']
                    skill = m.get('skill_vs_persistence', 'N/A')
                    base = (f"Mejor modelo: {m['label']} — "
                            f"RMSE={m['rmse']} m/s, MAE={m['mae']} m/s, "
                            f"R²={m['r2']}, nRMSE={m['nrmse']:.1%}, ")
                    if isinstance(skill, (int, float)):
                        sign = '+' if skill > 0 else ''
                        return base + f"Skill vs persistencia: {sign}{skill*100:.1f}%."
                    return base + f"Skill: {skill}."
            return "Entrena los modelos en Model Arena primero."

        if any(w in ql for w in ['energía', 'energia', 'aep', 'potencia', 'factor de capacidad', 'cf']):
            if has_energy:
                cf = estats.get('capacity_factor_pct', '?')
                e = estats.get('energy_net_mwh', '?')
                flh = estats.get('full_load_hours', '?')
                return (f"Producción estimada: {e} MWh netos en el periodo analizado. "
                        f"Factor de capacidad: {cf}%. "
                        f"Horas equivalentes de plena carga: {flh}.")
            return "Configura y ejecuta la simulación en Energy Lab."

        if any(w in ql for w in ['persistencia', 'baseline']):
            return ("La persistencia predice que el viento en t+h es igual al viento en t. "
                    "Es el baseline estándar para pronóstico a corto plazo. "
                    "Un skill score positivo frente a persistencia indica que el modelo ML agrega valor real.")

        if any(w in ql for w in ['horizonte', 'plazo', 'corto', 'largo', 'semanal']):
            return ("Para h=1h: los modelos ML suelen superar ampliamente la persistencia. "
                    "Para h=6-24h: la ventaja ML disminuye; se recomienda combinar con NWP. "
                    "Para pronóstico semanal: se modelan distribuciones y percentiles, no series exactas. "
                    "AERO entrena un modelo independiente por horizonte para máxima precisión.")

        if any(w in ql for w in ['leakage', 'sobreajuste', 'overfitting', 'validación', 'test']):
            return ("AERO usa separación cronológica estricta:"
                    "el 80% inicial es entrenamiento y el 20% final (futuro absoluto) es test. "
                    "No hay shuffling ni cross-validation temporal que cruce el futuro al pasado. "
                    "Las features usan únicamente información disponible en el momento de la predicción.")

        if any(w in ql for w in ['merra', 'era5', 'reanálisis', 'reanalisis']):
            return ("Para cargar datos MERRA-2: exporta el dataset tabular desde GES DISC "
                    "y selecciona fuente 'MERRA-2' al subir. "
                    "Para ERA5: exporta desde Copernicus CDS en CSV y selecciona 'ERA5'. "
                    "AERO detecta automáticamente columnas U50M/V50M y deriva velocidad y dirección.")

        if any(w in ql for w in ['limitaciones', 'advertencia', 'incertidumbre', 'sesgo']):
            return ("Limitaciones declaradas: (1) Los modelos son válidos para el sitio y periodo entrenado. "
                    "(2) El pronóstico semanal no predice trazas horarias exactas — produce distribuciones. "
                    "(3) La curva de potencia asume condiciones de operación nominales. "
                    "(4) El AEP de largo plazo incluye incertidumbre inter-anual representada en P50/P75/P90.")

        # fallback
        context = f"Sesión activa: {'datos cargados' if has_data else 'sin datos'}. "
        context += f"{'Modelos entrenados.' if has_models else 'Sin modelos aún.'} "
        context += f"{'Simulación energética disponible.' if has_energy else ''}"
        return (f"No encontré una respuesta específica para '{q}'. {context} "
                f"Temas disponibles: calidad de datos, Weibull, modelos y métricas, "
                f"energía y factor de capacidad, horizonte de pronóstico, validación científica.")

    # ─── MONTH × HOUR HEATMAP ─────────────────────────────────────────────────
    @app.route('/api/wind_stats/month_hour_heatmap', methods=['POST'])
    def month_hour_heatmap():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        col = body.get('col', 'wind_speed')
        sess = get_session(sid)
        if 'df' not in sess:
            return jsonify({'error': 'No data loaded'}), 400
        hm = compute_month_hour_heatmap(sess['df'], col=col)
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

    @app.route('/api/kriging/legacy_maps', methods=['POST'])
    def kriging_legacy_maps():
        """
        Replica exacta del script ajuste_aerogeneradores.py:
        lee todos los sitios, calcula técnica-económica con el último
        aerogenerador del catálogo y genera 3 grillas Kriging
        (viento, inversión, VAN).
        """
        body = request.get_json(silent=True) or {}
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
                result = legacy_three_maps(
                    wind_folder=Config.DATA_FOLDER,
                    turbines_folder=Config.TURBINES_FOLDER,
                    year=int(body.get('year', 2018)),
                    demand_mwh=float(body.get('demand_mwh', 1000)),
                    tasa=float(body.get('tasa', 0.08)),
                    price_usd_kwh=float(body.get('price_usd_kwh', 0.20)),
                    price_mxn_per_usd=float(body.get('price_mxn_per_usd', 20)),
                    max_files=body.get('max_files'),
                    n_grid=int(body.get('n_grid', 80)),
                    model=body.get('model', 'exponential'),
                    center_lat=float(cen_lat) if cen_lat is not None else None,
                    center_lon=float(cen_lon) if cen_lon is not None else None,
                    radius_km=float(body['radius_km']) if body.get('radius_km') else None,
                    filter_filenames=body.get('filenames') or None,
                    physical=bool(body.get('physical', False)),
                    grid_bounds=grid_bounds,
                    cost_model=CostModel.from_dict(body.get('cost_model')),
                    z_tgt=float(body['target_height_m']) if body.get('target_height_m') else None,
                )
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
        year = int(body.get('year', 2018))
        demand_mwh = float(body.get('demand_mwh', 1000))
        tasa = float(body.get('tasa', 0.08))
        price_usd_kwh = float(body.get('price_usd_kwh', 0.20))
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
                    max_files=max_files, filter_filenames=filter_files,
                    bounds=area_bounds,
                    turbine_filenames=turbine_files,
                    cost_model=CostModel.from_dict(cost_model_payload),
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

        if 'alternatives' in body:
            if not isinstance(body['alternatives'], list):
                return jsonify({'error': 'alternatives must be a list'}), 400
            result = topsis(body['alternatives'], weights=weights)
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
        mode = body.get('mode') or ('area_turbines' if scope == 'park' else 'pairs')
        year = int(body.get('year', 2018))
        demand_mwh = float(body.get('demand_mwh', 1000))
        tasa = float(body.get('tasa', 0.08))
        price_usd_kwh = float(body.get('price_usd_kwh', 0.20))
        price_mxn_per_usd = float(body.get('price_mxn_per_usd', 20))
        n_grid = int(body.get('n_grid', 80))
        model = body.get('model', 'exponential')
        max_points = int(body.get('max_points') or (48 if scope == 'park' else 60))
        filter_files = body.get('filenames')
        turbine_files = body.get('turbine_filenames') or body.get('turbines') or None

        task_id = str(uuid.uuid4())
        tasks[task_id] = {'status': 'running', 'progress': 0,
                          'message': 'Construyendo análisis TOPSIS espacial…',
                          'result': None}

        def _run():
            try:
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
                    n_grid=n_grid,
                    model=model,
                    max_points=max_points,
                    filter_filenames=filter_files,
                    turbine_filenames=turbine_files,
                    cost_model=CostModel.from_dict(body.get('cost_model')),
                )
                ranking = rank_sites_turbines(spatial['sites'], weights=weights, mode=mode)
                ranking['mode'] = mode
                tasks[task_id]['result'] = numpy_to_python({
                    **spatial,
                    'ranking': ranking,
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
        'TIIE_28D_HISTORICO.txt':('tiie_28d',   'TIIE 28 Días (Banxico)',      '%'),
        'USDMXN_HISTORICO.txt':  ('usdmxn',     'USD/MXN FIX (Banxico)',       'MXN'),
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
    def DEMAND_PRESETS():    return _scan_market_folder('demand')
    def PML_PRESETS():       return _scan_market_folder('pml')
    def INTEREST_PRESETS():  return _scan_market_folder('interest')
    def FX_PRESETS():        return _scan_market_folder('fx')

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

    @app.route('/api/market/list_presets', methods=['GET'])
    def market_list_presets():
        """Lista los presets de demanda, PML, TIIE y USD/MXN disponibles
        en sus carpetas dedicadas (descubrimiento dinámico)."""
        out = {
            'folders': {cat: f for cat, f in MARKET_FOLDERS.items()},
            'legacy_folder': Config.MARKET_DATA_FOLDER,
            'demand': [], 'pml': [], 'interest': [], 'fx': [],
        }

        def _emit(category, presets):
            for key, meta in presets.items():
                fpath = meta.get('path') or _market_path(meta['file'], category)
                if os.path.isfile(fpath):
                    size_kb = round(os.path.getsize(fpath) / 1024, 1)
                    out[category].append({'key': key, **meta, 'size_kb': size_kb})

        _emit('demand',   DEMAND_PRESETS())
        _emit('pml',      PML_PRESETS())
        _emit('interest', INTEREST_PRESETS())
        _emit('fx',       FX_PRESETS())
        return jsonify(out)

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
        if category == 'demand' and preset_key:
            cache = (sess.get('demand') or {}).get(preset_key)
            if cache:
                return np.asarray(cache['values'], dtype=float), cache.get('meta', {})
        if category == 'pml' and preset_key:
            cache = (sess.get('pml') or {}).get(preset_key)
            if cache:
                return cache, cache.get('meta', {})
        return None, None

    def _real_annual_from_hourly(values, agg='mean'):
        """Convierte serie horaria a promedios anuales reales (8760 h/año).
        Si hay >=2 años, devuelve (years_rel, annual_values, base_year).
        Si solo hay 1 año o menos, devuelve None."""
        values = np.asarray(values, dtype=float)
        n = len(values)
        if n < 8760:
            return None
        # Construir DataFrame con índice temporal asumido
        df = pd.DataFrame({'v': values}, index=pd.date_range('2000-01-01', periods=n, freq='h'))
        if agg == 'sum':
            annual = df.resample('YE')['v'].sum().dropna()
        else:
            annual = df.resample('YE')['v'].mean().dropna()
        if len(annual) < 2:
            return None
        years_rel = np.arange(len(annual))
        return years_rel, annual.values, int(annual.index[-1].year)

    @app.route('/api/demand/load', methods=['POST'])
    def demand_load():
        """Carga el archivo de demanda y devuelve estadísticos del año base."""
        body = request.get_json(silent=True) or {}
        sid  = _require_sid(body)
        key  = body.get('preset', 'juchitan')
        sess = get_session(sid)

        # Intentar desde Data Hub primero
        values, meta = _get_market_values(sess, 'demand', key)
        if values is None:
            meta = DEMAND_PRESETS().get(key)
            if not meta:
                return jsonify({'error': f'Preset desconocido: {key}'}), 400
            fpath = _market_path(meta['file'])
            if not os.path.isfile(fpath):
                return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
            values = read_one_column(fpath)
            sess.setdefault('demand', {})
            sess['demand'][key] = {'values': values.tolist(), 'meta': meta}

        try:
            if isinstance(values, np.ndarray):
                vals = values
            else:
                vals = np.asarray(values, dtype=float)
            stats, stats_hit = get_or_compute(
                'market_stats',
                ('base-year-stats-v1', _array_cache_signature(vals, {'category': 'demand', 'key': key})),
                lambda: base_year_stats(vals),
            )
            stats = dict(stats)
            stats['cache_hit'] = bool(stats_hit)
            return jsonify(numpy_to_python({
                'preset': key, 'meta': meta, 'stats': stats,
                'sample_first_24h': vals[:24].tolist(),
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/demand/forecast', methods=['POST'])
    def demand_forecast():
        """
        Ajusta modelos de tendencia sobre datos REALES anuales y proyecta a 25 años.
        Usa promedios anuales reales de la serie horaria histórica (no sintetiza).
        """
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        key = body.get('preset', 'juchitan')
        growth = float(body.get('growth', Config.DEMAND_GROWTH_DEFAULT))
        horizon = int(body.get('horizon', Config.FORECAST_HORIZON_YEARS))
        hist_y  = int(body.get('history_years', Config.FORECAST_HISTORY_YEARS))
        models  = body.get('models')

        sess = get_session(sid)
        # Obtener valores — Data Hub primero, luego presets legacy
        values, meta = _get_market_values(sess, 'demand', key)
        if values is None:
            meta = DEMAND_PRESETS().get(key)
            if not meta:
                return jsonify({'error': f'Preset desconocido: {key}'}), 400
            fpath = _market_path(meta['file'])
            if not os.path.isfile(fpath):
                return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
            values = read_one_column(fpath)
            sess.setdefault('demand', {})[key] = {'values': values.tolist(), 'meta': meta}

        try:
            if not isinstance(values, np.ndarray):
                values = np.asarray(values, dtype=float)

            # Demanda: agregamos por SUMA anual (MWh totales) para mantener
            # consistencia con synthesize_annual_series (que también usa sumas).
            real_annual = _real_annual_from_hourly(values, agg='sum')
            if real_annual is not None:
                t, annual_hist, base_year = real_annual
                use_real = True
            else:
                t, annual_hist = synthesize_annual_series(
                    values, n_years_history=hist_y, growth_seed=growth)
                base_year = 2024
                use_real = False

            fitted = fit_all_trend_models(t, annual_hist, models=models)
            projection = project_25_years(values, fitted, horizon_years=horizon,
                                           base_year_label=base_year)
            best = best_trend_model(fitted)

            summary = []
            for k, m in fitted.items():
                if 'error' in m:
                    summary.append({'model': k, 'error': m['error']})
                    continue
                proj = projection.get(k, {})
                summary.append({
                    'model':   k,
                    'kind':    m.get('kind'),
                    'r2':      m['metrics']['r2'],
                    'rmse':    m['metrics']['rmse'],
                    'mape':    m['metrics']['mape'],
                    'cagr':    proj.get('cagr'),
                    'final':   proj.get('annual', [None])[-1] if proj.get('annual') else None,
                })

            for m in fitted.values():
                m.pop('_predict', None)

            raw = values.tolist()

            return jsonify(numpy_to_python({
                'preset': key,
                'growth_seed': growth,
                'horizon_years': horizon,
                'history_years': hist_y,
                'history_t': [int(x) for x in t],
                'history_annual': [float(x) for x in annual_hist],
                'fitted': fitted,
                'projection': projection,
                'summary': sorted(summary, key=lambda r: r.get('r2') or -1, reverse=True),
                'best_model': best,
                'raw_values': raw,
                'raw_n': len(raw),
                'used_real_annual': use_real,
                'annual_kind': 'sum',
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/demand/forecast_hourly', methods=['POST'])
    @limiter.limit('5 per minute')
    def demand_forecast_hourly():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        key = body.get('preset', 'juchitan')
        models = body.get('models')

        sess = get_session(sid)
        # Data Hub primero, luego legacy
        values, meta = _get_market_values(sess, 'demand', key)
        if values is None:
            meta = DEMAND_PRESETS().get(key)
            if not meta:
                return jsonify({'error': f'Preset desconocido: {key}'}), 400
            fpath = _market_path(meta['file'])
            if not os.path.isfile(fpath):
                return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
            values = read_one_column(fpath)
            sess.setdefault('demand', {})[key] = {'values': values.tolist(), 'meta': meta}

        try:
            if not isinstance(values, np.ndarray):
                values = np.asarray(values, dtype=float)
            t = np.arange(len(values))
            
            from core.forecasting import fit_all_hourly_models
            fitted = fit_all_hourly_models(t, values, models)
            
            summary = []
            for name, m in fitted.items():
                summary.append({'model': name, **m['metrics']})
                
            best = max(summary, key=lambda r: r.get('r2') or -1)['model'] if summary else None

            # Usamos raw_values completos para Demanda/PML Horario
            raw = values.tolist()
            
            for m in fitted.values():
                m['fitted_values'] = m['fitted']
                m.pop('fitted', None)

            return jsonify(numpy_to_python({
                'preset': key,
                'fitted': fitted,
                'summary': sorted(summary, key=lambda r: r.get('r2') or -1, reverse=True),
                'best_model': best,
                'raw_values': raw,
                'raw_n': len(raw),
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

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
                compra = np.asarray(pml_data['compra'], dtype=float)
                venta  = np.asarray(pml_data['venta'], dtype=float)
            else:
                # Single-column fallback — treat as venta
                vals = np.asarray(pml_data if isinstance(pml_data, np.ndarray) else pml_data, dtype=float)
                compra = venta = vals

            stats_compra, hit_compra = get_or_compute(
                'market_stats',
                ('base-year-stats-v1', _array_cache_signature(compra, {'category': 'pml', 'key': key, 'side': 'compra'})),
                lambda: base_year_stats(compra),
            )
            stats_venta, hit_venta = get_or_compute(
                'market_stats',
                ('base-year-stats-v1', _array_cache_signature(venta, {'category': 'pml', 'key': key, 'side': 'venta'})),
                lambda: base_year_stats(venta),
            )
            stats_compra = dict(stats_compra)
            stats_venta = dict(stats_venta)
            stats_compra['cache_hit'] = bool(hit_compra)
            stats_venta['cache_hit'] = bool(hit_venta)
            return jsonify(numpy_to_python({
                'preset': key, 'meta': meta,
                'stats_compra': stats_compra,
                'stats_venta':  stats_venta,
                'sample_first_24h': {
                    'compra': compra[:24].tolist(),
                    'venta':  venta[:24].tolist(),
                },
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/pml/forecast', methods=['POST'])
    def pml_forecast():
        """
        Proyecta PML a 25 años usando datos REALES anuales (no sintetizados).
        """
        body = request.get_json(silent=True) or {}
        sid     = _require_sid(body)
        key     = body.get('preset', 'pml_ipo')
        growth  = float(body.get('growth', Config.PML_GROWTH_DEFAULT))
        horizon = int(body.get('horizon', Config.FORECAST_HORIZON_YEARS))
        hist_y  = int(body.get('history_years', Config.FORECAST_HISTORY_YEARS))
        side    = body.get('side', 'venta')
        models  = body.get('models')

        sess = get_session(sid)
        pml_data, meta = _get_market_values(sess, 'pml', key)
        if pml_data is None:
            meta = PML_PRESETS().get(key)
            if not meta:
                return jsonify({'error': f'Preset desconocido: {key}'}), 400
            fpath = _market_path(meta['file'])
            if not os.path.isfile(fpath):
                return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
            data = read_pml(fpath)
            sess.setdefault('pml', {})[key] = {
                'compra': data['compra'].tolist(),
                'venta':  data['venta'].tolist(),
                'meta':   meta,
            }
            pml_data = sess['pml'][key]

        # Normalize to dict with compra/venta
        if isinstance(pml_data, dict) and 'compra' in pml_data:
            cache = pml_data
        else:
            vals = pml_data if isinstance(pml_data, list) else pml_data.tolist()
            cache = {'compra': vals, 'venta': vals}

        sides = ['compra', 'venta'] if side == 'both' else [side]
        result = {'preset': key, 'growth_seed': growth, 'horizon_years': horizon,
                  'sides': {}}

        try:
            for s in sides:
                values = np.asarray(cache[s], dtype=float)

                # PML: usamos suma anual para mantener consistencia con synthesize
                real_annual = _real_annual_from_hourly(values, agg='sum')
                if real_annual is not None:
                    t, annual_hist, base_year = real_annual
                    use_real = True
                else:
                    t, annual_hist = synthesize_annual_series(
                        values, n_years_history=hist_y, growth_seed=growth,
                        noise_pct=0.04)
                    base_year = 2024
                    use_real = False

                fitted = fit_all_trend_models(t, annual_hist, models=models)
                projection = project_25_years(values, fitted, horizon_years=horizon,
                                               base_year_label=base_year)
                best = best_trend_model(fitted)
                summary = []
                for k, m in fitted.items():
                    if 'error' in m:
                        summary.append({'model': k, 'error': m['error']}); continue
                    proj = projection.get(k, {})
                    summary.append({
                        'model':   k,
                        'kind':    m.get('kind'),
                        'r2':      m['metrics']['r2'],
                        'rmse':    m['metrics']['rmse'],
                        'mape':    m['metrics']['mape'],
                        'cagr':    proj.get('cagr'),
                        'final':   proj.get('annual', [None])[-1] if proj.get('annual') else None,
                    })
                for m in fitted.values():
                    m.pop('_predict', None)
                raw_s = cache[s]
                result['sides'][s] = {
                    'history_t': [int(x) for x in t],
                    'history_annual': [float(x) for x in annual_hist],
                    'fitted': fitted,
                    'projection': projection,
                    'summary': sorted(summary, key=lambda r: r.get('r2') or -1, reverse=True),
                    'best_model': best,
                    'raw_values': raw_s,
                    'raw_n': len(raw_s),
                    'used_real_annual': use_real,
                    'annual_kind': 'sum',
                }
            return jsonify(numpy_to_python(result))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/pml/forecast_hourly', methods=['POST'])
    @limiter.limit('5 per minute')
    def pml_forecast_hourly():
        body = request.get_json(silent=True) or {}
        sid = _require_sid(body)
        preset = body.get('preset', 'pml_ipo')
        models = body.get('models')

        sess = get_session(sid)
        # Data Hub primero, luego legacy
        pml_data, meta = _get_market_values(sess, 'pml', preset)
        if pml_data is not None and isinstance(pml_data, dict) and 'compra' in pml_data:
            cache = pml_data
        elif pml_data is not None:
            vals = pml_data.tolist() if isinstance(pml_data, np.ndarray) else pml_data
            cache = {'compra': vals, 'venta': vals}
        else:
            cache = (sess.get('pml') or {}).get(preset)
            if not cache:
                meta = PML_PRESETS().get(preset)
                if not meta:
                    return jsonify({'error': f'Preset desconocido: {preset}'}), 400
                fpath = _market_path(meta['file'])
                if not os.path.isfile(fpath):
                    return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
                data = read_pml(fpath)
                sess.setdefault('pml', {})[preset] = {
                    'compra': data['compra'].tolist(),
                    'venta': data['venta'].tolist(),
                    'meta': meta,
                }
                cache = sess['pml'][preset]

        try:
            from core.forecasting import fit_all_hourly_models
            result = {'preset': preset, 'sides': {}}
            for s in ['compra', 'venta']:
                if s not in cache: continue
                values = np.asarray(cache[s], dtype=float)
                t = np.arange(len(values))
                
                fitted = fit_all_hourly_models(t, values, models)
                
                summary = []
                for name, m in fitted.items():
                    summary.append({'model': name, **m['metrics']})
                    
                best = max(summary, key=lambda r: r.get('r2') or -1)['model'] if summary else None

                raw_s = cache[s]
                
                for m in fitted.values():
                    m['fitted_values'] = m['fitted']
                    m.pop('fitted', None)

                result['sides'][s] = {
                    'fitted': fitted,
                    'summary': sorted(summary, key=lambda r: r.get('r2') or -1, reverse=True),
                    'best_model': best,
                    'raw_values': raw_s,
                    'raw_n': len(raw_s),
                }
            return jsonify(numpy_to_python(result))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/interest/forecast', methods=['POST'])
    def interest_forecast():
        """Proyecta Tasa de Interés a 25 años usando históricos anuales REALES."""
        body = request.get_json(silent=True) or {}
        sid     = body.get('session_id')
        key     = body.get('preset', 'tiie_28d')
        horizon = int(body.get('horizon', Config.FORECAST_HORIZON_YEARS))
        models  = body.get('models')

        # Intentar Data Hub primero
        raw_values = None
        if sid:
            sess = get_session(sid)
            market = sess.get('market', {}).get('interest')
            if market and market.get('data') and 'values' in market['data']:
                raw_values = np.asarray(market['data']['values'], dtype=float)
                dates = market['data'].get('dates')

        if raw_values is not None and dates:
            # Usar datos del Data Hub — ya tenemos valores y fechas
            df = pd.DataFrame({'v': raw_values},
                               index=pd.to_datetime(dates))
            col_name = 'v'
        else:
            meta = INTEREST_PRESETS().get(key)
            if not meta: return jsonify({'error': f'Preset desconocido: {key}'}), 400
            fpath = _market_path(meta['file'])
            if not os.path.isfile(fpath): return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
            df = pd.read_csv(fpath, sep='\t')
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            col_name = df.columns[0]

        try:
            # Agregar anualmente (promedio)
            annual_df = df.resample('YE').mean().dropna()
            
            # Serie completa para la gráfica
            years_full = annual_df.index.year.values
            values_full = annual_df[col_name].values
            
            # Serie de entrenamiento (desde 2001)
            train_df = annual_df[annual_df.index.year >= 2001]
            if len(train_df) < 2:
                train_df = annual_df  # usar todo si no hay suficiente post-2001
            years_train = train_df.index.year.values
            values_train = train_df[col_name].values
            
            t_train = years_train - years_train[0]
            
            import core.forecasting as fc
            
            fitted = fc.fit_all_trend_models(t_train, values_train, models=models)
            
            if 'mean_reversion' in fitted and '_predict' not in fitted.get('mean_reversion', {}):
                mr = fc.fit_mean_reversion(t_train, values_train)
                yhat = mr['fitted']
                fitted['mean_reversion'] = {
                    'kind': mr['kind'],
                    'params': mr['params'],
                    'metrics': {
                        'r2': fc._r2(values_train, yhat),
                        'rmse': fc._rmse(values_train, yhat),
                        'mape': fc._mape(values_train, yhat)
                    },
                    '_predict': mr['predict'],
                    'fitted': [float(x) for x in yhat],
                }
            
            projection = fc.project_25_years(values_train, fitted, horizon_years=horizon, base_year_label=years_train[-1])
            best = fc.best_trend_model(fitted)
            
            summary = []
            for k, m in fitted.items():
                if 'error' in m:
                    summary.append({'model': k, 'error': m['error']}); continue
                proj = projection.get(k, {})
                summary.append({
                    'model':   k,
                    'r2':      m['metrics'].get('r2'),
                    'rmse':    m['metrics'].get('rmse'),
                    'mape':    m['metrics'].get('mape'),
                    'final':   proj.get('annual', [None])[-1] if proj.get('annual') else None,
                })
            
            for m in fitted.values(): m.pop('_predict', None)
            
            # Raw values for Forecast Studio
            all_raw = df[col_name].dropna().values.tolist()
            
            return jsonify(numpy_to_python({
                'preset': key, 'horizon_years': horizon,
                'history_years': [int(y) for y in years_full],
                'history_annual': [float(v) for v in values_full],
                'fitted': fitted, 'projection': projection,
                'summary': sorted(summary, key=lambda r: r.get('r2') or -1, reverse=True),
                'best_model': best,
                'raw_values': all_raw,
                'raw_n': len(all_raw),
                'used_real_annual': True,
                'annual_kind': 'mean',
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/fx/forecast', methods=['POST'])
    def fx_forecast():
        """Proyecta Tipo de Cambio a 25 años usando históricos anuales REALES."""
        body = request.get_json(silent=True) or {}
        sid     = body.get('session_id')
        key     = body.get('preset', 'usdmxn')
        depr    = float(body.get('depreciation', 2.5))
        horizon = int(body.get('horizon', Config.FORECAST_HORIZON_YEARS))
        models  = body.get('models')

        # Data Hub primero
        raw_values = None
        if sid:
            sess = get_session(sid)
            market = sess.get('market', {}).get('fx')
            if market and market.get('data') and 'values' in market['data']:
                raw_values = np.asarray(market['data']['values'], dtype=float)
                dates = market['data'].get('dates')

        if raw_values is not None and dates:
            df = pd.DataFrame({'v': raw_values},
                               index=pd.to_datetime(dates))
            col_name = 'v'
        else:
            meta = FX_PRESETS().get(key)
            if not meta: return jsonify({'error': f'Preset desconocido: {key}'}), 400
            fpath = _market_path(meta['file'])
            if not os.path.isfile(fpath): return jsonify({'error': f'Archivo no encontrado: {meta["file"]}'}), 404
            df = pd.read_csv(fpath, sep='\t')
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            col_name = df.columns[0]

        try:
            annual_df = df.resample('YE').mean().dropna()
            years = annual_df.index.year.values
            values = annual_df[col_name].values
            t = years - years[0]
            
            import core.forecasting as fc
            fitted = fc.fit_all_trend_models(t, values, models=models)
            
            projection = fc.project_25_years(values, fitted, horizon_years=horizon, base_year_label=years[-1])
            best = fc.best_trend_model(fitted)
            
            summary = []
            for k, m in fitted.items():
                if 'error' in m:
                    summary.append({'model': k, 'error': m['error']}); continue
                proj = projection.get(k, {})
                summary.append({
                    'model':   k,
                    'r2':      m['metrics'].get('r2'),
                    'rmse':    m['metrics'].get('rmse'),
                    'mape':    m['metrics'].get('mape'),
                    'final':   proj.get('annual', [None])[-1] if proj.get('annual') else None,
                })
            
            for m in fitted.values(): m.pop('_predict', None)
            
            # Raw values for Forecast Studio
            all_raw = df[col_name].dropna().values.tolist()
            
            return jsonify(numpy_to_python({
                'preset': key, 'horizon_years': horizon, 'depreciation': depr,
                'history_years': [int(y) for y in years],
                'history_annual': [float(v) for v in values],
                'fitted': fitted, 'projection': projection,
                'summary': sorted(summary, key=lambda r: r.get('r2') or -1, reverse=True),
                'best_model': best,
                'raw_values': all_raw,
                'raw_n': len(all_raw),
                'used_real_annual': True,
                'annual_kind': 'mean',
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    @app.route('/api/wind/annual_forecast', methods=['POST'])
    def wind_annual_forecast():
        """Proyecta velocidad media anual del viento a 25 años a partir de la
        serie cargada en sesión. Si hay >=2 años de histórico, usa la media
        anual real; si no, sintetiza una historia con growth_seed pequeño."""
        body = request.get_json(silent=True) or {}
        sid     = _require_sid(body)
        ws_col  = body.get('ws_col', 'wind_speed')
        growth  = float(body.get('growth', 0.005))
        horizon = int(body.get('horizon', Config.FORECAST_HORIZON_YEARS))
        hist_y  = int(body.get('history_years', Config.FORECAST_HISTORY_YEARS))
        models  = body.get('models')

        sess = get_session(sid)
        if 'df' not in sess:
            return jsonify({'error': 'No data loaded.'}), 400

        try:
            import core.forecasting as fc
            df = sess['df']
            if ws_col not in df.columns:
                ws_col = sess.get('target_col', 'wind_speed')
            if ws_col not in df.columns:
                return jsonify({'error': f'Columna {ws_col} no encontrada'}), 400

            series = pd.to_numeric(df[ws_col], errors='coerce').dropna()
            if len(series) == 0:
                return jsonify({'error': 'Serie de viento vacía'}), 400

            # Determinar año base (último timestamp si hay índice temporal)
            try:
                idx = pd.to_datetime(df.index)
                base_year = int(idx.year.max())
            except Exception:
                base_year = 2024

            # Si hay >=2 años reales, usar promedio anual real
            try:
                idx = pd.to_datetime(df.index)
                ser = pd.Series(series.values, index=idx[:len(series)])
                annual_real = ser.resample('YE').mean().dropna()
                if len(annual_real) >= 2:
                    years_rel = np.arange(len(annual_real))
                    annual_hist = annual_real.values
                    base_year = int(annual_real.index.year.max())
                    use_real = True
                else:
                    use_real = False
            except Exception:
                use_real = False

            if not use_real:
                base_mean = float(np.nanmean(series.values))
                rng = np.random.default_rng(42)
                years_rel = np.arange(hist_y + 1)
                factors = np.array([(1.0 + growth) ** (y - hist_y) for y in years_rel])
                noise = rng.normal(0, 0.02, size=len(years_rel))
                annual_hist = base_mean * factors * (1.0 + noise)

            fitted = fc.fit_all_trend_models(years_rel, annual_hist, models=models)
            projection = fc.project_25_years(
                series.values, fitted,
                horizon_years=horizon, base_year_label=base_year
            )
            best = fc.best_trend_model(fitted)

            summary = []
            for k, m in fitted.items():
                if 'error' in m:
                    summary.append({'model': k, 'error': m['error']}); continue
                proj = projection.get(k, {})
                summary.append({
                    'model': k,
                    'kind':  m.get('kind'),
                    'r2':    m['metrics'].get('r2'),
                    'rmse':  m['metrics'].get('rmse'),
                    'mape':  m['metrics'].get('mape'),
                    'cagr':  proj.get('cagr'),
                    'final': proj.get('annual', [None])[-1] if proj.get('annual') else None,
                })

            for m in fitted.values(): m.pop('_predict', None)

            base_value = float(np.nanmean(series.values))
            stats_summary = {
                'mean':   base_value,
                'std':    float(np.nanstd(series.values)),
                'min':    float(np.nanmin(series.values)),
                'max':    float(np.nanmax(series.values)),
                'p10':    float(np.nanpercentile(series.values, 10)),
                'p90':    float(np.nanpercentile(series.values, 90)),
                'n_hours': int(len(series)),
                'used_real_annual': bool(use_real),
            }

            raw_values = [float(x) for x in series.values]
            return jsonify(numpy_to_python({
                'horizon_years': horizon,
                'growth_seed':   growth,
                'history_years': [int(base_year - len(annual_hist) + 1 + i) for i in range(len(annual_hist))],
                'history_annual': [float(v) for v in annual_hist],
                'fitted': fitted, 'projection': projection,
                'summary': sorted(summary, key=lambda r: r.get('r2') or -1, reverse=True),
                'best_model': best,
                'stats': stats_summary,
                'unit': 'm/s',
                'raw_values': raw_values,
                'raw_n': len(raw_values),
                'annual_kind': 'mean',
                'used_real_annual': bool(use_real),
            }))
        except Exception as e:
            logger.exception('Error en %s', request.path)
            return jsonify({'error': str(e)}), 500

    return app

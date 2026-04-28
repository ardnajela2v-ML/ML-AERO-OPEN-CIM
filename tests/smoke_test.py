"""
AERO by OPEN CIM — smoke test.
Generates a synthetic wind time series and exercises all major API endpoints.
Run: python tests/smoke_test.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import io
import json
import time
import numpy as np
import pandas as pd
import requests

BASE = f"http://localhost:{os.environ.get('PORT', '5051')}"


def ok(name, r):
    if r.status_code == 200 and 'error' not in r.json():
        print(f'  ✓  {name}')
        return r.json()
    else:
        body = r.text[:300]
        print(f'  ✗  {name}  [{r.status_code}] {body}')
        return None


def make_csv():
    """2 years of synthetic hourly wind data at N16°W95°."""
    rng = np.random.default_rng(42)
    idx = pd.date_range('2022-01-01', '2023-12-31 23:00', freq='1h')
    n = len(idx)
    hour = idx.hour
    day  = idx.dayofyear

    # diurnal + seasonal signal + Weibull-ish distribution
    diurnal   = 1.5 * np.sin(2 * np.pi * (hour - 14) / 24)
    seasonal  = 2.0 * np.sin(2 * np.pi * (day - 15) / 365)
    base_wind = 7.5 + diurnal + seasonal
    wind_speed = rng.weibull(2.2, n) * (base_wind / 0.887)
    wind_speed = np.clip(wind_speed, 0, 35)

    wind_dir = (rng.uniform(0, 360, n) + 200) % 360
    temp     = 25 + 5 * np.sin(2 * np.pi * (day - 180) / 365) + rng.normal(0, 2, n)
    pressure = 1013 + rng.normal(0, 5, n)
    humidity = 60 + rng.uniform(-20, 20, n)

    df = pd.DataFrame({
        'datetime':   idx.strftime('%Y-%m-%d %H:%M:%S'),
        'wind_speed': np.round(wind_speed, 2),
        'wind_dir':   np.round(wind_dir, 1),
        'temperature':np.round(temp, 1),
        'pressure':   np.round(pressure, 1),
        'humidity':   np.round(humidity, 1),
    })
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode()


def run():
    print('\n═══ AERO Smoke Test ═══\n')

    # ── new session ──
    r = requests.post(f'{BASE}/api/session/new', json={})
    data = ok('new session', r)
    if not data:
        print('Cannot get session. Is the server running?'); return
    sid = data['session_id']
    print(f'     session_id = {sid}')

    csv_bytes = make_csv()

    # ── upload ──
    r = requests.post(f'{BASE}/api/data/upload',
                      files={'file': ('test_wind.csv', csv_bytes, 'text/csv')},
                      data={'session_id': sid, 'source': 'csv'})
    d = ok('data upload', r)
    if not d: return
    print(f'     rows={d["n_rows"]}, cols={d["columns"][:4]}')

    # ── quality check ──
    r = requests.post(f'{BASE}/api/data/quality',
                      json={'session_id': sid, 'target_col': 'wind_speed'})
    d = ok('data quality', r)
    if d:
        print(f'     QC overall={d["report"]["overall"]}, '
              f'Weibull k={d["report"].get("weibull",{}).get("k","—")}')

    # ── build features ──
    r = requests.post(f'{BASE}/api/features/build',
                      json={'session_id': sid, 'target_col': 'wind_speed',
                            'horizons': [1, 6, 24], 'test_frac': 0.2})
    d = ok('feature engineering', r)
    if d:
        s1 = d['summary']['1']
        print(f'     h=1h: {s1["n_samples"]} samples, {s1["n_features"]} features')

    # ── train models (h=1) ──
    r = requests.post(f'{BASE}/api/models/train',
                      json={'session_id': sid, 'horizon_h': 1})
    d = ok('start training h=1', r)
    if not d: return
    task_id = d['task_id']

    print('     waiting for training...', end='', flush=True)
    for _ in range(90):
        time.sleep(2)
        r = requests.get(f'{BASE}/api/models/status/{task_id}')
        s = r.json()
        print(f'.', end='', flush=True)
        if s['status'] == 'done':
            print(f' done ({s["progress"]}%)')
            break
        if s['status'] == 'error':
            print(f' ERROR: {s["message"]}'); return

    # ── comparison table ──
    r = requests.post(f'{BASE}/api/forecast/comparison',
                      json={'session_id': sid, 'horizon_h': 1})
    d = ok('comparison table', r)
    if d:
        print(f'     best={d["best"]}, rows={len(d["rows"])}')
        best = d['rows'][0]
        print(f'     {best["label"]}: RMSE={best["rmse"]}, R2={best["r2"]}, skill={best.get("skill_vs_persistence")}')

    # ── predictions ──
    r = requests.post(f'{BASE}/api/forecast/predictions',
                      json={'session_id': sid, 'horizon_h': 1})
    d = ok('predictions', r)

    # ── energy simulation ──
    r = requests.post(f'{BASE}/api/energy/compute', json={
        'session_id': sid,
        'turbine': {'builtin_model': 'V150_4.5'},
        'simulation': {
            'n_turbines': 5, 'availability': 0.97,
            'electrical_losses': 0.02, 'wake_losses': 0.03,
            'ws_height_m': 100.0, 'altitude_m': 200.0,
        }
    })
    d = ok('energy simulation', r)
    if d:
        s = d['summary']
        print(f'     CF={s["capacity_factor_pct"]}%, FLH={s["full_load_hours"]}, '
              f'E_net={s["energy_net_mwh"]} MWh')
        if d.get('aep'):
            print(f'     AEP P50={d["aep"].get("aep_p50_mwh")} MWh, '
                  f'P90={d["aep"].get("aep_p90_mwh")} MWh')

    # ── turbine comparison ──
    r = requests.post(f'{BASE}/api/energy/compare_turbines',
                      json={'session_id': sid, 'ws_height_m': 100.0})
    d = ok('turbine comparison', r)
    if d:
        for t in d['comparison']:
            print(f'     #{t["rank"]} {t["name"]}: CF={t["capacity_factor_pct"]}%')

    # ── AI assistant ──
    r = requests.post(f'{BASE}/api/ai/chat',
                      json={'session_id': sid, 'question': '¿Cuál es el mejor modelo?'})
    d = ok('AI assistant', r)
    if d:
        print(f'     AI: {d["answer"][:120]}…')

    print('\n═══ Smoke test complete ═══\n')


if __name__ == '__main__':
    run()

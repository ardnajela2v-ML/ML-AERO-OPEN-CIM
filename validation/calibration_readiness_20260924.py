"""Audit whether the saved project can calibrate financial/wake uncertainty.

Usage: python3 validation/calibration_readiness_20260924.py PROJECT.aeroproj
Prints JSON; never modifies the project. The exported 2018 hourly PML and
ranked layouts are the only observations inspected here.
"""
import calendar
import json
import statistics
import sys
import zipfile
from pathlib import Path


def audit(project_path, ranking_path):
    with zipfile.ZipFile(project_path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    session = manifest['session_state']
    park = session['park_configuration']
    wake_assumptions = manifest['client_state']['mcda']['lastSpatial']['assumptions']
    pml = park['market_models']['pml']
    observed = session['data_sources']['observed']
    prices = pml['hourly_profile_2018']
    alternatives = json.loads(Path(ranking_path).read_text())['alternatives']
    first = alternatives[0]
    month_means = []
    offset = 0
    for month in range(1, 13):
        hours = calendar.monthrange(int(pml['base_year']), month)[1] * 24
        month_means.append(statistics.mean(prices[offset:offset + hours]))
        offset += hours
    return {
        'project': str(project_path),
        'wind_resource': {
            'selected_year': observed.get('selected_year'),
            'period_start': observed.get('period_start'),
            'period_end': observed.get('period_end'),
            'timezone': observed.get('selection_timezone'),
            'ten_minute_rows': observed.get('rows'),
            'annual_interannual_uncertainty_estimable_from_project': False,
        },
        'pml': {
            'observed_calendar_years': [pml['base_year']],
            'n_observed_annual_means': 1,
            'hourly_values': len(prices),
            'hourly_source_values': pml.get('normalization', {}).get('input_hours'),
            'filled_hours': pml.get('normalization', {}).get('filled_hours'),
            'node': park.get('pml_node') or None,
            'pml_timezone_documented': False,
            'sale_scheme': park.get('sale_scheme'),
            'projection_policy': pml.get('projection_policy'),
            'future_annual_values': len(pml.get('annual', [])),
            'future_annual_distinct_values': len(set(pml.get('annual', []))),
            'observed_mean_usd_kwh': statistics.mean(prices),
            'observed_monthly_means_usd_kwh': month_means,
            'observed_hourly_min_usd_kwh': min(prices),
            'observed_hourly_max_usd_kwh': max(prices),
            'annual_volatility_estimable_from_project': False,
            'reason': 'One annual PML observation; within-year hourly spread is not year-to-year forecast error.',
        },
        'wake': {
            'model': wake_assumptions.get('wake_model'),
            'base_k': wake_assumptions.get('k_wake'),
            'base_ct': wake_assumptions.get('ct'),
            'ct_curve_available': wake_assumptions.get('wake_model_validity', {}).get('ct_curve_available'),
            'assumed_uncertainty_components_pct': first.get('uncertainty_components_pct'),
            'layout_wake_loss_pct_range': [min(a['wake_loss_pct'] for a in alternatives),
                                           max(a['wake_loss_pct'] for a in alternatives)],
            'observed_wake_model_errors': 0,
            'calibrated_wake_error_estimable_from_project': False,
            'reason': 'Differences among layouts and deterministic PyWake agreement do not measure model error.',
        },
        'decision': {
            'price_20pct_aep_10pct_wake_3pct_statistically_calibrated': False,
            'required_for_pml': 'NodeP and time-aligned multi-year hourly PML; use rolling-year out-of-sample validation for price trajectories.',
            'required_for_aep': 'Multi-year colocated wind or validated long-term reanalysis, including interannual variability and MCP uncertainty.',
            'required_for_wake': 'SCADA or equivalent observed wake deficits by speed, direction and turbulence; evaluate held-out periods and spatial layout effects.',
        },
    }


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    project = Path(sys.argv[1])
    ranking = root / 'exports/benchmark_horario_20260924/deferred_pipeline.json'
    print(json.dumps(audit(project, ranking), ensure_ascii=False, indent=2))

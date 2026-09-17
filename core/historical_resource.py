"""Selected historical resource used by spatial energy calculations.

Spatial scaling preserves paired speed/direction samples; it does not create
new measurements or extend the selected period.
"""
import numpy as np
from core.energy import extrapolate_wind_hellmann


def prepare_historical_wind(resource, target_height_m, require_direction=True):
    speed = np.asarray(resource.get('ws'), dtype=float).reshape(-1)
    direction = resource.get('wd')
    if require_direction and direction is None:
        raise ValueError('La fuente histórica requiere dirección para calcular estelas; no se sustituye por Weibull.')
    direction = np.asarray(direction, dtype=float).reshape(-1) if direction is not None else None
    if direction is not None and len(direction) != len(speed):
        raise ValueError('Velocidad y dirección históricas deben compartir las mismas fechas.')
    valid = np.isfinite(speed) & (speed >= 0)
    if direction is not None:
        valid &= np.isfinite(direction)
    if not valid.any():
        raise ValueError('No hay pares históricos válidos en el periodo elegido.')
    height = float(resource['measurement_height_m'])
    dt = float(resource.get('dt_hours', 1))
    if not np.isfinite(height) or height <= 0 or not np.isfinite(dt) or dt <= 0:
        raise ValueError('Altura o intervalo temporal histórico inválido.')
    ws = extrapolate_wind_hellmann(speed[valid], height, float(target_height_m))
    mean = float(np.mean(ws))
    if mean <= 0:
        raise ValueError('La media histórica debe ser positiva para el ajuste espacial.')
    provenance = {**(resource.get('provenance') or {}),
        'source_id': resource.get('source_id'), 'source_label': resource.get('source_label'),
        'method': 'historical_series_spatial_scaling',
        'rows_total': len(speed), 'rows_used': int(valid.sum()),
        'rows_excluded': int((~valid).sum()), 'dt_hours': dt,
        'valid_record_hours': float(valid.sum() * dt),
        'source_height_m': height, 'analysis_height_m': float(target_height_m),
        'reference_mean_ms': mean, 'gap_filling': False,
        'annualization': '8760_hours_from_valid_selected_samples',
        'spatial_method': 'local_map_mean / historical_mean_at_same_height',
        'direction_method': 'paired_historical_directions_unchanged',
        'wake_integration': 'empirical_joint_frequency_36_sectors_1_ms_bins'}
    result = {'ws': ws, 'wd': direction[valid] % 360 if direction is not None else None,
              'mean': mean, 'dt_hours': dt, 'provenance': provenance}
    for key in ('temperature_c', 'pressure_pa'):
        if resource.get(key) is not None:
            values = np.asarray(resource[key], dtype=float).reshape(-1)
            if len(values) != len(speed):
                raise ValueError('Temperatura/presión y viento deben compartir las mismas fechas.')
            result[key] = values[valid]
    return result


def historical_layout_simulation(points, turbine, resource, wind_grid, origin, **options):
    from core.farm import _sample_wind_grid, _xy_to_latlon, simulate_wind_farm
    multipliers = np.ones(len(points))
    if wind_grid:
        coords = np.asarray(points, dtype=float)
        lats, lons = _xy_to_latlon(coords[:, 0], coords[:, 1], origin[0], origin[1])
        values = [_sample_wind_grid(wind_grid, lat, lon) for lat, lon in zip(lats, lons)]
        if any(value is None or not np.isfinite(value) or value < 0 for value in values):
            raise ValueError('Falta viento espacial válido en una posición de turbina.')
        multipliers = np.asarray(values) / resource['mean']
    result = simulate_wind_farm(points, turbine,
        np.asarray(resource['ws']), np.asarray(resource['wd']),
        local_wind_multipliers=multipliers,
        sample_duration_hours=resource['dt_hours'], **options)
    result['production_source'] = {**resource['provenance'],
        'local_speed_multipliers': multipliers.tolist()}
    return result

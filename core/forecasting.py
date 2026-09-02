"""Lectura y normalización de series comerciales usadas por el flujo vigente.

La demanda se consume directamente como serie histórica. El PML conserva el
perfil horario observado de 2018 y lo replica sin modelado ni escalamiento.
"""
import numpy as np
import pandas as pd


def read_one_column(path):
    """Lee un archivo de texto con un valor numérico por línea."""
    vals = []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                vals.append(float(line.split(',')[0]))
            except ValueError:
                continue
    return np.array(vals, dtype=float)


def read_pml(path):
    """Lee un archivo PML con columnas compra, venta."""
    compra, venta = [], []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) < 2:
                continue
            try:
                compra.append(float(parts[0]))
                venta.append(float(parts[1]))
            except ValueError:
                continue
    return {'compra': np.array(compra), 'venta': np.array(venta)}


def normalize_fixed_hourly_profile(values, expected_hours=8760):
    """Normaliza un perfil anual sin inventar una tendencia temporal."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) > expected_hours:
        return values[:expected_hours], {
            'input_hours': int(len(values)),
            'output_hours': int(expected_hours),
            'method': 'truncate_after_expected_year',
            'filled_hours': 0,
        }
    missing = max(0, expected_hours - len(values))
    if missing > 24 or len(values) < expected_hours - 24:
        raise ValueError(
            f'El PML base requiere al menos {expected_hours - 24} horas; '
            f'el archivo contiene {len(values)}')
    if missing:
        fill = float(np.nanmean(values))
        values = np.concatenate([values, np.full(missing, fill)])
        method = 'annual_mean_fill_without_timestamp'
    else:
        method = 'none'
    return values, {
        'input_hours': int(expected_hours - missing),
        'output_hours': int(len(values)),
        'method': method,
        'filled_hours': int(missing),
    }


def hourly_to_dataframe(values, start='2018-01-01'):
    """Empaqueta un vector horario en un DataFrame indexado en el tiempo."""
    idx = pd.date_range(start=start, periods=len(values), freq='h')
    return pd.DataFrame({'value': values}, index=idx)


def base_year_stats(values):
    """Calcula estadísticos descriptivos del perfil horario cargado."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return {}
    df = hourly_to_dataframe(v)
    return {
        'n_hours': int(len(v)),
        'mean': float(np.mean(v)),
        'median': float(np.median(v)),
        'std': float(np.std(v, ddof=1)),
        'min': float(np.min(v)),
        'max': float(np.max(v)),
        'p10': float(np.percentile(v, 10)),
        'p90': float(np.percentile(v, 90)),
        'annual_sum': float(np.sum(v)),
        'load_factor': float(np.mean(v) / np.max(v)) if np.max(v) > 0 else None,
        'monthly': [float(x) for x in df.resample('ME')['value'].mean().values],
        'diurnal': [float(x) for x in df.groupby(df.index.hour)['value'].mean().values],
        'weekly': [float(x) for x in df.groupby(df.index.dayofweek)['value'].mean().values],
        'duration_curve': [float(x) for x in sorted(v, reverse=True)[::max(1, len(v) // 200)]],
    }

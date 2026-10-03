"""Lectura y normalización de series comerciales usadas por el flujo vigente.

La demanda se consume directamente como serie histórica. El PML conserva el
perfil horario observado (idealmente con fechas CENACE) y lo replica sin
modelado ni escalamiento.

Formatos PML admitidos
----------------------
1) Con fechas (preferido, export CENACE → WindPro)::

    fecha,hora,compra,venta
    2018-01-01,1,0.030524,0.030524

   ``hora`` es 1–24 (CENACE). Se construye timestamp como
   ``fecha + (hora-1) hours``.

2) Legado sin fechas (PML_IPO.txt / PML_usd.txt)::

    0.061686,0.061686
"""
from __future__ import annotations

from datetime import datetime, timedelta

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


def _parse_pml_fecha(token: str):
    token = token.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(token, fmt)
        except ValueError:
            continue
    return None


def _cenace_hora_to_timestamp(day: datetime, hora: int) -> datetime:
    """Hora CENACE 1 → 00:00 del día; 24 → 23:00; 25 → 24:00 (DST)."""
    return day + timedelta(hours=max(0, int(hora) - 1))


def read_pml(path):
    """Lee un archivo PML con columnas compra, venta.

    Si el archivo trae ``fecha`` y ``hora`` (cabecera o detectadas), también
    devuelve ``timestamps`` (array datetime64) alineado fila a fila.
    """
    compra, venta, timestamps = [], [], []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines:
        return {
            'compra': np.array([], dtype=float),
            'venta': np.array([], dtype=float),
            'timestamps': None,
        }

    first_parts = [p.strip() for p in lines[0].split(',')]
    lower = [p.lower() for p in first_parts]
    has_header = (
        'fecha' in lower or 'datetime' in lower or 'timestamp' in lower
        or (len(lower) >= 2 and lower[0] in {'compra', 'venta', 'pml'})
    )

    fecha_idx = hora_idx = compra_idx = venta_idx = None
    data_lines = lines
    if has_header:
        data_lines = lines[1:]
        if 'fecha' in lower:
            fecha_idx = lower.index('fecha')
        if 'hora' in lower:
            hora_idx = lower.index('hora')
        if 'datetime' in lower:
            fecha_idx = lower.index('datetime')
            hora_idx = None
        if 'timestamp' in lower:
            fecha_idx = lower.index('timestamp')
            hora_idx = None
        if 'compra' in lower:
            compra_idx = lower.index('compra')
        if 'venta' in lower:
            venta_idx = lower.index('venta')
        # Si no hay compra/venta nombradas, tomar las dos últimas numéricas
        if compra_idx is None or venta_idx is None:
            compra_idx = len(first_parts) - 2
            venta_idx = len(first_parts) - 1

    dated = has_header and fecha_idx is not None
    # Detección sin cabecera: primera columna parece fecha
    if not has_header and _parse_pml_fecha(first_parts[0]) is not None:
        dated = True
        fecha_idx, hora_idx = 0, 1
        compra_idx, venta_idx = 2, 3 if len(first_parts) > 3 else 2

    for line in data_lines:
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 2:
            continue
        try:
            if dated:
                day = _parse_pml_fecha(parts[fecha_idx])
                if day is None:
                    # ISO datetime completo en una sola columna
                    try:
                        ts = pd.to_datetime(parts[fecha_idx])
                        timestamps.append(ts.to_pydatetime())
                    except (ValueError, TypeError):
                        continue
                    c_i = compra_idx if compra_idx is not None else 1
                    v_i = venta_idx if venta_idx is not None else min(2, len(parts) - 1)
                    compra.append(float(parts[c_i]))
                    venta.append(float(parts[v_i if v_i < len(parts) else c_i]))
                    continue
                hora = 1
                if hora_idx is not None and hora_idx < len(parts):
                    hora = int(float(parts[hora_idx]))
                timestamps.append(_cenace_hora_to_timestamp(day, hora))
                c_i = compra_idx if compra_idx is not None else 2
                v_i = venta_idx if venta_idx is not None else 3
                if v_i >= len(parts):
                    v_i = c_i
                compra.append(float(parts[c_i]))
                venta.append(float(parts[v_i]))
            else:
                compra.append(float(parts[0]))
                venta.append(float(parts[1]))
        except (ValueError, TypeError, IndexError):
            continue

    result = {
        'compra': np.array(compra, dtype=float),
        'venta': np.array(venta, dtype=float),
        'timestamps': None,
    }
    if timestamps and len(timestamps) == len(compra):
        result['timestamps'] = np.array(timestamps, dtype='datetime64[ns]')
    return result


def normalize_fixed_hourly_profile(values, expected_hours=8760,
                                   timestamps=None):
    """Normaliza un perfil horario sin inventar una tendencia temporal.

    Si ``timestamps`` está presente y tiene la misma longitud que ``values``,
    se preserva el emparejamiento fecha↔precio. Series multi-año con fechas
    se conservan completas (no se truncan a 8760).
    """
    values = np.asarray(values, dtype=float)
    ts = None
    if timestamps is not None:
        ts = np.asarray(timestamps)
        if len(ts) != len(values):
            ts = None

    mask = np.isfinite(values)
    values = values[mask]
    if ts is not None:
        ts = ts[mask]

    # Multi-año fechado: conservar toda la serie
    if ts is not None and len(values) > expected_hours:
        meta = {
            'input_hours': int(len(values)),
            'output_hours': int(len(values)),
            'method': 'multi_year_keep_all_with_timestamps',
            'filled_hours': 0,
            'has_timestamps': True,
            'start': str(ts[0]),
            'end': str(ts[-1]),
        }
        return values, ts, meta

    if len(values) > expected_hours:
        meta = {
            'input_hours': int(len(values)),
            'output_hours': int(expected_hours),
            'method': 'truncate_after_expected_year',
            'filled_hours': 0,
            'has_timestamps': False,
        }
        return values[:expected_hours], None, meta

    missing = max(0, expected_hours - len(values))
    if missing > 24 or len(values) < expected_hours - 24:
        raise ValueError(
            f'El PML base requiere al menos {expected_hours - 24} horas; '
            f'el archivo contiene {len(values)}')
    if missing:
        fill = float(np.nanmean(values))
        values = np.concatenate([values, np.full(missing, fill)])
        if ts is not None and len(ts):
            last = pd.Timestamp(ts[-1])
            extra = np.array(
                [(last + pd.Timedelta(hours=i + 1)).to_datetime64()
                 for i in range(missing)],
                dtype='datetime64[ns]')
            ts = np.concatenate([ts, extra])
        method = (
            'annual_mean_fill_with_timestamp_extension'
            if ts is not None else 'annual_mean_fill_without_timestamp'
        )
    else:
        method = 'none'
    meta = {
        'input_hours': int(expected_hours - missing),
        'output_hours': int(len(values)),
        'method': method,
        'filled_hours': int(missing),
        'has_timestamps': ts is not None,
    }
    if ts is not None and len(ts):
        meta['start'] = str(ts[0])
        meta['end'] = str(ts[-1])
    return values, ts, meta


def hourly_to_dataframe(values, start='2018-01-01', timestamps=None):
    """Empaqueta un vector horario en un DataFrame indexado en el tiempo."""
    if timestamps is not None and len(timestamps) == len(values):
        idx = pd.to_datetime(timestamps)
    else:
        idx = pd.date_range(start=start, periods=len(values), freq='h')
    return pd.DataFrame({'value': values}, index=idx)


def base_year_stats(values, timestamps=None):
    """Calcula estadísticos descriptivos del perfil horario cargado."""
    raw = np.asarray(values, dtype=float)
    finite = np.isfinite(raw)
    v = raw[finite]
    if len(v) == 0:
        return {}
    ts = None
    if timestamps is not None and len(timestamps) == len(raw):
        ts = np.asarray(timestamps)[finite]
    df = hourly_to_dataframe(v, timestamps=ts)
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
        'start': str(df.index[0]) if len(df) else None,
        'end': str(df.index[-1]) if len(df) else None,
        'monthly': [float(x) for x in df.resample('ME')['value'].mean().values],
        'diurnal': [float(x) for x in df.groupby(df.index.hour)['value'].mean().values],
        'weekly': [float(x) for x in df.groupby(df.index.dayofweek)['value'].mean().values],
        'duration_curve': [float(x) for x in sorted(v, reverse=True)[::max(1, len(v) // 200)]],
    }

"""Reversible calendar-year views of a registered wind source (no gap filling)."""
import pandas as pd


def period_options(source, timezone='UTC'):
    frame = source.get('_full_df', source.get('df'))
    if frame is None or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError('La fuente requiere fechas para seleccionar un año')
    clock = pd.to_datetime(frame.index, utc=True).tz_convert(timezone)
    years = []
    for year in sorted(set(clock.year)):
        subset = frame.loc[clock.year == year]
        valid = int(pd.to_numeric(subset['wind_speed'], errors='coerce').notna().sum())
        if valid:
            years.append({'year': int(year), 'rows': len(subset), 'valid_rows': valid})
    return {'source_id': source['source_id'], 'label': source.get('label'),
            'year': source.get('selected_year'), 'timezone': timezone,
            'available_years': years, 'period_start': source.get('period_start'),
            'period_end': source.get('period_end'), 'selection_revision': source.get('selection_revision')}


def select_period(source, year, timezone='UTC'):
    options = period_options(source, timezone)
    if year is not None and (isinstance(year, bool) or not isinstance(year, int)
                            or year not in [row['year'] for row in options['available_years']]):
        raise ValueError('Elige un año disponible o todo el registro')
    original = source.get('_full_df', source['df'])
    clock = pd.to_datetime(original.index, utc=True).tz_convert(timezone)
    frame = original.copy() if year is None else original.loc[clock.year == year].copy()
    return {**source, '_full_df': original, 'df': frame, 'rows': len(frame),
            'selected_year': year, 'selection_timezone': timezone,
            'period_selection': 'full_available_record' if year is None else 'calendar_year',
            'years': sorted(set(int(value) for value in pd.to_datetime(frame.index, utc=True).tz_convert(timezone).year)),
            'period_start': frame.index.min().isoformat(), 'period_end': frame.index.max().isoformat()}

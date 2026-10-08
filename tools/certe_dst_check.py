#!/usr/bin/env python3
"""
tools/certe_dst_check.py
Verifica si el reloj de la torre CERTE M04 siguió o no el horario de verano mexicano,
contando registros de 10 min por día calendario en hora local cruda (sin conversión
a UTC) alrededor de los cambios de hora de 2018, 2019 y 2020.

Días de transición DST en México:
  Spring forward (primer domingo de abril): 2018-04-01, 2019-04-07, 2020-04-05
  Fall back     (último domingo de octubre): 2018-10-28, 2019-10-27
  (2020-10-25 fuera del rango de datos; los datos terminan ago-2020)

Interpretación de n_records por día:
  144 registros = 24 h × 6 reg/h  → reloj UTC-6 fijo (sin DST)
  138 registros = 23 h             → spring forward (DST observado)
  150 registros = 25 h             → fall back     (DST observado)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from config import Config

CERTE_FILE = Path(Config.CERTE_FOLDER) / 'M04_CERTE_CONSOLIDADO_2017-2020.txt'

if not CERTE_FILE.exists():
    print(f'ERROR: archivo no encontrado: {CERTE_FILE}')
    print(f'Verifica que CERTE_FOLDER={Config.CERTE_FOLDER!r} sea correcto.')
    sys.exit(1)

# Leer solo las columnas de fecha (YYYY, MM, DD); saltar filas Height y Units
df = pd.read_csv(
    CERTE_FILE, sep=r'\s+', header=0, skiprows=[1, 2],
    engine='python', usecols=['YYYY', 'MM', 'DD'],
)
df.columns = ['year', 'month', 'day']
df[['year', 'month', 'day']] = df[['year', 'month', 'day']].apply(
    pd.to_numeric, errors='coerce')
df = df.dropna(subset=['year', 'month', 'day'])
df['date'] = pd.to_datetime(df.rename(columns={'year': 'year', 'month': 'month', 'day': 'day'}))
counts = df.groupby('date').size().rename('n_records')

DST_DATES = {
    '2018 spring (2018-04-01, dom)': '2018-04-01',
    '2018 fall   (2018-10-28, dom)': '2018-10-28',
    '2019 spring (2019-04-07, dom)': '2019-04-07',
    '2019 fall   (2019-10-27, dom)': '2019-10-27',
    '2020 spring (2020-04-05, dom)': '2020-04-05',
}

print(f'\nArchivo: {CERTE_FILE.name}')
print(f'Registros totales leídos: {len(df):,}')
print(f'Rango de fechas locales: {df["date"].min().date()} a {df["date"].max().date()}\n')

print('─' * 72)
print(f'{"Día DST":<43} {"n_reg":>6} {"h_equiv":>8}  resultado')
print('─' * 72)

conclusions = []
for label, ds in DST_DATES.items():
    date = pd.Timestamp(ds)
    n = int(counts.get(date, 0))
    h = n / 6.0
    if n == 0:
        verdict = '─ sin datos'
    elif abs(h - 24) < 0.5:
        verdict = '✓ 24 h — UTC-6 fijo'
        conclusions.append(True)
    elif abs(h - 23) < 0.5:
        verdict = '✗ 23 h — spring forward (DST detectado)'
        conclusions.append(False)
    elif abs(h - 25) < 0.5:
        verdict = '✗ 25 h — fall back (DST detectado)'
        conclusions.append(False)
    else:
        verdict = f'? datos incompletos ({h:.1f} h)'
    print(f'{label:<43} {n:>6} {h:>8.1f}  {verdict}')

print('─' * 72)

print('\n─── Días vecinos ±1 para contexto ───────────────────────────────────────')
for label, ds in DST_DATES.items():
    d = pd.Timestamp(ds)
    print(f'\n  {label}:')
    for delta in (-1, 0, 1):
        nd = d + pd.Timedelta(days=delta)
        n = int(counts.get(nd, 0))
        h = n / 6.0
        mark = ' ← (día de transición)' if delta == 0 else ''
        print(f'    {nd.date()}  {n:>4} reg = {h:.1f} h{mark}')

print('\n' + '─' * 72)
if conclusions and all(conclusions):
    print(
        'CONCLUSIÓN: todos los días de transición DST tienen exactamente 24 horas.\n'
        'Confirma reloj UTC-6 fijo (sin DST) — consistente con la auditoría de\n'
        'hora solar de HANDOFF.md §7.'
    )
elif conclusions:
    print(
        'CONCLUSIÓN: se detectaron días con distinto número de horas que 24.\n'
        'El logger puede haber seguido DST total o parcialmente — revisar.'
    )
else:
    print('No se pudieron verificar días de transición (sin datos en esas fechas).')

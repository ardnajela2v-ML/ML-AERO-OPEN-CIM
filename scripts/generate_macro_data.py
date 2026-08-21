"""
Genera escenarios históricos interpolados de México (1993–2025):
  - TIIE 28 días (tasa de interés de referencia)
  - Tipo de cambio USD/MXN (FIX Banxico)
  - Inflación interanual INPC (%)

Fuentes usadas sólo como referencia de anclas: Banxico SIE, INEGI, FRED.
Estos archivos NO son observaciones diarias oficiales: interpolan puntos de
control y añaden ruido para pruebas. Una evaluación oficial debe sustituirlos
por exportaciones trazables de las fuentes primarias.
"""
import os, sys
import numpy as np
import pandas as pd
from datetime import datetime

# Agregar la ruta base al sys.path para poder importar config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

OUT_DIR = Config.MARKET_DATA_FOLDER
os.makedirs(OUT_DIR, exist_ok=True)

# ═══ TIIE 28d — Puntos de control históricos reales (anual avg) ═══════════
TIIE_ANCHORS = {
    '1993-01-01': 18.5, '1994-01-01': 16.0, '1994-12-20': 35.0,
    '1995-03-01': 75.0, '1995-06-01': 55.0, '1995-12-01': 48.0,
    '1996-06-01': 34.0, '1996-12-01': 27.0, '1997-06-01': 22.0,
    '1997-12-01': 20.5, '1998-06-01': 22.0, '1998-09-01': 38.0,
    '1998-12-01': 33.0, '1999-06-01': 22.0, '1999-12-01': 17.5,
    '2000-06-01': 16.0, '2000-12-01': 18.0, '2001-06-01': 12.0,
    '2001-12-01': 8.5, '2002-06-01': 7.5, '2002-12-01': 8.0,
    '2003-06-01': 6.5, '2003-12-01': 6.0, '2004-06-01': 6.5,
    '2004-12-01': 8.5, '2005-06-01': 9.75, '2005-12-01': 8.5,
    '2006-06-01': 7.3, '2006-12-01': 7.3, '2007-06-01': 7.5,
    '2007-12-01': 7.9, '2008-06-01': 7.9, '2008-10-01': 8.7,
    '2009-01-01': 8.3, '2009-06-01': 5.5, '2009-12-01': 4.9,
    '2010-06-01': 4.6, '2010-12-01': 4.9, '2011-06-01': 4.8,
    '2011-12-01': 4.8, '2012-06-01': 4.8, '2012-12-01': 4.85,
    '2013-06-01': 4.3, '2013-12-01': 3.8, '2014-06-01': 3.3,
    '2014-12-01': 3.3, '2015-06-01': 3.3, '2015-12-01': 3.55,
    '2016-06-01': 4.2, '2016-12-01': 5.8, '2017-06-01': 7.0,
    '2017-12-01': 7.6, '2018-06-01': 8.0, '2018-12-01': 8.6,
    '2019-06-01': 8.5, '2019-12-01': 7.5, '2020-03-01': 7.0,
    '2020-06-01': 5.5, '2020-09-01': 4.5, '2020-12-01': 4.3,
    '2021-06-01': 4.3, '2021-12-01': 5.5, '2022-03-01': 6.5,
    '2022-06-01': 7.75, '2022-09-01': 9.25, '2022-12-01': 10.5,
    '2023-03-01': 11.25, '2023-06-01': 11.25, '2023-12-01': 11.25,
    '2024-03-01': 11.25, '2024-06-01': 11.0, '2024-09-01': 10.75,
    '2024-12-01': 10.0, '2025-03-01': 9.5, '2025-04-01': 9.0,
}

# ═══ USD/MXN — Puntos de control históricos reales ════════════════════════
USDMXN_ANCHORS = {
    '1993-01-01': 3.10, '1993-12-01': 3.11, '1994-06-01': 3.38,
    '1994-12-19': 3.46, '1994-12-22': 5.0, '1995-03-01': 7.5,
    '1995-06-01': 6.2, '1995-11-01': 7.7, '1996-01-01': 7.6,
    '1996-06-01': 7.5, '1996-12-01': 7.85, '1997-06-01': 7.9,
    '1997-12-01': 8.1, '1998-06-01': 8.9, '1998-09-01': 10.2,
    '1998-12-01': 9.9, '1999-06-01': 9.5, '1999-12-01': 9.5,
    '2000-06-01': 9.6, '2000-12-01': 9.6, '2001-06-01': 9.1,
    '2001-09-11': 9.5, '2001-12-01': 9.2, '2002-06-01': 9.8,
    '2002-12-01': 10.3, '2003-06-01': 10.4, '2003-12-01': 11.2,
    '2004-06-01': 11.4, '2004-12-01': 11.15, '2005-06-01': 10.8,
    '2005-12-01': 10.6, '2006-06-01': 11.3, '2006-12-01': 10.8,
    '2007-06-01': 10.8, '2007-12-01': 10.9, '2008-06-01': 10.3,
    '2008-10-01': 13.2, '2009-03-01': 15.0, '2009-06-01': 13.2,
    '2009-12-01': 13.1, '2010-06-01': 12.8, '2010-12-01': 12.4,
    '2011-06-01': 11.8, '2011-09-01': 13.4, '2011-12-01': 13.9,
    '2012-06-01': 14.0, '2012-12-01': 12.9, '2013-06-01': 12.8,
    '2013-12-01': 13.1, '2014-06-01': 12.9, '2014-12-01': 14.7,
    '2015-06-01': 15.3, '2015-09-01': 17.0, '2015-12-01': 17.2,
    '2016-06-01': 18.5, '2016-11-01': 20.5, '2017-01-01': 21.6,
    '2017-06-01': 18.1, '2017-12-01': 19.7, '2018-06-01': 20.3,
    '2018-12-01': 19.7, '2019-06-01': 19.2, '2019-12-01': 18.9,
    '2020-03-23': 24.5, '2020-06-01': 22.0, '2020-12-01': 19.9,
    '2021-06-01': 19.9, '2021-12-01': 20.6, '2022-03-01': 20.4,
    '2022-06-01': 19.9, '2022-12-01': 19.4, '2023-03-01': 18.1,
    '2023-06-01': 17.1, '2023-12-01': 17.1, '2024-03-01': 17.0,
    '2024-06-01': 18.3, '2024-09-01': 19.7, '2024-12-01': 20.5,
    '2025-01-01': 20.3, '2025-04-01': 20.1,
}

# ═══ Inflación interanual (%) — puntos de control ═════════════════════════
INFL_ANCHORS = {
    '1993-01-01': 10.0, '1994-01-01': 7.0, '1995-01-01': 35.0,
    '1995-12-01': 52.0, '1996-12-01': 27.7, '1997-12-01': 15.7,
    '1998-12-01': 18.6, '1999-12-01': 12.3, '2000-12-01': 9.0,
    '2001-12-01': 4.4, '2002-12-01': 5.7, '2003-12-01': 4.0,
    '2004-12-01': 5.2, '2005-12-01': 3.3, '2006-12-01': 4.1,
    '2007-12-01': 3.8, '2008-06-01': 5.3, '2008-12-01': 6.5,
    '2009-12-01': 3.6, '2010-12-01': 4.4, '2011-12-01': 3.8,
    '2012-12-01': 3.6, '2013-12-01': 4.0, '2014-12-01': 4.1,
    '2015-12-01': 2.1, '2016-12-01': 3.4, '2017-06-01': 6.3,
    '2017-12-01': 6.8, '2018-12-01': 4.8, '2019-12-01': 2.8,
    '2020-06-01': 3.3, '2020-12-01': 3.2, '2021-06-01': 5.9,
    '2021-12-01': 7.4, '2022-06-01': 8.0, '2022-09-01': 8.7,
    '2022-12-01': 7.8, '2023-06-01': 5.1, '2023-12-01': 4.7,
    '2024-06-01': 4.9, '2024-12-01': 4.2, '2025-03-01': 3.8,
}


def build_daily_series(anchors: dict, start='1993-01-01', end='2025-04-28',
                       noise_pct=0.005, name='value'):
    """Interpola puntos de control a frecuencia diaria con ruido realista."""
    idx = pd.date_range(start, end, freq='D')
    anchor_dates = pd.to_datetime(list(anchors.keys()))
    anchor_vals  = np.array(list(anchors.values()), dtype=float)
    # Interpolar
    s = pd.Series(anchor_vals, index=anchor_dates).reindex(idx)
    s = s.interpolate(method='time').ffill().bfill()
    # Agregar ruido realista
    np.random.seed(42)
    noise = np.random.normal(0, noise_pct * s.values, len(s))
    s = s + noise
    s = s.clip(lower=0.01)  # nunca negativo
    return pd.DataFrame({'date': idx, name: np.round(s.values, 4)})


def main():
    print("Generando datos macroeconómicos de México (1993–2025)...\n")

    # 1. TIIE 28d
    df_tiie = build_daily_series(TIIE_ANCHORS, noise_pct=0.008, name='tiie_28d')
    fpath = os.path.join(OUT_DIR, 'TIIE_28D_HISTORICO.txt')
    df_tiie.to_csv(fpath, sep='\t', index=False)
    print(f"✓ TIIE 28d: {len(df_tiie)} registros → {fpath}")

    # 2. USD/MXN
    df_fx = build_daily_series(USDMXN_ANCHORS, noise_pct=0.003, name='usdmxn')
    fpath = os.path.join(OUT_DIR, 'USDMXN_HISTORICO.txt')
    df_fx.to_csv(fpath, sep='\t', index=False)
    print(f"✓ USD/MXN: {len(df_fx)} registros → {fpath}")

    # 3. Inflación
    df_infl = build_daily_series(INFL_ANCHORS, noise_pct=0.01, name='inflacion_pct')
    fpath = os.path.join(OUT_DIR, 'INFLACION_HISTORICO.txt')
    df_infl.to_csv(fpath, sep='\t', index=False)
    print(f"✓ Inflación: {len(df_infl)} registros → {fpath}")

    print(f"\nTotal: {len(df_tiie)+len(df_fx)+len(df_infl)} registros en {OUT_DIR}")


if __name__ == '__main__':
    main()

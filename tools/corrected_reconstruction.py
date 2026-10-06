#!/usr/bin/env python3
"""Serie reconstruida 1980-2024 con WS_80mA_mean sustituido en los dias
marcados por el detector diario (tools/sensor_qc_80m.py, umbral 15%), en
lugar de la correccion propuesta (no aplicada) para
windmcp.data._combine_height_sensors -- ver HANDOFF.md seccion 9.

No modifica windmcp/, core/ ni runs/mcp/ (el run oficial): solo lee
runs/mcp/20260926T185148Z_f23fc01b/historical_reconstruction.csv y el CERTE
crudo, y expone la serie corregida en memoria para que otros scripts de
tools/ la reutilicen sin repetir la logica de sustitucion.

Mismo patron ya usado en tools/capture_validation_2020_corrected.py, pero
sobre las 44.67 anios completos en vez de solo 2020 (los unicos dias
marcados en todo el periodo CERTE 2017-2020 son 2019-12-25 y los 123 dias de
2020 entre 2020-01-03 y 2020-06-05 -- ver sensor_qc_80m_output/).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS_DIR))

import sensor_qc_80m as qc  # noqa: E402

ACTIVE_RUN_RECONSTRUCTION_CSV = (
    ROOT / "runs" / "mcp" / "20260926T185148Z_f23fc01b" / "historical_reconstruction.csv"
)


def get_all_flagged_days(threshold: float = qc.MAIN_THRESHOLD) -> pd.DatetimeIndex:
    """Todos los dias marcados en todo el periodo CERTE (no solo 2020)."""
    a, b, _combined = qc.load_raw()
    daily_a, daily_b = qc.daily_means(a, b)
    ratio_daily = daily_a / daily_b
    return qc.flagged_days(ratio_daily, threshold)


def load_corrected_reconstruction() -> tuple[pd.DataFrame, pd.DatetimeIndex, dict]:
    """Devuelve (df, flagged_days, stats).

    df: historical_reconstruction.csv completo (1980-01-01 a 2024-08-31),
    indexado por timestamp UTC, con una columna nueva
    'long_term_wind_speed_corrected' -- igual a 'long_term_wind_speed' salvo
    en las horas 'CERTE_observed' que caen en un dia marcado, donde se
    sustituye por WS_80mA_mean (resampleado a 1h), igual altura de medicion
    (80 m) que la columna original, sin volver a correr el MCP.
    """
    df = pd.read_csv(
        ACTIVE_RUN_RECONSTRUCTION_CSV,
        usecols=["timestamp", "long_term_wind_speed", "wind_direction", "data_source"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()

    flagged = get_all_flagged_days()
    a, _b, _combined = qc.load_raw()
    a_hourly = a.resample("1h").mean()

    is_flagged_day = df.index.normalize().isin(flagged)
    is_observed = df["data_source"] == "CERTE_observed"
    swap_mask = is_flagged_day & is_observed

    corrected = df["long_term_wind_speed"].copy()
    replacement = a_hourly.reindex(df.index[swap_mask])
    corrected.loc[swap_mask] = replacement.to_numpy()
    n_missing_a = int(corrected.loc[swap_mask].isna().sum())
    # Si 80mA tampoco tiene dato esa hora (muy raro, ver monthly_bias output),
    # no se inventa un valor: se deja el original en vez de un NaN nuevo.
    corrected.loc[swap_mask] = corrected.loc[swap_mask].where(
        corrected.loc[swap_mask].notna(), df.loc[swap_mask, "long_term_wind_speed"])
    df["long_term_wind_speed_corrected"] = corrected

    stats = {
        "n_flagged_days": int(len(flagged)),
        "flagged_range": (str(flagged.min().date()), str(flagged.max().date())) if len(flagged) else (None, None),
        "n_hours_total": int(len(df)),
        "n_hours_swapped": int(swap_mask.sum()),
        "n_hours_swap_missing_a": n_missing_a,
    }
    return df, flagged, stats


if __name__ == "__main__":
    df, flagged, stats = load_corrected_reconstruction()
    print(f"Dias marcados (todo el periodo CERTE): {stats['n_flagged_days']} "
          f"({stats['flagged_range'][0]} -> {stats['flagged_range'][1]})")
    print(f"Horas totales: {stats['n_hours_total']}, horas sustituidas: {stats['n_hours_swapped']}, "
          f"sin dato 80mA para sustituir: {stats['n_hours_swap_missing_a']}")
    delta = (df["long_term_wind_speed_corrected"] - df["long_term_wind_speed"]).abs()
    print(f"Max |delta| ws: {delta.max():.3f} m/s, horas con delta>0: {int((delta > 1e-9).sum())}")

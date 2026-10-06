#!/usr/bin/env python3
"""Precio capturado 2017-2023, layout de referencia (turbina unica en
(0,0), Clipper 2.5 MW/89 m, altura de buje 80 m -- misma convencion que
tools/capture_validation.py), serie de viento con WS_80mA_mean sustituido
en los dias marcados (tools/corrected_reconstruction.py).

Compara dos formas de emparejar precio y viento para cada anio Y:
  - "fecha": PML real del mismo anio Y, por timestamp UTC exacto.
  - "hour_of_year" (metodo actual en api.py/core.farm_mcda -- perfil fijo de
    UN anio replicado via hour_of_year(t) % 8760): se usa el perfil de PML
    2018 (nodo 02JUD115) como el "perfil fijo" que hoy se guarda en
    hourly_profile_2018 y se aplica a los mismos timestamps de cada anio Y.

Ambas ramas usan exactamente la misma energia por hora (mismo viento, mismo
turbina, sin estela) -- solo cambia que precio se le empareja a cada hora.
Eso aisla cuanto pesa la correlacion dia a dia real vs. imponer la forma de
2018 sobre el viento de otro anio.

No modifica windmcp/, core/ ni runs/mcp/: solo lee y escribe bajo tools/.

Salida: tools/capture_dated_vs_hourofyear_output/
  capture_by_year.csv   1 fila por anio: capture_ratio dated vs hour_of_year,
                         precio realizado, correlacion diaria energia-precio.
  capture_ratio_range.csv  rango y desviacion de capture_ratio por metodo.
  summary.txt
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

import capture_validation as cv  # noqa: E402
from core.energy import extrapolate_wind_hellmann  # noqa: E402
from core.forecasting import read_pml  # noqa: E402
from corrected_reconstruction import load_corrected_reconstruction  # noqa: E402

YEARS = list(range(2017, 2024))
REFERENCE_PROFILE_YEAR = 2018
HUB_HEIGHT_M = 80.0   # "Altura de buje: 80 m" del proyecto PARQEU1, no el
                      # heuristico de from_file (78 m) -- misma convencion
                      # que capture_validation.py (target_height_m=80.0).
SOURCE_HEIGHT_M = 80.0
OUT_DIR = TOOLS_DIR / "capture_dated_vs_hourofyear_output"


def load_pml_series() -> pd.Series:
    pml = read_pml(str(cv.PML_JUD_FILE))
    ts = pd.DatetimeIndex(pml["timestamps_utc"])
    venta = pd.Series(np.asarray(pml["venta"], dtype=float), index=ts)
    venta = venta[~venta.index.duplicated(keep="first")].sort_index()
    return venta


def energy_kwh_series(ws_hub: pd.Series, turbine) -> pd.Series:
    power_kw = turbine.power_kw_density_corrected(ws_hub.to_numpy(), 1.225)
    return pd.Series(power_kw, index=ws_hub.index)  # dt=1h -> kWh == kW


def capture_metrics(energy_kwh: pd.Series, price: pd.Series) -> dict:
    revenue = float(np.sum(energy_kwh.to_numpy() * price.to_numpy()))
    total_energy = float(np.sum(energy_kwh.to_numpy()))
    avg_flat = float(np.mean(price.to_numpy()))
    avg_realized = revenue / total_energy if total_energy > 0 else np.nan
    capture_ratio = avg_realized / avg_flat if avg_flat > 0 else np.nan
    daily_energy = energy_kwh.resample("1D").sum()
    daily_price = price.resample("1D").mean()
    common = daily_energy.index.intersection(daily_price.index)
    corr_daily = (
        float(np.corrcoef(daily_energy.loc[common], daily_price.loc[common])[0, 1])
        if len(common) >= 3 else np.nan
    )
    return {
        "n_hours": int(len(energy_kwh)),
        "avg_flat_price_usd_kwh": round(avg_flat, 6),
        "avg_realized_price_usd_kwh": round(avg_realized, 6),
        "capture_ratio": round(capture_ratio, 4),
        "daily_corr_energy_price": round(corr_daily, 4) if np.isfinite(corr_daily) else None,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    turbine = cv.load_turbine()
    print(f"Turbina: {turbine.name if hasattr(turbine,'name') else 'clipper25'}, "
          f"altura de buje forzada a {HUB_HEIGHT_M} m (proyecto PARQEU1)")

    print("Construyendo serie de viento corregida (80mA en dias marcados) ...")
    df, flagged, stats = load_corrected_reconstruction()
    valid = np.isfinite(df["long_term_wind_speed_corrected"]) & (df["long_term_wind_speed_corrected"] >= 0)
    ws_80m = df.loc[valid, "long_term_wind_speed_corrected"]
    ws_hub = pd.Series(
        extrapolate_wind_hellmann(ws_80m.to_numpy(), SOURCE_HEIGHT_M, HUB_HEIGHT_M),
        index=ws_80m.index,
    )

    print("Cargando PML 02JUD115 (venta, UTC) ...")
    pml_venta = load_pml_series()
    reference_profile = cv.build_price_array(REFERENCE_PROFILE_YEAR)

    rows = []
    for year in YEARS:
        wind_y = ws_hub.loc[f"{year}-01-01":f"{year}-12-31 23:00"]
        price_dated_y = pml_venta.reindex(wind_y.index)
        common_idx = wind_y.index[price_dated_y.notna().to_numpy()]
        n_missing_price = int(len(wind_y) - len(common_idx))

        wind_common = wind_y.loc[common_idx]
        price_dated_common = price_dated_y.loc[common_idx]
        hoy = cv.hour_of_year_from_index(common_idx)
        price_hoy_common = pd.Series(reference_profile[hoy], index=common_idx)

        energy = energy_kwh_series(wind_common, turbine)
        m_dated = capture_metrics(energy, price_dated_common)
        m_hoy = capture_metrics(energy, price_hoy_common)

        rows.append({
            "year": year,
            "n_hours_wind": int(len(wind_y)),
            "n_hours_missing_price": n_missing_price,
            "n_hours_common": m_dated["n_hours"],
            "capture_ratio_dated": m_dated["capture_ratio"],
            "capture_ratio_hour_of_year_2018profile": m_hoy["capture_ratio"],
            "diff_capture_ratio_pct_points": round(
                100.0 * (m_hoy["capture_ratio"] - m_dated["capture_ratio"]), 2),
            "avg_realized_price_dated_usd_kwh": m_dated["avg_realized_price_usd_kwh"],
            "avg_realized_price_hour_of_year_usd_kwh": m_hoy["avg_realized_price_usd_kwh"],
            "avg_flat_price_dated_usd_kwh": m_dated["avg_flat_price_usd_kwh"],
            "avg_flat_price_hour_of_year_usd_kwh": m_hoy["avg_flat_price_usd_kwh"],
            "daily_corr_energy_price_dated": m_dated["daily_corr_energy_price"],
            "daily_corr_energy_price_hour_of_year": m_hoy["daily_corr_energy_price"],
        })
        print(f"{year}: capture_dated={m_dated['capture_ratio']:.4f}  "
              f"capture_hour_of_year={m_hoy['capture_ratio']:.4f}  "
              f"corr_dated={m_dated['daily_corr_energy_price']}  "
              f"corr_hoy={m_hoy['daily_corr_energy_price']}")

    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / "capture_by_year.csv", index=False)

    range_rows = []
    for col, label in [("capture_ratio_dated", "fecha_real"),
                        ("capture_ratio_hour_of_year_2018profile", "hour_of_year_perfil_2018")]:
        vals = out[col].to_numpy()
        range_rows.append({
            "method": label, "n_years": len(vals),
            "min": round(float(np.min(vals)), 4), "max": round(float(np.max(vals)), 4),
            "range": round(float(np.max(vals) - np.min(vals)), 4),
            "mean": round(float(np.mean(vals)), 4), "std": round(float(np.std(vals, ddof=1)), 4),
        })
    range_df = pd.DataFrame(range_rows)
    range_df.to_csv(OUT_DIR / "capture_ratio_range.csv", index=False)

    with open(OUT_DIR / "summary.txt", "w") as f:
        f.write("PRECIO CAPTURADO 2017-2023 -- emparejado por fecha vs hour_of_year (perfil 2018)\n")
        f.write("Layout de referencia: turbina unica Clipper 2.5MW/89m @ 80 m, sin estela\n")
        f.write("Serie de viento corregida (80mA en dias marcados)\n")
        f.write("=" * 78 + "\n\n")
        f.write(out.to_string(index=False) + "\n\n")
        f.write("-- Rango y desviacion de razon de captura por anio (2017-2023) --\n")
        f.write(range_df.to_string(index=False) + "\n")

    print("\nListo. Salidas en", OUT_DIR)


if __name__ == "__main__":
    main()

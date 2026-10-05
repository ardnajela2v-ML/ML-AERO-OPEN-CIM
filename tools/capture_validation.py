#!/usr/bin/env python3
"""Validacion de captura: precio capturado real, observado vs predicho MCP,
para 2019 y 2020.

Decisiones ya confirmadas con el usuario (no se re-evaluan aqui):
  - Fuente de reanalisis: MERRA-2, SIN desplazamiento de lag. La auditoria de
    tiempos (tools/audit_timing.py) encontro un desfase de ~-1h solo en ERA5,
    no en MERRA-2 (ver HANDOFF.md, seccion "Auditoria de tiempos") -- no hay
    evidencia suficiente para tocar nada, y MERRA-2 no muestra el patron.
  - Completitud: solo horas CERTE M04 con 6/6 muestras de 10 min (el filtro
    mas estricto de los 4 evaluados en la auditoria de tiempos).
  - PML: nodo JUD (mercado_pml/PML_2017-2024 02JUD115.txt), ya en UTC (Fase 1,
    core/forecasting.py::read_pml).

Reutiliza codigo de produccion existente, no lo reimplementa:
  - windmcp.service.run_mcp_validation: mismo pipeline de QC + ajuste de
    altura + entrenamiento (2018) + prediccion que uso el run MCP activo
    (20260926T185148Z_f23fc01b). Para 2019 se reutiliza DIRECTAMENTE el CSV
    que ese run ya escribio (no se reentrena); para 2020 se invoca la misma
    funcion con test_year=2020 (mismo train_year=2018, misma seleccion de
    metodo por holdout dentro de 2018 -- se verifica en tiempo de ejecucion
    que vuelve a elegir "ml_random_forest", y se advierte si no).
  - IMPORTANTE: el archivo "<ref>_ml_forward.csv" del run activo NO es el que
    se usa aqui -- ese corresponde a "ml_method_selection" (una comparacion
    ML-only que para 2018 elige gradient_boosting). El modelo realmente
    seleccionado para reconstruccion es "ml_random_forest"
    (result.json::speed_model_selection.selected_method), cuyas predicciones
    estan en "<ref>_forward.csv" (sin "_ml_"). Se verifico re-calculando R2
    desde ese CSV (0.7638, coincide con ml_random_forest 0.7625 en
    ml_model_comparison_2019.csv, no con gradient_boosting 0.7923).
  - core.farm.chronological_revenue: el cruce generacion horaria x precio
    horario, tal cual se implemento para el feature "ingreso real"
    (commit 2b19b6a). No se reimplementa el calculo de ingreso aqui.
  - core.energy.PowerCurve.from_file: curva Clipper 2.5 MW / 89 m
    (aerogeneradores/clipper25_p2500_r44.5.csv) -- misma clase de turbina que
    la tesis doctoral del usuario (Clipper C89/C93), elegida solo porque es
    un punto de comparacion razonable y disponible en el repo; la magnitud
    absoluta de MWh/ingreso no es el objetivo de esta validacion, solo la
    comparacion relativa observado-vs-predicho.
  - core.forecasting.read_pml: lectura del PML JUD.

No modifica windmcp/ ni core/. Si crea una corrida MCP nueva bajo
tools/capture_validation_output/mcp_run_test_year_2020/ (efecto normal de
llamar run_mcp_validation), separada de runs/mcp/ para no mezclarla con el
historial de corridas "oficiales".

Convencion de layout: turbina unica en (0,0) -- con n=1 no hay estela
(chronological_revenue salta el calculo de razon por sector cuando n<=1), asi
que `wd` es irrelevante aqui y se pasa en ceros.

Salidas (tools/capture_validation_output/):
  capture_summary.csv         1 fila por (year, branch) con revenue/aep/precio
                               capturado/capture_ratio.
  hourly_bias_by_local_hour.csv  sesgo y correlacion ws_pred-ws_obs por hora
                               local (UTC-6), por year.
  bootstrap_capture_diff.csv  bootstrap por bloques de dia de la diferencia
                               de precio capturado y de la correlacion
                               ws_obs vs ws_pred, por year.
  summary.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.energy import PowerCurve  # noqa: E402
from core.farm import chronological_revenue  # noqa: E402
from core.forecasting import read_pml  # noqa: E402
from windmcp.data import read_certe_files  # noqa: E402
from windmcp.service import run_mcp_validation  # noqa: E402

CERTE_FILE = "/Users/ale/Desktop/2222dose/copiaunoMCPSOFT/certe/M04_CERTE_CONSOLIDADO_2017-2020.txt"
OBS_TIMEZONE = "Etc/GMT+6"
TARGET_COLUMN = "WS_80mA_mean"
MERRA2_FILE = ROOT / "DATOS DE METEREOLOGICOS DE parques" / "UnionWindnavigator_MERRA2_N16,500_W95,000.txt"
PML_JUD_FILE = ROOT / "mercado_pml" / "PML_2017-2024 02JUD115.txt"
POWER_CURVE_FILE = ROOT / "aerogeneradores" / "clipper25_p2500_r44.5.csv"

ACTIVE_RUN_DIR = ROOT / "runs" / "mcp" / "20260926T185148Z_f23fc01b"
ACTIVE_RUN_2019_FORWARD_CSV = ACTIVE_RUN_DIR / "UnionWindnavigator_MERRA2_N16_500_W95_000.txt_forward.csv"
EXPECTED_RECONSTRUCTION_METHOD = "ml_random_forest"

OUT_DIR = Path(__file__).resolve().parent / "capture_validation_output"
YEARS = [2019, 2020]
N_BOOTSTRAP = 1000
BOOTSTRAP_SEED = 20261004
MIN_PAIRS_FOR_STATS = 30


def load_certe_completeness() -> pd.Series:
    """Igual que en audit_timing.py: completitud de muestras 10 min por hora."""
    frame, meta = read_certe_files([CERTE_FILE], target_column=TARGET_COLUMN, timezone=OBS_TIMEZONE)
    ws = pd.to_numeric(frame["wind_speed_obs"], errors="coerce")
    return ws.resample("1h").count()


def load_forward_2019() -> pd.DataFrame:
    df = pd.read_csv(ACTIVE_RUN_2019_FORWARD_CSV)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index("timestamp")[["observed_wind_speed", "predicted_wind_speed"]]


def generate_forward_2020() -> pd.DataFrame:
    output_dir = OUT_DIR / "mcp_run_test_year_2020"
    request = {
        "observation_files": [CERTE_FILE],
        "reference_files": [str(MERRA2_FILE)],
        "target_column": TARGET_COLUMN,
        "observation_timezone": OBS_TIMEZONE,
        "target_height_m": 80.0,
        "train_year": 2018,
        "test_year": 2020,
        "ranking_year": 2018,
        "max_wind_speed": 70.0,
        "max_jump": 15.0,
        "max_persistence": 6,
        "run_id": "capture_validation_test_year_2020",
        "output_dir": str(output_dir),
    }
    result = run_mcp_validation(request)
    selected_method = result["speed_model_selection"]["selected_method"]
    if selected_method != EXPECTED_RECONSTRUCTION_METHOD:
        print("ADVERTENCIA: para test_year=2020 el metodo seleccionado fue '%s', "
              "no '%s' como en el run activo. Los resultados usan el modelo "
              "realmente elegido, no se fuerza random_forest." % (selected_method, EXPECTED_RECONSTRUCTION_METHOD))
    else:
        print("OK: test_year=2020 vuelve a seleccionar '%s' (mismo modelo que el run activo)." % selected_method)
    forward_path = result["artifacts"][result["selected_reference"] + "_forward"]
    df = pd.read_csv(forward_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index("timestamp")[["observed_wind_speed", "predicted_wind_speed"]]


def verify_2019_method() -> None:
    comparison_path = ACTIVE_RUN_DIR / "UnionWindnavigator_MERRA2_N16_500_W95_000.txt_ml_model_comparison_2019.csv"
    comparison = pd.read_csv(comparison_path).set_index("method")
    forward = load_forward_2019()
    valid = forward.dropna()
    r2_forward = float(np.corrcoef(valid["observed_wind_speed"], valid["predicted_wind_speed"])[0, 1] ** 2)
    r2_rf = float(comparison.loc["ml_random_forest", "R2"])
    r2_gb = float(comparison.loc["ml_gradient_boosting", "R2"])
    closer_to_rf = abs(r2_forward - r2_rf) < abs(r2_forward - r2_gb)
    if closer_to_rf:
        print("OK: el forward.csv de 2019 corresponde a ml_random_forest "
              "(R2 recomputado=%.4f vs ml_random_forest=%.4f, ml_gradient_boosting=%.4f)." %
              (r2_forward, r2_rf, r2_gb))
    else:
        print("ADVERTENCIA: el forward.csv de 2019 parece corresponder a "
              "ml_gradient_boosting, no a ml_random_forest -- revisar antes de seguir.")


def build_price_array(year: int) -> np.ndarray:
    pml = read_pml(str(PML_JUD_FILE))
    timestamps_utc = pd.DatetimeIndex(pml["timestamps_utc"])
    venta = np.asarray(pml["venta"], dtype=float)
    mask = timestamps_utc.year == year
    ts_year = timestamps_utc[mask]
    price_year = venta[mask]
    day_of_year = np.minimum(ts_year.dayofyear, 365) - 1
    idx = (day_of_year * 24 + ts_year.hour) % 8760
    arr = np.full(8760, np.nan, dtype=float)
    order = np.argsort(ts_year.values)
    for position in order:
        arr[idx[position]] = price_year[position]
    n_missing = int(np.isnan(arr).sum())
    if n_missing:
        print("ADVERTENCIA: %d/8760 horas de precio PML faltantes para %d; "
              "se rellenan con el precio medio del año." % (n_missing, year))
        arr[np.isnan(arr)] = np.nanmean(arr)
    return arr


def hour_of_year_from_index(idx: pd.DatetimeIndex) -> np.ndarray:
    """Misma formula que api.py (linea ~5711): day_of_year tope 365, sin
    reindexar explícitamente Feb-29 -- se reutiliza tal cual, no se reinventa."""
    day_of_year = np.minimum(idx.dayofyear, 365) - 1
    return ((day_of_year * 24 + idx.hour) % 8760).astype(int)


def load_turbine() -> "PowerCurve":
    return PowerCurve.from_file(str(POWER_CURVE_FILE))


def chronological_result(ws: np.ndarray, idx: pd.DatetimeIndex, price_array: np.ndarray, turbine) -> dict:
    hoy = hour_of_year_from_index(idx)
    wd = np.zeros(len(ws))
    return chronological_revenue(
        layout_xy=np.array([[0.0, 0.0]]),
        turbine=turbine,
        ws=ws, wd=wd, hour_of_year=hoy,
        price_hourly_usd_kwh=price_array,
    )


def block_bootstrap(obs: np.ndarray, pred: np.ndarray, idx: pd.DatetimeIndex,
                     price_array: np.ndarray, turbine, n_boot: int, seed: int) -> dict:
    day_key = idx.normalize()
    unique_days = pd.unique(day_key)
    by_day = {
        d: {
            "obs": obs[day_key == d],
            "pred": pred[day_key == d],
            "idx": idx[day_key == d],
        }
        for d in unique_days
    }

    observed_corr = float(np.corrcoef(obs, pred)[0, 1])
    observed_capture_obs = chronological_result(obs, idx, price_array, turbine)["avg_realized_price_usd_kwh"]
    observed_capture_pred = chronological_result(pred, idx, price_array, turbine)["avg_realized_price_usd_kwh"]
    observed_capture_diff = observed_capture_pred - observed_capture_obs

    rng = np.random.default_rng(seed)
    corr_samples = np.empty(n_boot)
    capture_diff_samples = np.empty(n_boot)
    for i in range(n_boot):
        sample_days = rng.choice(unique_days, size=len(unique_days), replace=True)
        o = np.concatenate([by_day[d]["obs"] for d in sample_days])
        p = np.concatenate([by_day[d]["pred"] for d in sample_days])
        t = pd.DatetimeIndex(np.concatenate([by_day[d]["idx"].values for d in sample_days]))
        corr_samples[i] = np.corrcoef(o, p)[0, 1]
        cap_o = chronological_result(o, t, price_array, turbine)["avg_realized_price_usd_kwh"]
        cap_p = chronological_result(p, t, price_array, turbine)["avg_realized_price_usd_kwh"]
        capture_diff_samples[i] = cap_p - cap_o

    def ci(samples, observed):
        lo, hi = np.percentile(samples, [2.5, 97.5])
        return {
            "observed": observed, "boot_mean": float(np.mean(samples)),
            "ci_2_5": float(lo), "ci_97_5": float(hi),
            "significant_0_excluded": bool(lo > 0 or hi < 0),
        }

    return {
        "n_days": len(unique_days), "n_boot": n_boot,
        "correlation": ci(corr_samples, observed_corr),
        "capture_price_diff_usd_kwh": ci(capture_diff_samples, observed_capture_diff),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Verificando que el forward.csv de 2019 sea ml_random_forest (no ml_gradient_boosting) ...")
    verify_2019_method()

    print("\nCargando completitud CERTE M04 (10 min) ...")
    completeness = load_certe_completeness()

    print("Cargando forward 2019 (reutilizado del run activo) ...")
    forward_2019 = load_forward_2019()
    print("Generando forward 2020 (run_mcp_validation, test_year=2020) ...")
    forward_2020 = generate_forward_2020()
    forwards = {2019: forward_2019, 2020: forward_2020}

    turbine = load_turbine()

    summary_rows = []
    bias_rows = []
    bootstrap_rows = []

    for year in YEARS:
        price_array = build_price_array(year)
        complete_hours = completeness[(completeness.index.year == year) & (completeness == 6)].index

        forward = forwards[year].dropna(subset=["observed_wind_speed", "predicted_wind_speed"])
        common_idx = forward.index.intersection(complete_hours)
        common_idx = common_idx.sort_values()
        print(f"\nAño {year}: {len(forward)} horas con predicción MCP, "
              f"{len(complete_hours)} horas CERTE con 6/6 muestras, "
              f"{len(common_idx)} horas comunes usadas.")

        obs = forward.loc[common_idx, "observed_wind_speed"].to_numpy()
        pred = forward.loc[common_idx, "predicted_wind_speed"].to_numpy()

        result_obs = chronological_result(obs, common_idx, price_array, turbine)
        result_pred = chronological_result(pred, common_idx, price_array, turbine)
        for branch, result in [("observado", result_obs), ("predicho_merra2", result_pred)]:
            summary_rows.append({"year": year, "branch": branch, **result})

        overall_r = float(np.corrcoef(obs, pred)[0, 1]) if len(obs) >= MIN_PAIRS_FOR_STATS else np.nan
        overall_bias = float(np.mean(pred - obs))
        overall_rmse = float(np.sqrt(np.mean((pred - obs) ** 2)))
        print(f"  ws_obs vs ws_pred: r={overall_r:.4f}, bias={overall_bias:+.3f} m/s, rmse={overall_rmse:.3f} m/s")

        local_hour = (common_idx.hour - 6) % 24
        for hour in range(24):
            mask = local_hour == hour
            n = int(mask.sum())
            if n == 0:
                continue
            bias = float(np.mean(pred[mask] - obs[mask]))
            corr = float(np.corrcoef(obs[mask], pred[mask])[0, 1]) if n >= MIN_PAIRS_FOR_STATS else np.nan
            bias_rows.append({"year": year, "local_hour": hour, "n": n, "bias_m_s": bias, "pearson_r": corr})

        print(f"  Bootstrap por bloques de día (n_boot={N_BOOTSTRAP}) ...")
        boot = block_bootstrap(obs, pred, common_idx, price_array, turbine, N_BOOTSTRAP, BOOTSTRAP_SEED)
        bootstrap_rows.append({
            "year": year, "metric": "correlation_ws_obs_pred", **boot["correlation"],
            "n_days": boot["n_days"], "n_boot": boot["n_boot"],
        })
        bootstrap_rows.append({
            "year": year, "metric": "capture_price_diff_pred_minus_obs_usd_kwh",
            **boot["capture_price_diff_usd_kwh"], "n_days": boot["n_days"], "n_boot": boot["n_boot"],
        })

    summary_df = pd.DataFrame(summary_rows)
    bias_df = pd.DataFrame(bias_rows)
    bootstrap_df = pd.DataFrame(bootstrap_rows)

    summary_df.to_csv(OUT_DIR / "capture_summary.csv", index=False)
    bias_df.to_csv(OUT_DIR / "hourly_bias_by_local_hour.csv", index=False)
    bootstrap_df.to_csv(OUT_DIR / "bootstrap_capture_diff.csv", index=False)

    write_summary(summary_df, bias_df, bootstrap_df)
    print("\nListo. Salidas en %s" % OUT_DIR)


def write_summary(summary_df: pd.DataFrame, bias_df: pd.DataFrame, bootstrap_df: pd.DataFrame) -> None:
    lines = []
    lines.append("VALIDACION DE CAPTURA: OBSERVADO vs PREDICHO (MERRA-2, sin lag, 6/6)")
    lines.append("=" * 70)
    lines.append("")
    lines.append("Turbina: Clipper 2.5 MW / 89 m (aerogeneradores/clipper25_p2500_r44.5.csv),")
    lines.append("elegida solo para tener una comparacion relativa consistente -- la")
    lines.append("magnitud absoluta de MWh/USD no es el objetivo de esta validacion.")
    lines.append("PML: nodo JUD, UTC (mercado_pml/PML_2017-2024 02JUD115.txt).")
    lines.append("")

    lines.append("-- Precio capturado e ingreso: observado vs predicho MERRA-2 --")
    for year, group in summary_df.groupby("year"):
        obs_row = group[group["branch"] == "observado"].iloc[0]
        pred_row = group[group["branch"] == "predicho_merra2"].iloc[0]
        lines.append(f"Año {year} (n_hours={int(obs_row['n_hours'])}):")
        lines.append(f"  observado        -> precio capturado={obs_row['avg_realized_price_usd_kwh']:.6f} USD/kWh, "
                     f"capture_ratio={obs_row['capture_ratio']}, aep={obs_row['aep_mwh']:.1f} MWh/año(anualizado)")
        lines.append(f"  predicho (MERRA-2) -> precio capturado={pred_row['avg_realized_price_usd_kwh']:.6f} USD/kWh, "
                     f"capture_ratio={pred_row['capture_ratio']}, aep={pred_row['aep_mwh']:.1f} MWh/año(anualizado)")
        delta_pct = 100.0 * (pred_row['avg_realized_price_usd_kwh'] - obs_row['avg_realized_price_usd_kwh']) / obs_row['avg_realized_price_usd_kwh']
        lines.append(f"  diferencia precio capturado (predicho - observado): {delta_pct:+.2f}%")
    lines.append("")

    lines.append("-- Sesgo (pred - obs) y correlación de ws por hora local (UTC-6) --")
    for year, group in bias_df.groupby("year"):
        worst = group.loc[group["bias_m_s"].abs().idxmax()]
        lines.append(f"Año {year}: hora local con mayor sesgo absoluto = {int(worst['local_hour']):02d}:00 "
                     f"(bias={worst['bias_m_s']:+.3f} m/s, n={int(worst['n'])}). Detalle en hourly_bias_by_local_hour.csv")
    lines.append("")

    lines.append("-- Bootstrap por bloques de día (n_boot=%d, seed=%d) --" % (N_BOOTSTRAP, BOOTSTRAP_SEED))
    for _, row in bootstrap_df.iterrows():
        sig = "SI" if row["significant_0_excluded"] else "no"
        lines.append(f"  {int(row['year'])} | {row['metric']:40s} -> observado={row['observed']:+.5f}, "
                     f"IC95%=[{row['ci_2_5']:+.5f}, {row['ci_97_5']:+.5f}], significativo: {sig} "
                     f"(n_dias={int(row['n_days'])})")
    lines.append("")

    (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

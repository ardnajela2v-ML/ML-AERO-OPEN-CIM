#!/usr/bin/env python3
"""Auditoria de alineacion temporal CERTE (10 min) vs reanalisis (1 h).

Solo lectura: importa y llama windmcp.data tal cual esta en el repo. No
modifica resample_hourly ni ningun otro modulo de windmcp/ o core/, aunque
los resultados muestren un desfase.

Pregunta que responde: para 2019 y 2020, por separado para MERRA-2 y ERA5,
que corrimiento horario entero (-3h a +3h) entre la velocidad observada a
80 m (CERTE estacion M04) y la velocidad de referencia minimiza el error /
maximiza la correlacion. Si el minimo no cae en lag=0, hay un desfase real
de alineacion -- en el logger CERTE, en el remuestreo, o en el timestamp
del archivo de reanalisis -- que esta aportando ruido a cualquier metrica
de MCP entrenada sobre estos mismos datos (ver runs/mcp/20260926T185148Z_f23fc01b).

El script se autoverifica contra ese run manifest: ACTIVE_RUN_ID debe listar
exactamente los mismos dos archivos de referencia que REFERENCE_FILES abajo
(ver verify_reference_files_match_active_run()). Si alguna vez cambia el
modelo activo o los archivos de referencia, el script lo va a reportar en
vez de asumir que sigue igual.

Convencion de signo del lag (ver tambien summary.txt generado):
    serie_comparada(t) = referencia(t - lag_hours)
es decir, lag_hours positivo compara la observacion en el instante t contra
el valor de referencia cuyo timestamp es `lag_hours` horas ANTERIOR.

Pruebas, todas de solo lectura:
  1. lag_sweep.csv            -- barrido -3h..+3h, por year x source x
                                  completeness_filter x season (incluye
                                  "completo" = todo el año, y la separacion
                                  estacional abr-oct / nov-mar pedida para
                                  descartar DST en el reloj de la torre).
  2. subhour_sweep.csv        -- (solo ERA5) interpolacion lineal a pasos de
                                  10 min entre -60 y +60 min, y la comparacion
                                  "fisicamente correcta" promedio(ERA5(T),
                                  ERA5(T+1h)) vs la media horaria observada.
  3. solar_peak_by_month.csv  -- hora local del pico de radiacion (Rad_80m_mean)
                                  por mes calendario, para ver si el reloj de
                                  la torre sigue el horario de verano.
  4. direction_lag_sweep.csv  -- mismo barrido de lag pero con direccion
                                  observada (78 m) vs direccion de referencia,
                                  con estadistica circular (no Pearson).
  5. bootstrap_lag_diff.csv   -- bootstrap por bloques de dia de
                                  r(lag=-1h) - r(lag=0h), por year x source.

No se decide aqui ningun umbral de completitud minima por hora: se reportan
las 4 variantes (sin filtro, >=4/6, >=5/6, ==6/6) para que la decision la
tome el usuario viendo el impacto real en correlacion/RMSE/n. Las pruebas
2-5 usan "sin filtro" unicamente (documentado en cada seccion) para no
multiplicar la salida sin necesidad -- el barrido principal (1) ya mostro
que el filtro de completitud no cambia la conclusion del lag.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windmcp.data import read_certe_files, read_windnavigator_file, resample_hourly  # noqa: E402

# --- Config fija de esta auditoria (misma config que el run MCP activo) ---
CERTE_FILE = "/Users/ale/Desktop/2222dose/copiaunoMCPSOFT/certe/M04_CERTE_CONSOLIDADO_2017-2020.txt"
OBS_TIMEZONE = "Etc/GMT+6"
TARGET_COLUMN = "WS_80mA_mean"  # se combina internamente en WS_80m_mean (A+B)

REFERENCE_FILES = {
    "MERRA-2": ROOT / "DATOS DE METEREOLOGICOS DE parques" / "UnionWindnavigator_MERRA2_N16,500_W95,000.txt",
    "ERA5": ROOT / "DATOS DE METEREOLOGICOS DE parques" / "Dosarbolitos_ERA5_N16,500_W95,000.txt",
}
ACTIVE_RUN_MANIFEST = ROOT / "runs" / "mcp" / "20260926T185148Z_f23fc01b" / "run_manifest.json"

YEARS = [2019, 2020]
LAGS_HOURS = range(-3, 4)
SUBHOUR_OFFSETS_MIN = range(-60, 61, 10)
MIN_PAIRS_FOR_STATS = 30  # salvaguarda numerica, no un supuesto metodologico
N_BOOTSTRAP = 1000
BOOTSTRAP_SEED = 12345

COMPLETENESS_FILTERS = {
    "sin_filtro": lambda c: pd.Series(True, index=c.index),
    "min_4_de_6": lambda c: c >= 4,
    "min_5_de_6": lambda c: c >= 5,
    "completo_6_de_6": lambda c: c == 6,
}

SEASONS = {
    "completo": lambda idx: pd.Series(True, index=idx),
    "abr_oct": lambda idx: idx.month.isin(range(4, 11)),
    "nov_mar": lambda idx: idx.month.isin([11, 12, 1, 2, 3]),
}

OUT_DIR = Path(__file__).resolve().parent / "audit_timing_output"


def verify_reference_files_match_active_run() -> None:
    manifest = json.loads(ACTIVE_RUN_MANIFEST.read_text(encoding="utf-8"))
    manifest_names = {Path(p).name for p in manifest["config"]["reference_files"]}
    script_names = {p.name for p in REFERENCE_FILES.values()}
    if manifest_names != script_names:
        print("ADVERTENCIA: los archivos de referencia de este script NO coinciden")
        print("  con runs/mcp/20260926T185148Z_f23fc01b/run_manifest.json")
        print("  manifest: %s" % sorted(manifest_names))
        print("  script:   %s" % sorted(script_names))
    else:
        print("OK: REFERENCE_FILES coincide exactamente con el run MCP activo "
              "(20260926T185148Z_f23fc01b): %s" % sorted(script_names))


def load_observations_full() -> tuple[pd.DataFrame, pd.Series]:
    """Frame normalizado completo (wind_speed_obs, wind_direction_obs, Rad_80m_mean,
    etc., a cadencia 10 min) y completitud de muestras 10-min por hora (base: WS)."""
    frame, meta = read_certe_files([CERTE_FILE], target_column=TARGET_COLUMN, timezone=OBS_TIMEZONE)
    ws = pd.to_numeric(frame["wind_speed_obs"], errors="coerce")
    completeness = ws.resample("1h").count()
    return frame, completeness


def load_reference(path: Path) -> pd.DataFrame:
    ref = read_windnavigator_file(str(path))
    return ref.frame


def year_window(year: int, pad_hours: int) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    core = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="1h", tz="UTC")
    pad = pd.Timedelta(hours=pad_hours)
    padded = pd.date_range(core[0] - pad, core[-1] + pad, freq="1h")
    return core, padded


def pearson_and_rmse(obs: pd.Series, ref: pd.Series) -> tuple[float, float, int]:
    paired = pd.concat([obs, ref], axis=1, keys=["obs", "ref"]).dropna()
    n = len(paired)
    if n < MIN_PAIRS_FOR_STATS:
        return np.nan, np.nan, n
    corr = float(paired["obs"].corr(paired["ref"]))
    rmse = float(np.sqrt(np.mean((paired["obs"] - paired["ref"]) ** 2)))
    return corr, rmse, n


def circular_agreement_and_rmse(obs_deg: pd.Series, ref_deg: pd.Series) -> tuple[float, float, int]:
    """Estadistica circular, no Pearson: diff angular envuelto a (-180, 180].

    agreement = mean(cos(diff)) en [-1, 1], analogo a una correlacion
    (1 = perfectamente alineado, 0 = aleatorio, -1 = sistematicamente opuesto).
    circular_rmse_deg = sqrt(mean(diff^2)), en grados.
    """
    paired = pd.concat([obs_deg, ref_deg], axis=1, keys=["obs", "ref"]).dropna()
    n = len(paired)
    if n < MIN_PAIRS_FOR_STATS:
        return np.nan, np.nan, n
    diff = ((paired["obs"] - paired["ref"] + 180.0) % 360.0) - 180.0
    agreement = float(np.mean(np.cos(np.deg2rad(diff))))
    rmse_deg = float(np.sqrt(np.mean(diff ** 2)))
    return agreement, rmse_deg, n


def completeness_buckets(completeness: pd.Series, core_index: pd.DatetimeIndex) -> dict[str, int]:
    c = completeness.reindex(core_index).fillna(0)
    return {
        "6_de_6": int((c == 6).sum()),
        "5_de_6": int((c == 5).sum()),
        "4_de_6": int((c == 4).sum()),
        "menos_de_4_de_6": int((c < 4).sum()),
    }


# --------------------------------------------------------------------------
# 1. Barrido de lag por hora entera, con separacion estacional
# --------------------------------------------------------------------------

def run_lag_sweep(obs_hourly: pd.Series, obs_completeness: pd.Series,
                   refs_speed: dict[str, pd.Series]) -> tuple[pd.DataFrame, pd.DataFrame]:
    pad_hours = max(abs(min(LAGS_HOURS)), abs(max(LAGS_HOURS)))
    lag_rows = []
    completeness_rows = []

    for year in YEARS:
        core_idx, padded_idx = year_window(year, pad_hours)
        obs_y = obs_hourly.reindex(padded_idx)
        comp_y = obs_completeness.reindex(padded_idx)

        buckets = completeness_buckets(obs_completeness, core_idx)
        total_hours = len(core_idx)
        for bucket, count in buckets.items():
            completeness_rows.append({
                "year": year, "completeness_bucket": bucket,
                "hours": count, "pct_of_year": round(100.0 * count / total_hours, 2),
            })

        for source, ref_series in refs_speed.items():
            ref_y = ref_series.reindex(padded_idx)

            for season_name, season_fn in SEASONS.items():
                season_mask_core = pd.Series(season_fn(core_idx), index=core_idx)

                for lag in LAGS_HOURS:
                    shifted_ref = ref_y.shift(lag).reindex(core_idx)
                    obs_core = obs_y.reindex(core_idx)
                    comp_core = comp_y.reindex(core_idx)

                    for filter_name, filter_fn in COMPLETENESS_FILTERS.items():
                        mask = filter_fn(comp_core) & season_mask_core
                        corr, rmse, n = pearson_and_rmse(obs_core[mask], shifted_ref[mask])
                        lag_rows.append({
                            "year": year, "source": source, "season": season_name,
                            "completeness_filter": filter_name, "lag_hours": lag,
                            "n_pairs": n, "pearson_r": corr, "rmse_m_s": rmse,
                        })

    return pd.DataFrame(lag_rows), pd.DataFrame(completeness_rows)


# --------------------------------------------------------------------------
# 2. Fracciones de hora (solo ERA5): interpolacion lineal + promedio trapezoidal
# --------------------------------------------------------------------------

def run_subhour_sweep(obs_hourly: pd.Series, era5_hourly: pd.Series) -> pd.DataFrame:
    pad_hours = 3  # cubre +-60 min de interpolacion + 2h de margen para el trapezoide
    rows = []

    for year in YEARS:
        core_idx, padded_idx = year_window(year, pad_hours)
        obs_core = obs_hourly.reindex(core_idx)
        ref_padded = era5_hourly.reindex(padded_idx)

        # Interpolacion lineal de ERA5 a resolucion 10 min entre sus anclas horarias.
        ref_10min = ref_padded.resample("10min").interpolate(method="linear")

        for offset_min in SUBHOUR_OFFSETS_MIN:
            target_times = core_idx + pd.Timedelta(minutes=offset_min)
            interp_vals = ref_10min.reindex(target_times)
            interp_vals.index = core_idx
            corr, rmse, n = pearson_and_rmse(obs_core, interp_vals)
            rows.append({
                "year": year, "source": "ERA5", "method": "linear_interp",
                "offset_minutes": offset_min, "lag_hours": np.nan,
                "n_pairs": n, "pearson_r": corr, "rmse_m_s": rmse,
            })

        # Promedio trapezoidal ERA5(T+lag) y ERA5(T+lag+1h) vs obs(T): la
        # comparacion fisicamente correcta entre un promedio horario y una
        # serie instantanea, barrida sobre el mismo rango de lag que la
        # prueba 1 para ver si cambia cual lag es optimo.
        for lag in LAGS_HOURS:
            left = ref_padded.shift(lag).reindex(core_idx)
            right = ref_padded.shift(lag + 1).reindex(core_idx)
            trapezoid = (left + right) / 2.0
            corr, rmse, n = pearson_and_rmse(obs_core, trapezoid)
            rows.append({
                "year": year, "source": "ERA5", "method": "trapezoid_mean",
                "offset_minutes": np.nan, "lag_hours": lag,
                "n_pairs": n, "pearson_r": corr, "rmse_m_s": rmse,
            })

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 3. Hora local del pico de radiacion solar, por mes (chequeo de DST)
# --------------------------------------------------------------------------

def run_solar_peak_by_month(obs_frame: pd.DataFrame) -> pd.DataFrame:
    if "Rad_80m_mean" not in obs_frame.columns:
        raise ValueError("CERTE frame sin columna Rad_80m_mean; no se puede chequear DST por radiacion")
    rad = pd.to_numeric(obs_frame["Rad_80m_mean"], errors="coerce")
    # Etc/GMT+6 es offset fijo (sin DST); restar 6h recupera el timestamp
    # naive original del logger, exactamente como fue escrito en el archivo.
    local_naive = rad.index.tz_convert("UTC") - pd.Timedelta(hours=6)
    table = pd.DataFrame({"rad": rad.to_numpy(), "month": local_naive.month, "hour": local_naive.hour})
    table = table.dropna(subset=["rad"])

    rows = []
    for month in range(1, 13):
        month_table = table[table["month"] == month]
        if month_table.empty:
            rows.append({"month": month, "peak_local_hour": np.nan, "mean_rad_at_peak": np.nan, "n_samples": 0})
            continue
        by_hour = month_table.groupby("hour")["rad"].mean()
        peak_hour = int(by_hour.idxmax())
        rows.append({
            "month": month, "peak_local_hour": peak_hour,
            "mean_rad_at_peak": float(by_hour.loc[peak_hour]),
            "n_samples": int(len(month_table)),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 4. Direccion: barrido de lag con estadistica circular
# --------------------------------------------------------------------------

def run_direction_lag_sweep(obs_frame: pd.DataFrame, refs_full: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if "wind_direction_obs" not in obs_frame.columns:
        raise ValueError("CERTE frame sin columna wind_direction_obs (WD_78m_mean)")
    obs_dir_hourly = resample_hourly(obs_frame[["wind_direction_obs"]])["wind_direction_obs"]

    pad_hours = max(abs(min(LAGS_HOURS)), abs(max(LAGS_HOURS)))
    rows = []
    for year in YEARS:
        core_idx, padded_idx = year_window(year, pad_hours)
        obs_y = obs_dir_hourly.reindex(padded_idx)

        for source, ref_frame in refs_full.items():
            if "wind_direction_ref" not in ref_frame.columns:
                continue
            ref_y = ref_frame["wind_direction_ref"].reindex(padded_idx)

            for lag in LAGS_HOURS:
                shifted_ref = ref_y.shift(lag).reindex(core_idx)
                obs_core = obs_y.reindex(core_idx)
                agreement, rmse_deg, n = circular_agreement_and_rmse(obs_core, shifted_ref)
                rows.append({
                    "year": year, "source": source, "lag_hours": lag,
                    "n_pairs": n, "circular_agreement": agreement,
                    "circular_rmse_deg": rmse_deg,
                })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 5. Bootstrap por bloques de dia de r(lag=-1h) - r(lag=0h)
# --------------------------------------------------------------------------

def block_bootstrap_lag_diff(obs: pd.Series, ref_lag0: pd.Series, ref_lag_minus1: pd.Series,
                              n_boot: int, seed: int) -> dict[str, object]:
    paired = pd.concat(
        [obs, ref_lag0, ref_lag_minus1], axis=1, keys=["obs", "ref0", "refm1"]
    ).dropna()
    n_days_available = paired.index.normalize().nunique()
    if len(paired) < MIN_PAIRS_FOR_STATS or n_days_available < 2:
        return {"observed_diff": np.nan, "boot_mean_diff": np.nan, "ci_2_5": np.nan,
                "ci_97_5": np.nan, "significant_0_excluded": False,
                "n_boot": n_boot, "n_days": n_days_available, "n_pairs": len(paired)}

    r0_obs = float(paired["obs"].corr(paired["ref0"]))
    rm1_obs = float(paired["obs"].corr(paired["refm1"]))
    observed_diff = rm1_obs - r0_obs

    day_key = paired.index.normalize()
    unique_days = pd.unique(day_key)
    by_day_obs = {d: paired.loc[day_key == d, "obs"].to_numpy() for d in unique_days}
    by_day_ref0 = {d: paired.loc[day_key == d, "ref0"].to_numpy() for d in unique_days}
    by_day_refm1 = {d: paired.loc[day_key == d, "refm1"].to_numpy() for d in unique_days}

    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        sample_days = rng.choice(unique_days, size=len(unique_days), replace=True)
        o = np.concatenate([by_day_obs[d] for d in sample_days])
        r0 = np.concatenate([by_day_ref0[d] for d in sample_days])
        rm1 = np.concatenate([by_day_refm1[d] for d in sample_days])
        c0 = np.corrcoef(o, r0)[0, 1]
        cm1 = np.corrcoef(o, rm1)[0, 1]
        diffs[i] = cm1 - c0

    ci_2_5, ci_97_5 = np.percentile(diffs, [2.5, 97.5])
    return {
        "observed_diff": observed_diff, "boot_mean_diff": float(np.mean(diffs)),
        "ci_2_5": float(ci_2_5), "ci_97_5": float(ci_97_5),
        "significant_0_excluded": bool(ci_2_5 > 0 or ci_97_5 < 0),
        "n_boot": n_boot, "n_days": len(unique_days), "n_pairs": len(paired),
    }


def run_bootstrap(obs_hourly: pd.Series, refs_speed: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for year in YEARS:
        core_idx, padded_idx = year_window(year, pad_hours=1)
        obs_y = obs_hourly.reindex(padded_idx).reindex(core_idx)
        for source, ref_series in refs_speed.items():
            ref_y = ref_series.reindex(padded_idx)
            ref_lag0 = ref_y.shift(0).reindex(core_idx)
            ref_lag_minus1 = ref_y.shift(-1).reindex(core_idx)
            result = block_bootstrap_lag_diff(obs_y, ref_lag0, ref_lag_minus1, N_BOOTSTRAP, BOOTSTRAP_SEED)
            result.update({"year": year, "source": source})
            rows.append(result)
    cols = ["year", "source", "observed_diff", "boot_mean_diff", "ci_2_5", "ci_97_5",
            "significant_0_excluded", "n_boot", "n_days", "n_pairs"]
    return pd.DataFrame(rows)[cols]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    verify_reference_files_match_active_run()

    print("Leyendo observaciones CERTE M04 (10 min, %s) ..." % OBS_TIMEZONE)
    obs_frame, obs_completeness = load_observations_full()
    obs_hourly = pd.to_numeric(obs_frame["wind_speed_obs"], errors="coerce").resample("1h").mean()

    refs_full: dict[str, pd.DataFrame] = {}
    refs_speed: dict[str, pd.Series] = {}
    for source, path in REFERENCE_FILES.items():
        print("Leyendo referencia %s: %s" % (source, path.name))
        refs_full[source] = load_reference(path)
        refs_speed[source] = refs_full[source]["wind_speed_ref"]

    print("\n[1/5] Barrido de lag (-3h..+3h) por year x source x filtro x estacion ...")
    lag_df, completeness_df = run_lag_sweep(obs_hourly, obs_completeness, refs_speed)
    lag_df.to_csv(OUT_DIR / "lag_sweep.csv", index=False)
    completeness_df.to_csv(OUT_DIR / "completeness.csv", index=False)

    print("[2/5] Fracciones de hora (ERA5): interpolacion lineal + promedio trapezoidal ...")
    subhour_df = run_subhour_sweep(obs_hourly, refs_speed["ERA5"])
    subhour_df.to_csv(OUT_DIR / "subhour_sweep.csv", index=False)

    print("[3/5] Hora local del pico de radiacion solar, por mes (chequeo DST) ...")
    solar_df = run_solar_peak_by_month(obs_frame)
    solar_df.to_csv(OUT_DIR / "solar_peak_by_month.csv", index=False)

    print("[4/5] Barrido de lag en direccion (estadistica circular) ...")
    direction_df = run_direction_lag_sweep(obs_frame, refs_full)
    direction_df.to_csv(OUT_DIR / "direction_lag_sweep.csv", index=False)

    print("[5/5] Bootstrap por bloques de dia de r(-1h) - r(0h) (n_boot=%d) ..." % N_BOOTSTRAP)
    bootstrap_df = run_bootstrap(obs_hourly, refs_speed)
    bootstrap_df.to_csv(OUT_DIR / "bootstrap_lag_diff.csv", index=False)

    write_summary(lag_df, completeness_df, subhour_df, solar_df, direction_df, bootstrap_df)
    print("\nListo. Salidas en %s" % OUT_DIR)


def write_summary(lag_df, completeness_df, subhour_df, solar_df, direction_df, bootstrap_df) -> None:
    lines = []
    lines.append("AUDITORIA DE ALINEACION TEMPORAL CERTE vs REANALISIS")
    lines.append("=" * 60)
    lines.append("")
    lines.append("Convencion de signo: serie_comparada(t) = referencia(t - lag_hours).")
    lines.append("lag_hours > 0  => el mejor ajuste usa un valor de referencia con")
    lines.append("                  timestamp ANTERIOR al de la observacion.")
    lines.append("lag_hours < 0  => el mejor ajuste usa un valor de referencia con")
    lines.append("                  timestamp POSTERIOR al de la observacion.")
    lines.append("")
    lines.append("Altura: observacion CERTE a 80 m vs referencia a la altura nativa")
    lines.append("del archivo Windnavigator. La magnitud absoluta del RMSE de velocidad")
    lines.append("esta inflada por esa diferencia de altura; el lag que minimiza RMSE /")
    lines.append("maximiza correlacion es la senal relevante, no el valor absoluto.")
    lines.append("")
    lines.append("Salvaguarda numerica (no supuesto metodologico): con menos de %d pares" % MIN_PAIRS_FOR_STATS)
    lines.append("validos se reporta NaN en vez de un numero no confiable.")
    lines.append("")

    lines.append("-- [1] Mejor lag por (year, source, season, completeness_filter) --")
    for (year, source, season, filt), group in lag_df.groupby(["year", "source", "season", "completeness_filter"]):
        valid = group.dropna(subset=["pearson_r"])
        if valid.empty:
            continue
        best = valid.loc[valid["pearson_r"].idxmax()]
        lag0 = group.loc[group["lag_hours"] == 0].iloc[0]
        flag = "" if best["lag_hours"] == 0 else "  <-- lag optimo != 0"
        lines.append(
            f"{year} | {source:8s} | {season:9s} | {filt:16s} -> mejor lag={int(best['lag_hours']):+d}h "
            f"(r={best['pearson_r']:.4f}, n={int(best['n_pairs'])}) | lag=0h: r={lag0['pearson_r']:.4f}{flag}"
        )
    lines.append("")

    lines.append("-- [1b] Completitud horaria por año --")
    for year, group in completeness_df.groupby("year"):
        lines.append(f"Año {year}:")
        for _, row in group.iterrows():
            lines.append(f"  {row['completeness_bucket']:18s} {row['hours']:5d} h  ({row['pct_of_year']:5.1f}% del año)")
    lines.append("")

    lines.append("-- [2] Fracciones de hora, ERA5 (sin_filtro) --")
    for year, group in subhour_df.groupby("year"):
        interp = group[group["method"] == "linear_interp"].dropna(subset=["pearson_r"])
        if not interp.empty:
            best = interp.loc[interp["pearson_r"].idxmax()]
            lines.append(f"{year} | linear_interp -> mejor offset={int(best['offset_minutes']):+d} min "
                         f"(r={best['pearson_r']:.4f}, rmse={best['rmse_m_s']:.3f}, n={int(best['n_pairs'])})")
        trapz = group[group["method"] == "trapezoid_mean"].dropna(subset=["pearson_r"])
        if not trapz.empty:
            best_t = trapz.loc[trapz["pearson_r"].idxmax()]
            lag0_t = trapz.loc[trapz["lag_hours"] == 0]
            lag0_r = lag0_t.iloc[0]["pearson_r"] if not lag0_t.empty else np.nan
            lines.append(f"{year} | trapezoid_mean -> mejor lag={int(best_t['lag_hours']):+d}h "
                         f"(r={best_t['pearson_r']:.4f}, rmse={best_t['rmse_m_s']:.3f}, n={int(best_t['n_pairs'])}) | "
                         f"lag=0h: r={lag0_r:.4f}")
    lines.append("")

    lines.append("-- [3] Hora local del pico de radiacion solar por mes (chequeo DST) --")
    lines.append("  Mexico historico: DST abr-oct. Si el reloj de la torre seguia DST,")
    lines.append("  el pico deberia leerse ~1h mas tarde en abr-oct que en nov-mar.")
    for _, row in solar_df.iterrows():
        if pd.isna(row["peak_local_hour"]):
            lines.append(f"  mes {int(row['month']):02d}: sin datos")
        else:
            lines.append(f"  mes {int(row['month']):02d}: pico a las {int(row['peak_local_hour']):02d}:00 local "
                         f"(n={int(row['n_samples'])})")
    lines.append("")

    lines.append("-- [4] Barrido de lag en direccion (agreement circular = mean(cos(diff))) --")
    for (year, source), group in direction_df.groupby(["year", "source"]):
        valid = group.dropna(subset=["circular_agreement"])
        if valid.empty:
            continue
        best = valid.loc[valid["circular_agreement"].idxmax()]
        lag0 = group.loc[group["lag_hours"] == 0]
        lag0_agr = lag0.iloc[0]["circular_agreement"] if not lag0.empty else np.nan
        flag = "" if best["lag_hours"] == 0 else "  <-- lag optimo != 0"
        lines.append(f"{year} | {source:8s} -> mejor lag={int(best['lag_hours']):+d}h "
                     f"(agreement={best['circular_agreement']:.4f}, rmse={best['circular_rmse_deg']:.1f} deg, "
                     f"n={int(best['n_pairs'])}) | lag=0h: agreement={lag0_agr:.4f}{flag}")
    lines.append("")

    lines.append("-- [5] Bootstrap por bloques de dia: r(lag=-1h) - r(lag=0h) --")
    lines.append(f"  n_boot={N_BOOTSTRAP}, seed={BOOTSTRAP_SEED}, filtro=sin_filtro")
    for _, row in bootstrap_df.iterrows():
        if pd.isna(row["observed_diff"]):
            lines.append(f"  {int(row['year'])} | {row['source']:8s} -> sin datos suficientes")
            continue
        sig = "SI (CI 95% excluye 0)" if row["significant_0_excluded"] else "no (CI 95% incluye 0)"
        lines.append(
            f"  {int(row['year'])} | {row['source']:8s} -> diff observado={row['observed_diff']:+.4f} "
            f"(bootstrap media={row['boot_mean_diff']:+.4f}, CI95%=[{row['ci_2_5']:+.4f}, {row['ci_97_5']:+.4f}], "
            f"n_dias={int(row['n_days'])}) -> significativo: {sig}"
        )
    lines.append("")

    lines.append("-- Decisiones NO tomadas por este script (pendientes del usuario) --")
    lines.append("1. Umbral minimo de completitud por hora para la validacion de captura")
    lines.append("   final: 4 variantes en lag_sweep.csv (sin_filtro, >=4/6, >=5/6, ==6/6).")
    lines.append("2. Donde corregir el desfase si se confirma (logger CERTE, resample_hourly,")
    lines.append("   o el archivo de reanalisis) -- este script no modifica ningun codigo.")
    lines.append("")

    (OUT_DIR / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

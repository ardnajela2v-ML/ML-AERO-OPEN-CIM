#!/usr/bin/env python3
"""Compara aep_mwh de simulate_wind_farm vs el aep_mwh implicito en
chronological_revenue, para el campeon de cada una de las 8 tecnologias de
PARQEU1 (uploads/PARQEU1.aeroproj, bloque
client_state.mcda.lastSpatial.alternatives), sobre la serie completa
(~44.67 anios, "all"), usando la velocidad con WS_80mA_mean sustituido en
los dias marcados (tools/corrected_reconstruction.py) en vez de la
WS_80m_mean contaminada que sigue viva en windmcp/ (no se modifica).

Geometria, curva de turbina, alturas, densidad y multiplicadores espaciales
(Kriging) de cada campeon se tomaron tal cual del manifest.json original de
PARQEU1 (extraidos a tools/_scratch_champions/parqeu1_champions.json antes
de esta sesion -- el campo 'local_speed_multipliers' es el que
historical_layout_simulation ya pasaba como local_wind_multipliers a ambas
funciones). Direccion: columna 'wind_direction' del mismo
historical_reconstruction.csv (paired, no se recalcula).

No modifica windmcp/, core/ ni runs/mcp/: solo lee y escribe bajo tools/.

Salida: tools/aep_method_comparison_output/aep_method_comparison.csv + summary.txt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TOOLS_DIR))

from core.energy import PowerCurve, extrapolate_wind_hellmann  # noqa: E402
from core.farm import chronological_revenue, simulate_wind_farm  # noqa: E402
from corrected_reconstruction import load_corrected_reconstruction  # noqa: E402

CHAMPIONS_JSON = TOOLS_DIR / "_scratch_champions" / "parqeu1_champions.json"
AEROGEN_DIR = ROOT / "aerogeneradores"
OUT_DIR = TOOLS_DIR / "aep_method_comparison_output"
N_SECTORS = 36
K_WAKE = 0.075
CT = 0.8
WAKE_MODEL = "gaussian"


def label_to_turbine_file(label: str) -> str:
    # 'layout::acciona15_p1500_r35.csv::wake-optimal' -> 'acciona15_p1500_r35.csv'
    return label.split("::")[1]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    champions = json.loads(CHAMPIONS_JSON.read_text())

    print("Construyendo serie corregida (80mA en dias marcados) ...")
    df, flagged, stats = load_corrected_reconstruction()
    valid = (
        np.isfinite(df["long_term_wind_speed_corrected"])
        & (df["long_term_wind_speed_corrected"] >= 0)
        & np.isfinite(df["wind_direction"])
    )
    ws_80m = df.loc[valid, "long_term_wind_speed_corrected"].to_numpy()
    wd = df.loc[valid, "wind_direction"].to_numpy()
    print(f"Horas validas en la serie corregida: {len(ws_80m)} "
          f"(dias marcados sustituidos: {stats['n_hours_swapped']} horas)")

    rows = []
    for champ in champions:
        turbine_file = AEROGEN_DIR / label_to_turbine_file(champ["label"])
        turbine = PowerCurve.from_file(str(turbine_file))
        layout_xy_m = np.asarray(champ["layout_xy_m"], dtype=float)
        n = len(layout_xy_m)
        target_h = float(champ["target_height_m"])
        source_h = float(champ["source_height_m"])
        rho = float(champ["rho_used_kg_m3"])
        avail = 1.0 - float(champ["availability_loss_pct"]) / 100.0
        elec = float(champ["electrical_loss_pct"]) / 100.0
        local_mult = np.asarray(champ["local_speed_multipliers"], dtype=float)
        assert len(local_mult) == n, (champ["label"], len(local_mult), n)

        ws_hub = extrapolate_wind_hellmann(ws_80m, source_h, target_h) if target_h != source_h else ws_80m

        sim = simulate_wind_farm(
            layout_xy_m.tolist(), turbine, ws_hub, wd,
            k_wake=K_WAKE, ct=CT, n_sectors=N_SECTORS,
            availability=avail, electrical_losses=elec,
            local_wind_multipliers=local_mult, wake_model=WAKE_MODEL,
            rho_override=rho, sample_duration_hours=1.0,
        )
        sim_aep = float(sim["aep_mwh"])

        T = len(ws_hub)
        dummy_hour_of_year = np.zeros(T, dtype=int)  # aep_mwh no depende del precio
        dummy_price = np.ones(8760, dtype=float)
        chron = chronological_revenue(
            layout_xy_m, turbine, ws_hub, wd, dummy_hour_of_year, dummy_price,
            sample_duration_hours=1.0, k_wake=K_WAKE, ct=CT, n_sectors=N_SECTORS,
            availability=avail, electrical_losses=elec,
            local_wind_multipliers=local_mult, wake_model=WAKE_MODEL, rho_override=rho,
        )
        chron_aep = float(chron["aep_mwh"])

        diff_mwh = chron_aep - sim_aep
        diff_pct = 100.0 * diff_mwh / sim_aep if sim_aep else np.nan
        rows.append({
            "label": champ["label"],
            "n_turbines": n,
            "hub_height_m": target_h,
            "aep_mwh_simulate_wind_farm": round(sim_aep, 2),
            "aep_mwh_chronological_revenue": round(chron_aep, 2),
            "diff_mwh": round(diff_mwh, 2),
            "diff_pct": round(diff_pct, 4),
            "n_hours_used": sim["turbines"] and T,
            "aep_mwh_manifest_original_sim": champ["aep_mwh_manifest"],
            "aep_mwh_manifest_original_chron": champ["chron_aep_mwh_manifest"],
        })
        print(f"{champ['label']:55s} sim={sim_aep:10.2f}  chron={chron_aep:10.2f}  "
              f"diff={diff_mwh:+8.2f} MWh ({diff_pct:+.3f}%)")

    out = pd.DataFrame(rows)
    out.to_csv(OUT_DIR / "aep_method_comparison.csv", index=False)

    with open(OUT_DIR / "summary.txt", "w") as f:
        f.write("COMPARACION aep_mwh: simulate_wind_farm vs chronological_revenue\n")
        f.write("Serie corregida (80mA en dias marcados), ~44.67 anios, 8 campeones de PARQEU1\n")
        f.write("=" * 78 + "\n\n")
        f.write(f"Dias sustituidos: {stats['n_flagged_days']} ({stats['flagged_range'][0]} -> "
                f"{stats['flagged_range'][1]}), {stats['n_hours_swapped']} horas de "
                f"{stats['n_hours_total']} totales\n\n")
        f.write(out.to_string(index=False) + "\n\n")
        f.write(f"diff_pct: media={out['diff_pct'].mean():.4f}%, "
                f"abs media={out['diff_pct'].abs().mean():.4f}%, "
                f"min={out['diff_pct'].min():.4f}%, max={out['diff_pct'].max():.4f}%\n")

    print("\nListo. Salidas en", OUT_DIR)


if __name__ == "__main__":
    main()

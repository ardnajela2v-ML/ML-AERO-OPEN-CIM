
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Reconstrucción verídica del análisis de Machine Learning tipo Orange Data Mining
para la tesis de aerogeneradores, SIN inventar datos.

Qué hace este script:
1) Lee un archivo CERTE (CSV) con la serie temporal de velocidad de viento.
2) Lee las curvas de potencia reales de los aerogeneradores.
3) Convierte la serie de viento del sitio en una serie de potencia para cada turbina
   mediante interpolación de la curva de potencia.
4) Calcula una matriz de distancia de Pearson entre aerogeneradores.
5) Genera:
   - dendrograma jerárquico
   - mapa de distancias con dendrogramas
   - matrices CSV de correlación y distancias
   - resumen técnico y energético
   - archivo CSV listo para Orange (filas=turbinas, columnas=pasos de tiempo)

Importante:
- Este script NO reconstruye valores faltantes con supuestos arbitrarios.
- Utiliza exclusivamente:
    a) el CERTE cargado por el usuario
    b) las curvas de potencia CSV que realmente existan
- La tesis no especifica por completo todos los parámetros de Orange
  (hay inconsistencias entre Pearson / euclidiana / Ward). Por ello este script
  implementa la versión más defendible y trazable con los datos disponibles:
      Distancia = 1 - correlación de Pearson
      Linkage jerárquico por defecto = complete
  porque reproduce razonablemente bien la figura 4.13 y conserva el hallazgo
  principal: Gamesa G80/2000 y Vestas V80/2000 son el par más cercano.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import gridspec
from scipy.cluster.hierarchy import (
    dendrogram,
    leaves_list,
    linkage,
    optimal_leaf_ordering,
)
from scipy.spatial.distance import squareform


BASE_DIR = Path(__file__).resolve().parent
CURVES_DIR = BASE_DIR / "aerogeneradores"


THESIS_TURBINES: Dict[str, str] = {
    "Acciona AW 70/1500": "acciona15_p1500_r35.csv",
    "Acciona 77/1.5 MW": "acciona16_p1500_r38.5.csv",
    "Gamesa G80/2000": "gamesa20_p2000_r40.csv",
    "Gamesa G90/2000": "gamesa21_p2000_r45.csv",
    "Vestas V80/2000": "vestas20_p2000_r40.csv",
    "Clipper 89 / 2.5 MW": "clipper25_p2500_r44.5.csv",
    "Vestas V90 / 3000": "vestas30_p3000_r45.csv",
    "Alstom Power 110/3MW": "alstom30_p3000_r55.csv",
}


@dataclass
class CurveData:
    name: str
    path: Path
    nominal_kw: float
    rotor_radius_m: Optional[float]
    rotor_diameter_m: Optional[float]
    df: pd.DataFrame


def parse_filename_metadata(path: Path) -> Tuple[Optional[float], Optional[float]]:
    """
    Extrae potencia nominal y radio del rotor a partir del nombre del archivo:
    ejemplo: gamesa20_p2000_r40.csv -> nominal_kw=2000, radius=40
    """
    stem = path.stem.lower()
    nominal_kw = None
    rotor_radius_m = None

    parts = stem.replace("-", "_").split("_")
    for part in parts:
        if part.startswith("p"):
            try:
                nominal_kw = float(part[1:])
            except ValueError:
                pass
        elif part.startswith("r"):
            try:
                rotor_radius_m = float(part[1:])
            except ValueError:
                pass

    return nominal_kw, rotor_radius_m


def infer_timestep_hours(certe: pd.DataFrame) -> float:
    """Intenta deducir el paso temporal en horas. Si falla, usa 10 minutos."""
    if "Date" in certe.columns and "h:min" in certe.columns:
        dt_strings = (
            certe["Date"].astype(str).str.strip() + " " + certe["h:min"].astype(str).str.strip()
        )
        dt = pd.to_datetime(dt_strings, dayfirst=True, errors="coerce")
        dt = dt.dropna()
        if len(dt) >= 2:
            deltas = dt.diff().dropna().dt.total_seconds().to_numpy()
            if deltas.size:
                # usar la mediana para robustez
                median_seconds = float(np.median(deltas))
                if median_seconds > 0:
                    return median_seconds / 3600.0
    return 10.0 / 60.0


def safe_input(prompt: str, default: Optional[str] = None, no_prompt: bool = False) -> str:
    if no_prompt:
        return default or ""
    raw = input(prompt).strip()
    if raw:
        return raw
    return default or ""


def detectar_certe_csv(base_dir: Path) -> Optional[Path]:
    candidates = sorted(base_dir.glob("*.csv"))
    certe = [p for p in candidates if "certe" in p.name.lower()]
    if certe:
        return certe[0]
    return None


def detectar_curvas(base_dir: Path) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    search_dirs = [base_dir, base_dir / "aerogeneradores"]
    for turbine_name, filename in THESIS_TURBINES.items():
        for d in search_dirs:
            path = d / filename
            if path.exists():
                found[turbine_name] = path
                break
    return found


def read_curve(path: Path, turbine_name: str) -> CurveData:
    # leer sin asumir cabecera
    df = pd.read_csv(path, header=None, names=["velocidad_m_s", "potencia_kw"])
    # convertir a numérico y limpiar
    df["velocidad_m_s"] = pd.to_numeric(df["velocidad_m_s"], errors="coerce")
    df["potencia_kw"] = pd.to_numeric(df["potencia_kw"], errors="coerce")
    df = df.dropna().copy()
    df = df.sort_values("velocidad_m_s").drop_duplicates("velocidad_m_s")
    if df.empty:
        raise ValueError(f"La curva '{path.name}' quedó vacía después de limpiar datos.")

    nominal_kw, rotor_radius_m = parse_filename_metadata(path)
    if nominal_kw is None:
        nominal_kw = float(df["potencia_kw"].max())
    rotor_diameter_m = rotor_radius_m * 2.0 if rotor_radius_m is not None else None

    return CurveData(
        name=turbine_name,
        path=path,
        nominal_kw=float(nominal_kw),
        rotor_radius_m=rotor_radius_m,
        rotor_diameter_m=rotor_diameter_m,
        df=df.reset_index(drop=True),
    )


def seleccionar_wind_column(certe: pd.DataFrame, no_prompt: bool = False, preset: Optional[str] = None) -> str:
    preferred = ["WS_80mA_mean", "WS_80mB_mean", "WS_60m_mean", "WS_40m_mean"]
    available = [c for c in preferred if c in certe.columns]
    if not available:
        # buscar por heurística
        available = [c for c in certe.columns if "WS_" in c and "_mean" in c]
    if not available:
        raise ValueError("No se encontró ninguna columna de velocidad de viento tipo WS_*_mean en el CERTE.")

    if preset:
        if preset not in certe.columns:
            raise ValueError(f"La columna de viento solicitada no existe: {preset}")
        return preset

    print("\nColumnas de velocidad de viento disponibles:")
    for i, col in enumerate(available, start=1):
        print(f"  {i}. {col}")

    default_col = "WS_80mA_mean" if "WS_80mA_mean" in available else available[0]
    choice = safe_input(
        f"Elige columna de viento [Enter = {default_col}]: ",
        default=default_col,
        no_prompt=no_prompt,
    )

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(available):
            return available[idx]

    if choice in certe.columns:
        return choice

    return default_col


def interp_power(curve_df: pd.DataFrame, wind_speed: np.ndarray) -> np.ndarray:
    x = curve_df["velocidad_m_s"].to_numpy(dtype=float)
    y = curve_df["potencia_kw"].to_numpy(dtype=float)

    # extrapolación conservadora:
    # por debajo del mínimo -> usar primer valor
    # por encima del máximo -> usar último valor declarado
    return np.interp(wind_speed, x, y, left=y[0], right=y[-1])


def construir_series_potencia(
    certe: pd.DataFrame,
    wind_col: str,
    curves: Sequence[CurveData],
) -> pd.DataFrame:
    ws = pd.to_numeric(certe[wind_col], errors="coerce").to_numpy(dtype=float)

    result = {}
    for curve in curves:
        result[curve.name] = interp_power(curve.df, ws)

    series_df = pd.DataFrame(result)
    return series_df


def pearson_distance_matrix(series_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    X = series_df.to_numpy(dtype=float).T  # filas=turbinas
    corr = np.corrcoef(X)
    dist = 1.0 - corr

    np.fill_diagonal(corr, 1.0)
    np.fill_diagonal(dist, 0.0)

    cols = list(series_df.columns)
    corr_df = pd.DataFrame(corr, index=cols, columns=cols)
    dist_df = pd.DataFrame(dist, index=cols, columns=cols)
    return corr_df, dist_df


def resumen_energetico(
    series_df: pd.DataFrame,
    curves: Sequence[CurveData],
    timestep_hours: float,
) -> pd.DataFrame:
    rows = []
    nominal_map = {c.name: c.nominal_kw for c in curves}
    radius_map = {c.name: c.rotor_radius_m for c in curves}
    diameter_map = {c.name: c.rotor_diameter_m for c in curves}

    for turbine in series_df.columns:
        p = pd.to_numeric(series_df[turbine], errors="coerce")
        mean_kw = float(p.mean())
        aep_kwh = float(p.sum() * timestep_hours)
        nominal_kw = float(nominal_map[turbine])
        cf_pct = (mean_kw / nominal_kw) * 100.0 if nominal_kw > 0 else np.nan
        rows.append(
            {
                "Aerogenerador": turbine,
                "Potencia nominal (kW)": nominal_kw,
                "Radio rotor (m)": radius_map[turbine],
                "Diametro rotor (m)": diameter_map[turbine],
                "Potencia media calculada (kW)": mean_kw,
                "AEP calculada (kWh/año)": aep_kwh,
                "Factor de capacidad calculado (%)": cf_pct,
            }
        )

    return pd.DataFrame(rows).sort_values("Aerogenerador").reset_index(drop=True)


def condensed_from_square(dist_df: pd.DataFrame) -> np.ndarray:
    return squareform(dist_df.to_numpy(dtype=float), checks=False)


def build_linkage(
    dist_df: pd.DataFrame,
    method: str = "complete",
    optimal_order: bool = False,
):
    condensed = condensed_from_square(dist_df)
    Z = linkage(condensed, method=method)
    if optimal_order:
        Z = optimal_leaf_ordering(Z, condensed)
    return Z


def closest_and_farthest_pairs(dist_df: pd.DataFrame) -> Tuple[Tuple[str, str, float], Tuple[str, str, float]]:
    names = list(dist_df.index)
    best = None
    worst = None
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            d = float(dist_df.iloc[i, j])
            item = (names[i], names[j], d)
            if best is None or d < best[2]:
                best = item
            if worst is None or d > worst[2]:
                worst = item
    assert best is not None and worst is not None
    return best, worst


def save_dendrogram_figure(
    Z,
    labels: Sequence[str],
    outpath: Path,
    title: str,
):
    fig = plt.figure(figsize=(13, 5))
    ax = fig.add_subplot(111)
    dendrogram(
        Z,
        labels=list(labels),
        orientation="right",
        leaf_font_size=11,
        color_threshold=0,
        above_threshold_color="black",
        ax=ax,
    )
    ax.set_title(title, fontsize=14)
    ax.set_xlabel("Distancia de Pearson (1 - r)")
    ax.grid(False)
    fig.tight_layout()
    fig.savefig(outpath, dpi=220, bbox_inches="tight")
    plt.close(fig)


def save_distance_map(
    dist_df: pd.DataFrame,
    Z_rows,
    Z_cols,
    outpath: Path,
    title: str,
):
    row_order = leaves_list(Z_rows)
    col_order = leaves_list(Z_cols)

    ordered = dist_df.iloc[row_order, col_order]
    row_labels = ordered.index.tolist()
    col_labels = ordered.columns.tolist()

    fig = plt.figure(figsize=(12, 10))
    gs = gridspec.GridSpec(
        2,
        2,
        width_ratios=[1.3, 4.2],
        height_ratios=[1.3, 4.2],
        wspace=0.02,
        hspace=0.02,
    )

    ax_top = fig.add_subplot(gs[0, 1])
    dendrogram(
        Z_cols,
        ax=ax_top,
        no_labels=True,
        color_threshold=0,
        above_threshold_color="black",
    )
    ax_top.set_xticks([])
    ax_top.set_yticks([])
    for sp in ax_top.spines.values():
        sp.set_visible(False)

    ax_left = fig.add_subplot(gs[1, 0])
    dendrogram(
        Z_rows,
        ax=ax_left,
        orientation="left",
        no_labels=True,
        color_threshold=0,
        above_threshold_color="black",
    )
    ax_left.set_xticks([])
    ax_left.set_yticks([])
    for sp in ax_left.spines.values():
        sp.set_visible(False)

    ax_heat = fig.add_subplot(gs[1, 1])
    im = ax_heat.imshow(ordered.to_numpy(), aspect="auto", interpolation="nearest")
    ax_heat.set_xticks(np.arange(len(col_labels)))
    ax_heat.set_xticklabels(col_labels, rotation=90, fontsize=9)
    ax_heat.set_yticks(np.arange(len(row_labels)))
    ax_heat.set_yticklabels(row_labels, fontsize=9)
    ax_heat.yaxis.tick_right()
    ax_heat.tick_params(axis="both", length=0)

    cbar = fig.colorbar(im, ax=ax_heat, fraction=0.046, pad=0.04)
    cbar.set_label("Distancia de Pearson (1 - r)")

    fig.suptitle(title, fontsize=14, y=0.98)
    fig.tight_layout()
    fig.savefig(outpath, dpi=220, bbox_inches="tight")
    plt.close(fig)


def export_orange_input(series_df: pd.DataFrame, outpath: Path):
    """
    Orange trabaja con filas=instancias y columnas=variables.
    Aquí cada fila es un aerogenerador y cada columna temporal es una característica.
    """
    orange_df = series_df.T.copy()
    orange_df.index.name = "Aerogenerador"
    orange_df.to_csv(outpath, index=True)


def guardar_curvas_combinadas(curves: Sequence[CurveData], outpath: Path):
    """
    Exporta las curvas de potencia a un solo CSV largo.
    """
    frames = []
    for c in curves:
        df = c.df.copy()
        df["Aerogenerador"] = c.name
        df["Potencia nominal (kW)"] = c.nominal_kw
        df["Radio rotor (m)"] = c.rotor_radius_m
        df["Diametro rotor (m)"] = c.rotor_diameter_m
        frames.append(df)
    final_df = pd.concat(frames, ignore_index=True)
    cols = [
        "Aerogenerador",
        "velocidad_m_s",
        "potencia_kw",
        "Potencia nominal (kW)",
        "Radio rotor (m)",
        "Diametro rotor (m)",
    ]
    final_df[cols].to_csv(outpath, index=False)


def build_report_text(
    certe_path: Path,
    wind_col: str,
    timestep_hours: float,
    linkage_method: str,
    curves: Sequence[CurveData],
    best_pair: Tuple[str, str, float],
    worst_pair: Tuple[str, str, float],
) -> str:
    lines = []
    lines.append("REPORTE DE RECONSTRUCCIÓN ML TIPO ORANGE")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Archivo CERTE: {certe_path}")
    lines.append(f"Columna de viento utilizada: {wind_col}")
    lines.append(f"Paso temporal inferido: {timestep_hours:.6f} horas")
    lines.append("Distancia utilizada: 1 - correlación de Pearson")
    lines.append(f"Método de agrupamiento jerárquico: {linkage_method}")
    lines.append("")
    lines.append("Curvas de potencia utilizadas:")
    for c in curves:
        lines.append(
            f"- {c.name} | archivo={c.path.name} | nominal={c.nominal_kw:.1f} kW"
            + (
                f" | radio={c.rotor_radius_m} m | diámetro={c.rotor_diameter_m} m"
                if c.rotor_radius_m is not None
                else ""
            )
        )
    lines.append("")
    lines.append(
        f"Par más cercano (menor distancia): {best_pair[0]} <-> {best_pair[1]} | d={best_pair[2]:.8f}"
    )
    lines.append(
        f"Par más lejano (mayor distancia): {worst_pair[0]} <-> {worst_pair[1]} | d={worst_pair[2]:.8f}"
    )
    lines.append("")
    lines.append("Notas metodológicas:")
    lines.append(
        "- Esta reconstrucción utiliza exclusivamente datos reales cargados por el usuario."
    )
    lines.append(
        "- No se generaron tablas sintéticas ni se reconstruyeron variables inexistentes."
    )
    lines.append(
        "- Si la tesis original utilizó alturas de buje específicas, pérdidas por estela, "
        "ajustes de potencia adicionales o una configuración exacta de Orange no documentada, "
        "eso puede provocar diferencias numéricas frente a las figuras impresas."
    )
    lines.append(
        "- Aun así, esta metodología es trazable, reproducible y estadísticamente defendible "
        "porque la similitud entre turbinas se calcula sobre sus series reales de potencia "
        "derivadas del recurso eólico del sitio."
    )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconstrucción ML tipo Orange con datos reales CERTE + curvas.")
    parser.add_argument("--certe", type=str, default=None, help="Ruta al archivo CERTE CSV.")
    parser.add_argument("--wind-col", type=str, default=None, help="Columna de velocidad de viento a usar.")
    parser.add_argument("--outdir", type=str, default=None, help="Directorio de salida.")
    parser.add_argument(
        "--linkage",
        type=str,
        default="complete",
        choices=["single", "complete", "average", "weighted"],
        help="Método de agrupamiento jerárquico.",
    )
    parser.add_argument(
        "--optimal-order",
        action="store_true",
        help="Usa optimal leaf ordering para el mapa de distancias.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Ejecuta sin interacción. Usa auto-detección y valores por defecto.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=== Reconstrucción ML tipo Orange sin inventar datos ===")

    certe_path: Optional[Path]
    if args.certe:
        certe_path = Path(args.certe)
    else:
        certe_path = detectar_certe_csv(BASE_DIR)

    if certe_path is None or not certe_path.exists():
        if args.no_prompt:
            raise FileNotFoundError("No se pudo detectar automáticamente el archivo CERTE.")
        manual = safe_input("No se detectó CERTE automáticamente. Escribe la ruta manual: ")
        certe_path = Path(manual)

    if not certe_path.exists():
        raise FileNotFoundError(f"No existe el archivo CERTE: {certe_path}")

    outdir = Path(args.outdir) if args.outdir else (BASE_DIR / "salida_orange_real")
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Archivo CERTE: {certe_path}")
    print("Leyendo CERTE...")
    certe = pd.read_csv(certe_path, low_memory=False)

    wind_col = seleccionar_wind_column(certe, no_prompt=args.no_prompt, preset=args.wind_col)
    print(f"Columna de viento seleccionada: {wind_col}")

    detected = detectar_curvas(BASE_DIR)
    if len(detected) < 2:
        raise RuntimeError("Se detectaron menos de 2 curvas de potencia. No se puede agrupar.")

    print("\nCurvas detectadas para la tesis:")
    for name, path in detected.items():
        print(f"  - {name}: {path.name}")

    curves = [read_curve(path, name) for name, path in detected.items()]
    timestep_hours = infer_timestep_hours(certe)
    print(f"Paso temporal inferido: {timestep_hours:.6f} h")

    print("\nConstruyendo series de potencia por aerogenerador...")
    series_df = construir_series_potencia(certe, wind_col, curves)

    corr_df, dist_df = pearson_distance_matrix(series_df)
    energy_df = resumen_energetico(series_df, curves, timestep_hours)
    best_pair, worst_pair = closest_and_farthest_pairs(dist_df)

    print(f"Par más cercano: {best_pair[0]} <-> {best_pair[1]} | d={best_pair[2]:.8f}")
    print(f"Par más lejano:  {worst_pair[0]} <-> {worst_pair[1]} | d={worst_pair[2]:.8f}")

    # Dendrograma principal: sin optimal ordering para parecerse más a la tesis
    Z_dend = build_linkage(dist_df, method=args.linkage, optimal_order=False)
    # Mapa: con opción de optimal ordering si se solicita
    Z_map = build_linkage(dist_df, method=args.linkage, optimal_order=args.optimal_order)

    print("\nGuardando archivos de salida...")
    series_df.to_csv(outdir / "series_potencia_turbinas.csv", index=False)
    corr_df.to_csv(outdir / "matriz_correlacion_pearson.csv")
    dist_df.to_csv(outdir / "matriz_distancias_pearson.csv")
    energy_df.to_csv(outdir / "resumen_energetico_calculado.csv", index=False)
    export_orange_input(series_df, outdir / "orange_input_series_potencia.csv")
    guardar_curvas_combinadas(curves, outdir / "curvas_potencia_utilizadas.csv")

    save_dendrogram_figure(
        Z_dend,
        labels=dist_df.index.tolist(),
        outpath=outdir / "figura_4_13_clustering_jerarquico_real.png",
        title=f"Clustering jerárquico real | Pearson + {args.linkage}",
    )

    save_distance_map(
        dist_df,
        Z_rows=Z_map,
        Z_cols=Z_map,
        outpath=outdir / "figura_4_14_mapa_distancias_real.png",
        title=f"Mapa de distancias real | Pearson + {args.linkage}",
    )

    report = build_report_text(
        certe_path=certe_path,
        wind_col=wind_col,
        timestep_hours=timestep_hours,
        linkage_method=args.linkage,
        curves=curves,
        best_pair=best_pair,
        worst_pair=worst_pair,
    )
    (outdir / "reporte_metodologico.txt").write_text(report, encoding="utf-8")

    print("\nListo. Archivos generados en:")
    print(f"  {outdir.resolve()}")
    print("\nRecomendación de uso en Orange:")
    print("  1) Carga 'orange_input_series_potencia.csv'")
    print("  2) Aplica Distances con Pearson")
    print("  3) Conecta a Hierarchical Clustering y Distance Map")
    print("  4) Compara visualmente con las figuras de la tesis")
    print("\nFin.")


if __name__ == "__main__":
    main()

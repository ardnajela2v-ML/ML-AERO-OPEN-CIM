#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
App web local para ejecutar el análisis ML tipo Orange.
Uso:
    pip install flask numpy pandas matplotlib scipy
    python app_orange.py
Luego abre: http://localhost:5050
"""

from __future__ import annotations

import io
import os
import sys
import json
import base64
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import gridspec
from scipy.cluster.hierarchy import dendrogram, leaves_list, linkage, optimal_leaf_ordering
from scipy.spatial.distance import squareform
from flask import Flask, request, jsonify, send_file, render_template_string

# ─────────────────────────────────────────────────────────────────────────────
# Configuración de turbinas
# ─────────────────────────────────────────────────────────────────────────────

THESIS_TURBINES: Dict[str, str] = {
    "Acciona AW 70/1500":  "acciona15_p1500_r35.csv",
    "Acciona 77/1.5 MW":   "acciona16_p1500_r38.5.csv",
    "Gamesa G80/2000":     "gamesa20_p2000_r40.csv",
    "Gamesa G90/2000":     "gamesa21_p2000_r45.csv",
    "Vestas V80/2000":     "vestas20_p2000_r40.csv",
    "Clipper 89 / 2.5 MW": "clipper25_p2500_r44.5.csv",
    "Vestas V90 / 3000":   "vestas30_p3000_r45.csv",
    "Alstom Power 110/3MW":"alstom30_p3000_r55.csv",
}

BASE_DIR = Path(__file__).resolve().parent
CURVES_DIR = BASE_DIR / "aerogeneradores"

# ─────────────────────────────────────────────────────────────────────────────
# Lógica de análisis (igual que el script original)
# ─────────────────────────────────────────────────────────────────────────────

def parse_filename_metadata(path: Path):
    stem = path.stem.lower()
    nominal_kw = rotor_radius_m = None
    for part in stem.replace("-","_").split("_"):
        if part.startswith("p"):
            try: nominal_kw = float(part[1:])
            except ValueError: pass
        elif part.startswith("r"):
            try: rotor_radius_m = float(part[1:])
            except ValueError: pass
    return nominal_kw, rotor_radius_m


def infer_timestep_hours(certe: pd.DataFrame) -> float:
    if "Date" in certe.columns and "h:min" in certe.columns:
        dt = pd.to_datetime(
            certe["Date"].astype(str).str.strip() + " " + certe["h:min"].astype(str).str.strip(),
            dayfirst=True, errors="coerce"
        ).dropna()
        if len(dt) >= 2:
            deltas = dt.diff().dropna().dt.total_seconds().to_numpy()
            if deltas.size:
                med = float(np.median(deltas))
                if med > 0:
                    return med / 3600.0
    return 10.0 / 60.0


def detectar_curvas(search_dirs: List[Path]) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    for turbine_name, filename in THESIS_TURBINES.items():
        for d in search_dirs:
            p = d / filename
            if p.exists():
                found[turbine_name] = p
                break
    return found


def read_curve(path: Path, turbine_name: str):
    df = pd.read_csv(path, header=None, names=["velocidad_m_s","potencia_kw"])
    df["velocidad_m_s"] = pd.to_numeric(df["velocidad_m_s"], errors="coerce")
    df["potencia_kw"]   = pd.to_numeric(df["potencia_kw"],   errors="coerce")
    df = df.dropna().sort_values("velocidad_m_s").drop_duplicates("velocidad_m_s")
    if df.empty:
        raise ValueError(f"Curva vacía: {path.name}")
    nom, rad = parse_filename_metadata(path)
    if nom is None: nom = float(df["potencia_kw"].max())
    diam = rad * 2.0 if rad is not None else None
    return {"name": turbine_name, "path": path, "nominal_kw": float(nom),
            "rotor_radius_m": rad, "rotor_diameter_m": diam, "df": df.reset_index(drop=True)}


def interp_power(curve_df: pd.DataFrame, ws: np.ndarray) -> np.ndarray:
    x = curve_df["velocidad_m_s"].to_numpy(float)
    y = curve_df["potencia_kw"].to_numpy(float)
    return np.interp(ws, x, y, left=y[0], right=y[-1])


def construir_series_potencia(certe: pd.DataFrame, wind_col: str, curves: list) -> pd.DataFrame:
    ws = pd.to_numeric(certe[wind_col], errors="coerce").to_numpy(float)
    return pd.DataFrame({c["name"]: interp_power(c["df"], ws) for c in curves})


def pearson_distance_matrix(series_df: pd.DataFrame):
    X = series_df.to_numpy(float).T
    corr = np.corrcoef(X)
    dist = 1.0 - corr
    np.fill_diagonal(corr, 1.0); np.fill_diagonal(dist, 0.0)
    cols = list(series_df.columns)
    return pd.DataFrame(corr, index=cols, columns=cols), pd.DataFrame(dist, index=cols, columns=cols)


def resumen_energetico(series_df: pd.DataFrame, curves: list, timestep_hours: float) -> pd.DataFrame:
    rows = []
    for c in curves:
        p = pd.to_numeric(series_df[c["name"]], errors="coerce")
        mean_kw = float(p.mean())
        aep = float(p.sum() * timestep_hours)
        nom = c["nominal_kw"]
        cf = (mean_kw / nom * 100.0) if nom > 0 else float("nan")
        rows.append({"Aerogenerador": c["name"], "Potencia nominal (kW)": nom,
                     "Radio rotor (m)": c["rotor_radius_m"], "Diametro rotor (m)": c["rotor_diameter_m"],
                     "Potencia media calculada (kW)": mean_kw,
                     "AEP calculada (kWh/año)": aep, "Factor de capacidad calculado (%)": cf})
    return pd.DataFrame(rows).sort_values("Aerogenerador").reset_index(drop=True)


def closest_and_farthest(dist_df: pd.DataFrame):
    names = list(dist_df.index)
    best = worst = None
    for i in range(len(names)):
        for j in range(i+1, len(names)):
            d = float(dist_df.iloc[i,j])
            item = (names[i], names[j], d)
            if best is None or d < best[2]: best = item
            if worst is None or d > worst[2]: worst = item
    return best, worst


def build_linkage(dist_df: pd.DataFrame, method: str, optimal_order: bool):
    cond = squareform(dist_df.to_numpy(float), checks=False)
    Z = linkage(cond, method=method)
    if optimal_order:
        Z = optimal_leaf_ordering(Z, cond)
    return Z


def fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def make_dendrogram_fig(Z, labels, title):
    fig, ax = plt.subplots(figsize=(12, 5))
    dendrogram(Z, labels=list(labels), orientation="right", leaf_font_size=10,
               color_threshold=0, above_threshold_color="black", ax=ax)
    ax.set_title(title, fontsize=13)
    ax.set_xlabel("Distancia de Pearson (1 − r)")
    ax.grid(False)
    fig.tight_layout()
    return fig


def make_distance_map_fig(dist_df: pd.DataFrame, Z_rows, Z_cols, title):
    row_order = leaves_list(Z_rows)
    col_order = leaves_list(Z_cols)
    ordered = dist_df.iloc[row_order, col_order]
    row_labels = ordered.index.tolist()
    col_labels = ordered.columns.tolist()

    fig = plt.figure(figsize=(11, 9))
    gs = gridspec.GridSpec(2, 2, width_ratios=[1.3,4.2], height_ratios=[1.3,4.2],
                           wspace=0.02, hspace=0.02)

    ax_top = fig.add_subplot(gs[0,1])
    dendrogram(Z_cols, ax=ax_top, no_labels=True, color_threshold=0, above_threshold_color="black")
    ax_top.set_xticks([]); ax_top.set_yticks([])
    for sp in ax_top.spines.values(): sp.set_visible(False)

    ax_left = fig.add_subplot(gs[1,0])
    dendrogram(Z_rows, ax=ax_left, orientation="left", no_labels=True,
               color_threshold=0, above_threshold_color="black")
    ax_left.set_xticks([]); ax_left.set_yticks([])
    for sp in ax_left.spines.values(): sp.set_visible(False)

    ax_heat = fig.add_subplot(gs[1,1])
    im = ax_heat.imshow(ordered.to_numpy(), aspect="auto", interpolation="nearest")
    ax_heat.set_xticks(np.arange(len(col_labels)))
    ax_heat.set_xticklabels(col_labels, rotation=90, fontsize=8)
    ax_heat.set_yticks(np.arange(len(row_labels)))
    ax_heat.set_yticklabels(row_labels, fontsize=8)
    ax_heat.yaxis.tick_right(); ax_heat.tick_params(axis="both", length=0)
    cbar = fig.colorbar(im, ax=ax_heat, fraction=0.046, pad=0.04)
    cbar.set_label("Distancia de Pearson (1 − r)")
    fig.suptitle(title, fontsize=13, y=0.99)
    fig.tight_layout()
    return fig


def run_analysis(certe_bytes: bytes, certe_name: str, wind_col: str,
                 linkage_method: str, optimal_order: bool,
                 extra_curve_files: list) -> dict:
    try:
        certe = pd.read_csv(io.BytesIO(certe_bytes), low_memory=False)

        extra_paths: List[Path] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            for fname, fbytes in extra_curve_files:
                p = tmp / fname
                p.write_bytes(fbytes)
                extra_paths.append(p)

            search_dirs = [tmp, CURVES_DIR]
            detected = detectar_curvas(search_dirs)

            for p in extra_paths:
                for tname, tfile in THESIS_TURBINES.items():
                    if p.name == tfile and tname not in detected:
                        detected[tname] = p

            if len(detected) < 2:
                return {"error": f"Se necesitan al menos 2 curvas de potencia. "
                                 f"Encontradas: {len(detected)}. "
                                 f"Coloca los CSV en la carpeta aerogeneradores/ junto al script, "
                                 f"o súbelos manualmente."}

            curves = [read_curve(path, name) for name, path in detected.items()]
            timestep_h = infer_timestep_hours(certe)

            series_df = construir_series_potencia(certe, wind_col, curves)
            corr_df, dist_df = pearson_distance_matrix(series_df)
            energy_df = resumen_energetico(series_df, curves, timestep_h)
            best_pair, worst_pair = closest_and_farthest(dist_df)

            Z_dend = build_linkage(dist_df, linkage_method, False)
            Z_map  = build_linkage(dist_df, linkage_method, optimal_order)

            # Figuras → base64 para previsualización inline
            fig_dend = make_dendrogram_fig(Z_dend, dist_df.index.tolist(),
                                           f"Clustering jerárquico | Pearson + {linkage_method}")
            fig_map  = make_distance_map_fig(dist_df, Z_map, Z_map,
                                              f"Mapa de distancias | Pearson + {linkage_method}")
            img_dend_b64 = fig_to_b64(fig_dend)
            img_map_b64  = fig_to_b64(fig_map)

            # Figuras → bytes para descarga (mayor DPI)
            def fig_bytes(fig) -> bytes:
                buf = io.BytesIO()
                fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
                buf.seek(0); return buf.read()

            fig_dend2 = make_dendrogram_fig(Z_dend, dist_df.index.tolist(),
                                             f"Clustering jerárquico | Pearson + {linkage_method}")
            fig_map2  = make_distance_map_fig(dist_df, Z_map, Z_map,
                                               f"Mapa de distancias | Pearson + {linkage_method}")
            dend_bytes = fig_bytes(fig_dend2)
            map_bytes  = fig_bytes(fig_map2)
            plt.close("all")

            orange_df = series_df.T.copy(); orange_df.index.name = "Aerogenerador"

            lines = [
                "REPORTE DE RECONSTRUCCIÓN ML TIPO ORANGE", "="*50, "",
                f"Archivo CERTE: {certe_name}",
                f"Columna de viento utilizada: {wind_col}",
                f"Paso temporal inferido: {timestep_h:.6f} horas",
                "Distancia utilizada: 1 - correlación de Pearson",
                f"Método de agrupamiento jerárquico: {linkage_method}", "",
                "Curvas de potencia utilizadas:",
            ]
            for c in curves:
                lines.append(f"  - {c['name']} | archivo={c['path'].name} | nominal={c['nominal_kw']:.1f} kW")
            lines += [
                "",
                f"Par más cercano: {best_pair[0]} <-> {best_pair[1]} | d={best_pair[2]:.8f}",
                f"Par más lejano:  {worst_pair[0]} <-> {worst_pair[1]} | d={worst_pair[2]:.8f}",
                "", "Notas metodológicas:",
                "- Utiliza exclusivamente los datos cargados por el usuario.",
                "- Distancia = 1 - correlación de Pearson.",
                "- No se generan datos sintéticos ni se reconstruyen variables inexistentes.",
            ]
            report_text = "\n".join(lines)

            def df_to_bytes(df: pd.DataFrame, index=True) -> bytes:
                buf = io.StringIO(); df.to_csv(buf, index=index); return buf.getvalue().encode()

            files = [
                {"name": "series_potencia_turbinas.csv",    "data": df_to_bytes(series_df, False), "mime": "text/csv"},
                {"name": "matriz_correlacion_pearson.csv",  "data": df_to_bytes(corr_df),           "mime": "text/csv"},
                {"name": "matriz_distancias_pearson.csv",   "data": df_to_bytes(dist_df),            "mime": "text/csv"},
                {"name": "resumen_energetico_calculado.csv","data": df_to_bytes(energy_df, False),   "mime": "text/csv"},
                {"name": "orange_input_series_potencia.csv","data": df_to_bytes(orange_df),          "mime": "text/csv"},
                {"name": "reporte_metodologico.txt",        "data": report_text.encode(),            "mime": "text/plain"},
                {"name": "figura_clustering_jerarquico.png","data": dend_bytes,                      "mime": "image/png"},
                {"name": "figura_mapa_distancias.png",      "data": map_bytes,                       "mime": "image/png"},
            ]

            summary = {
                "n_turbinas": len(curves),
                "turbinas": [c["name"] for c in curves],
                "n_pasos": len(series_df),
                "timestep_h": round(timestep_h, 6),
                "wind_col": wind_col,
                "best_pair":  {"a": best_pair[0],  "b": best_pair[1],  "d": round(best_pair[2], 8)},
                "worst_pair": {"a": worst_pair[0], "b": worst_pair[1], "d": round(worst_pair[2], 8)},
                "energy": energy_df[["Aerogenerador","AEP calculada (kWh/año)","Factor de capacidad calculado (%)"]].to_dict(orient="records"),
            }

            return {
                "error": None,
                "files": files,
                "images": [
                    {"name": "Clustering jerárquico", "b64": img_dend_b64},
                    {"name": "Mapa de distancias",    "b64": img_map_b64},
                ],
                "summary": summary,
                "report": report_text,
            }

    except Exception as e:
        import traceback
        return {"error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"}


# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
_file_store: dict = {}   # filename → (bytes, mime)


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orange ML – Análisis de Aerogeneradores</title>
<style>
  :root {
    --bg:#f4f6fb; --card:#fff; --border:#dde3ef;
    --accent:#2d6be4; --accent-h:#1a50c4; --ok:#1a9e5c;
    --warn:#e07b00; --err:#d02b2b; --text:#1e2433; --sub:#5a6480;
    --radius:10px; --shadow:0 2px 14px rgba(0,0,0,.09);
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);
       color:var(--text);min-height:100vh;padding:24px 16px}
  h1{font-size:1.5rem;font-weight:700;letter-spacing:-.3px}
  h2{font-size:1.05rem;font-weight:600;color:var(--sub);margin-bottom:14px}
  .header{max-width:880px;margin:0 auto 28px}
  .header p{color:var(--sub);margin-top:6px;font-size:.93rem;line-height:1.5}
  .card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
        box-shadow:var(--shadow);padding:24px;max-width:880px;margin:0 auto 22px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  label{display:block;font-size:.87rem;font-weight:600;margin-bottom:6px;color:var(--sub)}
  input[type=file],select{width:100%;padding:9px 12px;border:1px solid var(--border);
    border-radius:7px;font-size:.9rem;background:var(--bg);color:var(--text);outline:none;
    transition:border .18s}
  input[type=file]:focus,select:focus{border-color:var(--accent)}
  .hint{font-size:.78rem;color:var(--sub);margin-top:4px}
  .check-row{display:flex;align-items:center;gap:10px;margin-top:4px}
  .check-row input[type=checkbox]{width:17px;height:17px;accent-color:var(--accent);cursor:pointer}
  .check-row span{font-size:.9rem}
  button#runBtn{width:100%;padding:13px;border:none;border-radius:var(--radius);
    background:var(--accent);color:#fff;font-size:1rem;font-weight:700;
    cursor:pointer;transition:background .18s;margin-top:8px}
  button#runBtn:hover:not(:disabled){background:var(--accent-h)}
  button#runBtn:disabled{opacity:.6;cursor:default}
  #statusBox{display:none;align-items:center;gap:12px;padding:14px 18px;
    border-radius:8px;margin-top:16px;font-size:.92rem;font-weight:500}
  #statusBox.running{background:#e8f0ff;border:1px solid #b0c8ff;color:var(--accent);display:flex}
  #statusBox.ok{background:#e6f9f0;border:1px solid #80ddb0;color:var(--ok);display:flex}
  #statusBox.err{background:#fde8e8;border:1px solid #f5b0b0;color:var(--err);display:flex}
  .spinner{width:20px;height:20px;border:3px solid currentColor;border-top-color:transparent;
    border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0}
  @keyframes spin{to{transform:rotate(360deg)}}
  #results{display:none;max-width:880px;margin:0 auto}
  .summary-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:20px}
  .metric{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px 16px}
  .metric .val{font-size:1.3rem;font-weight:700;color:var(--accent)}
  .metric .lbl{font-size:.78rem;color:var(--sub);margin-top:2px}
  .pair-box{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
  .pair{border-radius:8px;padding:12px 16px;font-size:.88rem;font-weight:500;flex:1;min-width:200px}
  .pair.best{background:#e6f9f0;border:1px solid #80ddb0;color:var(--ok)}
  .pair.worst{background:#fff3e0;border:1px solid #f5c078;color:var(--warn)}
  .pair .tag{font-size:.74rem;opacity:.8;margin-bottom:2px}
  .images-row{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
  .img-wrap{border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--bg)}
  .img-wrap p{font-size:.82rem;color:var(--sub);padding:8px 12px;font-weight:600}
  .img-wrap img{width:100%;display:block}
  table{width:100%;border-collapse:collapse;font-size:.85rem}
  th{background:var(--bg);text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);
     color:var(--sub);font-weight:700;white-space:nowrap}
  td{padding:7px 10px;border-bottom:1px solid var(--border)}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#f8faff}
  .download-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
  .dl-btn{display:flex;align-items:center;gap:8px;padding:10px 14px;
    border:1px solid var(--border);border-radius:8px;background:var(--bg);
    text-decoration:none;color:var(--text);font-size:.85rem;font-weight:500;
    transition:border-color .18s,background .18s;cursor:pointer}
  .dl-btn:hover{border-color:var(--accent);background:#eef3ff;color:var(--accent)}
  .dl-icon{font-size:1.2rem}
  @media(max-width:600px){.grid2,.images-row{grid-template-columns:1fr}}
</style>
</head>
<body>

<div class="header">
  <h1>🌬️ Orange ML &mdash; Análisis de Aerogeneradores</h1>
  <p>Sube el archivo CERTE (CSV con serie temporal de velocidad de viento), configura las opciones
     y ejecuta el análisis. Se generarán las figuras de clustering y todos los archivos de salida.</p>
</div>

<div class="card">
  <h2>1 · Archivos de entrada</h2>
  <div style="margin-bottom:18px">
    <label>Archivo CERTE (CSV con serie temporal de viento) *</label>
    <input type="file" id="certeFile" accept=".csv">
    <p class="hint">Columnas esperadas: Date, h:min, WS_80mA_mean, WS_80mB_mean, …</p>
  </div>
  <div>
    <label>Curvas de potencia adicionales (opcional)</label>
    <input type="file" id="curveFiles" accept=".csv" multiple>
    <p class="hint">
      Si la carpeta <code>aerogeneradores/</code> ya existe junto al script, se detecta automáticamente.
      Solo sube aquí si quieres añadir curvas extra.<br>
      Nombres esperados: <code>gamesa20_p2000_r40.csv</code>, <code>vestas20_p2000_r40.csv</code>, etc.
    </p>
  </div>
</div>

<div class="card">
  <h2>2 · Columna de velocidad de viento</h2>
  <label>Columna seleccionada</label>
  <select id="windCol"><option value="">— sube el CERTE primero —</option></select>
  <p class="hint" id="windHint">La lista se actualiza automáticamente al seleccionar el CERTE.</p>
</div>

<div class="card">
  <h2>3 · Opciones de clustering</h2>
  <div class="grid2">
    <div>
      <label>Método de agrupamiento jerárquico</label>
      <select id="linkage">
        <option value="complete" selected>complete (recomendado)</option>
        <option value="average">average</option>
        <option value="single">single</option>
        <option value="weighted">weighted</option>
      </select>
    </div>
    <div>
      <label>Ordenamiento de hojas</label>
      <div class="check-row">
        <input type="checkbox" id="optOrder">
        <span>Optimal leaf ordering (más lento)</span>
      </div>
    </div>
  </div>
</div>

<div class="card">
  <button id="runBtn" onclick="runAnalysis()">▶ Ejecutar análisis</button>
  <div id="statusBox">
    <div class="spinner" id="spinner"></div>
    <span id="statusMsg"></span>
  </div>
</div>

<div id="results">
  <div class="card">
    <h2>Resumen</h2>
    <div class="summary-grid" id="metricsRow"></div>
    <div class="pair-box" id="pairsRow"></div>
  </div>
  <div class="card">
    <h2>Figuras generadas</h2>
    <div class="images-row" id="imagesRow"></div>
  </div>
  <div class="card">
    <h2>Resumen energético por turbina</h2>
    <div style="overflow-x:auto">
      <table id="energyTable"><thead></thead><tbody></tbody></table>
    </div>
  </div>
  <div class="card">
    <h2>Descargar archivos</h2>
    <div class="download-grid" id="downloadGrid"></div>
  </div>
</div>

<script>
document.getElementById('certeFile').addEventListener('change', async function() {
  const f = this.files[0];
  if (!f) return;
  const text = await f.text();
  const firstLine = text.split('\n')[0];
  const cols = firstLine.split(',').map(c => c.trim().replace(/^"|"$/g,''));
  const wsCols = cols.filter(c => c.includes('WS_') && c.includes('_mean'));
  const sel = document.getElementById('windCol');
  sel.innerHTML = '';
  const list = wsCols.length ? wsCols : cols;
  list.forEach((c,i) => {
    const o = document.createElement('option');
    o.value = c; o.text = c; if (i===0) o.selected = true;
    sel.appendChild(o);
  });
  document.getElementById('windHint').textContent =
    wsCols.length
      ? `${wsCols.length} columna(s) WS_*_mean detectadas.`
      : 'No se encontraron columnas WS_*_mean. Elige manualmente.';
});

function setStatus(type, msg) {
  const box = document.getElementById('statusBox');
  box.className = type;
  document.getElementById('statusMsg').textContent = msg;
  document.getElementById('spinner').style.display = type === 'running' ? 'block' : 'none';
}

async function runAnalysis() {
  const certeFile = document.getElementById('certeFile').files[0];
  if (!certeFile) { alert('Selecciona el archivo CERTE primero.'); return; }
  const windCol = document.getElementById('windCol').value;
  if (!windCol) { alert('Selecciona la columna de viento.'); return; }

  document.getElementById('runBtn').disabled = true;
  document.getElementById('results').style.display = 'none';
  setStatus('running', 'Procesando… esto puede tardar unos segundos.');

  const fd = new FormData();
  fd.append('certe', certeFile);
  fd.append('wind_col', windCol);
  fd.append('linkage', document.getElementById('linkage').value);
  fd.append('optimal_order', document.getElementById('optOrder').checked ? '1' : '0');
  const curves = document.getElementById('curveFiles').files;
  for (let i = 0; i < curves.length; i++) fd.append('curves', curves[i]);

  try {
    const res = await fetch('/analyze', { method: 'POST', body: fd });
    const data = await res.json();
    document.getElementById('runBtn').disabled = false;
    if (data.error) {
      setStatus('err', '❌ ' + data.error.split('\n')[0]);
      return;
    }
    setStatus('ok', `✅ Completado — ${data.summary.n_turbinas} turbinas, ${data.summary.n_pasos.toLocaleString()} pasos temporales`);
    renderResults(data);
  } catch(e) {
    document.getElementById('runBtn').disabled = false;
    setStatus('err', '❌ Error: ' + e.message);
  }
}

function renderResults(data) {
  const s = data.summary;
  document.getElementById('metricsRow').innerHTML = `
    <div class="metric"><div class="val">${s.n_turbinas}</div><div class="lbl">Turbinas analizadas</div></div>
    <div class="metric"><div class="val">${s.n_pasos.toLocaleString()}</div><div class="lbl">Pasos temporales</div></div>
    <div class="metric"><div class="val">${(s.timestep_h*60).toFixed(1)} min</div><div class="lbl">Paso temporal inferido</div></div>
    <div class="metric"><div class="val" style="font-size:.95rem">${s.wind_col}</div><div class="lbl">Columna de viento</div></div>
  `;
  document.getElementById('pairsRow').innerHTML = `
    <div class="pair best">
      <div class="tag">📌 PAR MÁS CERCANO (menor distancia Pearson)</div>
      <strong>${s.best_pair.a}</strong> ↔ <strong>${s.best_pair.b}</strong>
      <div style="margin-top:4px">d = ${s.best_pair.d}</div>
    </div>
    <div class="pair worst">
      <div class="tag">📍 PAR MÁS LEJANO (mayor distancia Pearson)</div>
      <strong>${s.worst_pair.a}</strong> ↔ <strong>${s.worst_pair.b}</strong>
      <div style="margin-top:4px">d = ${s.worst_pair.d}</div>
    </div>
  `;
  document.getElementById('imagesRow').innerHTML = data.images.map(img => `
    <div class="img-wrap">
      <p>${img.name}</p>
      <img src="data:image/png;base64,${img.b64}" alt="${img.name}">
    </div>
  `).join('');
  const tbl = document.getElementById('energyTable');
  tbl.querySelector('thead').innerHTML = '<tr><th>Aerogenerador</th><th>AEP (kWh/año)</th><th>Factor cap. (%)</th></tr>';
  tbl.querySelector('tbody').innerHTML = s.energy.map(r => `
    <tr>
      <td>${r['Aerogenerador']}</td>
      <td>${Math.round(r['AEP calculada (kWh/año)']).toLocaleString()}</td>
      <td>${r['Factor de capacidad calculado (%)'] != null ? (+r['Factor de capacidad calculado (%)']).toFixed(1) : '—'}</td>
    </tr>
  `).join('');
  const icons = {csv:'📊',png:'🖼️',txt:'📄'};
  document.getElementById('downloadGrid').innerHTML = data.files.map(f => {
    const ext = f.name.split('.').pop();
    return `<a class="dl-btn" onclick="downloadFile('${f.name}')">
      <span class="dl-icon">${icons[ext]||'📁'}</span>${f.name}
    </a>`;
  }).join('');
  document.getElementById('results').style.display = 'block';
  document.getElementById('results').scrollIntoView({behavior:'smooth'});
}

async function downloadFile(name) {
  const res = await fetch('/download?name=' + encodeURIComponent(name));
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/analyze", methods=["POST"])
def analyze():
    certe_file = request.files.get("certe")
    if certe_file is None:
        return jsonify({"error": "No se recibió el archivo CERTE."}), 400

    certe_bytes  = certe_file.read()
    certe_name   = certe_file.filename or "certe.csv"
    wind_col     = request.form.get("wind_col", "WS_80mA_mean")
    linkage_m    = request.form.get("linkage", "complete")
    opt_order    = request.form.get("optimal_order", "0") == "1"

    extra = [(f.filename, f.read()) for f in request.files.getlist("curves")]

    result = run_analysis(certe_bytes, certe_name, wind_col, linkage_m, opt_order, extra)

    if result.get("error"):
        return jsonify({"error": result["error"]})

    for fobj in result["files"]:
        _file_store[fobj["name"]] = (fobj["data"], fobj["mime"])

    return jsonify({
        "error":   None,
        "summary": result["summary"],
        "images":  result["images"],
        "report":  result["report"],
        "files":   [{"name": f["name"]} for f in result["files"]],
    })


@app.route("/download")
def download():
    name = request.args.get("name", "")
    if name not in _file_store:
        return "Archivo no encontrado", 404
    data, mime = _file_store[name]
    return send_file(io.BytesIO(data), mimetype=mime, as_attachment=True, download_name=name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n✅  Abre en tu navegador → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)

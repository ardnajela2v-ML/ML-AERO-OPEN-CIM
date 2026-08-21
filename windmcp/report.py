"""Portable HTML report for a completed WindMCP run."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, Iterable


def _value(value: Any) -> str:
    if value is None:
        return "No disponible"
    if isinstance(value, float):
        return f"{value:.4f}"
    return html.escape(str(value))


def _table(rows: Iterable[Dict[str, Any]], columns: Iterable[str]) -> str:
    columns = list(columns)
    body = []
    for row in rows:
        body.append("<tr>" + "".join(f"<td>{_value(row.get(column))}</td>" for column in columns) + "</tr>")
    header = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def write_final_report(result: Dict[str, Any], output_path: str | Path) -> str:
    """Write a self-contained final report from the JSON-safe run result."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    backtest = result.get("backtesting_multi_year", {})
    summary = result.get("historical_reconstruction", {})
    model = result.get("historical_reconstruction_model", {})
    validation = result.get("validation_forward", {}).get("metrics", {})
    direction = result.get("direction_correction", {})
    speed_selection = result.get("speed_model_selection", {})
    weibull = result.get("weibull_provenance", {})
    gap_rows = result.get("visualization", {}).get("gap_treatment", {}).get("yearly", [])
    future = result.get("visualization", {}).get("future_climate_25y", {})
    uncertainty = next(
        (row for row in backtest.get("summary", []) if row.get("method") == model.get("method")),
        {},
    )
    energy = result.get("energy_forward") or {}
    energy_block = (
        _table([energy], ["observed_period_energy_mwh", "predicted_period_energy_mwh", "delta_energy_pct"])
        if energy else "<p>No se calculó energía: no se proporcionó una curva de potencia.</p>"
    )
    backtest_rows = backtest.get("summary", [])
    html_text = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>WindMCP · Informe final</title>
<style>
body{{font-family:Arial,sans-serif;color:#17202a;max-width:1180px;margin:32px auto;padding:0 24px;line-height:1.45}}
h1,h2{{color:#123b5d}} .cards{{display:flex;gap:14px;flex-wrap:wrap}}
.card{{border:1px solid #d6e2eb;border-radius:8px;padding:14px 18px;min-width:180px;background:#f7fbfd}}
.label{{font-size:12px;color:#557080;text-transform:uppercase}} .value{{font-size:22px;font-weight:bold;margin-top:4px}}
table{{border-collapse:collapse;width:100%;margin:12px 0 24px;font-size:13px}}
th,td{{border:1px solid #d6e2eb;padding:7px;text-align:left}} th{{background:#eaf3f8}}
.note{{background:#fff8e6;border-left:4px solid #e0a72e;padding:12px;margin:16px 0}}
footer{{color:#667781;font-size:12px;margin-top:32px}}
</style></head><body>
<h1>WindMCP · Informe técnico final</h1>
<p>Ejecución <strong>{_value(result.get('run_id'))}</strong> · Referencia de velocidad: <strong>{_value(result.get('selected_reference'))}</strong> · Referencia de dirección: <strong>{_value(result.get('selected_direction_reference', result.get('selected_reference')))}</strong></p>
<div class="cards">
<div class="card"><div class="label">Método final</div><div class="value">{_value(model.get('method'))}</div></div>
<div class="card"><div class="label">Entrenamiento final</div><div class="value">{_value(model.get('training_year'))}</div></div>
<div class="card"><div class="label">RMSE backtesting</div><div class="value">{_value(uncertainty.get('RMSE'))} m/s</div></div>
<div class="card"><div class="label">R² backtesting</div><div class="value">{_value(uncertainty.get('R2'))}</div></div>
<div class="card"><div class="label">Horas reconstruidas</div><div class="value">{_value(summary.get('rows'))}</div></div>
<div class="card"><div class="label">Dirección / rosa</div><div class="value">{_value(direction.get('n_sectors'))} sectores</div></div>
</div>
<h2>Conclusión</h2>
<p>El modelo de velocidad final es <strong>{_value(model.get('method'))}</strong>. Se ajustó por mes exclusivamente con {_value(model.get('training_year'))} y se validó fuera de muestra con 2019. La dirección selecciona su propia referencia y se reconstruye por separado mediante {_value(direction.get('n_sectors'))} sectores condicionados por mes; ese modelo sólo alimenta la rosa de vientos.</p>
<h2>Validación cronológica y comparación</h2>
<p>Pliegues usados: {_value(backtest.get('valid_folds'))}. Política: {_value(speed_selection.get('selection'))}. El mapeo cuantílico estacional se exige cuando supera los límites fuera de muestra de sesgo, KS y parámetros Weibull. Los MCP cuantílicos por dirección se conservan sólo como diagnóstico.</p>
{_table(backtest_rows, ['family','method','N','R2','RMSE','MAE','Bias','KS','delta_weibull_k','delta_weibull_lambda','speed_selection_status','folds_passed'])}
<h2>Validación 2019</h2>
{_table([validation], ['N','R2','RMSE','MAE','Bias','KS','observed_weibull_k','predicted_weibull_k','delta_weibull_k','observed_weibull_lambda','predicted_weibull_lambda','delta_weibull_lambda','coverage_start','coverage_end'])}
<h2>Procedencia Weibull</h2>
<p>Todos los ajustes usan máxima verosimilitud de dos parámetros con localización fija en cero. El Weibull histórico se obtiene después de aplicar a cada mes del reanálisis el mapeo cuantílico de su mes correspondiente de 2018: k={_value(weibull.get('model_hindcast', {}).get('k'))}, λ={_value(weibull.get('model_hindcast', {}).get('lambda'))} m/s. La serie publicada conserva CERTE cuando existe y completa sus huecos con MCP: k={_value(weibull.get('project_series', {}).get('k'))}, λ={_value(weibull.get('project_series', {}).get('lambda'))} m/s.</p>
<h2>Datos faltantes M04</h2>
<p>Los datos CERTE permanecen identificados como observados. Los huecos se reconstruyen con MCP y se conservan como una fuente diferente.</p>
{_table(gap_rows, ['year','expected_hours','measured_hours','measured_availability_pct','mcp_filled_hours','unresolved_hours'])}
<h2>Dirección</h2>
<p>Referencia: {_value(result.get('selected_direction_reference', result.get('selected_reference')))}. Para cada mes se ajustó una corrección circular independiente en {_value(direction.get('n_sectors'))} sectores usando {_value(direction.get('N_train'))} pares concurrentes de 2018; la estación meteorológica y después el modelo anual actúan como respaldo cuando faltan pares. MAE angular antes: {_value(direction.get('mae_before_deg'))}°; después: {_value(direction.get('mae_after_deg'))}°.</p>
<h2>Incertidumbre empírica</h2>
<p>Intervalo global aproximado del 90% calculado con residuos fuera de muestra del método final: error P05 = <strong>{_value(uncertainty.get('error_p05'))} m/s</strong>; error P95 = <strong>{_value(uncertainty.get('error_p95'))} m/s</strong>.</p>
<p>Estas bandas representan incertidumbre de predicción del modelo; no sustituyen incertidumbre de medición, extrapolación vertical, representatividad espacial ni análisis de financiación.</p>
<h2>Energía</h2>
{energy_block}
<h2>Cobertura y limitaciones</h2>
<ul><li>Periodo reconstruido: {_value(summary.get('period_start'))} a {_value(summary.get('period_end'))}.</li>
<li>Horas observadas CERTE: {_value(summary.get('source_counts', {}).get('CERTE_observed'))}; horas modeladas: {_value(summary.get('source_counts', {}).get('MCP_reconstructed'))}.</li>
<li>2020 es un año parcial en los datos disponibles; se reporta como validación parcial.</li></ul>
<div class="note">{_value(future.get('note'))} La serie y su trazabilidad son reproducibles a partir de <code>result.json</code>, el manifiesto de archivos y los CSV de artefactos.</div>
<footer>WindMCP · versión {_value(result.get('software', {}).get('version'))}</footer>
</body></html>"""
    output.write_text(html_text, encoding="utf-8")
    return str(output)

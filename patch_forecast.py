import re

html_file = 'templates/index.html'
with open(html_file, 'r') as f:
    content = f.read()

# Locate mod-forecast
pattern = re.compile(r'(<section class="module" id="mod-forecast">.*?)(\n\s*<div class="toolbar mb-3">.*?</section>)', re.DOTALL)
match = pattern.search(content)

if match:
    header = match.group(1)
    
    # We will use a generic JS template for the tabs
    tabs_html = """
  <!-- Forecast Studio Tabs -->
  <div class="tabs">
    <button class="tab-btn active" data-tab="fs-wind">Viento</button>
    <button class="tab-btn" data-tab="fs-demand">Demanda</button>
    <button class="tab-btn" data-tab="fs-pml">PML</button>
    <button class="tab-btn" data-tab="fs-interest">Tasa de Interés</button>
    <button class="tab-btn" data-tab="fs-fx">Tipo de Cambio</button>
  </div>
"""

    def make_tab(key, active=False, has_toolbar=False):
        c = ' active' if active else ''
        toolbar = ""
        if has_toolbar:
            toolbar = """
  <div class="toolbar mb-3">
    <div class="toolbar-group">
      <label class="form-label">Horizonte</label>
      <select class="form-select" id="fc-horizon-select">
        <option value="1">+1h</option>
        <option value="6">+6h</option>
        <option value="24">+24h</option>
        <option value="72">+72h</option>
      </select>
    </div>
    <div class="toolbar-group">
      <label class="form-label">Modelo</label>
      <select class="form-select" id="fc-model-select">
        <option value="lightgbm">LightGBM</option>
        <option value="xgboost">XGBoost</option>
        <option value="random_forest">Random Forest</option>
      </select>
    </div>
    <button class="btn-primary" id="btn-load-forecast">Cargar Predicciones</button>
  </div>"""
        else:
            toolbar = f"""
  <div class="toolbar mb-3">
    <div class="toolbar-group">
      <label class="form-label">Seleccionar modelo entrenado en Arena</label>
      <select class="form-select" id="fs-{key}-model-select">
        <option value="">Aún no hay modelos entrenados</option>
      </select>
    </div>
  </div>"""

        return f"""
  <div id="fs-{key}-tab" class="tab-content{c}">
    {toolbar}
    <div class="card">
      <div class="card-header"><span class="card-title">Observado vs Predicho</span>
        <button class="btn-secondary" id="btn-reset-zoom-fs-{key}" type="button" style="font-size:.7rem;padding:2px 8px;margin-left:8px;display:none">Reset Zoom</button>
        <span class="cbadge" style="margin-left:8px;opacity:.75">scroll para zoom · drag para pan</span>
        <div class="metric-pills" id="fs-{key}-metric-pills"></div>
      </div>
      <div class="card-body" style="height:320px;">
        <canvas id="fs-{key}-main-chart"></canvas>
      </div>
    </div>

    <div class="grid-2 mt-3">
      <div class="card">
        <div class="card-header"><span class="card-title">Scatter: Obs vs Pred</span></div>
        <div class="card-body" style="height:250px;">
          <canvas id="fs-{key}-scatter-chart"></canvas>
        </div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-title">Distribución de Residuos</span></div>
        <div class="card-body" style="height:250px;">
          <canvas id="fs-{key}-residual-hist-chart"></canvas>
        </div>
      </div>
    </div>

    <div class="card mt-3">
      <div class="card-header"><span class="card-title">Estabilidad Temporal (RMSE)</span></div>
      <div class="card-body" style="height:220px;">
        <canvas id="fs-{key}-stability-chart"></canvas>
      </div>
    </div>
  </div>
"""

    new_content = header + "\n" + tabs_html
    new_content += make_tab('wind', True, True)
    new_content += make_tab('demand', False, False)
    new_content += make_tab('pml', False, False)
    new_content += make_tab('interest', False, False)
    new_content += make_tab('fx', False, False)
    new_content += "</section>"

    with open(html_file, 'w') as f:
        f.write(content.replace(match.group(0), new_content))
    print("HTML updated successfully.")

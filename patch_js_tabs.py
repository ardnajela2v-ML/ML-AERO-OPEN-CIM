import re

js_file = 'static/js/aero.js'
with open(js_file, 'r') as f:
    c = f.read()

script_to_add = """

// ─── FORECAST STUDIO TABS & DATA LOADING ─────────────────────────────────────
function bindForecastTabs() {
  document.querySelectorAll('.tab-btn[data-tab^="fs-"]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.tab-btn[data-tab^="fs-"], .tab-content[id^="fs-"]').forEach(el => el.classList.remove('active'));
      btn.classList.add('active');
      const targetId = btn.dataset.tab + '-tab';
      document.getElementById(targetId).classList.add('active');
      
      const key = btn.dataset.tab.replace('fs-', '');
      if (key !== 'wind') {
        populateForecastStudioSelect(key);
      }
    });
  });

  ['demand', 'pml', 'interest', 'fx'].forEach(key => {
    const sel = document.getElementById(`fs-${key}-model-select`);
    if (sel) {
      sel.addEventListener('change', () => loadForecastFromCache(key));
    }
  });
}

function populateForecastStudioSelect(key) {
  const cache = WP.arenaCache[key];
  const sel = document.getElementById(`fs-${key}-model-select`);
  if (!sel) return;
  
  if (!cache || !cache.fitted || Object.keys(cache.fitted).length === 0) {
    sel.innerHTML = '<option value="">Aún no hay modelos entrenados en Arena</option>';
    return;
  }
  
  const currentVal = sel.value;
  sel.innerHTML = '<option value="">-- Seleccionar Modelo --</option>';
  for (const modelName of Object.keys(cache.fitted)) {
    const m = cache.fitted[modelName];
    if (m.error) continue;
    const opt = document.createElement('option');
    opt.value = modelName;
    opt.textContent = `${modelName} (R² = ${m.metrics?.r2?.toFixed(2) || 'N/A'})`;
    sel.appendChild(opt);
  }
  
  if (currentVal && cache.fitted[currentVal] && !cache.fitted[currentVal].error) {
    sel.value = currentVal;
  } else if (cache.best_model) {
    sel.value = cache.best_model;
  }
  
  if (sel.value) {
    loadForecastFromCache(key);
  }
}

function loadForecastFromCache(key) {
  const cache = WP.arenaCache[key];
  const sel = document.getElementById(`fs-${key}-model-select`);
  if (!cache || !sel || !sel.value) return;

  const modelName = sel.value;
  const m = cache.fitted[modelName];
  if (!m || m.error) return;

  const y_pred = m.fitted_values || m.fitted || [];
  let y_true = [];
  let labels = [];

  if (key === 'interest' || key === 'fx') {
    // For interest/FX, the model is trained on a subset (e.g., from 2001)
    // The length of y_pred corresponds to the tail of history_annual
    const full_true = cache.history_annual;
    const full_years = cache.history_years;
    const train_len = y_pred.length;
    y_true = full_true.slice(full_true.length - train_len);
    labels = full_years.slice(full_years.length - train_len).map(y => y.toString());
  } else {
    // For demand/PML
    y_true = cache.history_annual;
    labels = cache.history_t ? cache.history_t.map(t => 't=' + t) : y_true.map((_, i) => 't=' + i);
  }

  const r = {
    y_true: y_true,
    y_pred: y_pred,
    labels: labels,
    metrics: m.metrics,
    residuals: {
       binned_bias: [] // Optional, could compute if needed
    },
    stability: [] // Annual data doesn't have temporal RMSE stability across months
  };

  const prefix = `fs-${key}`;
  renderForecastMainChart(r, prefix);
  renderScatterChart(r, prefix);
  renderResidualHistogram(r, prefix);
  
  // Hide stability for annual models
  document.getElementById(`${prefix}-stability-chart`).parentElement.parentElement.style.display = 'none';
  
  renderForecastMetricPills(r.metrics, prefix);
}

document.addEventListener('DOMContentLoaded', bindForecastTabs);
"""

if 'bindForecastTabs()' not in c:
    c += script_to_add
    
    with open(js_file, 'w') as f:
        f.write(c)
    print("Added tabs logic to aero.js")
else:
    print("Already added.")

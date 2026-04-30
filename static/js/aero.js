/* ═══════════════════════════════════════════════════
   AERO by OPEN CIM — Frontend application logic
═══════════════════════════════════════════════════ */

'use strict';

// ─── GLOBAL STATE ────────────────────────────────────────────────────────────
const WP = {
  sessionId: null,
  charts: {},
  trainTaskId: null,
  trainPollInterval: null,
  windStats:    null,
  technoResult: null,
  energyResult: null,
  compareResult:null,
  forecastResult:null,
  arenaCache: {},
};

// ─── CHART.JS DEFAULTS ───────────────────────────────────────────────────────
Chart.defaults.color = '#A6B3C8';
Chart.defaults.font.family = "'Inter', 'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.plugins.tooltip.backgroundColor = '#1B2230';
Chart.defaults.plugins.tooltip.borderColor = '#2A3346';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.padding = 10;

function gridColor() {
  return document.body.classList.contains('light') ? 'rgba(11,18,32,0.07)' : 'rgba(58,70,93,0.35)';
}
/* Theme-aware chart accent colors — engineering palette (dark = tech blue/violet/amber, light = deep navy) */
// OJO: NO usar `L` como nombre — colisiona con Leaflet (`window.L`) y rompe
// silenciosamente todos los L.map / L.tileLayer / L.imageOverlay en el módulo.
const isLight = () => document.body.classList.contains('light');
function acc()  { return isLight() ? '#0F5FA8' : '#4DA3FF'; }
function acc2() { return isLight() ? '#B45309' : '#F6A156'; }
function acc3() { return isLight() ? '#1E40AF' : '#9F7AEA'; }
function gold() { return isLight() ? '#B8860B' : '#F4C95D'; }
function mut()  { return isLight() ? '#7B8595' : '#A6B3C8'; }
function ink()  { return isLight() ? '#0B1220' : '#F4F7FB'; }

function updateChartDefaults() {
  const isLight = document.body.classList.contains('light');
  Chart.defaults.color           = isLight ? '#7B8595' : '#A6B3C8';
  Chart.defaults.font.family     = "'Inter','Segoe UI',system-ui,sans-serif";
  Chart.defaults.plugins.tooltip.backgroundColor = isLight ? '#FFFFFF' : '#1B2230';
  Chart.defaults.plugins.tooltip.borderColor     = isLight ? '#D6DEE9' : '#2A3346';
  Chart.defaults.plugins.tooltip.titleColor      = isLight ? '#0B1220' : '#F4F7FB';
  Chart.defaults.plugins.tooltip.bodyColor       = isLight ? '#3D4A5C' : '#A6B3C8';
}

function destroyChart(key) {
  if (WP.charts[key]) { WP.charts[key].destroy(); WP.charts[key] = null; }
}

// ─── DOCK BEHAVIOR ───────────────────────────────────────────────────────────
function initDock() {
  const dock = document.getElementById('topbar-dock');
  if (!dock) return;

  // Magnification: add .dock-near to immediate siblings of hovered nav-btn
  const navBtns = dock.querySelectorAll('.nav-btn');
  navBtns.forEach((btn, i) => {
    btn.addEventListener('mouseenter', () => {
      navBtns.forEach(b => b.classList.remove('dock-near'));
      if (i > 0) navBtns[i - 1].classList.add('dock-near');
      if (i < navBtns.length - 1) navBtns[i + 1].classList.add('dock-near');
    });
    btn.addEventListener('mouseleave', () => {
      navBtns.forEach(b => b.classList.remove('dock-near'));
    });
  });

  // Horizontal scroll with mouse wheel on the nav area
  const nav = dock.querySelector('.topbar-nav');
  if (nav) {
    nav.addEventListener('wheel', (e) => {
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        nav.scrollLeft += e.deltaY;
      }
    }, { passive: false });
  }
}

// ─── INIT ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  initDock();
  await initSession();
  bindNavigation();
  bindThemeToggle();
  bindDataHub();
  bindModelArena();
  bindForecastStudio();
  bindForecastTabs();
  bindDemandArena();
  bindPMLArena();
  bindInterestArena();
  bindFxArena();
  bindEnergyLab();
  bindAI();
  bindOverview();
  bindWind();
  bindAdvanced();
  bindHybrid();
  bindTechno();
  bindExport();
  bindMarketDataHub();
  await loadPresetFiles();   // populate preset file dropdown
  await loadMarketFiles();   // populate market data selectors
  await loadTurbineList();   // populate turbine dropdown from aerogeneradores
  setStatus('Plataforma iniciada — selecciona un parque eólico o carga tu archivo.');
});

// ─── SESSION ──────────────────────────────────────────────────────────────────
async function initSession() {
  try {
    const r = await api('/api/session/new', 'POST', {});
    WP.sessionId = r.session_id;
    document.getElementById('session-label').textContent = `Sesión ${r.session_id}`;
    document.getElementById('session-dot').classList.add('active');
  } catch (e) {
    document.getElementById('session-label').textContent = 'Sin conexión';
    toast('No se pudo conectar con el servidor. ¿Está running run.py?', 'error');
  }
}

// ─── NAVIGATION ──────────────────────────────────────────────────────────────
function bindNavigation() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const mod = btn.dataset.module;
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.module').forEach(m => m.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`mod-${mod}`).classList.add('active');
    });
  });
}

function bindThemeToggle() {
  // Auto-theme: user preference (localStorage) → system preference → time of day
  function pickAutoTheme() {
    const saved = localStorage.getItem('wp-theme');
    if (saved === 'light' || saved === 'dark') return saved;
    if (window.matchMedia) {
      if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
      if (window.matchMedia('(prefers-color-scheme: dark)').matches)  return 'dark';
    }
    const h = new Date().getHours();
    return (h >= 7 && h < 19) ? 'light' : 'dark';
  }
  const applyTheme = (t) => {
    document.body.classList.toggle('light', t === 'light');
    if (typeof updateChartDefaults === 'function') updateChartDefaults();
    if (typeof WP !== 'undefined' && WP.charts) {
      Object.values(WP.charts).forEach(c => { if (c) c.update(); });
    }
    // If Kriging map exists, swap tiles to match
    if (typeof KRIG !== 'undefined' && KRIG.map) {
      KRIG.map.eachLayer(l => { if (l instanceof L.TileLayer) KRIG.map.removeLayer(l); });
      const url = t === 'light'
        ? 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png'
        : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
      L.tileLayer(url, { maxZoom: 18, subdomains: 'abcd',
        attribution: '© OpenStreetMap · © CARTO' }).addTo(KRIG.map);
    }
  };
  applyTheme(pickAutoTheme());
  document.getElementById('theme-toggle').addEventListener('click', () => {
    const next = document.body.classList.contains('light') ? 'dark' : 'light';
    localStorage.setItem('wp-theme', next);
    applyTheme(next);
  });
}

// ─── PRESET FILES ────────────────────────────────────────────────────────────
async function loadPresetFiles() {
  try {
    const r = await api('/api/data/list_files', 'GET');
    const sel = document.getElementById('preset-file-select');
    const badge = document.getElementById('preset-count');
    badge.textContent = `${r.count} archivos`;

    if (!r.files || !r.files.length) {
      sel.innerHTML = '<option value="">— Carpeta de datos no encontrada —</option>';
      return;
    }

    // group by source type
    const merra = r.files.filter(f => f.source === 'merra2');
    const era5  = r.files.filter(f => f.source === 'era5');
    const other = r.files.filter(f => f.source === 'windnavigator');

    sel.innerHTML = '<option value="">— Selecciona un sitio / parque eólico —</option>';
    [['MERRA-2', merra], ['ERA5', era5], ['Windnavigator', other]].forEach(([label, group]) => {
      if (!group.length) return;
      const og = document.createElement('optgroup');
      og.label = label;
      group.forEach(f => {
        const opt = document.createElement('option');
        opt.value   = f.filename;
        opt.dataset.source = f.source;
        const loc = f.state ? `  ·  ${f.state}` : '';
        opt.textContent = `${f.display_name || f.filename}${loc}  (${f.size_mb} MB)`;
        opt.title = f.filename;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
  } catch (e) {
    document.getElementById('preset-count').textContent = 'Error';
  }
}

async function loadPresetFileByName(filename, source) {
  if (!filename) return;
  setStatus(`Cargando ${filename}…`);
  const badge = document.getElementById('preset-source-badge');
  badge.textContent = source?.toUpperCase() || 'AUTO';
  badge.style.display = '';

  try {
    const r = await api('/api/data/load_preset', 'POST', {
      session_id: WP.sessionId,
      filename,
      source: 'auto',
    });

    document.getElementById('badge-rows').textContent = `${r.n_rows.toLocaleString()} filas`;
    WP.meta = r.meta;
    // Invalidate cached wind analysis when new data is loaded
    WP.windStats = null;

    // meta grid
    const meta = r.meta;
    const grid = document.getElementById('meta-grid');
    grid.innerHTML = '';
    const items = [
      ['Sitio / Parque', r.display_name || r.filename],
      ['Estado',         r.state       || '—'],
      ['Municipio',      r.municipality || '—'],
      ['Filas',          r.n_rows?.toLocaleString()],
      ['Columnas',       meta.columns?.length],
      ['Desde',          meta.date_range?.[0]?.substring(0,10)],
      ['Hasta',          meta.date_range?.[1]?.substring(0,10)],
      ['Resolución',     meta.time_resolution_min != null ? `${meta.time_resolution_min} min` : '—'],
      ['Formato',        meta.reanalysis_type || meta.format?.toUpperCase()],
      ['Latitud',        meta.latitude != null ? meta.latitude : '—'],
      ['Longitud',       meta.longitude != null ? meta.longitude : '—'],
      ['Altura viento',  meta.wind_height_m != null ? `${meta.wind_height_m} m` : '—'],
    ];
    items.forEach(([label, val]) => {
      grid.innerHTML += `<div class="meta-item">
        <div class="meta-item-label">${label}</div>
        <div class="meta-item-val">${val ?? '—'}</div>
      </div>`;
    });

    // target col select
    const sel = document.getElementById('target-col-select');
    sel.innerHTML = '';
    (r.columns || []).forEach(c => {
      const opt = document.createElement('option');
      opt.value = c; opt.textContent = c;
      if (c === 'wind_speed') opt.selected = true;
      sel.appendChild(opt);
    });

    // set ws_height default
    if (meta.wind_height_m) {
      const h = document.getElementById('ws-height');
      if (h) h.value = meta.wind_height_m;
    }

    if (r.chart) renderLineChart('preview-chart', r.chart, {});

    const siteLabel = r.display_name || filename;
    toast(`${siteLabel} cargado — ${r.n_rows.toLocaleString()} filas`, 'success');
    setStatus(`Datos listos: ${siteLabel} | ${r.n_rows.toLocaleString()} registros | ${meta.reanalysis_type || meta.format}`);
  } catch (e) {
    toast(`Error cargando ${filename}: ${e.message}`, 'error');
    setStatus('Error al cargar archivo.');
  }
}

// ─── REAL TURBINE LIST ────────────────────────────────────────────────────────
async function loadTurbineList() {
  try {
    const r = await api('/api/energy/turbines', 'GET');
    const group = document.getElementById('turbine-real-group');
    if (!group) return;
    const fileTurbines = (r.turbines || []).filter(t => t.source === 'file');
    if (!fileTurbines.length) {
      group.innerHTML = '<option disabled>— Carpeta vacía —</option>';
      return;
    }
    group.innerHTML = '';
    fileTurbines.forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.filename;
      opt.dataset.source = 'file';
      opt.dataset.rated  = t.rated_power_kw || '';
      opt.dataset.rotor  = t.rotor_diameter_m || '';
      opt.textContent = `${t.name}  ${t.rated_power_kw ? '— ' + (t.rated_power_kw/1000).toFixed(1) + ' MW' : ''}  ⌀${t.rotor_diameter_m || '?'} m`;
      group.appendChild(opt);
    });
  } catch (e) { /* silent */ }
}

// ─── DATA HUB ─────────────────────────────────────────────────────────────────
function bindDataHub() {
  const zone  = document.getElementById('upload-zone');
  const input = document.getElementById('file-input');

  // drag & drop
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('drag-over');
    if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
  });
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { if (input.files[0]) uploadFile(input.files[0]); });

  document.getElementById('btn-upload').addEventListener('click', () => input.click());
  document.getElementById('btn-qc').addEventListener('click', runQC);

  // preset file loader
  document.getElementById('btn-load-preset').addEventListener('click', () => {
    const sel = document.getElementById('preset-file-select');
    const opt = sel.options[sel.selectedIndex];
    if (!sel.value) { toast('Selecciona un archivo primero', 'warn'); return; }
    loadPresetFileByName(sel.value, opt?.dataset?.source || 'auto');
  });
}

async function uploadFile(file) {
  setStatus(`Cargando ${file.name}…`);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('session_id', WP.sessionId);
  fd.append('source', document.getElementById('source-select').value);

  try {
    const r = await apiFD('/api/data/upload', fd);
    document.getElementById('badge-rows').textContent = `${r.n_rows.toLocaleString()} filas`;
    WP.windStats = null; // Invalidate cached wind analysis

    // meta grid
    const meta = r.meta;
    const grid = document.getElementById('meta-grid');
    grid.innerHTML = '';
    const items = [
      ['Filas', r.n_rows?.toLocaleString()],
      ['Columnas', meta.columns?.length],
      ['Desde', meta.date_range?.[0]?.substring(0,10)],
      ['Hasta', meta.date_range?.[1]?.substring(0,10)],
      ['Resolución', meta.time_resolution_min != null ? `${meta.time_resolution_min} min` : '—'],
      ['Formato', meta.source?.toUpperCase()],
    ];
    items.forEach(([label, val]) => {
      grid.innerHTML += `<div class="meta-item">
        <div class="meta-item-label">${label}</div>
        <div class="meta-item-val">${val ?? '—'}</div>
      </div>`;
    });

    // update target col select
    const sel = document.getElementById('target-col-select');
    sel.innerHTML = '';
    (r.columns || []).forEach(c => {
      const opt = document.createElement('option');
      opt.value = c; opt.textContent = c;
      if (c === 'wind_speed') opt.selected = true;
      sel.appendChild(opt);
    });

    // preview chart
    if (r.chart) renderLineChart('preview-chart', r.chart, { title: 'Vista previa' });

    toast(`Archivo cargado: ${r.n_rows.toLocaleString()} filas, ${r.columns.length} columnas`, 'success');
    setStatus(`Datos listos — ${r.n_rows.toLocaleString()} registros desde ${meta.date_range?.[0]?.substring(0,10)}`);
  } catch (e) {
    toast(`Error cargando archivo: ${e.message}`, 'error');
    setStatus('Error al cargar datos.');
  }
}

async function runQC() {
  setStatus('Ejecutando control de calidad…');
  try {
    const targetCol = document.getElementById('target-col-select').value;
    const r = await api('/api/data/quality', 'POST', {
      session_id: WP.sessionId, target_col: targetCol
    });

    const rpt = r.report;
    const container = document.getElementById('qc-results');
    container.classList.remove('hidden');

    const checks = [
      { key: 'qc_missing',  label: 'Datos Faltantes', detail: `${rpt.pct_missing}%` },
      { key: 'qc_gaps',     label: 'Gaps Consecutivos', detail: `máx ${rpt.max_consecutive_gap_periods} periodos` },
      { key: 'qc_physical', label: 'Límites Físicos', detail: `${rpt.n_out_physical} fuera de rango` },
      { key: 'qc_spikes',   label: 'Picos / Spikes', detail: `${rpt.n_spikes} detectados` },
      { key: 'qc_frozen',   label: 'Sensor Congelado', detail: `${rpt.n_frozen_periods} periodos` },
      { key: 'overall',     label: 'Estado General', detail: '' },
    ];

    container.innerHTML = checks.map(c => `
      <div class="qc-item ${rpt[c.key] || 'PASS'}">
        <div class="qc-label">${c.label}</div>
        <div class="qc-status">${rpt[c.key] || 'PASS'}</div>
        <div class="qc-detail">${c.detail}</div>
      </div>
    `).join('');

    // stats pills
    const stats = rpt.stats || {};
    const statsEl = document.getElementById('qc-stats-grid');
    statsEl.classList.remove('hidden');
    statsEl.innerHTML = [
      ['Media', stats.mean, 'm/s'], ['σ', stats.std, 'm/s'],
      ['Mín', stats.min, 'm/s'], ['P50', stats.p50, 'm/s'], ['Máx', stats.max, 'm/s'],
    ].map(([l, v, u]) =>
      `<div class="stat-pill">${l}: <span>${v ?? '—'} ${u}</span></div>`
    ).join('');

    // Weibull
    if (rpt.weibull) {
      statsEl.innerHTML += `<div class="stat-pill">Weibull k: <span>${rpt.weibull.k}</span></div>`;
      statsEl.innerHTML += `<div class="stat-pill">Weibull λ: <span>${rpt.weibull.lambda} m/s</span></div>`;
    }

    toast(`QC completado: ${rpt.overall}`, rpt.overall === 'PASS' ? 'success' : 'warn');
    setStatus(`QC: ${rpt.overall} — ${rpt.pct_missing}% faltantes, ${rpt.n_spikes} picos`);

    // populate per-variable quality table
    if (r.columns_report) populateQualityTable(r.columns_report);
  } catch (e) {
    toast(`Error en QC: ${e.message}`, 'error');
  }
}

function populateQualityTable(cols) {
  const card  = document.getElementById('quality-table-card');
  const badge = document.getElementById('quality-table-badge');
  const tbody = document.querySelector('#quality-tbl tbody');
  if (!card || !tbody) return;
  card.style.display = '';
  const keys = Object.keys(cols);
  if (badge) badge.textContent = keys.length + ' variables';
  tbody.innerHTML = keys.map(col => {
    const c = cols[col];
    const miss = c.missing_pct != null ? c.missing_pct.toFixed(1) + '%' : '—';
    const integ = c.missing_pct != null ? (100-c.missing_pct).toFixed(1) + '%' : '—';
    const integNum = c.missing_pct != null ? 100-c.missing_pct : 100;
    const cls = integNum>=99?'g':integNum>=95?'':'r';
    return `<tr>
      <td><strong>${col}</strong></td>
      <td>${c.mean!=null?c.mean.toFixed(3):'—'}</td>
      <td>${c.std!=null?c.std.toFixed(3):'—'}</td>
      <td>${c.min!=null?c.min.toFixed(2):'—'}</td>
      <td>${c.max!=null?c.max.toFixed(2):'—'}</td>
      <td>${miss}</td>
      <td><div style="display:flex;align-items:center;gap:.4rem">
        <div class="qbar"><div class="qfill${integNum<95?' bad':integNum<99?' warn':''}" style="width:${Math.min(100,integNum)}%"></div></div>
        <span class="cbadge ${cls}">${integ}</span></div></td>
    </tr>`;
  }).join('');
}

async function buildFeatures() {
  const horizons = document.getElementById('horizons-input').value
    .split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
  const testFrac = parseInt(document.getElementById('test-frac').value) / 100;
  const targetCol = document.getElementById('target-col-select').value;

  setStatus('Generando features…');
  try {
    const r = await api('/api/features/build', 'POST', {
      session_id: WP.sessionId,
      target_col: targetCol,
      horizons: horizons,
      test_frac: testFrac,
    });

    const sum = r.summary;
    const el = document.getElementById('features-summary');
    el.classList.remove('hidden');
    el.innerHTML = Object.entries(sum).map(([h, s]) => `
      <div class="feat-row">
        <div class="feat-item">h=+${h}h</div>
        <div class="feat-item">Muestras: <strong>${s.n_samples.toLocaleString()}</strong></div>
        <div class="feat-item">Features: <strong>${s.n_features}</strong></div>
        <div class="feat-item">Train: <strong>${s.n_train.toLocaleString()}</strong></div>
        <div class="feat-item">Test: <strong>${s.n_test.toLocaleString()}</strong></div>
        <div class="feat-item">μ_train: <strong>${s.target_mean_train} m/s</strong></div>
        <div class="feat-item">μ_test: <strong>${s.target_mean_test} m/s</strong></div>
      </div>
    `).join('<hr style="border-color:var(--border);margin:8px 0;">');

    // populate horizon selects
    ['train-horizon', 'fc-horizon-select'].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      sel.innerHTML = horizons.map(h =>
        `<option value="${h}">h = +${h}h</option>`).join('');
    });

    toast(`Features generadas para ${horizons.length} horizonte(s)`, 'success');
    setStatus(`Features listas — ${Object.keys(sum).length} horizontes`);
  } catch (e) {
    toast(`Error generando features: ${e.message}`, 'error');
  }
}

// ─── MODEL ARENA ─────────────────────────────────────────────────────────────
const FAST_MODELS = ['persistence','climatology','ridge','lightgbm','random_forest'];
const ML_MODELS   = ['random_forest','xgboost','lightgbm','ridge','mlp','svr'];

function bindModelArena() {
  // Tab switching (same pattern as Energy Lab)
  document.querySelectorAll('#mod-models .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tabName = btn.dataset.tab;
      document.querySelectorAll('#mod-models .tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('#mod-models .tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`${tabName}-tab`).classList.add('active');
    });
  });

  document.getElementById('btn-build-features').addEventListener('click', buildFeatures);
  document.getElementById('btn-train').addEventListener('click', trainModels);

  // chip toggle
  document.getElementById('model-tag-list').addEventListener('click', e => {
    const tag = e.target.closest('[data-model]');
    if (!tag) return;
    tag.classList.toggle('active');
    updateTrainBtnLabel();
  });

  // preset buttons
  document.getElementById('btn-models-all').addEventListener('click', () => {
    document.querySelectorAll('#model-tag-list [data-model]').forEach(t => t.classList.add('active'));
    updateTrainBtnLabel();
  });
  document.getElementById('btn-models-fast').addEventListener('click', () => {
    document.querySelectorAll('#model-tag-list [data-model]').forEach(t => {
      t.classList.toggle('active', FAST_MODELS.includes(t.dataset.model));
    });
    updateTrainBtnLabel();
  });
  document.getElementById('btn-models-ml').addEventListener('click', () => {
    document.querySelectorAll('#model-tag-list [data-model]').forEach(t => {
      t.classList.toggle('active', ML_MODELS.includes(t.dataset.model));
    });
    updateTrainBtnLabel();
  });
}

function getSelectedModels() {
  return [...document.querySelectorAll('#model-tag-list [data-model].active')]
    .map(t => t.dataset.model);
}

function updateTrainBtnLabel() {
  const sel = getSelectedModels();
  const lbl = document.getElementById('btn-train-label');
  if (lbl) lbl.textContent = sel.length === 9
    ? 'Entrenar todos los modelos'
    : `Entrenar ${sel.length} modelo${sel.length !== 1 ? 's' : ''}`;
}

async function trainModels() {
  const horizonH = parseInt(document.getElementById('train-horizon').value);
  const selectedModels = getSelectedModels();
  if (!selectedModels.length) { toast('Selecciona al menos un modelo', 'warn'); return; }

  const btn = document.getElementById('btn-train');
  const lbl = document.getElementById('btn-train-label');
  btn.disabled = true;
  if (lbl) lbl.textContent = 'Entrenando…';

  const progContainer = document.getElementById('train-progress-container');
  const progBar       = document.getElementById('train-progress-bar');
  const progLabel     = document.getElementById('train-progress-label');
  progContainer.classList.remove('hidden');
  progBar.style.width = '0%';

  setStatus(`Entrenando ${selectedModels.length} modelo(s) para h=+${horizonH}h…`);

  try {
    const r = await api('/api/models/train', 'POST', {
      session_id: WP.sessionId, horizon_h: horizonH, models: selectedModels
    });
    WP.trainTaskId = r.task_id;

    // stop any previous poll
    if (WP.trainPollInterval) clearInterval(WP.trainPollInterval);

    WP.trainPollCount = 0;
    WP.trainPollInterval = setInterval(async () => {
      WP.trainPollCount++;
      if (WP.trainPollCount > 600) {          // safety: ~20 min max
        clearInterval(WP.trainPollInterval);
        btn.disabled = false;
        updateTrainBtnLabel();
        toast('Timeout: el entrenamiento excedió 20 min.', 'error');
        return;
      }
      try {
        const s = await api(`/api/models/status/${WP.trainTaskId}`, 'GET');
        progBar.style.width = `${s.progress}%`;
        progLabel.textContent = s.message;

        if (s.status === 'done') {
          clearInterval(WP.trainPollInterval);
          progContainer.classList.add('hidden');
          btn.disabled = false;
          updateTrainBtnLabel();
          populateComparisonTable(s.result, horizonH);
          renderFeatureImportance(s.result);
          // Proyección anual de viento (predicción + 25 años)
          runWindAnnualForecast();
          toast(`Entrenamiento completo (${selectedModels.length} modelos)`, 'success');
          setStatus(`Modelos entrenados para h=+${horizonH}h`);
        } else if (s.status === 'error') {
          clearInterval(WP.trainPollInterval);
          progContainer.classList.add('hidden');
          btn.disabled = false;
          updateTrainBtnLabel();
          toast(`Error en entrenamiento: ${s.message}`, 'error');
          setStatus('Error durante el entrenamiento.');
        }
      } catch (e) { /* ignore poll errors */ }
    }, 2000);
  } catch (e) {
    btn.disabled = false;
    updateTrainBtnLabel();
    toast(`Error: ${e.message}`, 'error');
  }
}

function populateComparisonTable(result, horizonH) {
  const tbody = document.getElementById('comparison-tbody');
  tbody.innerHTML = '';

  const rows = Object.entries(result)
    .filter(([k, v]) => !k.startsWith('_') && v.metrics)
    .map(([k, v]) => ({ key: k, ...v.metrics, type: v.type }))
    .sort((a, b) => a.rmse - b.rmse);

  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="8" class="empty-msg">Sin resultados</td></tr>'; return; }

  const bestRmse = rows[0].rmse;
  rows.forEach((row, i) => {
    const isBest = i === 0;
    const skill = row.skill_vs_persistence;
    const skillStr = skill != null
      ? `<span style="color:${skill > 0 ? 'var(--green)' : 'var(--red)'}">${(skill*100).toFixed(1)}%</span>`
      : '—';
    tbody.innerHTML += `
      <tr class="${isBest ? 'best-row' : ''}">
        <td class="model-name">${isBest ? '★ ' : ''}${row.label}</td>
        <td><span class="badge badge--${row.type === 'ml' ? 'info' : 'warn'}">${row.type}</span></td>
        <td style="color:${isBest ? 'var(--green)' : ''}">${row.rmse?.toFixed(4)}</td>
        <td>${row.mae?.toFixed(4)}</td>
        <td>${row.r2?.toFixed(4)}</td>
        <td>${(row.nrmse * 100)?.toFixed(1)}%</td>
        <td>${row.smape?.toFixed(1)}%</td>
        <td>${skillStr}</td>
      </tr>`;
  });

  document.getElementById('best-model-badge').textContent = `Best: ${rows[0].label} — RMSE ${rows[0].rmse?.toFixed(4)}`;

  // populate overview model table
  populateOverviewModelTable(rows);
  renderModelBarOverview(result);

  // populate forecast model select
  const sel = document.getElementById('fc-model-select');
  sel.innerHTML = rows.filter(r => r.type === 'ml')
    .map(r => `<option value="${r.key || r.label.toLowerCase().replace(/ /g,'_')}">${r.label}</option>`)
    .join('');

  // populate validation
  populateValidationMetrics(rows[0]);
}

function renderFeatureImportance(result) {
  const mlModels = Object.entries(result)
    .filter(([k, v]) => !k.startsWith('_') && v.type === 'ml' && v.feature_importance?.length > 0);
  if (!mlModels.length) return;

  const [, bestRes] = mlModels[0];
  const fi = bestRes.feature_importance.slice(0, 12);

  destroyChart('fi');
  const ctx = document.getElementById('fi-chart').getContext('2d');
  WP.charts['fi'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: fi.map(d => d.feature),
      datasets: [{
        label: 'Importancia (%)',
        data: fi.map(d => d.importance),
        backgroundColor: fi.map((_, i) => i===0 ? acc()+'cc' : acc3()+(55+i).toString(16)),
        borderWidth: 0, borderRadius: 4,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: gridColor() }, ticks: { font:{size:9}, callback: v => `${v}%` } },
        y: { grid: { display: false }, ticks: { font: { size: 9 } } }
      }
    }
  });

  // populate FI table
  populateFiTable(fi);
}

function populateFiTable(fi) {
  const card = document.getElementById('fi-table-card');
  const tbody = document.querySelector('#fi-tbl tbody');
  if (!card || !tbody) return;
  card.style.display = '';
  const total = fi.reduce((s, d) => s + d.importance, 0) || 1;
  tbody.innerHTML = fi.map((d, i) => {
    const pct = (d.importance / total * 100).toFixed(1);
    const w   = Math.round(d.importance / fi[0].importance * 100);
    return `<tr class="${i===0?'best-row':''}"><td>${i+1}</td><td>${d.feature}</td>
      <td><div style="display:flex;align-items:center;gap:.5rem">
        <div class="fi-bar-wrap"><div class="fi-bar" style="width:${w}%;background:${i===0?acc():acc3()}"></div></div>
        <span>${d.importance.toFixed(2)}</span></div></td>
      <td>${pct}%</td></tr>`;
  }).join('');
}

function populateOverviewModelTable(rows) {
  const tbody = document.querySelector('#overview-model-tbl tbody');
  if (!tbody) return;
  tbody.innerHTML = rows.slice(0,6).map((r, i) => {
    const skill = r.skill_vs_persistence;
    const skillTag = skill != null
      ? `<span class="tag-${skill>0?'green':'red'}">${skill>0?'+':''}${(skill*100).toFixed(1)}%</span>`
      : '—';
    return `<tr class="${i===0?'best-row':''}"><td>${i===0?'★ ':''}${r.label}</td>
      <td style="color:${i===0?acc():''}"><strong>${r.rmse?.toFixed(4)}</strong></td>
      <td>${r.r2?.toFixed(4)}</td><td>${skillTag}</td></tr>`;
  }).join('');
}

// ─── FORECAST STUDIO ─────────────────────────────────────────────────────────
function bindForecastStudio() {
  document.getElementById('btn-load-forecast').addEventListener('click', loadForecast);
  ['wind', 'demand', 'demand_hourly', 'pml', 'pml_hourly', 'interest', 'fx'].forEach(key => {
    const btn = document.getElementById(`btn-download-fs-${key}-future`);
    if (btn) btn.addEventListener('click', () => downloadForecastFutureCsv(key));
  });
}

async function loadForecast() {
  const horizonH  = parseInt(document.getElementById('fc-horizon-select').value);
  const modelName = document.getElementById('fc-model-select').value;

  setStatus(`Cargando predicciones h=+${horizonH}h…`);
  try {
    const r = await api('/api/forecast/predictions', 'POST', {
      session_id: WP.sessionId, horizon_h: horizonH, model: modelName
    });

    renderForecastMainChart(r, 'fs-wind');
    renderScatterChart(r, 'fs-wind');
    renderResidualHistogram(r, 'fs-wind');
    renderStabilityChart(r.stability || [], 'fs-wind');
    renderBinnedBiasChart(r, 'fs-wind');
    renderForecastFutureChart('wind');
    renderForecastMetricPills(r.metrics, 'fs-wind');
    updateCalibration(r.calibration);
    populateValidationMetrics(r.metrics);

    setStatus(`Predicciones cargadas — RMSE ${r.metrics?.rmse?.toFixed(4)} m/s`);
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
}

function getPmlForecastStudioSide(cache) {
  if (!cache?.sides) return { sideKey: null, sideCache: null };
  const selectedSide = document.getElementById('pml-side')?.value;
  const sideKey = selectedSide && selectedSide !== 'both' && cache.sides[selectedSide]
    ? selectedSide
    : (cache.sides.venta ? 'venta' : Object.keys(cache.sides)[0]);
  return { sideKey, sideCache: cache.sides[sideKey] };
}

function getForecastFutureConfig(key) {
  if (key === 'wind') {
    const fc = WP.arenaCache.wind;
    if (!fc?.projection) return null;
    return {
      projection: fc.projection, bestModel: fc.best_model,
      rawValues: fc.raw_values || null, annualKind: fc.annual_kind || 'mean',
      ylabel: 'Velocidad del viento (m/s)', unitLabel: 'm/s', decimals: 2,
      scaler: v => v, filename: 'forecast_studio_wind_future_25y.csv',
      source: 'wind',
    };
  }

  if (key === 'demand' || key === 'demand_hourly') {
    const fc = WP.arenaCache.demand;
    if (!fc?.projection) return null;
    return {
      projection: fc.projection, bestModel: fc.best_model,
      rawValues: fc.raw_values || null, annualKind: fc.annual_kind || 'sum',
      ylabel: 'Demanda (MW)', unitLabel: 'MW', decimals: 1,
      scaler: v => v, filename: `forecast_studio_${key}_future_25y.csv`,
      source: key,
    };
  }

  if (key === 'pml' || key === 'pml_hourly') {
    const fc = WP.arenaCache.pml;
    const { sideKey, sideCache } = getPmlForecastStudioSide(fc);
    if (!sideCache?.projection) return null;
    return {
      projection: sideCache.projection, bestModel: sideCache.best_model,
      rawValues: sideCache.raw_values || null, annualKind: sideCache.annual_kind || 'sum',
      ylabel: `PML ($/kWh) — ${sideKey}`, unitLabel: '$/kWh', decimals: 4,
      scaler: v => v, filename: `forecast_studio_${key}_${sideKey}_future_25y.csv`,
      source: `${key}_${sideKey}`,
    };
  }

  if (key === 'interest') {
    const fc = WP.arenaCache.interest;
    if (!fc?.projection) return null;
    return {
      projection: fc.projection, bestModel: fc.best_model,
      rawValues: fc.raw_values || null, annualKind: fc.annual_kind || 'mean',
      ylabel: 'Tasa (%)', unitLabel: '%', decimals: 2,
      scaler: v => v, filename: 'forecast_studio_interest_future_25y.csv',
      source: 'interest',
    };
  }

  if (key === 'fx') {
    const fc = WP.arenaCache.fx;
    if (!fc?.projection) return null;
    return {
      projection: fc.projection, bestModel: fc.best_model,
      rawValues: fc.raw_values || null, annualKind: fc.annual_kind || 'mean',
      ylabel: 'MXN / USD', unitLabel: 'MXN/USD', decimals: 2,
      scaler: v => v, filename: 'forecast_studio_fx_future_25y.csv',
      source: 'fx',
    };
  }

  return null;
}

function renderForecastFutureChart(key) {
  const prefix = `fs-${key}`;
  const cfg = getForecastFutureConfig(key);
  const badge = document.getElementById(`${prefix}-future-badge`);
  const btn = document.getElementById(`btn-download-${prefix}-future`);
  const canvasId = `${prefix}-future-chart`;

  if (!cfg) {
    destroyChart(canvasId);
    if (badge) badge.textContent = 'Entrena el modelo para generar futuro';
    if (btn) btn.disabled = true;
    return;
  }

  // Si hay raw_values (serie histórica), renderizar continuación HORA A HORA.
  // Si no, fallback a vista anual con todos los modelos.
  let renderedHourly = false;
  if (cfg.rawValues && cfg.rawValues.length) {
    renderedHourly = renderArena25YearsHourly({
      canvasId, badgeId: `${prefix}-future-badge`,
      rawValues: cfg.rawValues, annualKind: cfg.annualKind,
      projection: cfg.projection, bestModel: cfg.bestModel,
      ylabel: cfg.ylabel, scaler: cfg.scaler,
      decimals: cfg.decimals, unitLabel: cfg.unitLabel,
    });
  }
  if (!renderedHourly) {
    renderArena25Years({
      canvasId, badgeId: `${prefix}-future-badge`,
      projection: cfg.projection, bestModel: cfg.bestModel,
      ylabel: cfg.ylabel, scaler: cfg.scaler,
      decimals: cfg.decimals, unitLabel: cfg.unitLabel,
    });
  }
  if (btn) btn.disabled = false;
}

function buildForecastFutureCsv(key) {
  const cfg = getForecastFutureConfig(key);
  if (!cfg) return '';
  const rows = [['subtab', 'modelo', 'best_model', 'year', 'predicted', 'unit', 'ci_low', 'ci_high', 'cagr']];
  for (const [model, proj] of Object.entries(cfg.projection || {})) {
    if (!proj || proj.error || !proj.years?.length) continue;
    proj.years.forEach((year, i) => {
      rows.push([
        cfg.source,
        model,
        model === cfg.bestModel ? 'yes' : 'no',
        year,
        cfg.scaler(proj.annual?.[i]),
        cfg.unitLabel,
        proj.ci_low?.[i] != null ? cfg.scaler(proj.ci_low[i]) : '',
        proj.ci_high?.[i] != null ? cfg.scaler(proj.ci_high[i]) : '',
        proj.cagr ?? '',
      ]);
    });
  }
  return rows.map(row => row.map(v => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(',')).join('\n');
}

function downloadForecastFutureCsv(key) {
  const cfg = getForecastFutureConfig(key);
  if (!cfg) {
    toast('Primero entrena o carga la proyección del subtab.', 'warn');
    return;
  }
  const csv = buildForecastFutureCsv(key);
  if (!csv) {
    toast('No hay datos futuros para descargar.', 'warn');
    return;
  }
  downloadText(csv, cfg.filename);
  toast(`Descargado: ${cfg.filename}`, 'success');
}

function renderForecastMainChart(r, prefix='fs-wind') {
  destroyChart(`${prefix}-main`);
  const ctx = document.getElementById(`${prefix}-main-chart`).getContext('2d');
  const resetBtn = document.getElementById(`btn-reset-zoom-${prefix}`);
  const hoverCommon = {
    pointRadius: 0,
    pointHoverRadius: 5,
    pointHoverBorderColor: '#fff',
    pointHoverBorderWidth: 2,
  };
  const datasets = [
    { label: 'Observado', data: r.y_true, borderColor: ink(),
      borderWidth: 1.5, fill: false, pointHoverBackgroundColor: ink(), ...hoverCommon },
    { label: 'Predicho', data: r.y_pred, borderColor: acc(),
      borderWidth: 1.5, fill: false, pointHoverBackgroundColor: acc(), ...hoverCommon },
    ...(r.persistence ? [{ label: 'Persistencia', data: r.persistence,
      borderColor: acc2(), borderWidth: 1, borderDash: [4,3], fill: false,
      pointHoverBackgroundColor: acc2(), ...hoverCommon }] : []),
  ];
  if (r.q10 && r.q90) {
    datasets.push({ label: 'Q10', data: r.q10, borderColor: acc3()+'44',
      borderWidth: 1, pointRadius: 0, pointHoverRadius: 0, fill: false });
    datasets.push({ label: 'Q90', data: r.q90, borderColor: acc3()+'44',
      borderWidth: 1, pointRadius: 0, pointHoverRadius: 0, fill: '-1', backgroundColor: acc3()+'12' });
  }
  WP.charts[`${prefix}-main`] = new Chart(ctx, {
    type: 'line',
    data: { labels: r.labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          enabled: true,
          backgroundColor: isLight() ? '#fff' : '#1B2230',
          titleColor: isLight() ? '#0B1220' : '#F4F7FB',
          bodyColor: isLight() ? '#3D4A5C' : '#A6B3C8',
          borderColor: isLight() ? '#D6DEE9' : '#2A3346',
          borderWidth: 1,
          padding: 10,
          callbacks: {
            title: (items) => items[0] ? `📅 ${items[0].label}` : '',
            label: (item) => `${item.dataset.label}: ${item.formattedValue} ${r.unit || 'm/s'}`,
          }
        },
        zoom: {
          pan: { enabled: true, mode: 'x', modifierKey: null },
          zoom: {
            wheel: { enabled: true, modifierKey: null },
            pinch: { enabled: true },
            drag: { enabled: false },
            mode: 'x',
            onZoomComplete: () => { if (resetBtn) resetBtn.style.display = 'inline-block'; },
          },
        },
      },
      scales: {
        x: { grid: { color: gridColor() }, ticks: { maxTicksLimit: 14, font:{size:9} } },
        y: { grid: { color: gridColor() }, title: { display: true, text: r.unit || 'm/s' }, ticks:{font:{size:9}} }
      }
    }
  });
  if (resetBtn) {
    resetBtn.style.display = 'none';
    resetBtn.onclick = () => {
      WP.charts[`${prefix}-main`]?.resetZoom();
      resetBtn.style.display = 'none';
    };
  }
}

function renderScatterChart(r, prefix='fs-wind') {
  destroyChart('scatter');
  const pts = r.y_true.map((y, i) => ({ x: y, y: r.y_pred[i] }))
    .filter(p => p.x != null && p.y != null);
  const maxVal = Math.max(...pts.map(p => Math.max(p.x, p.y)));
  const ctx = document.getElementById(`${prefix}-scatter-chart`).getContext('2d');
  WP.charts['scatter'] = new Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        { label: 'Obs vs Pred', data: pts, backgroundColor: acc()+'30',
          borderColor: acc()+'55', borderWidth: 1, pointRadius: 2 },
        { label: '1:1', data: [{x:0,y:0},{x:maxVal,y:maxVal}],
          type: 'line', borderColor: acc2()+'80',
          borderWidth: 1.5, borderDash: [5,3], pointRadius: 0, fill: false }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: gridColor() }, title: { display: true, text: `Observado (${r.unit || 'm/s'})` } },
        y: { grid: { color: gridColor() }, title: { display: true, text: `Predicho (${r.unit || 'm/s'})` } }
      }
    }
  });
}

function renderResidualHistogram(r, prefix='fs-wind') {
  destroyChart('residual-hist');
  const residuals = r.y_pred.map((yp, i) => yp - r.y_true[i])
    .filter(v => v != null && !isNaN(v));
  if (!residuals.length) return;
  const min = Math.min(...residuals), max = Math.max(...residuals);
  const nbins = 30;
  const binW = (max - min) / nbins || 1;
  const bins = Array.from({length: nbins}, (_, i) => ({
    x: +(min + (i + 0.5) * binW).toFixed(2),
    count: 0
  }));
  residuals.forEach(v => {
    const idx = Math.min(Math.floor((v - min) / binW), nbins - 1);
    bins[idx].count++;
  });
  const ctx = document.getElementById(`${prefix}-residual-hist-chart`).getContext('2d');
  destroyChart('residual-hist');
  WP.charts['residual-hist'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: bins.map(b => b.x.toFixed(2)),
      datasets: [{ label: 'Frecuencia', data: bins.map(b => b.count),
        backgroundColor: acc3()+'50', borderColor: acc3(), borderWidth: 1, borderRadius:2 }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, title: { display: true, text: `Residuo (${r.unit || 'm/s'})` } },
        y: { grid: { color: gridColor() }, title: { display: true, text: 'Frecuencia' } }
      }
    }
  });
}

function renderStabilityChart(stability, prefix='fs-wind') {
  destroyChart('stability');
  if (!stability.length) return;
  const ctx = document.getElementById(`${prefix}-stability-chart`).getContext('2d');
  WP.charts['stability'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: stability.map(s => s.period.substring(0,7)),
      datasets: [
        { label: 'RMSE (m/s)', data: stability.map(s => s.rmse),
          backgroundColor: acc()+'40', borderColor: acc(), borderWidth: 1.5, borderRadius:3 },
        { label: 'MAE (m/s)',  data: stability.map(s => s.mae),
          backgroundColor: acc3()+'40', borderColor: acc3(), borderWidth: 1.5, borderRadius:3 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'top' } },
      scales: {
        x: { grid: { display: false } },
        y: { grid: { color: gridColor() } }
      }
    }
  });
}

function buildBinnedBiasFromSeries(yTrue, yPred, nBins = 20) {
  const pts = yTrue.map((obs, i) => ({ obs, res: yPred[i] - obs }))
    .filter(p => p.obs != null && p.res != null && isFinite(p.obs) && isFinite(p.res));
  if (pts.length < 3) return [];

  const sorted = pts.map(p => p.obs).sort((a, b) => a - b);
  const binCount = Math.min(nBins, Math.max(3, Math.floor(pts.length / 3)));
  const quantile = q => {
    const pos = (sorted.length - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  };

  const edges = [];
  for (let i = 0; i <= binCount; i++) edges.push(quantile(i / binCount));
  const uniqueEdges = [...new Set(edges.map(v => Number(v.toFixed(8))))];
  if (uniqueEdges.length < 2) return [];

  const bins = [];
  for (let i = 0; i < uniqueEdges.length - 1; i++) {
    const lo = uniqueEdges[i];
    const hi = uniqueEdges[i + 1];
    const inBin = pts.filter(p => p.obs >= lo && (i === uniqueEdges.length - 2 ? p.obs <= hi : p.obs < hi));
    if (inBin.length < 3) continue;
    const residuals = inBin.map(p => p.res);
    const bias = residuals.reduce((a, b) => a + b, 0) / residuals.length;
    const rmse = Math.sqrt(residuals.reduce((a, b) => a + b * b, 0) / residuals.length);
    bins.push({
      ws_center: (lo + hi) / 2,
      bias,
      rmse,
      n: inBin.length,
    });
  }
  return bins;
}

function renderBinnedBiasChart(r, prefix='fs-wind') {
  const chartKey = `${prefix}-binned-bias`;
  destroyChart(chartKey);
  const canvas = document.getElementById(`${prefix}-binned-bias-chart`);
  if (!canvas) return;

  const binnedBias = r.residuals?.binned_bias?.length
    ? r.residuals.binned_bias
    : buildBinnedBiasFromSeries(r.y_true || [], r.y_pred || []);
  if (!binnedBias.length) return;

  const unit = r.unit || (prefix === 'fs-wind' ? 'm/s' : '');
  const xTitle = prefix === 'fs-wind'
    ? 'Velocidad observada (m/s)'
    : `Valor observado${unit ? ` (${unit})` : ''}`;

  const ctx = canvas.getContext('2d');
  WP.charts[chartKey] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: binnedBias.map(b => fcFormatNumber(b.ws_center, 2)),
      datasets: [{
        label: `Sesgo medio${unit ? ` (${unit})` : ''}`,
        data: binnedBias.map(b => b.bias),
        backgroundColor: binnedBias.map(b => b.bias >= 0 ? acc()+'60' : acc2()+'60'),
        borderWidth: 0, borderRadius:2,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: item => {
              const bin = binnedBias[item.dataIndex] || {};
              const bias = fcFormatNumber(item.parsed.y, 4);
              const rmse = fcFormatNumber(bin.rmse, 4);
              return [`Sesgo: ${bias} ${unit}`.trim(), `RMSE: ${rmse} ${unit}`.trim(), `n: ${bin.n || '—'}`];
            }
          }
        }
      },
      scales: {
        x: { grid: { display: false }, title: { display: true, text: xTitle } },
        y: { grid: { color: gridColor() }, title: { display: true, text: `Sesgo${unit ? ` (${unit})` : ''}` } }
      }
    }
  });
}

function renderForecastMetricPills(metrics, prefix='fs-wind', unit='m/s') {
  if (!metrics) return;
  const el = document.getElementById(`${prefix}-metric-pills`);
  if (!el) return;
  const fmt = (value, digits = 4) => value != null && isFinite(value) ? value.toFixed(digits) : '—';
  const pct = value => value != null && isFinite(value) ? (value * 100).toFixed(1) : '—';
  el.innerHTML = [
    ['RMSE', fmt(metrics.rmse), unit],
    ['MAE', fmt(metrics.mae), unit],
    ['R²', fmt(metrics.r2), ''],
    ['nRMSE', pct(metrics.nrmse), '%'],
    ['Skill', metrics.skill_vs_persistence != null ? pct(metrics.skill_vs_persistence) : '—', '%'],
  ].map(([l, v, u]) =>
    `<div class="metric-pill">${l}: <span>${v}${u}</span></div>`
  ).join('');
}

// ─── ENERGY LAB ──────────────────────────────────────────────────────────────
function bindEnergyLab() {
  // Tab switching
  document.querySelectorAll('#mod-energy .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tabName = btn.dataset.tab;
      document.querySelectorAll('#mod-energy .tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('#mod-energy .tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`${tabName}-tab`).classList.add('active');
    });
  });

  // Single turbine functionality
  document.getElementById('turbine-builtin').addEventListener('change', e => {
    const manual = document.getElementById('turbine-manual-form');
    manual.style.opacity = e.target.value ? '0.4' : '1';
  });
  document.getElementById('btn-run-energy').addEventListener('click', runEnergy);
  document.getElementById('btn-compare-turbines').addEventListener('click', compareTurbines);

  // Wind farm functionality
  bindFarmLayout();
}

async function runEnergy() {
  const builtin = document.getElementById('turbine-builtin').value;
  const turbineCfg = builtin ? { builtin_model: builtin } : {
    name: document.getElementById('t-name').value,
    rated_power_kw: parseFloat(document.getElementById('t-rated').value),
    cut_in: parseFloat(document.getElementById('t-cutin').value),
    rated_speed: parseFloat(document.getElementById('t-rated-spd').value),
    cut_out: parseFloat(document.getElementById('t-cutout').value),
    rotor_diameter_m: parseFloat(document.getElementById('t-rotor').value),
    hub_height_m: parseFloat(document.getElementById('t-hub').value),
  };

  const simCfg = {
    n_turbines: parseInt(document.getElementById('t-n').value),
    availability: parseFloat(document.getElementById('availability').value) / 100,
    electrical_losses: parseFloat(document.getElementById('elec-losses').value) / 100,
    wake_losses: parseFloat(document.getElementById('wake-losses').value) / 100,
    ws_height_m: parseFloat(document.getElementById('ws-height').value),
    altitude_m: parseFloat(document.getElementById('site-altitude').value) || null,
    extrapolation: document.getElementById('extrapolation').value,
  };

  setStatus('Ejecutando simulación energética…');
  try {
    const r = await api('/api/energy/compute', 'POST', {
      session_id: WP.sessionId, turbine: turbineCfg, simulation: simCfg
    });

    // KPIs
    const kpis = document.getElementById('energy-kpis');
    kpis.classList.remove('hidden');
    document.getElementById('kpi-cf').textContent     = `${r.summary.capacity_factor_pct}%`;
    document.getElementById('kpi-aep').textContent    = r.aep?.aep_p50_mwh?.toLocaleString() ?? r.summary.energy_net_mwh?.toLocaleString();
    document.getElementById('kpi-flh').textContent    = Math.round(r.summary.full_load_hours).toLocaleString();
    document.getElementById('kpi-energy-net').textContent = r.summary.energy_net_mwh?.toLocaleString();
    document.getElementById('kpi-p90').textContent    = r.aep?.aep_p90_mwh?.toLocaleString() ?? '—';

    // P-values card
    if (r.aep && r.aep.aep_p50_mwh) {
      const aepCard = document.getElementById('aep-card');
      aepCard.classList.remove('hidden');
      document.getElementById('p-value-row').innerHTML = [
        ['P10', r.aep.aep_p10_mwh, 'p10'],
        ['P50', r.aep.aep_p50_mwh, 'p50'],
        ['P75', r.aep.aep_p75_mwh, 'p75'],
        ['P90', r.aep.aep_p90_mwh, 'p90'],
      ].map(([l, v, cls]) => `
        <div class="p-val-item ${cls}">
          <div class="p-val-label">${l}</div>
          <div class="p-val-value">${v?.toLocaleString() ?? '—'} MWh</div>
        </div>`).join('');
    }

    renderPowerCurveChart(r.turbine);
    renderMonthlyEnergyChart(r.monthly);
    renderPowerTimeseries(r.timeseries);
    renderSensitivityChart(r.sensitivity || []);
    populateTurbineFicha(r.turbine, r.summary, simCfg);

    toast('Simulación completada', 'success');
    setStatus(`CF: ${r.summary.capacity_factor_pct}% | FLH: ${Math.round(r.summary.full_load_hours)} h | AEP P50: ${r.aep?.aep_p50_mwh?.toLocaleString()} MWh`);
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    setStatus('Error en simulación energética.');
  }
}

function renderPowerCurveChart(turbine) {
  destroyChart('power-curve');
  if (!turbine) return;
  const ctx = document.getElementById('power-curve-chart').getContext('2d');
  const nominal = turbine.rated_power_kw;
  WP.charts['power-curve'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: turbine.curve_ws,
      datasets: [
        { label: turbine.name, data: turbine.curve_pw,
          borderColor: acc(), backgroundColor: acc()+'20',
          borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3 },
        ...(nominal ? [{ label: `Nominal ${nominal} kW`,
          data: turbine.curve_ws.map(() => nominal),
          borderColor: gold()+'66', borderDash: [4,3], borderWidth: 1, pointRadius: 0, fill: false }] : [])
      ]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{font:{size:9},boxWidth:10}} },
      scales:{
        x:{grid:{color:gridColor()}, title:{display:true,text:'Velocidad (m/s)',font:{size:9}}, ticks:{font:{size:9}}},
        y:{grid:{color:gridColor()}, title:{display:true,text:'Potencia (kW)',font:{size:9}}, ticks:{font:{size:9}}}
      }
    }
  });
}

function renderMonthlyEnergyChart(monthly) {
  destroyChart('monthly-energy');
  if (!monthly) return;
  const ctx = document.getElementById('monthly-energy-chart').getContext('2d');
  // year-aware coloring like .bak
  const yearColors = [acc(), acc3(), gold(), acc2()];
  const yearMap = {}; let yi = 0;
  const bgColors = (monthly.labels || []).map(m => {
    const yr = String(m).split('-')[0];
    if (!(yr in yearMap)) yearMap[yr] = yearColors[yi++ % yearColors.length];
    return yearMap[yr] + 'cc';
  });
  WP.charts['monthly-energy'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: monthly.labels,
      datasets: [{ label: 'Energía neta (MWh)', data: monthly.energy_mwh,
        backgroundColor: bgColors, borderWidth: 0, borderRadius: 3 }]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false} },
      scales:{ x:{grid:{display:false}, ticks:{maxTicksLimit:18,font:{size:8},maxRotation:45}},
               y:{grid:{color:gridColor()}, ticks:{font:{size:9}}} } }
  });
}

function renderPowerTimeseries(ts) {
  destroyChart('power-ts');
  if (!ts) return;
  const ctx = document.getElementById('power-ts-chart').getContext('2d');
  WP.charts['power-ts'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ts.labels,
      datasets: [{ label: 'Potencia media diaria (kW)', data: ts.power_kw,
        borderColor: acc3(), backgroundColor: acc3()+'18',
        fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 }]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{font:{size:9},boxWidth:10}} },
      scales:{ x:{grid:{color:gridColor()}, ticks:{maxTicksLimit:12,font:{size:9}}},
               y:{grid:{color:gridColor()}, min:0, ticks:{font:{size:9}}} } }
  });
}

function populateTurbineFicha(turbine, summary, simCfg) {
  const card  = document.getElementById('turbine-ficha-card');
  const badge = document.getElementById('turbine-ficha-badge');
  const tLeft = document.querySelector('#turbine-ficha-tbl-left tbody');
  const tRight= document.querySelector('#turbine-ficha-tbl-right tbody');
  if (!card || !tLeft || !tRight || !turbine) return;
  card.style.display = '';
  if (badge) badge.textContent = turbine.name || 'Aerogenerador';
  const rowL = (k, v) => `<tr><td>${k}</td><td>${v ?? '—'}</td></tr>`;
  tLeft.innerHTML = [
    rowL('Nombre', turbine.name),
    rowL('Potencia nominal', `${turbine.rated_power_kw?.toLocaleString()} kW`),
    rowL('Diámetro rotor', `${turbine.rotor_diameter_m} m`),
    rowL('Altura de buje', `${turbine.hub_height_m} m`),
    rowL('Cut-in', `${turbine.cut_in_ms ?? turbine.cut_in} m/s`),
    rowL('Cut-out', `${turbine.cut_out_ms ?? turbine.cut_out} m/s`),
  ].join('');
  tRight.innerHTML = [
    rowL('Nº turbinas', simCfg?.n_turbines ?? 1),
    rowL('Factor de capacidad', summary?.capacity_factor_pct ? summary.capacity_factor_pct + '%' : '—'),
    rowL('FLH', summary?.full_load_hours ? Math.round(summary.full_load_hours).toLocaleString() + ' h' : '—'),
    rowL('Energía bruta', summary?.energy_gross_mwh ? summary.energy_gross_mwh.toLocaleString() + ' MWh' : '—'),
    rowL('Energía neta', summary?.energy_net_mwh ? summary.energy_net_mwh.toLocaleString() + ' MWh' : '—'),
    rowL('Disponibilidad', simCfg?.availability != null ? (simCfg.availability*100).toFixed(0) + '%' : '—'),
  ].join('');
}

function renderSensitivityChart(sensitivity) {
  destroyChart('sensitivity');
  if (!sensitivity.length) return;
  const ctx = document.getElementById('sensitivity-chart').getContext('2d');
  WP.charts['sensitivity'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: sensitivity.map(s => s.assumption),
      datasets: [{
        label: 'Δ AEP (%)',
        data: sensitivity.map(s => s.delta_pct),
        backgroundColor: sensitivity.map(s => s.delta_pct >= 0 ? acc() + '50' : acc2() + '50'),
        borderColor:     sensitivity.map(s => s.delta_pct >= 0 ? acc() : acc2()),
        borderWidth: 1,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: gridColor() }, title: { display: true, text: 'Δ AEP (%)' } },
        y: { grid: { display: false }, ticks: { font: { size: 10 } } }
      }
    }
  });
}

async function compareTurbines() {
  setStatus('Comparando turbinas…');
  try {
    const r = await api('/api/energy/compare_turbines', 'POST', {
      session_id: WP.sessionId,
      ws_height_m: parseFloat(document.getElementById('ws-height').value),
    });

    const tbody = document.getElementById('turbine-comp-tbody');
    tbody.innerHTML = (r.comparison || []).map((t, i) => `
      <tr ${i === 0 ? 'class="best-row"' : ''}>
        <td class="model-name">${i === 0 ? '★ ' : ''}${t.name}</td>
        <td>${t.rated_power_kw?.toLocaleString()}</td>
        <td>${t.rotor_diameter_m}</td>
        <td>${t.hub_height_m}</td>
        <td style="color:${i===0?'var(--green)':''}">${t.capacity_factor_pct}%</td>
        <td>${t.full_load_hours?.toLocaleString()}</td>
        <td>${t.mean_power_kw?.toLocaleString()}</td>
        <td><span class="badge badge--${i===0?'success':'info'}">#${t.rank}</span></td>
      </tr>`).join('');

    toast('Comparación completada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
}

// ─── VALIDATION ───────────────────────────────────────────────────────────────
function populateValidationMetrics(metrics) {
  if (!metrics) return;
  const el = document.getElementById('val-metrics-body');
  const items = [
    ['RMSE',  metrics.rmse?.toFixed(4), 'm/s'],
    ['MAE',   metrics.mae?.toFixed(4),  'm/s'],
    ['R²',    metrics.r2?.toFixed(4),   ''],
    ['Bias',  metrics.bias?.toFixed(4), 'm/s'],
    ['nRMSE', (metrics.nrmse*100)?.toFixed(2)+'%', ''],
    ['nMAE',  (metrics.nmae*100)?.toFixed(2)+'%',  ''],
    ['sMAPE', metrics.smape?.toFixed(2), '%'],
    ['Pearson r', metrics.pearson_r?.toFixed(4), ''],
    ['Skill vs Persistencia', metrics.skill_vs_persistence != null
      ? (metrics.skill_vs_persistence*100).toFixed(1)+'%' : '—', ''],
  ];
  el.innerHTML = `<div class="val-metric-list">${
    items.map(([l, v]) =>
      `<div class="val-metric-item">
        <span class="val-metric-name">${l}</span>
        <span class="val-metric-val">${v ?? '—'}</span>
      </div>`
    ).join('')
  }</div>`;
}

function updateCalibration(calib) {
  if (!calib) return;
  document.getElementById('calib-empirical').textContent = `${(calib.empirical_coverage*100).toFixed(1)}%`;
  document.getElementById('calib-error').textContent     = `${(calib.calibration_error*100).toFixed(1)}%`;
  document.getElementById('calib-sharp').textContent     = `${calib.sharpness?.toFixed(3)} m/s`;
  const errEl = document.getElementById('calib-error');
  const err = calib.calibration_error;
  errEl.style.color = err < 0.05 ? 'var(--green)' : err < 0.15 ? 'var(--yellow)' : 'var(--red)';
}

// ─── WIND FARM LAYOUT ────────────────────────────────────────────────────────
function bindFarmLayout() {
  const canvas = document.getElementById('farm-layout-canvas');
  const ctx = canvas.getContext('2d');
  let turbines = [];
  let scale = 1000; // meters per pixel
  let offsetX = canvas.width / 2;
  let offsetY = canvas.height / 2;
  let selectedTurbine = null;
  let isDragging = false;

  // Scale control
  document.getElementById('layout-scale').addEventListener('input', e => {
    scale = parseInt(e.target.value);
    document.getElementById('canvas-scale').textContent = `1:${scale}`;
    drawLayout();
  });

  // Rotation control
  document.getElementById('layout-rotation').addEventListener('input', e => {
    const rotation = parseInt(e.target.value);
    document.getElementById('rotation-value').textContent = `${rotation}°`;
    // TODO: Implement rotation
  });

  // Add turbine button
  document.getElementById('add-turbine-btn').addEventListener('click', () => {
    const x = Math.random() * 2000 - 1000; // -1000 to 1000 meters
    const y = Math.random() * 2000 - 1000;
    turbines.push({ id: turbines.length, x, y });
    drawLayout();
    updateTurbinesTable();
  });

  // Clear layout
  document.getElementById('clear-layout-btn').addEventListener('click', () => {
    turbines = [];
    drawLayout();
    updateTurbinesTable();
  });

  // Simulate farm
  document.getElementById('simulate-farm-btn').addEventListener('click', runFarmSimulation);

  // Canvas interaction
  canvas.addEventListener('mousedown', e => {
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left - offsetX) * scale;
    const y = (e.clientY - rect.top - offsetY) * scale;

    selectedTurbine = turbines.find(t => {
      const dx = t.x - x;
      const dy = t.y - y;
      return Math.sqrt(dx*dx + dy*dy) < 50; // 50m radius for selection
    });

    if (selectedTurbine) {
      isDragging = true;
    }
  });

  canvas.addEventListener('mousemove', e => {
    if (isDragging && selectedTurbine) {
      const rect = canvas.getBoundingClientRect();
      selectedTurbine.x = (e.clientX - rect.left - offsetX) * scale;
      selectedTurbine.y = (e.clientY - rect.top - offsetY) * scale;
      drawLayout();
    }
  });

  canvas.addEventListener('mouseup', () => {
    isDragging = false;
    updateTurbinesTable();
  });

  function drawLayout() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw grid
    ctx.strokeStyle = 'rgba(255,255,255,0.1)';
    ctx.lineWidth = 1;
    for (let i = 0; i < canvas.width; i += 50) {
      ctx.beginPath();
      ctx.moveTo(i, 0);
      ctx.lineTo(i, canvas.height);
      ctx.stroke();
    }
    for (let i = 0; i < canvas.height; i += 50) {
      ctx.beginPath();
      ctx.moveTo(0, i);
      ctx.lineTo(canvas.width, i);
      ctx.stroke();
    }

    // Draw turbines
    turbines.forEach(turbine => {
      const screenX = offsetX + turbine.x / scale;
      const screenY = offsetY + turbine.y / scale;

      // Turbine base
      ctx.fillStyle = turbine === selectedTurbine ? 'var(--accent)' : 'var(--text-primary)';
      ctx.beginPath();
      ctx.arc(screenX, screenY, 8, 0, 2 * Math.PI);
      ctx.fill();

      // Turbine label
      ctx.fillStyle = 'var(--text-primary)';
      ctx.font = '10px var(--font-mono)';
      ctx.textAlign = 'center';
      ctx.fillText(turbine.id.toString(), screenX, screenY - 12);
    });

    // Draw scale indicator
    ctx.fillStyle = 'var(--text-muted)';
    ctx.font = '12px var(--font)';
    ctx.textAlign = 'left';
    ctx.fillText(`${(50 * scale).toLocaleString()}m`, 10, canvas.height - 10);
    ctx.strokeStyle = 'var(--text-muted)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(10, canvas.height - 20);
    ctx.lineTo(60, canvas.height - 20);
    ctx.stroke();
  }

  function updateTurbinesTable() {
    const tbody = document.getElementById('farm-turbines-tbody');
    tbody.innerHTML = turbines.length === 0
      ? '<tr><td colspan="8" class="empty-msg">Añade turbinas y simula el parque</td></tr>'
      : turbines.map(t => `
          <tr>
            <td>${t.id}</td>
            <td>${t.x.toFixed(0)}</td>
            <td>${t.y.toFixed(0)}</td>
            <td id="aep-${t.id}">-</td>
            <td id="aep-no-wake-${t.id}">-</td>
            <td id="wake-loss-${t.id}">-</td>
            <td id="cf-${t.id}">-</td>
            <td><button class="btn btn-sm" onclick="removeTurbine(${t.id})">×</button></td>
          </tr>
        `).join('');
  }

  window.removeTurbine = (id) => {
    turbines = turbines.filter(t => t.id !== id);
    drawLayout();
    updateTurbinesTable();
  };

  drawLayout();
}

async function runFarmSimulation() {
  const turbines = Array.from(document.querySelectorAll('#farm-turbines-tbody tr')).map((tr, i) => {
    const cells = tr.querySelectorAll('td');
    if (cells.length < 3) return null;
    return [parseFloat(cells[1].textContent), parseFloat(cells[2].textContent)];
  }).filter(Boolean);

  if (turbines.length === 0) {
    toast('Añade al menos una turbina', 'error');
    return;
  }

  const builtin = document.getElementById('turbine-builtin').value;
  const turbineCfg = builtin ? { builtin_model: builtin } : {
    name: document.getElementById('t-name').value || 'Custom',
    rated_power_kw: parseFloat(document.getElementById('t-rated').value) || 2000,
    cut_in: parseFloat(document.getElementById('t-cutin').value) || 3.0,
    rated_speed: parseFloat(document.getElementById('t-rated-spd').value) || 12.0,
    cut_out: parseFloat(document.getElementById('t-cutout').value) || 25.0,
    rotor_diameter_m: parseFloat(document.getElementById('t-rotor').value) || 80,
    hub_height_m: parseFloat(document.getElementById('t-hub').value) || 80,
  };

  setStatus('Simulando parque eólico…');
  try {
    const r = await api('/api/farm/simulate', 'POST', {
      session_id: WP.sessionId,
      layout: turbines,
      turbine: turbineCfg,
      ws_height_m: parseFloat(document.getElementById('ws-height').value) || 50,
      availability: parseFloat(document.getElementById('availability').value) / 100 || 1.0,
      electrical_losses: parseFloat(document.getElementById('elec-losses').value) / 100 || 0.0,
    });

    // Update KPIs
    document.getElementById('farm-n-turbines').textContent = r.n_turbines;
    document.getElementById('farm-installed-mw').textContent = r.installed_mw;
    document.getElementById('farm-aep-mwh').textContent = r.aep_mwh.toLocaleString();
    document.getElementById('farm-cf-pct').textContent = `${r.cf_pct}%`;
    document.getElementById('farm-wake-loss-pct').textContent = `${r.wake_loss_pct}%`;
    document.getElementById('farm-flh').textContent = r.flh;

    // Update per-turbine table
    r.turbines.forEach(turbine => {
      document.getElementById(`aep-${turbine.i}`).textContent = turbine.aep_mwh;
      document.getElementById(`aep-no-wake-${turbine.i}`).textContent = turbine.aep_no_wake_mwh;
      document.getElementById(`wake-loss-${turbine.i}`).textContent = `${turbine.wake_loss_pct}%`;
      document.getElementById(`cf-${turbine.i}`).textContent = `${turbine.cf_pct}%`;
    });

    toast('Simulación del parque completada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
}

// ─── AI ASSISTANT ─────────────────────────────────────────────────────────────
function bindAI() {
  const input = document.getElementById('ai-input');
  document.getElementById('ai-send').addEventListener('click', sendAI);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') sendAI(); });
  document.getElementById('ai-chips').addEventListener('click', e => {
    if (e.target.classList.contains('chip')) {
      document.getElementById('ai-input').value = e.target.dataset.q;
      sendAI();
    }
  });
}

async function sendAI() {
  const inp = document.getElementById('ai-input');
  const q = inp.value.trim();
  if (!q) return;

  addAIMessage(q, true);
  inp.value = '';

  const typingEl = addAIMessage('…', false);
  try {
    const r = await api('/api/ai/chat', 'POST', {
      session_id: WP.sessionId, question: q
    });
    typingEl.querySelector('.ai-bubble').textContent = r.answer;
  } catch (e) {
    typingEl.querySelector('.ai-bubble').textContent = `Error: ${e.message}`;
  }
  const win = document.getElementById('ai-chat-window');
  win.scrollTop = win.scrollHeight;
}

function addAIMessage(text, isUser) {
  const win = document.getElementById('ai-chat-window');
  const div = document.createElement('div');
  div.className = `ai-msg ${isUser ? 'ai-msg--user' : 'ai-msg--system'}`;
  div.innerHTML = `
    <div class="ai-avatar">${isUser ? 'Tú' : 'WP'}</div>
    <div class="ai-bubble">${text}</div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
  return div;
}

// ─── CHART HELPERS ────────────────────────────────────────────────────────────
function renderLineChart(canvasId, chartData, opts = {}) {
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId)?.getContext('2d');
  if (!ctx) return;
  if (canvasId === 'preview-chart' && chartData?.datasets?.length) {
    const primary = isLight() ? '#111827' : acc();
    chartData.datasets[0].borderColor = primary;
    chartData.datasets[0].backgroundColor = primary + '26';
    chartData.datasets[0].pointBackgroundColor = primary;
  }
  WP.charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: chartData,
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: gridColor() }, ticks: { maxTicksLimit: 8 } },
        y: { grid: { color: gridColor() } }
      }
    }
  });
}

// ─── API HELPERS ──────────────────────────────────────────────────────────────
async function api(url, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body && method !== 'GET') opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

async function apiFD(url, formData) {
  const res = await fetch(url, { method: 'POST', body: formData });
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

// ─── UI HELPERS ───────────────────────────────────────────────────────────────
function setStatus(msg) {
  document.getElementById('status-text').textContent = msg;
}

function toast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ─── OVERVIEW MODULE ──────────────────────────────────────────────────────────
function bindOverview() {
  document.getElementById('btn-calc-stats').addEventListener('click', calcOverviewStats);
}

async function calcOverviewStats() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  setStatus('Calculando estadísticas del sitio…');
  document.getElementById('overview-status').textContent = 'Calculando…';
  try {
    const r = await api('/api/wind/stats', 'POST', { session_id: WP.sessionId });
    WP.windStats = r;
    renderOverviewKPIs(r.overview);
    renderDailyTS(r.overview?.daily_series);
    renderHistogramOverview(r.histogram);
    renderMClimOverview(r.climatology);
    renderHourlyOverview(r.diurnal);
    renderPowerDensityChart(r.overview);
    document.getElementById('overview-status').textContent = 'Listo';
    toast('Estadísticas calculadas', 'success');
    setStatus('Análisis de sitio completado.');
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    document.getElementById('overview-status').textContent = 'Error';
  }
}

function renderOverviewKPIs(ov) {
  if (!ov) return;
  const wb = ov.weibull;
  document.getElementById('kv-mean').textContent    = (ov.mean ?? '—') + ' m/s';
  document.getElementById('ks-mean').textContent    = `σ = ${ov.std ?? '—'} · p50 = ${ov.median ?? '—'}`;
  document.getElementById('kv-max').textContent     = (ov.max ?? '—') + ' m/s';
  document.getElementById('ks-max').textContent     = `p90 = ${ov.p90 ?? '—'} m/s`;
  document.getElementById('kv-weibull').textContent = wb ? `${wb.k} / ${wb.lambda}` : '—';
  const wbBadge = document.getElementById('badge-weibull-overview');
  if (wbBadge) wbBadge.textContent = wb ? `k=${wb.k}  λ=${wb.lambda} m/s` : '—';
  document.getElementById('kv-pd').textContent      = (ov.power_density_w_m2 ?? '—') + ' W/m²';
  document.getElementById('kv-ext').textContent     = (ov.h_above_15ms ?? '—') + ' h';
  document.getElementById('ks-ext').textContent     = `${ov.pct_above_15ms ?? '—'}% del total`;
  // % tiempo cerca de la velocidad media
  const elnear = document.getElementById('kv-near-mean');
  if (elnear) {
    elnear.textContent = ov.pct_near_mean != null ? `${ov.pct_near_mean}%` : '—';
    document.getElementById('ks-near-mean').textContent =
      ov.near_mean_band_ms != null ? `V̄ ± ${ov.near_mean_band_ms} m/s` : '';
  }
  document.getElementById('kv-temp').textContent    = ov.temp_mean != null ? ov.temp_mean + ' °C' : '—';
  document.getElementById('kv-pres').textContent    = ov.pressure_mean != null ? ov.pressure_mean + ' hPa' : '—';
  document.getElementById('kv-nobs').textContent    = (ov.n_obs ?? '—').toLocaleString?.() ?? ov.n_obs;
  // resolución temporal desde WP.meta (guardado al cargar el archivo)
  const resMin = WP.meta?.time_resolution_min;
  const resLabel = resMin != null
    ? (resMin < 60 ? `${resMin} min` : `${Math.round(resMin/60)}h`)
    : null;
  document.getElementById('ks-nobs').textContent =
    (ov.date_start ? `${ov.date_start} → ${ov.date_end}` : '') +
    (resLabel ? `  ·  Δt = ${resLabel}` : '');
}

function renderDailyTS(ds) {
  if (!ds) return;
  destroyChart('chart-daily-ts');
  const ctx = document.getElementById('chart-daily-ts')?.getContext('2d');
  if (!ctx) return;
  const resetBtn = document.getElementById('btn-reset-zoom-daily');
  const c = acc3();
  WP.charts['chart-daily-ts'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ds.labels,
      datasets: [{ label: 'V̄ diaria (m/s)', data: ds.values,
        borderColor: c, backgroundColor: c+'18',
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: c,
        pointHoverBorderColor: '#fff',
        pointHoverBorderWidth: 2,
        tension: 0.3, fill: true }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { font:{size:9}, boxWidth:10 } },
        tooltip: {
          enabled: true,
          backgroundColor: isLight() ? '#fff' : '#1B2230',
          titleColor: isLight() ? '#0B1220' : '#F4F7FB',
          bodyColor: isLight() ? '#3D4A5C' : '#A6B3C8',
          borderColor: isLight() ? '#D6DEE9' : '#2A3346',
          borderWidth: 1,
          padding: 10,
          displayColors: false,
          callbacks: {
            title: (items) => items[0] ? `📅 ${items[0].label}` : '',
            label: (item) => `Velocidad media: ${item.formattedValue} m/s`,
          }
        },
        zoom: {
          pan: { enabled: true, mode: 'x', modifierKey: null },
          zoom: {
            wheel: { enabled: true, modifierKey: null },
            pinch: { enabled: true },
            drag: { enabled: false },
            mode: 'x',
            onZoomComplete: () => { if (resetBtn) resetBtn.style.display = 'inline-block'; },
          },
        },
      },
      scales: {
        x: { grid: { color: gridColor() }, ticks: { maxTicksLimit: 14, font:{size:9} } },
        y: { grid: { color: gridColor() }, min: 0, ticks:{font:{size:9}, callback:v=>v+' m/s'} }
      }
    }
  });
  // Reset zoom button
  if (resetBtn) {
    resetBtn.style.display = 'none';
    resetBtn.onclick = () => {
      WP.charts['chart-daily-ts']?.resetZoom();
      resetBtn.style.display = 'none';
    };
  }
}

function renderMClimOverview(c) {
  if (!c) return;
  destroyChart('chart-mclim-overview');
  const ctx = document.getElementById('chart-mclim-overview')?.getContext('2d');
  if (!ctx) return;
  WP.charts['chart-mclim-overview'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: c.labels,
      datasets: [
        { label: 'Media m/s', data: c.mean, backgroundColor: acc3()+'99', borderRadius: 3 },
        { label: 'Máx m/s', data: c.max || c.mean_plus_std, type: 'line',
          borderColor: acc2(), pointRadius: 3, tension: 0.3, borderWidth: 1.5, backgroundColor: 'transparent' }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { font:{size:9}, boxWidth:8 } } },
      scales: { x: { grid:{display:false}, ticks:{font:{size:9}} },
                y: { grid: { color: gridColor() }, ticks:{font:{size:9}, callback:v=>v+' m/s'} } } }
  });
}

function renderHourlyOverview(d) {
  if (!d) return;
  destroyChart('chart-hourly-overview');
  const ctx = document.getElementById('chart-hourly-overview')?.getContext('2d');
  if (!ctx) return;
  WP.charts['chart-hourly-overview'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: d.hours,
      datasets: [{ label: 'V̄ horaria', data: d.mean,
        borderColor: gold(), backgroundColor: gold()+'25',
        fill: true, tension: 0.4, pointRadius: 2, borderWidth: 2 }]
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid:{display:false}, ticks:{font:{size:9}, callback:v=>v+'h'} },
        y: { grid: { color: gridColor() }, ticks:{font:{size:9}, callback:v=>v+' m/s'} } } }
  });
}

function renderModelBarOverview(result) {
  if (!result) return;
  destroyChart('chart-model-bar-overview');
  const ctx = document.getElementById('chart-model-bar-overview')?.getContext('2d');
  if (!ctx) return;
  const rows = Object.entries(result)
    .filter(([k,v]) => !k.startsWith('_') && v.metrics)
    .map(([,v]) => ({label: v.metrics.label || v.label, rmse: v.metrics.rmse}))
    .sort((a,b) => a.rmse - b.rmse).slice(0,8);
  const bestRmse = rows[0]?.rmse;
  WP.charts['chart-model-bar-overview'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: rows.map(r => r.label),
      datasets: [{ data: rows.map(r => r.rmse),
        backgroundColor: rows.map((r,i) => i===0 ? acc()+'cc' : mut()+'55'),
        borderRadius: 3 }]
    },
    options: { indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false}, tooltip:{callbacks:{label:ctx=>` RMSE: ${ctx.parsed.x?.toFixed(4)} m/s`}} },
      scales:{ x:{ grid:{color:gridColor()}, ticks:{font:{size:9}} },
               y:{ grid:{display:false}, ticks:{font:{size:9}} } } }
  });
  // update overview table badge
  const badge = document.getElementById('badge-best-model');
  if (badge && rows[0]) badge.textContent = '★ ' + rows[0].label;
}

function _weibullPDF(v, k, lam) {
  if (v <= 0) return 0;
  return (k / lam) * Math.pow(v / lam, k - 1) * Math.exp(-Math.pow(v / lam, k));
}

function _buildWeibullHistDatasets(hist) {
  const bw  = (hist.centers?.[1] - hist.centers?.[0]) || 1;
  let vals;
  if (hist.counts) {
    const tot = hist.counts.reduce((a,b)=>a+b, 0);
    vals = hist.counts.map(c => c / (tot * bw));          // → density (1/m·s)
  } else {
    vals = (hist.frequency || []).map(f => f / bw);       // freq=count/total → ÷bw → density
  }
  const wb   = hist.weibull;
  const barC = isLight() ? 'rgba(155,165,180,0.48)' : 'rgba(130,150,175,0.38)';
  const datasets = [{
    type:'bar', label:'Histograma', data:vals,
    backgroundColor:barC, borderColor: isLight()?'rgba(120,130,145,0.6)':'rgba(155,170,195,0.4)',
    borderWidth:0.5, borderRadius:1, order:2,
  }];
  if (wb) {
    const wbLine = (hist.centers||[]).map(v => _weibullPDF(v, wb.k, wb.lambda));
    datasets.push({
      type:'line', label:`Weibull  k=${wb.k}  λ=${wb.lambda} m/s`,
      data:wbLine, borderColor:acc2(), borderWidth:2.5,
      pointRadius:0, tension:0.4, order:1, fill:false,
    });
  }
  return { labels:(hist.centers||[]).map(v=>v.toFixed(1)), datasets };
}

function _weibullHistOptions(axisLabelSize) {
  const lc = isLight() ? '#64748b' : '#94a3b8';
  return {
    responsive:true, maintainAspectRatio:false,
    plugins:{ legend:{ position:'top', labels:{ font:{size:10}, boxWidth:13, boxHeight:10, padding:10 } } },
    scales:{
      x:{ grid:{display:false}, ticks:{maxTicksLimit:10, font:{size:axisLabelSize}},
          title:{display:true, text:'Velocidad del viento (m/s)', font:{size:9}, color:lc} },
      y:{ grid:{color:gridColor()}, ticks:{font:{size:axisLabelSize}},
          title:{display:true, text:'Densidad de probabilidad (1/m/s)', font:{size:9}, color:lc} },
    },
  };
}

function renderHistogramOverview(hist) {
  if (!hist) return;
  destroyChart('chart-hist-overview');
  const ctx = document.getElementById('chart-hist-overview')?.getContext('2d');
  if (!ctx) return;
  const { labels, datasets } = _buildWeibullHistDatasets(hist);
  WP.charts['chart-hist-overview'] = new Chart(ctx, {
    type: 'bar',
    data:{ labels, datasets },
    options: _weibullHistOptions(9),
  });
}

function renderPowerDensityChart(ov) {
  if (!ov?.monthly_power_density) return;
  destroyChart('chart-power-density');
  const ctx = document.getElementById('chart-power-density')?.getContext('2d');
  if (!ctx) return;

  const pd  = ov.monthly_power_density;
  const avg = ov.power_density_w_m2;
  const lc  = isLight() ? '#64748b' : '#94a3b8';

  // color bars: below avg → muted, above → accent
  const barColors = pd.values.map(v =>
    v >= avg ? acc() + 'cc' : mut() + '88'
  );

  // badges
  const cls = document.getElementById('badge-pd-class');
  const ann = document.getElementById('badge-pd-annual');
  if (cls) cls.textContent = ov.resource_class ?? '—';
  if (ann) ann.textContent = avg != null ? `${avg} W/m²  ·  media anual` : '—';

  WP.charts['chart-power-density'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: pd.labels,
      datasets: [{
        label: 'Densidad de potencia (W/m²)',
        data: pd.values,
        backgroundColor: barColors,
        borderRadius: 3,
        borderSkipped: false,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        annotation: avg != null ? {
          annotations: {
            meanLine: {
              type: 'line', yMin: avg, yMax: avg,
              borderColor: acc2(), borderWidth: 1.5, borderDash: [5, 4],
              label: { content: `V̄ ${avg} W/m²`, display: true,
                       position: 'end', font: { size: 9 }, color: acc2(),
                       backgroundColor: 'transparent' },
            }
          }
        } : {},
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 9 } } },
        y: {
          grid: { color: gridColor() }, ticks: { font: { size: 9 } },
          title: { display: true, text: 'W/m²', font: { size: 9 }, color: lc },
        },
      },
    },
  });
}

// ─── VIENTO MODULE ────────────────────────────────────────────────────────────
function bindWind() {
  document.getElementById('btn-calc-wind').addEventListener('click', calcWindAnalysis);
}

async function calcWindAnalysis() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  setStatus('Calculando análisis de viento…');
  document.getElementById('wind-status').textContent = 'Calculando…';
  try {
    WP.windStats = null; // force fresh fetch
    const r = await api('/api/wind/stats', 'POST', { session_id: WP.sessionId });
    WP.windStats = r;
    renderWindRose(r.rose);
    renderDiurnal(r.diurnal);
    renderClimatology(r.climatology);
    renderExtremes(r.extremes);
    renderWbHist(r.histogram);
    renderHistColored(r.histogram);
    renderMonthlyWS(r.climatology);
    renderMonthHourHeatmap(r.month_hour_heatmap);
    populateSectorTable(r.sectors);
    if (r.sectors?.dominant_sectors) {
      const bd = document.getElementById('wind-badge-dom');
      if (bd) bd.textContent = `Dom: ${r.sectors.dominant_sectors.join(', ')}`;
    }
    document.getElementById('wind-status').textContent = 'Listo';
    toast('Análisis de viento completado', 'success');
    setStatus('Análisis de viento completado.');
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    document.getElementById('wind-status').textContent = 'Error';
  }
}

// Cardinal labels for 16 sectors starting at N (0°), going CW
const ROSE_DIR_LABELS = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
                         'S','SSW','SW','WSW','W','WNW','NW','NNW'];
// Sequential 6-bin palette (low → high speed) — works on dark + light, no neon
const ROSE_PALETTE = ['#4A7BC8', '#52A6E8', '#48C2A3',
                      '#F2C94C', '#F39C5A', '#D9534F'];

function _niceRoseStep(maxPct) {
  const target = Math.max(maxPct, 1) / 5;
  const exp = Math.pow(10, Math.floor(Math.log10(target)));
  const f = target / exp;
  const nice = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
  return nice * exp;
}

function _drawWindRose(rose) {
  const canvas = document.getElementById('chart-rose');
  if (!canvas || !rose?.bin_matrix) return;

  const matrix = rose.bin_matrix;             // [16][6] in % of total
  const nSectors = matrix.length;
  const nBins    = matrix[0].length;
  const binLabels = (rose.bin_labels || []).map((l, i, arr) =>
    i === arr.length - 1 ? `≥${l.split('-')[0]}` : l);

  // Legend
  const legendEl = document.getElementById('rose-legend');
  if (legendEl) {
    legendEl.innerHTML =
      `<span style="opacity:.75;font-weight:600;letter-spacing:.02em">Velocidad (m/s)</span>` +
      binLabels.map((l, i) =>
        `<span style="display:inline-flex;align-items:center;gap:.3rem">
           <span style="width:14px;height:10px;background:${ROSE_PALETTE[i]};border-radius:1px"></span>${l}
         </span>`).join('');
  }

  // DPI-correct canvas sizing
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth  || 380;
  const cssH = canvas.clientHeight || 360;
  canvas.width  = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const cx = cssW / 2;
  const cy = cssH / 2 + 4;
  const R  = Math.min(cssW, cssH) / 2 - 30;

  const light    = isLight();
  const ringCol  = light ? 'rgba(11,18,32,0.10)' : 'rgba(166,179,200,0.18)';
  const spokeCol = light ? 'rgba(11,18,32,0.12)' : 'rgba(166,179,200,0.20)';
  const txtCol   = light ? '#3D4A5C' : '#A6B3C8';
  const txtBold  = light ? '#0B1220' : '#F4F7FB';
  const sepCol   = light ? 'rgba(255,255,255,0.85)' : 'rgba(11,18,32,0.55)';
  const labelBg  = light ? 'rgba(255,255,255,0.85)' : 'rgba(18,24,35,0.85)';

  // Ring scale
  const sectorTotals = matrix.map(row => row.reduce((s, v) => s + v, 0));
  const maxPct = Math.max(...sectorTotals, 1);
  const step   = _niceRoseStep(maxPct);
  const nRings = Math.max(2, Math.ceil(maxPct / step));
  const rMax   = step * nRings;

  // Concentric rings
  ctx.lineWidth   = 1;
  ctx.strokeStyle = ringCol;
  for (let k = 1; k <= nRings; k++) {
    const r = (R * k) / nRings;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, 2 * Math.PI);
    ctx.stroke();
  }

  // Radial spokes
  ctx.strokeStyle = spokeCol;
  for (let i = 0; i < nSectors; i++) {
    const ang = (i * 360 / nSectors - 90) * Math.PI / 180;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + R * Math.cos(ang), cy + R * Math.sin(ang));
    ctx.stroke();
  }

  // Stacked annular sectors (low speed near center, high speed outside)
  const sectorWidthDeg = 360 / nSectors;
  const padFrac        = 0.12;  // gap between sectors
  const halfW          = (sectorWidthDeg * (1 - padFrac)) / 2;

  for (let i = 0; i < nSectors; i++) {
    const centerDeg = i * sectorWidthDeg - 90;  // N at top
    const a0 = (centerDeg - halfW) * Math.PI / 180;
    const a1 = (centerDeg + halfW) * Math.PI / 180;
    let cum = 0;
    for (let b = 0; b < nBins; b++) {
      const v = matrix[i][b];
      if (v <= 0) continue;
      const r0 = (R * cum) / rMax;
      const r1 = (R * (cum + v)) / rMax;
      ctx.beginPath();
      ctx.arc(cx, cy, r1, a0, a1, false);
      ctx.arc(cx, cy, r0, a1, a0, true);
      ctx.closePath();
      ctx.fillStyle   = ROSE_PALETTE[b] + 'E6';
      ctx.fill();
      ctx.lineWidth   = 0.7;
      ctx.strokeStyle = sepCol;
      ctx.stroke();
      cum += v;
    }
  }

  // % ring labels (along N axis)
  ctx.font         = '600 9px "JetBrains Mono", ui-monospace, monospace';
  ctx.textAlign    = 'left';
  ctx.textBaseline = 'middle';
  for (let k = 1; k <= nRings; k++) {
    const r = (R * k) / nRings;
    const txt = `${(step * k).toFixed(step < 1 ? 1 : 0)}%`;
    const tw = ctx.measureText(txt).width;
    ctx.fillStyle = labelBg;
    ctx.fillRect(cx + 3, cy - r - 7, tw + 4, 12);
    ctx.fillStyle = txtCol;
    ctx.fillText(txt, cx + 5, cy - r);
  }

  // Direction labels
  const labelR = R + 16;
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'middle';
  for (let i = 0; i < nSectors; i++) {
    const ang = (i * sectorWidthDeg - 90) * Math.PI / 180;
    const x = cx + labelR * Math.cos(ang);
    const y = cy + labelR * Math.sin(ang);
    const cardinal = (i % 4 === 0);
    ctx.font      = cardinal
      ? '700 11px "JetBrains Mono", ui-monospace, monospace'
      : '500 9px "JetBrains Mono", ui-monospace, monospace';
    ctx.fillStyle = cardinal ? txtBold : txtCol;
    ctx.fillText(ROSE_DIR_LABELS[i], x, y);
  }

  // Stats annotation (bottom-left)
  const meanWS = (rose.mean_speed || []).reduce((acc, v, i) =>
    acc + v * (sectorTotals[i] / 100), 0);
  ctx.font         = '500 10px Inter, system-ui, sans-serif';
  ctx.textAlign    = 'left';
  ctx.textBaseline = 'bottom';
  ctx.fillStyle    = txtCol;
  ctx.fillText(`media = ${meanWS.toFixed(2)} m/s`, 6, cssH - 18);
  ctx.fillText(`calma = ${(rose.calm_pct ?? 0).toFixed(1)}%`, 6, cssH - 4);
}

function renderWindRose(rose) {
  if (!rose) return;
  destroyChart('chart-rose');  // safe: handles previous Chart.js instance if any
  if (!document.getElementById('chart-rose')) return;
  document.getElementById('rose-dom').textContent =
    `${rose.dominant_direction} ${rose.dominant_pct}%`;

  // Cache rose for redraw on resize / theme change
  WP._lastRose = rose;
  _drawWindRose(rose);

  // Re-bind resize listener (debounced)
  if (!WP._roseResizeBound) {
    let t;
    window.addEventListener('resize', () => {
      clearTimeout(t);
      t = setTimeout(() => { if (WP._lastRose) _drawWindRose(WP._lastRose); }, 120);
    });
    WP._roseResizeBound = true;
  }

  // Stub so destroyChart() / theme-update loop don't throw on next render
  WP.charts['chart-rose'] = {
    destroy: () => {
      const c = document.getElementById('chart-rose');
      if (c) c.getContext('2d').clearRect(0, 0, c.width, c.height);
    },
    update: () => { if (WP._lastRose) _drawWindRose(WP._lastRose); }
  };
}

function renderDiurnal(d) {
  if (!d) return;
  destroyChart('chart-diurnal');
  const ctx = document.getElementById('chart-diurnal')?.getContext('2d');
  if (!ctx) return;
  const a  = acc();
  const upper = d.mean.map((v,i) => parseFloat((v+(d.std?.[i]||0)).toFixed(3)));
  const lower = d.mean.map((v,i) => parseFloat((v-(d.std?.[i]||0)).toFixed(3)));
  WP.charts['chart-diurnal'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: d.hours,
      datasets: [
        { label:'Media', data:d.mean, borderColor:a, backgroundColor:a+'20', fill:'+1', tension:0.4, pointRadius:2, borderWidth:2 },
        { label:'Media+σ', data:upper, borderColor:a+'44', borderDash:[3,2], pointRadius:0, tension:0.4, fill:false },
        { label:'Media-σ', data:lower, borderColor:a+'44', borderDash:[3,2], pointRadius:0, tension:0.4, fill:'-1', backgroundColor:a+'08' }
      ]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{font:{size:9},boxWidth:10}} },
      scales:{ x:{grid:{display:false}, ticks:{font:{size:9}, callback:v=>v+'h'}},
               y:{grid:{color:gridColor()}, ticks:{font:{size:9}, callback:v=>v+' m/s'}} } }
  });
}

function renderClimatology(c) {
  if (!c) return;
  destroyChart('chart-climatology');
  const ctx = document.getElementById('chart-climatology')?.getContext('2d');
  if (!ctx) return;
  const a = acc(), a2 = acc2();
  WP.charts['chart-climatology'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: c.labels,
      datasets: [
        { type:'bar',  label:'Media m/s',  data:c.mean, backgroundColor:a+'55', borderColor:a, borderWidth:1.5, borderRadius:3 },
        { type:'line', label:'Media+σ', data:c.mean_plus_std,  borderColor:a2+'88', borderWidth:1.5, pointRadius:0, tension:0.3, fill:false },
        { type:'line', label:'Media-σ', data:c.mean_minus_std, borderColor:a2+'88', borderWidth:1.5, pointRadius:0, tension:0.3, fill:false },
      ]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{font:{size:9},boxWidth:8}} },
      scales:{ x:{grid:{display:false}, ticks:{font:{size:9}}},
               y:{grid:{color:gridColor()}, min:0, ticks:{font:{size:9}, callback:v=>v+' m/s'}} } }
  });
}

function renderExtremes(ex) {
  if (!ex || !ex.series?.length) return;
  destroyChart('chart-extremes');
  const ctx = document.getElementById('chart-extremes')?.getContext('2d');
  if (!ctx) return;
  const total15 = ex.series.find(s => s.threshold === 15)?.total ?? '—';
  document.getElementById('badge-extremes').textContent = `${total15} h ≥15 m/s`;
  // use a2 for primary, with fall-back colors
  const colors = [acc2(), acc3(), gold()];
  WP.charts['chart-extremes'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ex.labels,
      datasets: ex.series.map((s, i) => ({
        label: s.label, data: s.counts,
        backgroundColor: (colors[i]||acc2()) + 'bb', borderRadius: 3,
      }))
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:true, labels:{font:{size:9},boxWidth:8}} },
      scales:{ x:{grid:{display:false}, ticks:{font:{size:9}}},
               y:{grid:{color:gridColor()}, ticks:{font:{size:9}}} } }
  });
}

function renderWbHist(hist) {
  if (!hist) return;
  destroyChart('chart-wb-hist');
  const ctx = document.getElementById('chart-wb-hist')?.getContext('2d');
  if (!ctx) return;
  const { labels, datasets } = _buildWeibullHistDatasets(hist);
  WP.charts['chart-wb-hist'] = new Chart(ctx, {
    type: 'bar',
    data:{ labels, datasets },
    options: _weibullHistOptions(9),
  });
}

function renderHistColored(hist) {
  if (!hist) return;
  destroyChart('chart-hist-colored');
  const ctx = document.getElementById('chart-hist-colored')?.getContext('2d');
  if (!ctx) return;
  const a=acc(), a2=acc2(), m=mut();
  WP.charts['chart-hist-colored'] = new Chart(ctx, {
    type:'bar',
    data:{
      labels:(hist.centers||[]).map(v=>v.toFixed(1)),
      datasets:[{ label:'Frecuencia', data:hist.frequency,
        backgroundColor:(hist.centers||[]).map(v=>v>=15?a2+'cc':v>=7?a+'cc':m+'66'),
        borderRadius:2 }]
    },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false} },
      scales:{ x:{grid:{display:false}, ticks:{maxTicksLimit:8,font:{size:9}}},
               y:{grid:{color:gridColor()}, ticks:{font:{size:9}}} } }
  });
}

function renderMonthlyWS(c) {
  if (!c) return;
  destroyChart('chart-monthly-ws');
  const ctx = document.getElementById('chart-monthly-ws')?.getContext('2d');
  if (!ctx) return;
  WP.charts['chart-monthly-ws'] = new Chart(ctx, {
    type:'bar',
    data:{
      labels:c.labels,
      datasets:[
        { label:'Media m/s', data:c.mean, backgroundColor:acc3()+'99', borderRadius:3 },
        { label:'Máx m/s', data:c.max||c.mean_plus_std, type:'line',
          borderColor:acc2(), pointRadius:3, tension:0.3, borderWidth:1.5, backgroundColor:'transparent' }
      ]
    },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{font:{size:9},boxWidth:8}} },
      scales:{ x:{grid:{display:false}, ticks:{font:{size:9}}},
               y:{grid:{color:gridColor()}, ticks:{font:{size:9}, callback:v=>v+' m/s'}} } }
  });
}

// ─── TECNO-ECONÓMICO MODULE ───────────────────────────────────────────────────
function bindTechno() {
  document.getElementById('btn-calc-techno').addEventListener('click', calcTechnoEconomic);
  // populate turbine select from API
  loadTurbineList().then(() => {
    const sel = document.getElementById('turbine-real-group');
    if (!sel) return;
    sel.querySelectorAll('option').forEach(opt => {
      const clone = opt.cloneNode(true);
      document.getElementById('te-turbine').appendChild(clone);
    });
  });
}

async function calcTechnoEconomic() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  setStatus('Calculando análisis tecno-económico…');
  try {
    const turbineKey = document.getElementById('te-turbine').value;
    const nTurbines  = parseInt(document.getElementById('te-nturbines').value) || 10;
    const body = {
      session_id:    WP.sessionId,
      n_turbines:    nTurbines,
      ws_height_m:   parseFloat(document.getElementById('ws-height')?.value || 50),
      turbine: { builtin_model: turbineKey },
      financial: {
        demand_mwh:           parseFloat(document.getElementById('te-demand').value) || 0,
        energy_price_usd_kwh: parseFloat(document.getElementById('te-price').value),
        discount_rate_pct:    parseFloat(document.getElementById('te-rate').value),
        project_years:        parseInt(document.getElementById('te-years').value),
        cost_per_kw:          parseFloat(document.getElementById('te-costkw').value),
        usd_to_mxn:           parseFloat(document.getElementById('te-fx').value) || 1,
      }
    };
    const r = await api('/api/energy/techno_economic', 'POST', body);
    WP.technoResult = r;
    renderTechnoKPIs(r);
    renderCapexChart(r.analysis?.capex);
    renderCashflowChart(r.analysis?.cashflow_table);
    renderCpChart(r.cp);
    renderCashflowTable(r.analysis?.cashflow_table);
    if (r.n_turbines_needed > 0 && r.n_turbines_needed !== nTurbines) {
      toast(`Demanda requiere ${r.n_turbines_needed} aerogeneradores`, 'info');
    }
    toast('Análisis completado', 'success');
    setStatus('Análisis tecno-económico completado.');
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    setStatus('Error en análisis tecno-económico.');
  }
}

function renderTechnoKPIs(r) {
  const a = r.analysis;
  if (!a) return;
  const isViable = a.is_viable;
  const badge = document.getElementById('te-viable-badge');
  badge.style.display = '';
  badge.textContent   = isViable ? '✓ VAN POSITIVO' : '✗ VAN NEGATIVO';
  badge.className     = `badge ${isViable ? 'badge--success' : 'badge--error'}`;

  const fmt = n => n != null ? Math.abs(n).toLocaleString('es-MX', {maximumFractionDigits: 0}) : '—';
  const items = [
    ['VAN / NPV', (a.npv_usd >= 0 ? '+' : '-') + ' $' + fmt(a.npv_usd), 'USD'],
    ['LCOE', '$' + a.lcoe_usd_kwh, 'USD/kWh'],
    ['VAE', '$' + fmt(a.vae_usd), 'USD/año'],
    ['Retorno simple', a.payback_years ? a.payback_years + ' años' : '> proyecto', ''],
    ['Factor de capacidad', a.capacity_factor_pct + '%', ''],
    ['Energía anual', fmt(a.annual_energy_mwh) + ' MWh', '/parque'],
    ['Ingresos anuales', '$' + fmt(a.annual_revenue_usd), 'USD/año'],
    ['OPEX anual', '$' + fmt(a.annual_opex_usd), 'USD/año'],
    ['CAPEX total', '$' + fmt(a.capex?.total_capex_usd), 'USD'],
    ['Área requerida', fmt(a.capex?.area_ha) + ' ha', ''],
    ['Potencia instalada', a.capex?.installed_mw + ' MW', ''],
    ['N° aerogeneradores', a.capex?.n_turbines, ''],
  ];

  document.getElementById('te-kpi-grid').innerHTML =
    `<div class="te-kpi-grid">${items.map(([l,v,u]) =>
      `<div class="te-kpi-item">
        <div class="kpi-label">${l}</div>
        <div class="kpi-value">${v}<span style="font-size:.7rem;color:var(--text-muted);margin-left:4px">${u}</span></div>
      </div>`).join('')}</div>`;
}

function renderCapexChart(capex) {
  if (!capex) return;
  destroyChart('chart-capex');
  const ctx = document.getElementById('chart-capex')?.getContext('2d');
  if (!ctx) return;
  const labels = ['Aerogeneradores', 'Cimentación', 'Cableado', 'Caminos/Área', 'Subestación'];
  const values = [capex.turbines_total_usd, capex.foundation_usd,
                  capex.cabling_usd, capex.roads_usd, capex.substation_usd];
  const colors = [acc(), acc3(), acc2(), '#7f9b87', gold()];
  WP.charts['chart-capex'] = new Chart(ctx, {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: colors.map(c => c+'cc'), borderColor: colors, borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { font: { size: 10 }, padding: 8 } },
        tooltip: { callbacks: { label: ctx => ` $${ctx.parsed.toLocaleString('es-MX')} USD` } } } }
  });
}

function renderCashflowChart(table) {
  if (!table?.length) return;
  destroyChart('chart-cashflow');
  const ctx = document.getElementById('chart-cashflow')?.getContext('2d');
  if (!ctx) return;
  WP.charts['chart-cashflow'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: table.map(r => 'Año ' + r.year),
      datasets: [
        { type: 'bar', label: 'Flujo VP', data: table.map(r => r.pv_cashflow),
          backgroundColor: acc() + '55', borderColor: acc(), borderWidth: 1, borderRadius: 3 },
        { type: 'line', label: 'Acumulado', data: table.map(r => r.cumulative),
          borderColor: t => t.raw >= 0 ? '#7f9b87' : '#d17a7a',
          segment: { borderColor: ctx => ctx.p1.parsed.y >= 0 ? '#7f9b87' : '#d17a7a' },
          borderWidth: 2.5, pointRadius: 0, tension: 0.3, fill: false, yAxisID: 'y1' }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, labels: { font: { size: 10 } } } },
      scales: {
        x: { grid: { color: gridColor() } },
        y:  { grid: { color: gridColor() }, title: { display: true, text: 'USD/año (VP)' } },
        y1: { position: 'right', grid: { display: false }, title: { display: true, text: 'Acumulado USD' } }
      } }
  });
}

function renderCpChart(cp) {
  if (!cp?.cp_curve?.length) return;
  destroyChart('chart-cp');
  const ctx = document.getElementById('chart-cp')?.getContext('2d');
  if (!ctx) return;
  document.getElementById('badge-cp-max').textContent =
    `Cp_max=${cp.cp_max}  Betz ${cp.cp_betz_utilization_pct}%`;
  WP.charts['chart-cp'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: cp.cp_curve.map(p => p.ws.toFixed(1)),
      datasets: [{
        label: 'Cp', data: cp.cp_curve.map(p => p.cp),
        borderColor: '#7c3aed', backgroundColor: '#7c3aed22',
        borderWidth: 2, pointRadius: 3, tension: 0.3, fill: true,
      }, {
        label: 'Límite Betz (0.593)', data: cp.cp_curve.map(() => 0.593),
        borderColor: '#ef444466', borderWidth: 1.5, pointRadius: 0, borderDash: [6, 3], fill: false,
      }]
    },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, labels: { font: { size: 10 } } } },
      scales: { x: { grid: { color: gridColor() }, title: { display: true, text: 'm/s' } },
                y: { grid: { color: gridColor() }, min: 0, max: 0.65, title: { display: true, text: 'Cp' } } } }
  });
}

// ─── EXPORT CENTER ───────────────────────────────────────────────────────────
function bindExport() {
  // Update info box whenever the export tab becomes visible
  document.querySelectorAll('.nav-btn').forEach(btn => {
    if (btn.dataset.module === 'export') {
      btn.addEventListener('click', () => {
        const info = document.getElementById('export-session-info');
        if (!info) return;
        if (WP.sessionId) {
          info.className = 'info-box green';
          info.textContent = `✓ Sesión activa: ${WP.sessionId} — selecciona un archivo para descargar.`;
        } else {
          info.className = 'info-box amber';
          info.textContent = 'Carga un archivo de datos y ejecuta los módulos para habilitar las exportaciones.';
        }
      });
    }
  });
  const txtBtn = document.getElementById('btn-export-table-txt');
  const csvBtn = document.getElementById('btn-export-table-csv');
  const imgBtn = document.getElementById('btn-export-table-img');
  if (txtBtn) txtBtn.addEventListener('click', () => exportSelectedTable('txt'));
  if (csvBtn) csvBtn.addEventListener('click', () => exportSelectedTable('csv'));
  if (imgBtn) imgBtn.addEventListener('click', () => exportSelectedTable('png'));
}

async function exportData(type) {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }

  // Update info box
  const info = document.getElementById('export-session-info');
  if (info) { info.className = 'info-box green'; info.textContent = 'Preparando descarga…'; }

  try {
    let csvText = '';
    let filename = 'aero_export.csv';

    if (type === 'raw') {
      const r = await api(`/api/export/csv?session_id=${WP.sessionId}`, 'GET');
      csvText = r.csv || objectToCsv(r);
      filename = 'wind_series.csv';
    } else if (type === 'energy') {
      const r = await api(`/api/export/csv?session_id=${WP.sessionId}&type=energy`, 'GET');
      csvText = r.csv || buildEnergyCsv(WP.energyResult);
      filename = 'monthly_energy.csv';
    } else if (type === 'metrics') {
      csvText = buildMetricsCsv(WP.compareResult);
      filename = 'model_metrics.csv';
    } else if (type === 'forecast') {
      csvText = buildForecastCsv(WP.forecastResult);
      filename = 'forecast_backtest.csv';
    } else if (type === 'climatology') {
      const r = WP.windStats || await api('/api/wind/stats', 'POST', { session_id: WP.sessionId });
      WP.windStats = r;
      csvText = buildClimatologyCsv(r.climatology);
      filename = 'climatology.csv';
    } else if (type === 'techno') {
      csvText = buildTechnoCsv(WP.technoResult);
      filename = 'techno_economic.csv';
    } else if (type === 'wind_stats') {
      const r = WP.windStats || await api('/api/wind/stats', 'POST', { session_id: WP.sessionId });
      WP.windStats = r;
      csvText = buildWindStatsCsv(r);
      filename = 'wind_statistics.csv';
    }

    if (!csvText) { toast('No hay datos disponibles para este export', 'warn'); return; }
    downloadText(csvText, filename);
    if (info) { info.className = 'info-box green'; info.textContent = `✓ ${filename} descargado correctamente`; }
    toast(`Descargado: ${filename}`, 'success');
  } catch(e) {
    if (info) { info.className = 'info-box error'; info.textContent = 'Error: ' + e.message; }
    toast('Error al exportar: ' + e.message, 'error');
  }
}

async function exportReport() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  const info = document.getElementById('export-session-info');
  try {
    const ws  = WP.windStats;
    const te  = WP.technoResult;
    const ov  = ws?.overview;
    const now = new Date().toISOString().slice(0,10);
    const lines = [
      '══════════════════════════════════════════════════════',
      ' AERO by OPEN CIM — Reporte Técnico',
      `══════════════════════════════════════════════════════`,
      `Generado: ${now}  |  Sesión: ${WP.sessionId}`,
      '',
      '── RESUMEN DEL SITIO ──────────────────────────────────',
      ov ? `Velocidad media:      ${ov.mean} m/s  (σ=${ov.std})` : 'Ejecutar módulo Resumen',
      ov ? `Velocidad máxima:     ${ov.max} m/s  (p90=${ov.p90})` : '',
      ov ? `Weibull k/λ:          ${ov.weibull?.k} / ${ov.weibull?.lambda}` : '',
      ov ? `Densidad de potencia: ${ov.power_density_w_m2} W/m²` : '',
      ov ? `Horas ≥15 m/s:        ${ov.h_above_15ms} h  (${ov.pct_above_15ms}%)` : '',
      ov ? `Registros:            ${ov.n_obs}  [${ov.date_start} → ${ov.date_end}]` : '',
      '',
      '── ANÁLISIS TECNO-ECONÓMICO ───────────────────────────',
      te ? `VAN/NPV:       $ ${te.analysis?.npv_usd?.toLocaleString?.() ?? '—'} USD` : 'Ejecutar módulo Tecno-Económico',
      te ? `LCOE:          $ ${te.analysis?.lcoe_usd_kwh} USD/kWh` : '',
      te ? `CF:            ${te.analysis?.capacity_factor_pct} %` : '',
      te ? `CAPEX total:   $ ${te.analysis?.capex?.total_capex_usd?.toLocaleString?.() ?? '—'} USD` : '',
      te ? `Payback:       ${te.analysis?.payback_years} años` : '',
      te ? `Viable:        ${te.analysis?.is_viable ? 'SÍ (VAN>0)' : 'NO (VAN<0)'}` : '',
      '',
      '── ROSA DE VIENTOS ────────────────────────────────────',
      ws?.rose ? `Dirección dominante:  ${ws.rose.dominant_direction}  (${ws.rose.dominant_pct}% del tiempo)` : 'Ejecutar módulo Viento',
      '',
      '── METODOLOGÍA ────────────────────────────────────────',
      'Datos: MERRA-2 / ERA5 (Windnavigator) — horario, 50m',
      'Modelos ML: Ridge, RF, GBM, MLP, SVR, ARIMA — validación cronológica',
      'Energía: Curva de potencia real + corrección densidad de aire',
      'Tecno-econ.: Metodología PhD MATLAB — CAPEX, OPEX, VAN, LCOE, VAE',
      'Weibull: ajuste por método de momentos (scipy.stats.weibull_min)',
      '',
      '── LIMITACIONES ───────────────────────────────────────',
      '· Datos a 50m; hub height típico 80–120m — aplicar extrapolación logarítmica',
      '· CAPEX estimado; solicitar presupuesto detallado a OEM',
      '· VAN sensible a tasa de descuento y precio de energía asumidos',
      '',
      '══════════════════════════════════════════════════════',
      ' AERO by OPEN CIM · v2.0 · Flask + scikit-learn + LightGBM',
      '══════════════════════════════════════════════════════',
    ];
    downloadText(lines.join('\n'), 'aero_report.txt');
    if (info) { info.className = 'info-box green'; info.textContent = '✓ aero_report.txt descargado'; }
    toast('Reporte descargado', 'success');
  } catch(e) {
    toast('Error al generar reporte: ' + e.message, 'error');
  }
}

/* ── CSV builders ── */
function downloadText(text, filename) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function downloadBlob(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function tableToRows(table) {
  const rows = [];
  table.querySelectorAll('tr').forEach(tr => {
    const cells = [...tr.querySelectorAll('th,td')].map(td => td.textContent.trim().replace(/\s+/g, ' '));
    if (cells.length) rows.push(cells);
  });
  return rows;
}

function rowsToCsv(rows) {
  return rows
    .map(row => row.map(v => `"${String(v || '').replace(/"/g, '""')}"`).join(','))
    .join('\n');
}

function rowsToText(rows) {
  return rows.map(r => r.join(' | ')).join('\n');
}

function exportSelectedTable(format) {
  const sel = document.getElementById('export-table-select');
  const id = sel?.value;
  const table = id ? document.getElementById(id) : null;
  if (!table) {
    toast('Selecciona una tabla válida', 'warn');
    return;
  }
  const rows = tableToRows(table);
  if (!rows.length) {
    toast('La tabla está vacía', 'warn');
    return;
  }
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  if (format === 'txt') {
    downloadText(rowsToText(rows), `${id}-${stamp}.txt`);
    toast('Tabla exportada en texto', 'success');
    return;
  }
  if (format === 'csv') {
    const csv = rowsToCsv(rows);
    downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8' }), `${id}-${stamp}.csv`);
    toast('Tabla exportada en CSV', 'success');
    return;
  }
  if (format === 'png') {
    exportTableAsImage(rows, `${id}-${stamp}.png`);
  }
}

function exportTableAsImage(rows, filename) {
  const cellPadX = 12;
  const cellPadY = 8;
  const rowHeight = 28;
  const font = '12px Inter, system-ui, sans-serif';
  const headBg = '#1e293b';
  const bodyBg = '#0f172a';
  const headColor = '#f8fafc';
  const bodyColor = '#e2e8f0';

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  ctx.font = font;

  const nCols = Math.max(...rows.map(r => r.length));
  const widths = Array.from({ length: nCols }, (_, c) => {
    const max = Math.max(...rows.map(r => ctx.measureText(r[c] || '').width));
    return Math.ceil(max + cellPadX * 2);
  });
  const width = widths.reduce((a, b) => a + b, 0);
  const height = rows.length * rowHeight;
  canvas.width = Math.max(360, width);
  canvas.height = Math.max(80, height);

  rows.forEach((row, rIdx) => {
    let x = 0;
    const y = rIdx * rowHeight;
    const isHead = rIdx === 0;
    for (let c = 0; c < nCols; c += 1) {
      const w = widths[c];
      ctx.fillStyle = isHead ? headBg : bodyBg;
      ctx.fillRect(x, y, w, rowHeight);
      ctx.strokeStyle = '#334155';
      ctx.strokeRect(x, y, w, rowHeight);
      ctx.fillStyle = isHead ? headColor : bodyColor;
      ctx.font = isHead ? '600 12px Inter, system-ui, sans-serif' : font;
      ctx.fillText(row[c] || '', x + cellPadX, y + cellPadY + 11);
      x += w;
    }
  });

  canvas.toBlob(blob => {
    if (!blob) {
      toast('No se pudo generar imagen de la tabla', 'error');
      return;
    }
    downloadBlob(blob, filename);
    toast('Tabla exportada como imagen', 'success');
  }, 'image/png');
}

function buildClimatologyCsv(c) {
  if (!c) return '';
  const rows = ['mes,media_ms,std_ms,media_plus_std,media_minus_std'];
  c.labels.forEach((l, i) => rows.push(`${l},${c.mean[i]},${c.std?.[i]??''},${c.mean_plus_std[i]},${c.mean_minus_std[i]}`));
  return rows.join('\n');
}

function buildWindStatsCsv(r) {
  if (!r) return '';
  const rows = ['# AERO by OPEN CIM — Wind Statistics Export'];
  if (r.rose) {
    rows.push('\n# Rosa de vientos');
    rows.push('sector,frecuencia_pct,vel_media_ms');
    r.rose.labels.forEach((l, i) => rows.push(`${l},${r.rose.frequency_pct[i]},${r.rose.mean_speed?.[i]??''}`))
  }
  if (r.diurnal) {
    rows.push('\n# Perfil diurno');
    rows.push('hora,media_ms,std_ms');
    r.diurnal.hours.forEach((h, i) => rows.push(`${h},${r.diurnal.mean[i]},${r.diurnal.std?.[i]??''}`))
  }
  return rows.join('\n');
}

function buildEnergyCsv(r) {
  if (!r?.monthly) return '';
  const rows = ['mes,energia_mwh,fc_pct'];
  const m = r.monthly;
  if (m.labels) m.labels.forEach((l, i) => rows.push(`${l},${m.energy_mwh?.[i]??''},${m.capacity_factor_pct?.[i]??''}`));
  return rows.join('\n');
}

function buildMetricsCsv(r) {
  if (!r?.results?.length) return '';
  const rows = ['modelo,rmse,mae,r2,smape,nrmse,skill'];
  r.results.forEach(t => rows.push(`${t.name},${t.rmse??''},${t.mae??''},${t.r2??''},${t.smape??''},${t.nrmse??''},${t.skill??''}`));
  return rows.join('\n');
}

function buildForecastCsv(r) {
  if (!r) return '';
  const rows = ['timestamp,observado,predicho,persistencia'];
  const ts = r.timestamps || r.dates || [];
  const ob = r.observed || r.actual || [];
  const pr = r.predicted || [];
  const pe = r.persistence || [];
  ts.forEach((t, i) => rows.push(`${t},${ob[i]??''},${pr[i]??''},${pe[i]??''}`));
  return rows.join('\n');
}

function buildTechnoCsv(r) {
  if (!r?.analysis) return '';
  const a = r.analysis;
  const cap = a.capex || {};
  const rows = [
    'parametro,valor,unidad',
    `npv,${a.npv_usd},USD`,
    `lcoe,${a.lcoe_usd_kwh},USD/kWh`,
    `vae,${a.vae_usd},USD/año`,
    `payback,${a.payback_years},años`,
    `cf,${a.capacity_factor_pct},%`,
    `energia_anual,${a.annual_energy_mwh},MWh`,
    `ingresos_anuales,${a.annual_revenue_usd},USD/año`,
    `opex_anual,${a.annual_opex_usd},USD/año`,
    `capex_total,${cap.total_capex_usd},USD`,
    `capex_turbinas,${cap.turbines_total_usd},USD`,
    `capex_cimentacion,${cap.foundation_usd},USD`,
    `capex_cableado,${cap.cabling_usd},USD`,
    `capex_subestacion,${cap.substation_usd},USD`,
    `area_ha,${cap.area_ha},ha`,
    `potencia_instalada,${cap.installed_mw},MW`,
    `n_turbinas,${cap.n_turbines},unidades`,
  ];
  if (r.analysis?.cashflow_table?.length) {
    rows.push('\naño,flujo_usd,vp_flujo_usd,acumulado_usd');
    r.analysis.cashflow_table.forEach(row => rows.push(`${row.year},${row.cashflow},${row.pv_cashflow},${row.cumulative}`));
  }
  return rows.join('\n');
}

function renderCashflowTable(table) {
  if (!table?.length) return;
  const tbody = document.querySelector('#te-cashflow-table tbody');
  tbody.innerHTML = table.map(r => {
    const cls = r.cumulative >= 0 ? 'positive' : 'negative';
    return `<tr class="${cls}">
      <td>${r.year}</td>
      <td>$${r.cashflow.toLocaleString('es-MX')}</td>
      <td>$${r.pv_cashflow.toLocaleString('es-MX')}</td>
      <td>$${r.cumulative.toLocaleString('es-MX')}</td>
    </tr>`;
  }).join('');
}

// ─── RECURSO AVANZADO (Windographer-style) ────────────────────────────────────

function bindAdvanced() {
  document.getElementById('btn-run-advanced').addEventListener('click', runAdvanced);
}

async function runAdvanced() {
  const btn = document.getElementById('btn-run-advanced');
  btn.disabled = true; btn.textContent = 'Calculando…';
  setStatus('Calculando análisis avanzado de recurso…');
  try {
    const wsH        = parseFloat(document.getElementById('adv-ws-height')?.value)   || 50;
    const zHub       = parseFloat(document.getElementById('adv-z-hub')?.value)       || null;
    const orogFactor = parseFloat(document.getElementById('adv-orog-factor')?.value ?? '0.15') || 0.15;
    const zScale     = parseFloat(document.getElementById('adv-z-scale')?.value)     || 80;
    const r = await api('/api/wind/advanced', 'POST', {
      session_id: WP.sessionId, ws_height: wsH, z_hub: zHub,
      orog_factor: orogFactor, z_scale: zScale,
    });
    WP.advancedResult = r;

    // KPI row
    const kpiRow = document.getElementById('adv-kpi-row');
    kpiRow.style.display = '';
    const ti = r.wind_speed_variability || r.turbulence;
    const sh = r.shear;
    const rp = r.return_periods;

    const _set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
    _set('adv-kpi-ti',    ti?.ti_overall != null ? (ti.ti_overall * 100).toFixed(1) + '%' : '—');
    _set('adv-kpi-ti15',  ti?.ti_15ms    != null ? (ti.ti_15ms    * 100).toFixed(1) + '%' : '—');
    // KPI clase IEC con asterisco si es proxy
    const iecCls   = ti?.iec_class || null;
    const iecLabel = iecCls ? `Clase ${iecCls}${ti?.iec_is_proxy ? '*' : ''}` : '—';
    _set('adv-kpi-iec',   iecLabel);
    _set('adv-kpi-alpha', sh?.alpha != null ? sh.alpha.toFixed(3) : '—');
    _set('adv-kpi-v50',   rp?.v_50year != null ? rp.v_50year + ' m/s' : '—');

    // Badges
    const badgeIec = document.getElementById('adv-badge-iec');
    if (badgeIec) {
      badgeIec.textContent = iecCls
        ? `IEC ${iecCls}${ti?.iec_is_proxy ? '* (supuesto proxy)' : ''}`
        : 'TI proxy horario';
      badgeIec.className = `cbadge ${iecCls === 'A' || iecCls === 'S' ? 'r' : iecCls === 'B' ? '' : 'g'}`;
      badgeIec.title     = ti?.note || '';
    }
    _set('adv-badge-alpha',  `α = ${sh?.alpha?.toFixed(3) || '—'}`);
    _set('adv-badge-z0',     `z₀ = ${sh?.z0 ?? '—'} m`);
    _set('adv-badge-method', rp?.method || 'Gumbel');
    if (r.sectors?.dominant_sectors) {
      const bd = document.getElementById('wind-badge-dom');
      if (bd) bd.textContent = `Dom: ${r.sectors.dominant_sectors.join(', ')}`;
    }

    renderTIChart(ti);
    renderShearChart(sh);
    renderHubKPIs(sh?.hub);
    populateSectorTable(r.sectors);
    renderReturnChart(rp);
    populateReturnTable(rp);

    toast('Análisis avanzado completo', 'success');
    setStatus('Recurso avanzado calculado.');
  } catch(e) {
    toast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Calcular análisis avanzado';
  }
}

function renderTIChart(ti) {
  if (!ti?.bins?.length) return;
  destroyChart('ti');
  const bins = ti.bins.filter(b => b.ti_mean != null);
  const ctx  = document.getElementById('chart-ti').getContext('2d');
  const refs  = { A: 0.16, B: 0.14, C: 0.12 };
  WP.charts.ti = new Chart(ctx, {
    type: 'line',
    data: {
      labels: bins.map(b => b.ws_center),
      datasets: [
        { label: 'TI medio', data: bins.map(b => b.ti_mean ? +(b.ti_mean * 100).toFixed(2) : null),
          borderColor: acc(), backgroundColor: acc() + '25', fill: false, tension: 0.35, pointRadius: 3 },
        { label: 'TI P90', data: bins.map(b => b.ti_p90 ? +(b.ti_p90 * 100).toFixed(2) : null),
          borderColor: acc2(), borderDash: [4,4], fill: false, pointRadius: 2 },
        { label: 'IEC A (16%)', data: bins.map(() => refs.A * 100),
          borderColor: '#e55', borderDash: [6,3], fill: false, pointRadius: 0 },
        { label: 'IEC B (14%)', data: bins.map(() => refs.B * 100),
          borderColor: gold(), borderDash: [6,3], fill: false, pointRadius: 0 },
        { label: 'IEC C (12%)', data: bins.map(() => refs.C * 100),
          borderColor: acc() + '80', borderDash: [6,3], fill: false, pointRadius: 0 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: ink(), font: { size: 10 } } } },
      scales: {
        x: { title: { display: true, text: 'Velocidad (m/s)', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
        y: { title: { display: true, text: 'TI (%)', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
      },
    },
  });
}

function renderShearChart(sh) {
  if (!sh) return;
  destroyChart('shear');
  const ctx = document.getElementById('chart-shear')?.getContext('2d');
  if (!ctx) return;

  const lc = isLight() ? '#64748b' : '#94a3b8';
  const gc = gridColor();
  const H  = sh.heights;

  // helper: convert parallel speed/height arrays → scatter {x,y} points
  const pts = (speeds) => speeds.map((s, i) => ({ x: s, y: H[i] }));

  // helper: build a dataset
  const ds = (label, speeds, color, dash, width, radius = 0, extra = {}) => ({
    label, showLine: true,
    data: pts(speeds),
    borderColor: color, borderWidth: width,
    borderDash: dash || [],
    pointRadius: radius,
    fill: false, tension: 0.35,
    ...extra,
  });

  const dimRef = lc + (isLight() ? '88' : '66');

  const datasets = [
    // ── Referencia α (finas, fondo) ──────────────────────────────
    ds('α = 0.10  liso / offshore', sh.profile_smooth, dimRef, [2, 5], 1.2),
    ds('α = 0.143  IEC abierto',    sh.profile_iec,    dimRef, [4, 4], 1.4),
    ds('α = 0.25  rugoso / forestal', sh.profile_rough, dimRef, [2, 5], 1.2),
    // ── Perfiles ajustados al sitio (gruesos) ───────────────────
    ds(`Hellmann  α = ${sh.alpha}  (sitio)`, sh.profile_hellmann,
       acc(),  [],     2.6, 3),
    ds(`Log  z₀ = ${sh.z0} m`,              sh.profile_log,
       acc2(), [6, 3], 2.6, 2),
    // ── Efecto orográfico ────────────────────────────────────────
    ...(sh.profile_orog ? [ds(
      `Orografía  +${(sh.orog_factor * 100).toFixed(0)}%  e^(-z/${sh.z_scale}m)`,
      sh.profile_orog, '#48C2A3', [7, 2], 2.2, 0,
    )] : []),
    // ── Datos multi-altura medidos ───────────────────────────────
    ...(Object.keys(sh.multi_height || {}).length > 1 ? [{
      label: 'Medido (multi-altura)',
      showLine: false,
      data: Object.entries(sh.multi_height).map(([h, v]) => ({ x: v, y: +h })),
      borderColor: gold(), backgroundColor: gold(),
      pointRadius: 7, pointStyle: 'crossRot', pointBorderWidth: 2,
    }] : []),
    // ── Punto de referencia ──────────────────────────────────────
    {
      label: `Ref: ${sh.ref_speed} m/s @ ${sh.ref_height} m`,
      showLine: false,
      data: [{ x: sh.ref_speed, y: sh.ref_height }],
      borderColor: acc(), backgroundColor: acc() + 'cc',
      pointRadius: 6, pointStyle: 'circle',
    },
  ];

  // Hub-height reference lines via annotation plugin
  const annotations = {};
  [80, 100, 120, 140].forEach(h => {
    annotations[`hub${h}`] = {
      type: 'line', yMin: h, yMax: h,
      borderColor: lc + '40', borderWidth: 1, borderDash: [4, 5],
      label: {
        content: `${h} m`, display: true, position: 'end',
        color: lc, font: { size: 8 }, backgroundColor: 'transparent', padding: { x: 3, y: 1 },
      },
    };
  });

  WP.charts.shear = new Chart(ctx, {
    type: 'scatter',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: ink(), font: { size: 9 }, boxWidth: 26, boxHeight: 8, padding: 6 },
        },
        annotation: { annotations },
      },
      scales: {
        x: {
          type: 'linear',
          title: { display: true, text: 'Velocidad del viento (m/s)', font: { size: 9 }, color: lc },
          ticks: { color: lc, font: { size: 9 } }, grid: { color: gc },
        },
        y: {
          type: 'linear', min: 0, max: 220,
          title: { display: true, text: 'Altura (m)', font: { size: 9 }, color: lc },
          ticks: { color: lc, font: { size: 9 }, stepSize: 20 }, grid: { color: gc },
        },
      },
    },
  });
}

function populateSectorTable(sec) {
  if (!sec?.sectors) return;
  const tbody = document.getElementById('sector-tbl-body');
  const sorted = [...sec.sectors].sort((a, b) => b.energy_pct - a.energy_pct);
  tbody.innerHTML = sorted.map((s, i) => `<tr class="${i === 0 ? 'best-row' : ''}">
    <td>${s.sector}</td>
    <td><strong>${s.label}</strong> (${s.center_deg}°)</td>
    <td>${s.freq_pct?.toFixed(1) ?? '—'}%</td>
    <td>${s.mean_ws?.toFixed(2) ?? '—'}</td>
    <td>${s.power_density_wm2?.toFixed(0) ?? '—'}</td>
    <td><strong>${s.energy_pct?.toFixed(1) ?? '—'}%</strong></td>
    <td>${s.weibull_k ?? '—'}</td>
    <td>${s.weibull_lambda ?? '—'}</td>
  </tr>`).join('');
}

function renderHubKPIs(hub) {
  const row = document.getElementById('adv-hub-row');
  if (!row) return;
  if (!hub) { row.style.display = 'none'; return; }

  row.style.display = '';
  const _s = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };

  _s('adv-hub-v-hell',     `${hub.v_hub_hellmann.toFixed(2)} m/s`);
  _s('adv-hub-v-hell-sub', `z_hub = ${hub.z_hub} m`);
  _s('adv-hub-v-log',      `${hub.v_hub_log.toFixed(2)} m/s`);
  _s('adv-hub-v-log-sub',  `z₀ ajustada`);
  _s('adv-hub-pd-hell',    `${hub.pd_hub_hellmann.toFixed(0)} W/m²`);
  _s('adv-hub-pd-log',     `${hub.pd_hub_log.toFixed(0)} W/m²`);

  const gainHell = hub.gain_pct_hell;
  const gainLog  = hub.gain_pct_log;
  _s('adv-hub-gain-hell', `${gainHell >= 0 ? '+' : ''}${gainHell.toFixed(1)}%`);
  _s('adv-hub-gain-log',  `${gainLog  >= 0 ? '+' : ''}${gainLog.toFixed(1)}%`);
  _s('adv-hub-cutin',     `${hub.pct_above_cutin.toFixed(1)}%`);

  // Color ganancia: verde si sube, rojo si (hipotético) baja
  ['adv-hub-gain-hell', 'adv-hub-gain-log'].forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    const g = i === 0 ? gainHell : gainLog;
    el.style.color = g >= 0 ? 'var(--accent)' : '#e55';
  });
}

function renderReturnChart(rp) {
  if (!rp?.v_gumbel) return;
  destroyChart('return');
  const ctx = document.getElementById('chart-return').getContext('2d');
  WP.charts.return = new Chart(ctx, {
    type: 'line',
    data: {
      labels: rp.return_periods,
      datasets: [
        { label: 'Gumbel', data: rp.v_gumbel, borderColor: acc(), fill: false,
          tension: 0.3, pointRadius: 4 },
        ...(rp.v_gev ? [{ label: 'GEV', data: rp.v_gev, borderColor: acc2(),
          borderDash: [5,4], fill: false, tension: 0.3, pointRadius: 3 }] : []),
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: ink(), font: { size: 10 } } } },
      scales: {
        x: { title: { display: true, text: 'Período de retorno (años)', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
        y: { title: { display: true, text: 'V (m/s)', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
      },
    },
  });
}

function populateReturnTable(rp) {
  if (!rp) return;
  const tbody = document.getElementById('return-tbl-body');
  tbody.innerHTML = rp.return_periods.map((T, i) => `<tr ${T === 50 ? 'class="best-row"' : ''}>
    <td><strong>${T}</strong></td>
    <td>${rp.v_gumbel?.[i] ?? '—'} m/s</td>
    <td>${rp.v_gev?.[i] ?? '—'} m/s</td>
  </tr>`).join('');
}

// ─── SISTEMA HÍBRIDO (HOMER Pro-style) ───────────────────────────────────────

function bindHybrid() {
  document.getElementById('btn-run-hybrid').addEventListener('click', runHybrid);
}

async function runHybrid() {
  const btn = document.getElementById('btn-run-hybrid');
  btn.disabled = true; btn.textContent = 'Simulando…';
  setStatus('Simulando sistema híbrido…');
  try {
    const body = {
      session_id: WP.sessionId,
      lat: parseFloat(document.getElementById('hyb-lat').value) || 20,
      load: {
        annual_kwh:   parseFloat(document.getElementById('hyb-load-kwh').value)  || 1e6,
        peak_kw:      parseFloat(document.getElementById('hyb-load-peak').value) || 228,
        profile_type: document.getElementById('hyb-load-type').value,
      },
      wind: {
        capacity_kw:  parseFloat(document.getElementById('hyb-wind-kw').value)    || 500,
        hub_height_m: parseFloat(document.getElementById('hyb-wind-hub').value)   || 80,
        cut_in:       parseFloat(document.getElementById('hyb-wind-cutin').value) || 3,
        rated_speed:  parseFloat(document.getElementById('hyb-wind-rated').value) || 12,
      },
      solar: {
        capacity_kwp: parseFloat(document.getElementById('hyb-solar-kwp').value) || 0,
        pr:           parseFloat(document.getElementById('hyb-solar-pr').value)  || 0.80,
      },
      battery: {
        capacity_kwh: parseFloat(document.getElementById('hyb-batt-kwh').value)   || 0,
        c_rate:       parseFloat(document.getElementById('hyb-batt-crate').value) || 0.5,
      },
      diesel: {
        capacity_kw: parseFloat(document.getElementById('hyb-diesel-kw').value)     || 0,
        fuel_price:  parseFloat(document.getElementById('hyb-diesel-price').value)  || 0.90,
      },
    };

    const r = await api('/api/hybrid/simulate', 'POST', body);
    WP.hybridResult = r;

    // KPI row
    document.getElementById('hyb-kpi-row').style.display = '';
    document.getElementById('hyb-kpi-rf').textContent   = (r.renewable_fraction ?? 0).toFixed(1) + '%';
    document.getElementById('hyb-kpi-lcoe').textContent  = r.economics?.lcoe_usd_kwh?.toFixed(4) ?? '—';
    document.getElementById('hyb-kpi-npc').textContent   = r.economics?.npc ? '$' + Math.round(r.economics.npc).toLocaleString() : '—';
    document.getElementById('hyb-kpi-co2').textContent   = r.economics?.co2_saved_t_yr ?? '—';
    document.getElementById('hyb-kpi-lpsp').textContent  = (r.lpsp_pct ?? 0).toFixed(3) + '%';
    document.getElementById('hyb-kpi-fuel').textContent  = r.fuel_liters ? Math.round(r.fuel_liters).toLocaleString() : '—';

    const n = r.traces?.step ? Math.round(r.traces.load.length) : 0;
    document.getElementById('hyb-badge-pts').textContent = `${n} puntos`;

    renderHybridDispatch(r.traces);
    renderHybridEnergy(r);
    populateHybridCapex(r.economics);

    toast('Simulación híbrida completa', 'success');
    setStatus('Sistema híbrido simulado.');
  } catch(e) {
    toast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Simular sistema híbrido';
  }
}

function renderHybridDispatch(traces) {
  if (!traces) return;
  destroyChart('hybridDispatch');
  const ctx = document.getElementById('chart-hybrid-dispatch').getContext('2d');
  const labels = traces.load.map((_, i) => i);
  WP.charts.hybridDispatch = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Carga', data: traces.load, borderColor: ink(), fill: false, pointRadius: 0, borderWidth: 1.5 },
        { label: 'Renovables', data: traces.renewables, borderColor: acc(), backgroundColor: acc() + '30', fill: true, pointRadius: 0, borderWidth: 1 },
        { label: 'Batería', data: traces.battery, borderColor: acc3(), backgroundColor: acc3() + '20', fill: true, pointRadius: 0, borderWidth: 1 },
        { label: 'Diésel', data: traces.diesel, borderColor: acc2(), backgroundColor: acc2() + '30', fill: true, pointRadius: 0, borderWidth: 1 },
        { label: 'No atendida', data: traces.unmet, borderColor: '#e55', backgroundColor: '#e5530f20', fill: true, pointRadius: 0, borderWidth: 1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { labels: { color: ink(), font: { size: 10 } } } },
      scales: {
        x: { display: false },
        y: { title: { display: true, text: 'kW', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
      },
    },
  });
}

function renderHybridEnergy(r) {
  destroyChart('hybridEnergy');
  const ctx = document.getElementById('chart-hybrid-energy').getContext('2d');
  const labels = ['Viento', 'Solar', 'Diésel', 'No atendida'];
  const data   = [r.wind_kwh || 0, r.solar_kwh || 0, r.diesel_kwh || 0, r.unmet_kwh || 0];
  const colors = [acc(), gold(), acc2(), '#e55'];
  WP.charts.hybridEnergy = new Chart(ctx, {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 2,
      borderColor: document.body.classList.contains('light') ? '#FFFFFF' : '#121823' }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: ink(), font: { size: 10 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${Math.round(ctx.parsed).toLocaleString()} kWh` } },
      },
    },
  });
}

function populateHybridCapex(eco) {
  if (!eco?.capex) return;
  const body = document.getElementById('hyb-capex-body');
  const total = eco.capex.total || 1;
  const items = [
    { k: 'wind', label: 'Viento' }, { k: 'solar', label: 'Solar PV' },
    { k: 'battery', label: 'Batería' }, { k: 'diesel', label: 'Diésel' },
  ];
  body.innerHTML = items.filter(it => eco.capex[it.k] > 0).map(it => `<tr>
    <td>${it.label}</td>
    <td>$${Math.round(eco.capex[it.k]).toLocaleString()}</td>
    <td>${(eco.capex[it.k] / total * 100).toFixed(1)}%</td>
  </tr>`).join('') + `<tr class="best-row">
    <td><strong>Total CAPEX</strong></td>
    <td><strong>$${Math.round(total).toLocaleString()}</strong></td>
    <td>100%</td>
  </tr>`;

  const list = document.getElementById('hyb-econ-list');
  list.innerHTML = [
    ['O&M anual', `$${Math.round(eco.om_annual).toLocaleString()}`],
    ['NPC', `$${Math.round(eco.npc).toLocaleString()}`],
    ['LCOE', `${eco.lcoe_usd_kwh?.toFixed(4)} USD/kWh`],
    ['CO₂ evitado', `${eco.co2_saved_t_yr} t/año`],
    ['Tasa descuento', `${(eco.discount_rate * 100).toFixed(0)}%`],
    ['Vida útil', `${eco.lifetime_yr} años`],
  ].map(([k, v]) => `<div class="val-metric-item">
    <span class="val-metric-name">${k}</span>
    <span class="val-metric-val">${v}</span>
  </div>`).join('');
}

// ═══════════════════════ KRIGING — mapa único + curvas ══════════════════════
const KRIG = {
  task_id: null,
  map: null,             // single Leaflet map instance
  overlays: [],          // result overlays (image + result markers)
  siteMarkers: {},       // filename -> Leaflet marker (selection layer)
  sites: [],             // [{filename, lat, lon, ...}] from /api/kriging/sites
  selected: new Set(),   // filenames currently included in Kriging
  principal: null,       // filename of the main park (or null)
  result: null,          // last full result {wind, inversion, van, turbine_curves}
  layerKey: 'wind',      // active layer
  curveIdx: 0,           // active turbine in the curve viewer
  // ── México / encuadre / herramientas integradas desde Mapa & Kriging ──
  mode: 'mexico',
  areaBounds: null, areaLayer: null, areaDraft: null, areaStart: null, selectingArea: false,
  drawingPark: false, parkPoints: [], parkLayer: null, parkMarkers: [],
  optimal: [],
};

const KRIG_MEXICO_BOUNDS = [[14.2, -118.6], [33.2, -86.5]];

const KRIG_LAYERS = {
  wind:      { unit: 'm/s', title: 'Velocidad promedio del viento (m/s)' },
  inversion: { unit: 'USD', title: 'Inversión inicial (USD)' },
  van:       { unit: 'USD', title: 'Valor Actual Neto (USD)' },
};

// Rampa RdYlBu reversed (azul→rojo)
function krigColor(t) {
  const stops = [
    [0.00, [49, 54,149]], [0.16, [69,117,180]], [0.33, [116,173,209]],
    [0.50, [254,224,144]], [0.67, [253,174, 97]], [0.83, [244,109, 67]],
    [1.00, [215, 48, 39]],
  ];
  t = Math.max(0, Math.min(1, t));
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i], [t1, c1] = stops[i + 1];
    if (t <= t1) {
      const u = (t - t0) / (t1 - t0);
      return [c0[0] + u*(c1[0]-c0[0]), c0[1] + u*(c1[1]-c0[1]), c0[2] + u*(c1[2]-c0[2])];
    }
  }
  return stops[stops.length - 1][1];
}

function krig3InitMap() {
  if (KRIG.map) return KRIG.map;
  const isLight = document.body.classList.contains('light');
  const url = isLight
    ? 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png'
    : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
  // México centrado: lat ~23.5, lon ~-102, zoom 5 muestra todo el país
  const map = L.map('krig3-map', { center: [23.5, -102], zoom: 5, scrollWheelZoom: true });
  L.tileLayer(url, { maxZoom: 18, subdomains: 'abcd',
    attribution: '© OpenStreetMap · © CARTO' }).addTo(map);
  KRIG.map = map;
  krig3SetExtent('mexico');
  return map;
}

// ─── Encuadre México / mundo / sitio ──────────────────────────────────────────
function krig3SetExtent(mode) {
  if (!KRIG.map) return;
  KRIG.mode = mode;
  document.getElementById('btn-krig3-mexico')?.classList.toggle('is-active', mode === 'mexico');
  document.getElementById('btn-krig3-world') ?.classList.toggle('is-active', mode === 'world');
  if (mode === 'mexico') {
    KRIG.map.setMaxBounds(KRIG_MEXICO_BOUNDS);
    KRIG.map.fitBounds(KRIG_MEXICO_BOUNDS, { padding: [18, 18] });
  } else {
    KRIG.map.setMaxBounds(null);
  }
}

function krig3FocusSite() {
  krig3InitMap();
  // Preferimos sitio principal del Kriging; si no, leemos metadata de Data Hub.
  let target = null;
  if (KRIG.principal) {
    const s = KRIG.sites.find(x => x.filename === KRIG.principal);
    if (s) target = { lat: s.lat, lon: s.lon };
  }
  if (!target && typeof mapaCenterFromSession === 'function') {
    target = mapaCenterFromSession();
  }
  if (!target) {
    toast('Carga un sitio en Data Hub o marca un parque principal.', 'warning');
    return;
  }
  KRIG.map.setView([target.lat, target.lon], 9);
}

// ─── Selección de área de estudio (rectángulo fijo) ───────────────────────────
function krig3BeginAreaSelection() {
  krig3InitMap();
  KRIG.selectingArea = true;
  toast('Arrastra sobre el mapa para fijar el área de estudio.', 'info');
}

function krig3AreaMouseDown(e) {
  if (!KRIG.selectingArea) return;
  KRIG.areaStart = e.latlng;
  KRIG.map.dragging.disable();
  e.originalEvent.preventDefault();
}
function krig3AreaMouseMove(e) {
  if (!KRIG.selectingArea || !KRIG.areaStart) return;
  if (KRIG.areaDraft) KRIG.map.removeLayer(KRIG.areaDraft);
  KRIG.areaDraft = L.rectangle([KRIG.areaStart, e.latlng], {
    color: '#22c55e', weight: 2, fillOpacity: 0.12, dashArray: '6 4',
  }).addTo(KRIG.map);
}
function krig3AreaMouseUp(e) {
  if (!KRIG.selectingArea || !KRIG.areaStart) return;
  const bounds = L.latLngBounds(KRIG.areaStart, e.latlng);
  KRIG.selectingArea = false;
  KRIG.areaStart = null;
  KRIG.map.dragging.enable();
  if (KRIG.areaDraft) { KRIG.map.removeLayer(KRIG.areaDraft); KRIG.areaDraft = null; }
  if (KRIG.areaLayer) KRIG.map.removeLayer(KRIG.areaLayer);
  KRIG.areaBounds = bounds;
  KRIG.areaLayer = L.rectangle(bounds, {
    color: '#22c55e', weight: 2, fillOpacity: 0.08,
  }).addTo(KRIG.map);
  KRIG.map.fitBounds(bounds, { padding: [24, 24] });
  toast('Área de estudio fijada.', 'success');
  krig3RenderOptimalList();
}

function krig3ClearArea() {
  if (KRIG.areaLayer && KRIG.map) KRIG.map.removeLayer(KRIG.areaLayer);
  if (KRIG.areaDraft && KRIG.map) KRIG.map.removeLayer(KRIG.areaDraft);
  KRIG.areaLayer = null; KRIG.areaDraft = null; KRIG.areaBounds = null;
  KRIG.selectingArea = false;
  krig3RenderOptimalList();
}

// ─── Polígono del parque ──────────────────────────────────────────────────────
function krig3StartParkDraw() {
  krig3InitMap();
  KRIG.drawingPark = true;
  toast('Haz clic sobre el mapa para agregar vértices; cierra la figura al terminar.', 'info');
}
function krig3AddParkPoint(latlng) {
  KRIG.parkPoints.push([latlng.lat, latlng.lng]);
  const marker = L.circleMarker(latlng, {
    radius: 4, color: '#fff', weight: 1, fillColor: '#f59e0b', fillOpacity: 1,
  }).addTo(KRIG.map);
  KRIG.parkMarkers.push(marker);
  krig3RenderParkLayer(false);
}
function krig3RenderParkLayer(closed = true) {
  if (KRIG.parkLayer && KRIG.map) KRIG.map.removeLayer(KRIG.parkLayer);
  if (KRIG.parkPoints.length < 2) { krig3UpdateParkInfo(); return; }
  KRIG.parkLayer = closed && KRIG.parkPoints.length >= 3
    ? L.polygon(KRIG.parkPoints, { color: '#f59e0b', weight: 2, fillOpacity: 0.14 }).addTo(KRIG.map)
    : L.polyline(KRIG.parkPoints, { color: '#f59e0b', weight: 2, dashArray: '4 4' }).addTo(KRIG.map);
  krig3UpdateParkInfo();
}
function krig3FinishPark() {
  KRIG.drawingPark = false;
  if (KRIG.parkPoints.length < 3) {
    toast('Necesitas al menos 3 puntos para cerrar la figura.', 'warning');
    return;
  }
  krig3RenderParkLayer(true);
  krig3RenderOptimalList();
}
function krig3ClearPark() {
  KRIG.drawingPark = false;
  if (KRIG.parkLayer && KRIG.map) KRIG.map.removeLayer(KRIG.parkLayer);
  KRIG.parkMarkers.forEach(m => KRIG.map && KRIG.map.removeLayer(m));
  KRIG.parkLayer = null; KRIG.parkMarkers = []; KRIG.parkPoints = [];
  krig3UpdateParkInfo();
  krig3RenderOptimalList();
}

function krig3PolygonAreaKm2(points) {
  if (points.length < 3) return 0;
  const lat0 = points.reduce((s, p) => s + p[0], 0) / points.length;
  const coords = points.map(([lat, lon]) => [
    lon * 111.32 * Math.cos(lat0 * Math.PI / 180),
    lat * 110.57,
  ]);
  let area = 0;
  for (let i = 0; i < coords.length; i++) {
    const [x1, y1] = coords[i];
    const [x2, y2] = coords[(i + 1) % coords.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area) / 2;
}
function krig3PointInPolygon(point, polygon) {
  const [lat, lon] = point;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [lati, loni] = polygon[i];
    const [latj, lonj] = polygon[j];
    const cross = ((loni > lon) !== (lonj > lon)) &&
      (lat < (latj - lati) * (lon - loni) / ((lonj - loni) || 1e-12) + lati);
    if (cross) inside = !inside;
  }
  return inside;
}

function krig3UpdateParkInfo() {
  const el = document.getElementById('krig3-park-info');
  if (!el) return;
  if (KRIG.parkPoints.length < 3) {
    el.textContent = KRIG.parkPoints.length
      ? `${KRIG.parkPoints.length} vértices definidos. Falta cerrar la figura.`
      : 'Dibuja una figura sobre Leaflet para delimitar la zona del parque.';
    return;
  }
  const area = krig3PolygonAreaKm2(KRIG.parkPoints);
  el.innerHTML = `<b>Zona de parque:</b> ${area.toFixed(2)} km² · ${KRIG.parkPoints.length} vértices`;
}

// ─── Ranking de sitios óptimos (mismo criterio que Mapa & Kriging) ────────────
// Aproximación de Stirling para Γ(z), suficiente para Weibull k>0.4.
function krig3GammaApprox(z) {
  if (!(z > 0)) return 1;
  return Math.sqrt(2 * Math.PI / z) * Math.pow(z / Math.E, z) * (1 + 1 / (12 * z));
}
function krig3PowerDensity(rho, C, k) {
  if (!(C > 0 && k > 0 && rho > 0)) return 0;
  return 0.5 * rho * Math.pow(C, 3) * krig3GammaApprox(1 + 3 / k);
}

function krig3ComputeOptimal() {
  const sources = (KRIG.sites || []).filter(s => KRIG.selected.has(s.filename));
  const pool = sources.length ? sources : (KRIG.sites || []);
  if (!pool.length) { KRIG.optimal = []; return KRIG.optimal; }
  const enriched = pool.map(s => {
    const speed = Number(s.v_mean ?? 0);
    const gust  = Number(s.v_max  ?? speed * 1.6);
    const power = krig3PowerDensity(
      Number(s.rho_mean ?? 1.225),
      Number(s.weibull_C ?? speed * 1.128),
      Number(s.weibull_K ?? 2.0),
    );
    return { filename: s.filename, lat: s.lat, lon: s.lon, speed, gust, power };
  });
  const scale = (vals) => {
    const arr = vals.map(Number);
    const lo = Math.min(...arr), hi = Math.max(...arr);
    if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return arr.map(_ => 50);
    return arr.map(v => 100 * (v - lo) / (hi - lo));
  };
  const speedS = scale(enriched.map(e => e.speed));
  const powerS = scale(enriched.map(e => e.power));
  const gustS  = scale(enriched.map(e => e.gust));
  enriched.forEach((e, i) => {
    e.score = 0.45 * speedS[i] + 0.35 * powerS[i] + 0.20 * gustS[i];
  });
  enriched.sort((a, b) => b.score - a.score);
  KRIG.optimal = enriched.slice(0, 12).map((e, i) => ({ rank: i + 1, ...e }));
  return KRIG.optimal;
}

function krig3RenderOptimalList() {
  krig3ComputeOptimal();
  const el = document.getElementById('krig3-optimal-list');
  const status = document.getElementById('krig3-optimal-status');
  if (!el) return;
  let sites = KRIG.optimal.slice();
  if (KRIG.parkPoints.length >= 3) {
    sites = sites.filter(s => krig3PointInPolygon([s.lat, s.lon], KRIG.parkPoints));
  }
  if (KRIG.areaBounds) {
    sites = sites.filter(s => KRIG.areaBounds.contains(L.latLng(s.lat, s.lon)));
  }
  if (status) {
    if (!KRIG.optimal.length) status.textContent = '—';
    else status.textContent = `${sites.length}/${KRIG.optimal.length} candidatos`;
  }
  if (!sites.length) {
    el.textContent = KRIG.optimal.length
      ? 'Sin candidatos dentro del área/zona actual.'
      : 'Genera o carga sitios para calcular candidatos.';
    return;
  }
  el.innerHTML = sites.slice(0, 8).map(s => (
    `<div style="padding:.35rem 0;border-bottom:1px solid var(--border)">` +
      `<b>#${s.rank}</b> ${s.filename}<br>` +
      `Puntuación ${s.score.toFixed(1)} · Vel ${s.speed.toFixed(2)} m/s · ` +
      `Ráfaga ${s.gust.toFixed(2)} m/s · Pd ${s.power.toFixed(0)} W/m²` +
    `</div>`
  )).join('');
}

// ─── Dispatcher único de eventos del mapa Kriging ─────────────────────────────
function krig3HandleMapClick(e) {
  if (KRIG.drawingPark) krig3AddParkPoint(e.latlng);
}

// ── Distancia haversine (km) ──────────────────────────────────────────────────
function krig3HaversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371, toRad = d => d * Math.PI / 180;
  const dLat = toRad(lat2 - lat1), dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1))*Math.cos(toRad(lat2))*Math.sin(dLon/2)**2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// ── Estilo de marcador de sitio según selección ───────────────────────────────
function krig3MarkerStyle(filename) {
  const isPrincipal = filename === KRIG.principal;
  const isSelected = KRIG.selected.has(filename);
  if (isPrincipal) return { radius: 9, color: '#fff', weight: 2, fillColor: '#facc15', fillOpacity: 1 };
  if (isSelected)  return { radius: 6, color: '#fff', weight: 1, fillColor: '#3b82f6', fillOpacity: 0.95 };
  return                  { radius: 5, color: '#94a3b8', weight: 1, fillColor: '#1e293b', fillOpacity: 0.55 };
}

function krig3UpdateMarker(filename) {
  const m = KRIG.siteMarkers[filename];
  if (m) m.setStyle(krig3MarkerStyle(filename));
}

function krig3UpdateAllMarkers() {
  Object.keys(KRIG.siteMarkers).forEach(krig3UpdateMarker);
  krig3UpdateSelectionInfo();
  krig3RenderOptimalList();
}

function krig3UpdateSelectionInfo() {
  const el = document.getElementById('krig3-selection-info');
  if (!el) return;
  const total = KRIG.sites.length;
  const sel = KRIG.selected.size;
  const principalSite = KRIG.sites.find(x => x.filename === KRIG.principal);
  const principalLabel = principalSite
    ? `principal: <b>${principalSite.display_name || principalSite.filename}</b>`
    : '<span style="color:#f59e0b">sin parque principal</span>';
  el.innerHTML = `${sel}/${total} sitios seleccionados · ${principalLabel}`;
}

function krig3SitePopupHtml(s) {
  const isPrincipal = s.filename === KRIG.principal;
  return `
    <div style="min-width:220px">
      <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">${s.display_name || s.filename}</div>
      ${s.state ? `<div style="font-size:.72rem;color:#94a3b8;margin-bottom:.25rem">${s.state}${s.municipality ? ' · ' + s.municipality : ''}</div>` : ''}
      <div style="font-size:.75rem;color:#64748b;line-height:1.4">
        ${s.lat.toFixed(3)}°N  ${s.lon.toFixed(3)}°W<br>
        v̄ ${(+s.v_mean).toFixed(2)} m/s · Weibull C=${(+s.weibull_C).toFixed(2)} K=${(+s.weibull_K).toFixed(2)}
      </div>
      <div style="display:flex;gap:.3rem;margin-top:.5rem;flex-wrap:wrap">
        <button class="btn-secondary krig3-popup-toggle" data-fn="${s.filename}" style="font-size:.72rem;padding:.25rem .5rem">
          ${KRIG.selected.has(s.filename) ? '✕ Excluir' : '✓ Incluir'}
        </button>
        <button class="btn-primary krig3-popup-principal" data-fn="${s.filename}" style="font-size:.72rem;padding:.25rem .5rem">
          ${isPrincipal ? '★ Es principal' : '★ Marcar principal'}
        </button>
      </div>
    </div>`;
}

function krig3RebindPopupButtons() {
  document.querySelectorAll('.krig3-popup-toggle').forEach(b => {
    b.onclick = () => krig3ToggleSite(b.dataset.fn);
  });
  document.querySelectorAll('.krig3-popup-principal').forEach(b => {
    b.onclick = () => krig3SetPrincipal(b.dataset.fn);
  });
}

function krig3ToggleSite(filename) {
  if (KRIG.selected.has(filename)) KRIG.selected.delete(filename);
  else KRIG.selected.add(filename);
  // Si el principal sale de la selección, lo limpiamos.
  if (KRIG.principal && !KRIG.selected.has(KRIG.principal)) KRIG.principal = null;
  krig3UpdateMarker(filename);
  krig3UpdateSelectionInfo();
  // Refresh popup content for the open marker.
  const m = KRIG.siteMarkers[filename];
  if (m) {
    const s = KRIG.sites.find(x => x.filename === filename);
    if (s) m.setPopupContent(krig3SitePopupHtml(s));
  }
}

function krig3SetPrincipal(filename) {
  KRIG.principal = filename;
  // Si el principal no estaba seleccionado, inclúyelo.
  KRIG.selected.add(filename);
  krig3UpdateAllMarkers();
  // Refresh all popup contents (the previous principal needs to lose ★).
  Object.entries(KRIG.siteMarkers).forEach(([fn, m]) => {
    const s = KRIG.sites.find(x => x.filename === fn);
    if (s) m.setPopupContent(krig3SitePopupHtml(s));
  });
}

// ── Cargar todos los sitios y dibujarlos sobre el mapa de México ──────────────
async function krig3LoadSites(force=false) {
  if (KRIG.sites.length && !force) return;
  const map = krig3InitMap();
  try {
    const year = parseInt(document.getElementById('krig3-year').value) || 2018;
    const r = await api(`/api/kriging/sites?year=${year}`, 'GET');
    KRIG.sites = r.sites || [];
    KRIG.selected = new Set(KRIG.sites.map(s => s.filename));  // por defecto todos
    KRIG.principal = null;
    // limpiar marcadores anteriores
    Object.values(KRIG.siteMarkers).forEach(m => map.removeLayer(m));
    KRIG.siteMarkers = {};
    KRIG.sites.forEach(s => {
      const m = L.circleMarker([s.lat, s.lon], krig3MarkerStyle(s.filename))
        .bindPopup(krig3SitePopupHtml(s))
        .on('click', () => {
          // El click sólo abre el popup; los botones del popup hacen la acción.
          // Para selección rápida: shift+click toggle.
        })
        .addTo(map);
      // Shift+click rápido para toggle.
      m.on('mousedown', (e) => {
        if (e.originalEvent && e.originalEvent.shiftKey) {
          krig3ToggleSite(s.filename);
          e.originalEvent.preventDefault();
        }
      });
      m.on('popupopen', () => setTimeout(krig3RebindPopupButtons, 30));
      KRIG.siteMarkers[s.filename] = m;
    });
    // Auto-zoom to fit all loaded sites
    if (KRIG.sites.length) {
      const bounds = L.latLngBounds(KRIG.sites.map(s => [s.lat, s.lon]));
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 10 });
    }
    krig3UpdateSelectionInfo();
    krig3RenderOptimalList();
  } catch (e) {
    toast(`No se pudieron cargar sitios: ${e.message}`, 'error');
  }
}

function krig3RenderLayer(key) {
  if (!KRIG.result) return;
  const grid = KRIG.result[key];
  if (!grid) return;
  const meta = KRIG_LAYERS[key] || { unit: '' };
  const map = krig3InitMap();
  // limpiar overlays anteriores (NO los marcadores de selección)
  KRIG.overlays.forEach(l => map.removeLayer(l));
  KRIG.overlays = [];

  const { z, bounds, z_min, z_max, points } = grid;
  const ny = z.length, nx = z[0].length;
  const canvas = document.createElement('canvas');
  canvas.width = nx; canvas.height = ny;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(nx, ny);
  const rng = z_max - z_min;
  for (let j = 0; j < ny; j++) {
    const row = z[ny - 1 - j];
    for (let i = 0; i < nx; i++) {
      const t = rng > 0 ? (row[i] - z_min) / rng : 0.5;
      const [r_, g_, b_] = krigColor(t);
      const idx = (j * nx + i) * 4;
      img.data[idx]     = r_;
      img.data[idx + 1] = g_;
      img.data[idx + 2] = b_;
      img.data[idx + 3] = 190;
    }
  }
  ctx.putImageData(img, 0, 0);
  const overlay = L.imageOverlay(canvas.toDataURL(),
    [[bounds.lat_min, bounds.lon_min], [bounds.lat_max, bounds.lon_max]],
    { opacity: 0.78 }).addTo(map);
  KRIG.overlays.push(overlay);

  // Re-pinta los markers de los sitios usados con el color del valor (encima del overlay).
  (points || []).forEach(p => {
    const t = rng > 0 ? (p.value - z_min) / rng : 0.5;
    const [r_, g_, b_] = krigColor(t);
    const isPrincipal = p.name === KRIG.principal;
    const m = L.circleMarker([p.lat, p.lon], {
      radius: isPrincipal ? 9 : 6,
      color: isPrincipal ? '#fff' : '#111',
      weight: isPrincipal ? 2 : 1,
      fillColor: `rgb(${r_|0},${g_|0},${b_|0})`, fillOpacity: 1,
    }).bindPopup(`<b>${p.name}</b>${isPrincipal ? ' ★' : ''}<br>${grid.title}<br><b>${p.value.toFixed(2)} ${meta.unit}</b>`).addTo(map);
    KRIG.overlays.push(m);
  });

  document.getElementById('krig3-map-title').textContent = grid.title || meta.title;
  document.getElementById('krig3-legend').textContent = krig3FormatRange(grid, meta.unit);
  setTimeout(() => map.invalidateSize(), 60);
}

// ── Auto-zoom alrededor del parque principal tras correr Kriging ──────────────
// Si no hay parque principal, encuadra al extent del grid para que el overlay sea visible.
function krig3ZoomToPrincipal() {
  if (!KRIG.map) return;
  if (KRIG.principal) {
    const s = KRIG.sites.find(x => x.filename === KRIG.principal);
    if (s) {
      const radiusKm = parseFloat(document.getElementById('krig3-zoom-km').value) || 100;
      const dLat = radiusKm / 111.0;
      const dLon = radiusKm / (111.0 * Math.cos(s.lat * Math.PI / 180));
      KRIG.map.fitBounds([[s.lat - dLat, s.lon - dLon], [s.lat + dLat, s.lon + dLon]],
        { padding: [20, 20] });
      return;
    }
  }
  // Sin principal: ajusta a los bounds del grid del Kriging (cualquiera de las 3 capas).
  const g = KRIG.result && (KRIG.result.wind || KRIG.result.inversion || KRIG.result.van);
  if (g && g.bounds) {
    KRIG.map.fitBounds(
      [[g.bounds.lat_min, g.bounds.lon_min], [g.bounds.lat_max, g.bounds.lon_max]],
      { padding: [30, 30] });
  }
}

function krig3SetLayer(key) {
  if (!KRIG_LAYERS[key]) return;
  KRIG.layerKey = key;
  document.querySelectorAll('.krig3-tab').forEach(b => {
    b.classList.toggle('is-active', b.dataset.layer === key);
  });
  krig3RenderLayer(key);
}

// ── Visor de curvas de potencia (real + ajustada) ─────────────────────────────
function krig3PopulateCurveSelect() {
  const sel = document.getElementById('krig3-curve-select');
  if (!sel) return;
  sel.innerHTML = '';
  const curves = (KRIG.result && KRIG.result.turbine_curves) || [];
  if (!curves.length) {
    sel.innerHTML = '<option value="">— Sin catálogo —</option>';
    return;
  }
  curves.forEach((c, i) => {
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = `${c.name}  ·  ${c.p_nom_kw} kW  ·  Ø ${c.rotor_m*2} m`;
    sel.appendChild(o);
  });
  sel.value = String(KRIG.curveIdx);
}

function krig3RenderCurve() {
  const curves = (KRIG.result && KRIG.result.turbine_curves) || [];
  if (!curves.length) return;
  const idx = Math.max(0, Math.min(KRIG.curveIdx, curves.length - 1));
  KRIG.curveIdx = idx;
  const c = curves[idx];

  const counter = document.getElementById('krig3-curve-counter');
  if (counter) counter.textContent = `${idx + 1} / ${curves.length}`;
  const meta = document.getElementById('krig3-curve-meta');
  if (meta) meta.textContent = `Pnom ${c.p_nom_kw} kW · rotor Ø ${c.rotor_m*2} m`;

  destroyChart('krig3-curve');
  const ctx = document.getElementById('krig3-curve-chart').getContext('2d');
  const rawPts = c.x_raw.map((x, i) => ({ x, y: c.y_raw_kw[i] }));
  const fitPts = c.x_fit.map((x, i) => ({ x, y: c.y_fit_kw[i] }));
  WP.charts['krig3-curve'] = new Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        { label: 'Real (catálogo)', data: rawPts, showLine: true,
          borderColor: '#3b82f6', backgroundColor: '#3b82f6',
          pointRadius: 3, borderWidth: 1.5 },
        { label: 'Ajustada (gauss4)', data: fitPts, showLine: true,
          borderColor: '#ef4444', borderDash: [6, 4],
          pointRadius: 0, borderWidth: 2 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        title: { display: true, text: c.name },
        legend: { position: 'bottom' },
      },
      scales: {
        x: { type: 'linear', title: { display: true, text: 'Velocidad del viento (m/s)' } },
        y: { title: { display: true, text: 'Potencia (kW)' }, beginAtZero: true },
      },
    },
  });
}

function krig3CurveStep(delta) {
  const curves = (KRIG.result && KRIG.result.turbine_curves) || [];
  if (!curves.length) return;
  KRIG.curveIdx = (KRIG.curveIdx + delta + curves.length) % curves.length;
  const sel = document.getElementById('krig3-curve-select');
  if (sel) sel.value = String(KRIG.curveIdx);
  krig3RenderCurve();
}

function krig3FormatRange(g, unit='') {
  return `${g.z_min.toFixed(2)} → ${g.z_max.toFixed(2)} ${unit}`;
}

async function krig3Run() {
  if (KRIG.selected.size < 3) {
    toast('Selecciona al menos 3 sitios en el mapa para Kriging.', 'error');
    return;
  }
  const principal = KRIG.sites.find(x => x.filename === KRIG.principal);
  const body = {
    year:          parseInt(document.getElementById('krig3-year').value),
    demand_mwh:    parseFloat(document.getElementById('krig3-demand').value),
    tasa:          parseFloat(document.getElementById('krig3-tasa').value) / 100,
    price_usd_kwh: parseFloat(document.getElementById('krig3-price').value),
    price_mxn_per_usd: parseFloat(document.getElementById('krig3-fx').value),
    n_grid:        parseInt(document.getElementById('krig3-n').value),
    model:         document.getElementById('krig3-model').value,
    filenames:     Array.from(KRIG.selected),
  };
  if (principal) {
    body.center_file = principal.filename;
    body.center_lat  = principal.lat;
    body.center_lon  = principal.lon;
  }
  KRIG.result = null;
  document.getElementById('krig3-status').textContent = 'Ejecutando…';
  document.getElementById('krig3-progress-wrap').style.display = '';
  document.getElementById('krig3-progress-text').textContent =
    `Procesando ${KRIG.selected.size} sitios seleccionados…`;
  try {
    const r = await api('/api/kriging/legacy_maps', 'POST', body);
    KRIG.task_id = r.task_id;
    krig3Poll();
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('krig3-status').textContent = 'Error';
  }
}

async function krig3Poll() {
  if (!KRIG.task_id) return;
  try {
    const r = await api(`/api/kriging/status/${KRIG.task_id}`, 'GET');
    document.getElementById('krig3-progress-text').textContent = r.message || '';
    document.getElementById('krig3-progress-fill').style.width = `${r.progress || 0}%`;
    if (r.status === 'done') {
      const d = r.result;
      KRIG.result = d;
      KRIG.curveIdx = 0;
      // Oculta los markers de selección mientras se muestra el resultado.
      Object.values(KRIG.siteMarkers).forEach(m => KRIG.map && KRIG.map.removeLayer(m));
      krig3SetLayer(KRIG.layerKey || 'wind');
      krig3PopulateCurveSelect();
      krig3RenderCurve();
      // Auto-zoom alrededor del parque principal (100 km por defecto).
      krig3ZoomToPrincipal();
      const nCurves = (d.turbine_curves || []).length;
      document.getElementById('krig3-status').textContent =
        `${d.n_sites} sitios · 3 mapas · ${nCurves} curvas`;
      document.getElementById('krig3-progress-fill').style.width = '100%';
      toast('Kriging listo', 'success');
    } else if (r.status === 'error') {
      toast(`Kriging falló: ${r.message}`, 'error');
      document.getElementById('krig3-status').textContent = 'Error';
    } else {
      setTimeout(krig3Poll, 800);
    }
  } catch (e) {
    toast(`Polling: ${e.message}`, 'error');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const bRun = document.getElementById('btn-krig3-run');
  if (bRun) bRun.addEventListener('click', krig3Run);

  // Tabs de capa (Velocidad / Inversión / VAN)
  document.querySelectorAll('.krig3-tab').forEach(b => {
    b.addEventListener('click', () => krig3SetLayer(b.dataset.layer));
  });

  // Visor de curvas: prev / next / select
  const bPrev = document.getElementById('btn-curve-prev');
  const bNext = document.getElementById('btn-curve-next');
  const sel   = document.getElementById('krig3-curve-select');
  if (bPrev) bPrev.addEventListener('click', () => krig3CurveStep(-1));
  if (bNext) bNext.addEventListener('click', () => krig3CurveStep(+1));
  if (sel)   sel.addEventListener('change', () => {
    KRIG.curveIdx = parseInt(sel.value) || 0;
    krig3RenderCurve();
  });

  // Botones de selección masiva
  const bAll = document.getElementById('btn-krig3-select-all');
  if (bAll) bAll.addEventListener('click', () => {
    KRIG.selected = new Set(KRIG.sites.map(s => s.filename));
    krig3UpdateAllMarkers();
  });
  const bNone = document.getElementById('btn-krig3-select-none');
  if (bNone) bNone.addEventListener('click', () => {
    KRIG.selected.clear();
    KRIG.principal = null;
    krig3UpdateAllMarkers();
  });

  // Si cambia el año, recargar sitios (n_hours puede variar)
  const yearSel = document.getElementById('krig3-year');
  if (yearSel) yearSel.addEventListener('change', () => krig3LoadSites(true));

  // ── Rectangular selection: Ctrl+drag to select/deselect sites in a box ──
  (function initRectSelect() {
    let rectLayer = null, startLL = null, active = false;
    function onMouseDown(e) {
      if (KRIG.selectingArea) return;  // ceder al modo "Seleccionar área"
      if (!e.originalEvent.ctrlKey && !e.originalEvent.metaKey) return;
      active = true; startLL = e.latlng;
      KRIG.map && KRIG.map.dragging.disable();
      e.originalEvent.preventDefault();
    }
    function onMouseMove(e) {
      if (!active || !startLL) return;
      if (rectLayer) KRIG.map.removeLayer(rectLayer);
      rectLayer = L.rectangle([startLL, e.latlng], {
        color: '#3b82f6', weight: 2, fillOpacity: 0.15, dashArray: '6 4'
      }).addTo(KRIG.map);
    }
    function onMouseUp(e) {
      if (!active || !startLL) return;
      active = false;
      KRIG.map && KRIG.map.dragging.enable();
      if (rectLayer) {
        const b = rectLayer.getBounds();
        KRIG.map.removeLayer(rectLayer); rectLayer = null;
        // Toggle sites within bounds
        let toggled = 0;
        KRIG.sites.forEach(s => {
          if (b.contains(L.latLng(s.lat, s.lon))) {
            KRIG.selected.add(s.filename);
            toggled++;
          }
        });
        if (toggled) { krig3UpdateAllMarkers(); toast(`${toggled} sitios seleccionados`, 'info'); }
      }
      startLL = null;
    }
    // Defer binding until map is initialized
    const origInit = krig3InitMap;
    krig3InitMap = function() {
      const map = origInit();
      if (!map._rectSelectBound) {
        map.on('mousedown', onMouseDown);
        map.on('mousemove', onMouseMove);
        map.on('mouseup', onMouseUp);
        // Handlers integrados (Mapa & Kriging) — área fija + clic para parque
        map.on('mousedown', krig3AreaMouseDown);
        map.on('mousemove', krig3AreaMouseMove);
        map.on('mouseup',   krig3AreaMouseUp);
        map.on('click',     krig3HandleMapClick);
        map._rectSelectBound = true;
      }
      return map;
    };
  })();

  // ── Botones encuadre / área / parque ──
  document.getElementById('btn-krig3-mexico') ?.addEventListener('click', () => krig3SetExtent('mexico'));
  document.getElementById('btn-krig3-world')  ?.addEventListener('click', () => krig3SetExtent('world'));
  document.getElementById('btn-krig3-site')   ?.addEventListener('click', krig3FocusSite);
  document.getElementById('btn-krig3-select-area')?.addEventListener('click', krig3BeginAreaSelection);
  document.getElementById('btn-krig3-clear-area') ?.addEventListener('click', krig3ClearArea);
  document.getElementById('btn-krig3-draw-park')  ?.addEventListener('click', krig3StartParkDraw);
  document.getElementById('btn-krig3-finish-park')?.addEventListener('click', krig3FinishPark);
  document.getElementById('btn-krig3-clear-park') ?.addEventListener('click', krig3ClearPark);

  // Al entrar a la pestaña: inicializar mapa + cargar sitios + restaurar selección.
  // OJO: bindNavigation() se registra después de un await initSession(), por lo
  // que el handler de navegación corre DESPUÉS de este. Diferimos con rAF para
  // que la sección ya esté visible (display:block) cuando Leaflet mida el div.
  document.querySelectorAll('.nav-btn[data-module="kriging"]').forEach(b => {
    b.addEventListener('click', () => {
      requestAnimationFrame(() => {
        krig3InitMap();
        krig3LoadSites();
        if (!KRIG.result) {
          Object.values(KRIG.siteMarkers).forEach(m => {
            if (KRIG.map && !KRIG.map.hasLayer(m)) m.addTo(KRIG.map);
          });
        }
        // Doble invalidación: una inmediata y otra después de cualquier transición CSS.
        if (KRIG.map) KRIG.map.invalidateSize();
        setTimeout(() => { if (KRIG.map) KRIG.map.invalidateSize(); }, 200);
      });
    });
  });
});


// ═══════════════════════ MCDA (TOPSIS) ═══════════════════════════════════════
const MCDA = {
  taskId: null,
  lastAnalysis: null,   // raw analyze_sites output
  lastRanking:  null,   // last TOPSIS result
  defaults:     null,
};

async function mcdaLoadDefaults() {
  if (MCDA.defaults) return MCDA.defaults;
  MCDA.defaults = await api('/api/multicriteria/defaults', 'GET');
  return MCDA.defaults;
}

async function mcdaBuildWeightSliders() {
  const d = await mcdaLoadDefaults();
  const container = document.getElementById('mcda-weights');
  if (!container) return;
  container.innerHTML = Object.entries(d.weights).map(([k, v]) => {
    const dir = d.directions[k];
    const label = d.labels[k];
    const hint = dir > 0 ? '↑ max' : '↓ min';
    return `
      <div style="display:flex;align-items:center;gap:.5rem;font-size:.78rem">
        <label style="flex:1;color:var(--text-secondary)">${label}
          <span style="color:${dir>0?'var(--green)':'var(--orange)'};font-size:.7rem">${hint}</span>
        </label>
        <input type="range" min="0" max="0.5" step="0.01" value="${v}"
          data-crit="${k}" class="mcda-w"
          style="flex:1"
          oninput="this.nextElementSibling.textContent=(+this.value).toFixed(2)">
        <span style="font-family:var(--font-mono);width:34px;text-align:right">${v.toFixed(2)}</span>
      </div>`;
  }).join('');
}

function mcdaGetWeights() {
  const out = {};
  document.querySelectorAll('.mcda-w').forEach(el => {
    out[el.dataset.crit] = parseFloat(el.value);
  });
  return out;
}

async function mcdaResetWeights() {
  const d = await mcdaLoadDefaults();
  document.querySelectorAll('.mcda-w').forEach(el => {
    el.value = d.weights[el.dataset.crit];
    el.nextElementSibling.textContent = (+el.value).toFixed(2);
  });
}

async function mcdaRunAnalysis() {
  const body = {
    year:          parseInt(document.getElementById('mcda-year').value),
    demand_mwh:    parseFloat(document.getElementById('mcda-demand').value),
    tasa:          parseFloat(document.getElementById('mcda-tasa').value) / 100,
    price_usd_kwh: parseFloat(document.getElementById('mcda-price').value),
    price_mxn_per_usd: parseFloat(document.getElementById('mcda-fx').value),
    max_files:     parseInt(document.getElementById('mcda-maxfiles').value) || null,
  };
  document.getElementById('mcda-status').textContent = 'Ejecutando…';
  document.getElementById('mcda-progress-wrap').style.display = '';
  document.getElementById('mcda-progress-text').textContent = 'Iniciando…';
  try {
    const r = await api('/api/kriging/analyze', 'POST', body);
    MCDA.taskId = r.task_id;
    mcdaPollStatus();
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('mcda-status').textContent = 'Error';
    document.getElementById('mcda-progress-wrap').style.display = 'none';
  }
}

async function mcdaPollStatus() {
  if (!MCDA.taskId) return;
  try {
    const r = await api(`/api/kriging/status/${MCDA.taskId}`, 'GET');
    document.getElementById('mcda-progress-text').textContent = r.message || '';
    document.getElementById('mcda-progress-fill').style.width = `${r.progress || 0}%`;
    if (r.status === 'done') {
      MCDA.lastAnalysis = r.result;
      const nSites = r.result.length;
      const nTurbs = r.result.reduce((s, x) => s + (x.turbines||[]).length, 0);
      document.getElementById('mcda-status').textContent = `${nSites} sitios · ${nTurbs} evaluaciones`;
      document.getElementById('mcda-progress-fill').style.width = '100%';
      toast('Análisis completado — generando ranking…', 'success');
      await mcdaRankNow();
    } else if (r.status === 'error') {
      toast(`Análisis falló: ${r.message}`, 'error');
      document.getElementById('mcda-status').textContent = 'Error';
    } else {
      setTimeout(mcdaPollStatus, 800);
    }
  } catch (e) {
    toast(`Error polling: ${e.message}`, 'error');
  }
}

async function mcdaRankNow() {
  if (!MCDA.lastAnalysis) {
    toast('Ejecuta el análisis primero', 'warning');
    return;
  }
  const weights = mcdaGetWeights();
  const mode = document.getElementById('mcda-mode').value;
  try {
    const r = await api('/api/multicriteria/rank', 'POST', {
      sites_analysis: MCDA.lastAnalysis,
      weights, mode,
    });
    MCDA.lastRanking = r;
    mcdaRenderRanking(r);
  } catch (e) {
    toast(`Error ranking: ${e.message}`, 'error');
  }
}

function mcdaRenderRanking(r) {
  const body = document.getElementById('mcda-rank-body');
  const top = r.ranking.slice(0, 100);
  if (!top.length) {
    body.innerHTML = '<tr><td colspan="10" class="text-muted">Sin resultados.</td></tr>';
    return;
  }
  body.innerHTML = top.map(a => {
    const medal = a.rank === 1 ? '🥇' : a.rank === 2 ? '🥈' : a.rank === 3 ? '🥉' : a.rank;
    const highlight = a.rank === 1 ? 'style="background:var(--green-dim)"' : '';
    return `<tr ${highlight}>
      <td><strong>${medal}</strong></td>
      <td title="${a.site||''}">${(a.site||'').slice(0,30)}${(a.site||'').length>30?'…':''}</td>
      <td>${a.turbina||''}</td>
      <td><strong>${a.topsis_score.toFixed(4)}</strong></td>
      <td>${(a.factor_capacidad*100).toFixed(1)}%</td>
      <td>${Math.round(a.aep_mwh).toLocaleString()}</td>
      <td>${Math.round(a.van_usd).toLocaleString()}</td>
      <td>${a.lcoe_usd_kwh.toFixed(4)}</td>
      <td>${a.roi.toFixed(2)}</td>
      <td>${Math.round(a.capex_usd).toLocaleString()}</td>
    </tr>`;
  }).join('');
}

document.addEventListener('DOMContentLoaded', () => {
  mcdaBuildWeightSliders();
  const bRun = document.getElementById('btn-mcda-run');
  const bRe  = document.getElementById('btn-mcda-rerank');
  const bRst = document.getElementById('btn-mcda-reset-w');
  if (bRun) bRun.addEventListener('click', mcdaRunAnalysis);
  if (bRe)  bRe.addEventListener('click', mcdaRankNow);
  if (bRst) bRst.addEventListener('click', mcdaResetWeights);
});

// ═══════════════════════ INGENIERÍA DRAWER (f buttons) ═══════════════════════
/*
  Map of topic slugs → anchor in /ingenieria doc.
  Keep these in sync with section ids in templates/ingenieria.html.
*/
const ING_ANCHORS = {
  architecture:  's1',
  flow:          's2',   // Log profile, geostrophic, friction
  weibull:       's3',
  orography:     's4',
  roughness:     's5',
  aep:           's6',   // AEP, Cp, Power density, CF
  power_density: 's6',
  cf:            's6',
  wake:          's7',
  rix:           's8',
  kriging:       's13',  // Resource grid (closest analog)
  powercurve:    's10',  // wtg_parser
  project:       's9',
};

function openIngenieria(topic) {
  const drawer = document.getElementById('ing-drawer');
  const back   = document.getElementById('ing-backdrop');
  const frame  = document.getElementById('ing-frame');
  if (!drawer || !frame) return;
  const anchor = ING_ANCHORS[topic] || '';
  const url = '/ingenieria' + (anchor ? `#${anchor}` : '');
  // Force reload to honor anchor, even if drawer already has same src
  if (frame.dataset.lastSrc !== url) {
    frame.src = url;
    frame.dataset.lastSrc = url;
  } else {
    try { frame.contentWindow.location.hash = anchor; } catch (e) {}
  }
  drawer.classList.add('open');
  back.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
}

function closeIngenieria() {
  document.getElementById('ing-drawer').classList.remove('open');
  document.getElementById('ing-backdrop').classList.remove('open');
  document.getElementById('ing-drawer').setAttribute('aria-hidden', 'true');
}

function ingSprinkle() {
  /*
    Attach subtle ƒ buttons to existing elements by CSS selector → topic.
    We add one button if and only if none exists yet (idempotent).
  */
  const targets = [
    // (selector, topic)
    ['[data-module="kriging"]',              'kriging'],
    ['#mod-wind .card-title',                'weibull'],
    ['#mod-energy .card-title',              'aep'],
    ['#mod-techno .card-title',              'aep'],
    ['#mod-advanced .card-title',            'orography'],
    ['#mod-hybrid .card-title',              'aep'],
    ['#krig-map',                            'kriging'],
    ['#mcda-rank-tbl',                       'aep'],
  ];
  const mkBtn = (topic) => {
    const b = document.createElement('button');
    b.className = 'btn-formula';
    b.textContent = 'ƒ';
    b.title = `Ver fundamento matemático (${topic})`;
    b.type = 'button';
    b.addEventListener('click', (e) => { e.stopPropagation(); openIngenieria(topic); });
    return b;
  };
  const seen = new WeakSet();
  targets.forEach(([sel, topic]) => {
    document.querySelectorAll(sel).forEach(el => {
      if (seen.has(el)) return;
      if (el.querySelector(':scope > .btn-formula')) return;
      // For .card-title spans: append inside
      if (el.tagName === 'SPAN' || el.classList.contains('card-title')) {
        el.appendChild(mkBtn(topic));
      } else {
        // For container elements, put the button in their card-header (nearest)
        const header = el.closest('.card')?.querySelector('.card-header');
        if (header && !header.querySelector(':scope > .btn-formula')) {
          header.appendChild(mkBtn(topic));
        }
      }
      seen.add(el);
    });
  });

  // Also: for KPI cards (`.kpi`), add a small ƒ in top-right if it has a known metric label
  document.querySelectorAll('.kpi').forEach(k => {
    if (k.querySelector('.btn-formula')) return;
    const lbl = (k.querySelector('.kl,.kpi-label')?.textContent || '').toLowerCase();
    let topic = null;
    if (lbl.includes('weibull'))                   topic = 'weibull';
    else if (lbl.includes('aep') || lbl.includes('energía') || lbl.includes('energia')) topic = 'aep';
    else if (lbl.includes('factor') || lbl.includes('cf')) topic = 'cf';
    else if (lbl.includes('cp') || lbl.includes('coef')) topic = 'powercurve';
    else if (lbl.includes('densidad')) topic = 'power_density';
    if (topic) {
      const btn = mkBtn(topic);
      btn.style.cssText = 'position:absolute;top:6px;right:8px';
      k.style.position = 'relative';
      k.appendChild(btn);
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  // Wire open/close
  const fab = document.getElementById('ing-fab');
  if (fab) fab.addEventListener('click', () => openIngenieria('architecture'));
  const close = document.getElementById('ing-close');
  if (close) close.addEventListener('click', closeIngenieria);
  const back = document.getElementById('ing-backdrop');
  if (back) back.addEventListener('click', closeIngenieria);
  const openTab = document.getElementById('ing-open-tab');
  if (openTab) openTab.addEventListener('click', () => window.open('/ingenieria', '_blank'));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeIngenieria();
  });
  // First sprinkle (some elements exist now)
  setTimeout(ingSprinkle, 80);
  // Re-sprinkle when user switches modules (new DOM becomes visible)
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.addEventListener('click', () => setTimeout(ingSprinkle, 60));
  });
});

// ═══════════════════════ MONTH × HOUR HEATMAP ═══════════════════════════════
function renderMonthHourHeatmap(data) {
  const container = document.getElementById('mh-heatmap');
  if (!container) return;
  if (!data || !data.matrix) {
    container.innerHTML = '<span class="text-muted">Sin datos de mapa de calor. Verifica que el archivo tenga fechas e índice temporal.</span>';
    return;
  }
  try {
    const { matrix, months, hours, vmin, vmax, overall_mean } = data;
    const rng = Math.max(1e-9, vmax - vmin);
    const norm = v => (v - vmin) / rng;

    // HTML grid with value labels
    let html = '<div style="display:grid;grid-template-columns:42px repeat(24,1fr);gap:2px;font-family:var(--font-mono);font-size:.60rem;line-height:1">';
    // Hour headers
    html += '<div></div>' + hours.map(h => `<div style="text-align:center;color:var(--text-muted);font-weight:600;padding-bottom:4px">${h}</div>`).join('');
    for (let m = 0; m < 12; m++) {
      html += `<div style="color:var(--text-muted);font-weight:600;padding-right:6px;text-align:right;align-self:center">${months[m]}</div>`;
      for (let h = 0; h < 24; h++) {
        const v = matrix[m][h];
        const t = norm(v);
        const [r_, g_, b_] = krigColor(t);
        // Contrast: light text on dark cells, dark text on bright cells
        const lum = 0.299 * r_ + 0.587 * g_ + 0.114 * b_;
        const txtCol = lum > 140 ? '#1a1a2e' : '#f0f0f0';
        html += `<div title="${months[m]} ${hours[h]}:00 h · ${v.toFixed(2)} m/s"
                      style="background:rgb(${r_|0},${g_|0},${b_|0});height:26px;border-radius:3px;
                             display:flex;align-items:center;justify-content:center;
                             color:${txtCol};font-size:.52rem;font-weight:500;cursor:default;
                             transition:transform .1s;" 
                      onmouseenter="this.style.transform='scale(1.15)';this.style.zIndex='2'" 
                      onmouseleave="this.style.transform='';this.style.zIndex=''">${v.toFixed(1)}</div>`;
      }
    }
    html += '</div>';

    // Color scale bar
    const nSteps = 60;
    let scaleHtml = '<div style="display:flex;align-items:center;gap:8px;margin-top:10px;font-size:.68rem;color:var(--text-muted)">';
    scaleHtml += `<span style="font-weight:600">${vmin.toFixed(1)}</span>`;
    scaleHtml += '<div style="display:flex;flex:1;height:10px;border-radius:4px;overflow:hidden">';
    for (let i = 0; i < nSteps; i++) {
      const t = i / (nSteps - 1);
      const [r_, g_, b_] = krigColor(t);
      scaleHtml += `<div style="flex:1;background:rgb(${r_|0},${g_|0},${b_|0})"></div>`;
    }
    scaleHtml += '</div>';
    scaleHtml += `<span style="font-weight:600">${vmax.toFixed(1)}</span>`;
    scaleHtml += '<span style="margin-left:8px">m/s</span></div>';
    html += scaleHtml;

    container.innerHTML = html;
    document.getElementById('mh-legend').textContent =
      `${vmin.toFixed(1)} → ${vmax.toFixed(1)} m/s · μ = ${overall_mean} m/s`;
  } catch (e) {
    container.innerHTML = `<span style="color:var(--red)">Error renderizando heatmap: ${e.message}</span>`;
  }
}

// renderMonthHourHeatmap is now called from within calcWindAnalysis() with pre-fetched data.


// ═══════════════════════ PARQUE EÓLICO ═══════════════════════════════════════
const PARQUE = { result: null, layout: [] };

function parqueBuildLayout() {
  const rows = parseInt(document.getElementById('parque-rows').value) || 2;
  const cols = parseInt(document.getElementById('parque-cols').value) || 4;
  const sep  = parseFloat(document.getElementById('parque-sep').value) || 400;
  const layout = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      layout.push([c * sep, r * sep]);
    }
  }
  PARQUE.layout = layout;
  return layout;
}

async function parqueLoadTurbines() {
  const sel = document.getElementById('parque-turbine');
  if (!sel || sel.options.length) return;
  try {
    const r = await api('/api/energy/turbines', 'GET');
    sel.innerHTML = '';
    const og1 = document.createElement('optgroup'); og1.label = 'Builtin';
    const og2 = document.createElement('optgroup'); og2.label = 'Catálogo (CSV)';
    (r.turbines || []).forEach(t => {
      const opt = document.createElement('option');
      if (t.source === 'builtin') {
        opt.value = t.name;
        opt.textContent = `${t.name} — ${(t.rated_power_kw / 1000).toFixed(1)} MW`;
        og1.appendChild(opt);
      } else {
        opt.value = t.filename;
        opt.textContent = `${t.name}  ⌀${t.rotor_diameter_m || '?'} m  ${t.rated_power_kw ? (t.rated_power_kw/1000).toFixed(1)+' MW' : ''}`;
        og2.appendChild(opt);
      }
    });
    if (og1.children.length) sel.appendChild(og1);
    if (og2.children.length) sel.appendChild(og2);
  } catch (e) { /* silent */ }
}

function parqueDrawLayout() {
  const canvas = document.getElementById('parque-canvas');
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width  = canvas.clientWidth * dpr;
  canvas.height = canvas.clientHeight * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = canvas.clientWidth, H = canvas.clientHeight;
  ctx.clearRect(0, 0, W, H);

  const turbines = (PARQUE.result && PARQUE.result.turbines) || [];
  const layout = turbines.length ? turbines.map(t => [t.x_m, t.y_m]) : PARQUE.layout;
  if (!layout.length) {
    ctx.fillStyle = mut();
    ctx.font = '12px Inter';
    ctx.fillText('Pulsa "Simular parque" para ver el layout.', 20, H/2);
    return;
  }
  const xs = layout.map(t => t[0]), ys = layout.map(t => t[1]);
  const pad = 60;
  const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
  const sx = v => (v - minX) / (maxX - minX) * (W - 60) + 30;
  const sy = v => H - (v - minY) / (maxY - minY) * (H - 60) - 30;

  // Grid
  ctx.strokeStyle = isLight() ? 'rgba(11,18,32,0.08)' : 'rgba(58,70,93,0.35)';
  ctx.lineWidth = 0.5;
  const gstep = 200;
  for (let x = Math.ceil(minX / gstep) * gstep; x < maxX; x += gstep) {
    ctx.beginPath(); ctx.moveTo(sx(x), 30); ctx.lineTo(sx(x), H - 30); ctx.stroke();
  }
  for (let y = Math.ceil(minY / gstep) * gstep; y < maxY; y += gstep) {
    ctx.beginPath(); ctx.moveTo(30, sy(y)); ctx.lineTo(W - 30, sy(y)); ctx.stroke();
  }

  // Wind arrow (dirección dominante "FROM")
  const dir = PARQUE.result && PARQUE.result.dominant_dir_deg;
  if (dir != null) {
    const theta = (270 - dir) * Math.PI / 180;
    const cx = 50, cy = 50, len = 30;
    const dx = Math.cos(theta) * len, dy = -Math.sin(theta) * len;
    ctx.strokeStyle = '#3DDC97'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx - dx, cy - dy); ctx.lineTo(cx + dx, cy + dy); ctx.stroke();
    ctx.fillStyle = '#3DDC97';
    ctx.beginPath();
    ctx.moveTo(cx + dx, cy + dy);
    ctx.lineTo(cx + dx - 6 * Math.cos(theta - Math.PI/6), cy + dy + 6 * Math.sin(theta - Math.PI/6));
    ctx.lineTo(cx + dx - 6 * Math.cos(theta + Math.PI/6), cy + dy + 6 * Math.sin(theta + Math.PI/6));
    ctx.closePath(); ctx.fill();
    ctx.font = '10px Inter';
    ctx.fillText(`Viento dom. ${dir.toFixed(0)}°`, cx + 35, cy + 4);
  }

  // Turbines
  const aeps = turbines.map(t => t.aep_mwh);
  const maxA = Math.max(...aeps, 0.001);
  const minA = Math.min(...aeps, maxA);
  layout.forEach((pt, i) => {
    const cx = sx(pt[0]), cy = sy(pt[1]);
    const t = turbines.length ? (aeps[i] - minA) / (maxA - minA + 1e-6) : 0.5;
    const [r_, g_, b_] = krigColor(t);
    ctx.beginPath(); ctx.arc(cx, cy, 13, 0, 2 * Math.PI);
    ctx.fillStyle = `rgba(${r_|0},${g_|0},${b_|0},0.12)`; ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, 8, 0, 2 * Math.PI);
    ctx.fillStyle = `rgb(${r_|0},${g_|0},${b_|0})`; ctx.fill();
    ctx.strokeStyle = isLight() ? '#0B1220' : '#fff'; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = ink(); ctx.font = '9px Inter'; ctx.textAlign = 'center';
    ctx.fillText(`T${i + 1}`, cx, cy - 12);
    if (turbines.length) {
      ctx.fillStyle = mut();
      ctx.fillText(`${aeps[i].toFixed(1)}`, cx, cy + 18);
    }
  });
  ctx.textAlign = 'left';
}

function parqueRenderAEPChart() {
  const turbines = (PARQUE.result && PARQUE.result.turbines) || [];
  if (!turbines.length) return;
  destroyChart('parque-aep');
  const ctx = document.getElementById('parque-aep-chart').getContext('2d');
  const labels = turbines.map(t => `T${t.i + 1}`);
  const aep    = turbines.map(t => t.aep_mwh);
  const noWake = turbines.map(t => t.aep_no_wake_mwh);
  WP.charts['parque-aep'] = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'AEP con estela', data: aep, backgroundColor: acc() },
      { label: 'AEP sin estela', data: noWake, backgroundColor: 'rgba(150,150,150,0.4)' },
    ]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' } },
      scales: {
        x: { grid: { display: false } },
        y: { title: { display: true, text: 'MWh / año' } },
      },
    },
  });
}

function parqueRenderTable() {
  const tb = document.getElementById('parque-tbl-body');
  const turbines = (PARQUE.result && PARQUE.result.turbines) || [];
  tb.innerHTML = turbines.map(t => `
    <tr>
      <td>T${t.i + 1}</td><td>${t.x_m.toFixed(0)}</td><td>${t.y_m.toFixed(0)}</td>
      <td>${t.aep_mwh.toFixed(1)}</td><td>${t.aep_no_wake_mwh.toFixed(1)}</td>
      <td>${t.wake_loss_pct.toFixed(2)}</td><td>${t.cf_pct.toFixed(2)}</td>
    </tr>`).join('');
}

async function parqueRenderWakeChart() {
  const rotor = (PARQUE.result && PARQUE.result.rotor_d_m) || 80;
  const k = parseFloat(document.getElementById('parque-kwake').value) || 0.05;
  const ct = parseFloat(document.getElementById('parque-ct').value) || 0.8;
  try {
    const r = await api('/api/farm/wake_profile', 'POST',
      { rotor_d_m: rotor, k_wake: k, ct });
    destroyChart('parque-wake');
    const ctx = document.getElementById('parque-wake-chart').getContext('2d');
    WP.charts['parque-wake'] = new Chart(ctx, {
      type: 'line',
      data: { datasets: [{
        label: `NOJ k=${k}, Ct=${ct}, ⌀=${rotor}m`,
        data: r.x_over_d.map((x, i) => ({ x, y: r.deficit_pct[i] })),
        borderColor: acc2(), borderWidth: 2, pointRadius: 0,
        fill: true, backgroundColor: 'rgba(244,109,67,0.08)', tension: 0.3,
      }]},
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' } },
        scales: {
          x: { type: 'linear', title: { display: true, text: 'Distancia x/⌀' } },
          y: { title: { display: true, text: 'Déficit de velocidad (%)' } },
        },
      },
    });
  } catch (e) { /* silent */ }
}

async function parqueRun() {
  if (!WP.sessionId) { toast('Sin sesión.', 'error'); return; }
  const turbineSel = document.getElementById('parque-turbine');
  const turbineKey = turbineSel.value;
  const turbineCfg = turbineKey
    ? { builtin_model: turbineKey }
    : { builtin_model: 'V150_4.5' };

  const layout = parqueBuildLayout();
  const body = {
    session_id: WP.sessionId,
    layout,
    turbine: turbineCfg,
    k_wake:    parseFloat(document.getElementById('parque-kwake').value),
    ct:        parseFloat(document.getElementById('parque-ct').value),
    n_sectors: parseInt(document.getElementById('parque-sectors').value),
    availability:      parseFloat(document.getElementById('parque-avail').value),
    electrical_losses: parseFloat(document.getElementById('parque-elec').value),
    ws_height_m:       parseFloat(document.getElementById('parque-ws-h').value),
  };
  document.getElementById('parque-status').textContent = 'Simulando…';
  try {
    const r = await api('/api/farm/simulate', 'POST', body);
    if (r.error) throw new Error(r.error);
    PARQUE.result = r;
    document.getElementById('parque-status').textContent =
      `${r.n_turbines} turbinas · ${r.installed_mw} MW · ${r.hours.toLocaleString()} h`;
    document.getElementById('parque-info').textContent =
      `Turbina: ${r.turbine?.name || '—'} · ⌀${r.rotor_d_m} m · ${r.rated_kw} kW`;

    document.getElementById('parque-kpis').style.display = '';
    document.getElementById('parque-kpi-aep').textContent = (r.aep_mwh / 1000).toFixed(2);
    document.getElementById('parque-kpi-wl').textContent  = r.wake_loss_pct.toFixed(2);
    document.getElementById('parque-kpi-cf').textContent  = r.cf_pct.toFixed(2);
    document.getElementById('parque-kpi-mw').textContent  = r.installed_mw.toFixed(2);
    document.getElementById('parque-kpi-flh').textContent = r.flh.toFixed(0);
    document.getElementById('parque-kpi-dir').textContent = r.dominant_dir_deg.toFixed(0) + '°';

    document.getElementById('parque-layout-meta').textContent =
      `${r.n_turbines} turbinas · sep ≈ ${parseFloat(document.getElementById('parque-sep').value)} m`;

    parqueDrawLayout();
    parqueRenderAEPChart();
    parqueRenderTable();
    parqueRenderWakeChart();
    toast('Parque simulado', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('parque-status').textContent = 'Error';
  }
}

function parqueReset() {
  PARQUE.result = null;
  parqueBuildLayout();
  document.getElementById('parque-kpis').style.display = 'none';
  document.getElementById('parque-tbl-body').innerHTML = '';
  document.getElementById('parque-status').textContent = 'Sin simular';
  destroyChart('parque-aep'); destroyChart('parque-wake');
  parqueDrawLayout();
}

document.addEventListener('DOMContentLoaded', () => {
  const btnRun = document.getElementById('btn-parque-run');
  if (btnRun) btnRun.addEventListener('click', parqueRun);
  const btnReset = document.getElementById('btn-parque-reset');
  if (btnReset) btnReset.addEventListener('click', parqueReset);
  ['parque-rows','parque-cols','parque-sep'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => { parqueBuildLayout(); parqueDrawLayout(); });
  });
  document.querySelectorAll('.nav-btn[data-module="parque"]').forEach(b => {
    b.addEventListener('click', () => {
      requestAnimationFrame(() => {
        parqueLoadTurbines();
        if (!PARQUE.layout.length) parqueBuildLayout();
        parqueDrawLayout();
      });
    });
  });
});


// ═══════════════════════ MAPA & GRILLA DE RECURSO ═══════════════════════════
const MAPA = {
  map: null, baseLayer: null, gridLayer: null,
  result: null, sites: [], grid: null, var: 'speed',
  mode: 'mexico',
  areaBounds: null, areaLayer: null, areaDraft: null, areaStart: null, selectingArea: false,
  drawingPark: false, parkPoints: [], parkLayer: null, parkMarkers: [],
};

const MAPA_MEXICO_BOUNDS = [[14.2, -118.6], [33.2, -86.5]];

const MAPA_TILES = {
  dark:    'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  voyager: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
  osm:     'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  topo:    'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
  sat:     'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
};

function mapaInit() {
  if (MAPA.map) return MAPA.map;
  const map = window.L.map('mapa-leaflet', {
    center: [23.5, -102], zoom: 5, scrollWheelZoom: true,
  });
  MAPA.map = map;
  mapaSetBase(document.getElementById('mapa-baselayer').value);
  mapaSetExtent('mexico');
  map.on('click', mapaHandleClick);
  map.on('mousedown', mapaAreaMouseDown);
  map.on('mousemove', mapaAreaMouseMove);
  map.on('mouseup', mapaAreaMouseUp);
  return map;
}

function mapaSetBase(key) {
  if (!MAPA.map) return;
  if (MAPA.baseLayer) MAPA.map.removeLayer(MAPA.baseLayer);
  const url = MAPA_TILES[key] || MAPA_TILES.dark;
  MAPA.baseLayer = window.L.tileLayer(url, {
    maxZoom: 18, subdomains: 'abcd',
    attribution: '© OpenStreetMap · © CARTO · ESRI',
  }).addTo(MAPA.map);
}

function mapaSetExtent(mode) {
  if (!MAPA.map) return;
  MAPA.mode = mode;
  document.getElementById('btn-mapa-mexico')?.classList.toggle('is-active', mode === 'mexico');
  document.getElementById('btn-mapa-world')?.classList.toggle('is-active', mode === 'world');
  if (mode === 'mexico') {
    MAPA.map.setMaxBounds(MAPA_MEXICO_BOUNDS);
    MAPA.map.fitBounds(MAPA_MEXICO_BOUNDS, { padding: [18, 18] });
  } else {
    MAPA.map.setMaxBounds(null);
  }
}

function mapaCenterFromSession() {
  // Try to read latitude/longitude from the loaded session metadata UI.
  const items = document.querySelectorAll('#meta-grid .meta-item');
  let lat = null, lon = null;
  items.forEach(it => {
    const lab = it.querySelector('.meta-item-label')?.textContent?.trim().toLowerCase() || '';
    const val = it.querySelector('.meta-item-val')?.textContent?.trim();
    if (lab.startsWith('lat') && !isNaN(parseFloat(val))) lat = parseFloat(val);
    if (lab.startsWith('lon') && !isNaN(parseFloat(val))) lon = parseFloat(val);
  });
  return (lat != null && lon != null) ? { lat, lon } : null;
}

function mapaFocusSite() {
  mapaInit();
  const center = mapaCenterFromSession() || MAPA.result?.center;
  if (!center) {
    toast('Carga un sitio en Data Hub primero.', 'warning');
    return;
  }
  MAPA.map.setView([center.lat, center.lon], 9);
}

function mapaBoundsPayload() {
  if (!MAPA.areaBounds) return null;
  const sw = MAPA.areaBounds.getSouthWest();
  const ne = MAPA.areaBounds.getNorthEast();
  return {
    lat_min: sw.lat, lat_max: ne.lat,
    lon_min: sw.lng, lon_max: ne.lng,
  };
}

function mapaSetStatus(msg) {
  const el = document.getElementById('mapa-status');
  if (el) el.textContent = msg;
}

async function mapaBuild() {
  if (!WP.sessionId) { toast('Sin sesión.', 'error'); return; }
  mapaInit();
  const center = mapaCenterFromSession();
  if (!center) {
    toast('Carga un sitio en Data Hub primero (necesito lat/lon).', 'error');
    return;
  }
  const body = {
    session_id: WP.sessionId,
    center_lat: center.lat,
    center_lon: center.lon,
    year:       parseInt(document.getElementById('mapa-year').value),
    radius_km:  parseFloat(document.getElementById('mapa-radius').value),
    n_grid:     parseInt(document.getElementById('mapa-n').value),
    model:      document.getElementById('mapa-model').value,
    p_nom_kw:   2000,
    rotor_m:    80,
  };
  const fixedBounds = mapaBoundsPayload();
  if (fixedBounds) body.bounds = fixedBounds;
  mapaSetStatus('Calculando…');
  try {
    const r = await api('/api/grid/build', 'POST', body);
    if (r.error) throw new Error(r.error);
    MAPA.result = r;
    MAPA.sites  = r.sites || [];
    mapaSetStatus(`${r.n_sites} sitios · ${body.n_grid}×${body.n_grid}`);
    mapaRenderVariable(MAPA.var);
    const targetBounds = fixedBounds || r.bounds;
    MAPA.map.fitBounds(
      [[targetBounds.lat_min, targetBounds.lon_min], [targetBounds.lat_max, targetBounds.lon_max]],
      { padding: [25, 25] });
    setTimeout(() => MAPA.map.invalidateSize(), 60);
    toast('Grilla generada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    mapaSetStatus('Error');
  }
}

function mapaRenderVariable(varKey) {
  if (!MAPA.result || !MAPA.map) return;
  MAPA.var = varKey;
  const grid = MAPA.result.grids[varKey];
  if (!grid) return;
  MAPA.grid = grid;

  if (MAPA.gridLayer) MAPA.map.removeLayer(MAPA.gridLayer);
  const { z, bounds, z_min, z_max } = grid;
  const ny = z.length, nx = z[0].length;
  const canvas = document.createElement('canvas');
  canvas.width = nx; canvas.height = ny;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(nx, ny);
  const rng = z_max - z_min;
  for (let j = 0; j < ny; j++) {
    const row = z[ny - 1 - j];
    for (let i = 0; i < nx; i++) {
      const t = rng > 0 ? (row[i] - z_min) / rng : 0.5;
      const [r_, g_, b_] = krigColor(t);
      const idx = (j * nx + i) * 4;
      img.data[idx] = r_; img.data[idx+1] = g_; img.data[idx+2] = b_;
      img.data[idx+3] = 190;
    }
  }
  ctx.putImageData(img, 0, 0);
  const overlay = window.L.imageOverlay(canvas.toDataURL(),
    [[bounds.lat_min, bounds.lon_min], [bounds.lat_max, bounds.lon_max]],
    { opacity: 0.75 });
  const group = window.L.layerGroup([overlay]);

  // Site markers colored by the same variable
  (MAPA.sites || []).forEach(s => {
    const v = Number(s[varKey] ?? 0);
    const t = rng > 0 ? (v - z_min) / rng : 0.5;
    const [r_, g_, b_] = krigColor(t);
    const m = window.L.circleMarker([s.lat, s.lon], {
      radius: 6, color: '#111', weight: 1,
      fillColor: `rgb(${r_|0},${g_|0},${b_|0})`, fillOpacity: 1,
    }).bindPopup(
      `<b>${s.filename}</b><br>` +
      `Lat ${s.lat.toFixed(3)} · Lon ${s.lon.toFixed(3)}<br>` +
      `Vel: <b>${s.speed.toFixed(2)}</b> m/s<br>` +
      `Ráfaga: <b>${(s.gust || 0).toFixed(2)}</b> m/s<br>` +
      `Pd: <b>${s.power.toFixed(0)}</b> W/m²<br>` +
      `KPI: <b>${(s.resource_score || 0).toFixed(1)}</b>/100`);
    group.addLayer(m);
  });

  // Center marker
  if (MAPA.result.center) {
    const c = MAPA.result.center;
    group.addLayer(window.L.circleMarker([c.lat, c.lon], {
      radius: 9, color: '#fff', weight: 2, fillColor: '#facc15', fillOpacity: 1,
    }).bindPopup(`<b>Sitio cargado</b><br>${c.lat.toFixed(3)}, ${c.lon.toFixed(3)}`));
  }

  // Optimal candidate markers
  (MAPA.result.optimal_sites || []).forEach(s => {
    if (MAPA.parkPoints.length >= 3 && !mapaPointInPolygon([s.lat, s.lon], MAPA.parkPoints)) return;
    const radius = Math.max(7, 13 - (s.rank || 6));
    group.addLayer(window.L.circleMarker([s.lat, s.lon], {
      radius, color: '#0f172a', weight: 2,
      fillColor: '#22c55e', fillOpacity: 0.95,
    }).bindPopup(
      `<b>#${s.rank} sitio candidato</b><br>${s.filename}<br>` +
      `Score: <b>${s.score.toFixed(1)}</b>/100<br>` +
      `Vel: ${s.speed.toFixed(2)} m/s · Ráfaga: ${s.gust.toFixed(2)} m/s<br>` +
      `Densidad: ${s.power.toFixed(0)} W/m²`));
  });

  group.addTo(MAPA.map);
  MAPA.gridLayer = group;

  document.getElementById('mapa-title').textContent = grid.title;
  document.getElementById('mapa-legend').textContent =
    `${z_min.toFixed(2)} → ${z_max.toFixed(2)} ${grid.unit}`;
  const lbls = document.getElementById('mapa-cbar-lbl');
  lbls.innerHTML =
    `<span>${z_min.toFixed(2)}</span>` +
    `<span>${((z_min+z_max)/2).toFixed(2)}</span>` +
    `<span>${z_max.toFixed(2)} ${grid.unit}</span>`;

  const vals = MAPA.sites.map(s => s[varKey]);
  const mean = vals.reduce((a,b)=>a+b,0) / (vals.length || 1);
  document.getElementById('mapa-stats').innerHTML =
    `<b>Sitios:</b> ${MAPA.sites.length}<br>` +
    `<b>Min:</b> ${Math.min(...vals).toFixed(2)} ${grid.unit}<br>` +
    `<b>Max:</b> ${Math.max(...vals).toFixed(2)} ${grid.unit}<br>` +
    `<b>Media:</b> ${mean.toFixed(2)} ${grid.unit}<br>` +
    `<b>Modelo:</b> ${grid.model}<br>` +
    `<b>Área fija:</b> ${MAPA.areaBounds ? 'sí' : 'no'}`;
  mapaRenderOptimalList();
}

function mapaHandleClick(e) {
  if (MAPA.drawingPark) {
    mapaAddParkPoint(e.latlng);
    return;
  }
  mapaOnClick(e);
}

function mapaOnClick(e) {
  if (!MAPA.grid) return;
  const { z, bounds } = MAPA.grid;
  const ny = z.length, nx = z[0].length;
  const dy = (bounds.lat_max - bounds.lat_min) / (ny - 1);
  const dx = (bounds.lon_max - bounds.lon_min) / (nx - 1);
  const j = Math.round((e.latlng.lat - bounds.lat_min) / dy);
  const i = Math.round((e.latlng.lng - bounds.lon_min) / dx);
  if (j < 0 || j >= ny || i < 0 || i >= nx) return;
  const v = z[j][i];
  document.getElementById('mapa-pt').innerHTML =
    `<b>Click:</b> ${e.latlng.lat.toFixed(3)}, ${e.latlng.lng.toFixed(3)}<br>` +
    `<b>Celda (${i},${j}):</b> ${v.toFixed(2)} ${MAPA.grid.unit}`;
}

function mapaBeginAreaSelection() {
  mapaInit();
  MAPA.selectingArea = true;
  mapaSetStatus('Dibuja rectángulo');
  toast('Arrastra sobre el mapa para fijar el área de estudio.', 'info');
}

function mapaAreaMouseDown(e) {
  if (!MAPA.selectingArea) return;
  MAPA.areaStart = e.latlng;
  MAPA.map.dragging.disable();
  e.originalEvent.preventDefault();
}

function mapaAreaMouseMove(e) {
  if (!MAPA.selectingArea || !MAPA.areaStart) return;
  if (MAPA.areaDraft) MAPA.map.removeLayer(MAPA.areaDraft);
  MAPA.areaDraft = window.L.rectangle([MAPA.areaStart, e.latlng], {
    color: '#22c55e', weight: 2, fillOpacity: 0.12, dashArray: '6 4',
  }).addTo(MAPA.map);
}

function mapaAreaMouseUp(e) {
  if (!MAPA.selectingArea || !MAPA.areaStart) return;
  const bounds = window.L.latLngBounds(MAPA.areaStart, e.latlng);
  MAPA.selectingArea = false;
  MAPA.areaStart = null;
  MAPA.map.dragging.enable();
  if (MAPA.areaDraft) {
    MAPA.map.removeLayer(MAPA.areaDraft);
    MAPA.areaDraft = null;
  }
  if (MAPA.areaLayer) MAPA.map.removeLayer(MAPA.areaLayer);
  MAPA.areaBounds = bounds;
  MAPA.areaLayer = window.L.rectangle(bounds, {
    color: '#22c55e', weight: 2, fillOpacity: 0.08,
  }).addTo(MAPA.map);
  MAPA.map.fitBounds(bounds, { padding: [24, 24] });
  mapaSetStatus('Área fija');
  toast('Área de estudio fijada. Puedes salir del encuadre y volver a generar.', 'success');
}

function mapaClearArea() {
  if (MAPA.areaLayer && MAPA.map) MAPA.map.removeLayer(MAPA.areaLayer);
  if (MAPA.areaDraft && MAPA.map) MAPA.map.removeLayer(MAPA.areaDraft);
  MAPA.areaLayer = null;
  MAPA.areaDraft = null;
  MAPA.areaBounds = null;
  MAPA.selectingArea = false;
  mapaSetStatus(MAPA.result ? `${MAPA.result.n_sites} sitios` : '—');
}

function mapaAddParkPoint(latlng) {
  MAPA.parkPoints.push([latlng.lat, latlng.lng]);
  const marker = window.L.circleMarker(latlng, {
    radius: 4, color: '#fff', weight: 1, fillColor: '#f59e0b', fillOpacity: 1,
  }).addTo(MAPA.map);
  MAPA.parkMarkers.push(marker);
  mapaRenderParkLayer(false);
}

function mapaRenderParkLayer(closed=true) {
  if (MAPA.parkLayer && MAPA.map) MAPA.map.removeLayer(MAPA.parkLayer);
  if (MAPA.parkPoints.length < 2) {
    mapaUpdateParkInfo();
    return;
  }
  MAPA.parkLayer = closed && MAPA.parkPoints.length >= 3
    ? window.L.polygon(MAPA.parkPoints, { color: '#f59e0b', weight: 2, fillOpacity: 0.14 }).addTo(MAPA.map)
    : window.L.polyline(MAPA.parkPoints, { color: '#f59e0b', weight: 2, dashArray: '4 4' }).addTo(MAPA.map);
  mapaUpdateParkInfo();
}

function mapaStartParkDraw() {
  mapaInit();
  MAPA.drawingPark = true;
  toast('Haz clic para agregar vértices del parque; cierra la figura al terminar.', 'info');
}

function mapaFinishPark() {
  MAPA.drawingPark = false;
  if (MAPA.parkPoints.length < 3) {
    toast('Necesitas al menos 3 puntos para cerrar la figura.', 'warning');
    return;
  }
  mapaRenderParkLayer(true);
  mapaRenderVariable(MAPA.var);
}

function mapaClearPark() {
  MAPA.drawingPark = false;
  if (MAPA.parkLayer && MAPA.map) MAPA.map.removeLayer(MAPA.parkLayer);
  MAPA.parkMarkers.forEach(m => MAPA.map && MAPA.map.removeLayer(m));
  MAPA.parkLayer = null;
  MAPA.parkMarkers = [];
  MAPA.parkPoints = [];
  mapaUpdateParkInfo();
  mapaRenderVariable(MAPA.var);
}

function mapaPolygonAreaKm2(points) {
  if (points.length < 3) return 0;
  const lat0 = points.reduce((s, p) => s + p[0], 0) / points.length;
  const coords = points.map(([lat, lon]) => {
    const x = lon * 111.32 * Math.cos(lat0 * Math.PI / 180);
    const y = lat * 110.57;
    return [x, y];
  });
  let area = 0;
  for (let i = 0; i < coords.length; i++) {
    const [x1, y1] = coords[i];
    const [x2, y2] = coords[(i + 1) % coords.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area) / 2;
}

function mapaPointInPolygon(point, polygon) {
  const [lat, lon] = point;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [lati, loni] = polygon[i];
    const [latj, lonj] = polygon[j];
    const cross = ((loni > lon) !== (lonj > lon)) &&
      (lat < (latj - lati) * (lon - loni) / ((lonj - loni) || 1e-12) + lati);
    if (cross) inside = !inside;
  }
  return inside;
}

function mapaUpdateParkInfo() {
  const el = document.getElementById('mapa-park-info');
  if (!el) return;
  if (MAPA.parkPoints.length < 3) {
    el.textContent = MAPA.parkPoints.length
      ? `${MAPA.parkPoints.length} vértices definidos. Falta cerrar la figura.`
      : 'Dibuja una figura sobre Leaflet para delimitar la zona del parque.';
    return;
  }
  const area = mapaPolygonAreaKm2(MAPA.parkPoints);
  el.innerHTML = `<b>Zona de parque:</b> ${area.toFixed(2)} km² · ${MAPA.parkPoints.length} vértices`;
}

function mapaRenderOptimalList() {
  const el = document.getElementById('mapa-optimal-list');
  if (!el) return;
  let sites = (MAPA.result?.optimal_sites || []);
  if (MAPA.parkPoints.length >= 3) {
    sites = sites.filter(s => mapaPointInPolygon([s.lat, s.lon], MAPA.parkPoints));
  }
  if (!sites.length) {
    el.textContent = MAPA.result ? 'Sin candidatos dentro del área actual.' : 'Genera la grilla para calcular candidatos.';
    return;
  }
  el.innerHTML = sites.slice(0, 8).map(s => (
    `<div style="padding:.35rem 0;border-bottom:1px solid var(--border)">` +
    `<b>#${s.rank}</b> ${s.filename}<br>` +
    `Score ${s.score.toFixed(1)} · Vel ${s.speed.toFixed(2)} m/s · ` +
    `Ráfaga ${s.gust.toFixed(2)} m/s · Pd ${s.power.toFixed(0)} W/m²` +
    `</div>`
  )).join('');
}

function mapaExportGeoJSON() {
  if (!MAPA.result) { toast('Genera la grilla primero.', 'error'); return; }
  const fc = {
    type: 'FeatureCollection',
    features: MAPA.sites.map(s => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
      properties: {
        filename: s.filename, speed_ms: s.speed, power_density_wm2: s.power,
        gust_ms: s.gust, resource_score: s.resource_score,
        aep_mwh: s.aep, cf_pct: s.cf, weibull_C: s.weibull_C, weibull_K: s.weibull_K,
      },
    })).concat((MAPA.result.optimal_sites || []).map(s => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
      properties: { kind: 'optimal_site', rank: s.rank, score: s.score, filename: s.filename },
    }))),
  };
  if (MAPA.areaBounds) {
    const b = mapaBoundsPayload();
    fc.features.push({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [[
        [b.lon_min, b.lat_min], [b.lon_max, b.lat_min],
        [b.lon_max, b.lat_max], [b.lon_min, b.lat_max],
        [b.lon_min, b.lat_min],
      ]] },
      properties: { kind: 'fixed_area' },
    });
  }
  if (MAPA.parkPoints.length >= 3) {
    fc.features.push({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [[
        ...MAPA.parkPoints.map(([lat, lon]) => [lon, lat]),
        [MAPA.parkPoints[0][1], MAPA.parkPoints[0][0]],
      ]] },
      properties: { kind: 'park_zone', area_km2: mapaPolygonAreaKm2(MAPA.parkPoints) },
    });
  }
  const blob = new Blob([JSON.stringify(fc, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `aero_resource_grid_${Date.now()}.geojson`;
  a.click();
  toast('GeoJSON exportado', 'success');
}

document.addEventListener('DOMContentLoaded', () => {
  const baseSel = document.getElementById('mapa-baselayer');
  if (baseSel) baseSel.addEventListener('change', () => mapaSetBase(baseSel.value));
  const varSel = document.getElementById('mapa-var');
  if (varSel) varSel.addEventListener('change', () => mapaRenderVariable(varSel.value));
  const btnBuild = document.getElementById('btn-mapa-build');
  if (btnBuild) btnBuild.addEventListener('click', mapaBuild);
  const btnExport = document.getElementById('btn-mapa-export');
  if (btnExport) btnExport.addEventListener('click', mapaExportGeoJSON);
  document.getElementById('btn-mapa-mexico')?.addEventListener('click', () => mapaSetExtent('mexico'));
  document.getElementById('btn-mapa-world')?.addEventListener('click', () => mapaSetExtent('world'));
  document.getElementById('btn-mapa-site')?.addEventListener('click', mapaFocusSite);
  document.getElementById('btn-mapa-select-area')?.addEventListener('click', mapaBeginAreaSelection);
  document.getElementById('btn-mapa-clear-area')?.addEventListener('click', mapaClearArea);
  document.getElementById('btn-mapa-draw-park')?.addEventListener('click', mapaStartParkDraw);
  document.getElementById('btn-mapa-finish-park')?.addEventListener('click', mapaFinishPark);
  document.getElementById('btn-mapa-clear-park')?.addEventListener('click', mapaClearPark);
  document.querySelectorAll('.nav-btn[data-module="mapa"]').forEach(b => {
    b.addEventListener('click', () => {
      requestAnimationFrame(() => {
        mapaInit();
        const c = mapaCenterFromSession();
        if (c && !MAPA.result) MAPA.map.setView([c.lat, c.lon], 7);
        if (MAPA.map) MAPA.map.invalidateSize();
        setTimeout(() => MAPA.map && MAPA.map.invalidateSize(), 200);
      });
    });
  });
  document.querySelectorAll('[data-model]').forEach(tag => {
    const key = tag.dataset.model;
    const color = MODEL_COLORS[key];
    if (color) {
      tag.style.setProperty('--tag-color', color);
    }
  });
});

// ═════════════════════════ DEMAND & PML ARENA ═════════════════════════════
const MODEL_COLORS = {
  'linear':         '#3b82f6', // blue
  'polynomial':     '#8b5cf6', // purple
  'exponential':    '#ec4899', // pink
  'logarithmic':    '#0ea5e9', // light blue
  'logistic':       '#14b8a6', // teal
  'mean_reversion': '#f59e0b', // amber
  'arima':          '#eab308', // yellow
  'holt_winters':   '#f97316', // orange
  'random_forest':  '#10b981', // emerald
  'gradient_boost': '#06b6d4', // cyan
  'mlp':            '#6366f1', // indigo
  'ridge':          '#84cc16', // lime
  'svr':            '#d946ef'  // fuchsia
};

const FC_PALETTE = [
  '#4DA3FF', '#9F7AEA', '#3DDC97', '#F4C95D',
  '#F6A156', '#E5707A', '#7FB3D5', '#9CCC65',
];

function fcGetSelectedModels(containerId) {
  return [...document.querySelectorAll(`#${containerId} [data-model].active`)]
    .map(t => t.dataset.model);
}

function fcKpiCard(label, value, sub) {
  return `<div class="kpi-card">
    <div class="kpi-label">${label}</div>
    <div class="kpi-value">${value}</div>
    ${sub ? `<div class="kpi-sub">${sub}</div>` : ''}
  </div>`;
}

function fcFormatNumber(x, digits = 2) {
  if (x == null || !isFinite(x)) return '—';
  if (Math.abs(x) >= 1000) return x.toLocaleString('es-MX', { maximumFractionDigits: 0 });
  return x.toLocaleString('es-MX', { maximumFractionDigits: digits });
}

function fcPct(x, digits = 2) {
  if (x == null || !isFinite(x)) return '—';
  return (x * 100).toFixed(digits) + '%';
}

// ─── DEMAND ARENA ─────────────────────────────────────────────────────────────
function bindDemandArena() {
  const tagBox = document.getElementById('dem-model-tags');
  if (tagBox) {
    tagBox.addEventListener('click', e => {
      const tag = e.target.closest('[data-model]');
      if (tag) tag.classList.toggle('active');
    });
  }
  const btn = document.getElementById('btn-dem-train');
  if (btn) btn.addEventListener('click', runDemandArena);
  
  const btnHourly = document.getElementById('btn-dem-train-hourly');
  if (btnHourly) btnHourly.addEventListener('click', runDemandArenaHourly);
}

async function runDemandArena() {
  const preset = document.getElementById('dem-preset').value;
  const growth = parseFloat(document.getElementById('dem-growth').value || '0.025');
  const horizon = Math.max(25, parseInt(document.getElementById('dem-horizon').value || '25'));
  const models = fcGetSelectedModels('dem-model-tags');
  if (!models.length) { toast('Selecciona al menos un modelo', 'warn'); return; }

  const btn = document.getElementById('btn-dem-train');
  btn.disabled = true; btn.textContent = 'Cargando y ajustando…';
  try {
    const baseInfo = await api('/api/demand/load', 'POST', {
      session_id: WP.sessionId, preset
    });
    renderDemandKPIs(baseInfo);
    renderDemandProfile(baseInfo.stats);
    renderDemandDuration(baseInfo.stats);

    const fc = await api('/api/demand/forecast', 'POST', {
      session_id: WP.sessionId, preset, growth, horizon, models
    });
    WP.arenaCache.demand = fc;
    renderForecastFutureChart('demand');
    renderForecastFutureChart('demand_hourly');
    renderDemandProjection(fc, baseInfo.meta);
    // Predicción continua (histórico + proyección)
    const baseYear = new Date().getFullYear();
    const histYearsAbs = (fc.history_t || []).map(t => baseYear - (fc.history_t.length - 1) + t);
    renderArenaPrediction({
      canvasId: 'dem-prediction-chart', badgeId: 'dem-prediction-badge',
      history: { years: histYearsAbs, annual: (fc.history_annual || []).map(v => v / 1000) },
      projection: Object.fromEntries(Object.entries(fc.projection || {}).map(([k, v]) => [k, {
        ...v, annual: (v.annual || []).map(x => x / 1000),
        ci_low:  (v.ci_low  || []).map(x => x / 1000),
        ci_high: (v.ci_high || []).map(x => x / 1000),
      }])),
      bestModel: fc.best_model, ylabel: 'Energía anual (GWh)', decimals: 1, unitLabel: 'GWh',
    });
    renderDemandTable(fc);
    setStatus(`Demanda proyectada — modelo recomendado: ${fc.best_model}`);
    toast('Pronóstico de demanda completado', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Cargar y entrenar arena (Anual)';
  }
}

async function runDemandArenaHourly() {
  const preset = document.getElementById('dem-preset').value;
  const models = fcGetSelectedModels('dem-model-tags');
  if (!models.length) { toast('Selecciona al menos un modelo', 'warn'); return; }

  const btn = document.getElementById('btn-dem-train-hourly');
  btn.disabled = true; btn.textContent = 'Ajustando perfil horario… (aprox 30s)';
  try {
    const baseInfo = await api('/api/demand/load', 'POST', {
      session_id: WP.sessionId, preset
    });
    renderDemandKPIs(baseInfo);
    renderDemandProfile(baseInfo.stats);
    renderDemandDuration(baseInfo.stats);

    const fc = await api('/api/demand/forecast_hourly', 'POST', {
      session_id: WP.sessionId, preset, models
    });
    WP.arenaCache.demand_hourly = fc;

    const growth = parseFloat(document.getElementById('dem-growth').value || '0.025');
    const horizon = Math.max(25, parseInt(document.getElementById('dem-horizon').value || '25'));
    const annualFc = await api('/api/demand/forecast', 'POST', {
      session_id: WP.sessionId, preset, growth, horizon, models
    });
    WP.arenaCache.demand = annualFc;
    renderForecastFutureChart('demand');
    renderForecastFutureChart('demand_hourly');
    toast('Perfil horario ajustado. Ve al Forecast Studio.', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Entrenar Perfil Horario (8760 pts)';
  }
}

function renderDemandKPIs(info) {
  const s = info.stats || {};
  const unit = info.meta?.unit || '';
  const html = [
    fcKpiCard('Horas observadas', fcFormatNumber(s.n_hours), 'serie horaria'),
    fcKpiCard('Demanda media', `${fcFormatNumber(s.mean)} ${unit}`),
    fcKpiCard('Pico', `${fcFormatNumber(s.max)} ${unit}`),
    fcKpiCard('Factor de carga', s.load_factor != null ? (s.load_factor * 100).toFixed(1) + '%' : '—'),
    fcKpiCard('Energía anual', `${fcFormatNumber(s.annual_sum / 1000, 1)} GWh`, 'sum × 1 h'),
    fcKpiCard('Desviación', `${fcFormatNumber(s.std)} ${unit}`),
  ].join('');
  document.getElementById('dem-kpis').innerHTML = html;
}

function renderDemandProfile(stats) {
  destroyChart('dem-profile-chart');
  const ctx = document.getElementById('dem-profile-chart').getContext('2d');
  WP.charts['dem-profile-chart'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: Array.from({ length: 24 }, (_, i) => `${i}h`),
      datasets: [{
        label: 'Demanda media diurna', data: stats.diurnal,
        borderColor: acc(), backgroundColor: acc() + '22',
        fill: true, tension: 0.35, borderWidth: 2, pointRadius: 0,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true } },
      scales: { x: { grid: { color: gridColor() } }, y: { grid: { color: gridColor() } } }
    }
  });
}

function renderDemandDuration(stats) {
  destroyChart('dem-duration-chart');
  const ctx = document.getElementById('dem-duration-chart').getContext('2d');
  const data = stats.duration_curve || [];
  const step = Math.max(1, Math.floor(data.length / 200));
  const sub = data.filter((_, i) => i % step === 0);
  WP.charts['dem-duration-chart'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: sub.map((_, i) => ((i * step) / data.length * 100).toFixed(0) + '%'),
      datasets: [{
        label: 'Curva de duración', data: sub,
        borderColor: acc3(), backgroundColor: acc3() + '22',
        fill: true, tension: 0, borderWidth: 1.5, pointRadius: 0,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { grid: { color: gridColor() } }, y: { grid: { color: gridColor() } } }
    }
  });
}

function renderDemandProjection(fc, meta) {
  destroyChart('dem-projection-chart');
  const ctx = document.getElementById('dem-projection-chart').getContext('2d');
  const proj = fc.projection || {};
  const keys = Object.keys(proj);
  if (!keys.length) return;

  const sample = proj[keys[0]];
  const labels = sample.years;
  const datasets = keys.map((k, i) => ({
    label: `${k} (CAGR ${fcPct(proj[k].cagr)})`,
    data: proj[k].annual.map(v => v / 1000),  // GWh
    borderColor: FC_PALETTE[i % FC_PALETTE.length],
    backgroundColor: FC_PALETTE[i % FC_PALETTE.length] + '22',
    borderWidth: k === fc.best_model ? 2.6 : 1.3,
    borderDash: k === fc.best_model ? [] : [4, 3],
    fill: false, tension: 0.25, pointRadius: 0,
  }));

  // banda IC del mejor modelo
  if (fc.best_model && proj[fc.best_model]) {
    const b = proj[fc.best_model];
    datasets.push({
      label: 'IC 95 % (mejor modelo)',
      data: b.ci_high.map(v => v / 1000),
      borderColor: 'transparent', backgroundColor: acc() + '20',
      fill: '+1', pointRadius: 0, borderWidth: 0,
    });
    datasets.push({
      label: '_lower', data: b.ci_low.map(v => v / 1000),
      borderColor: 'transparent', backgroundColor: 'transparent',
      pointRadius: 0, borderWidth: 0, fill: false,
    });
  }

  WP.charts['dem-projection-chart'] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { display: true, labels: { filter: it => it.text !== '_lower' } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fcFormatNumber(ctx.parsed.y, 1)} GWh` } },
      },
      scales: {
        x: { grid: { color: gridColor() }, title: { display: true, text: 'Año' } },
        y: { grid: { color: gridColor() }, title: { display: true, text: 'Energía anual (GWh)' } }
      }
    }
  });

  const best = fc.best_model;
  if (best && proj[best]) {
    const b = proj[best];
    document.getElementById('dem-best-badge').textContent =
      `Best: ${best} · CAGR ${fcPct(b.cagr)} · Año final ${fcFormatNumber(b.annual.at(-1) / 1000, 1)} GWh`;
  }
}

function renderDemandTable(fc) {
  const tbody = document.querySelector('#dem-compare-tbl tbody');
  const rows = (fc.summary || []).filter(r => !r.error);
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-msg">Sin resultados.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((r, i) => {
    const isBest = r.model === fc.best_model;
    return `<tr class="${isBest ? 'best-row' : ''}">
      <td>${isBest ? '★ ' : ''}${r.model} <span class="text-muted" style="font-size:.7rem">${r.kind || ''}</span></td>
      <td>${r.r2 != null ? r.r2.toFixed(4) : '—'}</td>
      <td>${fcFormatNumber(r.rmse, 1)}</td>
      <td>${r.mape != null ? r.mape.toFixed(2) + '%' : '—'}</td>
      <td>${fcPct(r.cagr)}</td>
      <td>${fcFormatNumber((r.final || 0) / 1000, 1)} GWh</td>
    </tr>`;
  }).join('');
}

// ─── PML ARENA ────────────────────────────────────────────────────────────────
function bindPMLArena() {
  const tagBox = document.getElementById('pml-model-tags');
  if (tagBox) {
    tagBox.addEventListener('click', e => {
      const tag = e.target.closest('[data-model]');
      if (tag) tag.classList.toggle('active');
    });
  }
  const btn = document.getElementById('btn-pml-train');
  if (btn) btn.addEventListener('click', runPMLArena);
  
  const btnHourly = document.getElementById('btn-pml-train-hourly');
  if (btnHourly) btnHourly.addEventListener('click', runPmlArenaHourly);

  const sideSelect = document.getElementById('pml-side');
  if (sideSelect) {
    sideSelect.addEventListener('change', () => {
      if (WP.arenaCache.pml) populateForecastStudioSelect('pml');
      if (WP.arenaCache.pml_hourly) populateForecastStudioSelect('pml_hourly');
      renderForecastFutureChart('pml');
      renderForecastFutureChart('pml_hourly');
    });
  }
}

async function runPMLArena() {
  const preset = document.getElementById('pml-preset').value;
  const side = document.getElementById('pml-side').value;
  const growth = parseFloat(document.getElementById('pml-growth').value || '0.030');
  const horizon = Math.max(25, parseInt(document.getElementById('pml-horizon').value || '25'));
  const models = fcGetSelectedModels('pml-model-tags');
  if (!models.length) { toast('Selecciona al menos un modelo', 'warn'); return; }

  const btn = document.getElementById('btn-pml-train');
  btn.disabled = true; btn.textContent = 'Cargando y ajustando…';
  try {
    const baseInfo = await api('/api/pml/load', 'POST', {
      session_id: WP.sessionId, preset
    });
    renderPMLKPIs(baseInfo, side);
    renderPMLDiurnal(baseInfo);
    renderPMLDuration(baseInfo, side === 'compra' ? 'compra' : 'venta');

    const fc = await api('/api/pml/forecast', 'POST', {
      session_id: WP.sessionId, preset, growth, horizon, side, models
    });
    WP.arenaCache.pml = fc;
    renderForecastFutureChart('pml');
    renderForecastFutureChart('pml_hourly');
    renderPMLProjection(fc);
    // Predicción continua: tomamos el primer lado disponible (o venta por defecto)
    const sideKey = (fc.sides && fc.sides.venta) ? 'venta' : Object.keys(fc.sides || {})[0];
    if (sideKey && fc.sides[sideKey]) {
      const sd = fc.sides[sideKey];
      const baseYear = new Date().getFullYear();
      const histYearsAbs = (sd.history_t || []).map(t => baseYear - (sd.history_t.length - 1) + t);
      renderArenaPrediction({
        canvasId: 'pml-prediction-chart', badgeId: 'pml-prediction-badge',
        history: { years: histYearsAbs, annual: sd.history_annual || [] },
        projection: sd.projection, bestModel: sd.best_model,
        ylabel: `PML anual ($/kWh) — ${sideKey}`, decimals: 4, unitLabel: '$/kWh',
      });
    }
    renderPMLTable(fc);
    setStatus(`PML proyectado — lados: ${Object.keys(fc.sides).join(', ')}`);
    toast('Pronóstico de PML completado', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Cargar y entrenar arena (Anual)';
  }
}

async function runPmlArenaHourly() {
  const preset = document.getElementById('pml-preset').value;
  const side = document.getElementById('pml-side').value;
  const models = fcGetSelectedModels('pml-model-tags');
  if (!models.length) { toast('Selecciona al menos un modelo', 'warn'); return; }

  const btn = document.getElementById('btn-pml-train-hourly');
  btn.disabled = true; btn.textContent = 'Ajustando perfil horario… (aprox 30s)';
  try {
    const baseInfo = await api('/api/pml/load', 'POST', {
      session_id: WP.sessionId, preset
    });
    renderPMLKPIs(baseInfo, side);
    renderPMLDiurnal(baseInfo);
    renderPMLDuration(baseInfo, side === 'compra' ? 'compra' : 'venta');

    const fc = await api('/api/pml/forecast_hourly', 'POST', {
      session_id: WP.sessionId, preset, models
    });
    WP.arenaCache.pml_hourly = fc;

    const growth = parseFloat(document.getElementById('pml-growth').value || '0.030');
    const horizon = Math.max(25, parseInt(document.getElementById('pml-horizon').value || '25'));
    const annualFc = await api('/api/pml/forecast', 'POST', {
      session_id: WP.sessionId, preset, growth, horizon, side, models
    });
    WP.arenaCache.pml = annualFc;
    renderForecastFutureChart('pml');
    renderForecastFutureChart('pml_hourly');
    toast('Perfil horario de PML ajustado. Ve al Forecast Studio.', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Entrenar Perfil Horario (8760 pts)';
  }
}

function renderPMLKPIs(info, side) {
  const s = side === 'compra' ? info.stats_compra : info.stats_venta;
  const unit = info.meta?.unit || '$/kWh';
  const html = [
    fcKpiCard('Horas observadas', fcFormatNumber(s.n_hours)),
    fcKpiCard(`Precio medio (${side})`, `${fcFormatNumber(s.mean, 4)} ${unit}`),
    fcKpiCard('P10 / P90', `${fcFormatNumber(s.p10, 4)} / ${fcFormatNumber(s.p90, 4)}`, unit),
    fcKpiCard('Mín / Máx', `${fcFormatNumber(s.min, 4)} / ${fcFormatNumber(s.max, 4)}`),
    fcKpiCard('Volatilidad σ', fcFormatNumber(s.std, 4)),
    fcKpiCard('Ingreso/MWh medio', `${fcFormatNumber(s.mean * 1000, 2)} $/MWh`),
  ].join('');
  document.getElementById('pml-kpis').innerHTML = html;
}

function renderPMLDiurnal(info) {
  destroyChart('pml-diurnal-chart');
  const ctx = document.getElementById('pml-diurnal-chart').getContext('2d');
  WP.charts['pml-diurnal-chart'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: Array.from({ length: 24 }, (_, i) => `${i}h`),
      datasets: [
        { label: 'Compra', data: info.stats_compra.diurnal,
          borderColor: acc2(), backgroundColor: acc2() + '20',
          fill: true, tension: 0.35, borderWidth: 1.8, pointRadius: 0 },
        { label: 'Venta', data: info.stats_venta.diurnal,
          borderColor: acc(), backgroundColor: acc() + '20',
          fill: false, tension: 0.35, borderWidth: 2.2, pointRadius: 0 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true } },
      scales: { x: { grid: { color: gridColor() } }, y: { grid: { color: gridColor() } } }
    }
  });
}

function renderPMLDuration(info, sideKey) {
  destroyChart('pml-duration-chart');
  const ctx = document.getElementById('pml-duration-chart').getContext('2d');
  const stats = sideKey === 'compra' ? info.stats_compra : info.stats_venta;
  const data = stats.duration_curve || [];
  const step = Math.max(1, Math.floor(data.length / 200));
  const sub = data.filter((_, i) => i % step === 0);
  WP.charts['pml-duration-chart'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: sub.map((_, i) => ((i * step) / data.length * 100).toFixed(0) + '%'),
      datasets: [{
        label: `Curva de duración — ${sideKey}`, data: sub,
        borderColor: gold(), backgroundColor: gold() + '22',
        fill: true, tension: 0, borderWidth: 1.5, pointRadius: 0,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { grid: { color: gridColor() } }, y: { grid: { color: gridColor() } } }
    }
  });
}

function renderPMLProjection(fc) {
  destroyChart('pml-projection-chart');
  const ctx = document.getElementById('pml-projection-chart').getContext('2d');
  const sides = fc.sides || {};
  const sideKeys = Object.keys(sides);
  if (!sideKeys.length) return;

  let labels = null;
  const datasets = [];
  sideKeys.forEach((s, si) => {
    const sideData = sides[s];
    const proj = sideData.projection || {};
    const best = sideData.best_model;
    Object.keys(proj).forEach((k, i) => {
      const p = proj[k];
      labels = labels || p.years;
      const isBest = (k === best);
      datasets.push({
        label: `${s} · ${k}${isBest ? ' ★' : ''}`,
        data: p.annual,
        borderColor: FC_PALETTE[(i + si * 3) % FC_PALETTE.length],
        backgroundColor: FC_PALETTE[(i + si * 3) % FC_PALETTE.length] + '20',
        borderWidth: isBest ? 2.6 : 1.0,
        borderDash: isBest ? [] : [4, 3],
        fill: false, tension: 0.25, pointRadius: 0,
      });
    });
    if (best && proj[best]) {
      const b = proj[best];
      datasets.push({
        label: `IC 95 % · ${s}`, data: b.ci_high,
        borderColor: 'transparent',
        backgroundColor: (s === 'venta' ? acc() : acc2()) + '18',
        fill: '+1', pointRadius: 0, borderWidth: 0,
      });
      datasets.push({
        label: '_lower', data: b.ci_low,
        borderColor: 'transparent', backgroundColor: 'transparent',
        pointRadius: 0, borderWidth: 0, fill: false,
      });
    }
  });

  WP.charts['pml-projection-chart'] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { display: true, labels: { filter: it => it.text !== '_lower' } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fcFormatNumber(ctx.parsed.y, 4)} $/kWh` } },
      },
      scales: {
        x: { grid: { color: gridColor() }, title: { display: true, text: 'Año' } },
        y: { grid: { color: gridColor() }, title: { display: true, text: 'PML anual ($/kWh)' } }
      }
    }
  });

  const fragments = sideKeys.map(s => {
    const b = sides[s].best_model;
    const last = sides[s].projection?.[b]?.annual?.at(-1);
    const cagr = sides[s].projection?.[b]?.cagr;
    return `${s}: ${b} · CAGR ${fcPct(cagr)} · Año final ${fcFormatNumber(last, 4)} $/kWh`;
  });
  document.getElementById('pml-best-badge').textContent = fragments.join('   ·   ');
}

function renderPMLTable(fc) {
  const tbody = document.querySelector('#pml-compare-tbl tbody');
  const sides = fc.sides || {};
  const rows = [];
  Object.keys(sides).forEach(s => {
    const best = sides[s].best_model;
    (sides[s].summary || []).filter(r => !r.error).forEach(r => {
      rows.push({ ...r, side: s, isBest: r.model === best });
    });
  });
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">Sin resultados.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => `
    <tr class="${r.isBest ? 'best-row' : ''}">
      <td>${r.isBest ? '★ ' : ''}${r.model} <span class="text-muted" style="font-size:.7rem">${r.kind || ''}</span></td>
      <td>${r.side}</td>
      <td>${r.r2 != null ? r.r2.toFixed(4) : '—'}</td>
      <td>${fcFormatNumber(r.rmse, 4)}</td>
      <td>${r.mape != null ? r.mape.toFixed(2) + '%' : '—'}</td>
      <td>${fcPct(r.cagr)}</td>
      <td>${fcFormatNumber(r.final, 4)} $/kWh</td>
    </tr>`).join('');
}

// ─── TASA DE INTERÉS ARENA ───────────────────────────────────────────────────
function bindInterestArena() {
  const tagBox = document.getElementById('interest-model-tags');
  if (tagBox) {
    tagBox.addEventListener('click', e => {
      const tag = e.target.closest('[data-model]');
      if (tag) tag.classList.toggle('active');
    });
  }
  const btn = document.getElementById('btn-interest-train');
  if (btn) btn.addEventListener('click', runInterestArena);
}

async function runInterestArena() {
  const preset = document.getElementById('interest-preset').value;
  const horizon = Math.max(25, parseInt(document.getElementById('interest-horizon').value || '25'));
  const models = fcGetSelectedModels('interest-model-tags');
  if (!models.length) { toast('Selecciona al menos un modelo', 'warn'); return; }

  const btn = document.getElementById('btn-interest-train');
  btn.disabled = true; btn.textContent = 'Cargando y ajustando…';
  try {
    const fc = await api('/api/interest/forecast', 'POST', {
      session_id: WP.sessionId, preset, horizon, models
    });
    WP.arenaCache.interest = fc;
    renderForecastFutureChart('interest');
    
    // KPIs (Año Base)
    renderInterestKPIs(fc);
    
    // Proyección Chart (continua: histórico + futuro)
    renderInterestProjection(fc);

    // Proyección 25 años (solo futuro)
    renderArena25Years({
      canvasId: 'interest-25y-chart', badgeId: 'interest-25y-badge',
      projection: fc.projection, bestModel: fc.best_model,
      ylabel: 'Tasa (%)', decimals: 2, unitLabel: '%',
    });

    // Tabla Comparativa
    renderInterestCompareTable(fc);

    document.getElementById('interest-best-badge').textContent = `★ Mejor: ${fc.best_model}`;
    toast('Arena de Tasa de Interés entrenada con éxito');
  } catch (err) {
    console.error(err);
    toast(err.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Cargar y entrenar arena';
  }
}

function renderInterestKPIs(fc) {
  const kpiBox = document.getElementById('interest-kpis');
  if (!fc.history_annual || !fc.history_annual.length) return;
  const vals = fc.history_annual;
  const first = vals[0], last = vals[vals.length - 1];
  const max = Math.max(...vals), min = Math.min(...vals);
  
  kpiBox.innerHTML = [
    fcKpiCard(`Año Inicio (${fc.history_years[0]})`, `${fcFormatNumber(first)} %`),
    fcKpiCard(`Año Base (${fc.history_years[fc.history_years.length-1]})`, `${fcFormatNumber(last)} %`),
    fcKpiCard('Máximo Histórico', `${fcFormatNumber(max)} %`),
    fcKpiCard('Mínimo Histórico', `${fcFormatNumber(min)} %`)
  ].join('');
}

let interestProjChart = null;
function renderInterestProjection(fc) {
  const ctx = document.getElementById('interest-projection-chart');
  if (!ctx) return;
  if (interestProjChart) interestProjChart.destroy();

  const datasets = [];
  const histYears = fc.history_years;
  const histVals = fc.history_annual;
  
  datasets.push({
    label: 'Histórico',
    data: histYears.map((y, i) => ({ x: y, y: histVals[i] })),
    borderColor: isLight() ? '#1e293b' : '#ffffff', backgroundColor: isLight() ? '#1e293b' : '#ffffff',
    borderWidth: 3, pointRadius: 2, pointHoverRadius: 5, fill: false,
    order: 10
  });

  const best = fc.best_model;
  let colorIdx = 0;
  for (const [key, m] of Object.entries(fc.projection)) {
    if (m.error) continue;
    const isBest = (key === best);
    const color = MODEL_COLORS[key] || FC_PALETTE[colorIdx % FC_PALETTE.length];
    if (!isBest) colorIdx++;
    const lw = isBest ? 4 : 3;
    const ldash = isBest ? [] : [5, 5];
    const alpha = isBest ? 1.0 : 0.6;

    datasets.push({
      label: `Proj: ${key}`,
      data: m.years.map((y, i) => ({ x: y, y: m.annual[i] })),
      backgroundColor: 'transparent',
      borderWidth: lw, borderDash: ldash, tension: 0.2, pointRadius: isBest ? 2 : 0,
      borderColor: Chart.helpers.color(color).alpha(alpha).rgbString()
    });
    
    if (isBest && m.ci_low && m.ci_high) {
       datasets.push({
         label: `${key} Límite Sup`,
         data: m.years.map((y, i) => ({ x: y, y: m.ci_high[i] })),
         borderColor: 'transparent', backgroundColor: Chart.helpers.color(color).alpha(0.1).rgbString(),
         fill: '+1', pointRadius: 0, order: 20
       });
       datasets.push({
         label: `${key} Límite Inf`,
         data: m.years.map((y, i) => ({ x: y, y: m.ci_low[i] })),
         borderColor: 'transparent', backgroundColor: 'transparent',
         fill: false, pointRadius: 0, order: 20
       });
    }
  }

  interestProjChart = new Chart(ctx, {
    type: 'line', data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#a0a0a0' } } },
      scales: {
        x: { type: 'linear', title: { display: true, text: 'Año', color: '#a0a0a0' },
             grid: { color: '#333' }, ticks: { color: '#a0a0a0', callback: v => v.toString() } },
        y: { title: { display: true, text: 'Tasa (%)', color: '#a0a0a0' },
             grid: { color: '#333' }, ticks: { color: '#a0a0a0' } }
      }
    }
  });
}

function renderInterestCompareTable(fc) {
  const tbody = document.querySelector('#interest-compare-tbl tbody');
  if (!tbody || !fc.summary) return;
  const best = fc.best_model;
  
  const rows = fc.summary.map(r => {
    return { ...r, isBest: (r.model === best) };
  });
  
  tbody.innerHTML = rows.map(r => `
    <tr class="${r.isBest ? 'best-row' : ''}">
      <td>${r.isBest ? '★ ' : ''}${r.model}</td>
      <td>${r.r2 != null ? r.r2.toFixed(4) : '—'}</td>
      <td>${fcFormatNumber(r.rmse, 4)}</td>
      <td>${r.mape != null ? r.mape.toFixed(2) + '%' : '—'}</td>
      <td>${fcFormatNumber(r.final, 2)} %</td>
    </tr>`).join('');
}


// ─── TIPO DE CAMBIO ARENA ────────────────────────────────────────────────────
function bindFxArena() {
  const tagBox = document.getElementById('fx-model-tags');
  if (tagBox) {
    tagBox.addEventListener('click', e => {
      const tag = e.target.closest('[data-model]');
      if (tag) tag.classList.toggle('active');
    });
  }
  const btn = document.getElementById('btn-fx-train');
  if (btn) btn.addEventListener('click', runFxArena);
}

async function runFxArena() {
  const preset = document.getElementById('fx-preset').value;
  const depreciation = parseFloat(document.getElementById('fx-depreciation').value || '2.5');
  const horizon = Math.max(25, parseInt(document.getElementById('fx-horizon').value || '25'));
  const models = fcGetSelectedModels('fx-model-tags');
  if (!models.length) { toast('Selecciona al menos un modelo', 'warn'); return; }

  const btn = document.getElementById('btn-fx-train');
  btn.disabled = true; btn.textContent = 'Cargando y ajustando…';
  try {
    const fc = await api('/api/fx/forecast', 'POST', {
      session_id: WP.sessionId, preset, depreciation, horizon, models
    });
    WP.arenaCache.fx = fc;
    renderForecastFutureChart('fx');
    
    renderFxKPIs(fc);
    renderFxProjection(fc);
    renderArena25Years({
      canvasId: 'fx-25y-chart', badgeId: 'fx-25y-badge',
      projection: fc.projection, bestModel: fc.best_model,
      ylabel: 'MXN / USD', decimals: 2, unitLabel: 'MXN',
    });
    renderFxCompareTable(fc);

    document.getElementById('fx-best-badge').textContent = `★ Mejor: ${fc.best_model}`;
    toast('Arena de Tipo de Cambio entrenada con éxito');
  } catch (err) {
    console.error(err);
    toast(err.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Cargar y entrenar arena';
  }
}

function renderFxKPIs(fc) {
  const kpiBox = document.getElementById('fx-kpis');
  if (!fc.history_annual || !fc.history_annual.length) return;
  const vals = fc.history_annual;
  const first = vals[0], last = vals[vals.length - 1];
  const max = Math.max(...vals), min = Math.min(...vals);
  
  kpiBox.innerHTML = [
    fcKpiCard(`Año Inicio (${fc.history_years[0]})`, `$ ${fcFormatNumber(first)}`),
    fcKpiCard(`Año Base (${fc.history_years[fc.history_years.length-1]})`, `$ ${fcFormatNumber(last)}`),
    fcKpiCard('Máximo Histórico', `$ ${fcFormatNumber(max)}`),
    fcKpiCard('Mínimo Histórico', `$ ${fcFormatNumber(min)}`)
  ].join('');
}

let fxProjChart = null;
function renderFxProjection(fc) {
  const ctx = document.getElementById('fx-projection-chart');
  if (!ctx) return;
  if (fxProjChart) fxProjChart.destroy();

  const datasets = [];
  const histYears = fc.history_years;
  const histVals = fc.history_annual;
  
  datasets.push({
    label: 'Histórico',
    data: histYears.map((y, i) => ({ x: y, y: histVals[i] })),
    borderColor: isLight() ? '#1e293b' : '#ffffff', backgroundColor: isLight() ? '#1e293b' : '#ffffff',
    borderWidth: 3, pointRadius: 2, pointHoverRadius: 5, fill: false,
    order: 10
  });

  const best = fc.best_model;
  let colorIdx = 0;
  for (const [key, m] of Object.entries(fc.projection)) {
    if (m.error) continue;
    const isBest = (key === best);
    const color = MODEL_COLORS[key] || FC_PALETTE[colorIdx % FC_PALETTE.length];
    if (!isBest) colorIdx++;
    const lw = isBest ? 4 : 3;
    const ldash = isBest ? [] : [5, 5];
    const alpha = isBest ? 1.0 : 0.6;

    datasets.push({
      label: `Proj: ${key}`,
      data: m.years.map((y, i) => ({ x: y, y: m.annual[i] })),
      backgroundColor: 'transparent',
      borderWidth: lw, borderDash: ldash, tension: 0.2, pointRadius: isBest ? 2 : 0,
      borderColor: Chart.helpers.color(color).alpha(alpha).rgbString()
    });
    
    if (isBest && m.ci_low && m.ci_high) {
       datasets.push({
         label: `${key} Límite Sup`,
         data: m.years.map((y, i) => ({ x: y, y: m.ci_high[i] })),
         borderColor: 'transparent', backgroundColor: Chart.helpers.color(color).alpha(0.1).rgbString(),
         fill: '+1', pointRadius: 0, order: 20
       });
       datasets.push({
         label: `${key} Límite Inf`,
         data: m.years.map((y, i) => ({ x: y, y: m.ci_low[i] })),
         borderColor: 'transparent', backgroundColor: 'transparent',
         fill: false, pointRadius: 0, order: 20
       });
    }
  }

  fxProjChart = new Chart(ctx, {
    type: 'line', data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { color: '#a0a0a0' } } },
      scales: {
        x: { type: 'linear', title: { display: true, text: 'Año', color: '#a0a0a0' },
             grid: { color: '#333' }, ticks: { color: '#a0a0a0', callback: v => v.toString() } },
        y: { title: { display: true, text: 'MXN / USD', color: '#a0a0a0' },
             grid: { color: '#333' }, ticks: { color: '#a0a0a0' } }
      }
    }
  });
}

function renderFxCompareTable(fc) {
  const tbody = document.querySelector('#fx-compare-tbl tbody');
  if (!tbody || !fc.summary) return;
  const best = fc.best_model;
  
  const rows = fc.summary.map(r => {
    return { ...r, isBest: (r.model === best) };
  });
  
  tbody.innerHTML = rows.map(r => `
    <tr class="${r.isBest ? 'best-row' : ''}">
      <td>${r.isBest ? '★ ' : ''}${r.model}</td>
      <td>${r.r2 != null ? r.r2.toFixed(4) : '—'}</td>
      <td>${fcFormatNumber(r.rmse, 4)}</td>
      <td>${r.mape != null ? r.mape.toFixed(2) + '%' : '—'}</td>
      <td>$ ${fcFormatNumber(r.final, 2)}</td>
    </tr>`).join('');
}

// ─── HELPERS GENÉRICOS DE GRÁFICA UNIFICADA ──────────────────────────────────
// Predicción continua: histórico + todas las proyecciones en el mismo eje
function renderArenaPrediction({ canvasId, badgeId, history, projection, bestModel, ylabel, scaler = (v) => v, decimals = 2, unitLabel = '' }) {
  destroyChart(canvasId);
  const ctxEl = document.getElementById(canvasId);
  if (!ctxEl) return;
  const ctx = ctxEl.getContext('2d');

  const datasets = [];
  const histYears = history.years || [];
  const histVals  = history.annual || [];
  datasets.push({
    label: 'Histórico',
    data: histYears.map((y, i) => ({ x: y, y: scaler(histVals[i]) })),
    borderColor: ink(), backgroundColor: ink(),
    borderWidth: 2.6, pointRadius: 1.6, fill: false, order: 10,
  });

  let colorIdx = 0;
  for (const [key, m] of Object.entries(projection || {})) {
    if (!m || m.error || !m.years) continue;
    const isBest = (key === bestModel);
    const color = isBest ? '#22c55e' : FC_PALETTE[colorIdx % FC_PALETTE.length];
    if (!isBest) colorIdx++;
    datasets.push({
      label: `Proj: ${key}${isBest ? ' ★' : ''}`,
      data: m.years.map((y, i) => ({ x: y, y: scaler(m.annual[i]) })),
      borderColor: color, backgroundColor: 'transparent',
      borderWidth: isBest ? 2.8 : 1.4,
      borderDash: isBest ? [] : [5, 4],
      pointRadius: 0, tension: 0.2,
    });
    if (isBest && m.ci_low && m.ci_high) {
      datasets.push({
        label: `${key} IC sup`,
        data: m.years.map((y, i) => ({ x: y, y: scaler(m.ci_high[i]) })),
        borderColor: 'transparent',
        backgroundColor: Chart.helpers.color(color).alpha(0.12).rgbString(),
        fill: '+1', pointRadius: 0, order: 20,
      });
      datasets.push({
        label: `${key} IC inf`,
        data: m.years.map((y, i) => ({ x: y, y: scaler(m.ci_low[i]) })),
        borderColor: 'transparent', backgroundColor: 'transparent',
        fill: false, pointRadius: 0, order: 20,
      });
    }
  }

  WP.charts[canvasId] = new Chart(ctx, {
    type: 'line', data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { filter: it => !/IC (sup|inf)$/.test(it.text) } },
        tooltip: { callbacks: {
          label: ctx => `${ctx.dataset.label}: ${fcFormatNumber(ctx.parsed.y, decimals)} ${unitLabel}`.trim()
        } }
      },
      scales: {
        x: { type: 'linear', title: { display: true, text: 'Año' }, grid: { color: gridColor() }, ticks: { callback: v => v.toString() } },
        y: { title: { display: true, text: ylabel }, grid: { color: gridColor() } }
      }
    }
  });

  if (badgeId && bestModel) {
    const b = projection?.[bestModel];
    const last = b?.annual?.at(-1);
    const cagr = b?.cagr;
    const txt = `Best: ${bestModel}` + (cagr != null ? ` · CAGR ${fcPct(cagr)}` : '') + (last != null ? ` · Año final ${fcFormatNumber(scaler(last), decimals)} ${unitLabel}` : '');
    const bd = document.getElementById(badgeId);
    if (bd) bd.textContent = txt;
  }
}

// Proyección continua hora a hora a N años: escala el último ciclo (8760 h o 365 d)
// del histórico por annual[y] / aggregate(perfil) para CADA modelo proyectado.
// Incluye zoom con scroll y pan con drag igual que la gráfica observado-vs-predicho.
function renderArena25YearsHourly({ canvasId, badgeId, rawValues, projection, bestModel,
                                     ylabel, scaler = (v) => v, decimals = 2, unitLabel = '',
                                     baseYearLabel = null, annualKind = 'mean' }) {
  destroyChart(canvasId);
  const ctxEl = document.getElementById(canvasId);
  if (!ctxEl) return false;
  if (!rawValues || !rawValues.length) return false;

  const proj = projection || {};
  const modelKeys = Object.keys(proj).filter(k => proj[k] && !proj[k].error && proj[k].annual?.length);
  if (!modelKeys.length) return false;
  const refModel = (bestModel && proj[bestModel]) ? proj[bestModel] : proj[modelKeys[0]];

  // Determinar tamaño del ciclo
  const n = rawValues.length;
  let cycleLen;
  if (n >= 8760) cycleLen = 8760;
  else if (n >= 365) cycleLen = 365;
  else cycleLen = n;
  const baseProfile = rawValues.slice(-cycleLen);

  // Suma y media del perfil base
  let baseSum = 0;
  for (let i = 0; i < baseProfile.length; i++) baseSum += baseProfile[i];
  const baseMean = baseSum / baseProfile.length;
  const denom = (annualKind === 'sum') ? baseSum : baseMean;
  if (!isFinite(denom) || denom === 0) return false;

  const horizon = refModel.annual.length;
  const totalLen = horizon * cycleLen;
  const startYear = (refModel.years && refModel.years[0]) ||
                    (baseYearLabel ? baseYearLabel + 1 : new Date().getFullYear() + 1);

  // Etiquetas
  const labels = new Array(totalLen);
  for (let y = 0; y < horizon; y++) {
    const yr = startYear + y;
    for (let h = 0; h < cycleLen; h++) {
      labels[y * cycleLen + h] = `${yr} · ${cycleLen === 8760 ? 'h' : 'd'}${h+1}`;
    }
  }

  // Construir serie horaria por modelo
  function buildHourlySeries(annualArr) {
    const arr = new Array(totalLen);
    for (let y = 0; y < annualArr.length && y < horizon; y++) {
      const factor = annualArr[y] / denom;
      for (let h = 0; h < cycleLen; h++) {
        arr[y * cycleLen + h] = scaler(baseProfile[h] * factor);
      }
    }
    return arr;
  }

  const datasets = [];

  // Banda IC del mejor modelo (al fondo)
  const best = bestModel && proj[bestModel] && !proj[bestModel].error ? proj[bestModel] : null;
  if (best && best.ci_high?.length && best.ci_low?.length) {
    datasets.push({
      label: 'IC 95% sup', data: buildHourlySeries(best.ci_high),
      borderColor: 'transparent', backgroundColor: '#22c55e1a',
      fill: '+1', pointRadius: 0, borderWidth: 0, order: 99,
    });
    datasets.push({
      label: 'IC 95% inf', data: buildHourlySeries(best.ci_low),
      borderColor: 'transparent', backgroundColor: 'transparent',
      fill: false, pointRadius: 0, borderWidth: 0, order: 99,
    });
  }

  // Una línea por modelo
  modelKeys.forEach((k, i) => {
    const isBest = (k === bestModel);
    datasets.push({
      label: `${k}${isBest ? ' \u2605' : ''}`,
      data: buildHourlySeries(proj[k].annual),
      borderColor: isBest ? '#22c55e' : FC_PALETTE[i % FC_PALETTE.length],
      backgroundColor: (isBest ? '#22c55e' : FC_PALETTE[i % FC_PALETTE.length]) + '22',
      borderWidth: isBest ? 1.4 : 0.8,
      borderDash: isBest ? [] : [3, 2],
      fill: false, tension: 0, pointRadius: 0,
      order: isBest ? 1 : 10 + i,
    });
  });

  // ── Toolbar de navegación ──────────────────────────────────────────────────
  const wrapper = ctxEl.parentElement;
  if (wrapper) wrapper.style.position = wrapper.style.position || 'relative';

  // Limpiar toolbar anterior si existe
  const oldToolbar = wrapper?.querySelector('.hourly-nav-toolbar');
  if (oldToolbar) oldToolbar.remove();

  // Estado de ventana deslizante
  const WIN_YEARS  = 5;
  let winStart     = 0;
  let winEnd       = WIN_YEARS * cycleLen - 1;
  let isZoomed     = false;
  let boxSelectOn  = false;

  const toolbar = document.createElement('div');
  toolbar.className = 'hourly-nav-toolbar';
  toolbar.style.cssText = [
    'position:absolute', 'top:6px', 'right:6px', 'z-index:10',
    'display:flex', 'gap:5px', 'align-items:center',
  ].join(';');

  const btnPrev = document.createElement('button');
  btnPrev.type = 'button'; btnPrev.textContent = '\u25C4'; btnPrev.title = 'Año anterior';
  btnPrev.className = 'hourly-nav-btn';

  const yrLabel = document.createElement('span');
  yrLabel.className = 'hourly-nav-label';

  const btnNext = document.createElement('button');
  btnNext.type = 'button'; btnNext.textContent = '\u25BA'; btnNext.title = 'Año siguiente';
  btnNext.className = 'hourly-nav-btn';

  const btnAll = document.createElement('button');
  btnAll.type = 'button'; btnAll.textContent = '\u22DE Ver todo';
  btnAll.title = 'Mostrar todos los años (sin zoom)';
  btnAll.className = 'hourly-nav-btn hourly-nav-btn--all';

  const btnZoomSel = document.createElement('button');
  btnZoomSel.type = 'button'; btnZoomSel.textContent = '\u25A3 Zoom sel.';
  btnZoomSel.title = 'Haz drag sobre el gráfico para hacer zoom en esa área';
  btnZoomSel.className = 'hourly-nav-btn hourly-nav-btn--zoom';

  toolbar.append(btnPrev, yrLabel, btnNext, btnAll, btnZoomSel);
  if (wrapper) wrapper.appendChild(toolbar);

  // ── Helpers de ventana ─────────────────────────────────────────────────────
  function updateLabel() {
    if (isZoomed) { yrLabel.textContent = 'Zoom activo'; return; }
    const y0 = startYear + Math.floor(winStart / cycleLen);
    const y1 = startYear + Math.min(horizon - 1, Math.floor(winEnd / cycleLen));
    yrLabel.textContent = `${y0} \u2013 ${y1}`;
  }

  function applyWindow(chart) {
    if (isZoomed) return;
    chart.options.scales.x.min = winStart;
    chart.options.scales.x.max = winEnd;
    chart.update('none');
    updateLabel();
    btnPrev.disabled = (winStart <= 0);
    btnNext.disabled = (winEnd >= totalLen - 1);
  }

  function slideYears(delta) {
    if (isZoomed) return;
    const step = cycleLen;
    winStart = Math.max(0, Math.min(winStart + delta * step, totalLen - WIN_YEARS * cycleLen));
    winEnd   = winStart + WIN_YEARS * cycleLen - 1;
    applyWindow(WP.charts[canvasId]);
  }

  // ── Crear chart ────────────────────────────────────────────────────────────
  const ctx = ctxEl.getContext('2d');
  WP.charts[canvasId] = new Chart(ctx, {
    type: 'line', data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { labels: { filter: it => !/IC 95%/.test(it.text) } },
        decimation: { enabled: true, algorithm: 'lttb', samples: 3000, threshold: 3000 },
        tooltip: { callbacks: {
          label: c => `${c.dataset.label}: ${fcFormatNumber(c.parsed.y, decimals)} ${unitLabel}`.trim()
        } },
        zoom: {
          pan:  { enabled: false },        // pan libre DESACTIVADO
          zoom: {
            wheel:  { enabled: false },    // scroll DESACTIVADO (evita lentitud)
            pinch:  { enabled: false },    // pinch DESACTIVADO
            drag:   { enabled: false },    // box-select OFF por defecto
            mode: 'x',
            onZoomComplete: () => {
              isZoomed = true;
              updateLabel();
              btnZoomSel.textContent = '\u2715 Salir zoom';
              btnZoomSel.classList.add('active');
              // desactivar drag para no re-zoomear encima
              WP.charts[canvasId].options.plugins.zoom.zoom.drag.enabled = false;
              WP.charts[canvasId].update('none');
              boxSelectOn = false;
            },
          },
        },
      },
      scales: {
        x: {
          min: 0, max: WIN_YEARS * cycleLen - 1,
          title: {
            display: true,
            text: `\u25C4 \u25BA Navega con los botones  \u00B7  ${WIN_YEARS} de ${horizon} años visibles  \u00B7  Zoom solo por selección`
          },
          grid: { color: gridColor() },
          ticks: {
            maxTicksLimit: Math.min(horizon, 26), font: { size: 9 },
            callback: function(_val, idx) {
              if (idx % cycleLen === 0) return startYear + Math.floor(idx / cycleLen);
              return '';
            }
          }
        },
        y: {
          title: { display: true, text: ylabel },
          grid: { color: gridColor() }, ticks: { font: { size: 9 } }
        }
      }
    }
  });

  // ── Eventos de toolbar ─────────────────────────────────────────────────────
  btnPrev.addEventListener('click', () => slideYears(-1));
  btnNext.addEventListener('click', () => slideYears(+1));

  btnAll.addEventListener('click', () => {
    const chart = WP.charts[canvasId];
    if (!chart) return;
    isZoomed = false; boxSelectOn = false;
    chart.options.plugins.zoom.zoom.drag.enabled = false;
    chart.options.scales.x.min = 0;
    chart.options.scales.x.max = totalLen - 1;
    chart.update('none');
    yrLabel.textContent = `Todo (${horizon} a\u00F1os)`;
    btnZoomSel.textContent = '\u25A3 Zoom sel.';
    btnZoomSel.classList.remove('active');
    btnPrev.disabled = true;
    btnNext.disabled = true;
  });

  btnZoomSel.addEventListener('click', () => {
    const chart = WP.charts[canvasId];
    if (!chart) return;
    if (isZoomed) {
      // Salir del zoom → volver a ventana de 5 años
      isZoomed = false; boxSelectOn = false;
      chart.options.plugins.zoom.zoom.drag.enabled = false;
      chart.resetZoom();
      btnZoomSel.textContent = '\u25A3 Zoom sel.';
      btnZoomSel.classList.remove('active');
      applyWindow(chart);
    } else if (boxSelectOn) {
      // Cancelar selección
      boxSelectOn = false;
      chart.options.plugins.zoom.zoom.drag.enabled = false;
      chart.update('none');
      btnZoomSel.textContent = '\u25A3 Zoom sel.';
      btnZoomSel.classList.remove('active');
    } else {
      // Activar box-select
      boxSelectOn = true;
      chart.options.plugins.zoom.zoom.drag.enabled = true;
      chart.update('none');
      btnZoomSel.textContent = '\uD83D\uDD0D Arrastra para zoom\u2026';
      btnZoomSel.classList.add('active');
    }
  });

  // Scroll horizontal → desliza años (sin zoom)
  ctxEl.addEventListener('wheel', (e) => {
    if (isZoomed || boxSelectOn) return;
    if (Math.abs(e.deltaX) < 5 && Math.abs(e.deltaY) < 5) return;
    e.preventDefault();
    const dir = (Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY) > 0 ? 1 : -1;
    slideYears(dir);
  }, { passive: false });

  // Touch swipe horizontal
  let touchStartX = null;
  ctxEl.addEventListener('touchstart', (e) => {
    if (e.touches.length === 1) touchStartX = e.touches[0].clientX;
  }, { passive: true });
  ctxEl.addEventListener('touchend', (e) => {
    if (touchStartX === null) return;
    const dx = (e.changedTouches[0]?.clientX ?? touchStartX) - touchStartX;
    touchStartX = null;
    if (Math.abs(dx) < 40) return;
    slideYears(dx < 0 ? 1 : -1);
  }, { passive: true });

  // Estado inicial
  updateLabel();
  btnPrev.disabled = true;
  btnNext.disabled = (horizon <= WIN_YEARS);

  // Badge
  if (badgeId && best) {
    const lastVal = best.annual[best.annual.length - 1];
    const txt = `Best: ${bestModel} \u00B7 ${totalLen.toLocaleString()} ${cycleLen === 8760 ? 'h' : 'd'} \u00B7 CAGR ${fcPct(best.cagr)} \u00B7 A\u00F1o final ${fcFormatNumber(scaler(lastVal), decimals)} ${unitLabel}`;
    const bd = document.getElementById(badgeId);
    if (bd) bd.textContent = txt;
  }
  return true;
}

// Proyección 25 años (solo futuro) con bandas IC del mejor modelo
function renderArena25Years({ canvasId, badgeId, projection, bestModel, ylabel, scaler = (v) => v, decimals = 2, unitLabel = '' }) {
  destroyChart(canvasId);
  const ctxEl = document.getElementById(canvasId);
  if (!ctxEl) return;
  const ctx = ctxEl.getContext('2d');
  const proj = projection || {};
  const keys = Object.keys(proj).filter(k => proj[k] && !proj[k].error);
  if (!keys.length) return;
  const labels = proj[keys[0]].years;

  const datasets = keys.map((k, i) => {
    const isBest = (k === bestModel);
    return {
      label: `${k}${isBest ? ' ★' : ''}`,
      data: proj[k].annual.map(scaler),
      borderColor: isBest ? '#22c55e' : FC_PALETTE[i % FC_PALETTE.length],
      backgroundColor: (isBest ? '#22c55e' : FC_PALETTE[i % FC_PALETTE.length]) + '22',
      borderWidth: isBest ? 2.8 : 1.4,
      borderDash: isBest ? [] : [5, 4],
      fill: false, tension: 0.25, pointRadius: 0,
    };
  });

  if (bestModel && proj[bestModel]) {
    const b = proj[bestModel];
    datasets.push({
      label: 'IC 95% sup', data: b.ci_high.map(scaler),
      borderColor: 'transparent', backgroundColor: '#22c55e22',
      fill: '+1', pointRadius: 0, borderWidth: 0,
    });
    datasets.push({
      label: 'IC 95% inf', data: b.ci_low.map(scaler),
      borderColor: 'transparent', backgroundColor: 'transparent',
      pointRadius: 0, borderWidth: 0, fill: false,
    });
  }

  WP.charts[canvasId] = new Chart(ctx, {
    type: 'line', data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { labels: { filter: it => !/IC 95%/.test(it.text) } },
        tooltip: { callbacks: {
          label: ctx => `${ctx.dataset.label}: ${fcFormatNumber(ctx.parsed.y, decimals)} ${unitLabel}`.trim()
        } }
      },
      scales: {
        x: { title: { display: true, text: 'Año' }, grid: { color: gridColor() } },
        y: { title: { display: true, text: ylabel }, grid: { color: gridColor() } }
      }
    }
  });

  if (badgeId && bestModel && proj[bestModel]) {
    const b = proj[bestModel];
    const txt = `Best: ${bestModel} · CAGR ${fcPct(b.cagr)} · Año final ${fcFormatNumber(scaler(b.annual.at(-1)), decimals)} ${unitLabel}`;
    const bd = document.getElementById(badgeId);
    if (bd) bd.textContent = txt;
  }
}

// ─── VIENTO: PROYECCIÓN ANUAL (motor de tendencia, post-entrenamiento ML) ────
async function runWindAnnualForecast() {
  if (!WP.sessionId) return;
  try {
    const fc = await api('/api/wind/annual_forecast', 'POST', {
      session_id: WP.sessionId, ws_col: 'wind_speed', growth: 0.005, horizon: 25,
    });
    WP.arenaCache.wind = fc;
    renderForecastFutureChart('wind');
    renderWindKPIs(fc);
    renderArenaPrediction({
      canvasId: 'wind-prediction-chart', badgeId: 'wind-prediction-badge',
      history: { years: fc.history_years, annual: fc.history_annual },
      projection: fc.projection, bestModel: fc.best_model,
      ylabel: 'Velocidad media anual (m/s)', decimals: 2, unitLabel: 'm/s',
    });
    renderArena25Years({
      canvasId: 'wind-25y-chart', badgeId: 'wind-25y-badge',
      projection: fc.projection, bestModel: fc.best_model,
      ylabel: 'Velocidad media anual (m/s)', decimals: 2, unitLabel: 'm/s',
    });
  } catch (e) {
    console.warn('wind annual forecast skipped:', e.message);
  }
}

function renderWindKPIs(fc) {
  const box = document.getElementById('wind-kpis');
  if (!box) return;
  const s = fc.stats || {};
  const html = [
    fcKpiCard('Horas observadas', fcFormatNumber(s.n_hours), 'serie horaria'),
    fcKpiCard('Velocidad media', `${fcFormatNumber(s.mean, 2)} m/s`),
    fcKpiCard('P10 / P90', `${fcFormatNumber(s.p10, 2)} / ${fcFormatNumber(s.p90, 2)}`, 'm/s'),
    fcKpiCard('Mín / Máx', `${fcFormatNumber(s.min, 2)} / ${fcFormatNumber(s.max, 2)}`, 'm/s'),
    fcKpiCard('Desviación', `${fcFormatNumber(s.std, 2)} m/s`),
    fcKpiCard('Histórico anual', s.used_real_annual ? 'Real (>=2 años)' : 'Sintético'),
  ].join('');
  box.innerHTML = html;
}



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
      renderForecastFutureChart(key);
    });
  });

  ['demand', 'demand_hourly', 'pml', 'pml_hourly', 'interest', 'fx'].forEach(key => {
    const sel = document.getElementById(`fs-${key}-model-select`);
    if (sel) {
      sel.addEventListener('change', () => loadForecastFromCache(key));
    }
  });
  renderForecastFutureChart('wind');
}

function getForecastStudioTargetCache(key) {
  const cache = WP.arenaCache[key];
  if ((key === 'pml' || key === 'pml_hourly') && cache?.sides) {
    const selectedSide = document.getElementById('pml-side')?.value;
    const sideKey = selectedSide && selectedSide !== 'both' && cache.sides[selectedSide]
      ? selectedSide
      : (cache.sides.venta ? 'venta' : Object.keys(cache.sides)[0]);
    return { cache, targetCache: cache.sides[sideKey], sideKey };
  }
  return { cache, targetCache: cache, sideKey: null };
}

function populateForecastStudioSelect(key) {
  const { targetCache, sideKey } = getForecastStudioTargetCache(key);
  const sel = document.getElementById(`fs-${key}-model-select`);
  if (!sel) return;

  if (!targetCache || !targetCache.fitted || Object.keys(targetCache.fitted).length === 0) {
    sel.innerHTML = '<option value="">Aún no hay modelos entrenados en Arena</option>';
    return;
  }
  
  const currentVal = sel.value;
  sel.innerHTML = '<option value="">-- Seleccionar Modelo --</option>';
  for (const modelName of Object.keys(targetCache.fitted)) {
    const m = targetCache.fitted[modelName];
    if (m.error) continue;
    const opt = document.createElement('option');
    opt.value = modelName;
    const sideLabel = sideKey ? ` · ${sideKey}` : '';
    opt.textContent = `${modelName}${sideLabel} (R² = ${m.metrics?.r2?.toFixed(2) || 'N/A'})`;
    sel.appendChild(opt);
  }
  
  if (currentVal && targetCache.fitted[currentVal] && !targetCache.fitted[currentVal].error) {
    sel.value = currentVal;
  } else if (targetCache.best_model) {
    sel.value = targetCache.best_model;
  }
  
  if (sel.value) {
    loadForecastFromCache(key);
  }
}

function loadForecastFromCache(key) {
  const { cache, targetCache } = getForecastStudioTargetCache(key);
  const sel = document.getElementById(`fs-${key}-model-select`);
  if (!cache) { console.warn(`Forecast Studio: no hay caché para "${key}"`); return; }
  if (!sel || !sel.value) return;

  const modelName = sel.value;

  if (!targetCache || !targetCache.fitted) { console.warn(`Forecast Studio: fitted no disponible para "${key}"`); return; }
  const m = targetCache.fitted[modelName];
  if (!m || m.error) { console.warn(`Forecast Studio: modelo "${modelName}" no disponible`); return; }

  const y_pred_raw = m.fitted_values || m.fitted || [];
  let y_true = [];
  let y_pred = [];
  let labels = [];

  if (key === 'interest' || key === 'fx') {
    // Financieras: entrenadas sobre datos anuales reales (desde 2001)
    const full_true = targetCache.history_annual || [];
    const full_years = targetCache.history_years || [];
    const train_len  = y_pred_raw.length;
    y_true  = full_true.slice(full_true.length - train_len);
    y_pred  = y_pred_raw;
    labels  = full_years.slice(full_years.length - train_len).map(y => y.toString());
  } else if (key === 'demand_hourly' || key === 'pml_hourly') {
    y_true = (targetCache.raw_values || []).slice(0, y_pred_raw.length);
    y_pred = y_pred_raw;
    labels = y_true.map((_, i) => 'h=' + (i + 1));
  } else {
    // Demanda / PML: entrenadas sobre agregados anuales (sintéticos o reales)
    y_true  = targetCache.history_annual || [];
    y_pred  = y_pred_raw;
    labels  = (targetCache.history_t || []).map(t => 't=' + t);

    if (key === 'demand') {
      y_true = y_true.map(v => v / 1000);  // MWh → GWh
      y_pred = y_pred.map(v => v / 1000);
    }
  }

  const unitMap = {
    'demand':   'GWh',
    'demand_hourly': 'MW',
    'pml':      '$/kWh',
    'pml_hourly': '$/kWh',
    'interest': '%',
    'fx':       'MXN/USD',
    'wind':     'm/s'
  };

  const r = {
    y_true,
    y_pred,
    labels,
    unit:    unitMap[key] || '',
    metrics: m.metrics,
    residuals: { binned_bias: [] },
    stability: []
  };

  const prefix = `fs-${key}`;
  renderForecastMainChart(r, prefix);
  renderScatterChart(r, prefix);
  renderResidualHistogram(r, prefix);
  renderBinnedBiasChart(r, prefix);
  renderForecastFutureChart(key);
  renderForecastMetricPills(r.metrics, prefix, r.unit);

  // Ocultar tarjeta de estabilidad (solo para viento)
  const stabEl = document.getElementById(`${prefix}-stability-chart`);
  if (stabEl) stabEl.parentElement.parentElement.style.display = 'none';

  // Perfil horario real (solo Demanda y PML)
  if ((key === 'demand' || key === 'pml') && targetCache.raw_values?.length) {
    renderRawHourlyProfile(targetCache.raw_values, key, unitMap[key]);
  }
}

function renderRawHourlyProfile(rawValues, key, unit) {
  const canvasId = `fs-${key}-raw-chart`;
  const el = document.getElementById(canvasId);
  if (!el) return;

  const chartKey = `${key}-raw`;
  destroyChart(chartKey);

  const vals = key === 'demand' ? rawValues.map(v => v / 1000) : rawValues;
  const labels = vals.map((_, i) => i + 1);

  WP.charts[chartKey] = new Chart(el.getContext('2d'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: `Perfil horario (${unit})`,
        data: vals,
        borderColor: acc(),
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: item => `Hora ${item.label}: ${item.formattedValue} ${unit}`
          }
        }
      },
      scales: {
        x: {
          grid: { color: gridColor() },
          ticks: { maxTicksLimit: 12, font: { size: 9 } }
        },
        y: {
          grid: { color: gridColor() },
          title: { display: true, text: unit },
          ticks: { font: { size: 9 } }
        }
      }
    }
  });
}

// ─── MARKET DATA HUB ─────────────────────────────────────────────────────────

const MARKET_CATS = ['demand', 'pml', 'interest', 'fx'];
const MARKET_LABELS = {
  demand: 'Demanda', pml: 'PML', interest: 'Tasa de Interés', fx: 'Tipo de Cambio'
};

async function loadMarketFiles() {
  try {
    const r = await api('/api/market/list_files', 'GET');
    const badge = document.getElementById('market-file-count');
    badge.textContent = `${r.count} archivos`;

    MARKET_CATS.forEach(cat => {
      const sel = document.getElementById(`market-${cat}-select`);
      if (!sel) return;
      const files = (r.grouped && r.grouped[cat]) || [];
      // Preserve the default empty option
      sel.innerHTML = sel.options[0] ? sel.options[0].outerHTML : '';
      files.forEach(f => {
        const opt = document.createElement('option');
        opt.value = f.filename;
        opt.textContent = `${f.filename}  (${f.size_kb} KB)`;
        sel.appendChild(opt);
      });
    });
  } catch (e) {
    const badge = document.getElementById('market-file-count');
    if (badge) badge.textContent = 'Error';
  }
}

function bindMarketDataHub() {
  MARKET_CATS.forEach(cat => {
    // Load button
    const btn = document.getElementById(`btn-load-market-${cat}`);
    if (btn) {
      btn.addEventListener('click', () => {
        const sel = document.getElementById(`market-${cat}-select`);
        if (!sel || !sel.value) { toast('Selecciona un archivo primero', 'warn'); return; }
        loadMarketFile(cat, sel.value);
      });
    }
    // Upload input
    const uploadInput = document.getElementById(`market-${cat}-upload`);
    if (uploadInput) {
      uploadInput.addEventListener('change', () => {
        if (uploadInput.files[0]) uploadMarketFile(cat, uploadInput.files[0]);
      });
    }
  });
}

async function loadMarketFile(category, filename) {
  setStatus(`Cargando ${filename}…`);
  try {
    const r = await api('/api/market/load_file', 'POST', {
      session_id: WP.sessionId,
      filename: filename,
      category: category,
    });
    _renderMarketResult(category, r);
    toast(`${MARKET_LABELS[category]}: ${r.filename} cargado — ${r.n_rows.toLocaleString()} registros`, 'success');
    setStatus(`${MARKET_LABELS[category]} listo: ${r.filename}`);
  } catch (e) {
    toast(`Error cargando ${filename}: ${e.message}`, 'error');
    setStatus('Error al cargar datos de mercado.');
  }
}

async function uploadMarketFile(category, file) {
  setStatus(`Subiendo ${file.name}…`);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('session_id', WP.sessionId);
  fd.append('category', category);
  try {
    const r = await apiFD('/api/market/upload', fd);
    _renderMarketResult(category, r);
    // Reload file list so the new file appears in the selector
    await loadMarketFiles();
    toast(`${MARKET_LABELS[category]}: ${r.filename} subido — ${r.n_rows.toLocaleString()} registros`, 'success');
    setStatus(`${MARKET_LABELS[category]} listo: ${r.filename}`);
  } catch (e) {
    toast(`Error subiendo ${file.name}: ${e.message}`, 'error');
    setStatus('Error al subir archivo de mercado.');
  }
}

function _renderMarketResult(category, r) {
  // Badge
  const badge = document.getElementById(`market-${category}-badge`);
  if (badge) {
    badge.textContent = `${r.n_rows.toLocaleString()} registros`;
    badge.style.display = '';
  }
  // Info text
  const info = document.getElementById(`market-${category}-info`);
  if (info) {
    const meta = r.meta || {};
    const parts = [r.filename];
    if (meta.date_range) parts.push(`${meta.date_range[0]?.substring(0,10)} → ${meta.date_range[1]?.substring(0,10)}`);
    parts.push(`${r.n_rows.toLocaleString()} registros`);
    info.textContent = parts.join(' · ');
  }

  // Chart preview
  if (r.chart && r.chart.values) {
    const ctx = document.getElementById('market-preview-chart');
    if (ctx) {
      if (WP.charts['market-preview']) WP.charts['market-preview'].destroy();
      const step = Math.max(1, Math.floor(r.chart.values.length / 1500));
      const vals = r.chart.values.filter((_, i) => i % step === 0);
      const labels = vals.map((_, i) => i * step);
      WP.charts['market-preview'] = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: r.chart.label || MARKET_LABELS[category],
            data: vals,
            borderColor: acc(),
            borderWidth: 1.2,
            pointRadius: 0,
            fill: false,
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          plugins: { legend: { display: true, position: 'top' } },
          scales: {
            x: { grid: { color: gridColor() }, ticks: { maxTicksLimit: 10, font: { size: 9 } } },
            y: { grid: { color: gridColor() }, title: { display: true, text: (r.meta && r.meta.unit) || '' }, ticks: { font: { size: 9 } } },
          }
        }
      });
    }
  }

  // Stats grid
  const statsGrid = document.getElementById('market-stats-grid');
  if (statsGrid && r.stats) {
    const s = r.stats.compra ? r.stats.compra : r.stats;  // Use compra for PML, or the stats directly
    const items = [
      ['Registros', s.n_hours?.toLocaleString() || r.n_rows?.toLocaleString()],
      ['Media', s.mean?.toFixed(4)],
      ['Desv. Est.', s.std?.toFixed(4)],
      ['Mín', s.min?.toFixed(4)],
      ['Máx', s.max?.toFixed(4)],
      ['P10', s.p10?.toFixed(4)],
      ['P90', s.p90?.toFixed(4)],
      ['Factor de carga', s.load_factor?.toFixed(3)],
    ];
    statsGrid.innerHTML = items.map(([label, val]) =>
      `<div class="meta-item"><div class="meta-item-label">${label}</div><div class="meta-item-val">${val ?? '—'}</div></div>`
    ).join('');
  }

  // Store in WP for later use by Model Arena / Forecast Studio
  WP.arenaCache[`market_${category}`] = r;
}

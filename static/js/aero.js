/* ═══════════════════════════════════════════════════
   AERO OPEN CIM — Frontend application logic
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
  technoActiveScenario: 'real',
  technoScenarios: {
    real:      { inputs: null, result: null, leveraged: null },
    ml:        { inputs: null, result: null, leveraged: null },
    corrected: { inputs: null, result: null, leveraged: null },
  },
  levParams: { debt_ratio_pct: 70, interest_rate_pct: 8, loan_years: 15, equity_discount_rate_pct: 12 },
  technoFinalLoss: { enabled: false, pct: 3.0 },  // Pérdidas eléctricas aplicadas al AEP final (post-simulación)
  referenceHeight: null,   // altura de referencia global (m) — set por modal al cargar estación
  referenceMethod: 'hellmann', // método de extrapolación seleccionado
};

/** Altura de referencia activa para todos los cálculos.
 *  Prioridad: referenceHeight → input #ws-height → 50 m. */
function wsH() {
  if (WP.referenceHeight != null) return WP.referenceHeight;
  const el = document.getElementById('ws-height');
  return el ? (parseFloat(el.value) || 50) : 50;
}

/** Actualiza todos los inputs de altura de la UI con la altura de referencia. */
function applyReferenceHeightToUI(h) {
  ['ws-height', 'adv-ws-height'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = h;
  });
  const badge = document.getElementById('ref-height-badge');
  if (badge) {
    badge.textContent = `↕ ${h} m ref`;
    badge.style.display = '';
  }
}

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
  bindResumenUnified();
  bindHybrid();
  bindTechno();
  bindExport();
  bindMarketDataHub();
  await loadPresetFiles();   // populate preset file dropdown
  await loadMarketFiles();   // populate market data selectors
  await loadMarketPresets(); // populate dem/pml/interest/fx preset dropdowns
  await loadTurbineList();   // populate turbine dropdown from aerogeneradores
  bindGlobeStage();          // pantalla inicial: globo terráqueo + selección de sitio
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
  const moduleGroups = {
    models: ['models', 'forecast'],
    forecast: ['models', 'forecast'],
    kriging: ['kriging', 'parque'],
    parque: ['kriging', 'parque'],
    validation: ['validation', 'export'],
    export: ['validation', 'export']
  };
  const primaryModule = {
    forecast: 'models',
    parque: 'kriging',
    export: 'validation'
  };
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const mod = btn.dataset.module;
      if (!mod) return;  // botones sin data-module (ej. Volver al Globe) tienen su handler dedicado
      // Si el globo está visible, colapsarlo al cambiar de módulo via el topbar.
      if (typeof GLOBE !== 'undefined' && GLOBE && !GLOBE.collapsed && GLOBE.appOn) {
        collapseGlobeStage();
      }
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.module').forEach(m => m.classList.remove('active'));
      const activeMod = primaryModule[mod] || mod;
      const activeBtn = document.querySelector(`.nav-btn[data-module="${activeMod}"]`) || btn;
      activeBtn.classList.add('active');
      (moduleGroups[mod] || [mod]).forEach(moduleName => {
        const target = document.getElementById(`mod-${moduleName}`);
        if (target) target.classList.add('active');
      });
      if ((moduleGroups[mod] || [mod]).includes('parque') && typeof parqueActivate === 'function') {
        requestAnimationFrame(parqueActivate);
      }
      if ((moduleGroups[mod] || [mod]).includes('export') && typeof updateExportSessionInfo === 'function') {
        updateExportSessionInfo();
      }
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

async function loadPresetFileByName(filename, source, targetHeight = null, extrMethod = null) {
  if (!filename) return;
  setStatus(`Cargando ${filename}…`);
  const badge = document.getElementById('preset-source-badge');
  badge.textContent = source?.toUpperCase() || 'AUTO';
  badge.style.display = '';

  const body = { session_id: WP.sessionId, filename, source: 'auto' };
  const th = targetHeight ?? WP.referenceHeight;
  if (th != null) {
    body.target_height_m      = th;
    body.extrapolation_method = extrMethod ?? WP.referenceMethod ?? 'hellmann';
  }

  try {
    const r = await api('/api/data/load_preset', 'POST', body);

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

    // Update reference height from server response
    const refH = r.reference_height_m ?? meta.wind_height_m;
    if (refH) {
      WP.referenceHeight = refH;
      applyReferenceHeightToUI(refH);
    }
    if (r.height_normalized) {
      toast(`Datos normalizados ${r.native_height_m} m → ${r.reference_height_m} m`, 'info');
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

// ─── CENTRO DE MODELAJE ──────────────────────────────────────────────────────
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

  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="9" class="empty-msg">Sin resultados</td></tr>'; return; }

  const bestRmse = rows[0].rmse;
  const fmtSkill = (s) => s == null
    ? '—'
    : `<span style="color:${s > 0 ? 'var(--green)' : 'var(--red)'}">${(s*100).toFixed(1)}%</span>`;
  rows.forEach((row, i) => {
    const isBest = i === 0;
    tbody.innerHTML += `
      <tr class="${isBest ? 'best-row' : ''}">
        <td class="model-name">${isBest ? '★ ' : ''}${row.label}</td>
        <td><span class="badge badge--${row.type === 'ml' ? 'info' : 'warn'}">${row.type}</span></td>
        <td style="color:${isBest ? 'var(--green)' : ''}">${row.rmse?.toFixed(4)}</td>
        <td>${row.mae?.toFixed(4)}</td>
        <td>${row.r2?.toFixed(4)}</td>
        <td>${(row.nrmse * 100)?.toFixed(1)}%</td>
        <td>${row.smape?.toFixed(1)}%</td>
        <td>${fmtSkill(row.skill_vs_persistence)}</td>
        <td>${fmtSkill(row.skill_vs_climatology)}</td>
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
    const fc  = WP.arenaCache.demand;
    const fcH = WP.arenaCache.demand_hourly;
    if (!fc?.projection && !fcH?.raw_values) return null;
    // Para horario, raw_values vienen del cache horario; projection del anual.
    const rawValues = (key === 'demand_hourly')
      ? (fcH?.raw_values || fc?.raw_values || null)
      : (fc?.raw_values || null);
    return {
      projection: fc?.projection || {}, bestModel: fc?.best_model || (fcH?.best_model),
      rawValues, annualKind: fc?.annual_kind || 'sum',
      ylabel: 'Demanda (MW)', unitLabel: 'MW', decimals: 1,
      scaler: v => v, filename: `forecast_studio_${key}_future_25y.csv`,
      source: key,
    };
  }

  if (key === 'pml' || key === 'pml_hourly') {
    const fc  = WP.arenaCache.pml;
    const fcH = WP.arenaCache.pml_hourly;
    const { sideKey, sideCache } = getPmlForecastStudioSide(fc);
    const { sideCache: sideCacheH } = getPmlForecastStudioSide(fcH);
    if (!sideCache?.projection && !sideCacheH?.raw_values) return null;
    const rawValues = (key === 'pml_hourly')
      ? (sideCacheH?.raw_values || sideCache?.raw_values || null)
      : (sideCache?.raw_values || null);
    return {
      projection: sideCache?.projection || {}, bestModel: sideCache?.best_model || sideCacheH?.best_model,
      rawValues, annualKind: sideCache?.annual_kind || 'sum',
      ylabel: `PML ($/kWh) — ${sideKey || 'serie'}`, unitLabel: '$/kWh', decimals: 4,
      scaler: v => v, filename: `forecast_studio_${key}_${sideKey || 'series'}_future_25y.csv`,
      source: `${key}_${sideKey || 'series'}`,
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
  try { renderForecastStatsCard(key); } catch (e) {}
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

// ─── Tabla estadística por sub-tab (Forecast Studio) ────────────────────────
// Estadísticos: tendencia central + dispersión sobre observado, ajustado y
// proyección futura cuando estén disponibles.
function _statsBlock(values, label) {
  const arr = (values || []).map(v => +v).filter(v => Number.isFinite(v));
  if (!arr.length) return { label, n: 0 };
  const sorted = [...arr].sort((a, b) => a - b);
  const sum = arr.reduce((s, v) => s + v, 0);
  const n = arr.length;
  const mean = sum / n;
  const median = n % 2 ? sorted[(n-1)/2] : (sorted[n/2-1] + sorted[n/2]) / 2;
  const std = Math.sqrt(arr.reduce((s, v) => s + (v - mean) ** 2, 0) / Math.max(1, n - 1));
  const q = (p) => sorted[Math.min(n - 1, Math.max(0, Math.floor((p/100) * n)))];
  const min = sorted[0], max = sorted[n - 1];
  const p10 = q(10), p25 = q(25), p75 = q(75), p90 = q(90);
  const iqr = p75 - p25;
  const cv = mean !== 0 ? (std / Math.abs(mean)) * 100 : null;
  // Skewness (Fisher-Pearson)
  const skew = std > 0 ? arr.reduce((s, v) => s + ((v - mean) / std) ** 3, 0) / n : null;
  return { label, n, mean, median, std, min, max, p10, p90, iqr, cv, skew };
}

function ensureForecastStatsCard(key) {
  const tab = document.getElementById(`fs-${key}-tab`);
  if (!tab) return null;
  let card = document.getElementById(`fs-${key}-stats-card`);
  if (card) return card;
  card = document.createElement('div');
  card.id = `fs-${key}-stats-card`;
  card.className = 'card mt-3';
  card.innerHTML = `
    <div class="card-header">
      <span class="card-title">Parámetros estadísticos</span>
      <span class="cbadge" id="fs-${key}-stats-badge" style="margin-left:.4rem">tendencia central · dispersión</span>
    </div>
    <div class="card-body" style="overflow-x:auto">
      <table class="tbl" id="fs-${key}-stats-tbl">
        <thead><tr>
          <th>Serie</th><th>N</th><th>Media</th><th>Mediana</th><th>σ</th>
          <th>Mín</th><th>Máx</th><th>P10</th><th>P90</th><th>IQR</th>
          <th>CV %</th><th>Skew</th>
        </tr></thead>
        <tbody><tr><td colspan="12" class="ks" style="text-align:center;padding:1rem">Carga datos para ver estadísticos.</td></tr></tbody>
      </table>
    </div>
  `;
  tab.appendChild(card);
  return card;
}

function renderForecastStatsCard(key) {
  ensureForecastStatsCard(key);
  const tbody = document.querySelector(`#fs-${key}-stats-tbl tbody`);
  if (!tbody) return;

  // Recolectar series disponibles desde el cache correspondiente
  const series = [];
  const cache = (key === 'wind') ? WP.arenaCache.wind
              : (key === 'demand' || key === 'demand_hourly') ? (WP.arenaCache[key] || WP.arenaCache.demand)
              : (key === 'pml' || key === 'pml_hourly') ? (WP.arenaCache[key] || WP.arenaCache.pml)
              : (key === 'interest') ? WP.arenaCache.interest
              : (key === 'fx') ? WP.arenaCache.fx
              : null;
  if (!cache) {
    tbody.innerHTML = `<tr><td colspan="12" class="ks" style="text-align:center;padding:1rem">Aún no hay datos cargados ni modelo entrenado.</td></tr>`;
    return;
  }

  // Observado: raw_values o predictions.actual
  const observed = cache.raw_values || cache.predictions?.actual || cache.observed || null;
  if (observed) series.push(_statsBlock(observed, 'Observado (histórico horario)'));

  // Predicho/ajustado del best model (si está disponible)
  const best = cache.best_model;
  const fittedMap = cache.fitted || cache.models || {};
  const fittedBest = best && fittedMap[best];
  const fittedVals = fittedBest?.fitted_values || fittedBest?.predicted_values || cache.predictions?.predicted || null;
  if (fittedVals) series.push(_statsBlock(fittedVals, `Ajustado · ${best || 'best'}`));

  // Proyección futura (si key es _hourly, expandimos rawValues con factor anual)
  const cfg = getForecastFutureConfig(key);
  if (cfg?.projection && cfg.bestModel && cfg.projection[cfg.bestModel]?.values?.length) {
    series.push(_statsBlock(cfg.projection[cfg.bestModel].values, `Proyección 25a · ${cfg.bestModel}`));
  }

  if (!series.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="ks" style="text-align:center;padding:1rem">Sin series listas para estadísticos.</td></tr>`;
    return;
  }
  const fmt = (v, d=2) => (v == null || !Number.isFinite(v)) ? '—' : (Math.abs(v) >= 1000 ? v.toFixed(0) : v.toFixed(d));
  tbody.innerHTML = series.map(s => `
    <tr>
      <td><b>${s.label}</b></td>
      <td>${s.n.toLocaleString()}</td>
      <td>${fmt(s.mean)}</td>
      <td>${fmt(s.median)}</td>
      <td>${fmt(s.std)}</td>
      <td>${fmt(s.min)}</td>
      <td>${fmt(s.max)}</td>
      <td>${fmt(s.p10)}</td>
      <td>${fmt(s.p90)}</td>
      <td>${fmt(s.iqr)}</td>
      <td>${fmt(s.cv, 1)}</td>
      <td>${fmt(s.skew, 2)}</td>
    </tr>`).join('');
}

function renderForecastMainChart(r, prefix='fs-wind') {
  // Refresh stats card siempre que se renderice el main chart
  const k = (prefix || 'fs-wind').replace(/^fs-/, '');
  try { renderForecastStatsCard(k); } catch (e) {}
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
    ws_height_m: wsH(),
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
      ws_height_m: wsH(),
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
      ws_height_m: wsH(),
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

// ─── TECNOECONÓMICO MODULE ────────────────────────────────────────────────────
function bindTechno() {
  document.getElementById('btn-calc-techno').addEventListener('click', calcTechnoEconomic);
  document.querySelectorAll('[data-te-scenario]').forEach(btn => {
    btn.addEventListener('click', () => technoSwitchScenario(btn.dataset.teScenario));
  });
  // Modelo económico: refrescar badge total CAPEX % al editar cualquier input
  ['cm-turbines-pct','cm-civil-pct','cm-elec-pct','cm-install-pct','cm-eng-pct','cm-misc-pct']
    .forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('input', technoUpdatePctTotalBadge);
    });
  technoUpdatePctTotalBadge();
  // populate turbine select from API
  loadTurbineList().then(() => {
    const sel = document.getElementById('turbine-real-group');
    if (!sel) return;
    sel.querySelectorAll('option').forEach(opt => {
      const clone = opt.cloneNode(true);
      document.getElementById('te-turbine').appendChild(clone);
    });
  });
  technoRefreshScenarioView();
  const cmpBtn = document.getElementById('btn-te-compare');
  if (cmpBtn) cmpBtn.addEventListener('click', renderTechnoCompare);
  const cmpTurb = document.getElementById('btn-te-compare-turbines');
  if (cmpTurb) cmpTurb.addEventListener('click', compareTurbinesTE);
  const levBtn = document.getElementById('btn-lev-from-scenario');
  if (levBtn) levBtn.addEventListener('click', () => showFinancingModal(WP.technoActiveScenario));
  document.getElementById('btn-clear-financing')?.addEventListener('click', clearLeveraged);
  document.getElementById('fin-modal-cancel')?.addEventListener('click', closeFinancingModal);
  document.getElementById('financing-modal-backdrop')?.addEventListener('click', closeFinancingModal);
  document.getElementById('btn-calc-sensitivity')?.addEventListener('click', calcSensitivity);

  // Toggle de % pérdidas eléctricas finales — re-escala AEP/VAN/LCOE/flujos en cliente
  const lossEn  = document.getElementById('te-final-loss-enable');
  const lossPct = document.getElementById('te-final-loss-pct');
  if (lossEn) {
    lossEn.checked = WP.technoFinalLoss.enabled;
    lossEn.addEventListener('change', () => {
      WP.technoFinalLoss.enabled = lossEn.checked;
      if (lossPct) lossPct.disabled = !lossEn.checked;
      onTechnoLossChange();
    });
  }
  if (lossPct) {
    lossPct.value = WP.technoFinalLoss.pct;
    lossPct.disabled = !WP.technoFinalLoss.enabled;
    lossPct.addEventListener('input', () => {
      const v = parseFloat(lossPct.value);
      WP.technoFinalLoss.pct = Number.isFinite(v) ? Math.max(0, Math.min(30, v)) : 0;
      if (WP.technoFinalLoss.enabled) onTechnoLossChange();
    });
  }

  // Escalamiento de exportación — re-render proyecto al cambiar (no requiere API)
  ['te-price-escalation', 'te-inflation'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('input', () => {
        // Re-renderiza KPIs/flujos del proyecto. Leveraged se mantiene con NOI
        // del año 1 (asunción conservadora — el escalamiento aplica al proyecto).
        if (technoCurrentScenario()?.result) renderTechnoScenarioResult();
      });
    }
  });

  // Selectores de modo (Fijo / 8760) — actualizar hints en tiempo real
  ['te-price-mode', 'te-rate-mode', 'te-fx-mode'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', technoUpdateModeHints);
  });
  // Recalcular hints al cambiar FX manual (afecta conversión PML)
  ['te-fx', 'te-price', 'te-rate', 'te-years'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', () => technoUpdateModeHints());
  });
  technoUpdateModeHints();

  // Sync TOPSIS winner on TE tab activation (in case TOPSIS ran before)
  document.querySelectorAll('.nav-btn[data-module="techno"]').forEach(btn => {
    btn.addEventListener('click', () => {
      requestAnimationFrame(() => syncTopsisWinnerToModules());
    });
  });
}

async function onTechnoLossChange() {
  // Re-render escenario con la analysis ajustada
  renderTechnoScenarioResult();
  // Si el escenario activo tiene financiamiento calculado, recalcular con cifras ajustadas
  const sc = technoCurrentScenario();
  if (sc?.leveraged && sc?.result?.analysis) {
    await calcLeveragedForScenario(WP.technoActiveScenario, WP.levParams);
  }
}

/** TIR de proyecto (sin apalancamiento) por bisección sobre [-CAPEX, CF1..CFn]. */
function _projectIRR(cashflowTable, capexUsd) {
  if (!cashflowTable?.length || !(capexUsd > 0)) return null;
  const cfs = [-capexUsd, ...cashflowTable.map(r => Number(r.cashflow) || 0)];
  const npv = r => cfs.reduce((acc, cf, t) => acc + cf / Math.pow(1 + r, t), 0);
  let lo = -0.9999, hi = 9.99;
  let fLo = npv(lo), fHi = npv(hi);
  if (!Number.isFinite(fLo) || !Number.isFinite(fHi) || fLo * fHi > 0) return null;
  for (let i = 0; i < 256; i++) {
    const mid = (lo + hi) / 2;
    if (Math.abs(hi - lo) < 1e-9) return mid * 100;
    const fMid = npv(mid);
    if (fMid * fLo < 0) { hi = mid; fHi = fMid; }
    else                { lo = mid; fLo = fMid; }
  }
  return ((lo + hi) / 2) * 100;
}

/** Discounted Payback Period: primer año donde el acumulado de flujos en VP cubre la inversión.
 *  Usa interpolación lineal dentro del año. */
function _projectDPBP(cashflowTable, capexUsd) {
  if (!cashflowTable?.length || !(capexUsd > 0)) return null;
  let cum = -capexUsd;
  for (const row of cashflowTable) {
    const pv = Number(row.pv_cashflow) || 0;
    const prev = cum;
    cum += pv;
    if (cum >= 0 && prev < 0 && pv > 0) {
      return Math.round((row.year - 1 + (-prev) / pv) * 10) / 10;
    }
    if (cum >= 0) return row.year;
  }
  return null;  // no se recupera dentro del horizonte
}

/** DPBP del equity: suma equity_cf descontados al equity rate hasta cubrir el capital propio. */
function _equityDPBP(leveraged, equityRate) {
  if (!leveraged?.cashflow_table?.length) return null;
  const equity0 = Number(leveraged.equity_usd) || 0;
  if (!(equity0 > 0)) return null;
  let cum = -equity0;
  for (const r of leveraged.cashflow_table) {
    const pv = (Number(r.equity_cf) || 0) / Math.pow(1 + equityRate, r.year);
    const prev = cum;
    cum += pv;
    if (cum >= 0 && prev < 0 && pv > 0) {
      return Math.round((r.year - 1 + (-prev) / pv) * 10) / 10;
    }
    if (cum >= 0) return r.year;
  }
  return null;
}

/** Render de la tarjeta resumen "Indicadores de retorno y viabilidad". */
function renderTechnoSummaryCard(adjAnalysis, leveraged) {
  const card = document.getElementById('te-summary-card');
  if (!card) return;
  if (!adjAnalysis) { card.style.display = 'none'; return; }
  card.style.display = '';

  const fmtUsd = n => (n != null && Number.isFinite(n))
    ? (n >= 0 ? '+' : '−') + ' $' + Math.abs(Math.round(n)).toLocaleString('es-MX') : '—';
  const fmtPct = n => (n != null && Number.isFinite(n)) ? n.toFixed(2) + ' %' : '—';
  const fmtYrs = n => (n != null && Number.isFinite(n)) ? n.toFixed(1) + ' años' : '> proyecto';

  const capex   = adjAnalysis.capex?.total_capex_usd || 0;
  const projIRR = _projectIRR(adjAnalysis.cashflow_table, capex);
  const projDPBP= _projectDPBP(adjAnalysis.cashflow_table, capex);

  const badge = document.getElementById('te-summary-mode-badge');
  const thead = card.querySelector('thead');

  if (leveraged) {
    if (badge) badge.textContent = 'proyecto + financiamiento';
    if (thead) thead.innerHTML =
      '<tr><th>Indicador</th>' +
      '<th style="text-align:right">Sin financiamiento</th>' +
      '<th style="text-align:right">Con financiamiento (equity)</th></tr>';
    const eqRate = (leveraged.inputs?.equity_discount_rate_pct
                    ?? WP.levParams.equity_discount_rate_pct ?? 12) / 100;
    const rows = [
      ['VAN / VPN',                  fmtUsd(adjAnalysis.npv_usd),       fmtUsd(leveraged.leveraged_npv_usd)],
      ['TIR',                        fmtPct(projIRR),                   fmtPct(leveraged.leveraged_irr_pct)],
      ['Retorno simple',             fmtYrs(adjAnalysis.payback_years), fmtYrs(leveraged.equity_payback_years)],
      ['Retorno descontado (DPBP)',  fmtYrs(projDPBP),                  fmtYrs(_equityDPBP(leveraged, eqRate))],
    ];
    document.getElementById('te-summary-tbody').innerHTML = rows.map(([l, a, b]) =>
      `<tr><td>${l}</td>
        <td style="text-align:right;font-family:var(--font-mono),monospace">${a}</td>
        <td style="text-align:right;font-family:var(--font-mono),monospace">${b}</td></tr>`
    ).join('');
  } else {
    if (badge) badge.textContent = 'sólo proyecto';
    if (thead) thead.innerHTML =
      '<tr><th>Indicador</th><th style="text-align:right">Valor</th></tr>';
    const rows = [
      ['VAN / VPN',                  fmtUsd(adjAnalysis.npv_usd)],
      ['TIR del proyecto',           fmtPct(projIRR)],
      ['Retorno simple',             fmtYrs(adjAnalysis.payback_years)],
      ['Retorno descontado (DPBP)',  fmtYrs(projDPBP)],
    ];
    document.getElementById('te-summary-tbody').innerHTML = rows.map(([l, v]) =>
      `<tr><td>${l}</td>
        <td style="text-align:right;font-family:var(--font-mono),monospace">${v}</td></tr>`
    ).join('');
  }
}

/** Devuelve una analysis "ajustada" tras aplicar:
 *    1) % pérdidas finales al AEP (si el toggle está activo)
 *    2) tasa anual de escalamiento de exportación de electricidad (input `te-price-escalation`)
 *  No muta el original. Si ambos parámetros son 0, devuelve `a` tal cual. */
function technoAdjustedAnalysis(a) {
  if (!a) return a;
  const ls      = WP.technoFinalLoss;
  const lossPct = ls?.enabled ? Math.max(0, Number(ls.pct) || 0) : 0;
  const escPct  = parseFloat(document.getElementById('te-price-escalation')?.value) || 0;
  const inflInput = parseFloat(document.getElementById('te-inflation')?.value);
  const inflPct = Number.isFinite(inflInput) ? Math.max(0, inflInput) : (Number(a.annual_inflation_pct) || 0);
  const baseInflPct = Number(a.annual_inflation_pct) || 0;
  if (lossPct <= 0 && escPct <= 0 && Math.abs(inflPct - baseInflPct) < 1e-9) return a;

  const factor     = 1 - lossPct / 100;
  const esc        = escPct / 100;
  const infl       = inflPct / 100;
  const N          = a.project_years || 20;
  const capexTotal = a.capex?.total_capex_usd || 0;
  const opex       = a.annual_opex_usd || 0;
  const baseRev    = (a.annual_revenue_usd || 0) * factor;   // revenue año 1 (post-pérdidas)

  const adj = JSON.parse(JSON.stringify(a));
  adj.annual_energy_kwh   = Math.round((a.annual_energy_kwh || 0) * factor);
  adj.annual_energy_mwh   = Math.round((a.annual_energy_mwh || 0) * factor * 10) / 10;
  adj.annual_revenue_usd  = Math.round(baseRev);             // KPI = revenue año 1
  adj.annual_cashflow_usd = Math.round(baseRev - opex);
  adj.capacity_factor_pct = Math.round((a.capacity_factor_pct || 0) * factor * 100) / 100;

  // Si el backend usó series variables (price/rate/fx por año), reescalamos los
  // flujos existentes preservando la variación por año. Si no, reconstruimos
  // desde year-1 baseRev (compatibilidad histórica).
  const useExisting = !!a.variable_params_used && Array.isArray(a.cashflow_table) && a.cashflow_table.length;
  const rateSeries  = Array.isArray(a.discount_rate_series_pct) ? a.discount_rate_series_pct : null;
  let cum    = -capexTotal;
  let cumPv  = -capexTotal;
  let pbYears = null;
  adj.cashflow_table = [];
  let cumDF = 1;  // cumulative discount factor when rate series available
  for (let yr = 1; yr <= N; yr++) {
    let cfT;
    let opexT = opex * Math.pow(1 + infl, yr - 1);
    if (useExisting) {
      const baseRow = a.cashflow_table[yr - 1] || {};
      const baseOpexYr = Number.isFinite(Number(baseRow.opex)) ? Number(baseRow.opex) : opexT;
      opexT = opex * Math.pow(1 + infl, yr - 1);
      const baseRevYr = (Number(baseRow.cashflow) || 0) + baseOpexYr;  // recover revenue from cashflow + opex
      const revT = baseRevYr * factor * Math.pow(1 + esc, yr - 1);
      cfT = revT - opexT;
    } else {
      const revT = baseRev * Math.pow(1 + esc, yr - 1);
      cfT = revT - opexT;
    }
    // Discount: variable rate per year if series provided
    let pvT;
    if (rateSeries && rateSeries.length) {
      const rY = (Number(rateSeries[Math.min(yr - 1, rateSeries.length - 1)]) || 0) / 100;
      cumDF *= (1 + rY);
      pvT = cumDF > 0 ? cfT / cumDF : cfT;
    } else {
      const r = (a.discount_rate_pct || 0) / 100;
      pvT = cfT / Math.pow(1 + r, yr);
    }
    const prevCum = cum;
    cum   += cfT;
    cumPv += pvT;
    if (pbYears == null && cum >= 0 && prevCum < 0 && cfT > 0) {
      pbYears = Math.round((yr - 1 + (-prevCum) / cfT) * 10) / 10;
    }
    const baseRow = useExisting ? (a.cashflow_table[yr - 1] || {}) : {};
    adj.cashflow_table.push({
      year: yr,
      energy_mwh:        baseRow.energy_mwh,
      price_usd_kwh:     baseRow.price_usd_kwh,
      discount_rate_pct: baseRow.discount_rate_pct,
      fx_mxn_per_usd:    baseRow.fx_mxn_per_usd,
      opex:              Math.round(opexT),
      cashflow:          Math.round(cfT),
      pv_cashflow:       Math.round(pvT),
      cumulative:        Math.round(cum),
    });
  }
  adj.npv_usd       = Math.round(cumPv);
  adj.payback_years = pbYears;
  // VAE / LCOE — usar rate medio cuando hay serie
  const rMean = rateSeries && rateSeries.length
    ? rateSeries.reduce((s,v) => s + (Number(v) || 0), 0) / rateSeries.length / 100
    : (a.discount_rate_pct || 0) / 100;
  adj.vae_usd = rMean > 0
    ? Math.round(adj.npv_usd * rMean / (1 - Math.pow(1 + rMean, -N)))
    : Math.round(adj.npv_usd / N);

  // LCOE — métrica de costo, no se afecta por escalamiento; solo por pérdidas (vía energía)
  const totalEnergyKwh = adj.annual_energy_kwh * N;
  const totalOpexUsd   = adj.cashflow_table.reduce((s, row) => s + (Number(row.opex) || 0), 0);
  const totalCostsUsd  = capexTotal + totalOpexUsd;
  adj.lcoe_usd_kwh = totalEnergyKwh > 0
    ? Math.round((totalCostsUsd / totalEnergyKwh) * 10000) / 10000 : 0;

  adj.is_viable = adj.npv_usd > 0;
  adj._final_loss_applied_pct = lossPct;
  adj._price_escalation_pct   = escPct;
  adj.annual_inflation_pct    = Math.round(inflPct * 1000) / 1000;
  adj.total_opex_project_usd  = Math.round(totalOpexUsd);
  return adj;
}

function technoCurrentScenario() {
  return WP.technoScenarios[WP.technoActiveScenario] || WP.technoScenarios.real;
}

function technoCollectCostModel() {
  // Modelo económico central AERO OPEN CIM — % se ingresan como porcentajes (0–100), se convierten a fracción
  const pct = id => (parseFloat(document.getElementById(id)?.value) || 0) / 100.0;
  return {
    turbine_usd_per_kw: parseFloat(document.getElementById('cm-turbine-usd-kw')?.value) || 1800,
    turbines_pct:       pct('cm-turbines-pct'),
    civil_works_pct:    pct('cm-civil-pct'),
    electrical_pct:     pct('cm-elec-pct'),
    installation_pct:   pct('cm-install-pct'),
    engineering_pct:    pct('cm-eng-pct'),
    miscellaneous_pct:  pct('cm-misc-pct'),
    opex_pct_capex:     pct('cm-opex-pct'),
  };
}

function technoApplyCostModel(cm) {
  if (!cm) return;
  const set = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
  set('cm-turbine-usd-kw', cm.turbine_usd_per_kw);
  set('cm-turbines-pct',   (cm.turbines_pct * 100).toFixed(1));
  set('cm-civil-pct',      (cm.civil_works_pct * 100).toFixed(1));
  set('cm-elec-pct',       (cm.electrical_pct * 100).toFixed(1));
  set('cm-install-pct',    (cm.installation_pct * 100).toFixed(1));
  set('cm-eng-pct',        (cm.engineering_pct * 100).toFixed(1));
  set('cm-misc-pct',       (cm.miscellaneous_pct * 100).toFixed(1));
  set('cm-opex-pct',       (cm.opex_pct_capex * 100).toFixed(2));
  technoUpdatePctTotalBadge();
}

function technoUpdatePctTotalBadge() {
  const cm = technoCollectCostModel();
  const total = cm.turbines_pct + cm.civil_works_pct + cm.electrical_pct +
                cm.installation_pct + cm.engineering_pct + cm.miscellaneous_pct;
  const badge = document.getElementById('cm-pct-total-badge');
  if (!badge) return;
  const pct = (total * 100).toFixed(1);
  const ok = Math.abs(total - 1.0) < 0.005;
  badge.textContent = `${pct} %`;
  badge.style.color = ok ? '' : '#d97706';
}

function technoCollectInputs() {
  return {
    turbine: document.getElementById('te-turbine')?.value || 'SG_8.0_167',
    nTurbines: parseInt(document.getElementById('te-nturbines')?.value) || 10,
    demand: parseFloat(document.getElementById('te-demand')?.value) || 0,
    price: parseFloat(document.getElementById('te-price')?.value) || 0.07,
    priceEscalation: parseFloat(document.getElementById('te-price-escalation')?.value) || 0,
    inflation: parseFloat(document.getElementById('te-inflation')?.value) || 0,
    rate: parseFloat(document.getElementById('te-rate')?.value) || 8,
    years: parseInt(document.getElementById('te-years')?.value) || 20,
    fx: parseFloat(document.getElementById('te-fx')?.value) || 1,
    priceMode: document.getElementById('te-price-mode')?.value || 'fixed',
    rateMode:  document.getElementById('te-rate-mode')?.value  || 'fixed',
    fxMode:    document.getElementById('te-fx-mode')?.value    || 'fixed',
    costModel: technoCollectCostModel(),
  };
}

function technoApplyInputs(inputs) {
  if (!inputs) return;
  const set = (id, value) => { const el = document.getElementById(id); if (el && value != null) el.value = value; };
  set('te-turbine', inputs.turbine);
  set('te-nturbines', inputs.nTurbines);
  set('te-demand', inputs.demand);
  set('te-price', inputs.price);
  set('te-price-escalation', inputs.priceEscalation ?? 0);
  set('te-inflation', inputs.inflation ?? 0);
  set('te-rate', inputs.rate);
  set('te-years', inputs.years);
  set('te-fx', inputs.fx);
  set('te-price-mode', inputs.priceMode || 'fixed');
  set('te-rate-mode',  inputs.rateMode  || 'fixed');
  set('te-fx-mode',    inputs.fxMode    || 'fixed');
  if (inputs.costModel) technoApplyCostModel(inputs.costModel);
  technoUpdateModeHints();
}

// ─── 8760 / Predicción ML — series anuales y arrays horarios ──────────────────

function _arrMean(arr) {
  if (!Array.isArray(arr) || !arr.length) return null;
  let s = 0, n = 0;
  for (const v of arr) {
    const x = Number(v);
    if (Number.isFinite(x)) { s += x; n++; }
  }
  return n ? s / n : null;
}

/**
 * Devuelve el array crudo (raw_values) y un detector de unidad para PML.
 * Para PML: prefiere pml_hourly (8760), si no hay usa la cara compra/venta del cache anual.
 * Para interest/fx: usa cache.<key>.raw_values directamente.
 */
function _arenaRawValues(paramKey) {
  const c = WP.arenaCache || {};
  if (paramKey === 'price') {
    const { sideCache: hourlySide } = getPmlForecastStudioSide(c.pml_hourly);
    const h = hourlySide?.raw_values || c.pml_hourly?.raw_values;
    if (Array.isArray(h) && h.length) return { values: h, source: 'pml_hourly' };
    const { sideCache } = getPmlForecastStudioSide(c.pml);
    const side = sideCache || c.pml?.compra || c.pml?.venta || c.pml || null;
    const r = side?.raw_values;
    return Array.isArray(r) && r.length ? { values: r, source: 'pml_annual' } : null;
  }
  if (paramKey === 'rate') {
    const r = c.interest?.raw_values;
    return Array.isArray(r) && r.length ? { values: r, source: 'interest' } : null;
  }
  if (paramKey === 'fx') {
    const r = c.fx?.raw_values;
    return Array.isArray(r) && r.length ? { values: r, source: 'fx' } : null;
  }
  return null;
}

/**
 * Devuelve la proyección anual (25 años) para un parámetro desde Forecast Studio.
 * Para 'price' usa la cara activa de pml.
 */
function _arenaProjectionAnnual(paramKey) {
  const cfgKey = paramKey === 'price' ? 'pml'
              : paramKey === 'rate'  ? 'interest'
              : paramKey === 'fx'    ? 'fx' : null;
  if (!cfgKey) return null;
  const cfg = getForecastFutureConfig(cfgKey);
  if (!cfg) return null;
  const proj = cfg.projection || {};
  const model = cfg.bestModel && proj[cfg.bestModel] ? cfg.bestModel
              : Object.keys(proj).find(k => proj[k]?.annual?.length);
  const annual = model ? proj[model]?.annual : null;
  return Array.isArray(annual) && annual.length ? annual.map(Number) : null;
}

/**
 * Convierte un valor PML al sistema USD/kWh.
 * Heurística: si val > 50 → asumimos MXN/MWh; entre 1 y 50 → USD/MWh; <1 → USD/kWh ya.
 */
function _pmlToUsdKwh(val, fxScalar) {
  const v = Number(val);
  if (!Number.isFinite(v)) return null;
  if (v > 50) return v / (fxScalar > 0 ? fxScalar : 18) / 1000; // MXN/MWh → USD/kWh
  if (v > 1)  return v / 1000;                                  // USD/MWh → USD/kWh
  return v;                                                      // ya USD/kWh
}

/**
 * Construye la serie anual (length nYears) para un parámetro según mode/escenario.
 * - mode='fixed' → null (backend usa escalar)
 * - mode='arena' + scenario in {real, corrected} → media histórica para todos los años
 * - mode='arena' + scenario='ml' → año 1 = media histórica; años 2..N = proyección ML
 * Devuelve { series, baseline, hourly } donde:
 *   series = array length nYears (en unidades del backend) o null
 *   baseline = valor escalar año 1 (mostrado al usuario)
 *   hourly  = (solo para 'price') array de 8760 en USD/kWh para year-1 weighting
 */
function technoBuildParamSeries(paramKey, mode, scenarioKey, nYears, fxScalar) {
  if (mode !== 'arena') return { series: null, baseline: null, hourly: null };
  const raw = _arenaRawValues(paramKey);
  if (!raw) return { series: null, baseline: null, hourly: null };

  // baseline (year 1) en unidades del backend
  let baseline = null;
  let hourly   = null;
  if (paramKey === 'price') {
    const meanRaw = _arrMean(raw.values);
    baseline = _pmlToUsdKwh(meanRaw, fxScalar);
    // Hourly array (USD/kWh) cuando hay 8760 disponibles
    if (raw.source === 'pml_hourly') {
      hourly = raw.values.map(v => _pmlToUsdKwh(v, fxScalar)).map(v => Number.isFinite(v) ? v : baseline);
    }
  } else {
    baseline = _arrMean(raw.values);
  }
  if (baseline == null || !Number.isFinite(baseline)) {
    return { series: null, baseline: null, hourly: null };
  }

  const series = new Array(nYears).fill(baseline);
  if (scenarioKey === 'ml') {
    const annual = _arenaProjectionAnnual(paramKey);
    if (annual && annual.length) {
      // Años 2..N = annual[0..N-2] (proyección típicamente comienza en el año siguiente al base)
      for (let i = 1; i < nYears; i++) {
        let v = Number(annual[i - 1]);
        if (!Number.isFinite(v)) continue;
        if (paramKey === 'price') v = _pmlToUsdKwh(v, fxScalar);
        if (Number.isFinite(v)) series[i] = v;
      }
    }
  }
  return { series, baseline, hourly };
}

/**
 * Actualiza los hints debajo de cada selector mostrando la fuente de datos
 * cuando el modo es 'arena'.
 */
function technoUpdateModeHints() {
  const fxScalar = parseFloat(document.getElementById('te-fx')?.value) || 18;
  const scenarioKey = WP.technoActiveScenario || 'real';
  const fmt = (v, dec=4) => Number.isFinite(v) ? Number(v).toLocaleString('es-MX', {maximumFractionDigits: dec}) : '—';

  const cfg = [
    { key: 'price', mode: document.getElementById('te-price-mode')?.value, hintId: 'te-price-mode-hint', label: 'USD/kWh', dec: 5 },
    { key: 'rate',  mode: document.getElementById('te-rate-mode')?.value,  hintId: 'te-rate-mode-hint',  label: '%',       dec: 3 },
    { key: 'fx',    mode: document.getElementById('te-fx-mode')?.value,    hintId: 'te-fx-mode-hint',    label: 'MXN/USD', dec: 3 },
  ];
  cfg.forEach(c => {
    const el = document.getElementById(c.hintId);
    if (!el) return;
    if (c.mode !== 'arena') {
      // Reset to default hint
      if (c.key === 'fx') el.textContent = 'Análisis siempre en USD; FX solo muestra equivalente MXN bajo cada KPI.';
      else el.textContent = '';
      return;
    }
    const built = technoBuildParamSeries(c.key, 'arena', scenarioKey, parseInt(document.getElementById('te-years')?.value) || 20, fxScalar);
    if (!built.series) {
      const srcLabel = c.key === 'price' ? 'PML/PRECIO' : c.key.toUpperCase();
      el.innerHTML = `<span style="color:#d97706">⚠ No hay datos del Centro de modelaje para ${srcLabel}. Ejecuta Forecast Studio o entrena el modelo.</span>`;
      return;
    }
    const hAvg = built.series.length > 1
      ? built.series.slice(1).reduce((a,b)=>a+b,0) / (built.series.length - 1)
      : built.baseline;
    const tag = scenarioKey === 'ml'
      ? `Año 1: <strong>${fmt(built.baseline, c.dec)} ${c.label}</strong> (${built.hourly ? '8760 horario' : 'media hist.'}) · Años 2-${built.series.length}: <strong>${fmt(hAvg, c.dec)} ${c.label}</strong> (proy. ML)`
      : `Todos los años: <strong>${fmt(built.baseline, c.dec)} ${c.label}</strong> (media de ${built.hourly ? '8760 horario' : 'histórico'} Centro de modelaje)`;
    el.innerHTML = tag;
  });
}

function technoBestAnnualValue(key) {
  const cfg = getForecastFutureConfig(key);
  const proj = cfg?.projection || {};
  const model = cfg?.bestModel && proj[cfg.bestModel] ? cfg.bestModel : Object.keys(proj).find(k => proj[k]?.annual?.length);
  const arr = model ? proj[model]?.annual : null;
  if (!arr?.length) return null;
  const vals = arr.map(Number).filter(Number.isFinite);
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function technoWindAnnualSeries() {
  const cfg = getForecastFutureConfig('wind');
  const proj = cfg?.projection || {};
  const model = cfg?.bestModel && proj[cfg.bestModel] ? cfg.bestModel : Object.keys(proj).find(k => proj[k]?.annual?.length);
  const arr = model ? proj[model]?.annual : null;
  if (!arr?.length) return null;
  const vals = arr.map(Number).filter(Number.isFinite);
  return vals.length ? vals : null;
}

function technoScenarioDefaults(key) {
  const base = technoCollectInputs();
  if (key === 'ml' || key === 'corrected') base.years = 25;
  const demand = technoBestAnnualValue('demand');
  const pml = technoBestAnnualValue('pml');
  const rate = technoBestAnnualValue('interest');
  const fx = technoBestAnnualValue('fx');
  if (demand != null) base.demand = Math.round(demand);
  if (pml != null) base.price = Number(pml).toFixed(4);
  if (rate != null) base.rate = Number(rate).toFixed(2);
  if (fx != null) base.fx = Number(fx).toFixed(2);
  if (key === 'corrected' && PARQUE.result?.turbine?.name) base.turbine = PARQUE.result.turbine.name;
  if (key === 'corrected' && PARQUE.result?.turbines?.length) base.nTurbines = PARQUE.result.turbines.length;
  return base;
}

function technoSwitchScenario(key) {
  WP.technoScenarios[WP.technoActiveScenario].inputs = technoCollectInputs();
  WP.technoActiveScenario = key || 'real';
  document.querySelectorAll('[data-te-scenario]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.teScenario === WP.technoActiveScenario);
  });
  const scenario = technoCurrentScenario();
  technoApplyInputs(scenario.inputs || technoScenarioDefaults(WP.technoActiveScenario));
  technoUpdateModeHints();
  technoRefreshScenarioView();
}

function technoRefreshScenarioView() {
  const key = WP.technoActiveScenario;
  const badge = document.getElementById('te-scenario-badge');
  const status = document.getElementById('te-scenario-status');
  const meta = {
    real: ['Base', 'Viento real de la estación meteorológica cargado en Data Hub. Demanda/PML/FX pueden capturarse o venir del archivo cargado.'],
    ml: ['ML 25 años', 'Usa proyecciones futuras de Forecast Studio para viento, demanda, PML, tasa de interés y tipo de cambio cuando estén disponibles.'],
    corrected: ['ML + Kriging', 'Usa la predicción ML y prioriza el AEP/layout corregido de Parque/Kriging cuando existe una optimización vigente.'],
  };
  if (badge) badge.textContent = meta[key]?.[0] || 'Escenario';
  if (status) status.innerHTML = `<p class="text-muted" style="font-size:.8rem">${meta[key]?.[1] || ''}</p>`;
  renderTechnoLayoutPreview();
  renderTechnoScenarioResult();
}

function renderTechnoScenarioResult() {
  const scenario = technoCurrentScenario();
  const r = scenario.result;
  const levBtn  = document.getElementById('btn-lev-from-scenario');
  const lossBadge = document.getElementById('te-loss-applied-badge');
  if (!r) {
    document.getElementById('te-viable-badge').style.display = 'none';
    if (lossBadge) lossBadge.style.display = 'none';
    document.getElementById('te-kpi-grid').innerHTML = '<p class="text-muted" style="font-size:.8rem">Ejecuta el análisis para ver resultados.</p>';
    document.querySelector('#te-cashflow-table tbody').innerHTML = '';
    document.getElementById('te-dispatch-kpis').innerHTML = '<p class="text-muted" style="font-size:.8rem">Ejecuta el análisis para ver despacho horario.</p>';
    document.getElementById('te-dispatch-badge').textContent = '—';
    document.querySelector('#te-dispatch-table tbody').innerHTML = '';
    document.getElementById('badge-cp-max').textContent = '—';
    ['chart-capex', 'chart-cashflow', 'chart-cp', 'chart-te-dispatch'].forEach(destroyChart);
    if (levBtn) levBtn.style.display = 'none';
    renderTechnoLeveraged(null);
    renderTechnoSummaryCard(null, null);
    renderTechnoEnergyLab(null);
    return;
  }
  WP.technoResult = r;

  // Aplicar pérdidas finales al AEP si el toggle está activo
  const adjAnalysis = technoAdjustedAnalysis(r.analysis);
  const adjR = { ...r, analysis: adjAnalysis };

  // Badge informativo de ajustes aplicados (pérdidas y/o escalamiento)
  if (lossBadge) {
    const parts = [];
    if (WP.technoFinalLoss.enabled && WP.technoFinalLoss.pct > 0) {
      parts.push(`−${WP.technoFinalLoss.pct}% pérdidas`);
    }
    const escVal = parseFloat(document.getElementById('te-price-escalation')?.value) || 0;
    if (escVal > 0) parts.push(`+${escVal}%/año esc.`);
    const inflVal = parseFloat(document.getElementById('te-inflation')?.value) || 0;
    if (inflVal > 0) parts.push(`${inflVal}%/año inflación`);
    if (parts.length) {
      lossBadge.textContent = parts.join(' · ');
      lossBadge.style.display = '';
    } else {
      lossBadge.style.display = 'none';
    }
  }

  renderTechnoKPIs(adjR, scenario.leveraged);
  renderCapexChart(adjR.analysis?.capex);
  renderCashflowChart(adjR.analysis?.cashflow_table);
  renderTechnoDispatch(r.hourly_dispatch, r.wind_source);
  renderCpChart(r.cp);
  // Badge de alturas: medición vs hub — clave para detectar extrapolaciones inesperadas
  const hBadge = document.getElementById('te-height-badge');
  if (hBadge && r.ws_height_m_used != null && r.hub_height_m_used != null) {
    const wsH_val  = r.ws_height_m_used;
    const hubH_val = r.hub_height_m_used;
    const diff = Math.abs(wsH_val - hubH_val);
    const arrow = wsH_val < hubH_val ? '↑' : wsH_val > hubH_val ? '↓' : '=';
    const color = diff > 10 ? '#f59e0b' : 'var(--text-muted)';
    hBadge.innerHTML = `<span title="Altura medición → hub (extrapolación Hellmann)" style="color:${color}">` +
      `⚙ Medición ${wsH_val} m ${arrow} Hub ${hubH_val} m` +
      (diff > 30 ? ' ⚠' : '') + `</span>`;
    hBadge.style.display = '';
  } else if (hBadge) {
    hBadge.style.display = 'none';
  }
  // La tabla de flujos se construye en renderTechnoLeveraged si hay financiamiento (integrada);
  // si no, se renderiza aquí simple.
  if (!scenario.leveraged) renderCashflowTable(adjR.analysis?.cashflow_table);
  if (levBtn) levBtn.style.display = '';
  renderTechnoLeveraged(scenario.leveraged);
  renderTechnoSummaryCard(adjR.analysis, scenario.leveraged);
  renderTechnoEnergyLab(r.energy_lab);

  // Badge de viento de estación — solo visible en escenario "real" (sin predicción)
  const windBadge = document.getElementById('te-wind-station-badge');
  if (windBadge) {
    const ws = r.wind_source;
    if (WP.technoActiveScenario === 'real' && ws?.source === 'session') {
      const rawM  = ws.raw_mean_ms  != null ? ws.raw_mean_ms.toFixed(2)  + ' m/s' : '—';
      const hubM  = ws.hub_mean_ms  != null ? ws.hub_mean_ms.toFixed(2)  + ' m/s' : '—';
      const nRec  = ws.n_rows       != null ? ws.n_rows.toLocaleString() + ' reg.' : '';
      windBadge.textContent = `⬢ Estación · med. medición: ${rawM} · med. hub: ${hubM}${nRec ? ' · ' + nRec : ''}`;
      windBadge.style.display = '';
    } else {
      windBadge.style.display = 'none';
    }
  }

  const kTable = scenario.leveraged?.cashflow_table?.length
    ? scenario.leveraged.cashflow_table
    : adjR.analysis?.cashflow_table;
  renderKmeansLifecycle(kTable);
}

function renderTechnoLayoutPreview() {
  const el = document.getElementById('te-layout-preview');
  if (!el) return;
  const pts = PARQUE.optResult?.layout_xy_m || [];
  if (!pts.length) {
    el.innerHTML = '<span class="text-muted" style="font-size:.78rem">Optimiza Parque para ver el layout de referencia.</span>';
    return;
  }
  const xs = pts.map(p => Number(p[0])).filter(Number.isFinite);
  const ys = pts.map(p => Number(p[1])).filter(Number.isFinite);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const rangeX = maxX - minX || 1, rangeY = maxY - minY || 1;
  const nodes = pts.map((p, i) => {
    const x = 8 + ((Number(p[0]) - minX) / rangeX) * 84;
    const y = 10 + (1 - ((Number(p[1]) - minY) / rangeY)) * 80;
    return `<div title="T${i+1}" style="position:absolute;left:${x}%;top:${y}%;transform:translate(-50%,-50%);filter:drop-shadow(0 8px 10px rgba(0,0,0,.35));">
      <div style="width:30px;height:30px;border-radius:50%;background:linear-gradient(145deg,#f8fbff,#6fb7ff 55%,#0f5fa8);border:1px solid rgba(255,255,255,.85);display:flex;align-items:center;justify-content:center">
        <svg width="24" height="24" viewBox="0 0 48 48" aria-hidden="true">
          <line x1="24" y1="24" x2="24" y2="44" stroke="#0b1220" stroke-width="3" stroke-linecap="round"/>
          <circle cx="24" cy="24" r="3.8" fill="#0b1220"/>
          <path d="M24 22 C20 11 22 5 24 2 C28 8 29 15 26 22 Z" fill="#e0f2fe" stroke="#0b1220" stroke-width="1"/>
          <path d="M26 25 C37 22 43 24 46 27 C39 30 32 31 25 27 Z" fill="#dbeafe" stroke="#0b1220" stroke-width="1"/>
          <path d="M22 25 C15 34 9 36 5 36 C7 29 12 24 21 23 Z" fill="#bfdbfe" stroke="#0b1220" stroke-width="1"/>
        </svg>
      </div>
      <div style="position:absolute;left:50%;top:18px;width:2px;height:18px;background:linear-gradient(#dbeafe,#64748b);transform:translateX(-50%)"></div>
    </div>`;
  }).join('');
  el.innerHTML = `<div style="position:absolute;inset:0;background:linear-gradient(45deg,rgba(255,255,255,.03) 25%,transparent 25%,transparent 50%,rgba(255,255,255,.03) 50%,rgba(255,255,255,.03) 75%,transparent 75%);background-size:34px 34px"></div>${nodes}
    <span style="position:absolute;left:10px;bottom:8px;font-size:.68rem;color:var(--text-muted)">${pts.length} aerogeneradores · layout referencial</span>`;
}

async function calcTechnoEconomic() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  const scenarioKey = WP.technoActiveScenario;
  setStatus('Calculando análisis tecnoeconómico…');
  try {
    const inputs = technoCollectInputs();
    // For ML scenario: send full 25-year annual series so backend uses per-year AEP.
    // For corrected scenario: keep single mean (Kriging AEP overrides energy anyway).
    const windSeries = scenarioKey === 'ml' ? technoWindAnnualSeries() : null;
    const windMean = windSeries
      ? null
      : (scenarioKey === 'ml' || scenarioKey === 'corrected' ? technoBestAnnualValue('wind') : null);
    const parkAep = scenarioKey === 'corrected' ? PARQUE.result?.aep_mwh : null;

    // ── Modos 8760 / Predicción ML para precio, tasa y FX ─────────────────
    const fxScalar = Number.isFinite(inputs.fx) && inputs.fx > 0 ? inputs.fx : 18;
    const builtPrice = technoBuildParamSeries('price', inputs.priceMode, scenarioKey, inputs.years, fxScalar);
    const builtRate  = technoBuildParamSeries('rate',  inputs.rateMode,  scenarioKey, inputs.years, fxScalar);
    const builtFx    = technoBuildParamSeries('fx',    inputs.fxMode,    scenarioKey, inputs.years, fxScalar);

    // Si la modalidad arena no encontró datos, avisamos pero continuamos en modo fijo
    const missing = [];
    if (inputs.priceMode === 'arena' && !builtPrice.series) missing.push('precio (PML)');
    if (inputs.rateMode  === 'arena' && !builtRate.series)  missing.push('tasa (interés)');
    if (inputs.fxMode    === 'arena' && !builtFx.series)    missing.push('FX');
    if (missing.length) {
      toast(`Modo 8760 sin datos para: ${missing.join(', ')}. Carga el Centro de modelaje o usa Fijo.`, 'warn');
    }

    const body = {
      session_id:    WP.sessionId,
      scenario:      scenarioKey,
      n_turbines:    inputs.nTurbines,
      ws_height_m:   wsH(),
      annual_wind_series_ms: windSeries,
      annual_wind_mean_ms: windMean,
      annual_energy_mwh: Number.isFinite(Number(parkAep)) ? Number(parkAep) : null,
      turbine: { builtin_model: inputs.turbine },
      financial: {
        demand_mwh:           inputs.demand,
        energy_price_usd_kwh: inputs.price,
        discount_rate_pct:    inputs.rate,
        project_years:        inputs.years,
        usd_to_mxn:           inputs.fx,
        annual_inflation_pct: inputs.inflation,
        cost_model:           inputs.costModel,
        // Series por año (cuando mode='arena')
        energy_price_series_usd_kwh: builtPrice.series || null,
        discount_rate_series_pct:    builtRate.series  || null,
        usd_to_mxn_series:           builtFx.series    || null,
        // Año 1 horario (precio) → backend usa override de revenue ponderado
        price_hourly_year1_usd_kwh:  builtPrice.hourly || null,
        // Metadatos de modo (informativos)
        price_mode: inputs.priceMode,
        rate_mode:  inputs.rateMode,
        fx_mode:    inputs.fxMode,
      }
    };
    const r = await api('/api/energy/techno_economic', 'POST', body);
    WP.technoScenarios[scenarioKey].inputs = inputs;
    WP.technoScenarios[scenarioKey].result = r;
    WP.technoResult = r;
    renderTechnoScenarioResult();
    if (r.n_turbines_needed > 0 && r.n_turbines_needed !== inputs.nTurbines) {
      toast(`Demanda requiere ${r.n_turbines_needed} aerogeneradores`, 'info');
    }
    toast('Análisis completado', 'success');
    setStatus('Análisis tecnoeconómico completado.');
    // Auto-trigger risk analysis after TE completes
    setTimeout(() => calcSensitivity(), 400);
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    setStatus('Error en análisis tecnoeconómico.');
  }
}

// ─── ANÁLISIS DE RIESGO FINANCIERO ───────────────────────────────────────────
let _riskData = null;

async function calcSensitivity() {
  const sc = WP.technoScenarios[WP.technoActiveScenario];
  const r  = sc?.result;
  if (!r?.analysis || !WP.sessionId) return;
  const a   = r.analysis;
  // Prefer stored scenario inputs (consistent with the executed analysis).
  // Fall back to current form values only when no stored inputs exist.
  const stored = sc.inputs || {};
  const inp    = technoCollectInputs();
  const price  = stored.price || inp.price;
  const rate   = stored.rate  || inp.rate;
  const body = {
    session_id: WP.sessionId,
    turbine:    { builtin_model: stored.turbine || inp.turbine },
    n_turbines: stored.nTurbines || inp.nTurbines,
    base_annual_kwh: a.annual_energy_kwh || 0,
    financial: {
      energy_price_usd_kwh:    price,
      discount_rate_pct:       rate,
      project_years:           stored.years || inp.years,
      usd_to_mxn:              stored.fx    || inp.fx || 1,
      price_escalation_pct:    stored.priceEscalation ?? inp.priceEscalation ?? 0,
      annual_inflation_pct:    stored.inflation        ?? inp.inflation        ?? 0,
      cost_model:              stored.costModel || technoCollectCostModel(),
    },
  };
  // Si hay financiamiento activo, incluir params de apalancamiento para que
  // el análisis de riesgo use VAN equity en lugar de VAN proyecto.
  if (sc?.leveraged) body.leveraged = { ...WP.levParams };
  try {
    setStatus('Calculando análisis de riesgo…');
    const data = await api('/api/energy/sensitivity', 'POST', body);
    _riskData  = data;
    renderRiskSection(data);
    setStatus('Análisis de riesgo completado.');
  } catch (e) {
    setStatus('Error en análisis de riesgo: ' + e.message);
  }
}

function renderRiskSection(data) {
  if (!data) return;
  document.getElementById('te-risk-card').style.display = '';

  const metric  = document.querySelector('input[name="risk-metric"]:checked')?.value || 'npv';
  const base    = data.base || {};
  const infoEl  = document.getElementById('risk-base-info');
  const isLev   = !!WP.technoScenarios[WP.technoActiveScenario]?.leveraged;
  if (infoEl) infoEl.textContent =
    `Precio base: ${(base.energy_price_usd_kwh||0).toFixed(4)} USD/kWh · Tasa: ${(base.discount_rate_pct||0).toFixed(2)}% · ` +
    `VAN${isLev ? ' equity' : ''}: $${Math.round(base.npv_usd||0).toLocaleString()} · LCOE: ${(base.lcoe_usd_kwh||0).toFixed(4)} · Payback: ${(base.payback_years||0).toFixed(1)} a`;

  renderTornadoChart(data.tornado || [], metric);
  renderHeatmapCanvas(data.heatmap);
  if (data.regimes) renderRegimeChart(data.regimes);

  // Re-render when metric toggle changes
  document.querySelectorAll('input[name="risk-metric"]').forEach(el => {
    el.onchange = () => renderTornadoChart(data.tornado || [], el.value);
  });
}

function renderTornadoChart(tornado, metric) {
  const canvas = document.getElementById('risk-tornado-chart');
  if (!canvas) return;
  if (WP.charts.riskTornado) { WP.charts.riskTornado.destroy(); WP.charts.riskTornado = null; }

  const loKey = metric === 'lcoe' ? 'low_lcoe_pct'  : 'low_npv_pct';
  const hiKey = metric === 'lcoe' ? 'high_lcoe_pct' : 'high_npv_pct';
  const unit  = metric === 'lcoe' ? 'ΔLCOE %' : 'ΔVAN %';

  const labels = tornado.map(t => t.label);
  const loVals = tornado.map(t => t[loKey] || 0);
  const hiVals = tornado.map(t => t[hiKey] || 0);

  WP.charts.riskTornado = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Reducción (peor escenario)', data: loVals.map(v => v < 0 ? v : 0),
          backgroundColor: 'rgba(239,68,68,.75)', borderRadius: 3 },
        { label: 'Aumento (mejor escenario)', data: hiVals.map(v => v > 0 ? v : 0),
          backgroundColor: 'rgba(34,197,94,.75)', borderRadius: 3 },
        { label: 'Cambio positivo bajo', data: loVals.map(v => v > 0 ? v : 0),
          backgroundColor: 'rgba(34,197,94,.45)', borderRadius: 3 },
        { label: 'Cambio negativo alto', data: hiVals.map(v => v < 0 ? v : 0),
          backgroundColor: 'rgba(239,68,68,.45)', borderRadius: 3 },
      ],
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.raw > 0 ? '+' : ''}${ctx.raw.toFixed(2)}%`,
          },
        },
        annotation: {
          annotations: { zeroline: { type: 'line', x: 0, borderColor: 'rgba(255,255,255,.3)', borderWidth: 1 } }
        },
      },
      scales: {
        x: { stacked: true, grid: { color: gridColor() }, ticks: { callback: v => v + '%', font: { size: 9 } }, title: { display: true, text: unit, font: { size: 9 }, color: mut() } },
        y: { stacked: true, grid: { display: false }, ticks: { font: { size: 9 } } },
      },
    },
  });
}

function renderHeatmapCanvas(hm) {
  if (!hm?.data?.length) return;
  const canvas = document.getElementById('risk-heatmap-canvas');
  if (!canvas) return;
  const rows  = hm.data.length;
  const cols  = hm.data[0].length;
  const cellW = 62;
  const cellH = 27;
  const padL  = 66;
  const padT  = 24;
  const padB  = 18;
  canvas.width  = padL + cols * cellW;
  canvas.height = padT + rows * cellH + padB;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const bri = hm.base_rate_idx  ?? 3;  // base rate column index
  const bpi = hm.base_price_idx ?? 4;  // base price row index

  const flat = hm.data.flat();
  const vMin = Math.min(...flat);
  const vMax = Math.max(...flat);

  function cellColor(v) {
    if (vMax === vMin) return 'rgba(100,116,139,.5)';
    const t = (v - vMin) / (vMax - vMin);
    return `rgba(${Math.round(239 + (34-239)*t)},${Math.round(68 + (197-68)*t)},${Math.round(68 + (94-68)*t)},0.82)`;
  }

  // Column headers (rate)
  hm.rate_labels.forEach((lbl, ci) => {
    ctx.font = ci === bri ? 'bold 9px Inter,system-ui' : '9px Inter,system-ui';
    ctx.fillStyle = ci === bri ? '#4DA3FF' : '#94a3b8';
    ctx.textAlign = 'center';
    ctx.fillText(lbl, padL + ci * cellW + cellW / 2, padT - 7);
  });

  // Row labels (price)
  hm.price_labels.forEach((lbl, ri) => {
    ctx.font = ri === bpi ? 'bold 9px Inter,system-ui' : '9px Inter,system-ui';
    ctx.fillStyle = ri === bpi ? '#4DA3FF' : '#94a3b8';
    ctx.textAlign = 'right';
    ctx.fillText(lbl, padL - 5, padT + ri * cellH + cellH / 2 + 3);
  });

  // Cells
  ctx.textAlign = 'center';
  hm.data.forEach((row, ri) => {
    row.forEach((val, ci) => {
      const x = padL + ci * cellW;
      const y = padT + ri * cellH;
      ctx.fillStyle = cellColor(val);
      ctx.fillRect(x, y, cellW - 1, cellH - 1);
      // Base scenario cell — thicker accent border
      if (ri === bpi && ci === bri) {
        ctx.strokeStyle = '#4DA3FF';
        ctx.lineWidth   = 2;
        ctx.strokeRect(x + 1, y + 1, cellW - 3, cellH - 3);
        ctx.lineWidth = 1;
      }
      ctx.fillStyle = Math.abs(val) > Math.abs(vMax) * 0.3 ? '#fff' : '#374151';
      ctx.font = (ri === bpi && ci === bri) ? 'bold 8.5px Inter,system-ui' : '8.5px Inter,system-ui';
      ctx.fillText(val.toFixed(2), x + cellW / 2, y + cellH / 2 + 3);
    });
  });

  // Subtitle: base values used
  if (hm.base_price != null && hm.base_rate != null) {
    ctx.font = '8px Inter,system-ui';
    ctx.fillStyle = '#64748b';
    ctx.textAlign = 'left';
    ctx.fillText(
      `Base: ${hm.base_price.toFixed(4)} USD/kWh · ${hm.base_rate.toFixed(2)}%  (celda resaltada)`,
      padL, padT + rows * cellH + 13);
  }
}

function renderRegimeChart(regimes) {
  if (!regimes) return;
  document.getElementById('risk-regimes-wrap').style.display = '';
  document.getElementById('risk-regime-col').textContent = regimes.column || '';

  // Stat badges
  const statsEl = document.getElementById('risk-regime-stats');
  if (statsEl) {
    statsEl.innerHTML = (regimes.stats || []).map(s => `
      <div style="background:${s.color}22;border:1px solid ${s.color}55;border-radius:8px;padding:.3rem .65rem;font-size:.75rem">
        <strong style="color:${s.color}">${s.name}</strong>
        <span style="color:var(--text-muted);margin-left:.35rem">μ=${s.mean} · ${s.pct}% del tiempo</span>
      </div>`).join('');
  }

  const canvas = document.getElementById('risk-regime-chart');
  if (!canvas) return;
  if (WP.charts.riskRegime) { WP.charts.riskRegime.destroy(); WP.charts.riskRegime = null; }

  const colors = ['#22c55e', '#f59e0b', '#ef4444'];
  const bgColors = regimes.ts_regimes.map(r => colors[r] || '#94a3b8');

  WP.charts.riskRegime = new Chart(canvas, {
    type: 'bar',
    data: {
      labels:   regimes.ts_labels,
      datasets: [{
        label: regimes.column,
        data:  regimes.ts_values,
        backgroundColor: bgColors,
        borderWidth: 0,
        barPercentage: 1.0,
        categoryPercentage: 1.0,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => `${ctx.raw} · ${['Bajo','Medio','Alto'][regimes.ts_regimes[ctx.dataIndex]]}` } } },
      scales: {
        x: { display: false },
        y: { grid: { color: gridColor() }, ticks: { font: { size: 8 } } },
      },
    },
  });
}

function renderTechnoKPIs(r, leveraged = null) {
  const a = r.analysis;
  if (!a) return;

  // Viability badge reflects leveraged NPV when financing is active
  const isViable = leveraged
    ? (leveraged.leveraged_npv_usd != null ? leveraged.leveraged_npv_usd > 0 : a.is_viable)
    : a.is_viable;
  const badge = document.getElementById('te-viable-badge');
  badge.style.display = '';
  badge.textContent   = isViable ? '✓ VAN POSITIVO' : '✗ VAN NEGATIVO';
  badge.className     = `badge ${isViable ? 'badge--success' : 'badge--error'}`;

  // Badge de modo apalancado
  const levBadge = document.getElementById('te-lev-active-badge');
  if (levBadge) levBadge.style.display = leveraged ? '' : 'none';

  const fmt = n => n != null ? Math.abs(n).toLocaleString('es-MX', {maximumFractionDigits: 0}) : '—';
  const fx  = Number(a.fx_mxn_per_usd) || Number(document.getElementById('te-fx')?.value) || 1;
  const showMxn = fx && fx > 1.0001;
  const mxn = (usd) => showMxn && usd != null && Number.isFinite(usd)
    ? `≈ $${fmt(usd * fx)} MXN` : '';
  const signed = (n) => (n >= 0 ? '+' : '−') + ' $' + fmt(n);

  // Con financiamiento activo: métricas equity sustituyen las del proyecto
  // [label, valor, unidad, valor_usd_para_mxn]
  const items = leveraged ? [
    ['VAN (equity)',        signed(leveraged.leveraged_npv_usd),                                                      'USD',     leveraged.leveraged_npv_usd],
    ['LCOE',               '$' + a.lcoe_usd_kwh,                                                                     'USD/kWh', null],
    ['TIR equity',         leveraged.leveraged_irr_pct != null ? leveraged.leveraged_irr_pct + '%' : '—',            '',        null],
    ['Retorno equity',     leveraged.equity_payback_years != null ? leveraged.equity_payback_years + ' años' : '> proyecto', '', null],
    ['Factor de capacidad', a.capacity_factor_pct + '%',                                                              '',        null],
    ['Energía anual',      fmt(a.annual_energy_mwh) + ' MWh',                                                        '/parque', null],
    ['Ingresos anuales',   '$' + fmt(a.annual_revenue_usd),                                                          'USD/año', a.annual_revenue_usd],
    ['OPEX anual',         '$' + fmt(a.annual_opex_usd),                                                             'USD/año', a.annual_opex_usd],
    ['Capital equity',     '$' + fmt(leveraged.equity_usd),                                                          'USD',     leveraged.equity_usd],
    ['Área requerida',     fmt(a.capex?.area_ha) + ' ha',                                                            '',        null],
    ['Potencia instalada', a.capex?.installed_mw + ' MW',                                                            '',        null],
    ['N° aerogeneradores', a.capex?.n_turbines,                                                                       '',        null],
  ] : [
    ['VAN / NPV',           signed(a.npv_usd),                       'USD',     a.npv_usd],
    ['LCOE',                '$' + a.lcoe_usd_kwh,                    'USD/kWh', null],
    ['VAE',                 signed(a.vae_usd),                       'USD/año', a.vae_usd],
    ['Retorno simple',      a.payback_years ? a.payback_years + ' años' : '> proyecto', '', null],
    ['Factor de capacidad', a.capacity_factor_pct + '%',             '',        null],
    ['Energía anual',       fmt(a.annual_energy_mwh) + ' MWh',       '/parque', null],
    ['Ingresos anuales',    '$' + fmt(a.annual_revenue_usd),         'USD/año', a.annual_revenue_usd],
    ['OPEX anual',          '$' + fmt(a.annual_opex_usd),            'USD/año', a.annual_opex_usd],
    ['CAPEX total',         '$' + fmt(a.capex?.total_capex_usd),     'USD',     a.capex?.total_capex_usd],
    ['Área requerida',      fmt(a.capex?.area_ha) + ' ha',           '',        null],
    ['Potencia instalada',  a.capex?.installed_mw + ' MW',           '',        null],
    ['N° aerogeneradores',  a.capex?.n_turbines,                     '',        null],
  ];

  document.getElementById('te-kpi-grid').innerHTML =
    `<div class="te-kpi-grid">${items.map(([l, v, u, usdVal]) => {
      const mxnLine = mxn(usdVal);
      return `<div class="te-kpi-item">
        <div class="kpi-label">${l}</div>
        <div class="kpi-value">${v}<span style="font-size:.7rem;color:var(--text-muted);margin-left:4px">${u}</span></div>
        ${mxnLine ? `<div style="font-size:.7rem;color:var(--text-muted);margin-top:2px">${mxnLine}</div>` : ''}
      </div>`;
    }).join('')}</div>`;
}

function renderCapexChart(capex) {
  if (!capex) return;
  destroyChart('chart-capex');
  const ctx = document.getElementById('chart-capex')?.getContext('2d');
  if (!ctx) return;
  const labels = ['Aerogeneradores', 'Obras civiles', 'Sistema eléctrico', 'Instalación', 'Ingeniería + permisos', 'Misceláneos'];
  const values = [capex.turbines_total_usd, capex.civil_works_usd,
                  capex.electrical_usd, capex.installation_usd,
                  capex.engineering_usd, capex.miscellaneous_usd];
  const colors = [acc(), acc3(), acc2(), '#7f9b87', gold(), '#9F7AEA'];
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

// ─── K-Means ciclo de vida financiero ────────────────────────────────────────
function _kmeansRun(pts, k, maxIter = 150) {
  const n = pts.length, dims = pts[0].length;
  if (n < k) return { labels: pts.map((_, i) => i % k) };
  const mins = Array(dims).fill(Infinity), maxs = Array(dims).fill(-Infinity);
  pts.forEach(p => p.forEach((v, d) => { if (v < mins[d]) mins[d] = v; if (v > maxs[d]) maxs[d] = v; }));
  const norm = pts.map(p => p.map((v, d) => maxs[d] > mins[d] ? (v - mins[d]) / (maxs[d] - mins[d]) : 0));
  const sorted = [...norm].sort((a, b) => a[0] - b[0]);
  let centroids = Array.from({ length: k }, (_, ki) => sorted[Math.round(ki * (n - 1) / (k - 1))].slice());
  const d2 = (a, b) => a.reduce((s, v, i) => s + (v - b[i]) ** 2, 0);
  let labels = new Array(n).fill(0);
  for (let iter = 0; iter < maxIter; iter++) {
    const nl = norm.map(p => { let b = 0, bd = Infinity; centroids.forEach((c, ki) => { const dd = d2(p, c); if (dd < bd) { bd = dd; b = ki; } }); return b; });
    const changed = nl.some((l, i) => l !== labels[i]);
    labels = nl;
    if (!changed) break;
    centroids = Array.from({ length: k }, (_, ki) => {
      const m = norm.filter((_, i) => labels[i] === ki);
      return m.length ? Array.from({ length: dims }, (_, d) => m.reduce((s, p) => s + p[d], 0) / m.length) : centroids[ki];
    });
  }
  return { labels, norm };
}

function _kmeansSilhouette(norm, labels, k) {
  const n = norm.length;
  const edist = (a, b) => Math.sqrt(a.reduce((s, v, i) => s + (v - b[i]) ** 2, 0));
  let total = 0;
  for (let i = 0; i < n; i++) {
    const ki = labels[i];
    const mem = norm.filter((_, j) => labels[j] === ki && j !== i);
    const a = mem.length ? mem.reduce((s, p) => s + edist(norm[i], p), 0) / mem.length : 0;
    const b = Math.min(...Array.from({ length: k }, (_, kj) => {
      if (kj === ki) return Infinity;
      const oth = norm.filter((_, j) => labels[j] === kj);
      return oth.length ? oth.reduce((s, p) => s + edist(norm[i], p), 0) / oth.length : Infinity;
    }));
    total += (b - a) / Math.max(a, b || 1e-9);
  }
  return total / n;
}

function renderKmeansLifecycle(table) {
  const fmtUsd = n => '$' + Math.round(n).toLocaleString();
  const card = document.getElementById('te-kmeans-card');
  if (!card) return;
  if (!table?.length) { card.style.display = 'none'; return; }
  card.style.display = '';

  const k = 3;
  const pts = table.map(r => [Number(r.year), Number(r.pv_cashflow), Number(r.cumulative)]);
  const { labels, norm } = _kmeansRun(pts, k);

  // Remap so cluster 0 = earliest years, 2 = latest
  const meanYr = Array.from({ length: k }, (_, ki) => {
    const yrs = pts.filter((_, i) => labels[i] === ki).map(p => p[0]);
    return yrs.length ? yrs.reduce((s, v) => s + v, 0) / yrs.length : 0;
  });
  const order = [0, 1, 2].sort((a, b) => meanYr[a] - meanYr[b]);
  const remap = new Array(k); order.forEach((orig, ni) => { remap[orig] = ni; });
  const sl = labels.map(l => remap[l]);

  const sil = _kmeansSilhouette(norm, sl, k);

  const COLORS = ['#4DA3FF', '#22c55e', '#f59e0b'];
  const PHASES = ['Recuperación de inversión', 'Operación plena', 'Madurez tardía'];

  // Badges
  const silBadge = document.getElementById('te-kmeans-sil-badge');
  if (silBadge) {
    const ok = sil >= 0.5;
    silBadge.textContent = `Silhouette = ${sil.toFixed(3)}`;
    silBadge.style.background = ok ? 'rgba(34,197,94,.12)' : 'rgba(245,158,11,.12)';
    silBadge.style.color     = ok ? '#22c55e' : '#f59e0b';
    silBadge.style.border    = `1px solid ${ok ? 'rgba(34,197,94,.3)' : 'rgba(245,158,11,.3)'}`;
  }

  // Bar chart — VP flujo por año, coloreado por clúster
  destroyChart('kmeans-bar-chart');
  const barCtx = document.getElementById('kmeans-bar-chart')?.getContext('2d');
  if (barCtx) {
    WP.charts['kmeans-bar-chart'] = new Chart(barCtx, {
      type: 'bar',
      data: {
        labels: table.map(r => 'Año ' + r.year),
        datasets: [{ label: 'VP Flujo (USD)',
          data: table.map(r => r.pv_cashflow),
          backgroundColor: sl.map(l => COLORS[l] + '99'),
          borderColor: sl.map(l => COLORS[l]),
          borderWidth: 1, borderRadius: 3 }]
      },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: ctx => ` ${fmtUsd(ctx.parsed.y)}` } } },
        scales: {
          x: { grid: { color: gridColor() }, ticks: { font: { size: 9 } } },
          y: { grid: { color: gridColor() }, title: { display: true, text: 'USD (VP)', font: { size: 10 } } }
        } }
    });
  }

  // Scatter — año vs acumulado, por clúster
  destroyChart('kmeans-scatter-chart');
  const scCtx = document.getElementById('kmeans-scatter-chart')?.getContext('2d');
  if (scCtx) {
    WP.charts['kmeans-scatter-chart'] = new Chart(scCtx, {
      type: 'scatter',
      data: { datasets: Array.from({ length: k }, (_, ki) => ({
        label: PHASES[ki],
        data: table.filter((_, i) => sl[i] === ki).map(r => ({ x: r.year, y: r.cumulative })),
        backgroundColor: COLORS[ki] + 'cc', borderColor: COLORS[ki],
        pointRadius: 6, pointHoverRadius: 8, showLine: false,
      })) },
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, position: 'bottom',
            labels: { font: { size: 10 }, padding: 6 } },
          tooltip: { callbacks: { label: ctx => `Año ${ctx.parsed.x} · Acum. ${fmtUsd(ctx.parsed.y)}` } } },
        scales: {
          x: { grid: { color: gridColor() }, title: { display: true, text: 'Año', font: { size: 10 } } },
          y: { grid: { color: gridColor() }, title: { display: true, text: 'VP acumulado (USD)', font: { size: 10 } } }
        } }
    });
  }

  // Tabla resumen por clúster
  const tbody = document.getElementById('te-kmeans-tbody');
  if (tbody) {
    tbody.innerHTML = Array.from({ length: k }, (_, ki) => {
      const mem = table.filter((_, i) => sl[i] === ki);
      const yrs = mem.map(r => r.year);
      const meanPV  = mem.reduce((s, r) => s + r.pv_cashflow, 0) / mem.length;
      const meanCum = mem.reduce((s, r) => s + r.cumulative,  0) / mem.length;
      const dot = `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${COLORS[ki]};margin-right:5px;vertical-align:middle"></span>`;
      return `<tr>
        <td>${dot}${ki + 1}</td>
        <td>${mem.length}</td>
        <td>${Math.min(...yrs)}–${Math.max(...yrs)}</td>
        <td>${fmtUsd(meanPV)}</td>
        <td>${fmtUsd(meanCum)}</td>
        <td><span style="font-size:.75rem;color:${COLORS[ki]}">${PHASES[ki]}</span></td>
      </tr>`;
    }).join('');
  }

  // Métricas de calidad
  const metricsEl = document.getElementById('te-kmeans-metrics');
  if (metricsEl) {
    metricsEl.style.display = 'flex';
    const chip = (lbl, val) =>
      `<span style="font-size:.75rem;background:var(--bg-elevated);border:1px solid var(--border);padding:.2rem .65rem;border-radius:12px;color:var(--text-secondary)">` +
      `<span style="color:var(--text-muted)">${lbl}:</span> <strong style="color:var(--text-primary)">${val}</strong></span>`;
    metricsEl.innerHTML = [
      chip('Silhouette', sil.toFixed(3) + (sil >= 0.5 ? ' ✓' : '')),
      chip('Clusters K', k),
      chip('Años analizados', table.length),
      chip('Método', 'K-Means (determinístico)'),
    ].join('');
  }

  // Botón recalcular
  const btn = document.getElementById('btn-calc-kmeans');
  if (btn && !btn._wired) {
    btn._wired = true;
    btn.addEventListener('click', () => {
      const sc  = technoCurrentScenario();
      const adj = technoAdjustedAnalysis(sc?.result?.analysis);
      renderKmeansLifecycle(adj?.cashflow_table);
    });
  }
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

function renderTechnoDispatch(d, windSource = null) {
  const kpiEl = document.getElementById('te-dispatch-kpis');
  const badge = document.getElementById('te-dispatch-badge');
  const tbody = document.querySelector('#te-dispatch-table tbody');
  if (!d) {
    if (kpiEl) kpiEl.innerHTML = '<p class="text-muted" style="font-size:.8rem">Sin despacho horario disponible.</p>';
    if (badge) badge.textContent = '—';
    if (tbody) tbody.innerHTML = '';
    destroyChart('chart-te-dispatch');
    return;
  }
  const fmt = (v, dec = 0) => Number.isFinite(Number(v)) ? Number(v).toLocaleString('es-MX', { maximumFractionDigits: dec }) : '—';
  const loadSrc = d.load_source?.source === 'session_column'
    ? `Carga real: ${d.load_source.column}`
    : 'Carga sintética escalada';
  if (badge) badge.textContent = `${d.n_records || 0} datos · Δt=${d.dt_h || 1} h`;
  const items = [
    ['Carga total', fmt(d.total_load_mwh, 1) + ' MWh', loadSrc],
    ['Viento disponible', fmt(d.wind_available_mwh, 1) + ' MWh', windSource?.source || 'serie'],
    ['Energía servida', fmt(d.served_by_wind_mwh, 1) + ' MWh', 'wind → carga'],
    ['Déficit/importación', fmt(d.deficit_mwh, 1) + ' MWh', 'demanda no cubierta'],
    ['Excedente/curtailment', fmt(d.curtailed_mwh, 1) + ' MWh', `${fmt(d.curtailment_pct, 2)}%`],
    ['Cobertura horaria', fmt(d.coverage_pct, 2) + '%', `${d.years_detected || 1} año(s)`],
  ];
  if (kpiEl) {
    kpiEl.innerHTML = items.map(([l, v, u]) => `<div class="te-kpi-item">
      <div class="kpi-label">${l}</div>
      <div class="kpi-value">${v}<span style="font-size:.68rem;color:var(--text-muted);margin-left:4px">${u}</span></div>
    </div>`).join('');
  }
  if (tbody) {
    tbody.innerHTML = (d.annual || []).map(r => `<tr>
      <td>${r.year}</td>
      <td>${fmt(r.hours, 1)}</td>
      <td>${fmt(r.load_mwh, 2)}</td>
      <td>${fmt(r.wind_mwh, 2)}</td>
      <td>${fmt(r.served_mwh, 2)}</td>
      <td>${fmt(r.curtailed_mwh, 2)}</td>
      <td>${fmt(r.deficit_mwh, 2)}</td>
      <td>${fmt(r.coverage_pct, 2)}%</td>
      <td>${fmt(r.curtailment_pct, 2)}%</td>
      <td>${fmt(r.cf_pct, 2)}%</td>
    </tr>`).join('');
  }
  const tr = d.traces || {};
  destroyChart('chart-te-dispatch');
  const ctx = document.getElementById('chart-te-dispatch')?.getContext('2d');
  if (!ctx || !tr.load_kw?.length) return;
  WP.charts['chart-te-dispatch'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: tr.labels || tr.load_kw.map((_, i) => i),
      datasets: [
        { label: 'Carga', data: tr.load_kw, borderColor: ink(), backgroundColor: ink() + '10', fill: false, pointRadius: 0, borderWidth: 1.4 },
        { label: 'Viento disponible', data: tr.wind_kw, borderColor: acc(), backgroundColor: acc() + '22', fill: true, pointRadius: 0, borderWidth: 1.2 },
        { label: 'Servida', data: tr.served_kw, borderColor: acc3(), backgroundColor: acc3() + '16', fill: true, pointRadius: 0, borderWidth: 1 },
        { label: 'Déficit', data: tr.deficit_kw, borderColor: '#ef4444', backgroundColor: '#ef444422', fill: true, pointRadius: 0, borderWidth: 1 },
        { label: 'Excedente', data: tr.curtailed_kw, borderColor: gold(), backgroundColor: gold() + '20', fill: true, pointRadius: 0, borderWidth: 1 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: { legend: { labels: { font: { size: 10 }, boxWidth: 10 } } },
      scales: {
        x: { display: false },
        y: { title: { display: true, text: 'kW' }, grid: { color: gridColor() } },
      },
    },
  });
}

// ─── TECHNO ENERGY LAB INTEGRATION ───────────────────────────────────────────
function renderTechnoEnergyLab(el) {
  const sectionIds = ['te-energy-section', 'te-el-charts-1', 'te-el-charts-2', 'te-el-compare-card'];
  if (!el) {
    sectionIds.forEach(id => { const e = document.getElementById(id); if (e) e.style.display = 'none'; });
    ['te-pcurve', 'te-monthly', 'te-pts', 'te-sens'].forEach(destroyChart);
    return;
  }
  sectionIds.forEach(id => { const e = document.getElementById(id); if (e) e.style.display = ''; });

  const fmt = (n, dec = 0) => Number.isFinite(Number(n))
    ? Number(n).toLocaleString('es-MX', { maximumFractionDigits: dec }) : '—';
  const a = el.summary || {};
  const turbName = el.turbine?.name || '—';
  const nTurb = el.summary ? '' : '';

  const badge = document.getElementById('te-el-badge');
  if (badge) badge.textContent = turbName;

  const kpisEl = document.getElementById('te-el-kpis');
  if (kpisEl) {
    kpisEl.innerHTML = `<div class="te-kpi-grid">${[
      ['Factor de Capacidad', (a.capacity_factor_pct ?? '—') + '%', ''],
      ['AEP P50', fmt(el.aep?.aep_p50_mwh) + ' MWh', '/año'],
      ['FLH', fmt(a.full_load_hours) + ' h', ''],
      ['Energía neta', fmt(a.energy_net_mwh) + ' MWh', '/año'],
      ['AEP P90', fmt(el.aep?.aep_p90_mwh) + ' MWh', ''],
    ].map(([l, v, u]) => `<div class="te-kpi-item">
      <div class="kpi-label">${l}</div>
      <div class="kpi-value">${v}<span style="font-size:.7rem;color:var(--text-muted);margin-left:4px">${u}</span></div>
    </div>`).join('')}</div>`;
  }

  const pvEl  = document.getElementById('te-el-pvalues');
  const pvRow = document.getElementById('te-el-pvalue-row');
  if (pvEl && pvRow && el.aep?.aep_p50_mwh) {
    pvEl.style.display = '';
    pvRow.innerHTML = [
      ['P10', el.aep.aep_p10_mwh, 'p10'],
      ['P50', el.aep.aep_p50_mwh, 'p50'],
      ['P75', el.aep.aep_p75_mwh, 'p75'],
      ['P90', el.aep.aep_p90_mwh, 'p90'],
    ].map(([l, v, cls]) => `<div class="p-val-item ${cls}">
      <div class="p-val-label">${l}</div>
      <div class="p-val-value">${v?.toLocaleString() ?? '—'} MWh</div>
    </div>`).join('');
  }

  renderTEPowerCurveChart(el.turbine);
  renderTEMonthlyChart(el.monthly);
  renderTETimeseries(el.timeseries);
  renderTESensitivityChart(el.sensitivity || []);
}

function renderTEPowerCurveChart(turbine) {
  destroyChart('te-pcurve');
  if (!turbine) return;
  const ctx = document.getElementById('te-pcurve-chart')?.getContext('2d');
  if (!ctx) return;
  const nominal = turbine.rated_power_kw;
  WP.charts['te-pcurve'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: turbine.curve_ws,
      datasets: [
        { label: turbine.name, data: turbine.curve_pw,
          borderColor: acc(), backgroundColor: acc() + '20',
          borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3 },
        ...(nominal ? [{ label: `Nominal ${nominal} kW`,
          data: turbine.curve_ws.map(() => nominal),
          borderColor: gold() + '66', borderDash: [4, 3], borderWidth: 1, pointRadius: 0, fill: false }] : []),
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { font: { size: 9 }, boxWidth: 10 } } },
      scales: {
        x: { grid: { color: gridColor() }, title: { display: true, text: 'Velocidad (m/s)', font: { size: 9 } }, ticks: { font: { size: 9 } } },
        y: { grid: { color: gridColor() }, title: { display: true, text: 'Potencia (kW)', font: { size: 9 } }, ticks: { font: { size: 9 } } },
      },
    },
  });
}

function renderTEMonthlyChart(monthly) {
  destroyChart('te-monthly');
  if (!monthly) return;
  const ctx = document.getElementById('te-monthly-chart')?.getContext('2d');
  if (!ctx) return;
  const yearColors = [acc(), acc3(), gold(), acc2()];
  const yearMap = {}; let yi = 0;
  const bgColors = (monthly.labels || []).map(m => {
    const yr = String(m).split('-')[0];
    if (!(yr in yearMap)) yearMap[yr] = yearColors[yi++ % yearColors.length];
    return yearMap[yr] + 'cc';
  });
  WP.charts['te-monthly'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: monthly.labels,
      datasets: [{ label: 'Energía neta (MWh)', data: monthly.energy_mwh,
        backgroundColor: bgColors, borderWidth: 0, borderRadius: 3 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 18, font: { size: 8 }, maxRotation: 45 } },
        y: { grid: { color: gridColor() }, ticks: { font: { size: 9 } } },
      },
    },
  });
}

function renderTETimeseries(ts) {
  destroyChart('te-pts');
  if (!ts) return;
  const ctx = document.getElementById('te-pts-chart')?.getContext('2d');
  if (!ctx) return;
  WP.charts['te-pts'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ts.labels,
      datasets: [{ label: 'Potencia estimada (kW)', data: ts.power_kw,
        borderColor: acc3(), backgroundColor: acc3() + '18',
        fill: true, borderWidth: 1.5, pointRadius: 0, tension: 0.3 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { font: { size: 9 }, boxWidth: 10 } } },
      scales: {
        x: { grid: { color: gridColor() }, ticks: { maxTicksLimit: 12, font: { size: 9 } } },
        y: { grid: { color: gridColor() }, min: 0, ticks: { font: { size: 9 } } },
      },
    },
  });
}

function renderTESensitivityChart(sensitivity) {
  destroyChart('te-sens');
  if (!sensitivity?.length) return;
  const ctx = document.getElementById('te-sens-chart')?.getContext('2d');
  if (!ctx) return;
  WP.charts['te-sens'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: sensitivity.map(s => s.assumption),
      datasets: [{
        label: 'Δ AEP (%)',
        data: sensitivity.map(s => s.delta_pct),
        backgroundColor: sensitivity.map(s => s.delta_pct >= 0 ? acc() + '50' : acc2() + '50'),
        borderColor: sensitivity.map(s => s.delta_pct >= 0 ? acc() : acc2()),
        borderWidth: 1,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: gridColor() }, title: { display: true, text: 'Δ AEP (%)' } },
        y: { grid: { display: false }, ticks: { font: { size: 10 } } },
      },
    },
  });
}

async function compareTurbinesTE() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  setStatus('Comparando turbinas…');
  try {
    const r = await api('/api/energy/compare_turbines', 'POST', {
      session_id: WP.sessionId,
      ws_height_m: wsH(),
    });
    const tbody = document.getElementById('te-comp-tbody');
    tbody.innerHTML = (r.comparison || []).map((t, i) => `
      <tr ${i === 0 ? 'class="best-row"' : ''}>
        <td class="model-name">${i === 0 ? '★ ' : ''}${t.name}</td>
        <td>${t.rated_power_kw?.toLocaleString()}</td>
        <td>${t.rotor_diameter_m}</td>
        <td>${t.hub_height_m}</td>
        <td style="color:${i === 0 ? 'var(--green)' : ''}">${t.capacity_factor_pct}%</td>
        <td>${t.full_load_hours?.toLocaleString()}</td>
        <td>${t.mean_power_kw?.toLocaleString()}</td>
        <td><span class="badge badge--${i === 0 ? 'success' : 'info'}">#${t.rank}</span></td>
      </tr>`).join('');
    toast('Comparación completada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
}

// ─── LEVERAGED FINANCIAL ANALYSIS ────────────────────────────────────────────

const _LEV_NAMES = { real: 'Sin predicción', ml: 'Con predicción ML', corrected: 'Viento corregido' };

function showFinancingModal(scenarioKey) {
  const res = WP.technoScenarios[scenarioKey]?.result;
  if (!res?.analysis) {
    toast('Calcula primero el escenario activo antes de incluir financiamiento', 'warn');
    return;
  }
  const a   = technoAdjustedAnalysis(res.analysis);
  const fmt = n => n != null ? Math.abs(n).toLocaleString('es-MX', {maximumFractionDigits: 0}) : '—';

  // Pre-fill with last used params
  const p = WP.levParams;
  document.getElementById('fin-debt-ratio').value    = p.debt_ratio_pct;
  document.getElementById('fin-interest-rate').value = p.interest_rate_pct;
  document.getElementById('fin-loan-years').value    = p.loan_years;
  document.getElementById('fin-equity-rate').value   = p.equity_discount_rate_pct;

  document.getElementById('fin-modal-desc').textContent =
    `Escenario activo: ${_LEV_NAMES[scenarioKey] || scenarioKey}`;
  document.getElementById('fin-modal-base-info').innerHTML =
    `CAPEX <strong>$${fmt(a.capex?.total_capex_usd)} USD</strong> · Ingreso anual <strong>$${fmt(a.annual_revenue_usd)} USD</strong> · OPEX <strong>$${fmt(a.annual_opex_usd)} USD</strong> · <strong>${a.project_years || '—'} años</strong>`;

  const modal = document.getElementById('financing-modal');
  modal.style.display = 'flex';
  modal.setAttribute('aria-hidden', 'false');

  // Confirm handler (one-shot)
  const confirmBtn = document.getElementById('fin-modal-confirm');
  const handler = async () => {
    confirmBtn.removeEventListener('click', handler);
    closeFinancingModal();
    WP.levParams = {
      debt_ratio_pct:           parseFloat(document.getElementById('fin-debt-ratio').value)    || 70,
      interest_rate_pct:        parseFloat(document.getElementById('fin-interest-rate').value)  || 8,
      loan_years:               parseInt(document.getElementById('fin-loan-years').value)       || 15,
      equity_discount_rate_pct: parseFloat(document.getElementById('fin-equity-rate').value)    || 12,
    };
    await calcLeveragedForScenario(scenarioKey, WP.levParams);
  };
  confirmBtn.addEventListener('click', handler);
}

function closeFinancingModal() {
  const modal = document.getElementById('financing-modal');
  if (!modal) return;
  modal.style.display = 'none';
  modal.setAttribute('aria-hidden', 'true');
}

async function calcLeveragedForScenario(scenarioKey, params) {
  const res = WP.technoScenarios[scenarioKey]?.result;
  if (!res?.analysis) return;
  // Usa la analysis ajustada por pérdidas finales (si aplica) — los flujos apalancados
  // deben coincidir con los KPIs de proyecto que el usuario ve.
  const a = technoAdjustedAnalysis(res.analysis);
  const body = {
    capex_usd:                a.capex?.total_capex_usd,
    annual_revenue_usd:       a.annual_revenue_usd,
    annual_opex_usd:          a.annual_opex_usd,
    project_years:            a.project_years || parseInt(document.getElementById('te-years')?.value) || 20,
    debt_ratio_pct:           params.debt_ratio_pct,
    interest_rate_pct:        params.interest_rate_pct,
    loan_years:               params.loan_years,
    equity_discount_rate_pct: params.equity_discount_rate_pct,
    usd_to_mxn:               parseFloat(document.getElementById('te-fx')?.value) || 1,
  };
  setStatus('Calculando análisis apalancado…');
  try {
    const r = await api('/api/energy/leveraged', 'POST', body);
    WP.technoScenarios[scenarioKey].leveraged = r;
    // Only render if this scenario is still active
    if (WP.technoActiveScenario === scenarioKey) renderTechnoLeveraged(r);
    toast('Análisis apalancado completado', 'success');
    setStatus('Análisis apalancado completado.');
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    setStatus('Error en análisis apalancado.');
  }
}

function clearLeveraged() {
  WP.technoScenarios[WP.technoActiveScenario].leveraged = null;
  renderTechnoLeveraged(null);
  const sc = technoCurrentScenario();
  if (sc?.result?.analysis) {
    const adjA = technoAdjustedAnalysis(sc.result.analysis);
    renderCashflowTable(adjA.cashflow_table);
    renderTechnoSummaryCard(adjA, null);
  }
}

function renderTechnoLeveraged(r) {
  const section   = document.getElementById('te-lev-section');
  const amortCard = document.getElementById('te-lev-amort-card');
  const cfTitle   = document.getElementById('te-cashflow-title');
  const cfBadge   = document.getElementById('te-cashflow-mode-badge');
  if (!section) return;
  const clearBtn = document.getElementById('btn-clear-financing');
  if (!r) {
    section.style.display = 'none';
    destroyChart('chart-lev-structure');
    destroyChart('chart-lev-cashflow');
    if (amortCard) amortCard.style.display = '';
    if (cfTitle) cfTitle.textContent = 'Tabla de flujos anuales';
    if (cfBadge) cfBadge.style.display = 'none';
    if (clearBtn) clearBtn.style.display = 'none';
    const _cb = document.getElementById('te-lev-compare-tbody');
    if (_cb) _cb.innerHTML = '<tr><td colspan="4" class="text-muted" style="text-align:center;padding:1rem">Activa financiamiento para ver comparativa.</td></tr>';
    // Revert main KPI grid to unlevered values
    const _sc0 = technoCurrentScenario();
    if (_sc0?.result?.analysis) {
      renderTechnoKPIs({ ..._sc0.result, analysis: technoAdjustedAnalysis(_sc0.result.analysis) }, null);
    }
    return;
  }
  if (clearBtn) clearBtn.style.display = '';
  section.style.display = '';
  // Los flujos del proyecto + financiamiento se integran en la tabla de flujos del proyecto
  // (no se muestran aparte). La tabla de amortización queda oculta porque su info
  // está embebida en la tabla principal.
  if (amortCard) amortCard.style.display = 'none';
  if (cfTitle) cfTitle.textContent = 'Flujos anuales — proyecto + financiamiento';
  if (cfBadge) {
    cfBadge.textContent = 'con financiamiento';
    cfBadge.style.display = '';
  }
  const sc = technoCurrentScenario();
  const baseCF = technoAdjustedAnalysis(sc?.result?.analysis)?.cashflow_table || [];
  renderCashflowTable(baseCF, r);

  // Badge
  const badge = document.getElementById('lev-viable-badge');
  if (badge) {
    badge.style.display = '';
    badge.textContent   = r.is_viable ? '✓ VAN POSITIVO' : '✗ VAN NEGATIVO';
    badge.className     = `badge ${r.is_viable ? 'badge--success' : 'badge--error'}`;
  }

  const fmt    = n => n != null && Number.isFinite(n) ? Math.abs(n).toLocaleString('es-MX', {maximumFractionDigits: 0}) : '—';
  const signed = n => n != null && Number.isFinite(n) ? (n >= 0 ? '+' : '−') + ' $' + fmt(n) : '—';
  const fx     = parseFloat(document.getElementById('te-fx')?.value) || 1;
  const showMxn = fx > 1.0001;
  const mxnStr  = usd => showMxn && usd != null && Number.isFinite(usd)
    ? `<div style="font-size:.7rem;color:var(--text-muted);margin-top:2px">≈ $${fmt(usd * fx)} MXN</div>` : '';

  const items = [
    ['Capital equity',             '$' + fmt(r.equity_usd),        'USD',     r.equity_usd],
    ['Deuda financiada',           '$' + fmt(r.debt_usd),          'USD',     r.debt_usd],
    ['Servicio deuda anual (PMT)', '$' + fmt(r.annual_pmt_usd),    'USD/año', r.annual_pmt_usd],
    ['NOI anual',                  signed(r.noi_usd),              'USD/año', r.noi_usd],
    ['WACC',                       r.wacc_pct != null ? r.wacc_pct + '%' : '—', '', null],
    ['VAN apalancado',             signed(r.leveraged_npv_usd),    'USD',     r.leveraged_npv_usd],
    ['TIR apalancada',             r.leveraged_irr_pct != null ? r.leveraged_irr_pct + '%' : '—', '', null],
    ['Retorno equity',             r.equity_payback_years != null ? Number(r.equity_payback_years).toFixed(1) + ' años' : '> proyecto', '', null],
    ['DSCR promedio',              r.dscr_mean != null ? r.dscr_mean.toFixed(2) + 'x' : '—', '', null],
    ['DSCR mínimo',                r.dscr_min  != null ? r.dscr_min.toFixed(2)  + 'x' : '—', '', null],
  ];

  const grid = document.getElementById('lev-kpi-grid');
  if (grid) {
    grid.innerHTML = `<div class="te-kpi-grid">${items.map(([l, v, u, usdVal]) =>
      `<div class="te-kpi-item">
        <div class="kpi-label">${l}</div>
        <div class="kpi-value">${v}<span style="font-size:.7rem;color:var(--text-muted);margin-left:4px">${u}</span></div>
        ${mxnStr(usdVal)}
      </div>`
    ).join('')}</div>`;
  }

  renderLevStructureChart(r);
  renderLevCashflowChart(r);
  renderLevAmortTable(r.cashflow_table);

  // Sync main KPI grid to show leveraged metrics
  const _sc = technoCurrentScenario();
  if (_sc?.result?.analysis) {
    renderTechnoKPIs({ ..._sc.result, analysis: technoAdjustedAnalysis(_sc.result.analysis) }, r);
  }

  // ── Tabla comparativa post-apalancamiento ─────────────────────────────────
  const compareBody = document.getElementById('te-lev-compare-tbody');
  if (compareBody && _sc?.result?.analysis) {
    const adjA   = technoAdjustedAnalysis(_sc.result.analysis);
    const nYrs   = adjA.project_years || (_sc.inputs?.years) || 20;
    const rProj  = ((adjA.discount_rate_pct  ?? (_sc.inputs?.rate ?? 8)) / 100);
    const rEq    = (r.inputs?.equity_discount_rate_pct ?? WP.levParams.equity_discount_rate_pct ?? 12) / 100;
    const infl   = ((_sc.inputs?.inflation ?? 0) / 100);

    // VAE = VAN × r(1+r)^n / ((1+r)^n − 1)
    const annuityFactor = (rate, n) => {
      if (rate <= 0 || n <= 0) return n > 0 ? 1/n : 1;
      return (rate * Math.pow(1+rate, n)) / (Math.pow(1+rate, n) - 1);
    };
    const vaeProj  = Number.isFinite(adjA.npv_usd)             ? adjA.npv_usd            * annuityFactor(rProj, nYrs) : null;
    const vaeEq    = Number.isFinite(r.leveraged_npv_usd)      ? r.leveraged_npv_usd     * annuityFactor(rEq,   nYrs) : null;
    // VAN real = VAN nominal / (1+infl)^n  (deflación al período medio)
    const vanRealProj = adjA.npv_usd        != null && infl > 0 ? adjA.npv_usd        / Math.pow(1+infl, nYrs/2) : adjA.npv_usd;
    const vanRealEq   = r.leveraged_npv_usd != null && infl > 0 ? r.leveraged_npv_usd / Math.pow(1+infl, nYrs/2) : r.leveraged_npv_usd;

    const fmtC = n => n != null && Number.isFinite(n) ? (n >= 0 ? '+' : '−') + ' $' + Math.abs(Math.round(n)).toLocaleString('es-MX') : '—';
    const fmtP = n => n != null && Number.isFinite(n) ? (n >= 0 ? '+' : '') + n.toFixed(1) + '%' : '—';
    const fmtY = n => n != null && Number.isFinite(n) ? Number(n).toFixed(1) + ' a' : '> proyecto';
    const delta = (a, b) => {
      if (a == null || b == null || !Number.isFinite(a) || !Number.isFinite(b)) return '—';
      const d = b - a, pct = a !== 0 ? (d / Math.abs(a) * 100) : null;
      const col = d >= 0 ? '#22c55e' : '#ef4444';
      return `<span style="color:${col}">${d >= 0 ? '+' : ''}$${Math.abs(Math.round(d)).toLocaleString('es-MX')}${pct != null ? ` (${pct >= 0 ? '+' : ''}${pct.toFixed(0)}%)` : ''}</span>`;
    };
    const deltaP = (a, b) => {
      if (a == null || b == null || !Number.isFinite(+a) || !Number.isFinite(+b)) return '—';
      const d = parseFloat(b) - parseFloat(a);
      return `<span style="color:${d >= 0 ? '#22c55e' : '#ef4444'}">${d >= 0 ? '+' : ''}${d.toFixed(2)}pp</span>`;
    };
    const rows = [
      ['VAN / VPN',   fmtC(adjA.npv_usd),        fmtC(r.leveraged_npv_usd),   delta(adjA.npv_usd, r.leveraged_npv_usd)],
      ['VAN real' + (infl > 0 ? ` (−${(_sc.inputs?.inflation||0).toFixed(1)}% infl.)` : ' (nominal)'),
                      fmtC(vanRealProj),           fmtC(vanRealEq),             delta(vanRealProj, vanRealEq)],
      ['VAE (USD/a)', fmtC(adjA.vae_usd ?? vaeProj), fmtC(vaeEq),             delta(adjA.vae_usd ?? vaeProj, vaeEq)],
      ['TIR',         fmtP(adjA.irr_pct),          fmtP(r.leveraged_irr_pct),  deltaP(adjA.irr_pct, r.leveraged_irr_pct)],
      ['Payback',     fmtY(adjA.payback_years),    fmtY(r.equity_payback_years), ''],
      ['WACC',        fmtP(rProj*100),             r.wacc_pct != null ? r.wacc_pct + '%' : '—', ''],
    ];
    compareBody.innerHTML = rows.map(([lbl, proj, eq, dlt]) =>
      `<tr>
        <td style="font-weight:500">${lbl}</td>
        <td style="font-family:var(--font-mono,monospace);font-size:.8rem">${proj}</td>
        <td style="font-family:var(--font-mono,monospace);font-size:.8rem">${eq}</td>
        <td style="font-size:.78rem">${dlt}</td>
      </tr>`
    ).join('');
  }
}

function renderLevStructureChart(r) {
  destroyChart('chart-lev-structure');
  const ctx = document.getElementById('chart-lev-structure')?.getContext('2d');
  if (!ctx) return;
  WP.charts['chart-lev-structure'] = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Equity (capital propio)', 'Deuda (financiado)'],
      datasets: [{
        data: [r.equity_usd, r.debt_usd],
        backgroundColor: ['#4DA3FF', '#9F7AEA'],
        borderColor: '#121823',
        borderWidth: 3,
        hoverOffset: 8,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      cutout: '62%',
      plugins: {
        legend: { position: 'bottom', labels: { padding: 16 } },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.parsed;
              const total = r.equity_usd + r.debt_usd;
              const pct = total > 0 ? ((v / total) * 100).toFixed(1) : 0;
              return ` $${Math.abs(v).toLocaleString('es-MX', {maximumFractionDigits:0})} USD (${pct}%)`;
            }
          }
        }
      }
    }
  });
}

function renderLevCashflowChart(r) {
  destroyChart('chart-lev-cashflow');
  const ctx = document.getElementById('chart-lev-cashflow')?.getContext('2d');
  if (!ctx || !r.cashflow_table?.length) return;
  const rows  = r.cashflow_table;
  const years = rows.map(row => `Año ${row.year}`);
  const noi   = rows.map(row => (r.noi_usd / 1e3).toFixed(1));
  const pmt   = rows.map(row => row.year <= (r.inputs?.loan_years || 0) ? (r.annual_pmt_usd / 1e3).toFixed(1) : 0);
  const ecf   = rows.map(row => (row.equity_cf / 1e3).toFixed(1));
  const cumEcf= rows.map(row => (row.cum_equity_cf / 1e3).toFixed(1));
  WP.charts['chart-lev-cashflow'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: years,
      datasets: [
        {
          label: 'NOI (kUSD)',
          data: noi,
          backgroundColor: 'rgba(77,163,255,.35)',
          borderColor: '#4DA3FF',
          borderWidth: 1.5,
          borderRadius: 3,
          order: 2,
        },
        {
          label: 'Servicio deuda PMT (kUSD)',
          data: pmt,
          backgroundColor: 'rgba(159,122,234,.35)',
          borderColor: '#9F7AEA',
          borderWidth: 1.5,
          borderRadius: 3,
          order: 3,
        },
        {
          label: 'FC Equity (kUSD)',
          data: ecf,
          type: 'line',
          borderColor: '#34D399',
          backgroundColor: 'rgba(52,211,153,.12)',
          borderWidth: 2,
          pointRadius: 2.5,
          tension: 0.3,
          fill: false,
          order: 1,
        },
        {
          label: 'FC Equity acum. (kUSD)',
          data: cumEcf,
          type: 'line',
          borderColor: '#F59E0B',
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          borderDash: [5, 3],
          pointRadius: 0,
          tension: 0.3,
          fill: false,
          order: 0,
        },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,.05)' }, ticks: { maxTicksLimit: 10 } },
        y: {
          grid: { color: 'rgba(255,255,255,.07)' },
          title: { display: true, text: 'Miles USD (kUSD)', color: '#A6B3C8', font: { size: 10 } },
        }
      },
      plugins: { legend: { position: 'bottom', labels: { padding: 14, boxWidth: 10 } } },
    }
  });
}

function renderLevAmortTable(rows) {
  const tbody = document.querySelector('#lev-amort-table tbody');
  if (!tbody || !rows?.length) return;
  const fmt = n => n != null && Number.isFinite(n) ? n.toLocaleString('es-MX', {maximumFractionDigits:0}) : '—';
  tbody.innerHTML = rows.map(row => {
    const dscr = row.dscr != null ? row.dscr.toFixed(2) : '—';
    const dscr_ok = row.dscr == null || row.dscr >= 1.2;
    return `<tr>
      <td>${row.year}</td>
      <td>$${fmt(row.balance_start)}</td>
      <td>$${fmt(row.interest)}</td>
      <td>$${fmt(row.principal)}</td>
      <td>$${fmt(row.balance_end)}</td>
      <td style="color:${dscr_ok ? 'var(--green)' : '#f87171'}">${dscr}x</td>
      <td style="color:${row.equity_cf >= 0 ? 'var(--green)' : '#f87171'}">${row.equity_cf >= 0 ? '+' : '−'}$${fmt(row.equity_cf)}</td>
      <td style="color:${row.cum_equity_cf >= 0 ? 'var(--green)' : '#f87171'}">${row.cum_equity_cf >= 0 ? '+' : '−'}$${fmt(row.cum_equity_cf)}</td>
    </tr>`;
  }).join('');
}

// ─── EXPORT CENTER ───────────────────────────────────────────────────────────
function updateExportSessionInfo() {
  const info = document.getElementById('export-session-info');
  if (!info) return;
  if (WP.sessionId) {
    info.className = 'info-box green';
    info.textContent = 'Sesión activa. Las exportaciones usarán los resultados disponibles.';
  } else {
    info.className = 'info-box amber';
    info.textContent = 'Carga un archivo de datos y ejecuta los módulos para habilitar las exportaciones.';
  }
}

function bindExport() {
  // Update info box whenever the export tab becomes visible
  document.querySelectorAll('.nav-btn').forEach(btn => {
    if (btn.dataset.module === 'export') {
      btn.addEventListener('click', updateExportSessionInfo);
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
      ' AERO OPEN CIM — Reporte Técnico',
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
      '── ANÁLISIS TECNOECONÓMICO ───────────────────────────',
      te ? `VAN/NPV:       $ ${te.analysis?.npv_usd?.toLocaleString?.() ?? '—'} USD` : 'Ejecutar módulo Tecnoeconómico',
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
      ' AERO OPEN CIM · v2.0 · Flask + scikit-learn + LightGBM',
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
  const rows = ['# AERO OPEN CIM — Wind Statistics Export'];
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
    `capex_obras_civiles,${cap.civil_works_usd},USD`,
    `capex_sistema_electrico,${cap.electrical_usd},USD`,
    `capex_instalacion,${cap.installation_usd},USD`,
    `capex_ingenieria,${cap.engineering_usd},USD`,
    `capex_miscelaneos,${cap.miscellaneous_usd},USD`,
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

function renderCashflowTable(table, leveraged = null) {
  const tableEl = document.getElementById('te-cashflow-table');
  if (!tableEl) return;
  const thead = tableEl.querySelector('thead');
  const tbody = tableEl.querySelector('tbody');
  if (!table?.length) { if (tbody) tbody.innerHTML = ''; return; }

  const fmt = n => (n != null && Number.isFinite(n))
    ? Math.abs(Math.round(n)).toLocaleString('es-MX') : '—';
  const usd    = n => '$' + fmt(n);
  const signed = n => (n != null && Number.isFinite(n))
    ? (n >= 0 ? '+' : '−') + ' $' + fmt(n) : '—';

  if (leveraged?.cashflow_table?.length) {
    const lev = Object.fromEntries(leveraged.cashflow_table.map(row => [row.year, row]));
    if (thead) thead.innerHTML = `<tr>
      <th>Año</th>
      <th>Flujo proyecto</th>
      <th>VP Flujo</th>
      <th>Acumulado</th>
      <th>Interés</th>
      <th>Principal</th>
      <th>FC Equity</th>
      <th>FC Equity acum.</th>
    </tr>`;
    tbody.innerHTML = table.map(r => {
      const lr     = lev[r.year] || {};
      const cls    = r.cumulative >= 0 ? 'positive' : 'negative';
      const ecf    = lr.equity_cf;
      const cumEcf = lr.cum_equity_cf;
      const ecfStyle = ecf == null ? '' : `color:${ecf >= 0 ? 'var(--green)' : '#f87171'}`;
      const cumStyle = cumEcf == null ? '' : `color:${cumEcf >= 0 ? 'var(--green)' : '#f87171'}`;
      return `<tr class="${cls}">
        <td>${r.year}</td>
        <td>${usd(r.cashflow)}</td>
        <td>${usd(r.pv_cashflow)}</td>
        <td>${usd(r.cumulative)}</td>
        <td>${usd(lr.interest)}</td>
        <td>${usd(lr.principal)}</td>
        <td style="${ecfStyle}">${signed(ecf)}</td>
        <td style="${cumStyle}">${signed(cumEcf)}</td>
      </tr>`;
    }).join('');
    return;
  }

  // Modo simple — sin financiamiento
  // Mostrar columnas adicionales si los valores varían año a año
  const varies = (key, eps) => {
    const vals = table.map(r => r[key]).filter(v => v != null);
    return vals.length === table.length && vals.some(v => Math.abs(v - vals[0]) > eps);
  };
  const showEnergy = varies('energy_mwh', 0.5);
  const showPrice  = varies('price_usd_kwh', 1e-5);
  const showRate   = varies('discount_rate_pct', 1e-3);
  const showFx     = varies('fx_mxn_per_usd', 1e-3);
  const showOpex   = varies('opex', 1);

  const headers = ['Año'];
  if (showEnergy) headers.push('Energía (MWh)');
  if (showPrice)  headers.push('Precio (USD/kWh)');
  if (showRate)   headers.push('Tasa (%)');
  if (showFx)     headers.push('FX (MXN/USD)');
  if (showOpex)   headers.push('OPEX (USD)');
  headers.push('Flujo (USD)', 'VP Flujo', 'Acumulado');
  if (thead) thead.innerHTML = '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';

  const fmtNum = (v, dec) => v != null && Number.isFinite(v) ? Number(v).toFixed(dec) : '—';
  tbody.innerHTML = table.map(r => {
    const cls = r.cumulative >= 0 ? 'positive' : 'negative';
    const cells = [`<td>${r.year}</td>`];
    if (showEnergy) cells.push(`<td>${r.energy_mwh != null ? r.energy_mwh.toLocaleString('es-MX') : '—'}</td>`);
    if (showPrice)  cells.push(`<td>${fmtNum(r.price_usd_kwh, 5)}</td>`);
    if (showRate)   cells.push(`<td>${fmtNum(r.discount_rate_pct, 3)}</td>`);
    if (showFx)     cells.push(`<td>${fmtNum(r.fx_mxn_per_usd, 3)}</td>`);
    if (showOpex)   cells.push(`<td>${usd(r.opex)}</td>`);
    cells.push(`<td>${usd(r.cashflow)}</td>`);
    cells.push(`<td>${usd(r.pv_cashflow)}</td>`);
    cells.push(`<td>${usd(r.cumulative)}</td>`);
    return `<tr class="${cls}">${cells.join('')}</tr>`;
  }).join('');
}

function renderTechnoCompare() {
  const tbody = document.getElementById('te-compare-tbody');
  if (!tbody) return;
  const keys = ['real', 'ml', 'corrected'];
  const labels = { real: 'Sin predicción', ml: 'ML 25 años', corrected: 'Corregido + ML' };
  const results = keys.map(k => WP.technoScenarios[k]?.result || null);
  if (results.every(r => !r)) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:1.5rem">Calcula al menos un escenario y presiona «Actualizar comparativo».</td></tr>';
    return;
  }

  const fmtM = v => v != null ? '$' + Math.abs(v).toLocaleString('es-MX', { maximumFractionDigits: 0 }) : '—';
  const fmtN = (v, dec = 2) => v != null ? Number(v).toFixed(dec) : '—';
  const sign = v => v == null ? '—' : (v >= 0 ? '+' : '-');

  const metrics = [
    { label: 'VAN / NPV (USD)', get: a => a?.npv_usd, fmt: v => (v >= 0 ? '+' : '-') + fmtM(v), best: 'max', highlight: true },
    { label: 'LCOE (USD/kWh)', get: a => a?.lcoe_usd_kwh, fmt: v => fmtN(v, 4), best: 'min' },
    { label: 'VAE (USD/año)', get: a => a?.vae_usd, fmt: v => sign(v) + fmtM(v), best: 'max' },
    { label: 'Payback (años)', get: a => a?.payback_years, fmt: v => v ? fmtN(v, 1) + ' años' : '> proyecto', best: 'min' },
    { label: 'Factor capacidad (%)', get: a => a?.capacity_factor_pct, fmt: v => fmtN(v, 2) + '%', best: 'max' },
    { label: 'Energía anual (MWh)', get: a => a?.annual_energy_mwh, fmt: v => fmtN(v, 0), best: 'max' },
    { label: 'Ingresos anuales (USD)', get: a => a?.annual_revenue_usd, fmt: fmtM, best: 'max' },
    { label: 'CAPEX total (USD)', get: a => a?.capex?.total_capex_usd, fmt: fmtM, best: 'min' },
    { label: 'Potencia instalada (MW)', get: a => a?.capex?.installed_mw, fmt: v => fmtN(v, 2) + ' MW', best: null },
    { label: 'Turbinas usadas', get: a => a?.capex?.n_turbines, fmt: v => v ?? '—', best: null },
  ];

  const rows = metrics.map(m => {
    const vals = results.map(r => r?.analysis ? m.get(r.analysis) : null);
    const finite = vals.filter(v => v != null && Number.isFinite(v));
    let bestVal = null;
    if (m.best === 'max' && finite.length) bestVal = Math.max(...finite);
    if (m.best === 'min' && finite.length) bestVal = Math.min(...finite);
    const bestIdx = bestVal != null ? vals.findIndex(v => v === bestVal) : -1;
    const bestLabel = bestIdx >= 0 ? labels[keys[bestIdx]] : '—';
    const cells = vals.map((v, i) => {
      const isBest = m.best && i === bestIdx;
      const style = isBest ? ' style="color:var(--accent);font-weight:700;text-align:center"' : ' style="text-align:center"';
      return `<td${style}>${v != null ? m.fmt(v) : '<span class="text-muted">—</span>'}</td>`;
    }).join('');
    const hl = m.highlight ? ' style="font-weight:700"' : '';
    return `<tr><td${hl}>${m.label}</td>${cells}<td style="text-align:center;color:var(--accent)">${bestLabel}</td></tr>`;
  }).join('');

  // Viabilidad row
  const viabilityRow = keys.map((k, i) => {
    const r = results[i];
    if (!r) return '<td style="text-align:center">—</td>';
    const ok = r.analysis?.is_viable;
    return `<td style="text-align:center"><span class="badge ${ok ? 'badge--success' : 'badge--error'}">${ok ? '✓ Viable' : '✗ No viable'}</span></td>`;
  }).join('');
  tbody.innerHTML = `<tr><td><strong>Viabilidad</strong></td>${viabilityRow}<td style="text-align:center">—</td></tr>${rows}`;

  // Gráfica de barras VAN + LCOE por escenario
  destroyChart('chart-te-compare');
  const ctx = document.getElementById('chart-te-compare')?.getContext('2d');
  if (!ctx) return;
  const npvVals = keys.map((k, i) => { const a = results[i]?.analysis; return a?.npv_usd != null ? Math.round(a.npv_usd / 1e6 * 100) / 100 : null; });
  const lcoeVals = keys.map((k, i) => { const a = results[i]?.analysis; return a?.lcoe_usd_kwh != null ? Number(a.lcoe_usd_kwh) : null; });
  WP.charts['chart-te-compare'] = new Chart(ctx, {
    data: {
      labels: keys.map(k => labels[k]),
      datasets: [
        { type: 'bar', label: 'VAN (M USD)', data: npvVals, backgroundColor: npvVals.map(v => v == null ? '#5558' : v >= 0 ? acc() + 'bb' : '#ef4444bb'), borderRadius: 6, yAxisID: 'y1' },
        { type: 'line', label: 'LCOE (USD/kWh)', data: lcoeVals, borderColor: acc2(), backgroundColor: 'transparent', pointRadius: 6, pointStyle: 'circle', borderWidth: 2.5, yAxisID: 'y2' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: { legend: { labels: { font: { size: 10 }, boxWidth: 10 } } },
      scales: {
        x: { grid: { color: gridColor() } },
        y1: { type: 'linear', position: 'left', title: { display: true, text: 'VAN (M USD)' }, grid: { color: gridColor() } },
        y2: { type: 'linear', position: 'right', title: { display: true, text: 'LCOE USD/kWh' }, grid: { display: false } },
      }
    }
  });
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
    const wsHAdv     = parseFloat(document.getElementById('adv-ws-height')?.value) || wsH();
    const zHub       = parseFloat(document.getElementById('adv-z-hub')?.value)       || null;
    const orogFactor = parseFloat(document.getElementById('adv-orog-factor')?.value ?? '0.15') || 0.15;
    const zScale     = parseFloat(document.getElementById('adv-z-scale')?.value)     || 80;
    const r = await api('/api/wind/advanced', 'POST', {
      session_id: WP.sessionId, ws_height: wsHAdv, z_hub: zHub,
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
  baseLayer: null,
  areaBounds: null, areaLayer: null, areaDraft: null, areaStart: null, selectingArea: false,
  drawingPark: false, parkPoints: [], parkLayer: null, parkMarkers: [],
  optimal: [],
  mcdaMarkers: [],
  searchMarker: null,
  // Overlays físicos opcionales (Fase 1: visualización)
  hillshadeLayer: null,
  landcoverLayer: null,
  overlayOpacity: 0.6,
  // Punto de interés (POI) — emplazamiento del parque marcado por el usuario
  poi: null,                // {lat, lon}
  poiMarker: null,          // marcador en modo selección
  poiCircle: null,          // círculo de radio en modo selección
  poiResultMarker: null,    // marcador con valor interpolado tras Kriging
  placingPoi: false,
};

const KRIG_MEXICO_BOUNDS = [[14.2, -118.6], [33.2, -86.5]];

const KRIG_LAYERS = {
  wind:         { unit: 'm/s',   title: 'Velocidad promedio del viento (m/s)' },
  wind_directional: { unit: 'm/s', title: 'Viento corregido direccional (m/s)' },
  gust:         { unit: 'm/s',   title: 'Ráfagas / extremo anual P99.5 (m/s)' },
  power_density:{ unit: 'W/m²',  title: 'Densidad de potencia eólica (W/m²)' },
  inversion:    { unit: 'USD',   title: 'Inversión inicial (USD)' },
  van:          { unit: 'USD',   title: 'Valor Actual Neto (USD)' },
  // ── Capas físicas (solo presentes cuando physical=true) ──
  wind_neutral: { unit: 'm/s',   title: 'Viento neutralizado (campo geoestadístico)' },
  elevation:    { unit: 'm',     title: 'Topografía — altitud DEM (m)' },
  roughness:    { unit: 'm',     title: 'Rugosidad superficial z₀ (m)' },
  air_density:  { unit: 'kg/m³', title: 'Densidad del aire local' },
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
  // México centrado: lat ~23.5, lon ~-102, zoom 5 muestra todo el país
  const map = L.map('krig3-map', { center: [23.5, -102], zoom: 5, scrollWheelZoom: true });
  KRIG.map = map;
  const isLight = document.body.classList.contains('light');
  const initialBase = (document.getElementById('krig3-baselayer')?.value)
    || (isLight ? 'voyager' : 'dark');
  krig3SetBase(initialBase);
  krig3SetExtent('mexico');
  return map;
}

// ── Cambia la capa base (incluye satélite ESRI) ─────────────────────────────
function krig3SetBase(key) {
  if (!KRIG.map) return;
  if (KRIG.baseLayer) KRIG.map.removeLayer(KRIG.baseLayer);
  const url = (typeof MAPA_TILES !== 'undefined' && MAPA_TILES[key])
    || 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
  KRIG.baseLayer = L.tileLayer(url, {
    maxZoom: 18, subdomains: (key === 'osm' || key === 'topo') ? 'abc' : 'abcd',
    crossOrigin: 'anonymous',  // habilita exportación a PNG (html2canvas)
    attribution: '© OpenStreetMap · © CARTO · ESRI',
  }).addTo(KRIG.map);
  // La capa base debe quedar al fondo para no tapar overlays/markers.
  if (KRIG.baseLayer.bringToBack) KRIG.baseLayer.bringToBack();
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
  // Preferimos POI del Globe (selección del usuario); si no, sitio principal
  // del Kriging; si no, metadata de Data Hub.
  let target = null;
  if (typeof GLOBE !== 'undefined' && GLOBE?.poi
      && Number.isFinite(GLOBE.poi.lat) && Number.isFinite(GLOBE.poi.lon)) {
    target = { lat: GLOBE.poi.lat, lon: GLOBE.poi.lon };
  }
  if (!target && KRIG.principal) {
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
  // Encuadre a ~15 km del punto (cuadro ±15 km de medio-lado).
  krig3FlyToHalfExtentKm(target.lat, target.lon, 15, 0.5);
}

/** Vuela el mapa Kriging a un cuadro de ±halfKm alrededor de (lat, lon). */
function krig3FlyToHalfExtentKm(lat, lon, halfKm, durationS = 0.7) {
  if (!KRIG.map) return;
  const dLat = halfKm / 111.32;
  const dLon = halfKm / (111.32 * Math.max(0.05, Math.cos(lat * Math.PI / 180)));
  const sw = [lat - dLat, lon - dLon];
  const ne = [lat + dLat, lon + dLon];
  try {
    KRIG.map.flyToBounds([sw, ne], { duration: durationS, padding: [40, 40] });
  } catch (e) {
    KRIG.map.setView([lat, lon], 11);
  }
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
  if (typeof mcdaUpdateScopeUi === 'function') mcdaUpdateScopeUi();
}

function krig3ClearArea() {
  if (KRIG.areaLayer && KRIG.map) KRIG.map.removeLayer(KRIG.areaLayer);
  if (KRIG.areaDraft && KRIG.map) KRIG.map.removeLayer(KRIG.areaDraft);
  KRIG.areaLayer = null; KRIG.areaDraft = null; KRIG.areaBounds = null;
  KRIG.selectingArea = false;
  krig3RenderOptimalList();
  if (typeof mcdaUpdateScopeUi === 'function') mcdaUpdateScopeUi();
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
  if (typeof mcdaUpdateScopeUi === 'function') mcdaUpdateScopeUi();
}
function krig3ClearPark() {
  KRIG.drawingPark = false;
  if (KRIG.parkLayer && KRIG.map) KRIG.map.removeLayer(KRIG.parkLayer);
  KRIG.parkMarkers.forEach(m => KRIG.map && KRIG.map.removeLayer(m));
  KRIG.parkLayer = null; KRIG.parkMarkers = []; KRIG.parkPoints = [];
  krig3UpdateParkInfo();
  krig3RenderOptimalList();
  if (typeof mcdaUpdateScopeUi === 'function') mcdaUpdateScopeUi();
  if (typeof mcdaClearSpatialMarkers === 'function') mcdaClearSpatialMarkers();
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
  el.innerHTML = sites.slice(0, 8).map(s => {
    const t = (KRIG.terrain || {})[s.filename];
    let terrainLine = '';
    if (t) {
      const bits = [];
      if (t.elevation_m != null) bits.push(`<b>${t.elevation_m.toFixed(0)} m</b> snm`);
      if (t.landcover_label) bits.push(t.landcover_label);
      if (t.z0 != null) bits.push(`z₀ ${t.z0} m`);
      if (bits.length) terrainLine = `<br><span style="color:var(--text-muted)">${bits.join(' · ')}</span>`;
    }
    return (
      `<div style="padding:.35rem 0;border-bottom:1px solid var(--border)">` +
        `<b>#${s.rank}</b> ${s.filename}<br>` +
        `Puntuación ${s.score.toFixed(1)} · Vel ${s.speed.toFixed(2)} m/s · ` +
        `Ráfaga ${s.gust.toFixed(2)} m/s · Pd ${s.power.toFixed(0)} W/m²` +
        terrainLine +
      `</div>`
    );
  }).join('');
}

// ─── Dispatcher único de eventos del mapa Kriging ─────────────────────────────
function krig3HandleMapClick(e) {
  if (KRIG.placingPoi) {
    krig3PlacePoi(e.latlng);
    return;
  }
  if (KRIG.drawingPark) krig3AddParkPoint(e.latlng);
}

// ═══════════════════════ POI — emplazamiento del parque ═══════════════════════
// SVG inline de un aerogenerador (torre + 3 aspas) — usado en el divIcon.
function krig3PoiIconHtml(highlight = false) {
  const fill = highlight ? '#facc15' : '#0ea5e9';
  return `
  <div style="position:relative;width:34px;height:42px;transform:translate(-17px,-42px);
              filter:drop-shadow(0 2px 4px rgba(0,0,0,.5))">
    <svg viewBox="0 0 34 42" width="34" height="42" xmlns="http://www.w3.org/2000/svg">
      <!-- Aspas -->
      <path d="M17 18 L16 4 L18 4 Z"   fill="${fill}"/>
      <path d="M17 18 L30 22 L29 24 Z" fill="${fill}"/>
      <path d="M17 18 L4 22  L5 24 Z"  fill="${fill}"/>
      <!-- Hub -->
      <circle cx="17" cy="18" r="2.4" fill="#fff" stroke="${fill}" stroke-width="1.2"/>
      <!-- Torre -->
      <polygon points="16,19 18,19 18.7,40 15.3,40" fill="#94a3b8" stroke="#0f172a" stroke-width=".5"/>
      <!-- Base -->
      <rect x="14.5" y="40" width="5" height="2" fill="#0f172a"/>
    </svg>
  </div>`;
}

function krig3HaversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 +
            Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) *
            Math.sin(dLon/2)**2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

async function krig3StartPoiPlacement() {
  krig3InitMap();
  if (KRIG.result) krig3ResetDesign();
  if (!KRIG.sites.length) await krig3LoadSites();
  KRIG.selectingArea = false;
  KRIG.drawingPark = false;
  KRIG.placingPoi = true;
  // Cambiar cursor del mapa para feedback visual.
  if (KRIG.map && KRIG.map._container) KRIG.map._container.style.cursor = 'crosshair';
  toast('Haz clic sobre el mapa para marcar el lugar de emplazamiento del parque.', 'info');
}

async function krig3PlacePoi(latlng) {
  krig3InitMap();
  if (KRIG.result) krig3ResetDesign();
  if (!KRIG.sites.length) await krig3LoadSites();
  KRIG.selectingArea = false;
  KRIG.drawingPark = false;
  KRIG.placingPoi = false;
  if (KRIG.map && KRIG.map._container) KRIG.map._container.style.cursor = '';
  KRIG.poi = { lat: latlng.lat, lon: latlng.lng };

  // Limpia marcador y círculo previos
  if (KRIG.poiMarker) { KRIG.map.removeLayer(KRIG.poiMarker); KRIG.poiMarker = null; }
  if (KRIG.poiCircle) { KRIG.map.removeLayer(KRIG.poiCircle); KRIG.poiCircle = null; }

  const icon = L.divIcon({
    className: 'krig3-poi-icon',
    html: krig3PoiIconHtml(true),
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  });
  KRIG.poiMarker = L.marker([KRIG.poi.lat, KRIG.poi.lon], {
    icon, zIndexOffset: 1000, interactive: true, draggable: false,
  }).addTo(KRIG.map);
  KRIG.poiMarker.bindTooltip('Emplazamiento del parque', { direction: 'top', offset: [0, -42] });

  krig3FilterByRadius();
}

function krig3ClearPoi() {
  KRIG.poi = null;
  KRIG.placingPoi = false;
  if (KRIG.map && KRIG.map._container) KRIG.map._container.style.cursor = '';
  if (KRIG.poiMarker)        { KRIG.map.removeLayer(KRIG.poiMarker);        KRIG.poiMarker = null; }
  if (KRIG.poiCircle)        { KRIG.map.removeLayer(KRIG.poiCircle);        KRIG.poiCircle = null; }
  if (KRIG.poiResultMarker)  { KRIG.map.removeLayer(KRIG.poiResultMarker);  KRIG.poiResultMarker = null; }
  const info = document.getElementById('krig3-poi-info');
  if (info) info.textContent = '—';
}

function krig3FilterByRadius() {
  if (!KRIG.poi) return;
  krig3InitMap();
  const radInput = document.getElementById('krig3-poi-radius');
  const km = Math.max(1, parseFloat(radInput?.value) || 100);

  // Dibuja / actualiza el círculo de radio
  if (KRIG.poiCircle) KRIG.map.removeLayer(KRIG.poiCircle);
  KRIG.poiCircle = L.circle([KRIG.poi.lat, KRIG.poi.lon], {
    radius: km * 1000,
    color: '#0ea5e9', weight: 2, dashArray: '6 4',
    fillColor: '#0ea5e9', fillOpacity: 0.06, interactive: false,
  }).addTo(KRIG.map);

  // Reset selección y selecciona estaciones dentro del radio
  KRIG.selected.clear();
  KRIG.principal = null;
  let inside = 0;
  let nearest = null, nearestD = Infinity;
  KRIG.sites.forEach(s => {
    const d = krig3HaversineKm(KRIG.poi.lat, KRIG.poi.lon, s.lat, s.lon);
    if (d <= km) {
      KRIG.selected.add(s.filename);
      inside++;
      if (d < nearestD) { nearestD = d; nearest = s; }
    }
  });
  // Marca como principal automáticamente la estación más cercana
  if (nearest) KRIG.principal = nearest.filename;
  krig3UpdateAllMarkers();

  const info = document.getElementById('krig3-poi-info');
  if (info) {
    info.innerHTML = `${KRIG.poi.lat.toFixed(3)}°, ${KRIG.poi.lon.toFixed(3)}° · ` +
      `<b>${inside}</b> estaciones dentro de ${km} km` +
      (nearest ? ` · ★ ${nearest.display_name || nearest.filename} (${nearestD.toFixed(1)} km)` : '');
  }
  if (inside < 3) {
    toast(`Solo ${inside} estaciones dentro de ${km} km. Aumenta el radio para Kriging (≥ 3 sitios).`,
          inside === 0 ? 'error' : 'warning');
  } else {
    toast(`${inside} estaciones seleccionadas en un radio de ${km} km.`, 'success');
  }
}

// ═══════════════════════ Bilineal sampling sobre el grid ══════════════════════
function krig3SampleBilinear(grid, lat, lon) {
  if (!grid || !grid.bounds) return null;
  const b = grid.bounds;
  if (lat < b.lat_min || lat > b.lat_max || lon < b.lon_min || lon > b.lon_max) return null;
  const z = grid.z;
  const ny = z.length, nx = z[0].length;
  if (ny < 2 || nx < 2) return null;
  const fx = (lon - b.lon_min) / (b.lon_max - b.lon_min) * (nx - 1);
  const fy = (lat - b.lat_min) / (b.lat_max - b.lat_min) * (ny - 1);
  const i0 = Math.max(0, Math.min(nx - 2, Math.floor(fx)));
  const j0 = Math.max(0, Math.min(ny - 2, Math.floor(fy)));
  const dx = fx - i0, dy = fy - j0;
  const v00 = z[j0  ][i0  ], v10 = z[j0  ][i0+1];
  const v01 = z[j0+1][i0  ], v11 = z[j0+1][i0+1];
  return v00*(1-dx)*(1-dy) + v10*dx*(1-dy) + v01*(1-dx)*dy + v11*dx*dy;
}

// ═══════════════════════ Hover sobre mapa de resultado ════════════════════════
function krig3HandleResultMouseMove(e) {
  const tip = document.getElementById('krig3-hover-tip');
  if (!tip) return;
  if (!KRIG.result) { tip.style.display = 'none'; return; }
  const grid = KRIG.result[KRIG.layerKey];
  if (!grid) { tip.style.display = 'none'; return; }
  const v = krig3SampleBilinear(grid, e.latlng.lat, e.latlng.lng);
  if (v === null || !isFinite(v)) { tip.style.display = 'none'; return; }
  const meta = KRIG_LAYERS[KRIG.layerKey] || { unit: '' };
  // Formateo según magnitud
  let valStr;
  if (Math.abs(v) >= 1e6)      valStr = (v/1e6).toFixed(2) + ' M';
  else if (Math.abs(v) >= 1e3) valStr = (v/1e3).toFixed(2) + ' k';
  else if (Math.abs(v) >= 1)   valStr = v.toFixed(2);
  else                         valStr = v.toFixed(4);
  tip.innerHTML =
    `${e.latlng.lat.toFixed(3)}°, ${e.latlng.lng.toFixed(3)}°<br>` +
    `<b>${valStr} ${meta.unit}</b>`;
  tip.style.display = 'block';
  const oe = e.originalEvent;
  if (oe) {
    tip.style.left = (oe.clientX + 14) + 'px';
    tip.style.top  = (oe.clientY + 14) + 'px';
  }
}

function krig3HandleResultMouseOut() {
  const tip = document.getElementById('krig3-hover-tip');
  if (tip) tip.style.display = 'none';
}

// ═══════════════════════ POI marker sobre el resultado ════════════════════════
function krig3RenderPoiOnResult() {
  if (!KRIG.result || !KRIG.poi) return;
  if (KRIG.poiResultMarker) {
    KRIG.map.removeLayer(KRIG.poiResultMarker);
    KRIG.poiResultMarker = null;
  }
  const grid = KRIG.result[KRIG.layerKey];
  if (!grid) return;
  const v = krig3SampleBilinear(grid, KRIG.poi.lat, KRIG.poi.lon);
  const meta = KRIG_LAYERS[KRIG.layerKey] || { unit: '' };
  const icon = L.divIcon({
    className: 'krig3-poi-result',
    html: krig3PoiIconHtml(true),
    iconSize: [0, 0], iconAnchor: [0, 0],
  });
  KRIG.poiResultMarker = L.marker([KRIG.poi.lat, KRIG.poi.lon], {
    icon, zIndexOffset: 1000, interactive: true,
  }).addTo(KRIG.map);
  let valHtml;
  if (v === null || !isFinite(v)) {
    valHtml = '<i>Sin valor en este punto</i>';
  } else {
    let s;
    if (Math.abs(v) >= 1e6)      s = (v/1e6).toFixed(2) + ' M';
    else if (Math.abs(v) >= 1e3) s = (v/1e3).toFixed(2) + ' k';
    else if (Math.abs(v) >= 1)   s = v.toFixed(2);
    else                         s = v.toFixed(4);
    valHtml = `<b>${s} ${meta.unit}</b>`;
  }
  KRIG.poiResultMarker.bindTooltip(
    `<div style="text-align:center"><b>Emplazamiento</b><br>${valHtml}</div>`,
    { permanent: true, direction: 'top', offset: [0, -42], className: 'krig3-poi-tooltip' }
  );
}

// ═══════════════════════ Reiniciar diseño ═════════════════════════════════════
// Limpia los overlays del Kriging y devuelve la UI al modo edición / selección.
// CONSERVA: estaciones seleccionadas, POI, polígono del parque, capa base.
function krig3ResetDesign() {
  // Quitar overlays de resultado
  if (KRIG.map) KRIG.overlays.forEach(l => KRIG.map.removeLayer(l));
  KRIG.overlays = [];
  if (KRIG.poiResultMarker && KRIG.map) {
    KRIG.map.removeLayer(KRIG.poiResultMarker);
    KRIG.poiResultMarker = null;
  }
  KRIG.result = null;
  KRIG.task_id = null;

  // Restaurar markers de selección (los oculta krig3Poll cuando llega resultado)
  Object.values(KRIG.siteMarkers).forEach(m => {
    if (KRIG.map && !KRIG.map.hasLayer(m)) m.addTo(KRIG.map);
  });
  krig3UpdateAllMarkers();

  // Reset visual de tabs físicos y panel
  document.querySelectorAll('.krig3-tab-phys').forEach(b => b.style.display = 'none');
  KRIG.layerKey = 'wind';
  document.querySelectorAll('.krig3-tab').forEach(b => {
    b.classList.toggle('is-active', b.dataset.layer === 'wind');
  });
  const btnReset = document.getElementById('btn-krig3-reset');
  if (btnReset) btnReset.style.display = 'none';
  const tip = document.getElementById('krig3-hover-tip');
  if (tip) tip.style.display = 'none';

  document.getElementById('krig3-status').textContent = 'En espera';
  document.getElementById('krig3-progress-wrap').style.display = 'none';
  document.getElementById('krig3-progress-fill').style.width = '0%';
  document.getElementById('krig3-progress-text').textContent = '';
  document.getElementById('krig3-map-title').textContent = 'Mapa Kriging';
  document.getElementById('krig3-legend').textContent = '—';

  toast('Volviendo al modo de diseño. Las estaciones, el emplazamiento y el polígono se conservan.', 'info');
}

// ─── Buscador estilo Google Maps (Nominatim/OSM) ──────────────────────────────
let _krig3SearchTimer = null;
let _krig3SearchSeq = 0;

// Permite que el usuario teclee "16.5, -95.0" y caiga directo al punto.
function krig3ParseLatLon(q) {
  const m = q.trim().match(/^(-?\d+(?:[.,]\d+)?)[\s,;]+(-?\d+(?:[.,]\d+)?)$/);
  if (!m) return null;
  const lat = parseFloat(m[1].replace(',', '.'));
  const lon = parseFloat(m[2].replace(',', '.'));
  if (!isFinite(lat) || !isFinite(lon)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon, label: `Coordenadas ${lat.toFixed(4)}, ${lon.toFixed(4)}` };
}

async function krig3SearchSuggest(query) {
  const box = document.getElementById('krig3-search-suggest');
  if (!box) return;
  const q = (query || '').trim();
  if (q.length < 3) { box.style.display = 'none'; box.innerHTML = ''; return; }

  // Atajo: si parece "lat,lon" lo mostramos como única sugerencia.
  const ll = krig3ParseLatLon(q);
  if (ll) {
    box.innerHTML =
      `<div class="krig3-sg" data-lat="${ll.lat}" data-lon="${ll.lon}" data-label="${ll.label}"
            style="padding:.5rem .6rem;cursor:pointer;border-bottom:1px solid var(--border)">
         <b>📍 ${ll.label}</b>
       </div>`;
    box.style.display = 'block';
    krig3BindSuggestions();
    return;
  }

  const seq = ++_krig3SearchSeq;
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&addressdetails=1&q=${encodeURIComponent(q)}`;
    const r = await fetch(url, { headers: { 'Accept-Language': 'es,en' } });
    if (seq !== _krig3SearchSeq) return;  // resultado obsoleto
    const data = await r.json();
    if (!Array.isArray(data) || !data.length) {
      box.innerHTML = `<div style="padding:.5rem .6rem;color:var(--text-muted)">Sin resultados.</div>`;
      box.style.display = 'block';
      return;
    }
    box.innerHTML = data.map(d => {
      const label = d.display_name.replace(/"/g, '&quot;');
      return `<div class="krig3-sg" data-lat="${d.lat}" data-lon="${d.lon}" data-label="${label}"
                  style="padding:.5rem .6rem;cursor:pointer;border-bottom:1px solid var(--border)">
                <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${d.display_name}</div>
              </div>`;
    }).join('');
    box.style.display = 'block';
    krig3BindSuggestions();
  } catch (e) {
    box.innerHTML = `<div style="padding:.5rem .6rem;color:var(--orange)">Error de búsqueda.</div>`;
    box.style.display = 'block';
  }
}

function krig3BindSuggestions() {
  document.querySelectorAll('#krig3-search-suggest .krig3-sg').forEach(el => {
    el.addEventListener('click', () => {
      const lat = parseFloat(el.getAttribute('data-lat'));
      const lon = parseFloat(el.getAttribute('data-lon'));
      const label = el.getAttribute('data-label') || '';
      krig3FlyToSearchResult(lat, lon, label);
    });
    el.addEventListener('mouseenter', () => el.style.background = 'var(--bg-hover)');
    el.addEventListener('mouseleave', () => el.style.background = '');
  });
}

function krig3FlyToSearchResult(lat, lon, label) {
  if (!isFinite(lat) || !isFinite(lon)) return;
  krig3InitMap();
  // Si está fijado el modo México y caemos fuera, abrimos a "ver resto".
  if (KRIG.mode === 'mexico') {
    const inside = lat >= KRIG_MEXICO_BOUNDS[0][0] && lat <= KRIG_MEXICO_BOUNDS[1][0] &&
                   lon >= KRIG_MEXICO_BOUNDS[0][1] && lon <= KRIG_MEXICO_BOUNDS[1][1];
    if (!inside) krig3SetExtent('world');
  }
  // Limpiar marcador anterior
  if (KRIG.searchMarker) { KRIG.map.removeLayer(KRIG.searchMarker); KRIG.searchMarker = null; }
  KRIG.searchMarker = L.marker([lat, lon], { title: label })
    .addTo(KRIG.map)
    .bindPopup(`<b>${label}</b><br>${lat.toFixed(4)}, ${lon.toFixed(4)}`)
    .openPopup();
  KRIG.map.setView([lat, lon], 12, { animate: true });
  // Cerrar el panel de sugerencias
  const box = document.getElementById('krig3-search-suggest');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
  const inp = document.getElementById('krig3-search-input');
  if (inp) inp.value = label;
}

async function krig3SearchSubmit() {
  const inp = document.getElementById('krig3-search-input');
  if (!inp) return;
  const q = inp.value.trim();
  if (!q) return;
  const ll = krig3ParseLatLon(q);
  if (ll) { krig3FlyToSearchResult(ll.lat, ll.lon, ll.label); return; }
  // Cae directo al primer resultado de Nominatim.
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(q)}`;
    const r = await fetch(url, { headers: { 'Accept-Language': 'es,en' } });
    const data = await r.json();
    if (!Array.isArray(data) || !data.length) {
      toast('No se encontró ese lugar.', 'warning');
      return;
    }
    const d = data[0];
    krig3FlyToSearchResult(parseFloat(d.lat), parseFloat(d.lon), d.display_name);
  } catch (e) {
    toast('Error de búsqueda: ' + e.message, 'error');
  }
}

function krig3SearchClear() {
  if (KRIG.searchMarker && KRIG.map) { KRIG.map.removeLayer(KRIG.searchMarker); KRIG.searchMarker = null; }
  const inp = document.getElementById('krig3-search-input');
  if (inp) inp.value = '';
  const box = document.getElementById('krig3-search-suggest');
  if (box) { box.style.display = 'none'; box.innerHTML = ''; }
}

// ─── Fase 2: enriquecimiento físico de sitios (altitud + z₀) ──────────────────
// Mapa filename → {elevation_m, landcover_class, landcover_label, z0, landcover_purity}
KRIG.terrain = {};
KRIG.terrainLoaded = false;

async function krig3LoadTerrain(force = false) {
  if (KRIG.terrainLoaded && !force) {
    toast('Datos de terreno ya cargados (cache).', 'info');
    return;
  }
  if (!KRIG.sites.length) {
    toast('Carga sitios primero.', 'warning');
    return;
  }
  const btn = document.getElementById('btn-krig3-enrich');
  const stat = document.getElementById('krig3-enrich-status');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Consultando DEM y cobertura…'; }
  if (stat) stat.textContent = `Consultando ${KRIG.sites.length} sitios…`;
  try {
    const payload = { sites: KRIG.sites.map(s => ({
      filename: s.filename, lat: s.lat, lon: s.lon,
    })) };
    const r = await api('/api/terrain/enrich', 'POST', payload);
    const enriched = (r && r.sites) || [];
    KRIG.terrain = {};
    let nElev = 0, nLc = 0;
    enriched.forEach(s => {
      KRIG.terrain[s.filename] = {
        elevation_m: s.elevation_m,
        z0: s.z0,
        landcover_class: s.landcover_class,
        landcover_label: s.landcover_label,
        landcover_purity: s.landcover_purity,
      };
      if (s.elevation_m != null) nElev++;
      if (s.z0 != null) nLc++;
    });
    KRIG.terrainLoaded = true;
    if (stat) stat.textContent = `${nElev}/${enriched.length} altitudes · ${nLc}/${enriched.length} con z₀`;
    // Refrescar popups y panel óptimos.
    Object.keys(KRIG.siteMarkers).forEach(fn => {
      const m = KRIG.siteMarkers[fn];
      const site = KRIG.sites.find(x => x.filename === fn);
      if (m && site) m.setPopupContent(krig3SitePopupHtml(site));
    });
    krig3RenderOptimalList();
    toast('Terreno cargado.', 'success');
  } catch (e) {
    if (stat) stat.textContent = `Error: ${e.message}`;
    toast(`No se pudo cargar terreno: ${e.message}`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🌐 Cargar terreno (DEM + cobertura)'; }
  }
}

function krig3TerrainBadge(filename) {
  const t = KRIG.terrain && KRIG.terrain[filename];
  if (!t) return '';
  const parts = [];
  if (t.elevation_m != null) parts.push(`<b>${t.elevation_m.toFixed(0)} m</b> snm`);
  if (t.landcover_label) {
    const pur = (t.landcover_purity != null) ? ` · ${(t.landcover_purity*100).toFixed(0)}%` : '';
    parts.push(`${t.landcover_label}${pur}`);
  }
  if (t.z0 != null) parts.push(`z₀ <b>${t.z0}</b> m`);
  return parts.length ? `<br><span style="color:var(--text-muted);font-size:.72rem">${parts.join(' · ')}</span>` : '';
}

// ─── Overlays físicos: hillshade (ESRI) y cobertura del suelo (ESA WorldCover) ─
// URLs de tiles públicos sin API key.
const KRIG_HILLSHADE_URL =
  'https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}';
// El TileMatrix en este WMTS sigue el patrón "EPSG:3857:N" (no solo "N").
const KRIG_LANDCOVER_URL =
  'https://services.terrascope.be/wmts/v2?layer=WORLDCOVER_2021_MAP'
  + '&style=default&tilematrixset=EPSG%3A3857'
  + '&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image%2Fpng'
  + '&TileMatrix=EPSG%3A3857%3A{z}&TileCol={x}&TileRow={y}';

function krig3ToggleHillshade(on) {
  krig3InitMap();
  if (on) {
    if (KRIG.hillshadeLayer) return;
    KRIG.hillshadeLayer = L.tileLayer(KRIG_HILLSHADE_URL, {
      maxZoom: 18, opacity: KRIG.overlayOpacity,
      attribution: '© ESRI World Hillshade',
      crossOrigin: 'anonymous',
    }).addTo(KRIG.map);
    if (KRIG.hillshadeLayer.bringToFront) KRIG.hillshadeLayer.bringToFront();
  } else if (KRIG.hillshadeLayer) {
    KRIG.map.removeLayer(KRIG.hillshadeLayer);
    KRIG.hillshadeLayer = null;
  }
  krig3SyncOverlayUI();
}

function krig3ToggleLandcover(on) {
  krig3InitMap();
  if (on) {
    if (KRIG.landcoverLayer) return;
    KRIG.landcoverLayer = L.tileLayer(KRIG_LANDCOVER_URL, {
      maxZoom: 13, minZoom: 1, opacity: KRIG.overlayOpacity,
      attribution: '© ESA WorldCover 2021 · Terrascope',
      crossOrigin: 'anonymous',
    }).addTo(KRIG.map);
    if (KRIG.landcoverLayer.bringToFront) KRIG.landcoverLayer.bringToFront();
  } else if (KRIG.landcoverLayer) {
    KRIG.map.removeLayer(KRIG.landcoverLayer);
    KRIG.landcoverLayer = null;
  }
  krig3SyncOverlayUI();
}

function krig3SetOverlayOpacity(v) {
  KRIG.overlayOpacity = Math.max(0.05, Math.min(1, +v));
  if (KRIG.hillshadeLayer) KRIG.hillshadeLayer.setOpacity(KRIG.overlayOpacity);
  if (KRIG.landcoverLayer) KRIG.landcoverLayer.setOpacity(KRIG.overlayOpacity);
}

function krig3SyncOverlayUI() {
  // La leyenda de WorldCover sólo se muestra si está activo el overlay correspondiente.
  const legendBox = document.getElementById('krig3-landcover-legend');
  if (legendBox) legendBox.style.display = KRIG.landcoverLayer ? 'block' : 'none';
  // Mantener overlays Kriging por encima de los terrenos
  if (Array.isArray(KRIG.overlays)) {
    KRIG.overlays.forEach(layer => {
      if (layer && typeof layer.bringToFront === 'function') layer.bringToFront();
    });
  }
}

// ─── Descargar el mapa como imagen PNG (html2canvas) ──────────────────────────
async function krig3DownloadMap() {
  if (typeof html2canvas === 'undefined') {
    toast('No se cargó html2canvas. Verifica tu conexión.', 'error');
    return;
  }
  const node = document.getElementById('krig3-map');
  if (!node) return;
  const btn = document.getElementById('btn-krig3-download');
  const prevTxt = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Generando…'; }
  // Forzar repintado completo de tiles antes de capturar.
  if (KRIG.map) KRIG.map.invalidateSize();
  await new Promise(r => setTimeout(r, 250));
  try {
    const canvas = await html2canvas(node, {
      useCORS: true,
      allowTaint: false,
      backgroundColor: null,
      logging: false,
      scale: window.devicePixelRatio || 1,
    });
    // Disparar descarga.
    const layer = (KRIG.layerKey || 'mapa').toLowerCase();
    const ts = new Date().toISOString().replace(/[:T]/g, '-').slice(0, 19);
    const fname = `kriging_${layer}_${ts}.png`;
    const url = canvas.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = url; a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    toast(`Imagen descargada: ${fname}`, 'success');
  } catch (e) {
    console.error('krig3DownloadMap', e);
    toast(`No se pudo exportar la imagen: ${e.message}. Prueba con otra capa base.`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = prevTxt || '⬇ Descargar imagen'; }
  }
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

function krig3SiteIconHtml(filename) {
  const isPrincipal = filename === KRIG.principal;
  const isSelected = KRIG.selected.has(filename);
  const bg = isPrincipal ? '#facc15' : (isSelected ? '#3b82f6' : '#475569');
  const stroke = isPrincipal ? '#78350f' : (isSelected ? '#dbeafe' : '#cbd5e1');
  const glyph = isPrincipal
    ? `<path d="M12 5l1.8 3.9 4.2.5-3.1 2.9.8 4.2L12 14.4 8.3 16.5l.8-4.2L6 9.4l4.2-.5L12 5z" fill="#78350f"/>`
    : `<path d="M7 16h10M9 16V8.5A3 3 0 0 1 12 5.5h0a3 3 0 0 1 3 3V16M8.5 10h7M10 7.2h4" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round"/>`;
  return `<div style="width:${isPrincipal ? 30 : 24}px;height:${isPrincipal ? 30 : 24}px;border-radius:999px;background:${bg};border:2px solid #fff;box-shadow:0 4px 12px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center">
    <svg viewBox="0 0 24 24" width="${isPrincipal ? 21 : 17}" height="${isPrincipal ? 21 : 17}" aria-hidden="true">${glyph}</svg>
  </div>`;
}

function krig3SiteIcon(filename) {
  const isPrincipal = filename === KRIG.principal;
  const size = isPrincipal ? 30 : 24;
  return L.divIcon({
    className: 'krig3-site-mini-icon',
    html: krig3SiteIconHtml(filename),
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -size / 2],
  });
}

function krig3ResultSiteIcon(filename, color) {
  const isPrincipal = filename === KRIG.principal;
  const size = isPrincipal ? 30 : 24;
  const stroke = isPrincipal ? '#78350f' : '#f8fafc';
  const glyph = isPrincipal
    ? `<path d="M12 5l1.8 3.9 4.2.5-3.1 2.9.8 4.2L12 14.4 8.3 16.5l.8-4.2L6 9.4l4.2-.5L12 5z" fill="#78350f"/>`
    : `<path d="M7 16h10M9 16V8.5A3 3 0 0 1 12 5.5h0a3 3 0 0 1 3 3V16M8.5 10h7M10 7.2h4" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linecap="round"/>`;
  return L.divIcon({
    className: 'krig3-site-result-icon',
    html: `<div style="width:${size}px;height:${size}px;border-radius:999px;background:${color};border:2px solid ${isPrincipal ? '#facc15' : '#fff'};box-shadow:0 4px 12px rgba(0,0,0,.42);display:flex;align-items:center;justify-content:center">
      <svg viewBox="0 0 24 24" width="${isPrincipal ? 21 : 17}" height="${isPrincipal ? 21 : 17}" aria-hidden="true">${glyph}</svg>
    </div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -size / 2],
  });
}

function krig3UpdateMarker(filename) {
  const m = KRIG.siteMarkers[filename];
  if (m) {
    m.setIcon(krig3SiteIcon(filename));
    if (m.setZIndexOffset) m.setZIndexOffset(filename === KRIG.principal ? 500 : 0);
  }
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
        v̄ ${(+s.v_mean).toFixed(2)} m/s · ráfaga ${(+s.gust_ms || +s.v_max || 0).toFixed(2)} m/s<br>
        densidad ${(+s.power_density_wm2 || 0).toFixed(1)} W/m² · Weibull C=${(+s.weibull_C).toFixed(2)} K=${(+s.weibull_K).toFixed(2)}
        ${krig3TerrainBadge(s.filename)}
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
    const htParam = WP.referenceHeight ? `&target_height_m=${WP.referenceHeight}` : '';
    const r = await api(`/api/kriging/sites?year=${year}${htParam}`, 'GET');
    KRIG.sites = r.sites || [];
    KRIG.selected = new Set(KRIG.sites.map(s => s.filename));  // por defecto todos
    KRIG.principal = null;
    // limpiar marcadores anteriores
    Object.values(KRIG.siteMarkers).forEach(m => map.removeLayer(m));
    KRIG.siteMarkers = {};
    KRIG.sites.forEach(s => {
      const m = L.marker([s.lat, s.lon], {
          icon: krig3SiteIcon(s.filename),
          riseOnHover: true,
          zIndexOffset: KRIG.principal === s.filename ? 500 : 0,
        })
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

  krig3RenderPoiOnResult();

  document.getElementById('krig3-map-title').textContent = grid.title || meta.title;
  document.getElementById('krig3-legend').textContent = krig3FormatRange(grid, meta.unit);
  setTimeout(() => map.invalidateSize(), 60);
}

// ── Auto-zoom alrededor del parque principal tras correr Kriging ──────────────
// Si no hay parque principal, encuadra al extent del grid para que el overlay sea visible.
function krig3ZoomToPrincipal() {
  if (!KRIG.map) return;
  if (KRIG.areaBounds) {
    KRIG.map.fitBounds(KRIG.areaBounds, { padding: [24, 24] });
    return;
  }
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
  const physical = !!document.getElementById('krig3-physical')?.checked;
  const body = {
    year:             parseInt(document.getElementById('krig3-year').value),
    demand_mwh:       parseFloat(document.getElementById('krig3-demand').value),
    tasa:             parseFloat(document.getElementById('krig3-tasa').value) / 100,
    price_usd_kwh:    parseFloat(document.getElementById('krig3-price').value),
    price_mxn_per_usd: parseFloat(document.getElementById('krig3-fx').value),
    n_grid:           parseInt(document.getElementById('krig3-n').value),
    model:            document.getElementById('krig3-model').value,
    filenames:        Array.from(KRIG.selected),
    physical:         physical,
    target_height_m:  wsH(),
  };
  if (principal) {
    body.center_file = principal.filename;
    body.center_lat  = principal.lat;
    body.center_lon  = principal.lon;
  }
  if (KRIG.areaBounds) {
    const sw = KRIG.areaBounds.getSouthWest();
    const ne = KRIG.areaBounds.getNorthEast();
    body.grid_bounds = {
      lat_min: sw.lat,
      lat_max: ne.lat,
      lon_min: sw.lng,
      lon_max: ne.lng,
    };
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
      // Mostrar u ocultar tabs físicos según el modo.
      const physOn = !!d.physical;
      document.querySelectorAll('.krig3-tab-phys').forEach(b => {
        b.style.display = physOn ? '' : 'none';
      });
      // Si el modo cambió y la pestaña activa ya no aplica, vuelve a "wind".
      if (!physOn && (KRIG.layerKey || '').match(/^(wind_directional|wind_neutral|elevation|roughness|air_density)$/)) {
        KRIG.layerKey = 'wind';
      }
      // Oculta los markers de selección mientras se muestra el resultado.
      Object.values(KRIG.siteMarkers).forEach(m => KRIG.map && KRIG.map.removeLayer(m));
      krig3SetLayer(KRIG.layerKey || 'wind');
      krig3PopulateCurveSelect();
      krig3RenderCurve();
      // Auto-zoom alrededor del parque principal (100 km por defecto).
      krig3ZoomToPrincipal();
      const nCurves = (d.turbine_curves || []).length;
      const tag = physOn ? ` · física (${d.n_corrected || 0}/${d.n_sites})` : '';
      const nMaps = Object.keys(d).filter(k => d[k] && Array.isArray(d[k].z)).length;
      document.getElementById('krig3-status').textContent =
        `${d.n_sites} sitios · ${nMaps} mapas · ${nCurves} curvas${tag}`;
      const btnReset = document.getElementById('btn-krig3-reset');
      if (btnReset) btnReset.style.display = '';
      document.getElementById('krig3-progress-fill').style.width = '100%';
      toast(physOn ? 'Kriging físico listo' : 'Kriging listo', 'success');
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
        map.on('mousemove', krig3HandleResultMouseMove);
        map.on('mouseout',  krig3HandleResultMouseOut);
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
  document.getElementById('btn-krig3-place-poi')  ?.addEventListener('click', krig3StartPoiPlacement);
  document.getElementById('btn-krig3-clear-poi')  ?.addEventListener('click', krig3ClearPoi);
  document.getElementById('btn-krig3-reset')      ?.addEventListener('click', krig3ResetDesign);
  document.getElementById('krig3-poi-radius')     ?.addEventListener('input', krig3FilterByRadius);
  document.getElementById('krig3-poi-radius')     ?.addEventListener('change', krig3FilterByRadius);
  document.getElementById('btn-krig3-draw-park')  ?.addEventListener('click', krig3StartParkDraw);
  document.getElementById('btn-krig3-finish-park')?.addEventListener('click', krig3FinishPark);
  document.getElementById('btn-krig3-clear-park') ?.addEventListener('click', krig3ClearPark);
  document.getElementById('krig3-baselayer')?.addEventListener('change', (e) => {
    krig3InitMap();
    krig3SetBase(e.target.value);
  });

  // ── Buscador estilo Google Maps (Nominatim) ──
  const searchInp = document.getElementById('krig3-search-input');
  if (searchInp) {
    searchInp.addEventListener('input', (e) => {
      clearTimeout(_krig3SearchTimer);
      const v = e.target.value;
      _krig3SearchTimer = setTimeout(() => krig3SearchSuggest(v), 320);
    });
    searchInp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); krig3SearchSubmit(); }
      else if (e.key === 'Escape') {
        const box = document.getElementById('krig3-search-suggest');
        if (box) { box.style.display = 'none'; box.innerHTML = ''; }
      }
    });
    searchInp.addEventListener('blur', () => {
      // Pequeño delay para permitir click en sugerencia antes de ocultar.
      setTimeout(() => {
        const box = document.getElementById('krig3-search-suggest');
        if (box) box.style.display = 'none';
      }, 200);
    });
    searchInp.addEventListener('focus', () => {
      const box = document.getElementById('krig3-search-suggest');
      if (box && box.innerHTML.trim()) box.style.display = 'block';
    });
  }
  document.getElementById('btn-krig3-search')?.addEventListener('click', krig3SearchSubmit);
  document.getElementById('btn-krig3-search-clear')?.addEventListener('click', krig3SearchClear);
  document.getElementById('btn-krig3-download')?.addEventListener('click', krig3DownloadMap);

  // ── Overlays físicos (hillshade + cobertura) ──
  document.getElementById('krig3-ovl-hillshade')?.addEventListener('change',
    (e) => krig3ToggleHillshade(e.target.checked));
  document.getElementById('krig3-ovl-landcover')?.addEventListener('change',
    (e) => krig3ToggleLandcover(e.target.checked));
  document.getElementById('krig3-ovl-opacity')?.addEventListener('input',
    (e) => krig3SetOverlayOpacity((+e.target.value) / 100));

  // ── Botón "Cargar terreno" (Fase 2: enriquecimiento de sitios) ──
  document.getElementById('btn-krig3-enrich')?.addEventListener('click', () => krig3LoadTerrain(true));

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
  lastSpatial:  null,
  defaults:     null,
  scope:        'area',
  miniMap:      null,
  miniBase:     null,
  miniLayers:   [],
  turbines:     [],
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

function mcdaUpdateTurbineCount() {
  const total = document.querySelectorAll('.mcda-turbine-check').length;
  const selected = document.querySelectorAll('.mcda-turbine-check:checked').length;
  const el = document.getElementById('mcda-turbine-count');
  if (el) el.textContent = total ? `${selected}/${total} seleccionadas` : 'Sin catálogo';
}

function mcdaSelectedTurbines() {
  return Array.from(document.querySelectorAll('.mcda-turbine-check:checked'))
    .map(el => el.value);
}

async function mcdaLoadTurbines() {
  const list = document.getElementById('mcda-turbine-list');
  if (!list) return;
  try {
    const r = await api('/api/energy/turbines', 'GET');
    MCDA.turbines = (r.turbines || []).filter(t => t.source === 'file');
    if (!MCDA.turbines.length) {
      list.innerHTML = '<span class="text-muted">No hay turbinas CSV disponibles.</span>';
      mcdaUpdateTurbineCount();
      return;
    }
    list.innerHTML = MCDA.turbines.map(t => {
      const mw = t.rated_power_kw ? `${(t.rated_power_kw / 1000).toFixed(1)} MW` : 'Potencia —';
      const rotor = t.rotor_diameter_m ? `Ø ${t.rotor_diameter_m} m` : 'Rotor —';
      return `
        <label style="display:flex;align-items:flex-start;gap:.45rem;padding:.28rem 0;border-bottom:1px solid var(--border)">
          <input type="checkbox" class="mcda-turbine-check" value="${t.filename}" checked style="margin-top:.15rem">
          <span>
            <strong>${t.name || t.filename}</strong><br>
            <span class="text-muted">${mw} · ${rotor} · ${t.filename}</span>
          </span>
        </label>`;
    }).join('');
    document.querySelectorAll('.mcda-turbine-check').forEach(el => {
      el.addEventListener('change', mcdaUpdateTurbineCount);
    });
    mcdaUpdateTurbineCount();
  } catch (e) {
    list.innerHTML = `<span class="text-muted">No se pudo cargar el catálogo: ${e.message}</span>`;
    mcdaUpdateTurbineCount();
  }
}

function mcdaCurrentAreaBounds() {
  if (!KRIG.areaBounds) return null;
  const sw = KRIG.areaBounds.getSouthWest();
  const ne = KRIG.areaBounds.getNorthEast();
  return {
    lat_min: sw.lat,
    lat_max: ne.lat,
    lon_min: sw.lng,
    lon_max: ne.lng,
  };
}

function mcdaParkPolygon() {
  if (!Array.isArray(KRIG.parkPoints) || KRIG.parkPoints.length < 3) return null;
  return KRIG.parkPoints.map(([lat, lon]) => ({ lat, lon }));
}

function mcdaClearSpatialMarkers() {
  if (KRIG.map && Array.isArray(KRIG.mcdaMarkers)) {
    KRIG.mcdaMarkers.forEach(m => KRIG.map.removeLayer(m));
  }
  KRIG.mcdaMarkers = [];
}

function mcdaInitMiniMap() {
  const el = document.getElementById('mcda-mini-map');
  if (!el || typeof L === 'undefined') return null;
  if (MCDA.miniMap) {
    setTimeout(() => MCDA.miniMap.invalidateSize(), 40);
    return MCDA.miniMap;
  }
  MCDA.miniMap = L.map(el, {
    center: [23.5, -102],
    zoom: 5,
    zoomControl: false,
    attributionControl: false,
    scrollWheelZoom: false,
  });
  const url = (typeof MAPA_TILES !== 'undefined' && (MAPA_TILES.voyager || MAPA_TILES.osm))
    || 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
  MCDA.miniBase = L.tileLayer(url, {
    maxZoom: 18,
    subdomains: 'abc',
    crossOrigin: 'anonymous',
  }).addTo(MCDA.miniMap);
  setTimeout(() => MCDA.miniMap.invalidateSize(), 80);
  return MCDA.miniMap;
}

function mcdaClearMiniLayers() {
  if (!MCDA.miniMap) return;
  MCDA.miniLayers.forEach(l => MCDA.miniMap.removeLayer(l));
  MCDA.miniLayers = [];
}

function mcdaRenderMiniMap(result = MCDA.lastSpatial) {
  const map = mcdaInitMiniMap();
  if (!map) return;
  mcdaClearMiniLayers();
  const title = document.getElementById('mcda-mini-title');
  const status = document.getElementById('mcda-mini-status');
  const areaMode = MCDA.scope === 'area';
  if (title) title.textContent = areaMode
    ? 'Mapa TOPSIS — parque dibujado'
    : 'Mapa TOPSIS — exploración global';

  let focusLayer = null;
  if (areaMode) {
    const poly = mcdaParkPolygon();
    if (poly) {
      focusLayer = L.polygon(poly.map(p => [p.lat, p.lon]), {
        color: '#f59e0b',
        weight: 2,
        fillColor: '#f59e0b',
        fillOpacity: 0.12,
      }).addTo(map);
      MCDA.miniLayers.push(focusLayer);
    }
  } else {
    const b = mcdaCurrentAreaBounds();
    if (b) {
      focusLayer = L.rectangle([[b.lat_min, b.lon_min], [b.lat_max, b.lon_max]], {
        color: '#22c55e',
        weight: 2,
        fillColor: '#22c55e',
        fillOpacity: 0.08,
      }).addTo(map);
      MCDA.miniLayers.push(focusLayer);
    }
  }

  const sites = Array.isArray(result?.sites) ? result.sites : [];
  const orderedSites = sites
    .slice()
    .sort((a, b) => (b.resource_score || b.v_mean || 0) - (a.resource_score || a.v_mean || 0))
    .slice(0, areaMode ? 20 : 35);
  orderedSites.forEach((s, i) => {
    const marker = L.circleMarker([s.lat, s.lon], {
      radius: i < 5 ? 4 : 3,
      color: i < 5 ? '#facc15' : '#38bdf8',
      weight: 1,
      fillColor: i < 5 ? '#facc15' : '#38bdf8',
      fillOpacity: 0.9,
    }).bindTooltip(
      `#${i + 1} ${s.name}<br>${s.lat.toFixed(5)}°, ${s.lon.toFixed(5)}°<br>` +
      `${Number(s.v_mean || 0).toFixed(2)} m/s` +
      (s.dominant_direction_deg != null ? ` · Dir ${Number(s.dominant_direction_deg).toFixed(0)}°` : ''),
      { direction: 'top' }
    ).addTo(map);
    MCDA.miniLayers.push(marker);
  });

  if (status) {
    status.textContent = orderedSites.length
      ? `${orderedSites.length} puntos`
      : (focusLayer ? 'Área enfocada' : 'Sin área');
  }
  if (orderedSites.length) {
    const bounds = L.latLngBounds(orderedSites.map(s => [s.lat, s.lon]));
    if (focusLayer && focusLayer.getBounds) bounds.extend(focusLayer.getBounds());
    map.fitBounds(bounds, { padding: [18, 18], maxZoom: 14 });
  } else if (focusLayer && focusLayer.getBounds) {
    map.fitBounds(focusLayer.getBounds(), { padding: [18, 18], maxZoom: 14 });
  }
  setTimeout(() => map.invalidateSize(), 80);
}

function mcdaRenderSpatialMarkers(result) {
  mcdaClearSpatialMarkers();
  if (!KRIG.map || !result || !Array.isArray(result.sites)) return;
  const ranking = result.ranking?.ranking || [];
  const bestBySite = new Map();
  ranking.forEach(r => {
    if (!bestBySite.has(r.site)) bestBySite.set(r.site, r);
  });
  const sites = result.sites
    .slice()
    .sort((a, b) => (b.resource_score || b.v_mean || 0) - (a.resource_score || a.v_mean || 0))
    .slice(0, MCDA.scope === 'exploratory' ? 30 : 18);
  sites.forEach((s, i) => {
    const best = bestBySite.get(s.name);
    const marker = L.circleMarker([s.lat, s.lon], {
      radius: MCDA.scope === 'exploratory' ? 3 : 2.5,
      color: i < 5 ? '#facc15' : '#38bdf8',
      weight: i < 5 ? 1.5 : 1,
      fillColor: i < 5 ? '#facc15' : '#38bdf8',
      fillOpacity: 0.85,
      interactive: true,
    }).bindPopup(
      `<b>${s.name}</b><br>` +
      `${s.lat.toFixed(5)}°, ${s.lon.toFixed(5)}°<br>` +
      `Viento real ${Number(s.v_mean || 0).toFixed(2)} m/s` +
      (s.power_density_wm2 != null ? ` · ${Number(s.power_density_wm2).toFixed(0)} W/m²` : '') +
      (s.dominant_direction_deg != null ? `<br>Sector dominante ${Number(s.dominant_direction_deg).toFixed(0)}° · concentración ${(Number(s.directional_concentration || 0) * 100).toFixed(1)}%` : '') +
      (best ? `<br><b>#${best.rank}</b> ${best.turbina} · TOPSIS ${Number(best.topsis_score || 0).toFixed(4)}` : '')
    ).addTo(KRIG.map);
    KRIG.mcdaMarkers.push(marker);
  });
}

function mcdaUpdateScopeUi() {
  const areaMode = MCDA.scope === 'area';
  document.querySelectorAll('.mcda-subtab').forEach(btn => {
    btn.classList.toggle('is-active', btn.dataset.scope === MCDA.scope);
  });
  document.querySelectorAll('.mcda-exploratory-only').forEach(el => {
    el.style.display = areaMode ? 'none' : '';
  });
  const runBtn = document.getElementById('btn-mcda-run');
  if (runBtn) runBtn.textContent = areaMode
    ? '▶ Analizar aerogeneradores del parque'
    : '▶ Ejecutar análisis exploratorio';
  const help = document.getElementById('mcda-scope-help');
  if (help) help.textContent = areaMode
    ? 'Modo parque: TOPSIS usa puntos interpolados dentro del polígono dibujado, con viento real corregido físicamente.'
    : 'Modo exploratorio: TOPSIS usa puntos interpolados dentro del recuadro y marca sitios candidatos en el mapa.';
  const info = document.getElementById('mcda-area-info');
  if (info) {
    if (areaMode) {
      const poly = mcdaParkPolygon();
      info.textContent = poly
        ? `Parque activo: ${poly.length} vértices · ${krig3PolygonAreaKm2(KRIG.parkPoints).toFixed(2)} km²`
        : 'Dibuja y cierra la figura del parque para este análisis.';
    } else {
      const b = mcdaCurrentAreaBounds();
      info.textContent = b
        ? `Área exploratoria: ${b.lat_min.toFixed(3)}–${b.lat_max.toFixed(3)}°, ${b.lon_min.toFixed(3)}–${b.lon_max.toFixed(3)}°`
        : 'Selecciona un recuadro en el mapa Kriging para la exploración global.';
    }
  }
  mcdaRenderMiniMap();
}

function mcdaSetScope(scope) {
  MCDA.scope = scope === 'exploratory' ? 'exploratory' : 'area';
  MCDA.lastAnalysis = null;
  MCDA.lastRanking = null;
  MCDA.lastSpatial = null;
  mcdaUpdateScopeUi();
  const body = document.getElementById('mcda-rank-body');
  if (body) {
    body.innerHTML = '<tr><td colspan="10" class="text-muted">Ejecuta el análisis para ver el ranking.</td></tr>';
  }
  const status = document.getElementById('mcda-status');
  if (status) status.textContent = 'Sin análisis';
  mcdaClearSpatialMarkers();
}

async function mcdaResetWeights() {
  const d = await mcdaLoadDefaults();
  document.querySelectorAll('.mcda-w').forEach(el => {
    el.value = d.weights[el.dataset.crit];
    el.nextElementSibling.textContent = (+el.value).toFixed(2);
  });
}

async function mcdaRunAnalysis() {
  krig3InitMap();
  if (!KRIG.sites.length) await krig3LoadSites();
  mcdaUpdateScopeUi();
  const areaMode = MCDA.scope === 'area';
  const areaBounds = mcdaCurrentAreaBounds();
  const parkPolygon = mcdaParkPolygon();
  if (areaMode && !parkPolygon) {
    toast('Primero dibuja y cierra la figura del parque.', 'warning');
    return;
  }
  if (!areaMode && !areaBounds) {
    toast('Primero selecciona un recuadro para la exploración global.', 'warning');
    return;
  }
  if (KRIG.selected.size < 3) {
    toast('Selecciona al menos 3 estaciones de entrada para construir el Kriging.', 'warning');
    return;
  }
  const turbineFilenames = mcdaSelectedTurbines();
  if (!turbineFilenames.length) {
    toast('Selecciona al menos una turbina de interés para TOPSIS.', 'warning');
    return;
  }
  const body = {
    scope:         areaMode ? 'park' : 'exploratory',
    year:          parseInt(document.getElementById('mcda-year').value),
    demand_mwh:    parseFloat(document.getElementById('mcda-demand').value),
    tasa:          parseFloat(document.getElementById('mcda-tasa').value) / 100,
    price_usd_kwh: parseFloat(document.getElementById('mcda-price').value),
    price_mxn_per_usd: parseFloat(document.getElementById('mcda-fx').value),
    n_grid:        parseInt(document.getElementById('krig3-n')?.value) || 80,
    model:         document.getElementById('krig3-model')?.value || 'exponential',
    filenames:     Array.from(KRIG.selected),
    weights:       mcdaGetWeights(),
    mode:          areaMode ? 'area_turbines' : document.getElementById('mcda-mode').value,
    max_points:    areaMode ? 56 : (parseInt(document.getElementById('mcda-maxfiles').value) || 60),
    turbine_filenames: turbineFilenames,
  };
  if (areaMode) body.polygon = parkPolygon;
  else body.area_bounds = areaBounds;
  document.getElementById('mcda-status').textContent = 'Ejecutando…';
  MCDA.lastSpatial = null;
  MCDA.lastAnalysis = null;
  MCDA.lastRanking = null;
  document.getElementById('mcda-progress-wrap').style.display = '';
  document.getElementById('mcda-progress-text').textContent = areaMode
    ? 'Interpolando viento real dentro del parque dibujado…'
    : 'Buscando sitios candidatos interpolados dentro del área exploratoria…';
  try {
    mcdaClearSpatialMarkers();
    const r = await api('/api/multicriteria/spatial', 'POST', body);
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
      MCDA.lastSpatial = r.result;
      MCDA.lastAnalysis = r.result?.sites || [];
      MCDA.lastRanking = r.result?.ranking || null;
      const nSites = MCDA.lastAnalysis.length;
      const nTurbs = MCDA.lastAnalysis.reduce((s, x) => s + (x.turbines||[]).length, 0);
      document.getElementById('mcda-status').textContent = MCDA.scope === 'area'
        ? `${nSites} puntos del parque · ${nTurbs} evaluaciones`
        : `${nSites} sitios candidatos · ${nTurbs} evaluaciones`;
      document.getElementById('mcda-progress-fill').style.width = '100%';
      toast('TOPSIS espacial completado.', 'success');
      mcdaRenderRanking(MCDA.lastRanking || { ranking: [] });
      mcdaRenderSpatialMarkers(r.result);
      mcdaRenderMiniMap(r.result);
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
  if (!Array.isArray(MCDA.lastAnalysis)) {
    toast('Espera a que termine el análisis antes de ranquear.', 'warning');
    return;
  }
  if (!MCDA.lastAnalysis.length) {
    toast('El análisis no encontró sitios dentro del área seleccionada.', 'warning');
    return;
  }
  const weights = mcdaGetWeights();
  const mode = MCDA.scope === 'area'
    ? 'area_turbines'
    : document.getElementById('mcda-mode').value;
  try {
    const r = await api('/api/multicriteria/rank', 'POST', {
      sites_analysis: MCDA.lastAnalysis,
      weights, mode,
    });
    MCDA.lastRanking = r;
    mcdaRenderRanking(r);
  } catch (e) {
    const status = document.getElementById('mcda-status');
    if (status) status.textContent = 'Error ranking';
    toast(`Error ranking: ${e.message}`, 'error');
  }
}

function mcdaRenderRanking(r) {
  const body = document.getElementById('mcda-rank-body');
  const ranking = Array.isArray(r && r.ranking) ? r.ranking : [];
  const top = ranking.slice(0, 100);
  if (!top.length) {
    body.innerHTML = '<tr><td colspan="10" class="text-muted">Sin resultados.</td></tr>';
    return;
  }
  body.innerHTML = top.map(a => {
    const medal = a.rank === 1 ? '🥇' : a.rank === 2 ? '🥈' : a.rank === 3 ? '🥉' : a.rank;
    const highlight = a.rank === 1 ? 'style="background:var(--green-dim)"' : '';
    const siteText = a.best_site
      ? `${a.site || ''}<br><span class="text-muted" style="font-size:.72rem">Mejor sitio: ${a.best_site}</span>`
      : `${(a.site||'').slice(0,30)}${(a.site||'').length>30?'…':''}`;
    return `<tr ${highlight}>
      <td><strong>${medal}</strong></td>
      <td title="${a.site||''}">${siteText}</td>
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
  // Propagate winner to Parque and Tecnoeconómico
  syncTopsisWinnerToModules();
}

function syncTopsisWinnerToModules() {
  if (typeof MCDA === 'undefined' || !MCDA.lastRanking) return;
  const ranking = MCDA.lastRanking.ranking || [];
  if (!ranking.length) return;

  // Unique turbines by key, sorted by TOPSIS score
  const seen = new Set();
  const items = [];
  ranking.forEach(row => {
    const key = row.turbina || row.label;
    if (key && !seen.has(key)) { seen.add(key); items.push(row); }
  });
  if (!items.length) return;

  const winner    = items[0];
  const winnerKey = winner.turbina || winner.label || '';
  if (!winnerKey) return;

  // ── 1. Parque: already handled by parquePopulateTopsisRanking ─────────
  if (typeof parquePopulateTopsisRanking === 'function') {
    parquePopulateTopsisRanking();
  }

  // ── 2. Tecnoeconómico: set #te-turbine to winner ─────────────────────
  const teSelect = document.getElementById('te-turbine');
  if (!teSelect) return;

  // Try to find option (CSV turbines are appended by bindTechno → loadTurbineList)
  let matchOpt = Array.from(teSelect.options).find(o => o.value === winnerKey);

  // If CSV turbine not yet loaded as option, add it now
  if (!matchOpt) {
    const meta = (MCDA.turbines || []).find(t => t.filename === winnerKey);
    const label = meta
      ? `${meta.name || winnerKey} — ${meta.rated_power_kw ? (meta.rated_power_kw/1000).toFixed(1)+' MW' : ''} ⌀${meta.rotor_diameter_m||'?'} m`
      : winnerKey.replace('.csv', '');
    matchOpt = new Option(label, winnerKey, false, false);
    teSelect.appendChild(matchOpt);
  }

  teSelect.value = winnerKey;

  // Badge below the select
  const badge = document.getElementById('te-turbine-topsis-badge');
  if (badge) {
    const meta  = (MCDA.turbines || []).find(t => t.filename === winnerKey);
    const name  = meta?.name || winnerKey.replace('.csv', '');
    const score = Number(winner.topsis_score || 0).toFixed(4);
    const cf    = winner.factor_capacidad != null
      ? ` · CF ${(Number(winner.factor_capacidad)*100).toFixed(1)}%` : '';
    badge.textContent = `🥇 TOPSIS #1: ${name} · Score ${score}${cf}`;
    badge.style.display = '';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  mcdaBuildWeightSliders();
  const bRun = document.getElementById('btn-mcda-run');
  const bRe  = document.getElementById('btn-mcda-rerank');
  const bRst = document.getElementById('btn-mcda-reset-w');
  if (bRun) bRun.addEventListener('click', mcdaRunAnalysis);
  if (bRe)  bRe.addEventListener('click', mcdaRankNow);
  if (bRst) bRst.addEventListener('click', mcdaResetWeights);
  document.getElementById('btn-mcda-turbines-all')?.addEventListener('click', () => {
    document.querySelectorAll('.mcda-turbine-check').forEach(el => { el.checked = true; });
    mcdaUpdateTurbineCount();
  });
  document.getElementById('btn-mcda-turbines-none')?.addEventListener('click', () => {
    document.querySelectorAll('.mcda-turbine-check').forEach(el => { el.checked = false; });
    mcdaUpdateTurbineCount();
  });
  document.querySelectorAll('.mcda-subtab').forEach(btn => {
    btn.addEventListener('click', () => mcdaSetScope(btn.dataset.scope));
  });
  mcdaLoadTurbines();
  mcdaUpdateScopeUi();
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
const PARQUE = {
  map: null, baseLayer: null, windOverlay: null, polyLayer: null,
  turbineMarkers: [], result: null, optResult: null,
  layerKey: 'wind', windVisible: true, mapLocked: false,
  wakeGroup: null, wakeVisible: false,
};

function parqueFmt(value, digits = 1, fallback = '—') {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : fallback;
}

// ── Mapa Leaflet del parque ───────────────────────────────────────────────────
function parqueInitMap() {
  const el = document.getElementById('parque-map');
  if (!el || typeof L === 'undefined') return null;
  if (PARQUE.map) {
    setTimeout(() => PARQUE.map.invalidateSize(), 60);
    return PARQUE.map;
  }
  PARQUE.map = L.map(el, {
    center: [23.5, -102], zoom: 5,
    zoomControl: true, attributionControl: false,
  });
  const tileUrl = (typeof MAPA_TILES !== 'undefined' && MAPA_TILES.dark) ||
    'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
  PARQUE.baseLayer = L.tileLayer(tileUrl, { maxZoom: 18, subdomains: 'abc', crossOrigin: 'anonymous' })
    .addTo(PARQUE.map);
  setTimeout(() => PARQUE.map.invalidateSize(), 80);
  return PARQUE.map;
}

function parqueRenderKrigingLayer(key) {
  const map = parqueInitMap();
  if (!map) return;
  let krigResult = null;
  if (typeof MAPA !== 'undefined' && MAPA.result?.grids) krigResult = MAPA.result.grids;
  else if (typeof KRIG !== 'undefined' && KRIG.result) krigResult = KRIG.result;
  
  if (!krigResult) {
    document.getElementById('parque-map-status').textContent = 'Sin Kriging';
    document.getElementById('parque-map-hint').style.display = '';
    return;
  }
  const grid = krigResult[key] || krigResult['wind'] || krigResult['speed'];
  if (!grid || !grid.z || !grid.bounds) return;
  if (PARQUE.windOverlay) { map.removeLayer(PARQUE.windOverlay); PARQUE.windOverlay = null; }
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
      img.data[idx] = r_; img.data[idx+1] = g_; img.data[idx+2] = b_; img.data[idx+3] = 175;
    }
  }
  ctx.putImageData(img, 0, 0);
  PARQUE.windOverlay = L.imageOverlay(
    canvas.toDataURL(),
    [[bounds.lat_min, bounds.lon_min], [bounds.lat_max, bounds.lon_max]],
    { opacity: 0.72 }
  ).addTo(map);
  const legendEl = document.getElementById('parque-map-legend');
  legendEl.textContent = `${parqueFmt(z_min, 1, '0.0')} – ${parqueFmt(z_max, 1, '0.0')} ${grid.title?.includes('W/m') ? 'W/m²' : 'm/s'}`;
  legendEl.style.display = '';
  document.getElementById('parque-map-status').textContent = `Kriging: ${grid.title || key}`;
  document.getElementById('parque-map-hint').style.display = 'none';
}

function parqueRenderPolygon() {
  const map = parqueInitMap();
  if (!map) return;
  if (PARQUE.polyLayer) { map.removeLayer(PARQUE.polyLayer); PARQUE.polyLayer = null; }
  let pts = null;
  if (typeof MAPA !== 'undefined' && MAPA.parkPoints?.length >= 3) pts = MAPA.parkPoints;
  else if (typeof KRIG !== 'undefined' && KRIG.parkPoints?.length >= 3) pts = KRIG.parkPoints;
  
  if (!pts || pts.length < 3) return;
  PARQUE.polyLayer = L.polygon(pts, {
    color: '#f59e0b', weight: 2.5,
    fillColor: '#f59e0b', fillOpacity: 0.08,
  }).addTo(map);
  const bounds = PARQUE.polyLayer.getBounds();
  map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
  // Bloquear mapa al área del parque por defecto
  map.setMaxBounds(bounds.pad(0.5));
  PARQUE.mapLocked = true;
  const lockBtn = document.getElementById('btn-parque-map-lock');
  if (lockBtn) lockBtn.textContent = '🔓 Ver todo';
  document.getElementById('parque-map-hint').style.display = 'none';
}

function parqueClearTurbineMarkers() {
  if (!PARQUE.map) return;
  PARQUE.turbineMarkers.forEach(m => PARQUE.map.removeLayer(m));
  PARQUE.turbineMarkers = [];
}

function _parkTurbineIconHtml(num, color) {
  const W = 32, H = 40, cx = 16;
  const topY = H * 0.12, botY = H - 5;
  const blades = [0, 120, 240].map(deg =>
    '<g transform="translate(' + cx + ' ' + (topY + 4) + ') rotate(' + deg + ')">' +
    '<path d="M -1.8 0 L 1.8 0 L 0.9 ' + (-(H * 0.26)) + ' L -0.9 ' + (-(H * 0.26)) + ' Z" fill="' + color + '" opacity="0.92" stroke="rgba(255,255,255,0.15)" stroke-width="0.3"/>' +
    '</g>'
  ).join('');
  return '<div style="position:relative;width:' + W + 'px;height:' + (H + 14) + 'px;cursor:grab">' +
    '<svg viewBox="0 0 ' + W + ' ' + H + '" style="display:block;overflow:visible" xmlns="http://www.w3.org/2000/svg">' +
    '<polygon points="' + (cx-3) + ',' + botY + ' ' + (cx+3) + ',' + botY + ' ' + (cx+1.2) + ',' + (topY+4) + ' ' + (cx-1.2) + ',' + (topY+4) + '" fill="' + color + '" stroke="rgba(255,255,255,0.2)" stroke-width="0.7"/>' +
    blades +
    '<circle cx="' + cx + '" cy="' + (topY + 4) + '" r="3" fill="#e2e8f0" stroke="' + color + '" stroke-width="1"/>' +
    '</svg>' +
    '<div style="position:absolute;bottom:0;left:50%;transform:translateX(-50%);background:' + color + ';color:#0b1220;border-radius:10px;padding:1px 5px;font-size:9px;font-weight:700;border:1px solid rgba(255,255,255,0.25);white-space:nowrap">' + num + '</div>' +
    '</div>';
}

function parquePlotTurbinesOnMap(layoutLatlon, simTurbines) {
  parqueClearTurbineMarkers();
  const map = parqueInitMap();
  if (!map || !layoutLatlon) return;
  const simRows = Array.isArray(simTurbines) ? simTurbines : [];
  const simByIndex = new Map(simRows.map((t, idx) => [Number.isFinite(Number(t?.i)) ? Number(t.i) : idx, t]));
  const aeps  = simRows.map(t => Number(t?.aep_mwh)).filter(Number.isFinite);
  const maxA  = aeps.length ? Math.max(...aeps, 0.001) : 1;
  const minA  = aeps.length ? Math.min(...aeps, maxA)  : 0;
  layoutLatlon.forEach(([lat, lon], i) => {
    const sim = simByIndex.get(i) || null;
    const aep = Number(sim?.aep_mwh);
    const t = Number.isFinite(aep) ? (aep - minA) / (maxA - minA + 1e-6) : 0.5;
    const [r_, g_, b_] = krigColor(t);
    const clr = 'rgb(' + (r_|0) + ',' + (g_|0) + ',' + (b_|0) + ')';
    const tip = sim
      ? '<b>T' + (i+1) + '</b><br>AEP: ' + parqueFmt(sim.aep_mwh, 1) + ' MWh<br>Estela: ' + parqueFmt(sim.wake_loss_pct, 1) + '%<br>CF: ' + parqueFmt(sim.cf_pct, 1) + '%'
      : '<b>T' + (i+1) + '</b><br>' + parqueFmt(lat, 5) + '°, ' + parqueFmt(lon, 5) + '°';
    const icon = L.divIcon({
      className: '',
      html: _parkTurbineIconHtml(i + 1, clr),
      iconSize: [32, 54], iconAnchor: [16, 40],
    });
    const m = L.marker([lat, lon], { icon, draggable: true }).bindPopup(tip);
    m.on('dragend', parqueMarkEdited);
    m.addTo(map);
    PARQUE.turbineMarkers.push(m);
  });
}

function parqueMarkEdited() {
  let btn = document.getElementById('btn-parque-recalc-edit');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'btn-parque-recalc-edit';
    btn.style.cssText = 'position:absolute;bottom:12px;left:50%;transform:translateX(-50%);z-index:1000;font-size:.78rem;padding:.42rem 1.1rem;background:#f59e0b;color:#0b1220;border:none;border-radius:8px;font-weight:700;cursor:pointer;box-shadow:0 2px 14px rgba(0,0,0,.5);letter-spacing:.02em';
    btn.textContent = '↺ Recalcular con posiciones editadas';
    btn.onclick = parqueRecalcEditedLayout;
    const mapEl = document.getElementById('parque-map');
    if (mapEl) { mapEl.style.position = 'relative'; mapEl.appendChild(btn); }
  }
  btn.style.display = '';
}

async function parqueRecalcEditedLayout() {
  if (!WP.sessionId) { toast('Carga datos primero.', 'warn'); return; }
  const EARTH_R = 6371000;
  const opt = PARQUE.optResult || {};
  // Best origin: centroid of original layout
  const orig = opt.layout_latlon || [];
  const lat0 = orig.length ? orig.reduce((s, p) => s + p[0], 0) / orig.length : 0;
  const lon0 = orig.length ? orig.reduce((s, p) => s + p[1], 0) / orig.length : 0;
  const lat0r = lat0 * Math.PI / 180;
  const layoutXY = PARQUE.turbineMarkers.map(m => {
    const ll = m.getLatLng();
    const x = (ll.lng - lon0) * Math.PI / 180 * EARTH_R * Math.cos(lat0r);
    const y = (ll.lat - lat0) * Math.PI / 180 * EARTH_R;
    return [x, y];
  });
  const layoutLatLon = PARQUE.turbineMarkers.map(m => { const ll = m.getLatLng(); return [ll.lat, ll.lng]; });
  const turbineKey = document.getElementById('parque-turbine')?.value;
  const hubHeight = parseFloat(document.getElementById('parque-ws-h')?.value) || 110;
  const body = {
    layout:            layoutXY,
    turbine:           { builtin_model: turbineKey, hub_height_m: hubHeight },
    ws_height_m:       wsH(),
    k_wake:            parseFloat(document.getElementById('parque-kwake')?.value) || 0.075,
    ct:                parseFloat(document.getElementById('parque-ct')?.value) || 0.8,
    availability:      (parseFloat(document.getElementById('parque-avail')?.value) || 97) / 100,
    electrical_losses: (parseFloat(document.getElementById('parque-elec')?.value) || 3) / 100,
    n_sectors:         parseInt(document.getElementById('parque-sectors')?.value) || 36,
    session_id:        WP.sessionId,
  };
  const btn = document.getElementById('btn-parque-recalc-edit');
  if (btn) btn.textContent = '⏳ Recalculando…';
  setStatus('Recalculando con layout editado…');
  try {
    const r = await api('/api/farm/simulate', 'POST', body);
    PARQUE.result = r;
    PARQUE.optResult = Object.assign({}, PARQUE.optResult, { simulation: r, layout_latlon: layoutLatLon });
    // Update marker colors
    const simTurbines = r.turbines || [];
    const aeps = simTurbines.map(t => Number(t?.aep_mwh)).filter(Number.isFinite);
    const maxA = aeps.length ? Math.max(...aeps, 0.001) : 1;
    const minA = aeps.length ? Math.min(...aeps, maxA) : 0;
    PARQUE.turbineMarkers.forEach((m, i) => {
      const sim = simTurbines[i] || null;
      const aep = Number(sim?.aep_mwh);
      const t2 = Number.isFinite(aep) ? (aep - minA) / (maxA - minA + 1e-6) : 0.5;
      const [r2, g2, b2] = krigColor(t2);
      const clr = 'rgb(' + (r2|0) + ',' + (g2|0) + ',' + (b2|0) + ')';
      m.setIcon(L.divIcon({ className: '', html: _parkTurbineIconHtml(i + 1, clr), iconSize: [32, 54], iconAnchor: [16, 40] }));
      const tip = sim
        ? '<b>T' + (i+1) + '</b><br>AEP: ' + parqueFmt(sim.aep_mwh, 1) + ' MWh<br>Estela: ' + parqueFmt(sim.wake_loss_pct, 1) + '%<br>CF: ' + parqueFmt(sim.cf_pct, 1) + '%'
        : '<b>T' + (i+1) + '</b>';
      m.setPopupContent(tip);
    });
    // Update KPIs
    document.getElementById('parque-kpi-aep').textContent = parqueFmt(Number(r.aep_mwh) / 1000, 3, '0.000');
    document.getElementById('parque-kpi-wl').textContent  = parqueFmt(r.wake_loss_pct, 2, '0.00');
    document.getElementById('parque-kpi-cf').textContent  = parqueFmt(r.cf_pct, 2, '0.00');
    parqueRenderAEPChart(simTurbines, layoutLatLon);
    parqueRenderTable(layoutLatLon, r.wind_at_turbines || [], simTurbines);
    if (btn) { btn.textContent = '✓ Recalculado'; setTimeout(() => { if (btn) btn.style.display = 'none'; }, 2500); }
    toast('Layout editado recalculado.', 'success');
    setStatus('Simulación con layout editado completada.');
  } catch (e) {
    if (btn) btn.textContent = '↺ Recalcular con posiciones editadas';
    toast('Error: ' + e.message, 'error');
  }
}

// ── Controles de mapa ────────────────────────────────────────────────────────
function parqueSetBaseLayer(key) {
  const map = PARQUE.map;
  if (!map) return;
  if (PARQUE.baseLayer) map.removeLayer(PARQUE.baseLayer);
  const urls = {
    dark:      MAPA_TILES.dark,
    voyager:   MAPA_TILES.voyager,
    osm:       MAPA_TILES.osm,
    satellite: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  };
  const subs = (key === 'osm') ? 'abc' : (key === 'satellite' ? '' : 'abcd');
  PARQUE.baseLayer = L.tileLayer(urls[key] || urls.dark, {
    maxZoom: 18, subdomains: subs, crossOrigin: 'anonymous',
  }).addTo(map);
  if (PARQUE.windOverlay) PARQUE.windOverlay.bringToFront();
  if (PARQUE.polyLayer)   PARQUE.polyLayer.bringToFront();
  PARQUE.turbineMarkers.forEach(m => { try { m.bringToFront(); } catch (_) {} });
}

function parqueToggleWindLayer() {
  const map = PARQUE.map;
  const btn = document.getElementById('btn-parque-wind-toggle');
  if (!map || !PARQUE.windOverlay) return;
  if (PARQUE.windVisible) {
    map.removeLayer(PARQUE.windOverlay);
    if (btn) btn.textContent = 'Mostrar capa';
    PARQUE.windVisible = false;
  } else {
    PARQUE.windOverlay.addTo(map);
    if (PARQUE.polyLayer)   PARQUE.polyLayer.bringToFront();
    PARQUE.turbineMarkers.forEach(m => { try { m.bringToFront(); } catch (_) {} });
    if (btn) btn.textContent = 'Ocultar capa';
    PARQUE.windVisible = true;
  }
}

function parqueToggleMapLock() {
  const map = PARQUE.map;
  const btn = document.getElementById('btn-parque-map-lock');
  if (!map) return;
  PARQUE.mapLocked = !PARQUE.mapLocked;
  if (PARQUE.mapLocked && PARQUE.polyLayer) {
    const bounds = PARQUE.polyLayer.getBounds();
    map.setMaxBounds(bounds.pad(0.5));
    map.fitBounds(bounds, { padding: [30, 30] });
    if (btn) btn.textContent = '🔓 Ver todo';
  } else {
    map.setMaxBounds(null);
    if (btn) btn.textContent = '🔒 Solo área';
  }
}

// ── Wake cone visualization ───────────────────────────────────────────────────
function parqueRenderWakeCones() {
  const map = parqueInitMap();
  if (!map || !PARQUE.optResult?.layout_latlon) return;

  if (PARQUE.wakeGroup) { map.removeLayer(PARQUE.wakeGroup); PARQUE.wakeGroup = null; }

  const windFrom = parseFloat(document.getElementById('parque-wake-dir')?.value ?? 270) || 270;
  const layoutLatlon = PARQUE.optResult.layout_latlon;
  const D   = PARQUE.optResult.D_m || 80;
  const k   = parseFloat(document.getElementById('parque-kwake')?.value)  || 0.075;
  const r0  = D / 2;

  const dirTo    = (windFrom + 180) % 360;
  const dirToRad = dirTo * Math.PI / 180;
  const ux = Math.sin(dirToRad);   // East  (downwind)
  const uy = Math.cos(dirToRad);   // North (downwind)
  const px =  Math.cos(dirToRad);  // East  (right side ⊥ downwind)
  const py = -Math.sin(dirToRad);  // North (right side ⊥ downwind)

  const latRef  = layoutLatlon.reduce((s, p) => s + p[0], 0) / layoutLatlon.length;
  const mPerLat = 111320;
  const mPerLon = 111320 * Math.cos(latRef * Math.PI / 180);

  function ll(lat, lon, dx, dy) { return [lat + dy / mPerLat, lon + dx / mPerLon]; }

  const maxXD = 15, nSlices = 8;
  const dStep = (maxXD * D) / nSlices;
  const fills = [
    'rgba(155,0,15,0.55)', 'rgba(205,28,0,0.46)', 'rgba(235,65,0,0.36)',
    'rgba(255,115,0,0.27)', 'rgba(255,160,28,0.19)', 'rgba(255,205,55,0.13)',
    'rgba(255,240,110,0.08)', 'rgba(255,255,190,0.04)',
  ];

  PARQUE.wakeGroup = L.layerGroup();
  layoutLatlon.forEach(([lat, lon]) => {
    for (let s = 0; s < nSlices; s++) {
      const dN = s * dStep, dF = (s + 1) * dStep;
      const rwN = r0 + k * dN, rwF = r0 + k * dF;
      L.polygon([
        ll(lat, lon, ux*dN - px*rwN, uy*dN - py*rwN),
        ll(lat, lon, ux*dN + px*rwN, uy*dN + py*rwN),
        ll(lat, lon, ux*dF + px*rwF, uy*dF + py*rwF),
        ll(lat, lon, ux*dF - px*rwF, uy*dF - py*rwF),
      ], { color: 'none', weight: 0, fillColor: fills[s], fillOpacity: 1, interactive: false })
      .addTo(PARQUE.wakeGroup);
    }
    // Dashed outline of full cone
    const outline = [];
    const nOut = 14;
    for (let s = 0; s <= nOut; s++) {
      const d = s * (maxXD * D / nOut), rw = r0 + k * d;
      outline.push(ll(lat, lon, ux*d + px*rw, uy*d + py*rw));
    }
    for (let s = nOut; s >= 0; s--) {
      const d = s * (maxXD * D / nOut), rw = r0 + k * d;
      outline.push(ll(lat, lon, ux*d - px*rw, uy*d - py*rw));
    }
    L.polygon(outline, {
      color: 'rgba(255,230,80,0.55)', weight: 0.8, fill: false,
      dashArray: '4,5', interactive: false,
    }).addTo(PARQUE.wakeGroup);
  });

  PARQUE.wakeGroup.addTo(map);
  if (PARQUE.polyLayer) PARQUE.polyLayer.bringToFront();
  PARQUE.turbineMarkers.forEach(m => { try { m.bringToFront(); } catch (_) {} });

  PARQUE.wakeVisible = true;
  const btn = document.getElementById('btn-parque-wake');
  if (btn) { btn.textContent = '🌬 Ocultar estelas'; btn.style.background = 'var(--accent)'; btn.style.color = '#fff'; }
}

function parqueToggleWakes() {
  const btn = document.getElementById('btn-parque-wake');
  if (!PARQUE.optResult?.layout_latlon) {
    toast('Optimiza el parque primero para ver las estelas.', 'warning');
    return;
  }
  if (PARQUE.wakeVisible) {
    if (PARQUE.wakeGroup && PARQUE.map) PARQUE.map.removeLayer(PARQUE.wakeGroup);
    PARQUE.wakeVisible = false;
    if (btn) { btn.textContent = '🌬 Ver estelas'; btn.style.background = ''; btn.style.color = ''; }
  } else {
    parqueRenderWakeCones();
  }
}

// ── Demanda desde sesión (Data Hub) ──────────────────────────────────────────
async function parqueLoadDemandFromSession() {
  if (!WP.sessionId) { toast('Carga un archivo en Data Hub primero.', 'warning'); return; }
  const badge = document.getElementById('parque-demand-badge');
  try {
    const r = await api('/api/farm/session_demand', 'POST', { session_id: WP.sessionId });
    if (r.found) {
      document.getElementById('parque-demand').value = Math.round(r.annual_mwh);
      if (badge) {
        badge.textContent = `📊 Col: "${r.column}"  ·  μ: ${parqueFmt(r.mean_mw, 2)} MW  ·  máx: ${parqueFmt(r.max_mw, 2)} MW  ·  ${r.n_records} reg  ·  Δt=${r.dt_h}h`;
        badge.style.display = '';
      }
      parqueUpdateRecommendation();
      toast(`Demanda cargada: ${Math.round(r.annual_mwh).toLocaleString()} MWh/año  (${r.column})`, 'success');
    } else {
      if (badge) badge.style.display = 'none';
      const cols = (r.columns || []).join(', ');
      console.warn(`No se encontró columna de demanda. Columnas disponibles: ${cols || '—'}`);
    }
  } catch (e) {
    toast(`Error al leer demanda: ${e.message}`, 'error');
  }
}

// ── Turbinas desde TOPSIS ─────────────────────────────────────────────────────
async function parqueLoadTurbines() {
  const sel = document.getElementById('parque-turbine');
  if (!sel || sel.options.length > 0) return;
  try {
    const r = await api('/api/energy/turbines', 'GET');
    sel.innerHTML = '';
    const og1 = document.createElement('optgroup'); og1.label = 'Builtin';
    const og2 = document.createElement('optgroup'); og2.label = 'Catálogo (CSV)';
    (r.turbines || []).forEach(t => {
      const opt = document.createElement('option');
      opt.dataset.hub = t.hub_height_m || '';
      if (t.source === 'builtin') {
        opt.value = t.name;
        opt.textContent = `${t.name} — ${parqueFmt(Number(t.rated_power_kw) / 1000, 1)} MW`;
        og1.appendChild(opt);
      } else {
        opt.value = t.filename;
        opt.textContent = `${t.name}  ⌀${t.rotor_diameter_m||'?'} m  ${t.rated_power_kw ? parqueFmt(Number(t.rated_power_kw) / 1000, 1)+' MW' : ''}`;
        og2.appendChild(opt);
      }
    });
    if (og1.children.length) sel.appendChild(og1);
    if (og2.children.length) sel.appendChild(og2);
    parqueSyncHubHeightFromTurbine(true);
  } catch (e) { /* silent */ }
}

function parqueSyncHubHeightFromTurbine(force = false) {
  const sel = document.getElementById('parque-turbine');
  const inp = document.getElementById('parque-ws-h');
  const hub = Number(sel?.selectedOptions?.[0]?.dataset?.hub);
  if (!inp || !Number.isFinite(hub) || hub <= 0) return;
  if (force || !inp.value || inp.dataset.autoHub === '1') {
    inp.value = parqueFmt(hub, 0, String(Math.round(hub)));
    inp.dataset.autoHub = '1';
  }
}

function parquePopulateTopsisRanking() {
  const listEl  = document.getElementById('parque-topsis-list');
  const badgeEl = document.getElementById('parque-topsis-badge');
  const sel     = document.getElementById('parque-turbine');
  if (!listEl) return;

  const ranking = (typeof MCDA !== 'undefined' && MCDA.lastRanking)
    ? (MCDA.lastRanking.ranking || [])
    : [];

  if (!ranking.length) {
    listEl.innerHTML = '<div style="padding:.6rem;color:var(--text-muted);font-size:.76rem">Ejecuta TOPSIS en la sección de diseño para ver la lista ranqueada.</div>';
    if (badgeEl) badgeEl.textContent = 'Sin TOPSIS';
    return;
  }

  if (badgeEl) badgeEl.textContent = `${ranking.length} opciones`;

  // Unique turbines sorted by score desc
  const seen = new Set();
  const items = [];
  ranking.forEach(r => {
    const key = r.turbina || r.label;
    if (!seen.has(key)) { seen.add(key); items.push(r); }
  });

  listEl.innerHTML = items.map((r, i) => {
    const score = parqueFmt(r.topsis_score, 4, '0.0000');
    const cf    = r.factor_capacidad ? parqueFmt(Number(r.factor_capacidad) * 100, 1) + '% CF' : '';
    const aep   = r.aep_mwh ? parqueFmt(r.aep_mwh, 0) + ' MWh' : '';
    const bg    = i === 0 ? 'var(--accent)' : 'transparent';
    const col   = i === 0 ? '#fff' : 'inherit';
    return `<div class="parque-topsis-item" data-turbina="${r.turbina||''}" data-key="${r.turbina||r.label||''}"
                 style="padding:.4rem .6rem;cursor:pointer;border-bottom:1px solid var(--border);
                        background:${bg};color:${col};font-size:.76rem;display:flex;gap:.4rem;align-items:center"
                 onclick="parqueSelectTurbina(this)">
              <span style="font-weight:700;min-width:1.4rem">#${i+1}</span>
              <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.turbina||''}">${r.turbina||r.label||'—'}</span>
              <span style="color:${i===0?'rgba(255,255,255,.7)':'var(--text-muted)'};font-size:.7rem">${score}</span>
            </div>`;
  }).join('');

  // Pre-select top-1 turbine
  if (items.length) parqueSelectTurbina(null, items[0].turbina || items[0].label);
}

function parqueSelectTurbina(el, forceKey) {
  const key = forceKey || (el && el.dataset.key) || '';
  if (!key) return;
  document.querySelectorAll('.parque-topsis-item').forEach(i => {
    const isThis = i.dataset.key === key;
    i.style.background = isThis ? 'var(--accent)' : 'transparent';
    i.style.color      = isThis ? '#fff' : 'inherit';
  });
  const sel = document.getElementById('parque-turbine');
  if (sel) {
    for (const opt of sel.options) {
      if (opt.value === key || opt.textContent.includes(key)) {
        sel.value = opt.value; break;
      }
    }
  }
  parqueSyncHubHeightFromTurbine(true);
  parqueUpdateRecommendation();
}

// ── Recomendación n° turbinas ─────────────────────────────────────────────────
async function parqueUpdateRecommendation() {
  const turbineKey = document.getElementById('parque-turbine')?.value;
  const demand     = parseFloat(document.getElementById('parque-demand')?.value) || 5000;
  if (!turbineKey) return;

  const hubHeight = parseFloat(document.getElementById('parque-ws-h')?.value) || 110;
  const turbineCfg = { builtin_model: turbineKey, hub_height_m: hubHeight };
  // Mean wind speed: use MCDA/kriging polygon mean if available
  let vMean = 9.0, wK = 2.0;
  if (typeof MCDA !== 'undefined' && Array.isArray(MCDA.lastAnalysis) && MCDA.lastAnalysis.length) {
    const v_vals = MCDA.lastAnalysis.map(s => s.v_mean || 0).filter(v => v > 0);
    if (v_vals.length) vMean = v_vals.reduce((a, b) => a + b, 0) / v_vals.length;
    const k_vals = MCDA.lastAnalysis.map(s => s.weibull_K || 0).filter(k => k > 0);
    if (k_vals.length) wK = k_vals.reduce((a, b) => a + b, 0) / k_vals.length;
  }

  try {
    const r = await api('/api/farm/recommend', 'POST', {
      turbine: turbineCfg, demand_mwh: demand,
      v_mean: vMean, weibull_k: wK,
      hub_height_m: hubHeight,
      measurement_height_m: wsH(),
      availability: (parseFloat(document.getElementById('parque-avail')?.value) || 97) / 100,
      electrical_losses: (parseFloat(document.getElementById('parque-elec')?.value) || 3) / 100,
    });
    document.getElementById('parque-rec-n').textContent   = r.n_turbines;
    document.getElementById('parque-rec-aep').textContent = r.aep_single_net_mwh?.toLocaleString() || '—';
    const nInp = document.getElementById('parque-n-turbines');
    if (nInp && (!nInp.value || parseInt(nInp.value) === 0)) nInp.value = r.n_turbines;
  } catch (e) { /* silent */ }
}

// ── Optimización y simulación ─────────────────────────────────────────────────
async function parqueOptimize() {
  let polygon = null;
  if (typeof MAPA !== 'undefined' && MAPA.parkPoints?.length >= 3) polygon = MAPA.parkPoints;
  else if (typeof KRIG !== 'undefined' && KRIG.parkPoints?.length >= 3) polygon = KRIG.parkPoints;

  if (!polygon || polygon.length < 3) {
    toast('Dibuja y cierra el polígono del parque en la sección de diseño primero.', 'warning');
    return;
  }
  const turbineKey = document.getElementById('parque-turbine')?.value;
  if (!turbineKey) { toast('Selecciona una turbina.', 'warning'); return; }

  // Wind grid from kriging result
  let krigResult = null;
  if (typeof MAPA !== 'undefined' && MAPA.result?.grids) krigResult = MAPA.result.grids;
  else if (typeof KRIG !== 'undefined' && KRIG.result) krigResult = KRIG.result;
  
  const layerKey   = document.getElementById('parque-map-layer')?.value || 'wind';
  const windGrid   = krigResult ? (krigResult[layerKey] || krigResult['wind'] || krigResult['speed'] || null) : null;

  // Mean wind speed for fallback / recommendation
  let vMean = 9.0, wK = 2.0;
  if (typeof MCDA !== 'undefined' && Array.isArray(MCDA.lastAnalysis) && MCDA.lastAnalysis.length) {
    const vv = MCDA.lastAnalysis.map(s => s.v_mean||0).filter(v=>v>0);
    if (vv.length) vMean = vv.reduce((a,b)=>a+b)/vv.length;
    const kk = MCDA.lastAnalysis.map(s => s.weibull_K||0).filter(k=>k>0);
    if (kk.length) wK   = kk.reduce((a,b)=>a+b)/kk.length;
  }

  const nInput = parseInt(document.getElementById('parque-n-turbines')?.value) || 0;
  const hubHeight = parseFloat(document.getElementById('parque-ws-h')?.value) || 110;
  const body = {
    polygon:           polygon.map(([lat, lon]) => ({ lat, lon })),
    turbine:           { builtin_model: turbineKey, hub_height_m: hubHeight },
    n_turbines:        nInput,
    demand_mwh:        parseFloat(document.getElementById('parque-demand')?.value) || 5000,
    wind_grid:         windGrid,
    v_mean:            vMean,
    weibull_k:         wK,
    min_spacing_d:     parseFloat(document.getElementById('parque-min-sep-d')?.value) || 5,
    k_wake:            parseFloat(document.getElementById('parque-kwake')?.value) || 0.075,
    ct:                parseFloat(document.getElementById('parque-ct')?.value) || 0.8,
    availability:      (parseFloat(document.getElementById('parque-avail')?.value) || 97) / 100,
    electrical_losses: (parseFloat(document.getElementById('parque-elec')?.value) || 3) / 100,
    n_sectors:         parseInt(document.getElementById('parque-sectors')?.value) || 36,
    layout_type:       document.getElementById('parque-layout-type')?.value || 'auto',
    hub_height_m:      hubHeight,
    measurement_height_m: wsH(),
    session_id:        WP.sessionId,
  };

  const progWrap = document.getElementById('parque-progress-wrap');
  const progFill = document.getElementById('parque-progress-fill');
  const progText = document.getElementById('parque-progress-text');
  const statusEl = document.getElementById('parque-status');

  progWrap.style.display = ''; progFill.style.width = '20%';
  progText.textContent   = 'Optimizando layout (GSP)…';
  statusEl.textContent   = 'Optimizando…';
  if (QD.active) { qdSetStep(4, 'Optimizando parque…'); qdMsg('Layout estructurado + estelas Jensen…'); }

  try {
    progFill.style.width = '50%';
    const r = await api('/api/farm/optimize', 'POST', body);
    if (r.error) throw new Error(r.error);
    progFill.style.width = '90%';

    PARQUE.optResult = r;
    PARQUE.result    = r.simulation || null;

    // Actualizar mapa
    parqueRenderKrigingLayer(layerKey);
    parqueRenderPolygon();
    parquePlotTurbinesOnMap(r.layout_latlon, r.simulation?.turbines || null);

    // KPIs
    const sim = r.simulation;
    statusEl.textContent = sim
      ? `${r.n_placed} turbinas · ${r.installed_mw} MW · ${sim.hours?.toLocaleString()||'—'} h`
      : `${r.n_placed} turbinas colocadas (sin serie temporal)`;

    // Layout config badge (tresbolillo / alineado / spacings / direction)
    const lcfg = r.layout_config;
    const lBadge = document.getElementById('parque-layout-badge');
    if (lBadge && lcfg) {
      const typeLabel = lcfg.layout_type === 'tresbolillo' ? '⬡ Tresbolillo'
                      : lcfg.layout_type === 'alineado'    ? '▦ Alineado'
                      : '⚙ GSP';
      const spacings = (lcfg.s_para_d != null && lcfg.s_perp_d != null)
        ? ` · ${lcfg.s_para_d}D×${lcfg.s_perp_d}D` : '';
      const dirLabel = lcfg.dominant_dir_deg != null
        ? ` · Dir ${Math.round(lcfg.dominant_dir_deg)}°` : '';
      const nTried = lcfg.n_configs_tried > 0 ? ` (${lcfg.n_configs_tried} configs)` : '';
      lBadge.textContent = typeLabel + spacings + dirLabel + nTried;
      lBadge.style.display = '';
    } else if (lBadge) { lBadge.style.display = 'none'; }

    document.getElementById('parque-layout-meta').textContent =
      `Área: ${r.area_km2} km² · D: ${r.D_m}m · Sep. mín: ${r.min_sep_m}m · ${r.n_candidates} candidatos`;

    document.getElementById('parque-info').textContent =
      `${r.turbine?.name||turbineKey} · ⌀${r.D_m}m`;

    if (sim) {
      if (sim.error === 'wind_series_invalid') {
        toast('⚠ Serie de viento con todos-NaN — se usó síntesis Weibull para AEP. Recarga datos en Data Hub.', 'warn', 7000);
      }
      // Auto-update wake direction from energy-weighted dominant direction
      const wdirEl = document.getElementById('parque-wake-dir');
      const domDir = r.layout_config?.dominant_dir_deg ?? sim.dominant_dir_deg;
      if (wdirEl && domDir != null) wdirEl.value = Math.round(domDir);

      document.getElementById('parque-kpis').style.display = '';
      document.getElementById('parque-kpi-aep').textContent = parqueFmt(Number(sim.aep_mwh) / 1000, 3, '0.000');
      document.getElementById('parque-kpi-wl').textContent  = parqueFmt(sim.wake_loss_pct, 2, '0.00');
      document.getElementById('parque-kpi-cf').textContent  = parqueFmt(sim.cf_pct, 2, '0.00');
      document.getElementById('parque-kpi-mw').textContent  = parqueFmt(sim.installed_mw ?? r.installed_mw, 3, '0.000');
      document.getElementById('parque-kpi-flh').textContent = parqueFmt(sim.flh, 0, '0');
      document.getElementById('parque-kpi-dir').textContent = parqueFmt(sim.dominant_dir_deg, 0, '0') + '°';
      parqueRenderAEPChart(sim.turbines, r.layout_latlon);
      parqueRenderTable(r.layout_latlon, r.wind_at_turbines, sim.turbines);
    } else {
      document.getElementById('parque-kpis').style.display = 'none';
      parqueRenderTable(r.layout_latlon, r.wind_at_turbines, null);
    }
    parqueRenderWakeChart(r.D_m);
    progFill.style.width = '100%';
    setTimeout(() => { progWrap.style.display = 'none'; }, 800);
    toast(`Parque optimizado: ${r.n_placed} turbinas`, 'success');

    // Quick Design pipeline hook: continue to TE + report
    if (WP._qdPipelineReady && QD.active) {
      qdSetStep(4, 'Parque optimizado ✓');
      setTimeout(() => quickDesignContinueAfterParque(r), 800);
    }
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    statusEl.textContent = 'Error en optimización';
    progWrap.style.display = 'none';
    progText.textContent = '';
  }
}

// ── Gráficas y tabla ──────────────────────────────────────────────────────────
function parqueRenderAEPChart(turbines, layoutLatlon) {
  const rows = Array.isArray(turbines) ? turbines : [];
  if (!rows.length) { destroyChart('parque-aep'); return; }
  destroyChart('parque-aep');
  const ctx    = document.getElementById('parque-aep-chart')?.getContext('2d');
  if (!ctx) return;
  const labels = rows.map((_, i) => `T${i+1}`);
  const aep    = rows.map(t => Number(t.aep_mwh) || 0);
  const noWake = rows.map(t => Number(t.aep_no_wake_mwh) || 0);
  WP.charts['parque-aep'] = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [
      { label: 'AEP con estela', data: aep,    backgroundColor: acc() },
      { label: 'AEP sin estela', data: noWake, backgroundColor: 'rgba(150,150,150,0.35)' },
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

function parqueRenderTable(layoutLatlon, windAtTurbines, simTurbines) {
  const tb = document.getElementById('parque-tbl-body');
  if (!tb || !layoutLatlon) return;
  const simRows = Array.isArray(simTurbines) ? simTurbines : [];
  const simByIndex = new Map(simRows.map((t, idx) => [Number.isFinite(Number(t?.i)) ? Number(t.i) : idx, t]));
  const fmt = (value, digits) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : '—';
  tb.innerHTML = layoutLatlon.map(([lat, lon], i) => {
    const v   = windAtTurbines ? fmt(windAtTurbines[i], 2) : '—';
    const sim = simByIndex.get(i) || null;
    const ox  = PARQUE.optResult?.origin_lat ? '' : '';
    const xy  = PARQUE.optResult?.layout_xy_m ? PARQUE.optResult.layout_xy_m[i] : null;
    return `<tr>
      <td>T${i+1}</td>
      <td>${fmt(lat, 5)}</td><td>${fmt(lon, 5)}</td>
      <td>${xy ? fmt(xy[0], 0) : '—'}</td><td>${xy ? fmt(xy[1], 0) : '—'}</td>
      <td>${v}</td>
      <td>${sim ? fmt(sim.aep_mwh, 1) : '—'}</td>
      <td>${sim ? fmt(sim.aep_no_wake_mwh, 1) : '—'}</td>
      <td>${sim ? fmt(sim.wake_loss_pct, 2) : '—'}</td>
      <td>${sim ? fmt(sim.cf_pct, 2) : '—'}</td>
    </tr>`;
  }).join('');
}

async function parqueRenderWakeChart(rotorD) {
  const rotor = rotorD || (PARQUE.optResult && PARQUE.optResult.D_m) || 80;
  const k  = parseFloat(document.getElementById('parque-kwake')?.value) || 0.075;
  const ct = parseFloat(document.getElementById('parque-ct')?.value)    || 0.8;
  try {
    const r = await api('/api/farm/wake_profile', 'POST', { rotor_d_m: rotor, k_wake: k, ct });
    destroyChart('parque-wake');
    const ctx = document.getElementById('parque-wake-chart')?.getContext('2d');
    if (!ctx) return;
    WP.charts['parque-wake'] = new Chart(ctx, {
      type: 'line',
      data: { datasets: [{
        label: `NOJ k=${k}, Ct=${ct}, ⌀=${rotor} m`,
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

// ── Reset ─────────────────────────────────────────────────────────────────────
function parqueReset() {
  PARQUE.result = null; PARQUE.optResult = null;
  parqueClearTurbineMarkers();
  if (PARQUE.wakeGroup  && PARQUE.map) { PARQUE.map.removeLayer(PARQUE.wakeGroup);   PARQUE.wakeGroup   = null; }
  if (PARQUE.windOverlay && PARQUE.map) { PARQUE.map.removeLayer(PARQUE.windOverlay); PARQUE.windOverlay = null; }
  if (PARQUE.polyLayer   && PARQUE.map) { PARQUE.map.removeLayer(PARQUE.polyLayer);   PARQUE.polyLayer   = null; }
  if (PARQUE.map) PARQUE.map.setMaxBounds(null);
  PARQUE.mapLocked = false; PARQUE.windVisible = true; PARQUE.wakeVisible = false;
  const wakeBtn = document.getElementById('btn-parque-wake');
  if (wakeBtn) { wakeBtn.textContent = '🌬 Ver estelas'; wakeBtn.style.background = ''; wakeBtn.style.color = ''; }
  const lockBtn = document.getElementById('btn-parque-map-lock');
  if (lockBtn) lockBtn.textContent = '🔓 Ver todo';
  const windBtn = document.getElementById('btn-parque-wind-toggle');
  if (windBtn) windBtn.textContent = 'Ocultar capa';
  document.getElementById('parque-kpis').style.display = 'none';
  document.getElementById('parque-tbl-body').innerHTML = '';
  document.getElementById('parque-status').textContent = 'Sin simular';
  document.getElementById('parque-layout-meta').textContent = '';
  document.getElementById('parque-map-hint').style.display = '';
  document.getElementById('parque-map-legend').style.display = 'none';
  document.getElementById('parque-map-status').textContent = 'Sin Kriging';
  destroyChart('parque-aep'); destroyChart('parque-wake');
}

// ── Activación del módulo ─────────────────────────────────────────────────────
function parqueActivate() {
  parqueInitMap();
  parqueLoadTurbines();
  parquePopulateTopsisRanking();
  parqueLoadDemandFromSession();
  const layerKey = document.getElementById('parque-map-layer')?.value || 'wind';
  parqueRenderKrigingLayer(layerKey);
  parqueRenderPolygon();
  if (PARQUE.optResult?.layout_latlon) {
    parquePlotTurbinesOnMap(PARQUE.optResult.layout_latlon, PARQUE.result?.turbines || null);
    if (PARQUE.wakeVisible) parqueRenderWakeCones();
  }
  setTimeout(() => PARQUE.map && PARQUE.map.invalidateSize(), 120);
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-parque-optimize')?.addEventListener('click', parqueOptimize);
  document.getElementById('btn-parque-reset')?.addEventListener('click', parqueReset);
  document.getElementById('parque-map-layer')?.addEventListener('change', e => {
    PARQUE.layerKey = e.target.value;
    parqueRenderKrigingLayer(e.target.value);
  });
  document.getElementById('parque-demand')?.addEventListener('input', parqueUpdateRecommendation);
  document.getElementById('parque-turbine')?.addEventListener('change', () => {
    parqueSyncHubHeightFromTurbine(true);
    parqueUpdateRecommendation();
  });
  document.getElementById('parque-ws-h')?.addEventListener('input', e => {
    e.target.dataset.autoHub = '0';
    parqueUpdateRecommendation();
  });
  document.getElementById('parque-wake-dir')?.addEventListener('change', () => {
    if (PARQUE.wakeVisible) parqueRenderWakeCones();
  });
  document.querySelectorAll('.nav-btn[data-module="parque"]').forEach(b => {
    b.addEventListener('click', () => requestAnimationFrame(parqueActivate));
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
    maxZoom: 18, subdomains: (key === 'osm' || key === 'topo') ? 'abc' : 'abcd',
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
    toast('Perfil horario ajustado. Revisa el Centro de modelaje.', 'success');
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
    toast('Perfil horario de PML ajustado. Revisa el Centro de modelaje.', 'success');
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

// Pobla los selectores de preset (dem-preset, pml-preset, interest-preset,
// fx-preset) leyendo /api/market/list_presets. Cualquier archivo nuevo
// depositado en las carpetas mercado_demanda/, mercado_pml/, mercado_fx/,
// mercado_tasas/ aparece automáticamente.
const MARKET_PRESET_TARGETS = {
  demand:   { selectId: 'dem-preset',      defaultKey: 'juchitan' },
  pml:      { selectId: 'pml-preset',      defaultKey: 'pml_ipo'  },
  interest: { selectId: 'interest-preset', defaultKey: 'tiie_28d' },
  fx:       { selectId: 'fx-preset',       defaultKey: 'usdmxn'   },
};

async function loadMarketPresets() {
  try {
    const r = await api('/api/market/list_presets', 'GET');
    Object.entries(MARKET_PRESET_TARGETS).forEach(([cat, cfg]) => {
      const sel = document.getElementById(cfg.selectId);
      if (!sel) return;
      const items = r[cat] || [];
      if (!items.length) {
        sel.innerHTML = '<option value="">(sin archivos en carpeta)</option>';
        return;
      }
      sel.innerHTML = '';
      items.forEach(it => {
        const opt = document.createElement('option');
        opt.value = it.key;
        const unit = it.unit ? ` (${it.unit})` : '';
        opt.textContent = `${it.label}${unit}`;
        sel.appendChild(opt);
      });
      // Restaurar selección preferida si existe
      const has = (k) => Array.from(sel.options).some(o => o.value === k);
      if (has(cfg.defaultKey)) sel.value = cfg.defaultKey;
    });
  } catch (e) {
    console.warn('loadMarketPresets failed:', e);
    Object.values(MARKET_PRESET_TARGETS).forEach(cfg => {
      const sel = document.getElementById(cfg.selectId);
      if (sel) sel.innerHTML = '<option value="">(error al listar presets)</option>';
    });
  }
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
    const ctx = document.getElementById(`market-${category}-preview-chart`);
    if (ctx) {
      if (WP.charts[`market-${category}-preview`]) WP.charts[`market-${category}-preview`].destroy();
      const step = Math.max(1, Math.floor(r.chart.values.length / 1500));
      const vals = r.chart.values.filter((_, i) => i % step === 0);
      const labels = vals.map((_, i) => i * step);
      WP.charts[`market-${category}-preview`] = new Chart(ctx, {
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
  const statsGrid = document.getElementById(`market-${category}-stats-grid`);
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

  // Store in WP for later use by Centro de modelaje / Forecast Studio
  WP.arenaCache[`market_${category}`] = r;
}

// ════════════════════════════════════════════════════════════════════════════
//  GLOBE STAGE — pantalla inicial: globo terráqueo + búsqueda + estaciones
// ════════════════════════════════════════════════════════════════════════════
const GLOBE = {
  worldLand: null,
  graticule: null,
  loaderProj: null,  loaderPath: null,
  mainProj:   null,  mainPath:   null,
  W: 0, H: 0,
  rotate: [99, -23, 0],   // arranca centrado en México (lon ~ -99)
  isDragging: false, dragStart: null, rotateStart: null,
  autoSpin: true,
  loaderAngle: 0,
  loaderRAF: null, mainRAF: null,
  loaderReady: false, mainReady: false,
  poi: null,
  poiHaloPhase: 0,
  sites: [],
  visibleSites: [],
  master: null,
  collapsed: false,
  collapsedOnce: false,
  zoomScale: 1,           // 1 = base, hasta 4
  appOn: false,
  flyAF: null,
  // Modo 3D ↔ 2D Leaflet
  mode: 'globe',          // 'globe' | 'leaflet'
  leafletMap: null,
  leafletTile: null,
  leafletPoi: null,
  leafletCircle: null,
  leafletStations: [],
};

const GLOBE_TILE_URLS = {
  dark:    { url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
             attr: '© OpenStreetMap · © CARTO', subdomains: 'abcd' },
  voyager: { url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
             attr: '© OpenStreetMap · © CARTO', subdomains: 'abcd' },
  osm:     { url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
             attr: '© OpenStreetMap', subdomains: 'abc' },
  topo:    { url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
             attr: '© OpenTopoMap (CC-BY-SA) · © OSM', subdomains: 'abc' },
  sat:     { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
             attr: 'Tiles © Esri', subdomains: '' },
};

const GLOBE_RADIUS_DEFAULT_KM = 200;

async function bindGlobeStage() {
  if (typeof d3 === 'undefined' || typeof topojson === 'undefined') {
    console.warn('[globe] d3 / topojson no disponibles, salteando globo.');
    document.getElementById('globe-stage')?.classList.add('is-collapsed');
    return;
  }
  initGlobeLoader();
  initGlobeMain();
  fetchWorldAtlas();
  bindGlobeSearchUI();
  bindGlobeRadiusUI();
  bindGlobeUploadModalUI();
  bindGlobeBackButton();
  bindGlobeModeSwitch();

  // Auto-advance loader → app after 2.4s
  setTimeout(() => switchToGlobeApp(), 2400);
  // Click loader to advance early
  document.getElementById('globe-loader')?.addEventListener('click', () => switchToGlobeApp());

  // Pre-cargar catálogo de sitios para que el globo los pueda mostrar
  globePreloadSites();
}

function fetchWorldAtlas() {
  fetch('https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json')
    .then(r => r.json())
    .then(world => {
      GLOBE.worldLand = topojson.feature(world, world.objects.land);
      GLOBE.graticule = d3.geoGraticule()();
      GLOBE.loaderReady = true;
      GLOBE.mainReady = true;
    })
    .catch(e => console.warn('[globe] no se pudo cargar world-atlas:', e));

  // Estados de México (estética) — fetch en background, no bloquea el render
  fetch('https://cdn.jsdelivr.net/gh/strotgen/mexico-leaflet@master/states.geojson')
    .then(r => r.json())
    .then(geo => { GLOBE.mxStates = geo; })
    .catch(e => console.warn('[globe] estados MX no disponibles:', e));
}

async function globePreloadSites() {
  const yearSel = document.getElementById('krig3-year');
  const year = yearSel ? (parseInt(yearSel.value) || 2018) : 2018;

  // 1) Versión LIGHT: solo nombre + coords. Responde casi instantáneo y permite
  //    pintar los pines del globo de inmediato.
  try {
    const rl = await api('/api/kriging/sites/light', 'GET');
    GLOBE.sites = rl.sites || [];
    GLOBE.sitesLight = true;
    updateGlobeStationsBadge();
    console.log(`[globe] ${GLOBE.sites.length} estaciones (light) listas`);
  } catch (e) {
    console.error('[globe] error light:', e);
    updateGlobeStationsBadge(true);
    if (typeof toast === 'function') toast('No se pudieron cargar las estaciones meteorológicas en el globo.', 'warn');
    return;
  }

  // 2) Versión completa con Weibull/v_mean en background — enriquece los pines
  //    sin bloquear el render inicial.
  try {
    const htParamG = WP.referenceHeight ? `&target_height_m=${WP.referenceHeight}` : '';
    const r = await api(`/api/kriging/sites?year=${year}${htParamG}`, 'GET');
    if (r.sites && r.sites.length) {
      const byName = new Map(r.sites.map(s => [s.filename, s]));
      GLOBE.sites = GLOBE.sites.map(s => byName.get(s.filename) || s);
      GLOBE.sitesLight = false;
      updateGlobeStationsBadge();
      console.log(`[globe] enriquecimiento completo (año ${year})`);
    }
  } catch (e) {
    console.warn('[globe] enriquecimiento falló (pines siguen visibles):', e);
  }
}

function updateGlobeStationsBadge(error = false) {
  const el = document.getElementById('globe-stations-badge');
  if (!el) return;
  if (error) {
    el.textContent = '⚠ Estaciones no cargadas';
    el.style.color = '#d97706';
    el.style.display = '';
    return;
  }
  const n = GLOBE.sites?.length || 0;
  if (n === 0) {
    el.textContent = 'Cargando estaciones…';
    el.style.color = 'var(--text-muted)';
  } else {
    el.textContent = `${n} estaciones meteorológicas disponibles`;
    el.style.color = 'var(--text)';
  }
  el.style.display = '';
}

// ──────────────────────────── LOADER (200×200) ───────────────────────────────
function initGlobeLoader() {
  const cv = document.getElementById('globe-loader-canvas');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  GLOBE.loaderProj = d3.geoOrthographic()
    .scale(96)
    .translate([110, 110])
    .clipAngle(90);
  GLOBE.loaderPath = d3.geoPath(GLOBE.loaderProj, ctx);

  function draw() {
    ctx.clearRect(0, 0, 220, 220);

    // Halo exterior leve (brillo costeño)
    const halo = ctx.createRadialGradient(110, 110, 80, 110, 110, 120);
    halo.addColorStop(0, 'rgba(77,163,255,0.35)');
    halo.addColorStop(1, 'rgba(77,163,255,0)');
    ctx.fillStyle = halo;
    ctx.beginPath(); ctx.arc(110, 110, 120, 0, 2*Math.PI); ctx.fill();

    // Océano
    ctx.beginPath();
    GLOBE.loaderPath({ type: 'Sphere' });
    ctx.fillStyle = '#0F1A2E';
    ctx.fill();

    if (GLOBE.loaderReady) {
      // Graticule
      ctx.beginPath();
      GLOBE.loaderPath(GLOBE.graticule);
      ctx.strokeStyle = 'rgba(77,163,255,0.18)';
      ctx.lineWidth = 0.4; ctx.stroke();

      // Land
      ctx.beginPath();
      GLOBE.loaderPath(GLOBE.worldLand);
      ctx.fillStyle = '#1B2A40';
      ctx.fill();
      ctx.strokeStyle = 'rgba(77,163,255,0.55)';
      ctx.lineWidth = 0.5; ctx.stroke();
    }

    // Outline
    ctx.beginPath();
    GLOBE.loaderPath({ type: 'Sphere' });
    ctx.strokeStyle = 'rgba(77,163,255,0.7)';
    ctx.lineWidth = 1; ctx.stroke();
  }

  function tick() {
    GLOBE.loaderAngle += 0.28;
    GLOBE.loaderProj.rotate([GLOBE.loaderAngle, -20, 0]);
    draw();
    GLOBE.loaderRAF = requestAnimationFrame(tick);
  }
  tick();
}

// ──────────────────────────── MAIN GLOBE (full) ──────────────────────────────
function initGlobeMain() {
  const cv = document.getElementById('globe-canvas');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  GLOBE.mainProj = d3.geoOrthographic()
    .clipAngle(90)
    .precision(0.4);
  GLOBE.mainPath = d3.geoPath(GLOBE.mainProj, ctx);

  function resize() {
    GLOBE.W = window.innerWidth;
    GLOBE.H = window.innerHeight;
    cv.width = GLOBE.W;
    cv.height = GLOBE.H;
    const r = Math.min(GLOBE.W, GLOBE.H) * 0.42 * GLOBE.zoomScale;
    GLOBE.mainProj.scale(r).translate([GLOBE.W/2, GLOBE.H/2]);
  }
  resize();
  window.addEventListener('resize', resize);

  // Drag a rotar
  cv.addEventListener('mousedown', e => {
    GLOBE.isDragging = true;
    GLOBE.dragStart = [e.clientX, e.clientY];
    GLOBE.rotateStart = [...GLOBE.rotate];
    GLOBE.autoSpin = false;
  });
  window.addEventListener('mousemove', e => {
    if (!GLOBE.isDragging) return;
    const dx = e.clientX - GLOBE.dragStart[0];
    const dy = e.clientY - GLOBE.dragStart[1];
    const k = 0.32 / Math.max(0.7, GLOBE.zoomScale);
    GLOBE.rotate[0] = GLOBE.rotateStart[0] + dx * k;
    GLOBE.rotate[1] = Math.max(-80, Math.min(80, GLOBE.rotateStart[1] - dy * k));
  });
  window.addEventListener('mouseup', () => { GLOBE.isDragging = false; });

  // Touch
  cv.addEventListener('touchstart', e => {
    if (!e.touches[0]) return;
    GLOBE.isDragging = true;
    GLOBE.dragStart = [e.touches[0].clientX, e.touches[0].clientY];
    GLOBE.rotateStart = [...GLOBE.rotate];
    GLOBE.autoSpin = false;
  }, { passive: true });
  window.addEventListener('touchmove', e => {
    if (!GLOBE.isDragging || !e.touches[0]) return;
    const dx = e.touches[0].clientX - GLOBE.dragStart[0];
    const dy = e.touches[0].clientY - GLOBE.dragStart[1];
    GLOBE.rotate[0] = GLOBE.rotateStart[0] + dx * 0.3;
    GLOBE.rotate[1] = Math.max(-80, Math.min(80, GLOBE.rotateStart[1] - dy * 0.3));
  }, { passive: true });
  window.addEventListener('touchend', () => { GLOBE.isDragging = false; });

  // Wheel: zoom de escala (permite acercarse mucho más para ver detalle)
  cv.addEventListener('wheel', e => {
    if (!GLOBE.appOn) return;
    e.preventDefault();
    const k = e.deltaY > 0 ? 0.90 : 1.12;
    GLOBE.zoomScale = Math.max(0.7, Math.min(40, GLOBE.zoomScale * k));
    const r = Math.min(GLOBE.W, GLOBE.H) * 0.42 * GLOBE.zoomScale;
    GLOBE.mainProj.scale(r);
  }, { passive: false });

  // Click: marcar POI o seleccionar estación
  cv.addEventListener('click', (e) => {
    if (!GLOBE.appOn) return;
    if (Math.abs(e.clientX - (GLOBE.dragStart?.[0] ?? e.clientX)) > 4) return; // ignore drag-end
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;

    // ¿Click sobre alguna estación visible? Hitbox grande (incluye torre + aspas)
    if (GLOBE.visibleSites.length) {
      for (const s of GLOBE.visibleSites) {
        if (!isPointVisible(s.lat, s.lon)) continue;
        const c = GLOBE.mainProj([s.lon, s.lat]);
        if (!c) continue;
        const isMaster = (GLOBE.master === s.filename);
        const hitR = isMaster ? 24 : 18;
        // hitbox vertical: rectángulo desde la base hacia arriba
        const dx = mx - c[0];
        const dy = my - (c[1] - 9);   // centroide del icono está por encima de la base
        if (Math.abs(dx) <= hitR && dy <= hitR && dy >= -16) {
          handleGlobeStationPick(s.filename);
          return;
        }
      }
    }

    // Click sobre globo → POI + auto-switch a Mapa 2D a 50 km
    const inv = GLOBE.mainProj.invert([mx, my]);
    if (!inv) return;
    const [lon, lat] = inv;
    if (!isFinite(lat) || !isFinite(lon)) return;
    setGlobePoi(lat, lon);
    switchTo2DAt(lat, lon, 50);
  });

  function tick() {
    GLOBE.mainRAF = requestAnimationFrame(tick);
    if (GLOBE.collapsed) return;     // no dibujar mientras está oculto
    if (GLOBE.autoSpin && !GLOBE.isDragging) {
      GLOBE.rotate[0] += 0.05;
    }
    GLOBE.poiHaloPhase = (GLOBE.poiHaloPhase + 0.04) % (Math.PI * 2);
    GLOBE.mainProj.rotate(GLOBE.rotate);
    drawGlobeMain();
  }
  tick();
}

function isPointVisible(lat, lon) {
  const r = GLOBE.mainProj.rotate();
  const center = [-r[0], -r[1]];
  return d3.geoDistance([lon, lat], center) <= Math.PI / 2 - 0.005;
}

function drawGlobeMain() {
  const cv = document.getElementById('globe-canvas');
  if (!cv || !GLOBE.mainProj) return;
  const ctx = cv.getContext('2d');
  const isLight = document.body.classList.contains('light');
  ctx.clearRect(0, 0, GLOBE.W, GLOBE.H);

  // Glow detrás del globo
  const cx = GLOBE.W / 2, cy = GLOBE.H / 2;
  const rGlobe = Math.min(GLOBE.W, GLOBE.H) * 0.42 * GLOBE.zoomScale;
  const halo = ctx.createRadialGradient(cx, cy, rGlobe * 0.85, cx, cy, rGlobe * 1.25);
  halo.addColorStop(0, isLight ? 'rgba(15,95,168,0.18)' : 'rgba(77,163,255,0.30)');
  halo.addColorStop(1, 'rgba(77,163,255,0)');
  ctx.fillStyle = halo;
  ctx.beginPath(); ctx.arc(cx, cy, rGlobe * 1.25, 0, 2*Math.PI); ctx.fill();

  // Océano
  ctx.beginPath();
  GLOBE.mainPath({ type: 'Sphere' });
  const oceanGrad = ctx.createRadialGradient(cx - rGlobe*0.3, cy - rGlobe*0.3, rGlobe*0.2, cx, cy, rGlobe);
  if (isLight) {
    oceanGrad.addColorStop(0, '#E2EAF4');
    oceanGrad.addColorStop(1, '#C9D6E8');
  } else {
    oceanGrad.addColorStop(0, '#152133');
    oceanGrad.addColorStop(1, '#0B1422');
  }
  ctx.fillStyle = oceanGrad;
  ctx.fill();

  if (!GLOBE.mainReady) return;

  // Graticule
  ctx.beginPath();
  GLOBE.mainPath(GLOBE.graticule);
  ctx.strokeStyle = isLight ? 'rgba(15,95,168,0.10)' : 'rgba(77,163,255,0.08)';
  ctx.lineWidth = 0.5;
  ctx.stroke();

  // Land
  ctx.beginPath();
  GLOBE.mainPath(GLOBE.worldLand);
  ctx.fillStyle = isLight ? '#E2E8F0' : '#1B2A40';
  ctx.fill();
  ctx.strokeStyle = isLight ? 'rgba(15,95,168,0.45)' : 'rgba(77,163,255,0.40)';
  ctx.lineWidth = 0.6;
  ctx.stroke();

  // Fronteras de estados de México (solo si MX está en el hemisferio visible)
  if (GLOBE.mxStates && isPointVisible(23.6, -102.5)) {
    ctx.beginPath();
    GLOBE.mainPath(GLOBE.mxStates);
    ctx.strokeStyle = isLight ? 'rgba(15,95,168,0.55)' : 'rgba(159,122,234,0.45)';
    ctx.lineWidth = 0.55;
    ctx.stroke();
  }

  // Outline
  ctx.beginPath();
  GLOBE.mainPath({ type: 'Sphere' });
  ctx.strokeStyle = isLight ? 'rgba(15,95,168,0.7)' : 'rgba(77,163,255,0.7)';
  ctx.lineWidth = 1.2;
  ctx.stroke();

  // Catálogo: estaciones del catálogo como pequeños pines elevados
  if (GLOBE.sites.length) {
    GLOBE.sites.forEach(s => {
      if (!isPointVisible(s.lat, s.lon)) return;
      const c = GLOBE.mainProj([s.lon, s.lat]);
      if (!c) return;
      drawCatalogPin(ctx, c[0], c[1], isLight);
    });
  }

  // POI (sitio elegido)
  if (GLOBE.poi) {
    const visible = isPointVisible(GLOBE.poi.lat, GLOBE.poi.lon);
    if (visible) {
      const c = GLOBE.mainProj([GLOBE.poi.lon, GLOBE.poi.lat]);
      if (c) {
        // Halo pulsante
        const pulse = 1 + 0.4 * Math.sin(GLOBE.poiHaloPhase);
        ctx.beginPath();
        ctx.arc(c[0], c[1], 14 * pulse, 0, 2*Math.PI);
        ctx.fillStyle = 'rgba(180,83,9,0.18)';
        ctx.fill();
        // Dot
        ctx.beginPath();
        ctx.arc(c[0], c[1], 6, 0, 2*Math.PI);
        ctx.fillStyle = '#E5A86B';
        ctx.fill();
        ctx.strokeStyle = '#FFFFFF';
        ctx.lineWidth = 1.4;
        ctx.stroke();
        // Inner
        ctx.beginPath();
        ctx.arc(c[0], c[1], 2.2, 0, 2*Math.PI);
        ctx.fillStyle = '#0A0E15';
        ctx.fill();

        // Círculo de radio (km → en proyección)
        const radiusKm = Math.max(1, parseFloat(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM);
        drawRadiusCircle(ctx, GLOBE.poi.lat, GLOBE.poi.lon, radiusKm);
      }
    }

    // Estaciones dentro del radio: turbina 3D con sombra y relieve
    GLOBE.visibleSites.forEach(s => {
      if (!isPointVisible(s.lat, s.lon)) return;
      const c = GLOBE.mainProj([s.lon, s.lat]);
      if (!c) return;
      const isMaster = (GLOBE.master === s.filename);
      drawTurbineIcon(ctx, c[0], c[1], isMaster, isLight, GLOBE.poiHaloPhase);
    });
  }

  // Coord readout
  const center = GLOBE.mainProj.invert([cx, cy]);
  if (center) {
    const lat = center[1], lon = center[0];
    const latStr = Math.abs(lat).toFixed(2) + (lat >= 0 ? '°N' : '°S');
    const lonStr = Math.abs(lon).toFixed(2) + (lon >= 0 ? '°E' : '°W');
    const out = document.getElementById('globe-readout');
    if (out) out.textContent = `${latStr} · ${lonStr}`;
  }
}

// ──────────────────────────── Iconos de estaciones ─────────────────────────
// Pin 3D pequeño para estaciones del catálogo (no en radio)
function drawCatalogPin(ctx, x, y, isLight) {
  ctx.save();
  // Halo exterior — ayuda a localizar la estación a vista de globo
  ctx.beginPath();
  ctx.arc(x, y, 9, 0, 2 * Math.PI);
  ctx.fillStyle = isLight ? 'rgba(15,95,168,0.18)' : 'rgba(77,163,255,0.22)';
  ctx.fill();
  // Sombra debajo
  ctx.beginPath();
  ctx.ellipse(x, y + 6, 5, 1.4, 0, 0, 2 * Math.PI);
  ctx.fillStyle = 'rgba(0,0,0,0.35)';
  ctx.fill();
  // Cabeza con gradiente para look 3D
  const grad = ctx.createRadialGradient(x - 1.6, y - 2, 0.8, x, y, 6);
  if (isLight) {
    grad.addColorStop(0, '#FFFFFF');
    grad.addColorStop(1, '#0F5FA8');
  } else {
    grad.addColorStop(0, '#CFE7FF');
    grad.addColorStop(1, '#1F4A7A');
  }
  ctx.beginPath();
  ctx.arc(x, y, 5.5, 0, 2 * Math.PI);
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.strokeStyle = isLight ? 'rgba(15,95,168,0.85)' : 'rgba(255,255,255,0.75)';
  ctx.lineWidth = 1.0;
  ctx.stroke();
  // Brillo
  ctx.beginPath();
  ctx.arc(x - 1.6, y - 1.8, 1.6, 0, 2 * Math.PI);
  ctx.fillStyle = 'rgba(255,255,255,0.65)';
  ctx.fill();
  ctx.restore();
}

// Turbina eólica 3D estilizada — base, mástil, nacela y tres aspas
function drawTurbineIcon(ctx, x, y, isMaster, isLight, phase) {
  const scale = isMaster ? 1.45 : 1.0;
  const mastH = 18 * scale;
  const mastW = 2.4 * scale;
  const hubR  = 3.2 * scale;
  const bladeL = 11 * scale;

  ctx.save();
  // Sombra elíptica debajo
  ctx.beginPath();
  ctx.ellipse(x, y + 1.5, 9 * scale, 2.2 * scale, 0, 0, 2*Math.PI);
  ctx.fillStyle = 'rgba(0,0,0,0.42)';
  ctx.fill();

  // Halo si es maestra
  if (isMaster) {
    const pulse = 1 + 0.18 * Math.sin(phase);
    const halo = ctx.createRadialGradient(x, y - mastH/2, 4, x, y - mastH/2, 22 * pulse);
    halo.addColorStop(0, 'rgba(255,200,144,0.55)');
    halo.addColorStop(1, 'rgba(255,200,144,0)');
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(x, y - mastH/2, 22 * pulse, 0, 2*Math.PI);
    ctx.fill();
  }

  // Base (anillo de cimentación)
  ctx.beginPath();
  ctx.ellipse(x, y, 4.2 * scale, 1.4 * scale, 0, 0, 2*Math.PI);
  ctx.fillStyle = isLight ? '#3D4A5C' : '#0B121F';
  ctx.fill();

  // Mástil: gradient de izq oscuro a der claro para "cilindro 3D"
  const mastTop = y - mastH;
  const mastGrad = ctx.createLinearGradient(x - mastW, 0, x + mastW, 0);
  if (isMaster) {
    mastGrad.addColorStop(0,    '#7A4A1A');
    mastGrad.addColorStop(0.45, '#FFC890');
    mastGrad.addColorStop(1,    '#5A3210');
  } else {
    mastGrad.addColorStop(0,    isLight ? '#0B3258' : '#13243C');
    mastGrad.addColorStop(0.45, isLight ? '#82BFFF' : '#9ECCFF');
    mastGrad.addColorStop(1,    isLight ? '#0B3258' : '#0B1A2E');
  }
  ctx.fillStyle = mastGrad;
  ctx.beginPath();
  ctx.moveTo(x - mastW * 0.75, y);
  ctx.lineTo(x + mastW * 0.75, y);
  ctx.lineTo(x + mastW * 0.45, mastTop + hubR * 0.6);
  ctx.lineTo(x - mastW * 0.45, mastTop + hubR * 0.6);
  ctx.closePath();
  ctx.fill();

  // Aspas: 3 palas a 120° rotando lentamente con phase
  const cx = x, cy = mastTop;
  const rot = phase * 0.6 + (isMaster ? phase * 0.5 : 0);
  for (let i = 0; i < 3; i++) {
    const a = rot + (i * 2 * Math.PI / 3);
    const x2 = cx + Math.cos(a) * bladeL;
    const y2 = cy + Math.sin(a) * bladeL * 0.95;
    // pala: línea con base ancha y punta fina (cuadrilátero)
    const perp = a + Math.PI / 2;
    const w0 = 1.7 * scale, w1 = 0.5 * scale;
    const ax = cx + Math.cos(perp) * w0, ay = cy + Math.sin(perp) * w0;
    const bx = cx - Math.cos(perp) * w0, by = cy - Math.sin(perp) * w0;
    const dx = x2 + Math.cos(perp) * w1, dy = y2 + Math.sin(perp) * w1;
    const ex = x2 - Math.cos(perp) * w1, ey = y2 - Math.sin(perp) * w1;
    const bladeGrad = ctx.createLinearGradient(cx, cy, x2, y2);
    if (isMaster) {
      bladeGrad.addColorStop(0, '#FFE7C7');
      bladeGrad.addColorStop(1, '#B45309');
    } else {
      bladeGrad.addColorStop(0, isLight ? '#FFFFFF' : '#E0EFFF');
      bladeGrad.addColorStop(1, isLight ? '#0F5FA8' : '#4DA3FF');
    }
    ctx.fillStyle = bladeGrad;
    ctx.strokeStyle = isLight ? 'rgba(11,18,32,0.55)' : 'rgba(0,0,0,0.55)';
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.lineTo(ex, ey);
    ctx.lineTo(dx, dy);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }

  // Hub (nacela) con relieve
  const hubGrad = ctx.createRadialGradient(cx - 1, cy - 1, 0.6, cx, cy, hubR);
  hubGrad.addColorStop(0, '#FFFFFF');
  hubGrad.addColorStop(1, isMaster ? '#B45309' : (isLight ? '#0F5FA8' : '#1B3A66'));
  ctx.fillStyle = hubGrad;
  ctx.beginPath();
  ctx.arc(cx, cy, hubR, 0, 2*Math.PI);
  ctx.fill();
  ctx.strokeStyle = 'rgba(0,0,0,0.4)';
  ctx.lineWidth = 0.6;
  ctx.stroke();

  ctx.restore();
}

function drawRadiusCircle(ctx, lat, lon, km) {
  const N = 96;
  const R_EARTH = 6371;
  const ang = km / R_EARTH;     // radianes
  const lat0 = lat * Math.PI/180, lon0 = lon * Math.PI/180;
  const pts = [];
  for (let i = 0; i <= N; i++) {
    const brg = (i / N) * 2 * Math.PI;
    const lat2 = Math.asin(Math.sin(lat0)*Math.cos(ang) + Math.cos(lat0)*Math.sin(ang)*Math.cos(brg));
    const lon2 = lon0 + Math.atan2(Math.sin(brg)*Math.sin(ang)*Math.cos(lat0),
                                   Math.cos(ang) - Math.sin(lat0)*Math.sin(lat2));
    pts.push([lon2 * 180/Math.PI, lat2 * 180/Math.PI]);
  }
  ctx.beginPath();
  let started = false;
  for (const p of pts) {
    if (!isPointVisible(p[1], p[0])) { started = false; continue; }
    const c = GLOBE.mainProj(p);
    if (!c) { started = false; continue; }
    if (!started) { ctx.moveTo(c[0], c[1]); started = true; }
    else ctx.lineTo(c[0], c[1]);
  }
  ctx.strokeStyle = 'rgba(229,168,107,0.65)';
  ctx.setLineDash([6, 4]);
  ctx.lineWidth = 1.4;
  ctx.stroke();
  ctx.setLineDash([]);
}

// ──────────────────────────── POI + estaciones ───────────────────────────────
function setGlobePoi(lat, lon) {
  GLOBE.poi = { lat, lon };
  document.getElementById('btn-globe-clear-poi')?.classList.add('is-visible');
  globeFilterByRadius();
  if (GLOBE.mode === 'leaflet' && GLOBE.leafletMap) {
    GLOBE.leafletMap.flyTo([lat, lon], Math.max(GLOBE.leafletMap.getZoom(), 7), { duration: 0.9 });
    leafletRenderPoi();
  } else {
    globeFlyTo(lon, lat, 800, 1.6);
  }
}

function clearGlobePoi() {
  GLOBE.poi = null;
  GLOBE.visibleSites = [];
  GLOBE.master = null;
  document.getElementById('btn-globe-clear-poi')?.classList.remove('is-visible');
  document.getElementById('globe-panel')?.classList.remove('is-open');
  const status = document.getElementById('globe-status');
  if (status) status.textContent = 'Marca un sitio en el mapa o busca una dirección';
  leafletClearAll();
}

function globeHaversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 +
            Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) *
            Math.sin(dLon/2)**2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

async function globeFilterByRadius() {
  if (!GLOBE.poi) return;
  if (!GLOBE.sites.length) await globePreloadSites();
  const km = Math.max(1, parseFloat(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM);
  const inside = [];
  GLOBE.sites.forEach(s => {
    const d = globeHaversineKm(GLOBE.poi.lat, GLOBE.poi.lon, s.lat, s.lon);
    if (d <= km) inside.push({ ...s, _dist: d });
  });
  inside.sort((a, b) => a._dist - b._dist);
  GLOBE.visibleSites = inside;
  // Maestra por default = la más cercana
  GLOBE.master = inside[0]?.filename || null;
  globeRenderStationList();
  globeUpdateStatus();
  // Abrir el panel
  document.getElementById('globe-panel')?.classList.add('is-open');
  // Si estamos en modo Leaflet, redibujar marcadores y círculo
  if (GLOBE.mode === 'leaflet' && GLOBE.leafletMap) {
    leafletRenderPoi();
    leafletRenderStations();
  }
}

function globeUpdateStatus() {
  const status = document.getElementById('globe-status');
  if (!status) return;
  const km = Math.max(1, parseFloat(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM);
  if (!GLOBE.poi) {
    status.textContent = 'Marca un sitio en el mapa o busca una dirección';
    return;
  }
  const n = GLOBE.visibleSites.length;
  if (n === 0) {
    status.innerHTML = `Sin estaciones a <b>${km} km</b> de tu sitio. Aumenta el radio o sube tu archivo.`;
  } else {
    status.innerHTML = `<b>${n}</b> estaciones a ≤ <b>${km} km</b> · ` +
      `${GLOBE.poi.lat.toFixed(3)}°, ${GLOBE.poi.lon.toFixed(3)}°`;
  }
}

function globeRenderStationList() {
  const list = document.getElementById('globe-station-list');
  const title = document.getElementById('globe-panel-title');
  if (!list) return;
  if (!GLOBE.visibleSites.length) {
    list.innerHTML = '<div class="globe-panel__empty">Sin estaciones a este radio. Aumenta el radio o usa "+ Cargar mi propio archivo".</div>';
    if (title) title.textContent = '0 estaciones';
    return;
  }
  if (title) title.textContent = `${GLOBE.visibleSites.length} estaciones cercanas`;
  list.innerHTML = GLOBE.visibleSites.map(s => {
    const isMaster = (GLOBE.master === s.filename);
    const src = (s.source || '').toUpperCase();
    return `<div class="globe-station ${isMaster ? 'is-master' : ''}" data-fn="${s.filename}">
      <div class="globe-station__star">${isMaster ? '★' : '○'}</div>
      <div class="globe-station__body">
        <div class="globe-station__name">${s.display_name || s.filename}</div>
        <div class="globe-station__meta">${src} · ${s.lat.toFixed(2)}°, ${s.lon.toFixed(2)}°${s.state ? ' · ' + s.state : ''}</div>
      </div>
      <div class="globe-station__dist">${s._dist.toFixed(0)} km</div>
    </div>`;
  }).join('');
  list.querySelectorAll('.globe-station').forEach(el => {
    el.addEventListener('click', () => handleGlobeStationPick(el.dataset.fn));
  });
}

// ─── HEIGHT NORMALIZATION MODAL ───────────────────────────────────────────────

function showHeightNormModal(filename, source) {
  const site = GLOBE.visibleSites.find(s => s.filename === filename) ||
               GLOBE.sites.find(s => s.filename === filename);

  // Derive native height: from site metadata, then from filename pattern, then 50 m
  let nativeH = site?.wind_height_m ?? site?.z_ref ?? null;
  if (!nativeH) {
    const m = filename.match(/_(\d{2,3})M_|_U(\d{2,3})M/i);
    nativeH = m ? parseInt(m[1] || m[2]) : 50;
  }
  nativeH = Number(nativeH) || 50;

  // Override: archivos con 'certe' o '2018' en el nombre → altura nativa 80 m
  const fnLower = (filename || '').toLowerCase();
  if (fnLower.includes('certe') || fnLower.includes('2018')) {
    nativeH = 80;
  }

  const modal = document.getElementById('height-norm-modal');
  document.getElementById('hnm-station-desc').textContent =
    (site?.display_name || site?.municipality || filename) + ' — ' + (site?.state || '');
  document.getElementById('hnm-native-h').textContent = nativeH + ' m';
  document.getElementById('hnm-source-lbl').textContent =
    site?.source?.toUpperCase() || filename.includes('MERRA') ? 'MERRA-2' :
    filename.includes('ERA5') ? 'ERA5' : 'AUTO';

  const targetInput = document.getElementById('hnm-target-h');
  targetInput.value = WP.referenceHeight ?? nativeH;
  document.getElementById('hnm-method').value = WP.referenceMethod || 'hellmann';
  document.getElementById('hnm-preview').style.display = 'none';

  // Show modal
  modal.style.display = 'flex';
  modal.setAttribute('aria-hidden', 'false');
  targetInput.focus();
  targetInput.select();

  // Preview text on input change
  targetInput.oninput = () => {
    const t = parseFloat(targetInput.value);
    const prev = document.getElementById('hnm-preview');
    if (t && t !== nativeH) {
      prev.textContent = `Datos convertidos de ${nativeH} m → ${t} m antes del análisis`;
      prev.style.display = '';
    } else {
      prev.style.display = 'none';
    }
  };

  return new Promise((resolve) => {
    document.getElementById('hnm-confirm').onclick = () => {
      const h = parseFloat(targetInput.value) || nativeH;
      const method = document.getElementById('hnm-method').value;
      closeHeightNormModal();
      resolve({ confirmed: true, quickDesign: false, targetHeight: h, method, nativeHeight: nativeH, filename, source });
    };
    const qdBtn = document.getElementById('hnm-quick');
    if (qdBtn) {
      qdBtn.onclick = () => {
        const h = parseFloat(targetInput.value) || nativeH;
        const method = document.getElementById('hnm-method').value;
        closeHeightNormModal();
        resolve({ confirmed: true, quickDesign: true, targetHeight: h, method, nativeHeight: nativeH, filename, source });
      };
    }
    document.getElementById('hnm-cancel').onclick = () => {
      closeHeightNormModal();
      resolve({ confirmed: false });
    };
    document.getElementById('height-norm-backdrop').onclick = () => {
      closeHeightNormModal();
      resolve({ confirmed: false });
    };
  });
}

function closeHeightNormModal() {
  const modal = document.getElementById('height-norm-modal');
  if (modal) { modal.style.display = 'none'; modal.setAttribute('aria-hidden', 'true'); }
}

async function handleGlobeStationPick(filename) {
  if (!filename) return;
  const site = GLOBE.visibleSites.find(s => s.filename === filename) ||
               GLOBE.sites.find(s => s.filename === filename);
  GLOBE.master = filename;
  globeRenderStationList();

  const result = await showHeightNormModal(filename, site?.source || 'auto');
  if (!result.confirmed) return;

  WP.referenceHeight = result.targetHeight;
  WP.referenceMethod = result.method;
  applyReferenceHeightToUI(result.targetHeight);

  try {
    await loadPresetFileByName(filename, site?.source || 'auto',
                               result.targetHeight, result.method);
  } finally {
    collapseGlobeStage();
    if (result.quickDesign) {
      setTimeout(() => quickDesignStart(result), 400);
    } else if (typeof runResumenPipeline === 'function') {
      setTimeout(() => runResumenPipeline(), 350);
    }
  }
}

// ═══════════════════════ DISEÑO RÁPIDO (QUICK DESIGN) ════════════════════════
/**
 * Estado global del pipeline rápido.
 * Persiste resultados para que cada módulo los muestre pre-cargados.
 */
const QD = {
  active:    false,
  station:   null,
  refHeight: null,
  results:   {},
  // Draw-modal state
  drawMap:      null,
  drawPoints:   [],
  drawMarkers:  [],
  drawPolyLine: null,
  drawPolygon:  null,
  params:       {},
};

function qdSetStep(n, msg) {
  const total = 6;
  document.querySelectorAll('.qd-dot').forEach((d, i) => {
    d.classList.toggle('done',   i + 1 < n);
    d.classList.toggle('active', i + 1 === n);
  });
  const pct = Math.round(((n - 1) / total) * 100);
  const fill = document.getElementById('qd-progress-fill');
  if (fill) fill.style.width = pct + '%';
  const lbl = document.getElementById('qd-step-label');
  if (lbl) lbl.textContent = msg || '';
  const msgEl = document.getElementById('qd-msg');
  if (msgEl) msgEl.textContent = '';
}

function qdMsg(txt) {
  const el = document.getElementById('qd-msg');
  if (el) el.textContent = txt;
}

function quickDesignCancel() {
  QD.active = false;
  document.getElementById('qd-wizard').style.display = 'none';
  document.getElementById('qd-parque-banner').style.display = 'none';
  toast('Diseño rápido cancelado.', 'info');
}

/** Punto de entrada: abre el popup de dibujo en lugar de navegar a Parque. */
function quickDesignStart(modalResult) {
  QD.active    = true;
  QD.station   = modalResult.filename;
  QD.refHeight = modalResult.targetHeight;
  QD.results   = {};
  QD.drawPoints = []; QD.drawMarkers = [];
  QD.drawPolyLine = null; QD.drawPolygon = null;
  qdDrawModalOpen(modalResult);
}

// ─── QD DRAW MODAL ────────────────────────────────────────────────────────────
async function qdDrawModalOpen(modalResult) {
  // Populate turbine select from MCDA cache or API
  const sel = document.getElementById('qd-turbine');
  if (sel && sel.options.length === 0) {
    let turbines = (typeof MCDA !== 'undefined' && MCDA.turbines?.length)
      ? MCDA.turbines
      : [];
    if (!turbines.length) {
      try {
        const r = await api('/api/energy/turbines', 'GET');
        turbines = (r.turbines || []).filter(t => t.source === 'file');
        if (typeof MCDA !== 'undefined') MCDA.turbines = turbines;
      } catch (_) {}
    }
    turbines.forEach(t => {
      const mw  = t.rated_power_kw ? (t.rated_power_kw/1000).toFixed(1)+' MW' : '';
      const rot = t.rotor_diameter_m ? 'Ø'+t.rotor_diameter_m+'m' : '';
      sel.appendChild(new Option(`${t.name||t.filename} ${mw} ${rot}`.trim(), t.filename));
    });
    // Pre-select G80
    const g80 = Array.from(sel.options).find(o =>
      o.value.toLowerCase().includes('g80') ||
      o.textContent.toLowerCase().includes('g80') ||
      o.value.toLowerCase().includes('gamesa'));
    if (g80) sel.value = g80.value;
  }

  const modal = document.getElementById('qd-draw-modal');
  modal.style.display = 'flex';
  qdClearPoly();

  // Init map after Leaflet can measure container size
  setTimeout(() => {
    const site = ((typeof GLOBE !== 'undefined') &&
      [...(GLOBE.visibleSites||[]), ...(GLOBE.sites||[])]
        .find(s => s.filename === (modalResult?.filename || QD.station)));
    const center = (site?.lat && site?.lon) ? [site.lat, site.lon] : [16.533, -94.957];
    qdDrawMapInit(center);
  }, 160);
}

function qdDrawModalClose() {
  const modal = document.getElementById('qd-draw-modal');
  if (modal) modal.style.display = 'none';
  if (QD.drawMap) { try { QD.drawMap.remove(); } catch(_){} QD.drawMap = null; }
  QD.drawMarkers = []; QD.drawPolyLine = null; QD.drawPolygon = null; QD.drawPoints = [];
  QD.active = false;
}

function qdDrawMapInit(center) {
  const el = document.getElementById('qd-draw-map');
  if (!el || typeof L === 'undefined') return;
  if (QD.drawMap) { QD.drawMap.invalidateSize(); return; }

  QD.drawMap = L.map('qd-draw-map', { zoomControl: true, attributionControl: false })
    .setView(center, 11);

  const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 });
  const sat = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 19 });
  osm.addTo(QD.drawMap);
  L.control.layers({ 'Mapa': osm, 'Satélite': sat }, {}, { position: 'topright' }).addTo(QD.drawMap);

  // Station marker
  L.circleMarker(center, {
    radius: 7, color: '#f59e0b', fillColor: '#f59e0b', fillOpacity: 0.9, weight: 2
  }).bindTooltip('Estación meteorológica').addTo(QD.drawMap);

  // Kriging overlay if available
  if (typeof KRIG !== 'undefined' && KRIG.result) {
    const grid = KRIG.result.wind || KRIG.result.speed || KRIG.result[Object.keys(KRIG.result)[0]];
    if (grid?.img_b64 && grid?.bounds) {
      const b = grid.bounds;
      L.imageOverlay('data:image/png;base64,' + grid.img_b64,
        [[b.lat_min, b.lon_min],[b.lat_max, b.lon_max]], { opacity: 0.6 }).addTo(QD.drawMap);
    }
  }

  QD.drawMap.on('click', e => qdAddVertex(e.latlng.lat, e.latlng.lng));
  QD.drawMap.on('dblclick', e => { e.originalEvent.preventDefault(); qdClosePolygon(); });
  QD.drawMap.on('contextmenu', e => { e.originalEvent.preventDefault(); qdClosePolygon(); });
  QD.drawMap.invalidateSize();
}

function qdAddVertex(lat, lon) {
  QD.drawPoints.push([lat, lon]);
  const m = L.circleMarker([lat, lon], {
    radius: 5, color: '#22c55e', fillColor: '#22c55e', fillOpacity: 1, weight: 2
  }).addTo(QD.drawMap);
  QD.drawMarkers.push(m);
  qdUpdatePolyVisual();
  const n = QD.drawPoints.length;
  const countEl = document.getElementById('qd-poly-count');
  if (countEl) countEl.textContent = n < 3
    ? `${n} punto${n!==1?'s':''} — agrega ${3-n} más para habilitar cálculo`
    : `${n} vértices · clic derecho o doble clic para cerrar`;
  if (n >= 3) {
    const btn = document.getElementById('qd-calc-btn');
    if (btn) { btn.disabled = false; btn.style.opacity = '1'; btn.style.pointerEvents = ''; }
  }
}

function qdUpdatePolyVisual() {
  if (QD.drawPolyLine) { QD.drawMap.removeLayer(QD.drawPolyLine); QD.drawPolyLine = null; }
  if (QD.drawPolygon)  { QD.drawMap.removeLayer(QD.drawPolygon);  QD.drawPolygon  = null; }
  const pts = QD.drawPoints;
  if (pts.length < 2) return;
  if (pts.length >= 3) {
    QD.drawPolygon = L.polygon(pts, {
      color: '#f59e0b', fillColor: 'rgba(245,158,11,.15)', weight: 2, dashArray: '6 4'
    }).addTo(QD.drawMap);
  } else {
    QD.drawPolyLine = L.polyline(pts, { color: '#f59e0b', weight: 2, dashArray: '6 4' }).addTo(QD.drawMap);
  }
}

function qdClosePolygon() {
  if (QD.drawPoints.length < 3) return;
  if (QD.drawPolyLine) { QD.drawMap.removeLayer(QD.drawPolyLine); QD.drawPolyLine = null; }
  if (QD.drawPolygon)  { QD.drawMap.removeLayer(QD.drawPolygon);  QD.drawPolygon  = null; }
  QD.drawPolygon = L.polygon(QD.drawPoints, {
    color: '#22c55e', fillColor: 'rgba(34,197,94,.18)', weight: 2.5
  }).addTo(QD.drawMap);
  const n = QD.drawPoints.length;
  const countEl = document.getElementById('qd-poly-count');
  if (countEl) countEl.textContent = `✓ Polígono cerrado · ${n} vértices`;
}

function qdClearPoly() {
  QD.drawPoints = [];
  QD.drawMarkers.forEach(m => { try { QD.drawMap?.removeLayer(m); } catch(_){} });
  QD.drawMarkers = [];
  if (QD.drawPolyLine) { try { QD.drawMap?.removeLayer(QD.drawPolyLine); } catch(_){} QD.drawPolyLine = null; }
  if (QD.drawPolygon)  { try { QD.drawMap?.removeLayer(QD.drawPolygon);  } catch(_){} QD.drawPolygon  = null; }
  const countEl = document.getElementById('qd-poly-count');
  if (countEl) countEl.textContent = 'Sin polígono — haz clic en el mapa para comenzar';
  const btn = document.getElementById('qd-calc-btn');
  if (btn) { btn.disabled = true; btn.style.opacity = '.45'; btn.style.pointerEvents = 'none'; }
}

function qdCollectParams() {
  const g = id => document.getElementById(id);
  return {
    turbineKey:  g('qd-turbine')?.value       || '',
    nTurbines:   parseInt(g('qd-nturbines')?.value)    || 10,
    minSepD:     parseFloat(g('qd-min-sep')?.value)    || 4,
    layoutType:  g('qd-layout-type')?.value            || 'auto',
    ventaTotal:  g('qd-venta-total')?.checked          ?? true,
    price:       parseFloat(g('qd-price')?.value)      || 0.10,
    rate:        parseFloat(g('qd-rate')?.value)       || 9.5,
    years:       parseInt(g('qd-years')?.value)        || 25,
    escalation:  parseFloat(g('qd-escalation')?.value) || 0,
    debtRatio:   parseFloat(g('qd-debt-ratio')?.value) || 70,
    debtRate:    parseFloat(g('qd-debt-rate')?.value)  || 7,
    loanYears:   parseInt(g('qd-loan-years')?.value)   || 15,
    equityRate:  parseFloat(g('qd-equity-rate')?.value)|| 3,
    kWake:       0.09,   // Istmo TI alto
  };
}

async function qdStartCalc() {
  if (QD.drawPoints.length < 3) { toast('Dibuja al menos 3 puntos en el mapa.', 'warn'); return; }

  QD.params  = qdCollectParams();
  const poly = [...QD.drawPoints];

  // Close draw modal (destroy map)
  const modal = document.getElementById('qd-draw-modal');
  if (modal) modal.style.display = 'none';
  if (QD.drawMap) { try { QD.drawMap.remove(); } catch(_){} QD.drawMap = null; }

  // Store polygon in KRIG so parqueOptimize() picks it up
  if (typeof KRIG !== 'undefined') KRIG.parkPoints = poly;

  // Show wizard
  const wiz = document.getElementById('qd-wizard');
  if (wiz) wiz.style.display = 'block';
  qdSetStep(4, 'Preparando parque…');
  qdMsg('Configurando parámetros…');

  // Pre-fill ALL Parque DOM inputs
  const sv = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  sv('parque-turbine',     QD.params.turbineKey);
  sv('parque-n-turbines',  QD.params.nTurbines);
  sv('parque-min-sep-d',   QD.params.minSepD);
  sv('parque-layout-type', QD.params.layoutType);
  sv('parque-demand',      0);          // venta total
  sv('parque-avail',       97);
  sv('parque-elec',        3);
  sv('parque-kwake',       QD.params.kWake);

  // Pre-fill TE DOM inputs
  sv('te-turbine',          QD.params.turbineKey);
  sv('te-nturbines',        QD.params.nTurbines);
  sv('te-price',            QD.params.price);
  sv('te-rate',             QD.params.rate);
  sv('te-years',            QD.params.years);
  sv('te-price-escalation', QD.params.escalation);
  sv('te-demand',           0);

  // Pre-fill leverage params
  WP.levParams = {
    debt_ratio_pct:           QD.params.debtRatio,
    interest_rate_pct:        QD.params.debtRate,
    loan_years:               QD.params.loanYears,
    equity_discount_rate_pct: QD.params.equityRate,
  };

  // Navigate to Parque and run
  const parqueBtn = document.querySelector('.nav-btn[data-module="parque"]');
  if (parqueBtn) parqueBtn.click();

  WP._qdPipelineReady = true;
  setTimeout(async () => {
    if (typeof parqueRenderPolygon === 'function') parqueRenderPolygon();
    await parqueOptimize();
  }, 700);
}

/**
 * Hook called at the end of parqueOptimize() success path in quick-design mode.
 * Continues the pipeline: TE → report.
 */
async function quickDesignContinueAfterParque(optResult) {
  if (!QD.active) return;
  QD.results.parque = optResult;

  // Hide parque banner
  const banner = document.getElementById('qd-parque-banner');
  if (banner) banner.style.display = 'none';

  // Step 5: Tecnoeconómico
  qdSetStep(5, 'Tecnoeconómico…');
  qdMsg('Calculando VAN, LCOE, TIR, payback…');
  try {
    await calcTechnoEconomic();
    QD.results.techno = WP.technoScenarios[WP.technoActiveScenario]?.result || null;
  } catch (e) {
    qdMsg('Error en TE: ' + e.message);
  }

  // Step 6: Report
  qdSetStep(6, 'Generando informe…');
  qdMsg('Compilando resultados…');
  await new Promise(r => setTimeout(r, 600));

  // Mark all done
  document.querySelectorAll('.qd-dot').forEach(d => {
    d.classList.remove('active'); d.classList.add('done');
  });
  document.getElementById('qd-progress-fill').style.width = '100%';
  document.getElementById('qd-step-label').textContent = '✓ Informe listo';
  qdMsg('Abriendo en nueva pestaña…');

  // Navigate to Export and generate report
  const expBtn = document.querySelector('.nav-btn[data-module="export"]');
  if (expBtn) setTimeout(() => expBtn.click(), 300);

  setTimeout(() => quickDesignGenerateReport(), 800);

  // Auto-hide wizard after 4s
  setTimeout(() => {
    if (document.getElementById('qd-wizard'))
      document.getElementById('qd-wizard').style.display = 'none';
    QD.active = false;
    WP._qdPipelineReady = false;
  }, 4000);
}

/** Helper: wait until predicate() is true, polling every 500 ms, with timeout. */
function qdWaitFor(predicate, timeoutMs = 120000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const check = () => {
      if (predicate()) { resolve(); return; }
      if (Date.now() - t0 > timeoutMs) { reject(new Error('Timeout esperando resultado')); return; }
      setTimeout(check, 500);
    };
    check();
  });
}

/** Generate and open a comprehensive HTML feasibility report in a new window. */
function quickDesignGenerateReport() {
  const sim   = QD.results.parque?.simulation || PARQUE.result || {};
  const opt   = QD.results.parque  || PARQUE.optResult || {};
  const te    = QD.results.techno  || WP.technoResult  || {};
  const stats = WP.windStats        || {};
  const turb  = opt.turbine         || te.turbine       || {};

  const now       = new Date();
  const dateStr   = now.toLocaleDateString('es-MX', { year:'numeric', month:'long', day:'numeric' });
  const stationId = QD.station?.replace('.csv','') || '—';
  const refH      = QD.refHeight || wsH();
  const turbName  = turb.name || '—';
  const turbMW    = turb.rated_power_kw ? (turb.rated_power_kw/1000).toFixed(2) : '—';
  const turbD     = turb.rotor_diameter_m ? turb.rotor_diameter_m + ' m' : '—';
  const turbHub   = turb.hub_height_m ? turb.hub_height_m + ' m' : '—';

  const nTurb     = sim.n_turbines || opt.n_placed || '—';
  const instMW    = sim.installed_mw || opt.installed_mw || '—';
  const aepGWh    = sim.aep_mwh ? (Number(sim.aep_mwh)/1000).toFixed(3) : '—';
  const cf        = sim.cf_pct ? Number(sim.cf_pct).toFixed(2) + '%' : '—';
  const wakeLoss  = sim.wake_loss_pct ? Number(sim.wake_loss_pct).toFixed(2) + '%' : '—';
  const flh       = sim.flh ? Math.round(sim.flh).toLocaleString() : '—';
  const domDir    = sim.dominant_dir_deg ?? opt.layout_config?.dominant_dir_deg;
  const layoutTyp = opt.layout_config?.layout_type || '—';
  const sPara     = opt.layout_config?.s_para_d;
  const sPerp     = opt.layout_config?.s_perp_d;

  const an        = te.analysis || {};
  const capex     = an.capex_total_usd   ? '$' + Math.round(an.capex_total_usd).toLocaleString() : '—';
  const lcoe      = an.lcoe_usd_kwh      ? an.lcoe_usd_kwh.toFixed(4) : '—';
  const npv       = an.npv_usd           ? '$' + Math.round(an.npv_usd).toLocaleString()          : '—';
  const payback   = an.payback_years     ? Number(an.payback_years).toFixed(1) + ' años'          : '—';
  const roi       = an.roi               ? (Number(an.roi)*100).toFixed(1) + '%'                  : '—';

  const vMean     = stats.wind_stats?.mean_speed_ms ? Number(stats.wind_stats.mean_speed_ms).toFixed(2) : '—';
  const wK        = stats.wind_stats?.weibull_k     ? Number(stats.wind_stats.weibull_k).toFixed(3)     : '—';
  const wC        = stats.wind_stats?.weibull_c     ? Number(stats.wind_stats.weibull_c).toFixed(3)     : '—';

  // CO₂ avoided (Mexico SFE grid factor ≈ 0.454 tCO₂/MWh)
  const co2Factor = 0.454;
  const co2Avoided = sim.aep_mwh ? (Number(sim.aep_mwh) * co2Factor / 1000).toFixed(1) : '—';
  const treesEq    = sim.aep_mwh ? Math.round(Number(sim.aep_mwh) * co2Factor / 0.022).toLocaleString() : '—';

  const row = (label, value, unit='') => `
    <tr><td style="color:#6b7280;padding:.4rem .6rem;border-bottom:1px solid #1f2937">${label}</td>
        <td style="font-weight:600;padding:.4rem .6rem;border-bottom:1px solid #1f2937;text-align:right">${value}<span style="font-size:.8em;font-weight:400;color:#6b7280;margin-left:.3rem">${unit}</span></td></tr>`;

  const section = (title, content) => `
    <div style="margin-bottom:2rem;break-inside:avoid">
      <h3 style="font-size:1rem;font-weight:700;color:#0ea5e9;border-bottom:2px solid #0ea5e9;padding-bottom:.35rem;margin:0 0 .75rem">${title}</h3>
      ${content}
    </div>`;

  const table = (rows) => `<table style="width:100%;border-collapse:collapse;font-size:.88rem">${rows}</table>`;

  const html = `<!DOCTYPE html><html lang="es"><head>
<meta charset="UTF-8">
<title>Informe de Factibilidad Eólica — AERO OPEN CIM</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0b1220; color: #e2e8f0; }
  @media print { body { background: #fff; color: #000; } .no-print { display:none } }
  .page { max-width: 900px; margin: 0 auto; padding: 2.5rem 2rem; }
  .cover { text-align: center; padding: 4rem 2rem 3rem; background: linear-gradient(135deg,#0f172a,#1e3a5f); border-radius: 16px; margin-bottom: 2.5rem; }
  .kpi-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: .75rem; margin-bottom: 1.5rem; }
  .kpi { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: .75rem 1rem; }
  .kpi-val { font-size: 1.5rem; font-weight: 700; color: #22c55e; }
  .kpi-lbl { font-size: .73rem; color: #6b7280; margin-top: .2rem; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
  h1 { font-size: 2rem; font-weight: 800; color: #f0f9ff; }
  h2 { font-size: 1rem; color: #94a3b8; font-weight: 400; margin-top:.5rem }
  .badge { display: inline-block; background: #14532d; color: #86efac; font-size: .75rem; border-radius: 20px; padding: .2rem .7rem; margin-top: .5rem; }
  @media print {
    .cover { background: #fff; color: #000; }
    .kpi { background: #f9fafb; border: 1px solid #e5e7eb; }
    .kpi-val { color: #059669; }
    body { color: #111827; }
  }
</style></head><body>
<div class="page">

  <!-- COVER -->
  <div class="cover">
    <div style="font-size:.7rem;letter-spacing:.15em;text-transform:uppercase;color:#38bdf8;margin-bottom:.5rem">AERO OPEN CIM — Informe de factibilidad eólica</div>
    <h1>Parque Eólico<br><span style="color:#38bdf8">${stationId}</span></h1>
    <h2>Estación meteorológica: ${stationId} &nbsp;·&nbsp; Altura de referencia: ${refH} m</h2>
    <div class="badge">Generado: ${dateStr}</div>
  </div>

  <!-- EXECUTIVE KPIs -->
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-val">${aepGWh}</div><div class="kpi-lbl">AEP GWh/año</div></div>
    <div class="kpi"><div class="kpi-val">${instMW}</div><div class="kpi-lbl">MW instalados</div></div>
    <div class="kpi"><div class="kpi-val">${cf}</div><div class="kpi-lbl">Factor de capacidad</div></div>
    <div class="kpi"><div class="kpi-val">${npv}</div><div class="kpi-lbl">VAN (USD)</div></div>
    <div class="kpi"><div class="kpi-val">${lcoe}</div><div class="kpi-lbl">LCOE (USD/kWh)</div></div>
    <div class="kpi"><div class="kpi-val">${payback}</div><div class="kpi-lbl">Payback</div></div>
  </div>

  <div class="two-col">
    <div>
      ${section('1. Recurso eólico', table(
        row('Velocidad media hub', vMean, 'm/s') +
        row('Weibull k', wK) +
        row('Weibull c', wC, 'm/s') +
        row('Altura de referencia', refH, 'm') +
        row('Dirección dominante (energía)', domDir != null ? Math.round(domDir)+'°' : '—') +
        row('Fuente de datos', stationId)
      ))}

      ${section('2. Turbina seleccionada (TOPSIS #1)', table(
        row('Modelo', turbName) +
        row('Potencia nominal', turbMW, 'MW') +
        row('Diámetro de rotor', turbD) +
        row('Altura de buje', turbHub) +
        row('Corte de entrada', turb.cut_in != null ? turb.cut_in : '—', 'm/s') +
        row('Corte de salida', turb.cut_out != null ? turb.cut_out : '—', 'm/s')
      ))}
    </div>
    <div>
      ${section('3. Modelo de energía del parque', table(
        row('N° aerogeneradores', nTurb) +
        row('Capacidad instalada', instMW, 'MW') +
        row('AEP bruto', sim.aep_no_wake_mwh ? (Number(sim.aep_no_wake_mwh)/1000).toFixed(3) : '—', 'GWh/año') +
        row('AEP neto (con estela)', aepGWh, 'GWh/año') +
        row('Pérdida por estela (wake)', wakeLoss) +
        row('Factor de capacidad', cf) +
        row('Horas equivalentes', flh, 'h/año') +
        row('Tipo layout', layoutTyp) +
        (sPara && sPerp ? row('Separación (axial × transv.)', `${sPara}D × ${sPerp}D`) : '') +
        row('Área del parque', opt.area_km2 ? opt.area_km2 + ' km²' : '—') +
        row('Densidad', opt.density_mw_km2 ? opt.density_mw_km2 + ' MW/km²' : '—')
      ))}

      ${section('4. Análisis financiero', table(
        row('CAPEX total', capex) +
        row('LCOE', lcoe, 'USD/kWh') +
        row('VAN / NPV', npv, 'USD') +
        row('ROI', roi) +
        row('Payback simple', payback) +
        row('Precio de energía', an.energy_price_usd_kwh ? an.energy_price_usd_kwh.toFixed(4) : '—', 'USD/kWh') +
        row('Tasa de descuento', an.discount_rate_pct ? an.discount_rate_pct + '%' : '—') +
        row('Horizonte del proyecto', an.project_years ? an.project_years + ' años' : '—')
      ))}
    </div>
  </div>

  ${section('5. Impacto ambiental', `
    ${table(
      row('CO₂ evitado (factor red México 0.454 tCO₂/MWh)', co2Avoided, 'ktCO₂/año') +
      row('Equivalente en árboles plantados', treesEq, 'árboles/año') +
      row('Energía renovable generada', aepGWh, 'GWh/año') +
      row('Desplazamiento de generación fósil', instMW, 'MW')
    )}
  `)}

  ${section('6. Conclusión', `
    <div style="font-size:.88rem;line-height:1.8;color:#d1d5db">
      El proyecto presenta un factor de capacidad de <strong>${cf}</strong> y una generación anual de
      <strong>${aepGWh} GWh</strong> con <strong>${nTurb} aerogeneradores ${turbName}</strong>.
      El LCOE de <strong>${lcoe} USD/kWh</strong> y VAN de <strong>${npv} USD</strong>
      ${an.npv_usd > 0 ? 'indican un <strong style="color:#22c55e">proyecto viable</strong>.' : 'requieren revisión de parámetros financieros.'}
      <br><br>
      <em>Informe generado automáticamente por AERO OPEN CIM — ${dateStr}. 
      Resultados sujetos a verificación con datos de campo y estudio de micrositing detallado.</em>
    </div>
  `)}

</div>
</body></html>`;

  const win = window.open('', '_blank', 'width=960,height=800,scrollbars=yes');
  if (win) {
    win.document.write(html);
    win.document.close();
    toast('Informe de factibilidad abierto en nueva pestaña.', 'success');
  } else {
    toast('Activa ventanas emergentes para ver el informe.', 'warn');
  }
}

// ──────────────────────────── BÚSQUEDA + GEOCODING ───────────────────────────
function bindGlobeSearchUI() {
  const inp = document.getElementById('globe-search-input');
  const btn = document.getElementById('btn-globe-search');
  if (!inp || !btn) return;
  let suggestTimer = null;

  inp.addEventListener('input', () => {
    clearTimeout(suggestTimer);
    const q = inp.value.trim();
    if (!q || q.length < 3 || /^-?\d/.test(q)) {
      hideGlobeSuggest();
      return;
    }
    suggestTimer = setTimeout(() => globeFetchSuggest(q), 280);
  });
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); globeSearchSubmit(); }
    if (e.key === 'Escape') { hideGlobeSuggest(); inp.blur(); }
  });
  btn.addEventListener('click', globeSearchSubmit);
  document.addEventListener('click', e => {
    if (!e.target.closest('#globe-search')) hideGlobeSuggest();
  });
}

async function globeFetchSuggest(q) {
  const box = document.getElementById('globe-search-suggest');
  if (!box) return;
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&addressdetails=1&countrycodes=mx&q=${encodeURIComponent(q)}`;
    const res = await fetch(url, { headers: { 'Accept-Language': 'es' } });
    const data = await res.json();
    if (!data || !data.length) { hideGlobeSuggest(); return; }
    box.innerHTML = data.map(d => {
      const name = d.display_name || d.name || '';
      return `<div class="globe-suggest__item" data-lat="${d.lat}" data-lon="${d.lon}">
        ${name}
        <small>${parseFloat(d.lat).toFixed(3)}°, ${parseFloat(d.lon).toFixed(3)}°</small>
      </div>`;
    }).join('');
    box.classList.add('is-open');
    box.querySelectorAll('.globe-suggest__item').forEach(el => {
      el.addEventListener('click', () => {
        const lat = parseFloat(el.dataset.lat);
        const lon = parseFloat(el.dataset.lon);
        document.getElementById('globe-search-input').value = el.firstChild.textContent.trim();
        hideGlobeSuggest();
        setGlobePoi(lat, lon);
        switchTo2DAt(lat, lon, 40);
      });
    });
  } catch (e) { hideGlobeSuggest(); }
}

function hideGlobeSuggest() {
  const box = document.getElementById('globe-search-suggest');
  if (box) { box.classList.remove('is-open'); box.innerHTML = ''; }
}

async function globeSearchSubmit() {
  const inp = document.getElementById('globe-search-input');
  const sp = document.getElementById('globe-search-spinner');
  const q = inp.value.trim();
  if (!q) return;
  hideGlobeSuggest();

  // 1) Coordenadas: "lat, lon" o "16.5, -95.0"
  const m = q.match(/^\s*(-?\d+(?:\.\d+)?)[,\s]+(-?\d+(?:\.\d+)?)\s*$/);
  if (m) {
    const lat = parseFloat(m[1]), lon = parseFloat(m[2]);
    if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
      setGlobePoi(lat, lon);
      switchTo2DAt(lat, lon, 40);
      return;
    }
  }

  // 2) Geocoding Nominatim (México first)
  sp?.classList.add('is-active');
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=mx&q=${encodeURIComponent(q)}`;
    let res = await fetch(url, { headers: { 'Accept-Language': 'es' } });
    let data = await res.json();
    if (!data || !data.length) {
      // fallback global
      const url2 = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(q)}`;
      res = await fetch(url2, { headers: { 'Accept-Language': 'es' } });
      data = await res.json();
    }
    if (data && data[0]) {
      const lat = parseFloat(data[0].lat), lon = parseFloat(data[0].lon);
      setGlobePoi(lat, lon);
      switchTo2DAt(lat, lon, 40);
    } else {
      toast(`No se encontró "${q}".`, 'warning');
    }
  } catch (e) {
    toast('Geocoding falló. Revisa tu conexión.', 'error');
  } finally {
    sp?.classList.remove('is-active');
  }
}

// ──────────────────────────── FLY-TO ─────────────────────────────────────────
function globeFlyTo(lon, lat, ms = 1100, targetZoom = 1.6) {
  if (!GLOBE.mainProj) return;
  GLOBE.autoSpin = false;
  if (GLOBE.flyAF) cancelAnimationFrame(GLOBE.flyAF);
  const startRot = [...GLOBE.rotate];
  const targetRot = [-lon, -lat, 0];
  const startZoom = GLOBE.zoomScale;
  const t0 = performance.now();
  function step(now) {
    const t = Math.min((now - t0) / ms, 1);
    const ease = t < 0.5 ? 2*t*t : -1 + (4 - 2*t)*t;
    GLOBE.rotate[0] = startRot[0] + (targetRot[0] - startRot[0]) * ease;
    GLOBE.rotate[1] = startRot[1] + (targetRot[1] - startRot[1]) * ease;
    GLOBE.zoomScale = startZoom + (targetZoom - startZoom) * ease;
    const r = Math.min(GLOBE.W, GLOBE.H) * 0.42 * GLOBE.zoomScale;
    GLOBE.mainProj.scale(r);
    if (t < 1) GLOBE.flyAF = requestAnimationFrame(step);
  }
  GLOBE.flyAF = requestAnimationFrame(step);
}

// ──────────────────────────── RADIO + BORRAR ─────────────────────────────────
function bindGlobeRadiusUI() {
  const r = document.getElementById('globe-radius');
  r?.addEventListener('input', () => { globeFilterByRadius(); });
  r?.addEventListener('change', () => { globeFilterByRadius(); });
  document.getElementById('btn-globe-clear-poi')?.addEventListener('click', clearGlobePoi);
}

// ──────────────────────────── MODAL UPLOAD ───────────────────────────────────
function bindGlobeUploadModalUI() {
  const open = document.getElementById('btn-globe-open-upload');
  const close = document.getElementById('btn-globe-modal-close');
  const back = document.getElementById('globe-modal-backdrop');
  const modal = document.getElementById('globe-modal');
  const zone = document.getElementById('globe-upload-zone');
  const input = document.getElementById('globe-file-input');
  const srcSel = document.getElementById('globe-source-select');

  const openModal  = () => modal?.classList.add('is-open');
  const closeModal = () => modal?.classList.remove('is-open');

  open?.addEventListener('click', openModal);
  close?.addEventListener('click', closeModal);
  back?.addEventListener('click', closeModal);

  zone?.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('is-drag'); });
  zone?.addEventListener('dragleave', () => zone.classList.remove('is-drag'));
  zone?.addEventListener('drop', e => {
    e.preventDefault(); zone.classList.remove('is-drag');
    if (e.dataTransfer.files[0]) handleGlobeOwnFile(e.dataTransfer.files[0], srcSel?.value);
  });
  zone?.addEventListener('click', () => input?.click());
  input?.addEventListener('change', () => {
    if (input.files[0]) handleGlobeOwnFile(input.files[0], srcSel?.value);
  });
}

async function handleGlobeOwnFile(file, source) {
  // Sincronizar con el select de Data Hub (que sigue oculto pero existe)
  const dhSrc = document.getElementById('source-select');
  if (dhSrc && source) dhSrc.value = source;
  document.getElementById('globe-modal')?.classList.remove('is-open');
  try {
    await uploadFile(file);
  } finally {
    collapseGlobeStage();
    if (typeof runResumenPipeline === 'function') {
      setTimeout(() => runResumenPipeline(), 350);
    }
  }
}

// ──────────────────────────── COLAPSO + RESTAURACIÓN ─────────────────────────
function switchToGlobeApp() {
  const stage = document.getElementById('globe-stage');
  if (!stage) return;
  if (GLOBE.appOn) return;
  stage.classList.add('loader-done');
  // Pequeño retraso para que la transición del loader luzca
  setTimeout(() => {
    stage.classList.add('app-on');
    GLOBE.appOn = true;
  }, 300);
}

function collapseGlobeStage() {
  const stage = document.getElementById('globe-stage');
  if (!stage) return;
  GLOBE.collapsed = true;
  GLOBE.collapsedOnce = true;
  GLOBE.autoSpin = false;
  GLOBE.isDragging = false;
  GLOBE.appOn = false;
  if (GLOBE.flyAF) { cancelAnimationFrame(GLOBE.flyAF); GLOBE.flyAF = null; }
  stage.classList.add('is-collapsed');
  document.body.classList.add('globe-collapsed');
  // Tras la transición, sacar del paint para que NO bloquee mouse / scroll
  setTimeout(() => {
    if (GLOBE.collapsed) stage.classList.add('is-hidden');
  }, 700);
}

function bindGlobeBackButton() {
  document.getElementById('btn-back-to-globe')?.addEventListener('click', restoreGlobeStage);
}

function restoreGlobeStage() {
  const stage = document.getElementById('globe-stage');
  if (!stage) return;
  // Reset completo de la simulación previa antes de mostrar el globo
  resetSimulationState();
  GLOBE.collapsed = false;
  stage.classList.remove('is-hidden');
  void stage.offsetWidth;        // reflow para que la transición se vea
  stage.classList.remove('is-collapsed');
  document.body.classList.remove('globe-collapsed');
  GLOBE.appOn = true;
  GLOBE.autoSpin = !GLOBE.poi;
  if (GLOBE.mode === 'leaflet' && GLOBE.leafletMap) {
    setTimeout(() => GLOBE.leafletMap.invalidateSize(), 360);
  }
}

// Limpia los datos cargados, KPIs renderizados, charts y estado del Globe
// para que volver al Globe equivalga a un reinicio completo del análisis.
function resetSimulationState() {
  // 1) Estado del Globe (POI, estación maestra, panel)
  clearGlobePoi();
  GLOBE.master = null;
  document.getElementById('globe-search-input') && (document.getElementById('globe-search-input').value = '');

  // 2) Estado en memoria del análisis
  if (typeof WP !== 'undefined') {
    WP.windStats = null;
    WP.advancedResult = null;
    WP.meta = null;
  }

  // 3) Charts: destruir todos los charts del análisis para evitar overlay viejo
  if (typeof WP !== 'undefined' && WP.charts) {
    Object.keys(WP.charts).forEach(id => {
      if (id.startsWith('chart-') || id.startsWith('dem-') ||
          id.startsWith('pml-') || id.startsWith('fx-') || id.startsWith('int-')) {
        try { WP.charts[id]?.destroy?.(); } catch (e) {}
        WP.charts[id] = null;
      }
    });
  }

  // 4) Veredicto y header del Resumen
  const vBox  = document.getElementById('resumen-verdict');
  const vVal  = document.getElementById('resumen-verdict-value');
  const vSub  = document.getElementById('resumen-verdict-sub');
  const vCta  = document.getElementById('btn-go-to-arena');
  const title = document.getElementById('resumen-site-title');
  const sub   = document.getElementById('resumen-site-sub');
  vBox?.classList.remove('is-good', 'is-marginal', 'is-poor');
  if (vVal) vVal.textContent = '—';
  if (vSub) vSub.textContent = 'Calcula el análisis para ver la evaluación del recurso';
  vCta?.setAttribute('disabled', '');
  if (title) title.textContent = '— sin datos —';
  if (sub)   sub.textContent   = 'Selecciona una estación en el Globe para iniciar el análisis automático.';

  // 5) Reset visual de KPIs principales
  ['kv-mean','kv-max','kv-weibull','kv-pd','kv-ext','kv-near-mean','kv-temp','kv-pres','kv-nobs'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '—';
  });
  ['ks-mean','ks-max','ks-ext','ks-near-mean','ks-nobs'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '';
  });
  const ovStatus = document.getElementById('overview-status');
  if (ovStatus) ovStatus.textContent = 'Sin datos';
  const wStatus  = document.getElementById('wind-status');
  if (wStatus)  wStatus.textContent = 'Sin datos';
  const advRow = document.getElementById('adv-kpi-row');
  if (advRow) advRow.style.display = 'none';
  const advHubRow = document.getElementById('adv-hub-row');
  if (advHubRow) advHubRow.style.display = 'none';

  // 6) Reset pipeline overlay
  resetPipelineSteps?.();

  // 7) Sub-tab por defecto = Recurso
  switchResumenTab?.('recurso');

  setStatus?.('Globe reiniciado — selecciona una estación para nuevo análisis.');
}

// ──────────────────────────── MODO 2D LEAFLET ────────────────────────────────
function bindGlobeModeSwitch() {
  document.querySelectorAll('#globe-modeswitch .globe-mode__btn').forEach(b => {
    b.addEventListener('click', () => setGlobeMode(b.dataset.mode));
  });
  document.getElementById('globe-leaflet-layer')?.addEventListener('change', e => {
    leafletSetTile(e.target.value);
  });
}

function setGlobeMode(mode) {
  if (mode !== 'globe' && mode !== 'leaflet') return;
  if (GLOBE.mode === mode) return;
  GLOBE.mode = mode;
  const app = document.getElementById('globe-app');
  app?.classList.toggle('is-mode-leaflet', mode === 'leaflet');
  document.querySelectorAll('#globe-modeswitch .globe-mode__btn').forEach(b => {
    b.classList.toggle('is-active', b.dataset.mode === mode);
  });
  if (mode === 'leaflet') {
    initGlobeLeaflet();
    GLOBE.autoSpin = false;
  } else {
    GLOBE.autoSpin = !GLOBE.poi;
  }
}

function initGlobeLeaflet() {
  if (GLOBE.leafletMap) {
    setTimeout(() => GLOBE.leafletMap.invalidateSize(), 50);
    return;
  }
  const el = document.getElementById('globe-leaflet');
  if (!el || typeof L === 'undefined') return;
  const map = L.map(el, {
    zoomControl: true,
    attributionControl: true,
    minZoom: 2,
    worldCopyJump: true,
  }).setView([23.5, -102], 5);   // centrado en México
  GLOBE.leafletMap = map;
  const layerSel = document.getElementById('globe-leaflet-layer');
  leafletSetTile(layerSel?.value || 'dark');

  // Click → POI
  map.on('click', (e) => {
    setGlobePoi(e.latlng.lat, e.latlng.lng);
  });

  // Render del estado actual (si ya había POI/estaciones)
  if (GLOBE.poi) {
    leafletRenderPoi();
    leafletRenderStations();
    map.setView([GLOBE.poi.lat, GLOBE.poi.lon], 7);
  }
}

function leafletSetTile(key) {
  if (!GLOBE.leafletMap) return;
  const conf = GLOBE_TILE_URLS[key] || GLOBE_TILE_URLS.dark;
  if (GLOBE.leafletTile) {
    GLOBE.leafletMap.removeLayer(GLOBE.leafletTile);
    GLOBE.leafletTile = null;
  }
  const opts = { maxZoom: 19, attribution: conf.attr };
  if (conf.subdomains) opts.subdomains = conf.subdomains;
  GLOBE.leafletTile = L.tileLayer(conf.url, opts).addTo(GLOBE.leafletMap);
}

function _poiIconHtml() {
  // Pin grande con halo pulsante (gradient ámbar premium)
  return `
    <div class="globe-l-poi__pulse"></div>
    <svg viewBox="0 0 56 64" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="poiHead" cx="40%" cy="35%" r="65%">
          <stop offset="0%"  stop-color="#FFE0BF"/>
          <stop offset="55%" stop-color="#E5A86B"/>
          <stop offset="100%" stop-color="#7A4A1A"/>
        </radialGradient>
        <linearGradient id="poiTail" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stop-color="#B45309"/>
          <stop offset="100%" stop-color="#5A3210"/>
        </linearGradient>
      </defs>
      <path d="M28 60 L18 38 Q28 36 38 38 Z" fill="url(#poiTail)" stroke="#0A0E15" stroke-width="1"/>
      <circle cx="28" cy="24" r="14" fill="url(#poiHead)" stroke="#FFFFFF" stroke-width="2"/>
      <circle cx="28" cy="24" r="5"  fill="#0A0E15"/>
      <circle cx="24" cy="20" r="3"  fill="rgba(255,255,255,0.7)"/>
    </svg>`;
}

function _metStationIconHtml(isMaster, distKm) {
  const W = isMaster ? 44 : 30;
  const H = isMaster ? 58 : 42;
  const mastClr   = isMaster ? '#B45309' : '#475569';
  const sensorClr = isMaster ? '#F59E0B' : '#60A5FA';
  const topClr    = isMaster ? '#FFC890' : '#E2F0FF';
  const guyClr    = isMaster ? '#92400E' : '#2D3748';
  const lbl = (distKm != null)
    ? distKm.toFixed(0) + ' km' + (isMaster ? ' ★' : '')
    : (isMaster ? '★ MAESTRA' : '');
  const cx = W / 2, topY = H * 0.09, botY = H - 7;
  return `
    <svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" overflow="visible">
      <ellipse cx="${cx}" cy="${botY+4}" rx="${W*0.28}" ry="${W*0.07}" fill="rgba(0,0,0,0.45)"/>
      <rect x="${cx-6}" y="${botY-2}" width="12" height="5" rx="2" fill="${mastClr}" opacity="0.9"/>
      <line x1="${cx}" y1="${topY+10}" x2="${cx-W*0.38}" y2="${botY}" stroke="${guyClr}" stroke-width="0.7" opacity="0.5" stroke-dasharray="3 2"/>
      <line x1="${cx}" y1="${topY+10}" x2="${cx+W*0.38}" y2="${botY}" stroke="${guyClr}" stroke-width="0.7" opacity="0.5" stroke-dasharray="3 2"/>
      <polygon points="${cx-2.2},${botY} ${cx+2.2},${botY} ${cx+0.7},${topY+4} ${cx-0.7},${topY+4}" fill="${mastClr}" stroke="rgba(255,255,255,0.12)" stroke-width="0.5"/>
      <line x1="${cx}" y1="${H*0.35}" x2="${cx+W*0.42}" y2="${H*0.35}" stroke="${sensorClr}" stroke-width="1.4"/>
      <circle cx="${cx+W*0.42}" cy="${H*0.35}" r="2.8" fill="${sensorClr}" stroke="#fff" stroke-width="0.8"/>
      <line x1="${cx}" y1="${H*0.56}" x2="${cx-W*0.35}" y2="${H*0.56}" stroke="#94A3B8" stroke-width="1.2"/>
      <rect x="${cx-W*0.35-3}" y="${H*0.56-3}" width="6" height="5" rx="1.2" fill="#94A3B8" stroke="#fff" stroke-width="0.7"/>
      <circle cx="${cx}" cy="${topY}" r="${isMaster ? 3.5 : 2.8}" fill="${topClr}" stroke="${sensorClr}" stroke-width="1.2"/>
      <circle cx="${cx}" cy="${topY-4.5}" r="1.8" fill="${sensorClr}"/>
      <circle cx="${cx+3.9}" cy="${topY+2.2}" r="1.8" fill="${sensorClr}"/>
      <circle cx="${cx-3.9}" cy="${topY+2.2}" r="1.8" fill="${sensorClr}"/>
    </svg>
    ${lbl ? '<div class="globe-l-station__label">' + lbl + '</div>' : ''}
  `;
}

function leafletRenderPoi() {
  if (!GLOBE.leafletMap) return;
  if (GLOBE.leafletPoi)    { GLOBE.leafletMap.removeLayer(GLOBE.leafletPoi); GLOBE.leafletPoi = null; }
  if (GLOBE.leafletCircle) { GLOBE.leafletMap.removeLayer(GLOBE.leafletCircle); GLOBE.leafletCircle = null; }
  if (!GLOBE.poi) return;
  const icon = L.divIcon({
    className: 'globe-l-poi',
    html: _poiIconHtml(),
    iconSize: [56, 64],
    iconAnchor: [28, 60],   // punta inferior del pin
  });
  GLOBE.leafletPoi = L.marker([GLOBE.poi.lat, GLOBE.poi.lon], { icon, zIndexOffset: 1000 })
    .addTo(GLOBE.leafletMap)
    .bindTooltip('Sitio del estudio', { direction: 'top', offset: [0, -56] });
  const km = Math.max(1, parseFloat(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM);
  GLOBE.leafletCircle = L.circle([GLOBE.poi.lat, GLOBE.poi.lon], {
    radius: km * 1000,
    color: '#E5A86B', weight: 1.8, dashArray: '8 5',
    fillColor: '#E5A86B', fillOpacity: 0.06, interactive: false,
  }).addTo(GLOBE.leafletMap);
}

function leafletRenderStations() {
  if (!GLOBE.leafletMap) return;
  GLOBE.leafletStations.forEach(m => GLOBE.leafletMap.removeLayer(m));
  GLOBE.leafletStations = [];
  GLOBE.visibleSites.forEach(s => {
    const isMaster = (GLOBE.master === s.filename);
    const W = isMaster ? 44 : 30;
    const H = isMaster ? 58 : 42;
    const icon = L.divIcon({
      className: `globe-l-station ${isMaster ? 'globe-l-station--master' : ''}`,
      html: _metStationIconHtml(isMaster, s._dist),
      iconSize: [W, H],
      iconAnchor: [W/2, H - 6],   // base de la torre cerca del lat/lon real
    });
    const m = L.marker([s.lat, s.lon], { icon, zIndexOffset: isMaster ? 700 : 250 })
      .addTo(GLOBE.leafletMap)
      .bindTooltip(`<b>${s.display_name || s.filename}</b><br>${s._dist.toFixed(1)} km · ${(s.source||'').toUpperCase()}`,
                   { direction: 'top', offset: [0, -H + 4] });
    m.on('click', () => handleGlobeStationPick(s.filename));
    GLOBE.leafletStations.push(m);
  });
}

// Vuela el mapa Leaflet a un cuadro de ~km × km alrededor del punto (auto-zoom)
function leafletFlyToRadius(lat, lon, km = 100) {
  if (!GLOBE.leafletMap) return;
  const dLat = km / 111.32;
  const dLon = km / (111.32 * Math.max(0.05, Math.cos(lat * Math.PI / 180)));
  const sw = [lat - dLat, lon - dLon];
  const ne = [lat + dLat, lon + dLon];
  try {
    GLOBE.leafletMap.flyToBounds([sw, ne], { duration: 0.9, padding: [50, 50] });
  } catch (e) {
    GLOBE.leafletMap.setView([lat, lon], 8);
  }
}

// Cambia a modo 2D Leaflet y centra a ~km del punto. Si el map no existe,
// lo inicializa y aplica el viaje en cuanto esté listo.
async function switchTo2DAt(lat, lon, km = 100) {
  setGlobeMode('leaflet');
  // Esperar a que invalidateSize del init tome efecto
  await new Promise(r => setTimeout(r, 80));
  if (!GLOBE.leafletMap) return;
  GLOBE.leafletMap.invalidateSize();
  leafletFlyToRadius(lat, lon, km);
}

function leafletClearAll() {
  if (!GLOBE.leafletMap) return;
  if (GLOBE.leafletPoi)    { GLOBE.leafletMap.removeLayer(GLOBE.leafletPoi); GLOBE.leafletPoi = null; }
  if (GLOBE.leafletCircle) { GLOBE.leafletMap.removeLayer(GLOBE.leafletCircle); GLOBE.leafletCircle = null; }
  GLOBE.leafletStations.forEach(m => GLOBE.leafletMap.removeLayer(m));
  GLOBE.leafletStations = [];
}

// ════════════════════════════════════════════════════════════════════════════
//  RESUMEN EÓLICO — módulo unificado (Recurso + Viento + Avanzado)
// ════════════════════════════════════════════════════════════════════════════
function bindResumenUnified() {
  // Sub-tabs
  document.querySelectorAll('.resumen-tab').forEach(tab => {
    tab.addEventListener('click', () => switchResumenTab(tab.dataset.rtab));
  });
  // Re-calcular
  document.getElementById('btn-resumen-recalc')?.addEventListener('click', () => {
    if (!WP.sessionId) { toast('No hay sesión activa', 'warning'); return; }
    runResumenPipeline();
  });
  // CTA → Centro de modelaje
  document.getElementById('btn-go-to-arena')?.addEventListener('click', () => {
    const arenaBtn = document.querySelector('.nav-btn[data-module="models"]');
    arenaBtn?.click();
  });
  // Mover cada selector de archivo del panel de mercado a su sub-tab
  // correspondiente dentro del Centro de modelaje, así no necesitamos una tab
  // "Datos" extra ni el panel agregado. Además creamos preview + stats
  // por sub-tab para que el resultado de cargar el archivo se vea.
  const movements = [
    ['#market-demand-select',   'arena-demand-tab',   'demand'],
    ['#market-pml-select',      'arena-pml-tab',      'pml'],
    ['#market-interest-select', 'arena-interest-tab', 'interest'],
    ['#market-fx-select',       'arena-fx-tab',       'fx'],
  ];
  movements.forEach(([sel, tabId, cat]) => {
    const select = document.querySelector(sel);
    const tab = document.getElementById(tabId);
    if (!select || !tab) return;
    const row = select.closest('.market-data-row');
    if (!row || row.dataset.movedToArena === '1') return;
    row.dataset.movedToArena = '1';
    tab.insertBefore(row, tab.firstChild);

    // Insertar tarjeta de Vista Previa + Estadísticos para esta categoría
    if (!document.getElementById(`market-${cat}-preview-card`)) {
      const card = document.createElement('div');
      card.id = `market-${cat}-preview-card`;
      card.className = 'grid-2 mt-2';
      card.innerHTML = `
        <div class="card">
          <div class="card-header"><span class="card-title">Vista Previa — ${cat.toUpperCase()}</span></div>
          <div class="card-body" style="height:200px;">
            <canvas id="market-${cat}-preview-chart"></canvas>
          </div>
        </div>
        <div class="card">
          <div class="card-header"><span class="card-title">Estadísticos del histórico observado</span></div>
          <div class="card-body">
            <div id="market-${cat}-stats-grid" class="meta-grid"></div>
          </div>
        </div>
      `;
      // Insertar justo después del row de carga
      row.insertAdjacentElement('afterend', card);
    }
  });
  // Si quedó arena-data-tab residual de un bind previo, lo retiramos.
  document.getElementById('arena-data-tab')?.remove();
  document.querySelector('#mod-models .tab-btn[data-tab="arena-data"]')?.remove();

  // Al entrar a Diseño y optimización: encuadre ~35 km al sitio del Globe +
  // estación maestra fija + parámetros desde Centro de modelaje. Solo se aplica la
  // primera vez por POI; entradas posteriores conservan el estado del usuario.
  document.querySelector('.nav-btn[data-module="kriging"]')?.addEventListener('click', () => {
    setTimeout(() => krig3AutoFitFromGlobe(), 80);
  });
}

// Encuadra el mapa Kriging a ~35 km del POI del Globe, fija la estación
// maestra como principal y aplica el radio por defecto. SOLO corre el setup
// completo la primera vez por cada POI del Globe (cuando el usuario cambia el
// punto en Globe). En entradas posteriores al módulo, conserva el estado
// (polígono del parque, selección, vista del mapa, parámetros editados, etc.)
// hasta que el usuario reinicie el diseño manualmente.
async function krig3AutoFitFromGlobe() {
  if (typeof krig3InitMap !== 'function') return;
  const map = krig3InitMap();
  if (!map) return;

  // Sin POI del Globe: nada que aplicar; conservar estado existente.
  if (!GLOBE || !GLOBE.poi) {
    if (map) setTimeout(() => map.invalidateSize(), 60);
    return;
  }

  // Identificador del POI del Globe actualmente aplicado al Kriging.
  const poiKey = `${GLOBE.poi.lat.toFixed(5)},${GLOBE.poi.lon.toFixed(5)}|${GLOBE.master || ''}`;
  if (KRIG._appliedGlobePoi === poiKey) {
    // Mismo POI ya aplicado: solo refrescamos el tamaño del mapa, sin tocar
    // estado ni encuadre. El usuario mantiene su vista/edición.
    setTimeout(() => map.invalidateSize(), 60);
    return;
  }

  // Pull parámetros económicos del cache de Centro de modelaje (solo en primera aplicación
  // de este POI; preserva ediciones manuales posteriores del usuario).
  krig3PullParamsFromArena();

  // Asegura que los sitios estén cargados
  if (typeof KRIG !== 'undefined' && KRIG && (!KRIG.sites?.length) && typeof krig3LoadSites === 'function') {
    try { await krig3LoadSites(); } catch (e) {}
  }

  // Marcar POI usando la lógica existente de Kriging (solo primera vez).
  if (typeof krig3PlacePoi === 'function') {
    krig3PlacePoi({ lat: GLOBE.poi.lat, lng: GLOBE.poi.lon });
  } else if (typeof KRIG !== 'undefined') {
    KRIG.poi = { lat: GLOBE.poi.lat, lon: GLOBE.poi.lon };
  }

  // Estación maestra del Globe → estación principal del Kriging
  if (GLOBE.master && typeof KRIG !== 'undefined') {
    const exists = KRIG.sites?.some(s => s.filename === GLOBE.master);
    if (exists) {
      KRIG.principal = GLOBE.master;
      try { krig3UpdateAllMarkers?.(); } catch (e) {}
    }
  }

  // Encuadre inicial a ~35 km del punto (medio-lado del cuadro).
  krig3FlyToHalfExtentKm(GLOBE.poi.lat, GLOBE.poi.lon, 35, 0.7);

  KRIG._appliedGlobePoi = poiKey;
}

// Llena los inputs de parámetros del diseño desde el cache de Centro de modelaje
function krig3PullParamsFromArena() {
  if (typeof WP === 'undefined' || !WP.arenaCache) return;
  const cache = WP.arenaCache;
  const _avg = arr => (arr && arr.length) ? arr.reduce((s,v)=>s+v,0) / arr.length : null;

  // Tasa de interés (%) — promedio del histórico observado
  const tasa = _avg(cache.interest?.raw_values);
  if (tasa != null) {
    const inp = document.getElementById('krig3-tasa');
    if (inp) inp.value = tasa.toFixed(2);
  }

  // USD/MXN — promedio del histórico observado
  const fxAvg = _avg(cache.fx?.raw_values);
  if (fxAvg != null) {
    const inp = document.getElementById('krig3-fx');
    if (inp) inp.value = fxAvg.toFixed(2);
  }

  // Precio (USD/kWh) — promedio del PML observado, convertido si está en $/MWh MXN
  const pmlSide = cache.pml?.compra || cache.pml?.venta || cache.pml;
  const pmlAvg = _avg(pmlSide?.raw_values);
  if (pmlAvg != null) {
    let usdKwh;
    if (pmlAvg > 50) {                     // típicamente MXN/MWh
      const fx = parseFloat(document.getElementById('krig3-fx')?.value) || fxAvg || 20;
      usdKwh = (pmlAvg / 1000) / fx;
    } else if (pmlAvg > 0.5) {             // MXN/kWh
      const fx = parseFloat(document.getElementById('krig3-fx')?.value) || fxAvg || 20;
      usdKwh = pmlAvg / fx;
    } else {                                // USD/kWh ya
      usdKwh = pmlAvg;
    }
    const inp = document.getElementById('krig3-price');
    if (inp && Number.isFinite(usdKwh)) inp.value = usdKwh.toFixed(4);
  }

  // Demanda anual (MWh) — suma del histórico horario observado
  const demSer = cache.demand_hourly?.raw_values || cache.demand?.raw_values;
  if (demSer && demSer.length) {
    const annualMWh = demSer.reduce((s,v)=>s+v, 0); // MW × 1h = MWh
    const inp = document.getElementById('krig3-demand');
    if (inp) inp.value = Math.round(annualMWh).toString();
  }
}

function switchResumenTab(name) {
  document.querySelectorAll('.resumen-tab').forEach(t => {
    t.classList.toggle('is-active', t.dataset.rtab === name);
  });
  document.querySelectorAll('.resumen-section').forEach(s => {
    s.classList.toggle('is-active', s.dataset.rsect === name);
  });
}

// Pipeline automático: Resumen → Viento → Avanzado
async function runResumenPipeline() {
  const overlay = document.getElementById('resumen-pipeline');
  if (!overlay) return;
  overlay.classList.add('is-on');
  resetPipelineSteps();

  const setStep = (step, state) => {
    const el = overlay.querySelector(`[data-step="${step}"]`);
    if (!el) return;
    el.classList.remove('is-pending', 'is-running', 'is-done', 'is-error');
    el.classList.add(`is-${state}`);
  };

  // 1) Resumen
  try {
    setStep('overview', 'running');
    await calcOverviewStats();
    setStep('overview', 'done');
  } catch (e) { setStep('overview', 'error'); }

  // 2) Viento
  try {
    setStep('wind', 'running');
    await calcWindAnalysis();
    setStep('wind', 'done');
  } catch (e) { setStep('wind', 'error'); }

  // 3) Avanzado
  try {
    setStep('advanced', 'running');
    await runAdvanced();
    setStep('advanced', 'done');
  } catch (e) { setStep('advanced', 'error'); }

  // Cerrar overlay y mostrar veredicto
  await new Promise(r => setTimeout(r, 600));
  overlay.classList.remove('is-on');
  updateResumenHeader();
  updateResumenVerdict();
  // Asegurar que el módulo Resumen esté visible
  const btn = document.querySelector('.nav-btn[data-module="resumen"]');
  if (btn && !btn.classList.contains('active')) btn.click();
  switchResumenTab('recurso');
}

function resetPipelineSteps() {
  document.querySelectorAll('.resumen-pipeline__step').forEach(el => {
    el.classList.remove('is-running', 'is-done', 'is-error');
    el.classList.add('is-pending');
  });
}

function updateResumenHeader() {
  const meta = WP.meta || {};
  const title = document.getElementById('resumen-site-title');
  const sub   = document.getElementById('resumen-site-sub');
  if (!title) return;
  const name = meta.display_name || meta.filename || meta.site_name || '— sin datos —';
  title.textContent = name;
  if (sub) {
    const parts = [];
    if (meta.reanalysis_type || meta.format) parts.push((meta.reanalysis_type || meta.format).toUpperCase());
    if (meta.latitude != null && meta.longitude != null)
      parts.push(`${(+meta.latitude).toFixed(3)}°, ${(+meta.longitude).toFixed(3)}°`);
    if (meta.wind_height_m) parts.push(`z = ${meta.wind_height_m} m`);
    sub.textContent = parts.length ? parts.join(' · ') : 'Datos cargados.';
  }
}

function updateResumenVerdict() {
  const ov = WP.windStats?.overview;
  const vBox  = document.getElementById('resumen-verdict');
  const vVal  = document.getElementById('resumen-verdict-value');
  const vSub  = document.getElementById('resumen-verdict-sub');
  const vCta  = document.getElementById('btn-go-to-arena');
  if (!vBox || !vVal) return;
  if (!ov) {
    vVal.textContent = '—';
    if (vSub) vSub.textContent = 'Calcula el análisis para ver la evaluación del recurso';
    vCta?.setAttribute('disabled', '');
    return;
  }
  const mean = parseFloat(ov.mean) || 0;
  const pd   = parseFloat(ov.power_density_w_m2) || 0;
  // Clasificación basada en NREL/IEC simplificada
  // Bueno: V̄ ≥ 6.4 m/s y PD ≥ 250 W/m²
  // Marginal: V̄ ≥ 5.0 m/s ó PD ≥ 150 W/m²
  // Pobre: por debajo
  let label, sub, cls;
  if (mean >= 6.4 && pd >= 250) {
    cls = 'is-good'; label = 'BUENO';
    sub = `V̄ = ${mean.toFixed(2)} m/s · DP = ${pd.toFixed(0)} W/m². Recurso apto para parque eólico utility-scale.`;
  } else if (mean >= 5.0 || pd >= 150) {
    cls = 'is-marginal'; label = 'MARGINAL';
    sub = `V̄ = ${mean.toFixed(2)} m/s · DP = ${pd.toFixed(0)} W/m². Considera turbinas de baja velocidad (Clase III) o sitio de respaldo.`;
  } else {
    cls = 'is-poor'; label = 'POBRE';
    sub = `V̄ = ${mean.toFixed(2)} m/s · DP = ${pd.toFixed(0)} W/m². No recomendable para generación eólica utility-scale.`;
  }
  vBox.classList.remove('is-good', 'is-marginal', 'is-poor');
  vBox.classList.add(cls);
  vVal.textContent = label;
  if (vSub) vSub.textContent = sub;
  vCta?.removeAttribute('disabled');
}

/* ═══════════════════════════════════════════════════
   AERO OPEN CIM — Frontend application logic
═══════════════════════════════════════════════════ */

'use strict';

// ─── GLOBAL STATE ────────────────────────────────────────────────────────────
const WP = {
  sessionId: null,
  charts: {},
  windStats:    null,
  technoResult: null,
  energyResult: null,
  technoActiveScenario: 'integrated',
  technoScenarios: {
    integrated: { inputs: null, result: null, leveraged: null },
  },
  technoInputsInitialized: false,
  technoInputsDirty: false,
  technoSelectedTopsisTurbine: null,  // turbina elegida manualmente entre las alternativas ya rankeadas por TOPSIS (null = usar la #1)
  levParams: { debt_ratio_pct: 70, interest_rate_pct: 8, loan_years: 15, equity_discount_rate_pct: 12 },
  technoFinalLoss: { enabled: false, pct: 3.0 },  // Pérdidas eléctricas aplicadas al AEP final (post-simulación)
  referenceHeight: null,   // altura de referencia global (m) — set por modal al cargar estación
  referenceMethod: 'hellmann', // método de extrapolación seleccionado
  mcpStudy: null,
  reanalysisStudy: null,
  studyMode: null,
  mcpRunId: null,
  activeDataSource: 'observed',
  parkConfiguration: null,
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
Chart.register({
  id: 'chartAreaBorder',
  beforeDraw(chart, args, opts) {
    if (opts?.display === false) return;
    const { ctx, chartArea } = chart;
    if (!chartArea) return;
    ctx.save();
    ctx.strokeStyle = opts?.color || chartAxisColor();
    ctx.lineWidth = opts?.width || 2;
    ctx.strokeRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, chartArea.bottom - chartArea.top);
    ctx.restore();
  }
});
Chart.defaults.plugins.chartAreaBorder = { width: 2 };

function gridColor() {
  return document.body.classList.contains('light') ? 'rgba(11,18,32,0.16)' : 'rgba(76,96,132,0.55)';
}
function chartAxisColor() {
  return '#000000';
}
function chartGridOptions(display = true) {
  return { display, color: gridColor(), lineWidth: display ? 1 : 0 };
}
function chartBorderOptions() {
  return { display: true, color: chartAxisColor(), width: 2 };
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
  Chart.defaults.plugins.chartAreaBorder.color   = chartAxisColor();
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
  bindProjectFiles();
  bindNavigation();
  bindThemeToggle();
  bindDataHub();
  bindParkConfiguration();
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
  bindMCPLab();
  await loadPresetFiles();   // populate preset file dropdown
  await loadMarketFiles();   // populate market data selectors
  await loadParkConfiguration({ quiet: true });
  await loadTurbineList();   // populate turbine dropdown from aerogeneradores
  bindGlobeStage();          // pantalla inicial: globo terráqueo + selección de sitio
  setStatus('Plataforma iniciada — define el área en Globe; MCP sólo se usa cuando existe una estación real.');
  const pending = sessionStorage.getItem('aero-project-session');
  if (pending) {
    sessionStorage.removeItem('aero-project-session');
    try {
      const restored = await api(`/api/projects/restored/${encodeURIComponent(pending)}`);
      WP.importedProject = true;
      await applyProjectClientState(restored.client_state || {});
      setStatus(`Proyecto restaurado: ${restored.project_name}`);
      toast(`Proyecto “${restored.project_name}” cargado con sus resultados guardados.`, 'success');
    } catch (error) { toast(error.message, 'error'); }
  }

});

// ─── SESSION ──────────────────────────────────────────────────────────────────
async function initSession() {
  try {
    const pending = sessionStorage.getItem('aero-project-session');
    WP.importedProject = !!pending;
    const r = pending ? { session_id: pending } : await api('/api/session/new', 'POST', {});
    WP.sessionId = r.session_id;
    document.getElementById('session-label').textContent = `Sesión ${r.session_id}`;
    document.getElementById('session-dot').classList.add('active');
  } catch (e) {
    document.getElementById('session-label').textContent = 'Sin conexión';
    toast('No se pudo conectar con el servidor. ¿Está running run.py?', 'error');
  }
}

function projectJsonClone(value) {
  if (value == null) return value;
  try { return JSON.parse(JSON.stringify(value)); }
  catch (_) { return null; }
}

function mcdaUsesObsoleteReweightedBase(weights) {
  if (!weights || typeof weights !== 'object') return false;
  const obsolete = {
    cp_at_site_mean: 0.08, cpmax_speed_mismatch_ms: 0.07,
    hours_near_cpmax: 0.10, van_usd: 0.13, lcoe_usd_kwh: 0.09,
    capex_usd: 0.04, opex_lifetime_usd: 0.03, aep_p90_mwh: 0.12,
    wake_loss_pct: 0.07, factor_capacidad: 0.06,
    iec_suitability_score: 0.06, aep_uncertainty_pct: 0.04,
    curtailment_loss_pct: 0.02, energy_density_mwh_km2: 0.055,
    collection_length_m: 0.02, civil_complexity_index: 0.015,
  };
  return Object.entries(obsolete).every(([key, value]) =>
    Math.abs(Number(weights[key]) - value) < 1e-9
  );
}

function mcdaMergeLayoutWeightSchema(storedWeights, defaultWeights) {
  const stored = storedWeights && typeof storedWeights === 'object'
    ? { ...storedWeights }
    : {};
  if (!Object.prototype.hasOwnProperty.call(stored, 'aep_p90_mwh_per_installed_mw')
      && Object.prototype.hasOwnProperty.call(stored, 'aep_p90_mwh')) {
    stored.aep_p90_mwh_per_installed_mw = stored.aep_p90_mwh;
  }
  return Object.fromEntries(Object.entries(defaultWeights || {}).map(([key, fallback]) => {
    const saved = Number(stored[key]);
    return [key, Object.prototype.hasOwnProperty.call(stored, key) && Number.isFinite(saved)
      ? saved
      : Number(fallback)];
  }));
}

function collectProjectClientState() {
  const controls = {};
  document.querySelectorAll('input[id], select[id], textarea[id]').forEach(el => {
    if (el.type === 'file' || el.type === 'password') return;
    controls[el.id] = (el.type === 'checkbox' || el.type === 'radio')
      ? { checked: !!el.checked }
      : { value: el.value, ...(el.tagName === 'SELECT' ? { options: Array.from(el.options, o => ({ value: o.value, text: o.text, source: o.dataset.source })) } : {}) };
  });
  return {
    schema_version: 1,
    snapshot_version: 2,
    controls,
    view: { modules: Array.from(document.querySelectorAll('.module.active'), el => el.id),
      nav: document.querySelector('.nav-btn.active')?.dataset.module, collapsed: GLOBE.collapsed },
    wp: projectJsonClone({
      windStats: WP.windStats, technoResult: WP.technoResult,
      advancedResult: WP.advancedResult, hybridResult: WP.hybridResult,
      qcResult: WP.qcResult, mcpResult: WP.mcpResult,
      energySimulation: WP.energySimulation, marketResults: WP.marketResults,
      farmResult: WP.farmResult, farmLayout: WP.farmLayout, turbineComparison: WP.turbineComparison,
      technoInputsInitialized: WP.technoInputsInitialized, technoInputsDirty: WP.technoInputsDirty,
      energyResult: WP.energyResult, technoActiveScenario: WP.technoActiveScenario,
      technoScenarios: WP.technoScenarios, levParams: WP.levParams,
      technoFinalLoss: WP.technoFinalLoss, referenceHeight: WP.referenceHeight,
      referenceMethod: WP.referenceMethod, mcpStudy: WP.mcpStudy,
      resourcePeriod: WP.resourcePeriod, reanalysisStudy: WP.reanalysisStudy, singleStationStudy: WP.singleStationStudy, studyMode: WP.studyMode,
      mcpRunId: WP.mcpRunId, activeDataSource: WP.activeDataSource,
      parkConfiguration: WP.parkConfiguration, meta: WP.meta,
    }),
    krig: projectJsonClone({
      sites: KRIG.sites, selected: Array.from(KRIG.selected || []),
      guidedMcp: KRIG.guidedMcp, curveIdx: KRIG.curveIdx, areaBounds: KRIG.areaBounds,
      principal: KRIG.principal, result: KRIG.result, layerKey: KRIG.layerKey,
      mode: KRIG.mode, parkPoints: KRIG.parkPoints, optimal: KRIG.optimal,
      poi: KRIG.poi, calibrationId: KRIG.calibrationId,
      calibrationSignature: KRIG.calibrationSignature,
      calibration: KRIG.calibration,
      orographicSpeedupGrid: KRIG.orographicSpeedupGrid,
    }),
    mcda: projectJsonClone({
      lastAnalysis: MCDA.lastAnalysis, lastRanking: MCDA.lastRanking,
      lastSpatial: MCDA.lastSpatial, scope: MCDA.scope,
      // defaults NO se guarda: es un caché de /api/multicriteria/defaults
      // (servidor), no un dato del proyecto — guardarlo congelaba el esquema
      // de criterios vigente al momento de guardar dentro del archivo.
      turbines: MCDA.turbines,
      layoutWeights: MCDA.layoutWeights,
      weightSchema: MCDA.weightSchema,
      ahpDraft: MCDA.ahpDraft, ahpResult: MCDA.ahpResult,
      ahpApplied: MCDA.ahpApplied, weightSource: MCDA.weightSource,
    }),
    cluster: projectJsonClone({
      result: CLUSTER.result, k: CLUSTER.k,
      linkageMethod: CLUSTER.linkageMethod,
      appliedFilenames: CLUSTER.appliedFilenames,
    }),
    parque: projectJsonClone({ result: PARQUE.result, optResult: PARQUE.optResult,
      wakeProfile: PARQUE.wakeProfile, layerKey: PARQUE.layerKey }),
    mapa: projectJsonClone({ result: MAPA.result, sites: MAPA.sites, grid: MAPA.grid,
      var: MAPA.var, mode: MAPA.mode, areaBounds: MAPA.areaBounds, parkPoints: MAPA.parkPoints }),
    risk: projectJsonClone(_riskData),
    quickDesign: projectJsonClone({ station: QD.station, refHeight: QD.refHeight, results: QD.results, params: QD.params }),
    globe: projectJsonClone({
      poi: GLOBE.poi, master: GLOBE.master, sites: GLOBE.sites,
      observedStation: GLOBE.observedStation,
      mcpReferences: Array.from(GLOBE.mcpReferences || []),
      studyMode: GLOBE.studyMode, sourceChoice: GLOBE.sourceChoice, singleReference: GLOBE.singleReference,
      rotate: GLOBE.rotate, zoomScale: GLOBE.zoomScale, mode: GLOBE.mode,
    }),
  };
}

async function applyProjectClientState(state) {
  if (!state || Number(state.schema_version || 1) !== 1) throw new Error('Versión del estado visual no compatible.');
  Object.entries(state.controls || {}).forEach(([id, saved]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.tagName === 'SELECT' && Array.isArray(saved.options)) {
      el.replaceChildren(...saved.options.map(o => { const option = new Option(o.text, o.value); if (o.source) option.dataset.source = o.source; return option; }));
    }
    if ('checked' in saved) el.checked = !!saved.checked;
    else if ('value' in saved) el.value = saved.value;
  });
  if (state.wp) {
    Object.assign(WP, state.wp);
    // Migración de proyectos guardados con los tres escenarios tecnoeconómicos
    // anteriores. Se conserva el resultado más completo dentro de la evaluación
    // integral única.
    const savedScenarios = state.wp.technoScenarios || {};
    const integrated = savedScenarios.integrated
      || savedScenarios.corrected || savedScenarios.ml || savedScenarios.real
      || { inputs: null, result: null, leveraged: null };
    WP.technoActiveScenario = 'integrated';
    WP.technoScenarios = { integrated };
    if (!integrated.result && WP.technoResult) integrated.result = WP.technoResult;
    krig3ActivatePublishedSource();
  }
  if (state.krig) {
    Object.assign(KRIG, state.krig);
    KRIG.selected = new Set(state.krig.selected || []);
    // areaBounds se guarda como el clon JSON plano de un L.LatLngBounds
    // ({_southWest, _northEast}); sin reconstruirlo como instancia real,
    // map.fitBounds()/.contains() lo rechazan ("Bounds are not valid") y
    // el rectángulo del área nunca se vuelve a dibujar tras importar.
    const savedBounds = state.krig.areaBounds;
    KRIG.areaBounds = (savedBounds && savedBounds._southWest && savedBounds._northEast)
      ? L.latLngBounds(
          [savedBounds._southWest.lat, savedBounds._southWest.lng],
          [savedBounds._northEast.lat, savedBounds._northEast.lng])
      : null;
    if (Array.isArray(state.krig.parkPoints)) {
      KRIG.parkPoints = state.krig.parkPoints;
      try { krig3InitMap(); krig3RenderParkLayer(true); } catch (_) {}
    }
    if (KRIG.areaBounds) {
      try { krig3InitMap(); krig3RenderAreaLayer(); } catch (_) {}
    }
  }
  if (state.mcda) {
    const legacyBaseWeights = state.mcda.scenarioWeights?.base || null;
    Object.assign(MCDA, {ahpDraft:null, ahpResult:null, ahpApplied:null, weightSource:'reference'}, state.mcda);
    MCDA.weightSchema = state.mcda.weightSchema || null;
    // MCDA.defaults es un caché de /api/multicriteria/defaults, no un dato
    // del proyecto: si el `state.mcda` restaurado trae un `defaults` viejo
    // (proyecto guardado antes de agregar/quitar un criterio), quedaba fijo
    // el resto de la sesión porque mcdaLoadDefaults() sólo lo pide una vez
    // (`if (MCDA.defaults) return MCDA.defaults;`) — TOPSIS, los sliders de
    // pesos y la calculadora AHP seguían mostrando el esquema viejo hasta
    // iniciar un proyecto nuevo. Forzar un refetch real: mcdaBuildWeightSliders()
    // más abajo ya llama a mcdaLoadDefaults() y, de paso, reconcilia
    // MCDA.layoutWeights contra el esquema fresco (mcdaMergeLayoutWeightSchema).
    MCDA.defaults = null;
    const storedLayoutWeights = state.mcda.layoutWeights || legacyBaseWeights;
    MCDA.layoutWeights = mcdaUsesObsoleteReweightedBase(storedLayoutWeights)
      ? null
      : storedLayoutWeights;
    if (MCDA.lastSpatial?.ranking_scenarios) {
      MCDA.lastSpatial.ranking = MCDA.lastSpatial.ranking_scenarios.base
        || MCDA.lastSpatial.ranking;
      delete MCDA.lastSpatial.ranking_scenarios;
      MCDA.lastRanking = MCDA.lastSpatial.ranking || MCDA.lastRanking;
    }
    delete MCDA.activeScenario;
    delete MCDA.scenarioWeights;
    delete MCDA.scenarioWeightsEdited;
    try { await mcdaBuildWeightSliders(); } catch (_) {}
  }
  if (state.cluster?.result) {
    Object.assign(CLUSTER, state.cluster);
    try {
      document.getElementById('cluster-status').textContent =
        `${CLUSTER.result.turbines.length} turbinas · ${CLUSTER.linkageMethod || 'complete'}`;
      const kInput = document.getElementById('cluster-k');
      if (kInput) kInput.value = CLUSTER.k || 3;
      renderClusterResult();
    } catch (_) {}
  }
  if (state.parque) Object.assign(PARQUE, state.parque);
  if (state.mapa) Object.assign(MAPA, state.mapa);
  _riskData = state.risk || null;
  Object.assign(QD, state.quickDesign || {}, { active: false });
  if (state.globe) {
    Object.assign(GLOBE, state.globe);
    GLOBE.areaKey = null;
    GLOBE.mcpReferences = new Set(state.globe.mcpReferences || []);
    requestAnimationFrame(() => {
      // Sincroniza también el DOM: un proyecto podía restaurar mode="leaflet"
      // mientras la pantalla seguía visualmente en el globo 3D.
      setGlobeMode(GLOBE.mode || 'globe');
      if (GLOBE.poi) globeFilterByRadius();
    });
  }
  // Evita que krig3AutoFitFromGlobe() trate la primera entrada a Diseño tras
  // importar como "POI nuevo": eso invalidaría (krig3PlacePoi) la calibración
  // MCP y el resultado de Kriging que este mismo estado acaba de restaurar.
  if (GLOBE.poi && KRIG.poi) {
    KRIG._appliedGlobePoi = `${Number(GLOBE.poi.lat).toFixed(5)},${Number(GLOBE.poi.lon).toFixed(5)}|${GLOBE.master || ''}`;
  }
  if (WP.referenceHeight != null) applyReferenceHeightToUI(WP.referenceHeight);
  try { await loadParkConfiguration({ quiet: true }); } catch (_) {}
  try { await loadMCPStudy(state.wp?.mcpStudy || null); } catch (_) {}
  if (MCDA.lastRanking) {
    try { mcdaRenderRanking(MCDA.lastRanking); } catch (_) {}
  }
  if (MCDA.lastSpatial) {
    try { mcdaRenderMiniMap(MCDA.lastSpatial); } catch (_) {}
  }
  Object.entries(state.controls || {}).forEach(([id, saved]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if ('checked' in saved) el.checked = !!saved.checked;
    else if ('value' in saved) el.value = saved.value;
  });
  if (state.view?.collapsed || (!state.view && WP.windStats)) collapseGlobeStage();
  if (state.view?.modules?.length) {
    document.querySelectorAll('.module').forEach(el => el.classList.toggle('active', state.view.modules.includes(el.id)));
    document.querySelectorAll('.nav-btn[data-module]').forEach(el => el.classList.toggle('active', el.dataset.module === state.view.nav));
  }
  await renderSavedProjectResults();
  if (!state.snapshot_version) toast('Proyecto anterior: se muestran los resultados disponibles; lo que no se guardó no puede recuperarse del archivo.', 'warning');
  updateExportSessionInfo();
}

async function exportProjectFile() {
  if (!WP.sessionId) { toast('No hay una sesión activa para guardar.', 'warning'); return; }
  const defaultName = `proyecto_aero_${new Date().toISOString().slice(0, 10)}`;
  const projectName = window.prompt('Nombre del proyecto', defaultName);
  if (projectName == null) return;
  try {
    toast('Empaquetando avances del proyecto…', 'info');
    const payload = JSON.stringify({
      session_id: WP.sessionId,
      project_name: projectName || defaultName,
      client_state: collectProjectClientState(),
    });
    let requestBody = payload;
    const requestHeaders = { 'Content-Type': 'application/json' };
    if (typeof CompressionStream !== 'undefined') {
      const compressedStream = new Blob([payload], { type: 'application/json' })
        .stream().pipeThrough(new CompressionStream('gzip'));
      requestBody = await new Response(compressedStream).blob();
      requestHeaders['Content-Type'] = 'application/octet-stream';
      requestHeaders['X-Aero-Content-Encoding'] = 'gzip';
    }
    const response = await fetch('/api/projects/export', {
      method: 'POST', headers: requestHeaders, body: requestBody,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error || `HTTP ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^";]+)/i);
    const filename = match ? decodeURIComponent(match[1].replace(/"/g, '')) : `${defaultName}.aeroproj`;
    downloadBlob(blob, filename);
    toast(`Proyecto guardado: ${filename}`, 'success');
  } catch (error) {
    let detail = error.message;
    if (error instanceof TypeError && /fetch/i.test(error.message)) {
      try {
        const health = await fetch('/health', { cache: 'no-store' });
        detail = health.ok
          ? 'La conexión se interrumpió al transferir el proyecto. Intenta guardarlo nuevamente.'
          : `El servidor respondió con HTTP ${health.status}.`;
      } catch (_) {
        detail = 'El servidor local no está respondiendo. Recarga la aplicación y vuelve a guardar.';
      }
    }
    toast(`No se pudo guardar el proyecto: ${detail}`, 'error');
  }
}

async function importProjectFile(file) {
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  try {
    toast('Validando y restaurando proyecto…', 'info');
    const restored = await apiFD('/api/projects/import', form);
    // Reinitialize the page so a previous project's charts and state cannot leak.
    sessionStorage.setItem('aero-project-session', restored.session_id);
    window.location.reload();
  } catch (error) {
    toast(`No se pudo abrir el proyecto: ${error.message}`, 'error');
  }
}

function bindProjectFiles() {
  const input = document.getElementById('project-file-input');
  const openPicker = () => input?.click();
  document.getElementById('btn-project-export')?.addEventListener('click', exportProjectFile);
  document.getElementById('btn-project-export-center')?.addEventListener('click', exportProjectFile);
  document.getElementById('btn-project-import')?.addEventListener('click', openPicker);
  document.getElementById('btn-project-import-center')?.addEventListener('click', openPicker);
  document.getElementById('btn-globe-import-project')?.addEventListener('click', openPicker);
  input?.addEventListener('change', async () => {
    const file = input.files?.[0];
    input.value = '';
    await importProjectFile(file);
  });
}

// ─── NAVIGATION ──────────────────────────────────────────────────────────────
function bindNavigation() {
  const moduleGroups = {
    kriging: ['kriging', 'parque'],
    parque: ['kriging', 'parque'],
    validation: ['validation', 'export'],
    export: ['validation', 'export']
  };
  const primaryModule = {
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
        ? 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}'
        : 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}';
      L.tileLayer(url, { maxZoom: 16,
        attribution: '© Esri · © OpenStreetMap contributors' }).addTo(KRIG.map);
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
    WP.meta = meta;
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
      session_id: WP.sessionId, data_source: WP.activeDataSource, target_col: targetCol
    });

    WP.qcResult = r;
    renderSavedQC(r);

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

function bindParkConfiguration() {
  const commercial = document.getElementById('park-commercial-mode');
  commercial?.addEventListener('change', updateParkCommercialControls);
  ['park-base-fx', 'park-base-interest'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', renderParkConfigurationReadiness);
  });
  document.getElementById('btn-save-park-configuration')?.addEventListener('click', () => {
    saveParkConfiguration();
  });
  document.getElementById('btn-park-continue-design')?.addEventListener('click', async () => {
    const saved = await saveParkConfiguration({ quiet: true });
    if (!saved?.ready_for_design) {
      toast('Completa el archivo de precios, tipo de cambio y tasa; en autoconsumo también la serie de demanda.', 'warn');
      return;
    }
    document.querySelector('.nav-btn[data-module="kriging"]')?.click();
  });
  updateParkCommercialControls();
  renderParkConfigurationReadiness();
}

function collectParkConfiguration() {
  return {
    commercialization_mode: document.getElementById('park-commercial-mode')?.value || 'total_sale',
    sale_scheme: document.getElementById('park-sale-scheme')?.value || 'spot_pml',
    pml_node: document.getElementById('park-pml-node')?.value?.trim() || '',
    project_horizon_years: parseInt(document.getElementById('park-project-horizon')?.value || '25'),
    base_fx_mxn_usd: Number.isFinite(parseFloat(document.getElementById('park-base-fx')?.value))
      ? parseFloat(document.getElementById('park-base-fx').value) : null,
    base_interest_rate_pct: Number.isFinite(parseFloat(document.getElementById('park-base-interest')?.value))
      ? parseFloat(document.getElementById('park-base-interest').value) : null,
    reporting_currency: document.getElementById('park-reporting-currency')?.value || 'USD',
  };
}

function applyParkConfiguration(config) {
  if (!config) return;
  WP.parkConfiguration = config;
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el && value != null) el.value = value;
  };
  set('park-commercial-mode', config.commercialization_mode);
  set('park-demand-source', config.demand_source || 'none');
  set('park-annual-demand', 0);
  set('park-sale-scheme', config.sale_scheme);
  set('park-pml-node', config.pml_node);
  set('park-project-horizon', config.project_horizon_years);
  set('park-base-fx', config.base_fx_mxn_usd);
  set('park-base-interest', config.base_interest_rate_pct);
  set('park-reporting-currency', config.reporting_currency);
  const revision = document.getElementById('park-config-revision');
  if (revision) revision.textContent = config.configuration_revision
    ? `Revisión ${config.configuration_revision}` : 'Configuración nueva';
  updateParkCommercialControls();
  renderParkConfigurationReadiness();
  if (!WP.technoInputsDirty && !WP.technoScenarios.integrated?.result) {
    requestAnimationFrame(() => technoPullProjectInputs({ force: true }));
  }
}

async function loadParkConfiguration({ quiet = false } = {}) {
  if (!WP.sessionId) return null;
  try {
    const config = await api(`/api/sessions/${WP.sessionId}/park-configuration`, 'GET');
    applyParkConfiguration(config);
    return config;
  } catch (error) {
    if (!quiet) toast(`No se pudo recuperar la configuración: ${error.message}`, 'error');
    return null;
  }
}

async function saveParkConfiguration({ quiet = false } = {}) {
  if (!WP.sessionId) return null;
  const payload = collectParkConfiguration();
  try {
    const config = await api(`/api/sessions/${WP.sessionId}/park-configuration`, 'PUT', payload);
    applyParkConfiguration(config);
    krig3PullProjectParams();
    if (!quiet) toast('Configuración del parque guardada', 'success');
    return config;
  } catch (error) {
    if (!quiet) toast(error.message, 'error');
    return null;
  }
}

function updateParkCommercialControls() {
  const mode = document.getElementById('park-commercial-mode')?.value || 'total_sale';
  const demandSource = document.getElementById('park-demand-source');
  const demandBlock = document.getElementById('park-demand-series-block');
  const note = document.getElementById('park-commercial-note');
  const totalSale = mode === 'total_sale';
  if (demandSource) demandSource.value = totalSale ? 'none' : 'market_series';
  if (demandBlock) {
    demandBlock.hidden = totalSale;
    demandBlock.style.display = totalSale ? 'none' : 'block';
  }
  if (note) {
    if (totalSale) {
      note.innerHTML = '<strong>Venta total.</strong> Toda la energía neta se liquida con el archivo de precios de compra y venta cargado.';
    } else if (mode === 'self_consumption') {
      note.innerHTML = '<strong>Autoconsumo.</strong> La serie histórica de demanda es obligatoria para comparar simultáneamente consumo y generación.';
    } else {
      note.innerHTML = '<strong>Autoconsumo con venta de excedentes.</strong> La generación atiende primero la demanda horaria y el excedente se valora como venta.';
    }
    note.classList.remove('hidden');
  }
  renderParkConfigurationReadiness();
}

function renderParkConfigurationReadiness() {
  const box = document.getElementById('park-market-readiness');
  const badge = document.getElementById('park-readiness-badge');
  const button = document.getElementById('btn-park-continue-design');
  const title = document.getElementById('park-config-status-title');
  const text = document.getElementById('park-config-status-text');
  if (!box) return;
  const storedConfig = WP.parkConfiguration || {};
  const selectedMode = document.getElementById('park-commercial-mode')?.value
    || storedConfig.commercialization_mode || 'total_sale';
  const inputFx = parseFloat(document.getElementById('park-base-fx')?.value);
  const inputInterest = parseFloat(document.getElementById('park-base-interest')?.value);
  const config = {
    ...storedConfig,
    commercialization_mode: selectedMode,
    base_fx_mxn_usd: Number.isFinite(inputFx) ? inputFx : storedConfig.base_fx_mxn_usd,
    base_interest_rate_pct: Number.isFinite(inputInterest)
      ? inputInterest : storedConfig.base_interest_rate_pct,
  };
  const models = config.market_models || {};
  const readiness = {
    ...(config.readiness || {}),
    demand: selectedMode === 'total_sale' || Boolean(config.demand_series),
    fx: Number.isFinite(inputFx) ? inputFx > 0 : config.readiness?.fx === true,
    interest: Number.isFinite(inputInterest) ? inputInterest >= 0 : config.readiness?.interest === true,
  };
  const labels = {
    demand: 'Demanda del proyecto',
    pml: 'PML de venta',
    fx: 'Tipo de cambio USD/MXN',
    interest: 'Tasa de interés',
  };
  const order = ['demand', 'pml', 'fx', 'interest'];
  box.innerHTML = order.map(category => {
    const ready = readiness[category] === true;
    const model = models[category] || {};
    const validation = model.validation || {};
    const fixedPml = category === 'pml' && model.mode === 'fixed_2018_profile';
    const metric = Number.isFinite(Number(validation.rmse))
      ? ` · RMSE val. ${Number(validation.rmse).toFixed(category === 'pml' ? 4 : 2)}` : '';
    let detail;
    if (category === 'demand') {
      detail = config.commercialization_mode === 'total_sale'
        ? 'No aplica · venta total'
        : ready
          ? `${config.demand_series?.filename || 'Serie histórica cargada'} · ${Number(config.demand_series?.n_rows || 0).toLocaleString('es-MX')} registros · ${Number(config.demand_series?.annual_mwh || 0).toLocaleString('es-MX', { maximumFractionDigits: 0 })} MWh/año`
          : 'Carga obligatoria de la serie histórica';
    } else if (category === 'fx') {
      detail = ready
        ? `${Number(config.base_fx_mxn_usd).toFixed(2)} MXN/USD · valor manual`
        : 'Captura el tipo de cambio base';
    } else if (category === 'interest') {
      detail = ready
        ? `${Number(config.base_interest_rate_pct).toFixed(2)} % anual · valor manual`
        : 'Captura la tasa de interés anual base';
    } else {
      detail = fixedPml && ready
      ? `Perfil 2018 fijo · ${Number(model.n_hours || 0).toLocaleString('es-MX')} h · sin escalamiento`
      : ready
        ? `${model.best_model || 'definida'} · ${model.years?.[0] || '—'}–${model.years?.at?.(-1) || '—'}${metric}`
        : 'Carga el archivo de precio de compra y venta';
    }
    const manual = category === 'fx' || category === 'interest';
    return `<div style="display:flex;justify-content:space-between;gap:12px;padding:.62rem 0;border-bottom:1px solid var(--border)">
      <div><strong>${labels[category]}</strong><div class="text-muted" style="font-size:.7rem">${detail}</div></div>
      <span class="cbadge ${ready ? 'g' : 'r'}">${ready ? (fixedPml ? 'FIJO 2018' : manual ? 'MANUAL' : 'LISTO') : 'PENDIENTE'}</span>
    </div>`;
  }).join('');
  const ready = config.ready_for_design === true;
  if (badge) { badge.textContent = ready ? 'Listo para diseño' : 'Variables pendientes'; badge.className = `badge ${ready ? 'badge--success' : 'badge--warn'}`; }
  if (button) button.disabled = !ready;
  if (title) title.textContent = ready ? 'Configuración lista para diseño' : 'Configuración incompleta';
  if (text) text.textContent = ready
    ? 'Diseño recibirá el esquema comercial y los parámetros guardados en esta revisión.'
    : 'Carga el archivo de precios y captura tipo de cambio y tasa; en autoconsumo también carga la demanda.';
}

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
      session_id: WP.sessionId, data_source: WP.activeDataSource,
      turbine: turbineCfg, simulation: simCfg
    });

    WP.energyResult = r;
    WP.energySimulation = simCfg;
    renderSavedEnergy(r, simCfg);

    toast('Simulación completada', 'success');
    setStatus(`CF: ${r.summary.capacity_factor_pct}% | FLH: ${Math.round(r.summary.full_load_hours)} h | AEP P50: ${r.aep?.aep_p50_mwh?.toLocaleString()} MWh`);
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    setStatus('Error en simulación energética.');
  }
}

/** Da estilo de "nube por frecuencia" a un scatter (ws, kW): agrupa los puntos
 * en celdas ws×potencia y pondera opacidad/radio por cuántas observaciones
 * cayeron en cada celda. La potencia sigue siendo f(ws) casi determinística
 * (no hay medición de potencia real, solo velocidad observada + curva del
 * fabricante), así que esto no inventa dispersión: solo hace visible dónde
 * se concentra realmente el régimen de viento del sitio a lo largo de la curva. */
function densityStyledScatter(pts, color) {
  if (!pts.length) return { backgroundColor: [], borderColor: [], radius: [] };
  const wsBin = 0.3;
  const pMax = Math.max(...pts.map(p => p.y), 1);
  const pBin = Math.max(pMax / 40, 1e-6);
  const counts = new Map();
  const keyOf = p => `${Math.round(p.x / wsBin)}|${Math.round(p.y / pBin)}`;
  pts.forEach(p => { const k = keyOf(p); counts.set(k, (counts.get(k) || 0) + 1); });
  const maxCount = Math.max(...counts.values());
  const alphaHex = a => Math.round(Math.min(1, Math.max(0, a)) * 255).toString(16).padStart(2, '0');
  const backgroundColor = [], borderColor = [], radius = [];
  pts.forEach(p => {
    const freq = counts.get(keyOf(p)) / maxCount;
    backgroundColor.push(color + alphaHex(0.08 + 0.72 * freq));
    borderColor.push(color + alphaHex(0.15 + 0.6 * freq));
    radius.push(1.3 + 2.6 * freq);
  });
  return { backgroundColor, borderColor, radius };
}

function renderPowerCurveChart(turbine, timeseries, nTurbines) {
  destroyChart('power-curve');
  if (!turbine) return;
  const ctx = document.getElementById('power-curve-chart').getContext('2d');
  const nominal = turbine.rated_power_kw;
  const curvePts = turbine.curve_ws.map((ws, i) => ({ x: ws, y: turbine.curve_pw[i] }));
  const maxCurveWs = turbine.curve_ws[turbine.curve_ws.length - 1] || 25;
  // timeseries.power_kw es la potencia del PARQUE (turbina × n_turbinas); hay
  // que llevarla a potencia por aerogenerador para compararla contra la curva.
  const perTurbine = Number(nTurbines) > 0 ? Number(nTurbines) : 1;
  const obsPts = [];
  const wsArr = timeseries?.ws_hub || [];
  const pArr  = timeseries?.power_kw || [];
  for (let i = 0; i < Math.min(wsArr.length, pArr.length); i++) {
    if (Number.isFinite(wsArr[i]) && Number.isFinite(pArr[i])) obsPts.push({ x: wsArr[i], y: pArr[i] / perTurbine });
  }
  const maxWs = obsPts.length ? Math.max(maxCurveWs, ...obsPts.map(p => p.x)) : maxCurveWs;
  const obsStyle = densityStyledScatter(obsPts, '#9F7AEA');
  WP.charts['power-curve'] = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        ...(obsPts.length ? [{
          type: 'scatter', label: 'Observaciones del sitio (densidad)',
          data: obsPts, order: 2,
          backgroundColor: obsStyle.backgroundColor, borderColor: obsStyle.borderColor,
          pointRadius: obsStyle.radius, pointHoverRadius: 5, showLine: false,
        }] : []),
        { label: turbine.name, data: curvePts, order: 0,
          borderColor: acc(), backgroundColor: acc()+'20',
          borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3 },
        ...(nominal ? [{ label: `Nominal ${nominal} kW`, order: 1,
          data: [{ x: 0, y: nominal }, { x: maxWs, y: nominal }],
          borderColor: gold()+'66', borderDash: [4,3], borderWidth: 1, pointRadius: 0, fill: false }] : [])
      ]
    },
    options: { responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{labels:{font:{size:9},boxWidth:10}} },
      scales:{
        x:{type:'linear', grid:{color:gridColor()}, title:{display:true,text:'Velocidad (m/s)',font:{size:9}}, ticks:{font:{size:9}}},
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
      data_source: WP.activeDataSource,
      ws_height_m: wsH(),
    });

    WP.turbineComparison = r;
    renderSavedTurbineComparison(r);

    toast('Comparación completada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
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

  WP.restoreFarmLayout = saved => { turbines = projectJsonClone(saved || []); drawLayout(); updateTurbinesTable(); };

  function drawLayout() {
    WP.farmLayout = projectJsonClone(turbines);
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
      data_source: WP.activeDataSource,
      layout: turbines,
      turbine: turbineCfg,
      ws_height_m: wsH(),
      availability: parseFloat(document.getElementById('availability').value) / 100 || 1.0,
      electrical_losses: parseFloat(document.getElementById('elec-losses').value) / 100 || 0.0,
    });

    WP.farmResult = r;
    renderSavedFarm(r);

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
  const windEpoch = WP.windSourceEpoch || 0;
  const spatialEpoch = KRIG.spatialEpoch;
  const spatialRequest = /^\/api\/(farm|multicriteria|kriging\/(status|legacy_maps|calibrations))/.test(url);
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body && method !== 'GET') opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  const json = await res.json();
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  if (windEpoch !== (WP.windSourceEpoch || 0)) throw new Error('La fuente cambió; se descartó la respuesta anterior.');
  if (spatialRequest && spatialEpoch !== KRIG.spatialEpoch) throw new Error('Las estaciones cambiaron; se descartó el cálculo espacial anterior.');
  return json;
}

async function apiFD(url, formData) {
  const res = await fetch(url, { method: 'POST', body: formData });
  let json;
  try { json = await res.json(); }
  catch {
    throw new Error(res.status === 413
      ? 'El archivo es demasiado grande para subirlo.'
      : `HTTP ${res.status}`);
  }
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
async function refreshResourcePeriod() {
  if (!WP.sessionId || !WP.activeDataSource) return;
  const element = document.getElementById('resource-year');
  if (!element || WP.applyingResourcePeriod) return;
  try {
    const selection = await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/resource-period`);
    WP.resourcePeriod = selection;
    element.innerHTML = '<option value="all">Todo el registro</option>' + selection.available_years.map(row =>
      `<option value="${row.year}">${row.year} · ${row.valid_rows.toLocaleString()} registros válidos</option>`).join('');
    element.value = selection.year == null ? 'all' : String(selection.year);
    element.disabled = false;
  } catch (_) { element.disabled = true; }
}

async function applyResourcePeriod() {
  const element = document.getElementById('resource-year');
  if (!element || WP.applyingResourcePeriod) return;
  WP.applyingResourcePeriod = true;
  element.disabled = true;
  try {
    const selection = await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/resource-period`, 'PUT', {
      source_id: WP.activeDataSource, year: element.value === 'all' ? null : Number(element.value),
    });
    invalidateProjectWindResults();
    WP.resourcePeriod = selection;
    WP.meta = {...(WP.meta || {}), date_range: [selection.period_start, selection.period_end]};
    KRIG.sites = []; KRIG.selected.clear(); KRIG.principal = null;
    KRIG._appliedGlobePoi = null;
    if (selection.year != null) {
      ['krig3-year', 'mcda-year', 'mapa-year'].forEach(id => {
        const input = document.getElementById(id);
        if (input) {
          if (![...input.options].some(option => option.value === String(selection.year))) {
            input.add(new Option(String(selection.year), String(selection.year)));
          }
          input.value = String(selection.year);
        }
      });
    }
    krig3ActivatePublishedSource();
    toast('Periodo actualizado. Recalcula Diseño y los resultados del parque con esta selección.', 'success');
    await runResumenPipeline();
  } catch (error) {
    toast(`No se pudo aplicar el periodo: ${error.message}`, 'error');
  } finally { WP.applyingResourcePeriod = false; await refreshResourcePeriod(); }
}

function bindOverview() {
  document.getElementById('resource-year')?.addEventListener('change', applyResourcePeriod);
  document.getElementById('resource-rose-sectors')?.addEventListener('change', () => renderWindRose(WP.windStats?.rose));
  document.getElementById('btn-calc-stats').addEventListener('click', calcOverviewStats);
}

async function calcOverviewStats() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  setStatus('Calculando estadísticas del sitio…');
  document.getElementById('overview-status').textContent = 'Calculando…';
  try {
    const r = await api('/api/wind/stats', 'POST', {
      session_id: WP.sessionId, data_source: WP.activeDataSource,
    });
    WP.windStats = r;
    renderSavedOverview(r);

    toast('Estadísticas calculadas', 'success');
    setStatus('Análisis de sitio completado.');
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    document.getElementById('overview-status').textContent = 'Error';
  }
}

function windSourcePeriodText(source) {
  if (!source?.period_start || !source?.period_end) return '';
  const clock = source.selection_timezone || (WP.resourcePeriod?.source_id === source.source_id ? WP.resourcePeriod.timezone : null) || 'UTC';
  const date = value => new Date(value).toLocaleDateString('sv-SE', {timeZone: clock});
  return `${date(source.period_start)} → ${date(source.period_end)} (${clock})`;
}

function measuredHeightTable(rows) {
  if (!rows?.length) return '';
  return `<details><summary>Mediciones por altura · consulta de sensores</summary>
    <p>Los cálculos usan la altura del estudio. Estas son las mediciones originales del periodo indicado.</p>
    <table class="table"><thead><tr><th>Altura</th><th>Serie</th><th>Media</th><th>A</th><th>B</th><th>Registros válidos</th></tr></thead>
    <tbody>${rows.map(row => `<tr><td>${Number(row.height_m)} m</td><td>${row.method === 'mean_AB' ? 'Promedio A/B' : 'Sensor único'}</td>
    <td>${row.mean_ms == null ? '—' : Number(row.mean_ms).toFixed(3)} m/s</td>
    <td>${row.sensor_means_ms?.A == null ? '—' : Number(row.sensor_means_ms.A).toFixed(3)}</td>
    <td>${row.sensor_means_ms?.B == null ? '—' : Number(row.sensor_means_ms.B).toFixed(3)}</td><td>${Number(row.valid_rows)}</td></tr>`).join('')}</tbody></table></details>`;
}

function renderOverviewKPIs(ov) {
  const heightBox = document.getElementById('resource-height-profile');
  if (heightBox) heightBox.innerHTML = measuredHeightTable(WP.windStats?.height_profile);
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
  const resMin = ov.time_resolution_min ?? WP.meta?.time_resolution_min;
  const resLabel = resMin != null
    ? (resMin < 60 ? `${resMin} min` : `${Math.round(resMin/60)}h`)
    : null;
  document.getElementById('ks-nobs').textContent =
    (windSourcePeriodText(WP.windStats?.data_source) || (ov.date_start ? `${ov.date_start} → ${ov.date_end} (UTC)` : '')) +
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
    const r = await api('/api/wind/stats', 'POST', {
      session_id: WP.sessionId, data_source: WP.activeDataSource,
    });
    WP.windStats = r;
    renderSavedWind(r);

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

function _drawWindRose(rose, canvasId = 'chart-rose', legendId = 'rose-legend') {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !rose?.bin_matrix) return;

  const matrix = rose.bin_matrix;             // [16][6] in % of total
  const nSectors = matrix.length;
  const nBins    = matrix[0].length;
  const binLabels = (rose.bin_labels || []).map((l, i, arr) =>
    i === arr.length - 1 ? `≥${l.split('-')[0]}` : l);

  // Legend
  const legendEl = document.getElementById(legendId);
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
  const directionLabels = rose.labels || Array.from({length: nSectors}, (_, i) => `${i * 360 / nSectors}°`);
  const labelStride = nSectors > 16 ? Math.max(1, Math.round(nSectors / 12)) : 1;
  for (let i = 0; i < nSectors; i++) {
    if (i % labelStride !== 0) continue;
    const ang = (i * sectorWidthDeg - 90) * Math.PI / 180;
    const x = cx + labelR * Math.cos(ang);
    const y = cy + labelR * Math.sin(ang);
    const bearing = i * sectorWidthDeg;
    const cardinal = Math.abs(bearing % 90) < 1e-9;
    ctx.font      = cardinal
      ? '700 11px "JetBrains Mono", ui-monospace, monospace'
      : '500 9px "JetBrains Mono", ui-monospace, monospace';
    ctx.fillStyle = cardinal ? txtBold : txtCol;
    ctx.fillText(directionLabels[i], x, y);
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
  if (document.getElementById('resource-rose-sectors')?.value === '36' && WP.windStats?.rose_36) rose = WP.windStats.rose_36;
  if (!rose) return;
  if (!document.getElementById('chart-rose')) return;
  const dominantIndex = (rose.frequency_pct || []).indexOf(Math.max(...(rose.frequency_pct || [0])));
  const dominantLabel = ROSE_DIR_LABELS[dominantIndex] || rose.dominant_direction;
  document.getElementById('rose-dom').textContent =
    `${rose.frequency_pct?.length === 16 ? dominantLabel : rose.dominant_direction} · ${rose.dominant_pct}%`;
  const legendEl = document.getElementById('rose-legend');
  if (legendEl) {
    const kind = WP.windStats?.data_source?.kind;
    const note = kind === 'reanalysis_area'
      ? 'Dirección circular combinada de los nodos ERA5/MERRA-2 del área · sin MCP.'
      : kind === 'mcp'
        ? 'La dirección observada se conserva y sólo los huecos usan MCP corregido.'
        : kind === 'reanalysis' ? 'Dirección de la referencia virtual seleccionada · sin MCP.'
        : 'Dirección registrada por la fuente observada activa.';
    legendEl.innerHTML = `<span style="opacity:.75">Frecuencia direccional de la fuente activa · ${note}</span>`;
  }
  renderDirectionalRoseChart('chart-rose', rose, 'Fuente activa', false);
}

function renderDirectionalRoseChart(id, rose, title, showLegend = true) {
  if (!rose?.frequency_pct) return;
  const palette = ['#3B82F6','#4A91E2','#52A6E8','#48C2A3','#1D9E75','#73B85B','#B6C94C','#F2C94C','#F5B14C','#F39C5A','#E8794F','#D9534F','#C14D76','#A855A1','#7C6BC4','#5B73C7'];
  destroyChart(id);
  const canvas = document.getElementById(id);
  if (!canvas) return;
  const labels = rose.frequency_pct.length === 16 ? ROSE_DIR_LABELS : (rose.labels || []);
  const colors = labels.map((_, index) => palette[index % palette.length]);
  // PolarArea angles start at sector boundaries, while meteorological labels
  // name their centres. Centre both the arc and label on N (0°), clockwise.
  const startAngleDeg = -180 / rose.frequency_pct.length;
  WP.charts[id] = new Chart(canvas, {
    type: 'polarArea',
    data: { labels, datasets: [{ label: title, data: rose.frequency_pct, backgroundColor: colors.map(color => color + 'b8'), borderColor: colors, borderWidth: 1 }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { display: showLegend, position: 'right', labels: { boxWidth: 10, font: { size: 9 } } },
        tooltip: { callbacks: { label: context => {
          const mean = rose.mean_speed?.[context.dataIndex];
          return `${context.label}: ${Number(context.raw).toFixed(2)}%${Number.isFinite(Number(mean)) ? ` · ${Number(mean).toFixed(2)} m/s` : ''}`;
        } } },
      },
      scales: { r: {
        beginAtZero: true,
        startAngle: startAngleDeg,
        grid: { color: gridColor() },
        pointLabels: { display: true, centerPointLabels: true, font: { size: labels.length > 16 ? 7 : 9 } },
        ticks: { backdropColor: 'transparent', font: { size: 8 } },
      } },
    },
  });
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
  document.getElementById('btn-te-pull-inputs')?.addEventListener('click', () => {
    technoPullProjectInputs({ force: true });
    toast('Parámetros actualizados desde las secciones anteriores.', 'success');
  });
  [
    'te-demand','te-price','te-price-escalation',
    'te-inflation','te-rate','te-years','te-fx','te-price-mode',
    'cm-turbine-usd-kw','cm-turbines-pct','cm-civil-pct',
    'cm-elec-pct','cm-install-pct','cm-eng-pct','cm-misc-pct','cm-opex-pct',
  ].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const eventName = el.tagName === 'SELECT' ? 'change' : 'input';
    el.addEventListener(eventName, () => { WP.technoInputsDirty = true; });
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
    if (!WP.technoInputsDirty && !technoCurrentScenario()?.result) {
      technoPullProjectInputs({ force: true });
    }
  });
  technoRefreshScenarioView();
  const levBtn = document.getElementById('btn-lev-from-scenario');
  if (levBtn) levBtn.addEventListener('click', () => showFinancingModal(WP.technoActiveScenario));
  document.getElementById('btn-clear-financing')?.addEventListener('click', clearLeveraged);
  document.getElementById('fin-modal-cancel')?.addEventListener('click', closeFinancingModal);
  document.getElementById('financing-modal-backdrop')?.addEventListener('click', closeFinancingModal);
  document.getElementById('btn-calc-sensitivity')?.addEventListener('click', calcSensitivity);
  document.getElementById('btn-gsens-run')?.addEventListener('click', runGlobalSensitivity);
  document.getElementById('gsens-metric')?.addEventListener('change', renderGlobalSensitivity);
  document.getElementById('btn-gsens-batch-run')?.addEventListener('click', runGlobalSensitivityBatch);
  document.getElementById('btn-topsis-robust-run')?.addEventListener('click', runTopsisRobustness);

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
  ['te-price-mode'].forEach(id => {
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
      requestAnimationFrame(() => {
        syncTopsisWinnerToModules();
        if (!WP.technoInputsDirty && !technoCurrentScenario()?.result) {
          technoPullProjectInputs({ force: true });
        }
        else technoRefreshScenarioView();
      });
    });
  });
}

async function onTechnoLossChange() {
  // Re-renderiza la evaluación con el análisis ajustado.
  renderTechnoScenarioResult();
  // Si existe financiamiento calculado, se recalcula con las cifras ajustadas.
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
  const N          = a.project_years || 25;
  const capexTotal = a.capex?.total_capex_usd || 0;
  const opex       = a.cashflow_table?.[0]?.opex ?? a.annual_opex_usd ?? 0;
  const baseRev    = (a.cashflow_table?.[0]?.revenue ?? a.annual_revenue_usd ?? 0) * factor;

  const adj = JSON.parse(JSON.stringify(a));
  adj.annual_energy_kwh   = Math.round((a.annual_energy_kwh || 0) * factor);
  adj.annual_energy_mwh   = Math.round((a.annual_energy_mwh || 0) * factor * 10) / 10;
  adj.annual_revenue_usd  = Math.round(baseRev);             // KPI = revenue año 1
  adj.annual_cashflow_usd = Math.round(baseRev - opex);
  adj.capacity_factor_pct = Math.round((a.capacity_factor_pct || 0) * factor * 100) / 100;

  // Preservar todos los flujos anuales, incluida la degradación y el costo final.
  // La reconstrucción solo aplica a resultados históricos sin tabla completa.
  const useExisting = Array.isArray(a.cashflow_table) && a.cashflow_table.length === N;
  let cum    = -capexTotal;
  let cumPv  = -capexTotal;
  let pbYears = null;
  adj.cashflow_table = [];
  let pvCosts = capexTotal, pvEnergy = 0, pvRevenue = 0;
  for (let yr = 1; yr <= N; yr++) {
    const baseRow = useExisting ? a.cashflow_table[yr - 1] : {};
    const terminal = Number(baseRow.decommissioning) ||
      (yr === N ? (Number(a.decommissioning_usd) || 0) : 0);
    const degradation = (Number(a.degradation_pct_per_year) || 0) / 100;
    const energyMwh = (baseRow.energy_mwh ??
      (a.annual_energy_kwh || 0) / 1000 * Math.pow(1 - degradation, yr - 1)) * factor;
    let cfT, revT;
    let opexT = opex * Math.pow(1 + infl, yr - 1);
    if (useExisting) {
      const baseOpexYr = Number.isFinite(Number(baseRow.opex)) ? Number(baseRow.opex) : opexT;
      const baseRevYr = Number(baseRow.revenue ?? ((Number(baseRow.cashflow) || 0) + baseOpexYr + terminal));
      revT = baseRevYr * factor * Math.pow(1 + esc, yr - 1);
      cfT = revT - opexT - terminal;
    } else {
      revT = baseRev * Math.pow((1 + esc) * (1 - degradation), yr - 1);
      cfT = revT - opexT - terminal;
    }
    const r = (a.discount_rate_pct || 0) / 100;
    const pvT = cfT / Math.pow(1 + r, yr);
    const prevCum = cum;
    cum   += cfT;
    cumPv += pvT;
    if (pbYears == null && cum >= 0 && prevCum < 0 && cfT > 0) {
      pbYears = Math.round((yr - 1 + (-prevCum) / cfT) * 10) / 10;
    }
    pvCosts += (opexT + terminal) / Math.pow(1 + r, yr);
    pvEnergy += energyMwh * 1000 / Math.pow(1 + r, yr);
    pvRevenue += revT / Math.pow(1 + r, yr);
    adj.cashflow_table.push({
      year: yr,
      energy_mwh:        energyMwh,
      price_usd_kwh:     (baseRow.price_usd_kwh ?? a.energy_price_usd_kwh) * Math.pow(1 + esc, yr - 1),
      discount_rate_pct: baseRow.discount_rate_pct,
      fx_mxn_per_usd:    baseRow.fx_mxn_per_usd,
      revenue:           revT,
      opex:              opexT,
      decommissioning:   terminal,
      cashflow:          cfT,
      pv_cashflow:       pvT,
      cumulative:        cum,
    });
  }
  adj.npv_usd       = Math.round(cumPv);
  adj.payback_years = pbYears;
  const rMean = (a.discount_rate_pct || 0) / 100;
  adj.vae_usd = rMean > 0
    ? Math.round(adj.npv_usd * rMean / (1 - Math.pow(1 + rMean, -N)))
    : Math.round(adj.npv_usd / N);

  // Preserve discounted, degraded energy and the terminal cost after UI adjustments.
  const totalOpexUsd = adj.cashflow_table.reduce((sum, row) => sum + row.opex, 0);
  adj.lcoe_usd_kwh = pvEnergy > 0 ? Math.round(pvCosts / pvEnergy * 10000) / 10000 : 0;
  adj.b_c_ratio = pvCosts > 0 ? Math.round(pvRevenue / pvCosts * 10000) / 10000 : null;
  adj.van_capex_ratio = capexTotal > 0 ? Math.round(cumPv / capexTotal * 10000) / 10000 : null;
  adj.irr_pct = _projectIRR(adj.cashflow_table, capexTotal);

  adj.is_viable = adj.npv_usd > 0;
  adj._final_loss_applied_pct = lossPct;
  adj._price_escalation_pct   = escPct;
  adj.annual_inflation_pct    = Math.round(inflPct * 1000) / 1000;
  adj.total_opex_project_usd  = Math.round(totalOpexUsd);
  return adj;
}

function technoCurrentScenario() {
  return WP.technoScenarios.integrated;
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
    price: parseFloat(document.getElementById('te-price')?.value),
    priceEscalation: parseFloat(document.getElementById('te-price-escalation')?.value) || 0,
    inflation: parseFloat(document.getElementById('te-inflation')?.value) || 0,
    rate: parseFloat(document.getElementById('te-rate')?.value),
    years: parseInt(document.getElementById('te-years')?.value),
    fx: parseFloat(document.getElementById('te-fx')?.value) || 1,
    priceMode: document.getElementById('te-price-mode')?.value || 'fixed',
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
  if (inputs.costModel) technoApplyCostModel(inputs.costModel);
  technoUpdateModeHints();
}

// ─── PML fijo 2018: serie anual y perfil horario 8760 ────────────────────────
function _arrMean(arr) {
  if (!Array.isArray(arr) || !arr.length) return null;
  const finite = arr.map(Number).filter(Number.isFinite);
  return finite.length ? finite.reduce((sum, value) => sum + value, 0) / finite.length : null;
}
function _fixedPmlProfile() {
  const pml = WP.parkConfiguration?.market_models?.pml;
  if (pml?.mode !== 'fixed_2018_profile' || !Array.isArray(pml.hourly_profile_2018)) return null;
  return { values: pml.hourly_profile_2018, unit: pml.unit || '' };
}
function _pmlToUsdKwh(val, fxScalar, unit = '') {
  const v = Number(val);
  if (!Number.isFinite(v)) return null;
  const fx = Number(fxScalar);
  const normalizedUnit = String(unit || '').toUpperCase().replace(/\s+/g, '');
  if (normalizedUnit.includes('USD/KWH')) return v;
  if (normalizedUnit.includes('USD/MWH')) return v / 1000;
  if (normalizedUnit.includes('MXN/KWH')) return fx > 0 ? v / fx : null;
  if (normalizedUnit.includes('MXN/MWH')) return fx > 0 ? v / fx / 1000 : null;
  if (v > 50) return fx > 0 ? v / fx / 1000 : null;
  if (v > 1) return v / 1000;
  return v;
}
function technoBuildPriceSeries(mode, nYears, fxScalar) {
  if (mode !== 'pml8760') return { series: null, baseline: null, hourly: null };
  const raw = _fixedPmlProfile();
  if (!raw) return { series: null, baseline: null, hourly: null };
  const hourly = raw.values.map(value => _pmlToUsdKwh(value, fxScalar, raw.unit));
  const baseline = _arrMean(hourly);
  if (!Number.isFinite(baseline)) return { series: null, baseline: null, hourly: null };
  const annual = WP.parkConfiguration?.market_models?.pml?.annual || [];
  const series = Array.from({ length: nYears }, (_, index) => {
    const value = Number(annual[Math.min(index, annual.length - 1)]);
    const converted = _pmlToUsdKwh(value, fxScalar, raw.unit);
    return Number.isFinite(converted) ? converted : baseline;
  });
  return { series, baseline: series[0] ?? baseline, hourly };
}
function technoUpdateModeHints() {
  const hint = document.getElementById('te-price-mode-hint');
  if (!hint) return;
  const mode = document.getElementById('te-price-mode')?.value || 'fixed';
  if (mode !== 'pml8760') { hint.textContent = ''; return; }
  const years = parseInt(document.getElementById('te-years')?.value);
  const fx = parseFloat(document.getElementById('te-fx')?.value);
  const built = technoBuildPriceSeries(mode, years, fx);
  hint.innerHTML = built.series
    ? `PML fijo 2018: <strong>${built.baseline.toLocaleString('es-MX', {maximumFractionDigits: 5})} USD/kWh</strong> · perfil horario replicado durante ${years} años.`
    : '<span style="color:#d97706">⚠ Carga y publica el PML 2018 en Configuración del parque.</span>';
}

/** El tecnoeconómico siempre se alimenta del layout aplicado en "Diseño y
 * optimización del parque" (PARQUE.optResult/PARQUE.result) — nunca de un
 * AEP recalculado sin estela. Si no hay layout aplicado, devuelve null y
 * el cálculo tecnoeconómico se bloquea en vez de inventar un número. */
function technoParkLayout() {
  // Alternativa elegida a mano entre las ya evaluadas por TOPSIS (todas con
  // estela real, calculadas en la misma corrida de "Diseño y optimización").
  // Se prioriza sobre el ganador #1 aplicado en Parque sin volver a simular nada.
  const overrideKey = WP.technoSelectedTopsisTurbine;
  if (overrideKey) {
    const ranking = (typeof MCDA !== 'undefined' && MCDA.lastRanking?.ranking) || [];
    const row = ranking.find(r => (r.turbina || r.label) === overrideKey && Number(r.n_aerogeneradores) > 0);
    const rowAep = Number(row?.aep_mwh ?? row?.aep_p50_mwh);
    if (row && Number.isFinite(rowAep) && rowAep > 0) {
      const meta = (MCDA.turbines || []).find(t => t.filename === overrideKey);
      const wake = Number(row.wake_loss_pct);
      return {
        turbine_key: overrideKey,
        turbine_name: meta?.name || overrideKey.replace('.csv', ''),
        n_placed: Math.round(Number(row.n_aerogeneradores)),
        aep_mwh: rowAep,
        wake_loss_pct: Number.isFinite(wake) ? wake : null,
      };
    }
  }
  const opt = PARQUE.optResult;
  const sim = PARQUE.result;
  const nPlaced = Number(opt?.n_placed ?? sim?.n_turbines);
  const aep = Number(sim?.aep_mwh);
  if (!opt || !sim || !(nPlaced > 0) || !Number.isFinite(aep) || aep <= 0) return null;
  const wakeFallback = Number(sim?.wake_loss_pct ?? opt?.wake_loss_pct);
  return {
    turbine_key: opt.turbine_key || null,
    turbine_name: opt.turbine?.name || sim.turbine?.name || null,
    n_placed: nPlaced,
    aep_mwh: aep,
    wake_loss_pct: Number.isFinite(wakeFallback) ? wakeFallback : null,
  };
}

function technoIntegratedDefaults() {
  const base = technoCollectInputs();
  const projectYears = Number(WP.parkConfiguration?.project_horizon_years);
  if (Number.isFinite(projectYears)) base.years = projectYears;
  const marketInputs = parkEquivalentDesignInputs();
  base.demand = Math.round(marketInputs.demand_mwh || 0);
  if (marketInputs.price_usd_kwh != null) base.price = Number(marketInputs.price_usd_kwh).toFixed(4);
  if (WP.parkConfiguration?.base_interest_rate_pct != null) base.rate = Number(WP.parkConfiguration.base_interest_rate_pct).toFixed(2);
  if (WP.parkConfiguration?.base_fx_mxn_usd != null) base.fx = Number(WP.parkConfiguration.base_fx_mxn_usd).toFixed(2);
  if (parkMarketTrajectory('pml')?.length) base.priceMode = 'pml8760';
  const layout = technoParkLayout();
  if (layout?.turbine_key) base.turbine = layout.turbine_key;
  if (layout?.n_placed > 0) base.nTurbines = layout.n_placed;
  return base;
}

function technoPullProjectInputs({ force = false } = {}) {
  if (WP.technoInputsInitialized && !force) return;
  technoApplyInputs(technoIntegratedDefaults());
  WP.technoInputsInitialized = true;
  WP.technoInputsDirty = false;
  WP.technoScenarios.integrated.inputs = technoCollectInputs();
  technoUpdateModeHints();
  technoRefreshScenarioView();
}

function technoRefreshScenarioView() {
  const badge = document.getElementById('te-scenario-badge');
  const status = document.getElementById('te-scenario-status');
  const layout = technoParkLayout();
  const sources = [];
  if (layout) sources.push(`Parque: ${layout.turbine_name || layout.turbine_key || 'turbina aplicada'} · ${layout.n_placed} aeros · AEP ${Math.round(layout.aep_mwh).toLocaleString()} MWh/año (con estela)`);
  else sources.push('⚠ Sin layout aplicado: ejecuta y aplica una configuración en Diseño y optimización del parque.');
  if (WP.parkConfiguration) sources.push(`Configuración comercial: revisión ${WP.parkConfiguration.configuration_revision || 0}`);
  if (WP.activeDataSource?.startsWith('mcp:')) sources.push(`Recurso: reconstrucción histórica MCP (${WP.activeDataSource})`);
  else if (WP.studyMode === 'reanalysis_only') sources.push(`Recurso: ERA5/MERRA-2 del área (${WP.activeDataSource}) · sin MCP`);
  else if (singleStationMode()) sources.push(`Fuente temporal: ${WP.singleStationStudy?.published_source?.label || WP.activeDataSource} (${WP.activeDataSource}) · sin MCP`);
  else sources.push('Recurso: serie observada activa');
  if (KRIG.result?.spatial_sources) sources.push(`Kriging: ${KRIG.result.spatial_sources.length} estaciones espaciales`);
  if (parkMarketTrajectory('pml')?.length) sources.push('Precio: PML fijo 2018 (8760)');
  if (badge) badge.textContent = 'Integral';
  if (status) status.innerHTML =
    `<p style="font-size:.8rem;margin:0 0 .35rem">${sources.map(mcdaAuditEscape).join(' · ')}</p>` +
    '<p class="text-muted" style="font-size:.72rem;margin:0">Los valores se heredan al entrar o al pulsar «Actualizar insumos»; después pueden editarse libremente.</p>';
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

  // Badge de viento de estación cuando la evaluación integral usa esa fuente.
  const windBadge = document.getElementById('te-wind-station-badge');
  if (windBadge) {
    const ws = r.wind_source;
    if (ws?.source === 'session') {
      const rawM  = ws.raw_mean_ms  != null ? ws.raw_mean_ms.toFixed(2)  + ' m/s' : '—';
      const hubM  = ws.hub_mean_ms  != null ? ws.hub_mean_ms.toFixed(2)  + ' m/s' : '—';
      const nRec  = ws.n_rows       != null ? ws.n_rows.toLocaleString() + ' reg.' : '';
      const sourceLabel = ws.data_source?.kind === 'mcp'
        ? `Reconstrucción MCP · ${ws.data_source.label || ws.data_source.source_id}`
        : ws.data_source?.kind === 'reanalysis_area'
          ? `Reanálisis del área · ${ws.data_source.label || ws.data_source.source_id} · sin MCP`
          : ws.data_source?.kind === 'reanalysis'
            ? `Referencia virtual · ${ws.data_source.label || ws.data_source.source_id} · sin MCP`
            : `Estación observada · ${ws.data_source?.label || 'observed'}`;
      windBadge.textContent = `⬢ ${sourceLabel} · media fuente: ${rawM} · media hub: ${hubM}${nRec ? ' · ' + nRec : ''}`;
      windBadge.style.display = '';
    } else {
      windBadge.style.display = 'none';
    }
  }

  // OJO: scenario.leveraged.cashflow_table (endpoint /api/energy/leveraged) trae
  // columnas de deuda/equity (balance_start, dscr, equity_cf, cum_equity_cf...),
  // no {year, pv_cashflow, cumulative} que espera el K-Means de ciclo de vida.
  // Usar siempre la tabla del proyecto (adjR.analysis), con o sin financiamiento.
  renderKmeansLifecycle(adjR.analysis?.cashflow_table);
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
  const scenarioKey = 'integrated';
  try {
    const inputs = technoCollectInputs();
    if (!Number.isFinite(inputs.price) || inputs.price < 0 ||
        !Number.isInteger(inputs.years) || inputs.years < 5 || inputs.years > 40) {
      toast('Captura un precio de energía válido y una vida del proyecto entre 5 y 40 años.', 'warn');
      return;
    }
    // El AEP financiero SIEMPRE viene del layout aplicado en "Diseño y
    // optimización del parque" (ya incluye estela, disponibilidad y
    // pérdidas eléctricas) — nunca de un recálculo sin estela. Sin layout
    // aplicado, no hay número confiable que mostrar: se bloquea el cálculo.
    const layout = technoParkLayout();
    if (!layout) {
      toast('Ejecuta y aplica una configuración en "Diseño y optimización del parque" antes de calcular el análisis financiero.', 'warn');
      setStatus('Falta aplicar un layout de parque.');
      return;
    }
    setStatus('Calculando análisis tecnoeconómico…');

    const fxScalar = Number.isFinite(inputs.fx) && inputs.fx > 0 ? inputs.fx : null;
    const builtPrice = technoBuildPriceSeries(inputs.priceMode, inputs.years, fxScalar);
    if (inputs.priceMode === 'pml8760' && !builtPrice.series) {
      toast('No hay un PML fijo 2018 publicado; se usará el precio manual.', 'warn');
    }

    const body = {
      session_id:    WP.sessionId,
      data_source:   WP.activeDataSource,
      scenario:      scenarioKey,
      n_turbines:    layout.n_placed,
      ws_height_m:   wsH(),
      annual_energy_mwh: layout.aep_mwh,
      turbine: { builtin_model: layout.turbine_key || inputs.turbine },
      financial: {
        demand_mwh:           inputs.demand,
        energy_price_usd_kwh: inputs.price,
        discount_rate_pct:    inputs.rate,
        project_years:        inputs.years,
        usd_to_mxn:           inputs.fx,
        annual_inflation_pct: inputs.inflation,
        cost_model:           inputs.costModel,
        energy_price_series_usd_kwh: builtPrice.series || null,
        price_hourly_year1_usd_kwh:  builtPrice.hourly || null,
        price_mode: inputs.priceMode,
      }
    };
    const r = await api('/api/energy/techno_economic', 'POST', body);
    WP.technoScenarios.integrated.inputs = inputs;
    WP.technoScenarios.integrated.result = r;
    WP.technoInputsDirty = false;
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

// ─── SENSIBILIDAD GLOBAL — MONTE CARLO + RANDOM FOREST (ML) ─────────────────
const GSENS = { result: null };

async function runGlobalSensitivity() {
  const sc = WP.technoScenarios[WP.technoActiveScenario];
  const r  = sc?.result;
  if (!r?.analysis || !WP.sessionId) { toast('Corre primero el análisis tecnoeconómico.', 'warn'); return; }
  const a = r.analysis;
  const stored = sc.inputs || {};
  const inp    = technoCollectInputs();
  const price  = stored.price || inp.price;
  const rate   = stored.rate  || inp.rate;
  const nSamples = Math.max(100, Math.min(3000, parseInt(document.getElementById('gsens-n-samples')?.value) || 800));
  const body = {
    session_id: WP.sessionId,
    turbine:    { builtin_model: stored.turbine || inp.turbine },
    n_turbines: stored.nTurbines || inp.nTurbines,
    base_annual_kwh: a.annual_energy_kwh || 0,
    n_samples: nSamples,
    financial: {
      energy_price_usd_kwh: price,
      discount_rate_pct:    rate,
      project_years:        stored.years || inp.years,
      annual_inflation_pct: stored.inflation ?? inp.inflation ?? 0,
      cost_model:           stored.costModel || technoCollectCostModel(),
    },
  };
  // Reusa P10/P90 del AEP ya calculado (si existe) en vez de inventar un rango de viento.
  const aep = WP.energyResult?.aep;
  if (aep?.aep_p10_mwh != null && aep?.aep_p90_mwh != null) {
    body.aep_p10_mwh = aep.aep_p10_mwh;
    body.aep_p90_mwh = aep.aep_p90_mwh;
  }
  // Estela como 6ª variable de diseño (±30% relativo), si hay un layout aplicado.
  const wakeBase = technoParkLayout()?.wake_loss_pct;
  if (Number.isFinite(wakeBase)) body.base_wake_loss_pct = wakeBase;
  const btn = document.getElementById('btn-gsens-run');
  if (btn) btn.disabled = true;
  document.getElementById('gsens-status').textContent = 'Ejecutando…';
  document.getElementById('gsens-progress-text').textContent =
    `Generando ${nSamples} escenarios Monte Carlo y entrenando Random Forest…`;
  try {
    const data = await api('/api/energy/global_sensitivity', 'POST', body);
    GSENS.result = data;
    document.getElementById('gsens-status').textContent = `${data.n_samples} muestras`;
    document.getElementById('gsens-progress-text').textContent = '';
    document.getElementById('gsens-results').style.display = '';
    renderGlobalSensitivity();
    toast('Sensibilidad global completada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('gsens-status').textContent = 'Error';
    document.getElementById('gsens-progress-text').textContent = '';
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** Interpreta la comparación RF vs. modelo lineal, calculada sobre los mismos
 * folds de validación cruzada — no asume que RF es mejor solo por ser ML.
 * delta = desempeño(RF) - desempeño(lineal); isPct indica si delta ya está
 * en puntos porcentuales (clasificación) en vez de unidades de R². */
function mlVsLinearVerdict(delta, isPct) {
  const d = isPct ? delta * 100 : delta;
  const threshold = isPct ? 5 : 0.05;
  if (d > threshold) return 'La relación no es suficientemente lineal para un modelo clásico — Random Forest aporta valor real aquí.';
  if (d < -threshold) return 'El modelo lineal iguala o supera a Random Forest fuera de muestra — en este rango la relación es predominantemente lineal y el modelo clásico es preferible.';
  return 'Random Forest y el modelo lineal rinden de forma similar fuera de muestra — no hay evidencia de no linealidad relevante en este rango.';
}

function renderGlobalSensitivity() {
  const data = GSENS.result;
  if (!data) return;
  const wakeRangeEl = document.getElementById('gsens-wake-range');
  const wakeRange = data.ranges_used?.wake_loss_pct;
  if (wakeRangeEl) wakeRangeEl.textContent = wakeRange
    ? `Estela muestreada: ${wakeRange[0].toFixed(2)}%–${wakeRange[1].toFixed(2)}% de pérdida.${wakeRange[1] === 0 ? ' Base cero: no se explora incertidumbre de estela.' : ''}`
    : 'Sin variación de estela: no se proporcionó una pérdida base.';
  const metric = document.getElementById('gsens-metric')?.value || 'b_c_ratio';

  // Badges de caso base para las 5 métricas
  const badgesEl = document.getElementById('gsens-base-badges');
  if (badgesEl) {
    const fmt = (k, v) => {
      if (v == null) return '—';
      if (k === 'lcoe_usd_kwh') return v.toFixed(4) + ' USD/kWh';
      if (k === 'payback_years') return v.toFixed(1) + ' años';
      if (k === 'irr_pct') return v.toFixed(2) + '%';
      return v.toFixed(3);
    };
    badgesEl.innerHTML = Object.entries(data.metric_labels).map(([k, label]) => `
      <span class="badge ${k === metric ? 'badge--success' : 'badge--info'}" style="font-size:.7rem">
        ${label}: <b>${fmt(k, data.base_case[k])}</b>
      </span>`).join('');
  }

  // Badges de robustez (P10-P90 + probabilidad de viabilidad) de la métrica activa
  const robEl = document.getElementById('gsens-robustness-badges');
  if (robEl) {
    const rob = data.robustness?.[metric];
    if (!rob) {
      robEl.innerHTML = '';
    } else {
      const fmtR = (k, v) => {
        if (v == null) return '—';
        if (k === 'lcoe_usd_kwh') return v.toFixed(4);
        if (k === 'payback_years') return v.toFixed(1);
        if (k === 'irr_pct') return v.toFixed(2) + '%';
        return v.toFixed(3);
      };
      const chips = [
        `<span class="badge badge--info" style="font-size:.7rem">P10: <b>${fmtR(metric, rob.p10)}</b></span>`,
        `<span class="badge badge--info" style="font-size:.7rem">P50: <b>${fmtR(metric, rob.p50)}</b></span>`,
        `<span class="badge badge--info" style="font-size:.7rem">P90: <b>${fmtR(metric, rob.p90)}</b></span>`,
      ];
      if (rob.prob_gt_1 != null) chips.push(`<span class="badge badge--success" style="font-size:.7rem">P(B/C&gt;1): <b>${rob.prob_gt_1}%</b></span>`);
      if (rob.prob_gt_0 != null) chips.push(`<span class="badge badge--success" style="font-size:.7rem">P(VAN/CAPEX&gt;0): <b>${rob.prob_gt_0}%</b></span>`);
      const wake = data.wake_breakeven;
      if (wake?.status === 'ok') {
        chips.push(`<span class="badge badge--warn" style="font-size:.7rem">Estela límite: <b>${wake.wake_breakeven_pct}%</b> (margen ${wake.wake_margin_pct > 0 ? '+' : ''}${wake.wake_margin_pct} pp)</span>`);
      } else if (wake?.status === 'already_unviable') {
        chips.push(`<span class="badge badge--error" style="font-size:.7rem">⚠ Ya inviable con la estela de diseño actual</span>`);
      } else if (wake?.status === 'robust_beyond_99pct') {
        chips.push(`<span class="badge badge--success" style="font-size:.7rem">Tolera estela &gt;99% (muy robusto)</span>`);
      }
      robEl.innerHTML = chips.join('');
    }
  }

  const imp = data.importance?.[metric];
  const gsensVal = data.validation?.[metric];
  const gsensValEl = document.getElementById('gsens-validation');
  if (gsensValEl) gsensValEl.innerHTML = gsensVal
    ? `<span class="text-muted">Validación fuera de muestra (${gsensVal.n_splits}-fold CV, n=${gsensVal.n_samples}): `
      + `R² Random Forest = <b>${gsensVal.r2_mean.toFixed(3)}</b> ± ${gsensVal.r2_std.toFixed(3)} · `
      + `R² regresión lineal = <b>${gsensVal.linear_model_r2_mean.toFixed(3)}</b> ± ${gsensVal.linear_model_r2_std.toFixed(3)}. `
      + mlVsLinearVerdict(gsensVal.r2_mean - gsensVal.linear_model_r2_mean)
      + '</span>'
    : '';
  destroyChart('gsens-importance');
  const ctx = document.getElementById('gsens-importance-chart')?.getContext('2d');
  if (ctx) {
    if (!imp) {
      ctx.canvas.parentElement.innerHTML = '<p class="text-muted" style="font-size:.8rem">No hay suficientes muestras válidas para esta métrica.</p>';
    } else {
      WP.charts['gsens-importance'] = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: imp.map(row => row.label),
          datasets: [{ label: 'Importancia (%)', data: imp.map(row => row.importance_pct),
            backgroundColor: acc() + '99', borderColor: acc(), borderWidth: 1, borderRadius: 3 }],
        },
        options: {
          indexAxis: 'y', responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false },
            tooltip: { callbacks: { label: ctx2 => ` ${ctx2.parsed.x.toFixed(1)}%` } } },
          scales: {
            x: { grid: { color: gridColor() }, title: { display: true, text: '% de importancia', font: { size: 10 } } },
            y: { grid: { display: false }, ticks: { font: { size: 10 } } },
          },
        },
      });
    }
  }

  renderGlobalSensitivityMatrix(data);
}

/** Matriz variable×métrica: cada celda es el % de importancia de esa variable en
 * esa métrica (mismo patrón visual que renderClusterDistanceTable) — reemplaza la
 * superficie 3D de 2 variables por una vista completa de las 5-6 variables contra
 * las 5 métricas de una sola vez. */
function renderGlobalSensitivityMatrix(data) {
  const el = document.getElementById('gsens-matrix');
  if (!el) return;
  const metricKeys = Object.keys(data.metric_labels);
  const maxV = Math.max(1e-9, ...data.features.map(f =>
    Math.max(...metricKeys.map(m => {
      const row = data.importance?.[m];
      const cell = row?.find(r => r.feature === f);
      return cell ? cell.importance_pct : 0;
    }))
  ));
  const cellColor = (v) => {
    const t = Math.max(0, Math.min(1, v / maxV));
    const alpha = Math.round(t * 200).toString(16).padStart(2, '0');
    return `#4DA3FF${alpha}`;
  };
  const header = `<tr><th></th>${metricKeys.map(m => `<th style="font-size:.66rem;white-space:nowrap">${data.metric_labels[m]}</th>`).join('')}</tr>`;
  const rows = data.features.map(f => {
    const cells = metricKeys.map(m => {
      const row = data.importance?.[m];
      const cell = row?.find(r => r.feature === f);
      const v = cell ? cell.importance_pct : null;
      return `<td style="background:${v != null ? cellColor(v) : 'transparent'};text-align:center;font-size:.66rem;padding:.25rem .4rem">${v != null ? v.toFixed(1) + '%' : '—'}</td>`;
    }).join('');
    return `<tr><th style="font-size:.66rem;white-space:nowrap;text-align:left">${data.feature_labels[f]}</th>${cells}</tr>`;
  }).join('');
  el.innerHTML = `<table class="data-table" style="border-collapse:collapse">${header}${rows}</table>`;
}

// ─── ROBUSTEZ COMPARADA ENTRE TODAS LAS TURBINAS DEL RANKING TOPSIS ─────────
const GSENS_BATCH = { result: null };

async function runGlobalSensitivityBatch() {
  const ranking = (typeof MCDA !== 'undefined' && MCDA.lastRanking?.ranking) || [];
  if (!ranking.length) { toast('Corre TOPSIS primero — no hay ranking disponible.', 'warn'); return; }
  // Una fila por modelo de turbina único (evita evaluar la misma turbina 2 veces).
  const seen = new Set();
  const turbines = [];
  ranking.forEach(row => {
    const key = row.turbina || row.label;
    const aep = Number(row.aep_mwh ?? row.aep_p50_mwh);
    const n = Number(row.n_aerogeneradores);
    if (!key || seen.has(key) || !Number.isFinite(aep) || aep <= 0 || !(n > 0)) return;
    seen.add(key);
    turbines.push({
      label: key, builtin_model: key, n_turbines: Math.round(n),
      annual_energy_kwh: aep * 1000,
      wake_loss_pct: Number.isFinite(Number(row.wake_loss_pct)) ? Number(row.wake_loss_pct) : null,
    });
  });
  if (!turbines.length) { toast('El ranking no trae AEP/n_aerogeneradores válidos.', 'warn'); return; }

  // No depende de haber corrido Tecnoeconómico: toma precio/demanda de la config
  // del parque, igual que ya hace mcdaRunAnalysis() para TOPSIS.
  const marketInputs = parkEquivalentDesignInputs();
  const projectYears = Number(WP.parkConfiguration?.project_horizon_years);
  if (marketInputs.price_usd_kwh == null) {
    toast('Carga el precio de energía en Configuración del parque antes de comparar robustez.', 'warn');
    return;
  }
  const body = {
    turbines,
    n_samples: 300,
    financial: {
      energy_price_usd_kwh: marketInputs.price_usd_kwh,
      discount_rate_pct:    WP.parkConfiguration?.base_interest_rate_pct ?? 8,
      project_years:        Number.isFinite(projectYears) ? projectYears : 20,
      annual_inflation_pct: 0,
      cost_model:           technoCollectCostModel(),
    },
  };
  const btn = document.getElementById('btn-gsens-batch-run');
  if (btn) btn.disabled = true;
  document.getElementById('gsens-batch-card').style.display = '';
  document.getElementById('gsens-batch-status').textContent = `Comparando ${turbines.length} turbinas…`;
  document.getElementById('gsens-batch-progress-text').textContent =
    `Corriendo Monte Carlo económico por turbina (sin Random Forest)…`;
  try {
    const data = await api('/api/energy/global_sensitivity_batch', 'POST', body);
    GSENS_BATCH.result = data;
    document.getElementById('gsens-batch-status').textContent = `${data.turbines.length} turbinas · ${data.n_samples} muestras c/u`;
    document.getElementById('gsens-batch-progress-text').textContent = '';
    renderGlobalSensitivityBatchTable(data);
    renderGlobalSensitivityBatchInterpretation(data, ranking[0]?.turbina || ranking[0]?.label);
    toast('Comparación de robustez completada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('gsens-batch-status').textContent = 'Error';
    document.getElementById('gsens-batch-progress-text').textContent = '';
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderGlobalSensitivityBatchTable(data) {
  const el = document.getElementById('gsens-batch-table');
  if (!el) return;
  const names = data.turbines.map(t => _topsisRobustName(t.label));
  const fmtPct = (v) => v != null ? v.toFixed(1) + '%' : '—';
  const fmtNum = (v) => v != null ? v.toFixed(3) : '—';
  const fmtWake = (w) => {
    if (!w) return '—';
    if (w.status === 'already_unviable') return 'inviable ya al 0% extra';
    if (w.status === 'robust_beyond_99pct') return '>99% (muy robusto)';
    return `${w.wake_breakeven_pct}% (margen ${w.wake_margin_pct > 0 ? '+' : ''}${w.wake_margin_pct} pp)`;
  };
  const fmtRiskClusters = (rc) => {
    if (!rc) return '<span class="text-muted">Muestras insuficientes</span>';
    const parts = rc.clusters.map(c =>
      `${c.label} ${c.share_pct}%<br><span class="text-muted">VAN&lt;0 en ${c.prob_van_negative_pct}%</span>`
    ).join(' · ');
    return `<span title="k=${rc.k} elegido por silhouette (${rc.silhouette_score}) sobre ${rc.n_valid_samples} muestras válidas">${parts}</span>`;
  };
  const rows = data.turbines.map((t, i) => {
    if (t.error) return `<tr><td>${names[i]}</td><td colspan="9" class="text-muted">${t.error}</td></tr>`;
    const bc = t.robustness?.b_c_ratio;
    const van = t.robustness?.van_capex_ratio;
    return `<tr>
      <td>${names[i]}</td>
      <td>${fmtPct(bc?.prob_gt_1)}</td>
      <td>${fmtNum(bc?.p10)}</td><td>${fmtNum(bc?.p50)}</td><td>${fmtNum(bc?.p90)}</td>
      <td>${fmtPct(van?.prob_gt_0)}</td>
      <td>${fmtNum(van?.p10)}</td><td>${fmtNum(van?.p50)}</td><td>${fmtNum(van?.p90)}</td>
      <td>${fmtWake(t.wake_breakeven)}</td>
      <td style="min-width:180px">${fmtRiskClusters(t.risk_clusters)}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table class="data-table" style="font-size:.72rem">
    <thead><tr>
      <th>Turbina</th><th>P(B/C&gt;1)</th><th>B/C P10</th><th>B/C P50</th><th>B/C P90</th>
      <th>P(VAN/CAPEX&gt;0)</th><th>VAN/CAPEX P10</th><th>VAN/CAPEX P50</th><th>VAN/CAPEX P90</th>
      <th>Estela límite (viabilidad)</th>
      <th>Clústeres de riesgo financiero (K-means real sobre Monte Carlo)</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

/** Interpretación en texto plano de la tabla comparativa — contrasta la ganadora
 * de TOPSIS (score puntual, escenario base) contra la más robusta (consistencia
 * ante incertidumbre), que no siempre son la misma alternativa. */
function renderGlobalSensitivityBatchInterpretation(data, topsisWinnerLabel) {
  const el = document.getElementById('gsens-batch-interpretation');
  if (!el) return;
  const valid = data.turbines.filter(t => !t.error && t.robustness?.b_c_ratio);
  if (!valid.length) { el.innerHTML = ''; return; }
  const mostRobust = valid[0]; // ya viene ordenado por prob_gt_1 desc del backend
  const mostRobustName = _topsisRobustName(mostRobust.label);
  const winnerName = _topsisRobustName(topsisWinnerLabel);
  const bc = mostRobust.robustness.b_c_ratio;
  const wake = mostRobust.wake_breakeven;
  const parts = [];
  parts.push(`<b>${mostRobustName}</b> es la alternativa más robusta: mantiene B/C&gt;1 en el <b>${bc.prob_gt_1}%</b> de los escenarios simulados (rango P10-P90: ${bc.p10}–${bc.p90}).`);
  if (mostRobust.label === topsisWinnerLabel) {
    parts.push(`Coincide con la ganadora actual del ranking TOPSIS (<b>${winnerName}</b>) — la elección está reforzada tanto en el escenario base como ante incertidumbre.`);
  } else {
    const winnerRow = valid.find(t => t.label === topsisWinnerLabel);
    const winnerBc = winnerRow?.robustness?.b_c_ratio;
    parts.push(`Difiere de la ganadora actual del ranking TOPSIS (<b>${winnerName}</b>), que en el escenario base tiene mejor score puntual` +
      (winnerBc ? ` pero se mantiene viable solo en el ${winnerBc.prob_gt_1}% de los escenarios` : '') +
      ` — vale la pena revisar si conviene priorizar robustez sobre desempeño puntual.`);
  }
  if (wake?.status === 'ok') {
    parts.push(`Su diseño actual tolera hasta <b>${wake.wake_breakeven_pct}%</b> de pérdida por estela antes de dejar de ser viable (margen de ${wake.wake_margin_pct} puntos porcentuales sobre el valor de diseño).`);
  } else if (wake?.status === 'already_unviable') {
    parts.push(`⚠ Con la estela de diseño actual ya está al límite: cualquier estela adicional la vuelve inviable (B/C&lt;1).`);
  }
  const rc = mostRobust.risk_clusters;
  if (rc) {
    const worst = rc.clusters[0];
    parts.push(`Sus escenarios Monte Carlo se agrupan en <b>${rc.k} perfiles de riesgo financiero</b> reales (k elegido por silhouette=${rc.silhouette_score}): el más riesgoso concentra ${worst.share_pct}% de los escenarios con VAN negativo en el ${worst.prob_van_negative_pct}% de ellos — probabilidad global de VAN negativo: <b>${rc.overall_prob_van_negative_pct}%</b>.`);
  }
  el.innerHTML = `<p style="margin:0">${parts.join(' ')}</p>`;
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
  if (data.regimes) {
    renderRegimeChart(data.regimes);
  } else {
    const wrap = document.getElementById('risk-regimes-wrap');
    if (wrap) wrap.style.display = 'none';
  }

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
        { label: 'Reducción (caso adverso)', data: loVals.map(v => v < 0 ? v : 0),
          backgroundColor: 'rgba(239,68,68,.75)', borderRadius: 3 },
        { label: 'Aumento (caso favorable)', data: hiVals.map(v => v > 0 ? v : 0),
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

  const assumptions = document.getElementById('te-lifecycle-assumptions');
  if (assumptions) assumptions.textContent = a.degradation_pct_per_year != null
    ? `Degradación: ${a.degradation_pct_per_year}% anual desde el año 2. Desmantelamiento: ${a.decommissioning_pct_capex}% del CAPEX inicial al final del proyecto. Supuestos del estudio; sin valor de rescate.`
    : 'Resultado guardado sin supuestos de ciclo de vida: vuelve a calcular para aplicar degradación y desmantelamiento.';

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
  const title = document.getElementById('te-cashflow-chart-title');
  if (title) title.textContent = `Flujo de caja acumulado (${table.length} años)`;
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
  const sectionIds = ['te-energy-section', 'te-el-charts-1', 'te-el-charts-2'];
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

  renderTEPowerCurveChart(el.turbine, el.timeseries, el.n_turbines);
  renderTEMonthlyChart(el.monthly);
  renderTETimeseries(el.timeseries);
  renderTESensitivityChart(el.sensitivity || []);
}

function renderTEPowerCurveChart(turbine, timeseries, nTurbines) {
  destroyChart('te-pcurve');
  if (!turbine) return;
  const ctx = document.getElementById('te-pcurve-chart')?.getContext('2d');
  if (!ctx) return;
  const nominal = turbine.rated_power_kw;
  const curvePts = turbine.curve_ws.map((ws, i) => ({ x: ws, y: turbine.curve_pw[i] }));
  const maxCurveWs = turbine.curve_ws[turbine.curve_ws.length - 1] || 25;
  // timeseries.power_kw es la potencia del PARQUE (turbina × n_turbinas); hay
  // que llevarla a potencia por aerogenerador para compararla contra la curva.
  const perTurbine = Number(nTurbines) > 0 ? Number(nTurbines) : 1;
  const obsPts = [];
  const wsArr = timeseries?.ws_hub || [];
  const pArr  = timeseries?.power_kw || [];
  for (let i = 0; i < Math.min(wsArr.length, pArr.length); i++) {
    if (Number.isFinite(wsArr[i]) && Number.isFinite(pArr[i])) obsPts.push({ x: wsArr[i], y: pArr[i] / perTurbine });
  }
  const maxWs = obsPts.length ? Math.max(maxCurveWs, ...obsPts.map(p => p.x)) : maxCurveWs;
  const obsStyle = densityStyledScatter(obsPts, '#9F7AEA');
  WP.charts['te-pcurve'] = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        ...(obsPts.length ? [{
          type: 'scatter', label: 'Observaciones del sitio (densidad)',
          data: obsPts, order: 2,
          backgroundColor: obsStyle.backgroundColor, borderColor: obsStyle.borderColor,
          pointRadius: obsStyle.radius, pointHoverRadius: 5, showLine: false,
        }] : []),
        { label: turbine.name, data: curvePts, order: 0,
          borderColor: acc(), backgroundColor: acc() + '20',
          borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3 },
        ...(nominal ? [{ label: `Nominal ${nominal} kW`, order: 1,
          data: [{ x: 0, y: nominal }, { x: maxWs, y: nominal }],
          borderColor: gold() + '66', borderDash: [4, 3], borderWidth: 1, pointRadius: 0, fill: false }] : []),
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { font: { size: 9 }, boxWidth: 10 } } },
      scales: {
        x: { type: 'linear', grid: { color: gridColor() }, title: { display: true, text: 'Velocidad (m/s)', font: { size: 9 } }, ticks: { font: { size: 9 } } },
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

// ─── LEVERAGED FINANCIAL ANALYSIS ────────────────────────────────────────────

const _LEV_NAMES = { integrated: 'Evaluación integral' };

function showFinancingModal(scenarioKey) {
  const res = WP.technoScenarios[scenarioKey]?.result;
  if (!res?.analysis) {
    toast('Calcula primero la evaluación integral antes de incluir financiamiento', 'warn');
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
    `Base financiera: ${_LEV_NAMES[scenarioKey] || 'Evaluación integral'}`;
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
    project_cashflows_usd:    a.cashflow_table.map(row => row.cashflow),
    project_years:            a.project_years,
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
    // Renderiza el financiamiento de la evaluación integral única.
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
    const nYrs   = adjA.project_years || (_sc.inputs?.years) || 25;
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
    } else if (type === 'climatology') {
      const r = WP.windStats || await api('/api/wind/stats', 'POST', {
        session_id: WP.sessionId, data_source: WP.activeDataSource,
      });
      WP.windStats = r;
      csvText = buildClimatologyCsv(r.climatology);
      filename = 'climatology.csv';
    } else if (type === 'techno') {
      csvText = buildTechnoCsv(WP.technoResult);
      filename = 'techno_economic.csv';
    } else if (type === 'wind_stats') {
      const r = WP.windStats || await api('/api/wind/stats', 'POST', {
        session_id: WP.sessionId, data_source: WP.activeDataSource,
      });
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
      'Energía: Curva de potencia real + corrección densidad de aire',
      'Tecno-econ.: Metodología PhD MATLAB — CAPEX, OPEX, VAN, LCOE, VAE',
      'Weibull: máxima verosimilitud de 2 parámetros, localización fija en 0 (scipy.stats.weibull_min)',
      '',
      '── LIMITACIONES ───────────────────────────────────────',
      '· Datos a 50m; hub height típico 80–120m — aplicar extrapolación logarítmica',
      '· CAPEX estimado; solicitar presupuesto detallado a OEM',
      '· VAN sensible a tasa de descuento y precio de energía asumidos',
      '',
      '══════════════════════════════════════════════════════',
      ' AERO OPEN CIM · v2.0 · Flask + scikit-learn',
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
  const showTerminal = table.some(row => Number(row.decommissioning) > 0);

  const headers = ['Año'];
  if (showEnergy) headers.push('Energía (MWh)');
  if (showPrice)  headers.push('Precio (USD/kWh)');
  if (showRate)   headers.push('Tasa (%)');
  if (showFx)     headers.push('FX (MXN/USD)');
  if (showOpex)   headers.push('OPEX (USD)');
  if (showTerminal) headers.push('Desmantelamiento (USD)');
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
    if (showTerminal) cells.push(`<td>${usd(r.decommissioning || 0)}</td>`);
    cells.push(`<td>${usd(r.cashflow)}</td>`);
    cells.push(`<td>${usd(r.pv_cashflow)}</td>`);
    cells.push(`<td>${usd(r.cumulative)}</td>`);
    return `<tr class="${cls}">${cells.join('')}</tr>`;
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
    const wsHAdv     = parseFloat(document.getElementById('adv-ws-height')?.value) || wsH();
    const zHub       = parseFloat(document.getElementById('adv-z-hub')?.value)       || null;
    const orogFactor = parseFloat(document.getElementById('adv-orog-factor')?.value ?? '0.15') || 0.15;
    const zScale     = parseFloat(document.getElementById('adv-z-scale')?.value)     || 80;
    const r = await api('/api/wind/advanced', 'POST', {
      session_id: WP.sessionId, data_source: WP.activeDataSource,
      ws_height: wsHAdv, z_hub: zHub,
      orog_factor: orogFactor, z_scale: zScale,
    });
    WP.advancedResult = r;

    renderSavedAdvanced(r);

    toast('Análisis avanzado completo', 'success');
    setStatus('Recurso avanzado calculado.');
  } catch(e) {
    toast('Error: ' + e.message, 'error');
  } finally {
    btn.disabled = false; btn.textContent = 'Calcular análisis avanzado';
  }
}

function renderTIChart(ti, isMeasured = false) {
  if (!ti?.bins?.length) return;
  destroyChart('ti');
  const bins = ti.bins.filter(b => b.ti_mean != null);
  const ctx  = document.getElementById('chart-ti').getContext('2d');
  WP.charts.ti = new Chart(ctx, {
    type: 'line',
    data: {
      labels: bins.map(b => b.ws_center),
      datasets: [
        { label: isMeasured ? 'TI medida media' : 'Variabilidad horaria media', data: bins.map(b => b.ti_mean != null ? +(b.ti_mean * 100).toFixed(2) : null),
          borderColor: acc(), backgroundColor: acc() + '25', fill: false, tension: 0.35, pointRadius: 3 },
        { label: isMeasured ? 'TI medida P90' : 'Variabilidad horaria P90', data: bins.map(b => b.ti_p90 != null ? +(b.ti_p90 * 100).toFixed(2) : null),
          borderColor: acc2(), borderDash: [4,4], fill: false, pointRadius: 2 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: ink(), font: { size: 10 } } } },
      scales: {
        x: { title: { display: true, text: 'Velocidad (m/s)', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
        y: { title: { display: true, text: isMeasured ? 'TI medida (%)' : 'Variabilidad σ/V̄ (%)', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
      },
    },
  });
}

function renderHistoricalTurbulenceChart(model) {
  destroyChart('ti-historical');
  const ctx = document.getElementById('chart-ti-historical')?.getContext('2d');
  if (!ctx || !model?.available || !model?.monthly?.length) return;
  const rows = model.monthly;
  WP.charts['ti-historical'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: rows.map(row => row.label),
      datasets: [
        { label: 'TI modelada P50', data: rows.map(row => row.ti_p50 != null ? +(row.ti_p50 * 100).toFixed(2) : null),
          borderColor: acc(), backgroundColor: acc() + '22', fill: true, tension: .3, pointRadius: 3 },
        { label: 'TI modelada P90', data: rows.map(row => row.ti_p90 != null ? +(row.ti_p90 * 100).toFixed(2) : null),
          borderColor: acc2(), borderDash: [5, 4], fill: false, tension: .3, pointRadius: 3 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: ink(), font: { size: 10 } } } },
      scales: {
        x: { ticks: { color: mut() }, grid: { color: mut() + '22' } },
        y: { title: { display: true, text: 'TI modelada (%)', color: mut() },
             ticks: { color: mut() }, grid: { color: mut() + '22' } },
      },
    },
  });
}

function renderTurbulenceTraceability(measured, model, hourly) {
  const detail = document.getElementById('adv-ti-model-details');
  const badge  = document.getElementById('adv-badge-ti-model');
  if (!detail) return;
  if (!model?.available) {
    if (badge) { badge.textContent = 'No disponible'; badge.className = 'cbadge r'; }
    detail.textContent = `${model?.reason || 'No se publicó un modelo histórico de turbulencia.'} La variabilidad horaria permanece disponible sólo como diagnóstico y no se clasifica como TI.`;
    return;
  }
  if (badge) {
    badge.textContent = `${model.training_year} → histórico · modelado`;
    badge.className = 'cbadge b';
  }
  const validation = model.validation || {};
  const metrics = validation.metrics_p50 || {};
  const missingNames = (validation.months_without_measurements || []).map(month =>
    ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][month - 1]
  );
  const measuredText = measured?.available
    ? `${Number(measured.training_records || measured.N || 0).toLocaleString()} ventanas CERTE de entrenamiento`
    : 'sin TI nativa';
  const campaignText = (measured?.campaign_years || []).map(row => {
    const coverage = row.logger_pair_coverage_pct != null ? `${Number(row.logger_pair_coverage_pct).toFixed(1)}%` : '—';
    const missing = (row.months_without_measurements || []).length;
    return `${row.year}: ${row.status === 'complete' ? 'completo' : 'parcial'} (${coverage}${missing ? `, ${missing} meses ausentes` : ''})`;
  }).join(' · ');
  const validationText = validation.available
    ? `Validación ${validation.year}: N=${Number(metrics.N || 0).toLocaleString()}, MAE=${metrics.MAE != null ? (metrics.MAE * 100).toFixed(2) + ' pp' : '—'}, sesgo=${metrics.Bias != null ? (metrics.Bias * 100).toFixed(2) + ' pp' : '—'}`
    : `Sin validación medida en ${validation.year || 'el año solicitado'}`;
  detail.innerHTML = `
    <div><strong>Procedencia:</strong> modelado estadístico, no medido.</div>
    <div><strong>Entrenamiento:</strong> ${model.training_year}, ${measuredText}; mes + velocidad + 16 sectores.</div>
    <div><strong>Campaña real 10 min:</strong> ${campaignText || 'sin desglose anual'}.</div>
    <div><strong>Histórico:</strong> ${Number(model.modelled_records || 0).toLocaleString()} horas con P50/P90 y σᵤ.</div>
    <div><strong>${validationText}</strong></div>
    <div><strong>Meses sin medición de validación:</strong> ${missingNames.length ? missingNames.join(', ') : 'ninguno'}.</div>
    <div class="text-muted" style="margin-top:.45rem">La desviación móvil horaria (${hourly?.note || 'proxy'}) no alimenta esta reconstrucción.</div>`;
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
      data_source: WP.activeDataSource,
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

    renderSavedHybrid(r);

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
  guidedMcp: false,
  calibrationId: null,
  calibrationTaskId: null,
  calibrationSignature: null,
  calibration: null,
  orographicSpeedupGrid: null,
};

const KRIG_MEXICO_BOUNDS = [[14.2, -118.6], [33.2, -86.5]];

const KRIG_LAYERS = {
  wind:         { unit: 'm/s',   title: 'Velocidad promedio del viento (m/s)' },
  wind_era5:    { unit: 'm/s',   title: 'Campo ERA5 calibrado y anclado a la fuente elegida' },
  wind_merra2:  { unit: 'm/s',   title: 'Campo MERRA-2 calibrado y anclado a la fuente elegida' },
  wind_raw_reference:{ unit: 'm/s', title: 'ERA5 + MERRA-2 raw · extrapolación vertical directa a 100 m' },
  wind_calibration_delta:{ unit: 'm/s', title: 'Diferencia calibrado − raw a 100 m' },
  wind_direction:{ unit: '°',    title: 'Dirección meteorológica · norte verdadero' },
  uncertainty:  { unit: 'm/s',   title: 'Incertidumbre MCP + Kriging + proveedores' },
  weibull_k:    { unit: '',      title: 'Weibull k · reconstrucción histórica' },
  weibull_lambda:{ unit: 'm/s',  title: 'Weibull λ · reconstrucción histórica' },
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
  terrain_complexity:{ unit: '%', title: 'RIX proxy · cribado DEM, no CFD' },
};

function krig3GenerationMode() {
  return document.getElementById('krig3-generation-mode')?.value || 'windpro_classic';
}

function singleStationMode() {
  return ['observed_only', 'single_reference'].includes(WP.studyMode);
}

function projectHasSpatialSource() {
  return !!WP.activeDataSource && (singleStationMode() || WP.studyMode === 'reanalysis_only' || WP.activeDataSource.startsWith('mcp:'));
}

function krig3AdvancedMcpRequested() {
  if (!projectHasSpatialSource()) return false;
  const manual = !!document.getElementById('krig3-manual-mode')?.checked;
  return !manual && (
    krig3GenerationMode() === 'mcp_advanced'
    || projectHasSpatialSource()
  );
}

function krig3ActivatePublishedSource() {
  if (!projectHasSpatialSource() || document.getElementById('krig3-manual-mode')?.checked) return;
  const mode = document.getElementById('krig3-generation-mode');
  if (mode) mode.value = 'mcp_advanced';
  requestAnimationFrame(() => krig3SyncGenerationMode());
}

function krig3SyncGenerationMode() {
  const modeSelect = document.getElementById('krig3-generation-mode');
  const advancedOption = modeSelect?.querySelector('option[value="mcp_advanced"]');
  const noMcp = !projectHasSpatialSource();
  if (advancedOption) advancedOption.disabled = noMcp;
  if (noMcp && modeSelect) modeSelect.value = 'windpro_classic';
  const advanced = !noMcp && krig3GenerationMode() === 'mcp_advanced';
  const details = document.getElementById('krig3-downscaling-details');
  const layers = document.getElementById('krig3-advanced-layers');
  const note = document.getElementById('krig3-generation-note');
  const physical = document.getElementById('krig3-physical');
  if (details) {
    details.style.display = advanced ? '' : 'none';
    details.open = advanced;
  }
  if (layers) layers.style.display = advanced ? '' : 'none';
  // Ambos métodos generan por defecto el campo afectado por terreno. El
  // usuario todavía puede desactivar la corrección física explícitamente.
  if (physical && !physical.checked) physical.checked = true;
  if (note) {
    note.textContent = singleStationMode()
      ? `Referencia espacial: ${WP.singleStationStudy?.published_source?.label || WP.activeDataSource}. Se aplica la misma cadena de anomalías, Kriging y terreno sobre el periodo elegido, sin reconstruir la serie con MCP.`
      : WP.studyMode === 'reanalysis_only'
      ? 'La serie del área alimenta la cadena espacial de anomalías, Kriging y terreno; no se ejecuta MCP.'
      : advanced
      ? 'Usa el hindcast MCP como ancla y aplica la cadena física clásica: neutralización, Kriging, rugosidad por píxel, respaldo de pendiente DEM y densidad por elevación.'
      : 'Kriging original con neutralización y reaplicación de rugosidad por píxel, topografía DEM y densidad del aire.';
  }
  const studyBadge = document.getElementById('krig3-study-source');
  if (studyBadge) {
    if (WP.studyMode === 'reanalysis_only') {
      const count = WP.reanalysisStudy?.reference_stations?.length || 0;
      studyBadge.textContent = `ERA5/MERRA-2 · ${count} nodos · sin MCP`;
      studyBadge.className = 'badge badge--warning';
    } else if (WP.activeDataSource?.startsWith('mcp:')) {
      studyBadge.textContent = 'Estación real · reconstrucción MCP';
      studyBadge.className = 'badge badge--success';
    } else if (singleStationMode()) {
      studyBadge.textContent = `Temporal: ${WP.singleStationStudy?.published_source?.label || WP.activeDataSource} · Kriging multipunto`;
      studyBadge.className = 'badge badge--info';
    } else {
      studyBadge.textContent = 'Fuente observada / clásica';
      studyBadge.className = 'badge badge--info';
    }
  }
}

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

// Rampa divergente centrada exactamente en cero para comparar campos.
function krigDeltaColor(t) {
  const stops = [
    [0.00, [37, 99, 235]], [0.25, [147, 197, 253]],
    [0.50, [248, 250, 252]], [0.75, [252, 165, 165]], [1.00, [185, 28, 28]],
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
    || 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}';
  KRIG.baseLayer = L.tileLayer(url, {
    maxZoom: (key === 'osm' || key === 'topo') ? 18 : 16,
    subdomains: (key === 'osm' || key === 'topo') ? 'abc' : 'abcd',
    crossOrigin: 'anonymous',  // habilita exportación a PNG (html2canvas)
    attribution: '© OpenStreetMap · © Esri',
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
  KRIG.areaBounds = bounds;
  krig3RenderAreaLayer();
  KRIG.map.fitBounds(bounds, { padding: [24, 24] });
  toast('Área de estudio fijada.', 'success');
  krig3RenderOptimalList();
  if (typeof mcdaUpdateScopeUi === 'function') mcdaUpdateScopeUi();
}

function krig3RenderAreaLayer() {
  if (KRIG.areaLayer && KRIG.map) KRIG.map.removeLayer(KRIG.areaLayer);
  KRIG.areaLayer = null;
  if (!KRIG.areaBounds || !KRIG.map) return;
  KRIG.areaLayer = L.rectangle(KRIG.areaBounds, {
    color: '#22c55e', weight: 2, fillOpacity: 0.08,
  }).addTo(KRIG.map);
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

/** Exporta KRIG.parkPoints como KML (Polygon), compatible con Google Earth /
 * Google My Maps / QGIS. Orden de coordenadas KML: lon,lat,alt. */
function krig3ExportParkKml() {
  if (!Array.isArray(KRIG.parkPoints) || KRIG.parkPoints.length < 3) {
    toast('Dibuja y cierra el polígono del parque antes de exportar.', 'warning');
    return;
  }
  const ring = KRIG.parkPoints.map(([lat, lon]) => `${lon},${lat},0`);
  ring.push(ring[0]); // KML exige cerrar el LinearRing repitiendo el primer punto
  const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Parque eólico — WindPro Platform</name>
    <Placemark>
      <name>Área del parque</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>${ring.join(' ')}</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>`;
  const url = URL.createObjectURL(new Blob([kml], {type: 'application/vnd.google-earth.kml+xml'}));
  const a = document.createElement('a'); a.href = url; a.download = 'parque-windpro.kml'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Importa un polígono desde un .kml (Google Earth/My Maps/QGIS) — toma el
 * primer <coordinates> del documento (Polygon o LineString), reemplaza
 * KRIG.parkPoints y redibuja la figura ya cerrada. */
async function krig3ImportParkKmlFile(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  try {
    const text = await file.text();
    const xml = new DOMParser().parseFromString(text, 'application/xml');
    if (xml.querySelector('parsererror')) throw new Error('KML inválido o mal formado.');
    const coordsNode = xml.getElementsByTagNameNS('*', 'coordinates')[0];
    if (!coordsNode) throw new Error('No se encontró un elemento <coordinates> en el archivo.');
    const points = coordsNode.textContent.trim().split(/\s+/).map(token => {
      const [lon, lat] = token.split(',').map(Number);
      return [lat, lon];
    }).filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon)
      && Math.abs(lat) <= 90 && Math.abs(lon) <= 180);
    // KML cierra el anillo repitiendo el primer punto — se quita para no duplicar vértice.
    if (points.length > 1) {
      const [firstLat, firstLon] = points[0];
      const [lastLat, lastLon] = points[points.length - 1];
      if (Math.abs(firstLat - lastLat) < 1e-9 && Math.abs(firstLon - lastLon) < 1e-9) points.pop();
    }
    if (points.length < 3) throw new Error('El KML necesita al menos 3 vértices válidos.');
    krig3ClearPark();
    krig3InitMap();
    KRIG.parkPoints = points;
    KRIG.parkMarkers = points.map(([lat, lon]) => L.circleMarker([lat, lon], {
      radius: 4, color: '#fff', weight: 1, fillColor: '#f59e0b', fillOpacity: 1,
    }).addTo(KRIG.map));
    krig3RenderParkLayer(true);
    krig3RenderOptimalList();
    if (typeof mcdaUpdateScopeUi === 'function') mcdaUpdateScopeUi();
    const bounds = L.latLngBounds(points);
    KRIG.map.fitBounds(bounds, {padding: [24, 24]});
    toast(`Polígono importado: ${points.length} vértices.`, 'success');
  } catch (e) {
    toast(`No se pudo importar el KML: ${e.message}`, 'error');
  }
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
  if (KRIG.guidedMcp && !KRIG.calibration) {
    KRIG.optimal = [];
    return KRIG.optimal;
  }
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
  krig3InvalidateCalibration('cambió el emplazamiento');
  if (krig3AdvancedMcpRequested() && projectHasSpatialSource()) {
    await krig3LoadSites(true);
  }
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
  krig3InvalidateCalibration('se retiró el emplazamiento');
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
  const anchor = KRIG.sites.find(s => s.allowed_role === 'anchor');
  KRIG.principal = anchor?.filename || null;
  let inside = 0;
  let nearest = null, nearestD = Infinity;
  KRIG.sites.forEach(s => {
    const d = krig3HaversineKm(KRIG.poi.lat, KRIG.poi.lon, s.lat, s.lon);
    if (d <= km && s.allowed_role !== 'anchor') {
      KRIG.selected.add(s.filename);
      inside++;
      if (d < nearestD) { nearestD = d; nearest = s; }
    }
  });
  // En el flujo MCP, M04 es el ancla fija. En manual se conserva el más cercano.
  if (!anchor && nearest) KRIG.principal = nearest.filename;
  krig3InvalidateCalibration('cambió el radio o la selección espacial');
  krig3UpdateAllMarkers();

  const info = document.getElementById('krig3-poi-info');
  if (info) {
    info.innerHTML = `${KRIG.poi.lat.toFixed(3)}°, ${KRIG.poi.lon.toFixed(3)}° · ` +
      `<b>${inside}</b> estaciones dentro de ${km} km` +
      (anchor
        ? ` · ancla <b>${krig3AnchorLabel(anchor)}</b>`
        : (nearest ? ` · ★ ${nearest.display_name || nearest.filename} (${nearestD.toFixed(1)} km)` : ''));
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
  document.querySelectorAll('.krig3-tab-mcp').forEach(b => b.style.display = 'none');
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
  const isAnchor = KRIG.sites.find(s => s.filename === filename)?.allowed_role === 'anchor';
  const bg = (isPrincipal || isAnchor) ? '#facc15' : (isSelected ? '#3b82f6' : '#475569');
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
  const total = KRIG.sites.filter(s => s.allowed_role !== 'anchor').length;
  const sel = KRIG.selected.size;
  const principalSite = KRIG.sites.find(x => x.filename === KRIG.principal);
  const principalLabel = principalSite
    ? `principal: <b>${principalSite.display_name || principalSite.filename}</b>`
    : '<span style="color:#f59e0b">sin parque principal</span>';
  const anchor = KRIG.sites.find(s => s.allowed_role === 'anchor');
  const anchorLabel = anchor ? krig3AnchorLabel(anchor) : 'sin ancla';
  el.innerHTML = WP.studyMode === 'reanalysis_only'
    ? `${sel}/${total} nodos ERA5/MERRA-2 del área · <b>sin ancla observada y sin MCP</b>`
    : KRIG.guidedMcp
    ? `${sel}/${total} nodos virtuales seleccionados · ancla: <b>${anchorLabel}</b>`
    : `${sel}/${total} sitios seleccionados · ${principalLabel}`;
}

/** Etiqueta dinámica de la fuente ancla (antes texto fijo "OBS · MCP M04"
 * sin importar la fuente activa — no distinguía CERTE observado de un
 * hindcast MCP). `anchor.source` viene del `kind` real de la fuente
 * (observed/mcp/reanalysis, ver api.py `_analysis_source_info`). */
function krig3AnchorLabel(anchor) {
  const prefix = anchor.source === 'mcp' ? 'MCP' : anchor.source === 'observed' ? 'OBS'
    : anchor.source === 'reanalysis' ? 'Reanálisis' : (anchor.source || '');
  const name = anchor.display_name || anchor.filename || '';
  return prefix ? `${prefix} · ${name}` : name;
}

function krig3SitePopupHtml(s) {
  const isPrincipal = s.filename === KRIG.principal;
  const isAnchor = s.allowed_role === 'anchor';
  const meanSpeed = Number(s.v_mean ?? s.calibrated_mean_speed);
  const gust = Number(s.gust_ms ?? s.v_max);
  const weibullC = Number(s.weibull_C ?? s.weibull_lambda);
  const weibullK = Number(s.weibull_K ?? s.weibull_k);
  const metricLine = isFinite(meanSpeed)
    ? `v̄ ${meanSpeed.toFixed(2)} m/s${isFinite(gust) ? ` · ráfaga ${gust.toFixed(2)} m/s` : ''}<br>`
    : '';
  const weibullLine = isFinite(weibullC) && isFinite(weibullK)
    ? `Weibull λ=${weibullC.toFixed(2)} · k=${weibullK.toFixed(2)}`
    : `Altura fuente ${Number(s.source_height_m || s.target_height_m || 0).toFixed(0)} m`;
  const aliasCount = Number(s.alias_count || s.file_ids?.length || 1);
  const coordinateLine = !isAnchor
    ? `<br>Coordenada: ${s.coordinate_source === 'file_metadata' ? 'metadato interno' : 'catálogo'}${s.coordinate_status === 'corrected_filename_mismatch' ? ' · nombre de archivo corregido' : ''}`
    : '<br>Coordenada oficial INEEL';
  const aliasLine = !isAnchor && aliasCount > 1
    ? `<br>${aliasCount} archivos agrupados en este nodo físico`
    : '';
  return `
    <div style="min-width:220px">
      <div style="font-weight:600;font-size:.85rem;margin-bottom:.15rem">${s.display_name || s.filename}</div>
      ${s.state ? `<div style="font-size:.72rem;color:#94a3b8;margin-bottom:.25rem">${s.state}${s.municipality ? ' · ' + s.municipality : ''}</div>` : ''}
      <div style="font-size:.75rem;color:#64748b;line-height:1.4">
        <b>${isAnchor ? 'OBS · MCP' : (s.provider || s.source || '').toUpperCase()}</b> · ${s.lat.toFixed(5)}°, ${s.lon.toFixed(5)}°<br>
        ${metricLine}${weibullLine}
        ${s.distance_km != null ? `<br>Distancia ${Number(s.distance_km).toFixed(1)} km · QC ${s.qc_status || 'pendiente'}` : ''}
        ${coordinateLine}${aliasLine}
        ${krig3TerrainBadge(s.filename)}
      </div>
      <div style="display:flex;gap:.3rem;margin-top:.5rem;flex-wrap:wrap">
        ${isAnchor ? '<span class="badge badge--warning">Ancla fija · hindcast completo</span>' : `<button class="btn-secondary krig3-popup-toggle" data-fn="${s.filename}" style="font-size:.72rem;padding:.25rem .5rem">
          ${KRIG.selected.has(s.filename) ? '✕ Excluir' : '✓ Incluir'}
        </button>`}
        ${KRIG.guidedMcp || isAnchor ? '' : `<button class="btn-primary krig3-popup-principal" data-fn="${s.filename}" style="font-size:.72rem;padding:.25rem .5rem">
          ${isPrincipal ? '★ Es principal' : '★ Marcar principal'}
        </button>`}
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
  if (KRIG.sites.find(s => s.filename === filename)?.allowed_role === 'anchor') return;
  if (KRIG.selected.has(filename)) KRIG.selected.delete(filename);
  else KRIG.selected.add(filename);
  // Si el principal sale de la selección, lo limpiamos.
  if (!KRIG.guidedMcp && KRIG.principal && !KRIG.selected.has(KRIG.principal)) KRIG.principal = null;
  krig3UpdateMarker(filename);
  krig3UpdateSelectionInfo();
  // Refresh popup content for the open marker.
  const m = KRIG.siteMarkers[filename];
  if (m) {
    const s = KRIG.sites.find(x => x.filename === filename);
    if (s) m.setPopupContent(krig3SitePopupHtml(s));
  }
  krig3InvalidateCalibration('cambió la selección de nodos');
}

function krig3SetPrincipal(filename) {
  if (KRIG.guidedMcp) return;
  KRIG.principal = filename;
  krig3InvalidateCalibration('cambió la estación espacial principal');
  // Si el principal no estaba seleccionado, inclúyelo.
  KRIG.selected.add(filename);
  krig3UpdateAllMarkers();
  // Refresh all popup contents (the previous principal needs to lose ★).
  Object.entries(KRIG.siteMarkers).forEach(([fn, m]) => {
    const s = KRIG.sites.find(x => x.filename === fn);
    if (s) m.setPopupContent(krig3SitePopupHtml(s));
  });
}

function krig3CalibrationKey() {
  return JSON.stringify({
    source: WP.activeDataSource, sourceVersion: WP.resourcePeriod?.selection_revision,
    selected: Array.from(KRIG.selected).sort(),
    poi: KRIG.poi ? [Number(KRIG.poi.lat).toFixed(5), Number(KRIG.poi.lon).toFixed(5)] : null,
    radius: Number(document.getElementById('krig3-poi-radius')?.value || 200),
    height: Number(wsH()),
  });
}

function krig3InvalidateSpatialResults() {
  KRIG.spatialEpoch = (KRIG.spatialEpoch || 0) + 1;
  if (KRIG.map) {
    (KRIG.overlays || []).forEach(layer => KRIG.map.removeLayer(layer));
    if (KRIG.poiResultMarker) KRIG.map.removeLayer(KRIG.poiResultMarker);
    Object.values(KRIG.siteMarkers || {}).forEach(marker => {
      if (!KRIG.map.hasLayer(marker)) marker.addTo(KRIG.map);
    });
  }
  KRIG.overlays = []; KRIG.poiResultMarker = null;
  const status = document.getElementById('krig3-status');
  if (status) status.textContent = 'Recalcula los mapas con la selección actual';
  KRIG.result = null; KRIG.task_id = null;
  MCDA.taskId = null;
  MCDA.lastSpatial = null; MCDA.lastAnalysis = null; MCDA.lastRanking = null;
  PARQUE.result = null; PARQUE.optResult = null; PARQUE.wakeProfile = null;
  WP.farmResult = null; WP.farmLayout = null;
  WP.technoResult = null; WP.technoInputsInitialized = false;
  WP.technoScenarios.integrated = { inputs: null, result: null, leveraged: null };
  _riskData = null;
}

function krig3InvalidateCalibration(reason='') {
  krig3InvalidateSpatialResults();
  if (!KRIG.calibrationId && !KRIG.calibration) return;
  KRIG.calibrationId = null;
  KRIG.calibrationTaskId = null;
  KRIG.calibrationSignature = null;
  KRIG.calibration = null;
  const panel = document.getElementById('krig3-calibration-panel');
  if (panel) {
    panel.style.display = '';
    panel.innerHTML = `<span class="text-muted" style="font-size:.75rem">La calibración anterior quedó invalidada${reason ? `: ${reason}` : ''}. Se recalculará al generar los mapas.</span>`;
  }
}

function krig3RenderCalibration(calibration) {
  const panel = document.getElementById('krig3-calibration-panel');
  if (!panel || !calibration) return;
  const period = calibration.common_period || {};
  const weights = calibration.provider_weights || {};
  const nodes = calibration.nodes || [];
  const validation = calibration.provider_validation || {};
  nodes.forEach(node => {
    const site = KRIG.sites.find(candidate =>
      candidate.station_id === node.station_id || candidate.filename === node.station_id
    );
    if (!site) return;
    site.v_mean = Number(node.calibrated_mean_speed);
    site.v_max = Number(node.gust_p995);
    site.gust_ms = Number(node.gust_p995);
    site.weibull_C = Number(node.weibull_lambda);
    site.weibull_K = Number(node.weibull_k);
    site.rho_mean = Number(node.air_density_mean);
    site.power_density_wm2 = Number(node.power_density_wm2);
    site.coverage_pct = Number(node.coverage_pct);
    site.qc_status = node.qc_status;
  });
  const rows = nodes.map(node => `<tr>
    <td>${node.display_name || node.station_id}</td>
    <td>${node.provider}</td>
    <td>${Number(node.raw_mean_speed).toFixed(2)}</td>
    <td>${Number(node.calibrated_mean_speed).toFixed(2)}</td>
    <td>${Number(node.generalized_mean_speed).toFixed(2)}</td>
    <td>${Number(node.coverage_pct).toFixed(1)}%</td>
    <td>${node.qc_status}</td>
  </tr>`).join('');
  const validationText = Object.entries(validation).map(([provider, row]) => {
    const rmse = row.rmse_monthly_balanced;
    return `${provider}: ${rmse == null ? 'sin validación' : `RMSE ${Number(rmse).toFixed(3)} m/s`} · meses ${(row.months_used || []).join(', ') || '—'}`;
  }).join('<br>');
  panel.style.display = '';
  panel.innerHTML = `
    <div style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-bottom:.45rem">
      <b style="font-size:.8rem">Calibración espacial anclada</b>
      <span class="badge badge--success">${period.start_year || '—'}–${period.end_year || '—'} · ${period.n_years || 0} años</span>
      <span class="badge badge--info">ERA5 ${(100 * Number(weights.ERA5 || 0)).toFixed(1)}%</span>
      <span class="badge badge--info">MERRA-2 ${(100 * Number(weights.MERRA2 || 0)).toFixed(1)}%</span>
      <span class="text-muted" style="font-size:.72rem">${mcpEsc(calibration.anchor?.display_name || WP.activeDataSource)} permanece como referencia; los nodos conservan sus gradientes.</span>
    </div>
    <div class="text-muted" style="font-size:.72rem;margin-bottom:.45rem">${validationText}</div>
    <div style="overflow:auto;max-height:220px"><table class="data-table" style="font-size:.7rem">
      <thead><tr><th>Nodo</th><th>Proveedor</th><th>Raw m/s</th><th>Calibrado m/s</th><th>Generalizado m/s</th><th>Cobertura</th><th>QC</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  krig3RenderOptimalList();
}

async function krig3WaitForTask(taskId, onProgress) {
  while (true) {
    const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`, 'GET');
    if (onProgress) onProgress(task);
    if (task.status === 'done') return task.result;
    if (task.status === 'error') throw new Error(task.message || 'La tarea falló');
    await new Promise(resolve => setTimeout(resolve, 800));
  }
}

async function krig3EnsureCalibration() {
  const signature = krig3CalibrationKey();
  const windEpoch = WP.windSourceEpoch || 0;
  const spatialEpoch = KRIG.spatialEpoch;
  if (KRIG.calibrationId && KRIG.calibrationSignature === signature) return KRIG.calibrationId;
  if (!projectHasSpatialSource()) {
    throw new Error('Selecciona una fuente de viento antes de entrar al flujo guiado de Diseño.');
  }
  const references = KRIG.sites.filter(s => s.allowed_role === 'spatial_reference');
  const excluded = references.filter(s => !KRIG.selected.has(s.filename)).map(s => s.station_id || s.filename);
  const studyPoi = KRIG.poi || (typeof GLOBE !== 'undefined' && GLOBE?.poi) || { lat: 16.54701944, lon: -94.95578611 };
  const body = {
    session_id: WP.sessionId,
    data_source: WP.activeDataSource,
    poi: { lat: Number(studyPoi.lat), lon: Number(studyPoi.lon) },
    radius_km: Number(document.getElementById('krig3-poi-radius')?.value || 200),
    target_height_m: Number(wsH()),
    excluded_station_ids: excluded,
    method: 'anchored_spatial_anomaly_v1',
  };
  document.getElementById('krig3-progress-wrap').style.display = '';
  const started = await api('/api/kriging/calibrations', 'POST', body);
  KRIG.calibrationTaskId = started.task_id;
  const summary = await krig3WaitForTask(started.task_id, task => {
    document.getElementById('krig3-progress-fill').style.width = `${task.progress || 0}%`;
    document.getElementById('krig3-progress-text').textContent = task.message || '';
    document.getElementById('krig3-status').textContent = 'Calibrando nodos…';
  });
  if (windEpoch !== (WP.windSourceEpoch || 0) || spatialEpoch !== KRIG.spatialEpoch) {
    throw new Error('La fuente o las estaciones cambiaron durante la calibración.');
  }
  KRIG.calibrationId = summary.calibration_id;
  KRIG.calibrationSignature = signature;
  const calibration = await api(`/api/kriging/calibrations/${encodeURIComponent(KRIG.calibrationId)}?session_id=${encodeURIComponent(WP.sessionId)}`, 'GET');
  if (windEpoch !== (WP.windSourceEpoch || 0) || spatialEpoch !== KRIG.spatialEpoch) throw new Error('Cambió la selección espacial.');
  KRIG.calibration = calibration;
  krig3RenderCalibration(KRIG.calibration);
  return KRIG.calibrationId;
}

// ── Cargar todos los sitios y dibujarlos sobre el mapa de México ──────────────
async function krig3LoadSites(force=false) {
  if (KRIG.sites.length && !force) return;
  const map = krig3InitMap();
  try {
    const manual = !!document.getElementById('krig3-manual-mode')?.checked;
    const advancedRequested = krig3AdvancedMcpRequested();
    KRIG.guidedMcp = projectHasSpatialSource() && advancedRequested;
    const sourceBadge = document.getElementById('krig3-source-badge');
    if (advancedRequested && !KRIG.guidedMcp) {
      KRIG.sites = [];
      KRIG.selected.clear();
      if (sourceBadge) {
        sourceBadge.textContent = 'Falta seleccionar fuente';
        sourceBadge.className = 'badge badge--warning';
      }
      throw new Error('Selecciona una fuente de viento para usar el cálculo espacial anclado.');
    }
    const year = parseInt(document.getElementById('krig3-year').value) || 2018;
    const htParam = WP.referenceHeight ? `&target_height_m=${WP.referenceHeight}` : '';
    let url;
    if (KRIG.guidedMcp) {
      const query = new URLSearchParams({
        session_id: WP.sessionId,
        data_source: WP.activeDataSource, anchored: '1',
        radius_km: String(Number(document.getElementById('krig3-poi-radius')?.value || 200)),
      });
      if (KRIG.poi) {
        query.set('poi_lat', String(KRIG.poi.lat));
        query.set('poi_lon', String(KRIG.poi.lon));
      }
      url = `/api/kriging/sites?${query.toString()}`;
    } else {
      url = `/api/kriging/sites?year=${year}${htParam}`;
    }
    const r = await api(url, 'GET');
    KRIG.sites = r.sites || [];
    if (WP.studyMode === 'reanalysis_only' && !KRIG.guidedMcp) {
      const allowedFiles = new Set();
      (WP.reanalysisStudy?.reference_stations || []).forEach(station => {
        if (station.primary_file_id) allowedFiles.add(station.primary_file_id);
        (station.file_ids || []).forEach(value => allowedFiles.add(value));
      });
      KRIG.sites = KRIG.sites.filter(site =>
        ['era5', 'merra2'].includes(String(site.source || '').toLowerCase())
        && (!allowedFiles.size || allowedFiles.has(site.filename))
      );
      if (KRIG.sites.length < 3) {
        throw new Error('El estudio de reanálisis no tiene tres series compatibles disponibles para Diseño.');
      }
    }
    KRIG.selected = new Set(KRIG.sites.filter(s => s.allowed_role !== 'anchor').map(s => s.filename));
    KRIG.principal = KRIG.sites.find(s => s.allowed_role === 'anchor')?.filename || null;
    krig3InvalidateCalibration('se recargó el catálogo espacial');
    if (sourceBadge) {
      sourceBadge.textContent = KRIG.guidedMcp
        ? `Activo · ${WP.activeDataSource}`
        : (WP.studyMode === 'reanalysis_only'
          ? 'ERA5/MERRA-2 del área · sin MCP'
          : (manual ? 'Archivos sin calibración MCP' : 'WindProPlatform clásico'));
      sourceBadge.className = `badge ${KRIG.guidedMcp ? 'badge--success' : 'badge--warning'}`;
    }
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
  const deltaScale = key === 'wind_calibration_delta'
    ? Math.max(Math.abs(Number(z_min)), Math.abs(Number(z_max)), 1e-9)
    : null;
  for (let j = 0; j < ny; j++) {
    const row = z[ny - 1 - j];
    for (let i = 0; i < nx; i++) {
      const t = deltaScale != null
        ? (Number(row[i]) + deltaScale) / (2 * deltaScale)
        : (rng > 0 ? (row[i] - z_min) / rng : 0.5);
      let r_, g_, b_;
      if (key === 'wind_direction') {
        const rgb = krigHslToRgb((((Number(row[i]) % 360) + 360) % 360) / 360, .72, .52);
        [r_, g_, b_] = rgb;
      } else if (key === 'wind_calibration_delta') {
        [r_, g_, b_] = krigDeltaColor(t);
      } else {
        [r_, g_, b_] = krigColor(t);
      }
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

  if (key === 'wind_direction' && Array.isArray(KRIG.result.direction_vectors)) {
    KRIG.result.direction_vectors.forEach(vector => {
      const bearing = ((Number(vector.direction_deg) % 360) + 360) % 360;
      const icon = L.divIcon({
        className: 'krig3-direction-vector',
        html: `<div title="Viento desde ${bearing.toFixed(0)}° · ${Number(vector.speed_ms).toFixed(2)} m/s" style="width:18px;height:18px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:17px;font-weight:800;text-shadow:0 1px 3px #000;transform:rotate(${bearing}deg)">↑</div>`,
        iconSize: [18, 18], iconAnchor: [9, 9],
      });
      const marker = L.marker([vector.lat, vector.lon], { icon, interactive: false }).addTo(map);
      KRIG.overlays.push(marker);
    });
  }

  krig3RenderPoiOnResult();

  document.getElementById('krig3-map-title').textContent = grid.title || meta.title;
  document.getElementById('krig3-legend').textContent = key === 'wind_calibration_delta'
    ? `${(-deltaScale).toFixed(2)} → 0 → ${deltaScale.toFixed(2)} ${meta.unit}`
    : krig3FormatRange(grid, meta.unit);
  setTimeout(() => map.invalidateSize(), 60);
}

function krigHslToRgb(h, s, l) {
  const hue = (p, q, t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1/6) return p + (q - p) * 6 * t;
    if (t < 1/2) return q;
    if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
    return p;
  };
  const q = l < .5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return [hue(p, q, h + 1/3), hue(p, q, h), hue(p, q, h - 1/3)].map(v => Math.round(v * 255));
}

// ── Auto-zoom alrededor del parque principal tras correr Kriging ──────────────
// Si no hay parque principal, encuadra al extent del grid para que el overlay sea visible.
function krig3ZoomToPrincipal() {
  if (!KRIG.map) return;
  // El contenedor puede seguir en display:none si la pestaña aún no está
  // activa (p.ej. justo tras restaurar un proyecto importado); sin esto
  // fitBounds calcula sobre un mapa de tamaño 0 y puede fallar o quedar mal
  // encuadrado incluso con un bounds válido.
  KRIG.map.invalidateSize();
  if (KRIG.areaBounds && typeof KRIG.areaBounds.isValid === 'function' && KRIG.areaBounds.isValid()) {
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
    o.textContent = `${c.name}  ·  ${c.p_nom_kw} kW  ·  Ø ${c.rotor_m} m`;
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
  if (meta) meta.textContent = `Pnom ${c.p_nom_kw} kW · rotor Ø ${c.rotor_m} m`;

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
  if (KRIG.task_id) {
    toast('Ya hay un Kriging ejecutándose. Espera a que termine.', 'warning');
    return;
  }
  const advancedRequested = krig3AdvancedMcpRequested();
  KRIG.guidedMcp = projectHasSpatialSource() && advancedRequested;
  if (advancedRequested && !KRIG.guidedMcp) {
    toast('Selecciona una fuente de viento para usar el cálculo espacial anclado.', 'error');
    return;
  }
  const principal = KRIG.sites.find(x => x.filename === KRIG.principal);
  const physical = !!document.getElementById('krig3-physical')?.checked;
  const marketInputs = parkEquivalentDesignInputs();
  const projectYears = Number(WP.parkConfiguration?.project_horizon_years);
  if (!Number.isFinite(projectYears) || projectYears < 5 ||
      !Number.isFinite(marketInputs.price_usd_kwh)) {
    toast('Guarda la vida del proyecto y carga el archivo de precios antes de ejecutar Kriging.', 'error');
    return;
  }
  const body = {
    year:             parseInt(document.getElementById('krig3-year').value),
    demand_mwh:       marketInputs.demand_mwh ?? parseFloat(document.getElementById('krig3-demand').value),
    tasa:             marketInputs.tasa ?? parseFloat(document.getElementById('krig3-tasa').value) / 100,
    price_usd_kwh:    marketInputs.price_usd_kwh,
    project_years:    projectYears,
    price_mxn_per_usd: marketInputs.price_mxn_per_usd ?? parseFloat(document.getElementById('krig3-fx').value),
    market_trajectories: marketInputs.trajectories,
    n_grid:           parseInt(document.getElementById('krig3-n').value),
    // La vista clásica conserva el selector exponencial/gaussiano. En el flujo
    // MCP guiado la selección del variograma continúa siendo automática (LOO).
    model:            KRIG.guidedMcp ? 'auto' : document.getElementById('krig3-model').value,
    filenames:        Array.from(KRIG.selected),
    physical:         physical,
    map_method:       KRIG.guidedMcp ? 'mcp_advanced' : 'windpro_classic',
    target_height_m:  wsH(),
  };
  if (singleStationMode() || WP.studyMode === 'reanalysis_only') {
    body.session_id = WP.sessionId;
    body.data_source = WP.activeDataSource;
  }
  if (KRIG.guidedMcp) {
    body.session_id = WP.sessionId;
    body.data_source = WP.activeDataSource;
    if (KRIG.orographicSpeedupGrid) body.orographic_speedup_grid = KRIG.orographicSpeedupGrid;
  }
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
  const runBtn = document.getElementById('btn-krig3-run');
  if (runBtn) runBtn.disabled = true;
  try {
    if (KRIG.guidedMcp) {
      body.calibration_id = await krig3EnsureCalibration();
      document.getElementById('krig3-progress-text').textContent = 'Generando campos ERA5, MERRA-2 y ensamble…';
    }
    const spatialEpoch = KRIG.spatialEpoch;
    const r = await api('/api/kriging/legacy_maps', 'POST', body);
    if (spatialEpoch !== KRIG.spatialEpoch) return;
    KRIG.task_id = r.task_id;
    krig3Poll();
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('krig3-status').textContent = 'Error';
    document.getElementById('krig3-progress-wrap').style.display = 'none';
    document.getElementById('krig3-progress-text').textContent = '';
    if (runBtn) runBtn.disabled = false;
  }
}

async function krig3Poll() {
  if (!KRIG.task_id) return;
  const taskId = KRIG.task_id;
  const spatialEpoch = KRIG.spatialEpoch;
  try {
    const r = await api(`/api/kriging/status/${taskId}`, 'GET');
    if (taskId !== KRIG.task_id || spatialEpoch !== KRIG.spatialEpoch) return;
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
      const mcpOn = !!d.calibration_id;
      document.querySelectorAll('.krig3-tab-mcp').forEach(b => {
        b.style.display = mcpOn && d[b.dataset.layer] ? '' : 'none';
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
      const tag = mcpOn
        ? ` · cálculo espacial anclado · ${Object.entries(d.provider_weights || {}).map(([p,w]) => `${p} ${(100*w).toFixed(0)}%`).join(' / ')} · comparación raw @ ${Number(d.raw_reference?.reference_height_m || 100).toFixed(0)} m`
        : (physOn ? ` · física (${d.n_corrected || 0}/${d.n_sites})` : '');
      const nMaps = Object.keys(d).filter(k => d[k] && Array.isArray(d[k].z)).length;
      document.getElementById('krig3-status').textContent =
        `${d.n_sites} sitios · ${nMaps} mapas · ${nCurves} curvas${tag}`;
      const btnReset = document.getElementById('btn-krig3-reset');
      if (btnReset) btnReset.style.display = '';
      document.getElementById('krig3-progress-fill').style.width = '100%';
      KRIG.task_id = null;
      const runBtn = document.getElementById('btn-krig3-run');
      if (runBtn) runBtn.disabled = false;
      toast(mcpOn ? 'Kriging MCP físico-direccional listo' : (physOn ? 'Kriging físico listo' : 'Kriging listo'), 'success');
    } else if (r.status === 'error') {
      toast(`Kriging falló: ${r.message}`, 'error');
      document.getElementById('krig3-status').textContent = 'Error';
      KRIG.task_id = null;
      const runBtn = document.getElementById('btn-krig3-run');
      if (runBtn) runBtn.disabled = false;
    } else {
      setTimeout(krig3Poll, 800);
    }
  } catch (e) {
    toast(`Polling: ${e.message}`, 'error');
    KRIG.task_id = null;
    const runBtn = document.getElementById('btn-krig3-run');
    if (runBtn) runBtn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const bRun = document.getElementById('btn-krig3-run');
  if (bRun) bRun.addEventListener('click', krig3Run);
  krig3SyncGenerationMode();

  clusterLoadTurbines();
  document.getElementById('btn-cluster-run')?.addEventListener('click', runTurbineClustering);
  document.getElementById('btn-cluster-turbines-all')?.addEventListener('click', () => {
    document.querySelectorAll('.cluster-turbine-check').forEach(el => { el.checked = true; });
    clusterUpdateTurbineCount();
  });
  document.getElementById('btn-cluster-turbines-none')?.addEventListener('click', () => {
    document.querySelectorAll('.cluster-turbine-check').forEach(el => { el.checked = false; });
    clusterUpdateTurbineCount();
  });
  document.getElementById('btn-cluster-apply')?.addEventListener('click', () => {
    if (!CLUSTER.result) return;
    // Selección 100% manual: se lleva a TOPSIS exactamente lo que el usuario dejó
    // marcado en la lista por clúster, sin priorizar por CF ni ningún otro criterio.
    const filenames = Array.from(document.querySelectorAll('.cluster-apply-check:checked')).map(el => el.value);
    if (!filenames.length) { toast('Marca al menos una turbina para llevar a TOPSIS.', 'warning'); return; }
    CLUSTER.appliedFilenames = filenames;
    applyClusterSelectionToTopsis(filenames);
  });

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
    KRIG.selected = new Set(KRIG.sites.filter(s => s.allowed_role !== 'anchor').map(s => s.filename));
    krig3InvalidateCalibration('se incluyeron todos los nodos');
    krig3UpdateAllMarkers();
  });
  const bNone = document.getElementById('btn-krig3-select-none');
  if (bNone) bNone.addEventListener('click', () => {
    KRIG.selected.clear();
    KRIG.principal = KRIG.sites.find(s => s.allowed_role === 'anchor')?.filename || null;
    krig3InvalidateCalibration('se excluyeron los nodos');
    krig3UpdateAllMarkers();
  });

  // Si cambia el año, recargar sitios (n_hours puede variar)
  const yearSel = document.getElementById('krig3-year');
  if (yearSel) yearSel.addEventListener('change', () => krig3LoadSites(true));
  document.getElementById('krig3-generation-mode')?.addEventListener('change', () => {
    krig3SyncGenerationMode();
    krig3InvalidateCalibration('cambió el método de generación');
    krig3LoadSites(true);
  });
  document.getElementById('krig3-manual-mode')?.addEventListener('change', () => {
    krig3InvalidateCalibration('cambió el modo de cálculo');
    krig3LoadSites(true);
  });

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
          if (s.allowed_role !== 'anchor' && b.contains(L.latLng(s.lat, s.lon))) {
            KRIG.selected.add(s.filename);
            toggled++;
          }
        });
        if (toggled) {
          krig3InvalidateCalibration('cambió la selección rectangular');
          krig3UpdateAllMarkers(); toast(`${toggled} sitios seleccionados`, 'info');
        }
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
  document.getElementById('krig3-poi-radius')     ?.addEventListener('change', () => {
    krig3InvalidateCalibration('cambió el radio');
    if (KRIG.guidedMcp) krig3LoadSites(true);
    else krig3FilterByRadius();
  });
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
  document.getElementById('krig3-speedup-file')?.addEventListener('change', async (event) => {
    const status = document.getElementById('krig3-speedup-status');
    const file = event.target.files?.[0];
    KRIG.orographicSpeedupGrid = null;
    if (!file) {
      if (status) status.textContent = 'Sin capa externa: el DEM afecta la rugosidad de respaldo, pero no aplica aceleración orográfica directa.';
      return;
    }
    try {
      const parsed = JSON.parse(await file.text());
      const grid = Array.isArray(parsed) ? parsed : parsed.speedup_grid;
      if (!Array.isArray(grid) || !grid.length || !grid.every(row => Array.isArray(row))) {
        throw new Error('se esperaba una matriz JSON o {speedup_grid: [[...]]}');
      }
      KRIG.orographicSpeedupGrid = grid;
      if (status) status.textContent = `${file.name} · ${grid.length}×${grid[0]?.length || 0} · se validará contra la grilla al ejecutar`;
      krig3InvalidateCalibration('cambió la capa WAsP/CFD importada');
    } catch (error) {
      event.target.value = '';
      if (status) status.textContent = `Archivo no válido: ${error.message}`;
      toast(`Capa WAsP/CFD no válida: ${error.message}`, 'error');
    }
  });

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
// ─── CLUSTERING JERÁRQUICO DE AEROGENERADORES (ML, antes de TOPSIS) ─────────
const CLUSTER = {
  result: null,      // respuesta cruda de /api/mcda/turbine_clustering
  k: 3,
  linkageMethod: 'complete',
  appliedFilenames: null,
};

function clusterUpdateTurbineCount() {
  const total = document.querySelectorAll('.cluster-turbine-check').length;
  const selected = document.querySelectorAll('.cluster-turbine-check:checked').length;
  const el = document.getElementById('cluster-turbine-count');
  if (el) el.textContent = total ? `${selected}/${total} seleccionadas` : 'Sin catálogo';
}

/** Catálogo propio del panel de clustering — independiente de la lista de TOPSIS,
 * para que el usuario defina explícitamente qué turbinas entran al estudio de
 * similitud (dendrograma/matriz), sin tener que coincidir con lo que ya esté
 * marcado más abajo para TOPSIS. */
async function clusterLoadTurbines() {
  const list = document.getElementById('cluster-turbine-list');
  if (!list) return;
  try {
    const r = await api('/api/energy/turbines', 'GET');
    const turbines = (r.turbines || []).filter(t => t.source === 'file');
    if (!turbines.length) {
      list.innerHTML = '<span class="text-muted">No hay turbinas CSV disponibles.</span>';
      clusterUpdateTurbineCount();
      return;
    }
    list.innerHTML = turbines.map(t => {
      const mw = t.rated_power_kw ? `${(t.rated_power_kw / 1000).toFixed(1)} MW` : 'Potencia —';
      const rotor = t.rotor_diameter_m ? `Ø ${t.rotor_diameter_m} m` : 'Rotor —';
      return `
        <label style="display:flex;align-items:flex-start;gap:.45rem;padding:.28rem 0;border-bottom:1px solid var(--border)">
          <input type="checkbox" class="cluster-turbine-check" value="${t.filename}" checked style="margin-top:.15rem">
          <span>${t.name} <span class="text-muted">· ${mw} · ${rotor}</span></span>
        </label>`;
    }).join('');
    document.querySelectorAll('.cluster-turbine-check').forEach(el => {
      el.addEventListener('change', clusterUpdateTurbineCount);
    });
    clusterUpdateTurbineCount();
  } catch (e) {
    list.innerHTML = '<span class="text-muted">No se pudo cargar el catálogo.</span>';
  }
}

async function runTurbineClustering() {
  if (!WP.sessionId) { toast('Carga datos primero', 'warn'); return; }
  const linkageMethod = document.getElementById('cluster-linkage')?.value || 'complete';
  CLUSTER.linkageMethod = linkageMethod;
  const turbineFilenames = Array.from(document.querySelectorAll('.cluster-turbine-check:checked')).map(el => el.value);
  if (turbineFilenames.length < 2) {
    toast('Selecciona al menos 2 turbinas para poder agruparlas.', 'warning');
    return;
  }
  document.getElementById('cluster-status').textContent = 'Ejecutando…';
  document.getElementById('cluster-progress-wrap').style.display = '';
  document.getElementById('cluster-progress-text').textContent =
    'Generando series de potencia por turbina e interpolando contra el viento del sitio…';
  const btn = document.getElementById('btn-cluster-run');
  if (btn) btn.disabled = true;
  try {
    const body = {
      session_id: WP.sessionId,
      data_source: WP.activeDataSource,
      linkage_method: linkageMethod,
      ws_height_m: wsH(),
      turbine_filenames: turbineFilenames,
    };
    const r = await api('/api/mcda/turbine_clustering', 'POST', body);
    CLUSTER.result = r;
    document.getElementById('cluster-progress-wrap').style.display = 'none';
    document.getElementById('cluster-progress-text').textContent = '';
    document.getElementById('cluster-status').textContent =
      `${r.turbines.length} turbinas · ${linkageMethod}`;
    renderClusterResult();
    toast('Clustering completado', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('cluster-status').textContent = 'Error';
    document.getElementById('cluster-progress-wrap').style.display = 'none';
    document.getElementById('cluster-progress-text').textContent = '';
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** Corta un árbol de linkage (formato scipy: [idxA, idxB, dist, count] por fila)
 * en k clusters — equivalente a scipy.cluster.hierarchy.fcluster(criterion='maxclust').
 * Devuelve un array de longitud n (n = número de hojas) con el id de cluster (0..k-1)
 * de cada hoja, en el mismo orden que `labels`. */
function cutTreeByK(linkage, k) {
  const n = linkage.length + 1;
  k = Math.max(1, Math.min(k, n));
  // parent[i] = nodo (0..2n-2) al que pertenece la hoja/cluster i mientras se recorren
  // las fusiones en orden ascendente de distancia; nos detenemos cuando quedan k grupos.
  const parent = new Array(2 * n - 1).fill(-1);
  const active = new Set(Array.from({ length: n }, (_, i) => i));
  for (let row = 0; row < linkage.length && active.size > k; row++) {
    const a = Math.round(linkage[row][0]);
    const b = Math.round(linkage[row][1]);
    const merged = n + row;
    active.delete(a); active.delete(b); active.add(merged);
    parent[a] = merged; parent[b] = merged;
  }
  // Para cada hoja, sube por parent[] hasta el nodo que quedó activo (raíz de su grupo).
  const rootIds = new Map();
  const labelsOut = new Array(n);
  for (let leaf = 0; leaf < n; leaf++) {
    let cur = leaf;
    while (!active.has(cur)) cur = parent[cur];
    if (!rootIds.has(cur)) rootIds.set(cur, rootIds.size);
    labelsOut[leaf] = rootIds.get(cur);
  }
  return labelsOut;
}

const CLUSTER_COLORS = ['#4DA3FF', '#9F7AEA', '#22c55e', '#f59e0b', '#F4F7FB', '#ec4899', '#14b8a6', '#eab308'];

function renderClusterResult() {
  const r = CLUSTER.result;
  const wrap = document.getElementById('cluster-results');
  if (!r || !wrap) return;
  wrap.style.display = '';
  const kInput = document.getElementById('cluster-k');
  const k = Math.max(2, Math.min(parseInt(kInput?.value) || CLUSTER.k, r.labels.length - 1 || 2));
  CLUSTER.k = k;
  const clusterOf = cutTreeByK(r.linkage, k);

  renderDendrogramSvg(document.getElementById('cluster-dendrogram'), r.linkage, r.labels, clusterOf, r.turbines);
  renderClusterDistanceTable(r.distance_matrix, r.labels);
  renderClusterSummaryTable(r.turbines, clusterOf);
  renderClusterRepresentatives(r.turbines, clusterOf);

  if (!kInput._wired) {
    kInput._wired = true;
    kInput.addEventListener('change', renderClusterResult);
  }
  const applyBtn = document.getElementById('btn-cluster-apply');
  if (applyBtn) applyBtn.style.display = '';
}

/** Lista de selección 100% manual por clúster — no sugiere ningún representante
 * (ni por CF, ni por AEP, ni por ningún otro criterio): el clustering solo aporta
 * la agrupación por similitud de comportamiento; decidir a quién de cada grupo
 * llevar a TOPSIS es una decisión del investigador, no del algoritmo. Todas las
 * turbinas quedan marcadas por defecto (no se descarta nada sin que el usuario
 * lo decida explícitamente). */
function renderClusterRepresentatives(turbines, clusterOf) {
  const el = document.getElementById('cluster-reps');
  if (!el) return;
  const byCluster = new Map();
  turbines.forEach((t, i) => {
    const c = clusterOf[i];
    if (!byCluster.has(c)) byCluster.set(c, []);
    byCluster.get(c).push(t);
  });
  el.innerHTML = Array.from(byCluster.entries()).sort((a, b) => a[0] - b[0]).map(([c, members]) => {
    const color = CLUSTER_COLORS[c % CLUSTER_COLORS.length];
    const rows = members.map(m => `
      <label style="display:flex;align-items:center;gap:.4rem;padding:.15rem 0 .15rem 1.1rem;font-size:.78rem">
        <input type="checkbox" class="cluster-apply-check" value="${m.filename}" checked>
        <span>${m.name}</span>
        <span class="text-muted" style="font-size:.72rem">CF ${m.capacity_factor_pct != null ? m.capacity_factor_pct.toFixed(1) + '%' : '—'} · AEP ${m.aep_mwh != null ? Math.round(m.aep_mwh).toLocaleString() + ' MWh' : '—'}</span>
      </label>`).join('');
    return `<div style="margin-bottom:.5rem">
      <div style="font-weight:600"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${color};margin-right:6px;vertical-align:middle"></span>Clúster ${c + 1} (${members.length})</div>
      ${rows}
    </div>`;
  }).join('');
}

function renderClusterDistanceTable(distMatrix, labels) {
  const el = document.getElementById('cluster-distance-table');
  if (!el) return;
  const names = labels.map(f => (MCDA.turbines || []).find(t => t.filename === f)?.name || f.replace('.csv', ''));
  const maxD = Math.max(...distMatrix.flat().filter(v => Number.isFinite(v)), 1e-9);
  const cellColor = (v) => {
    const t = Math.max(0, Math.min(1, v / maxD));
    const alpha = Math.round(t * 200).toString(16).padStart(2, '0');
    return `#4DA3FF${alpha}`;
  };
  const header = `<tr><th></th>${names.map(n => `<th style="font-size:.68rem;white-space:nowrap">${n}</th>`).join('')}</tr>`;
  const rows = distMatrix.map((row, i) => `<tr><th style="font-size:.68rem;white-space:nowrap;text-align:left">${names[i]}</th>${
    row.map(v => `<td style="background:${cellColor(v)};text-align:center;font-size:.66rem;padding:.2rem .35rem">${v.toFixed(3)}</td>`).join('')
  }</tr>`).join('');
  el.innerHTML = `<table class="data-table" style="border-collapse:collapse">${header}${rows}</table>`;
}

function renderClusterSummaryTable(turbines, clusterOf) {
  const tbody = document.getElementById('cluster-summary-tbody');
  if (!tbody) return;
  tbody.innerHTML = turbines.map((t, i) => {
    const color = CLUSTER_COLORS[clusterOf[i] % CLUSTER_COLORS.length];
    const dot = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:5px;vertical-align:middle"></span>`;
    return `<tr>
      <td>${dot}${t.name}</td>
      <td>${t.rated_power_kw ?? '—'}</td>
      <td>${t.rotor_diameter_m ?? '—'}</td>
      <td>${t.hub_height_m ?? '—'}</td>
      <td>${t.capacity_factor_pct != null ? t.capacity_factor_pct.toFixed(1) : '—'}</td>
      <td>${t.aep_mwh != null ? Math.round(t.aep_mwh).toLocaleString() : '—'}</td>
    </tr>`;
  }).join('');
}

/** Dibuja un dendrograma horizontal en SVG a partir de una matriz de linkage estilo
 * scipy ([idxA, idxB, distancia, nHojas] por fila). No hay librería de dendrogramas
 * en el proyecto — todo lo demás usa Chart.js/Leaflet — así que se dibuja a mano. */
function renderDendrogramSvg(svg, linkageMatrix, labels, clusterOf, turbines) {
  if (!svg) return;
  const n = labels.length;
  const names = labels.map(f => (turbines || []).find(t => t.filename === f)?.name || f.replace('.csv', ''));
  const margin = { top: 10, right: 140, bottom: 30, left: 10 };
  const width = Math.max(560, svg.parentElement?.clientWidth || 560) - margin.left - margin.right;
  const rowH = 26;
  const height = n * rowH;
  svg.setAttribute('viewBox', `0 0 ${width + margin.left + margin.right} ${height + margin.top + margin.bottom}`);
  svg.setAttribute('height', height + margin.top + margin.bottom);

  // Posición y de cada hoja = su índice en el orden dado (0..n-1), separadas rowH.
  // Posición x de cada nodo = distancia de fusión (0 para hojas).
  const maxDist = Math.max(...linkageMatrix.map(r => r[2]), 1e-9);
  const xScale = (d) => (d / maxDist) * (width - 20);
  const yOf = new Array(2 * n - 1);
  for (let i = 0; i < n; i++) yOf[i] = margin.top + i * rowH + rowH / 2;
  const xOf = new Array(2 * n - 1).fill(0);

  const parts = [];
  // Orden de hojas: se recalcula recorriendo el árbol para que las hojas de un mismo
  // clúster queden contiguas (mejor lectura que el orden crudo de entrada).
  const leafOrder = [];
  const collectLeaves = (node) => {
    if (node < n) { leafOrder.push(node); return; }
    const row = linkageMatrix[node - n];
    collectLeaves(Math.round(row[0]));
    collectLeaves(Math.round(row[1]));
  };
  collectLeaves(2 * n - 2);
  leafOrder.forEach((leaf, pos) => { yOf[leaf] = margin.top + pos * rowH + rowH / 2; });

  // Color por nodo: las hojas heredan su clúster (clusterOf solo cubre hojas); un nodo
  // interno conserva el color solo si AMBOS hijos son del mismo clúster — si no, es un
  // tramo "troncal" entre clústeres distintos y se dibuja en gris neutro.
  const nodeCluster = new Array(2 * n - 1);
  for (let i = 0; i < n; i++) nodeCluster[i] = clusterOf[i];

  linkageMatrix.forEach((row, i) => {
    const a = Math.round(row[0]), b = Math.round(row[1]), dist = row[2];
    const node = n + i;
    xOf[node] = xScale(dist);
    yOf[node] = (yOf[a] + yOf[b]) / 2;
    nodeCluster[node] = nodeCluster[a] === nodeCluster[b] ? nodeCluster[a] : -1;
    const color = nodeCluster[node] >= 0 ? CLUSTER_COLORS[nodeCluster[node] % CLUSTER_COLORS.length] : 'var(--text-muted)';
    parts.push(
      `<path d="M${margin.left + xOf[a]},${yOf[a]} H${margin.left + xOf[node]} V${yOf[b]} H${margin.left + xOf[b]}" fill="none" stroke="${color}" stroke-width="1.6"/>`
    );
  });

  leafOrder.forEach((leaf) => {
    const color = CLUSTER_COLORS[clusterOf[leaf] % CLUSTER_COLORS.length];
    parts.push(`<circle cx="${margin.left + xOf[leaf]}" cy="${yOf[leaf]}" r="3" fill="${color}"/>`);
    parts.push(`<text x="${margin.left + width + 8}" y="${yOf[leaf] + 4}" font-size="11" fill="var(--text-secondary)">${names[leaf]}</text>`);
    parts.push(`<line x1="${margin.left + xOf[leaf]}" y1="${yOf[leaf]}" x2="${margin.left + width + 4}" y2="${yOf[leaf]}" stroke="var(--border)" stroke-width="1" stroke-dasharray="1,3"/>`);
  });

  svg.innerHTML = parts.join('');
}

/** Marca/desmarca los checkboxes de turbinas de TOPSIS (.mcda-turbine-check) según
 * la lista de filenames dada — así el clustering "alimenta" a TOPSIS sin duplicar
 * el estado de selección de turbinas. */
function applyClusterSelectionToTopsis(filenames) {
  const wanted = new Set(filenames);
  document.querySelectorAll('.mcda-turbine-check').forEach(el => {
    el.checked = wanted.has(el.value);
  });
  if (typeof mcdaUpdateTurbineCount === 'function') mcdaUpdateTurbineCount();
  toast(`${wanted.size} turbina(s) seleccionadas para TOPSIS desde el clustering`, 'success');
  document.getElementById('mcda-turbine-list')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

const MCDA = {
  taskId: null,
  lastAnalysis: null,   // raw analyze_sites output
  lastRanking:  null,   // last TOPSIS result
  lastSpatial:  null,
  defaults:     null,
  layoutWeights: null,
  ahpDraft: null, ahpResult: null, ahpApplied: null, weightSource: 'reference',
  scope:        'area',
  miniMap:      null,
  miniBase:     null,
  miniLayers:   [],
  turbines:     [],
};

function mcdaActiveAhp() {
  const a = MCDA.ahpApplied;
  const keys = Object.keys(MCDA.defaults?.layout_weights || {});
  return MCDA.scope === 'area' && MCDA.weightSource === 'ahp' && a?.consistent
    && a.schema === 'layout-ahp-v1' && a.criteria?.length === keys.length
    && keys.every(key => a.criteria.includes(key)
      && Math.abs(a.weights[key] - MCDA.layoutWeights?.[key]) < 1e-12) ? a : null;
}

function mcdaAhpSourceLabel() {
  const el = document.getElementById('mcda-weight-source');
  if (el) el.textContent = mcdaActiveAhp() ? 'Pesos AHP individuales aplicados'
    : MCDA.weightSource === 'manual' && MCDA.scope === 'area' ? 'Escenario manual'
    : 'Escenario de referencia — sin elicitación AHP';
}

function mcdaAhpEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch]));
}

function mcdaAhpCapture() {
  const criteria = Object.keys(MCDA.defaults?.layout_weights || {});
  const pairs = {};
  document.querySelectorAll('.mcda-ahp-pair').forEach(el => { pairs[el.dataset.pair] = el.value; });
  return {criteria, pairs, author: document.getElementById('mcda-ahp-author')?.value || '',
    objective: document.getElementById('mcda-ahp-objective')?.value || '',
    rationale: document.getElementById('mcda-ahp-rationale')?.value || ''};
}

function mcdaAhpEdited() {
  MCDA.ahpDraft = mcdaAhpCapture();
  MCDA.ahpResult = null;
  mcdaRenderAhpResult();
}

function mcdaRenderAhp() {
  const panel = document.getElementById('mcda-ahp-panel');
  if (!panel) return;
  panel.hidden = MCDA.scope !== 'area';
  mcdaAhpSourceLabel();
  if (panel.hidden) return;
  const keys = Object.keys(MCDA.defaults?.layout_weights || {});
  if (!keys.length) return;
  if (JSON.stringify(MCDA.ahpDraft?.criteria) !== JSON.stringify(keys)) {
    MCDA.ahpDraft = {criteria: keys, pairs: {}, author:'', objective:'', rationale:''};
    MCDA.ahpResult = null;
  }
  const draft = MCDA.ahpDraft;
  for (const key of ['author', 'objective', 'rationale'])
    document.getElementById(`mcda-ahp-${key}`).value = draft[key] || '';
  const labels = MCDA.defaults.layout_labels;
  const rows = [];
  for (let i = 0; i < keys.length; i++) for (let j = i + 1; j < keys.length; j++) {
    const pair = `${i}-${j}`;
    const options = ['<option value="">Sin responder</option>', '<option value="1">Igual importancia (1)</option>'];
    for (let value = 2; value <= 9; value++) {
      options.push(`<option value="${value}">A tiene mayor importancia: ${value}</option>`);
      options.push(`<option value="${1/value}">B tiene mayor importancia: ${value}</option>`);
    }
    rows.push(`<div style="border-bottom:1px solid var(--border);padding:.5rem 0">
      <label for="ahp-${pair}"><b>A:</b> ${mcdaAhpEscape(labels[keys[i]])}<br><b>B:</b> ${mcdaAhpEscape(labels[keys[j]])}</label>
      <select id="ahp-${pair}" class="form-select mcda-ahp-pair" data-pair="${pair}" onchange="mcdaAhpEdited()">${options.join('')}</select></div>`);
  }
  document.getElementById('mcda-ahp-pairs').innerHTML = rows.join('');
  document.querySelectorAll('.mcda-ahp-pair').forEach(el => { el.value = draft.pairs[el.dataset.pair] || ''; });
  mcdaRenderAhpResult();
}

function mcdaRenderAhpResult() {
  const out = document.getElementById('mcda-ahp-result');
  if (!out) return;
  const a = MCDA.ahpResult;
  document.getElementById('btn-mcda-ahp-apply').disabled = !a?.consistent;
  document.getElementById('btn-mcda-ahp-export').disabled = !a;
  if (!a) { out.textContent = 'Completa las 28 comparaciones, autor, objetivo y justificación. El borrador no modifica los pesos aplicados.'; return; }
  const labels = MCDA.defaults?.layout_labels || {};
  out.innerHTML = `<p><b>${a.consistent ? 'Consistencia aceptable' : 'Revisar juicios antes de aplicar'}</b> · CR ${a.cr.toFixed(4)} (límite ${a.cr_threshold}) · CI ${a.ci.toFixed(4)} · RI ${a.ri} · λmax ${a.lambda_max.toFixed(4)}</p>`
    + '<ul>' + Object.entries(a.weights).map(([key,w]) => `<li>${mcdaAhpEscape(labels[key] || key)}: ${(w*100).toFixed(3)}%</li>`).join('') + '</ul>'
    + (!a.consistent ? '<p>Revisa las relaciones entre estos criterios; no se modificaron tus respuestas:</p><ul>'
      + a.review_triads.map(t => `<li>${t.criteria.map(key => mcdaAhpEscape(labels[key] || key)).join(' / ')}</li>`).join('') + '</ul>' : '')
    + '<p>El CR evalúa coherencia interna. La pertinencia científica de los criterios requiere su expediente bibliográfico.</p>';
}

async function mcdaCalculateAhp() {
  const button = document.getElementById('btn-mcda-ahp-calculate');
  button.disabled = true;
  try {
    const draft = mcdaAhpCapture();
    MCDA.ahpDraft = draft;
    MCDA.ahpResult = null;
    mcdaRenderAhpResult();
    const signature = JSON.stringify(draft);
    const matrix = draft.criteria.map((_,i) => draft.criteria.map((_,j) => i === j ? 1 : null));
    for (let i=0; i<matrix.length; i++) for (let j=i+1; j<matrix.length; j++) {
      const value = Number(draft.pairs[`${i}-${j}`]);
      if (!value) throw new Error('Faltan comparaciones: «sin responder» no equivale a igualdad.');
      matrix[i][j] = value; matrix[j][i] = 1/value;
    }
    const a = await api('/api/multicriteria/ahp', 'POST', {...draft, matrix});
    if (signature !== JSON.stringify(mcdaAhpCapture())) return;
    MCDA.ahpResult = a;
    mcdaRenderAhpResult();
  } catch (e) { toast(`AHP: ${e.message}`, 'error'); }
  finally { button.disabled = false; }
}

async function mcdaApplyAhp() {
  if (!MCDA.ahpResult?.consistent || MCDA.scope !== 'area') return;
  MCDA.ahpApplied = projectJsonClone(MCDA.ahpResult);
  MCDA.layoutWeights = {...MCDA.ahpApplied.weights};
  MCDA.weightSchema = 'effective-area-v1';
  MCDA.weightSource = 'ahp';
  await mcdaBuildWeightSliders();
  TOPSIS_ROBUST.result = null;
  const robustCard = document.getElementById('topsis-robust-card');
  if (robustCard) robustCard.style.display = 'none';
  if (MCDA.lastSpatial?.analysis_kind === 'farm_layout_mcda_v2') await mcdaRankNow();
  else toast('Pesos AHP aplicados. Ejecuta el análisis de parques para obtener el ranking.', 'success');
}

function mcdaExportAhp() {
  if (!MCDA.ahpResult) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(MCDA.ahpResult, null, 2)], {type:'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = 'windpro-ahp.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Importa un expediente AHP exportado con mcdaExportAhp() (o un borrador
 * {criteria, pairs, ...} equivalente) y recalcula pesos/CR contra el backend
 * — nunca confía en los números ya calculados del archivo como definitivos.
 * Los criterios deben coincidir EXACTAMENTE (mismo orden) con el esquema
 * actual de 8 criterios de parque: las claves de `pairs` son posiciones
 * (`"i-j"`), no nombres, así que un orden distinto asignaría mal las
 * respuestas sin que se note. */
async function mcdaImportAhpFile(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  try {
    if (MCDA.scope !== 'area') {
      throw new Error('Cambia a modo "Parque" (área) antes de importar — el AHP solo aplica al ranking de parques.');
    }
    const data = JSON.parse(await file.text());
    if (!Array.isArray(data.criteria) || !data.criteria.length) {
      throw new Error('El archivo no tiene una lista de criterios (criteria).');
    }
    const defaults = await mcdaLoadDefaults();
    const currentCriteria = Object.keys(defaults.layout_weights || {});
    if (JSON.stringify(data.criteria) !== JSON.stringify(currentCriteria)) {
      throw new Error('Los criterios del archivo no coinciden (mismo orden) con los 8 criterios actuales de parque — probablemente es de otra versión de la plataforma.');
    }
    let pairs = {};
    if (data.pairs && typeof data.pairs === 'object') {
      pairs = {...data.pairs};
    } else if (Array.isArray(data.matrix)) {
      for (let i = 0; i < data.criteria.length; i++) {
        for (let j = i + 1; j < data.criteria.length; j++) {
          const value = data.matrix[i]?.[j];
          if (value != null) pairs[`${i}-${j}`] = String(value);
        }
      }
    } else {
      throw new Error('El archivo no tiene matrix ni pairs para reconstruir las comparaciones.');
    }
    MCDA.ahpDraft = {
      criteria: data.criteria, pairs,
      author: typeof data.author === 'string' ? data.author : '',
      objective: typeof data.objective === 'string' ? data.objective : '',
      rationale: typeof data.rationale === 'string' ? data.rationale : '',
    };
    MCDA.ahpResult = null;
    mcdaRenderAhp();
    await mcdaCalculateAhp();
    toast('Expediente AHP importado y recalculado contra el esquema actual.', 'success');
  } catch (e) {
    toast(`No se pudo importar el AHP: ${e.message}`, 'error');
  }
}

async function mcdaLoadDefaults() {
  if (MCDA.defaults) return MCDA.defaults;
  MCDA.defaults = await api('/api/multicriteria/defaults', 'GET');
  return MCDA.defaults;
}

async function mcdaBuildWeightSliders() {
  const d = await mcdaLoadDefaults();
  const container = document.getElementById('mcda-weights');
  if (!container) return;
  const isLayout = MCDA.scope === 'area';
  if (isLayout) {
    MCDA.layoutWeights = MCDA.weightSchema !== 'effective-area-v1'
      || mcdaUsesObsoleteReweightedBase(MCDA.layoutWeights)
      ? { ...d.layout_weights }
      : mcdaMergeLayoutWeightSchema(MCDA.layoutWeights, d.layout_weights);
    MCDA.weightSchema = 'effective-area-v1';
  }
  mcdaRenderAhp();
  const weights = isLayout ? MCDA.layoutWeights : d.weights;
  const directions = isLayout ? d.layout_directions : d.directions;
  const labels = isLayout ? d.layout_labels : d.labels;
  const renderWeight = ([k, v]) => {
    const dir = directions[k];
    const label = labels[k];
    const hint = dir > 0 ? '↑ max' : '↓ min';
    return `
      <div style="display:flex;align-items:center;gap:.5rem;font-size:.78rem">
        <label style="flex:1;color:var(--text-secondary)">${label}
          <span style="color:${dir>0?'var(--green)':'var(--orange)'};font-size:.7rem">${hint}</span>
        </label>
        <input type="range" min="0" max="1" step="any" value="${v}"
          data-crit="${k}" class="mcda-w"
          style="flex:1"
          oninput="mcdaSyncWeightInput(this)">
        <input type="number" min="0" max="1" step="0.001" value="${Number(v).toFixed(3)}"
          data-crit="${k}" class="mcda-w-num form-input"
          style="width:64px;flex:none;font-family:var(--font-mono);text-align:right;padding:.15rem .3rem"
          oninput="mcdaSyncWeightNumber(this)"
          onblur="mcdaFinalizeWeightNumber(this)">
      </div>`;
  };
  if (!isLayout) {
    container.innerHTML = Object.entries(weights).map(renderWeight).join('');
    return;
  }
  const economicKeys = new Set([
    'capex_usd', 'opex_lifetime_usd', 'lcoe_usd_kwh', 'van_usd',
    'benefit_cost_ratio', 'payback_years', 'irr', 'roi',
    'capex_usd_per_installed_kw', 'opex_usd_per_installed_kw_year',
    'van_usd_per_installed_mw', 'van_usd_per_effective_km2', 'discounted_payback_years',
  ]);
  const entries = Object.entries(weights);
  const renderGroup = (title, rows) => `
    <section style="border:1px solid var(--border);border-radius:8px;padding:.65rem;display:grid;gap:.5rem">
      <h5 style="grid-column:1/-1;font-size:.78rem;margin:0;color:var(--text-primary)">${title}</h5>
      ${rows.map(renderWeight).join('')}
    </section>`;
  container.innerHTML = [
    renderGroup('1. Adaptabilidad técnica y energética', entries.filter(([key]) => !economicKeys.has(key))),
    renderGroup('2. Evaluación económica', entries.filter(([key]) => economicKeys.has(key))),
    '<p class="text-muted" style="grid-column:1/-1;font-size:.75rem">Área efectiva: predio menos la unión de exclusiones permanentes, común a todas las turbinas. Los retiros afectan el acomodo, no el denominador. Los pesos se normalizan entre criterios válidos y variables.</p>',
  ].join('');
}

function mcdaGetWeights() {
  const assessment = mcdaActiveAhp();
  if (assessment) return { ...assessment.weights };
  const out = {};
  document.querySelectorAll('.mcda-w-num').forEach(el => {
    out[el.dataset.crit] = parseFloat(el.value) || 0;
  });
  return out;
}

/** Slider movido a mano: sincroniza el input numérico hermano. */
function mcdaSyncWeightInput(rangeEl) {
  const num = rangeEl.nextElementSibling;
  if (num) num.value = (+rangeEl.value).toFixed(3);
  mcdaRememberLayoutWeights(true);
}

/** Valor tecleado en el input numérico: mueve el slider en vivo, sin forzar
 * el rango [0,1] todavía (para no pelear con el usuario a medio escribir). */
function mcdaSyncWeightNumber(numEl) {
  const value = parseFloat(numEl.value);
  const range = numEl.previousElementSibling;
  if (range && Number.isFinite(value)) range.value = value;
  mcdaRememberLayoutWeights(true);
}

/** Al salir del campo: normaliza a [0,1] con 3 decimales. */
function mcdaFinalizeWeightNumber(numEl) {
  const value = Math.max(0, Math.min(1, parseFloat(numEl.value) || 0));
  numEl.value = value.toFixed(3);
  mcdaSyncWeightNumber(numEl);
}

function mcdaRememberLayoutWeights(edited = false) {
  if (MCDA.scope !== 'area') return;
  if (edited) MCDA.weightSource = 'manual';
  MCDA.layoutWeights = mcdaGetWeights();
  mcdaAhpSourceLabel();
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

function mcdaExclusionPolygons() {
  const raw = document.getElementById('mcda-exclusions')?.value?.trim();
  if (!raw) return [];
  const polygons = JSON.parse(raw);
  if (!Array.isArray(polygons) || polygons.some(poly => !Array.isArray(poly) || poly.length < 3)) {
    throw new Error('Las exclusiones deben ser una lista JSON de polígonos con al menos 3 puntos.');
  }
  polygons.forEach(poly => poly.forEach(point => {
    if (!Number.isFinite(Number(point?.lat)) || !Number.isFinite(Number(point?.lon))) {
      throw new Error('Cada punto de exclusión necesita lat y lon numéricos.');
    }
  }));
  return polygons;
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
    ? 'Mapa TOPSIS — escenario de decisión'
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
    try {
      mcdaExclusionPolygons().forEach(exclusion => {
        const layer = L.polygon(exclusion.map(p => [p.lat, p.lon]), {
          color: '#dc2626', weight: 2, dashArray: '5 4',
          fillColor: '#ef4444', fillOpacity: 0.18,
        }).addTo(map);
        MCDA.miniLayers.push(layer);
      });
    } catch (_) { /* validation is shown before running */ }
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
      `${i < 5 ? 'Muestra prioritaria del recurso' : 'Muestra interpolada del recurso'} #${i + 1}<br>` +
      `${s.name}<br>${s.lat.toFixed(5)}°, ${s.lon.toFixed(5)}°<br>` +
      `${Number(s.v_mean || 0).toFixed(2)} m/s` +
      (s.dominant_direction_deg != null ? ` · Dir ${Number(s.dominant_direction_deg).toFixed(0)}°` : ''),
      { direction: 'top' }
    ).addTo(map);
    MCDA.miniLayers.push(marker);
  });
  const winningLayout = result?.ranking?.ranking?.[0]?.layout_latlon || [];
  winningLayout.forEach((point, i) => {
    const marker = L.circleMarker(point, {
      radius: 5, color: '#0f766e', weight: 2,
      fillColor: '#2dd4bf', fillOpacity: 0.95,
    }).bindTooltip(
      `Aerogenerador ${i + 1} del layout TOPSIS ganador`,
      { direction: 'top' }
    ).addTo(map);
    MCDA.miniLayers.push(marker);
  });

  if (status) {
    status.textContent = orderedSites.length
      ? `${orderedSites.length} muestras de recurso · ${winningLayout.length} aeros ganadores`
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
  const winner = ranking[0];
  (winner?.layout_latlon || []).forEach((point, i) => {
    const marker = L.circleMarker(point, {
      radius: 5, color: '#0f766e', weight: 2,
      fillColor: '#2dd4bf', fillOpacity: 0.95,
    }).bindPopup(
      `<b>Layout TOPSIS ganador</b><br>Aerogenerador ${i + 1}<br>` +
      `${winner.turbina} · ${winner.n_aerogeneradores}/${winner.max_n_fisico}`
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
    ? 'Modo parque: optimiza cantidades y layouts factibles por turbina, calcula estelas, AEP y economía, y después aplica TOPSIS.'
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
  mcdaBuildWeightSliders();
  const body = document.getElementById('mcda-rank-body');
  if (body) {
    mcdaRenderRanking({ranking: [], note: 'Ejecuta el análisis para ver el ranking.'});
  }
  const status = document.getElementById('mcda-status');
  if (status) status.textContent = 'Sin análisis';
  mcdaClearSpatialMarkers();
}

async function mcdaResetWeights() {
  const d = await mcdaLoadDefaults();
  const defaults = MCDA.scope === 'area'
    ? d.layout_weights
    : d.weights;
  document.querySelectorAll('.mcda-w').forEach(el => {
    el.value = defaults[el.dataset.crit];
    if (el.nextElementSibling) el.nextElementSibling.value = (+el.value).toFixed(3);
  });
  if (MCDA.scope === 'area') {
    MCDA.layoutWeights = { ...defaults };
    MCDA.weightSource = 'reference';
    mcdaAhpSourceLabel();
  }
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
  const marketInputs = parkEquivalentDesignInputs();
  const projectYears = Number(WP.parkConfiguration?.project_horizon_years);
  if (!Number.isFinite(projectYears) || projectYears < 5 ||
      !Number.isFinite(marketInputs.price_usd_kwh)) {
    toast('Guarda la vida del proyecto y carga el archivo de precios antes de ejecutar TOPSIS.', 'warning');
    return;
  }
  const safetyMinD = Math.max(3, Number(document.getElementById('parque-min-sep-d')?.value) || 3);
  const rowStartD = Math.max(safetyMinD, Number(document.getElementById('parque-row-sep-d')?.value) || 3);
  const colStartD = Math.max(safetyMinD, Number(document.getElementById('parque-col-sep-d')?.value) || 3);
  const wakeLimitRaw = Number(document.getElementById('parque-max-wake-loss')?.value);
  const wakeLimitPct = Number.isFinite(wakeLimitRaw) ? Math.max(0, wakeLimitRaw) : 5;
  const spacingRange = (start, conventionalMax) => {
    const values = [];
    const end = Math.max(start, conventionalMax);
    for (let value = start; value <= end + 1e-9; value += 1) {
      values.push(Number(value.toFixed(2)));
    }
    if (values[values.length - 1] < end) values.push(end);
    return values;
  };
  let exclusionPolygons = [];
  try {
    exclusionPolygons = areaMode ? mcdaExclusionPolygons() : [];
  } catch (e) {
    toast(e.message, 'warning');
    return;
  }
  const body = {
    scope:         areaMode ? 'park' : 'exploratory',
    year:          parseInt(document.getElementById('mcda-year').value),
    demand_mwh:    marketInputs.demand_mwh ?? parseFloat(document.getElementById('mcda-demand').value),
    tasa:          marketInputs.tasa ?? parseFloat(document.getElementById('mcda-tasa').value) / 100,
    price_usd_kwh: marketInputs.price_usd_kwh,
    project_years: projectYears,
    price_mxn_per_usd: marketInputs.price_mxn_per_usd ?? parseFloat(document.getElementById('mcda-fx').value),
    market_trajectories: marketInputs.trajectories,
    n_grid:        parseInt(document.getElementById('krig3-n')?.value) || 80,
    model:         document.getElementById('krig3-model')?.value || 'exponential',
    filenames:     Array.from(KRIG.selected),
    weights:       mcdaGetWeights(),
    mode:          areaMode ? 'area_turbines' : document.getElementById('mcda-mode').value,
    max_points:    areaMode ? 56 : (parseInt(document.getElementById('mcda-maxfiles').value) || 60),
    turbine_filenames: turbineFilenames,
    commercialization_mode: marketInputs.trajectories?.commercialization_mode || 'total_sale',
    min_spacing_d: safetyMinD,
    s_para_range: spacingRange(rowStartD, 10),
    s_perp_range: spacingRange(colStartD, 6),
    max_wake_loss_pct: wakeLimitPct,
    wake_model: 'gaussian',
    k_wake: 0.04,
    ct: Number(document.getElementById('parque-ct')?.value) || 0.8,
    availability: (Number(document.getElementById('parque-avail')?.value) || 97) / 100,
    electrical_losses: (Number(document.getElementById('parque-elec')?.value) || 3) / 100,
    exclusion_polygons: exclusionPolygons,
    boundary_setback_m: Math.max(0, Number(document.getElementById('mcda-boundary-setback')?.value) || 0),
  };
  if (WP.sessionId) body.session_id = WP.sessionId;
  if (WP.activeDataSource) body.data_source = WP.activeDataSource;
  if (areaMode) {
    mcdaRememberLayoutWeights();
    body.layout_weights = MCDA.layoutWeights || MCDA.defaults?.layout_weights;
    body.ahp_assessment = mcdaActiveAhp();
    delete body.weights;
  }
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
    const guidedMcp = projectHasSpatialSource() && krig3AdvancedMcpRequested();
    if (guidedMcp) {
      document.getElementById('mcda-progress-text').textContent =
        'Preparando la calibración MCP y sus campos espaciales…';
      body.calibration_id = await krig3EnsureCalibration();
      body.target_height_m = Number(wsH());
      body.physical = !!document.getElementById('krig3-physical')?.checked;
      if (KRIG.orographicSpeedupGrid) body.orographic_speedup_grid = KRIG.orographicSpeedupGrid;
    }
    const r = await api('/api/multicriteria/spatial', 'POST', body);
    MCDA.taskId = r.task_id;
    mcdaPollStatus();
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('mcda-status').textContent = 'Error';
    document.getElementById('mcda-progress-wrap').style.display = 'none';
    document.getElementById('mcda-progress-text').textContent = '';
  }
}

async function mcdaPollStatus() {
  if (!MCDA.taskId) return;
  const taskId = MCDA.taskId;
  try {
    const r = await api(`/api/kriging/status/${taskId}`, 'GET');
    if (taskId !== MCDA.taskId) return;
    document.getElementById('mcda-progress-text').textContent = r.message || '';
    document.getElementById('mcda-progress-fill').style.width = `${r.progress || 0}%`;
    if (r.status === 'done') {
      MCDA.lastSpatial = r.result;
      MCDA.lastAnalysis = r.result?.sites || [];
      MCDA.lastRanking = r.result?.ranking || null;
      if (MCDA.scope === 'area') mcdaBuildWeightSliders();
      if (MCDA.lastSpatial && MCDA.lastRanking) MCDA.lastSpatial.ranking = MCDA.lastRanking;
      const nSites = MCDA.lastAnalysis.length;
      const nTurbs = MCDA.lastAnalysis.reduce((s, x) => s + (x.turbines||[]).length, 0);
      document.getElementById('mcda-status').textContent = MCDA.scope === 'area'
        ? `${r.result?.n_valid_models || 0} modelos válidos · 1 layout óptimo dentro del límite de estela por modelo · ${nSites} muestras de recurso`
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
  if (MCDA.lastSpatial?.analysis_kind === 'farm_layout_mcda_v2') {
    try {
      const r = await api('/api/multicriteria/rank', 'POST', {
        alternatives: MCDA.lastSpatial.alternatives || [],
        mode: 'layout_alternatives',
        ahp_assessment: mcdaActiveAhp(),
        weights: mcdaGetWeights(),
        polygon: MCDA.lastSpatial.comparison_polygon || mcdaParkPolygon(),
        exclusion_polygons: MCDA.lastSpatial.comparison_exclusions || mcdaExclusionPolygons(),
      });
      MCDA.lastRanking = r;
      MCDA.lastSpatial.ranking = r;
      mcdaRenderRanking(r);
      mcdaRenderSpatialMarkers(MCDA.lastSpatial);
      mcdaRenderMiniMap(MCDA.lastSpatial);
    } catch (e) {
      toast(`Error ranking: ${e.message}`, 'error');
    }
    return;
  }
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

// ─── ROBUSTEZ DE PESOS — TOPSIS (SMAA) ───────────────────────────────────────
const TOPSIS_ROBUST = { result: null };

async function runTopsisRobustness() {
  const ranking = MCDA.lastRanking?.ranking;
  if (!Array.isArray(ranking) || ranking.length < 2) {
    toast('Corre TOPSIS primero — se necesitan al menos 2 alternativas.', 'warning');
    return;
  }
  const isLayout = MCDA.lastSpatial?.analysis_kind === 'farm_layout_mcda_v2';
  const nSamples = Math.max(100, Math.min(2000, parseInt(document.getElementById('topsis-robust-n-samples')?.value) || 500));
  const body = {
    alternatives: ranking,
    mode: isLayout ? 'layout_alternatives' : 'pairs',
    base_weights: mcdaGetWeights(),
    ahp_assessment: isLayout ? mcdaActiveAhp() : null,
    n_samples: nSamples,
  };
  const btn = document.getElementById('btn-topsis-robust-run');
  if (btn) btn.disabled = true;
  document.getElementById('topsis-robust-card').style.display = '';
  document.getElementById('topsis-robust-status').textContent = 'Ejecutando…';
  document.getElementById('topsis-robust-progress-text').textContent =
    `Perturbando pesos con ${nSamples} muestras Dirichlet (SMAA)…`;
  try {
    const data = await api('/api/multicriteria/weight_robustness', 'POST', body);
    TOPSIS_ROBUST.result = data;
    document.getElementById('topsis-robust-status').textContent = `${data.n_samples} muestras`;
    document.getElementById('topsis-robust-progress-text').textContent = '';
    document.getElementById('topsis-robust-results').style.display = '';
    renderTopsisRobustness(data);
    toast('Robustez de pesos completada', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
    document.getElementById('topsis-robust-status').textContent = 'Error';
    document.getElementById('topsis-robust-progress-text').textContent = '';
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _topsisRobustName(label) {
  const meta = (MCDA.turbines || []).find(t => t.filename === label);
  return meta?.name || (label || '').replace('.csv', '');
}

function renderTopsisRobustness(data) {
  const alts = data.alternatives || [];
  const names = alts.map(a => _topsisRobustName(a.label));

  // Stacked bar: distribución de rank por alternativa
  destroyChart('topsis-robust-rank');
  const ctx = document.getElementById('topsis-robust-rank-chart')?.getContext('2d');
  if (ctx && alts.length) {
    const maxRank = Math.max(...alts.map(a => Math.max(...Object.keys(a.rank_counts || {}).map(Number), 1)));
    const nSamples = data.n_samples || 1;
    const datasets = [];
    for (let rk = 1; rk <= maxRank; rk++) {
      datasets.push({
        label: `#${rk}`,
        data: alts.map(a => 100 * (a.rank_counts?.[rk] || 0) / nSamples),
        backgroundColor: CLUSTER_COLORS[(rk - 1) % CLUSTER_COLORS.length] + (rk === 1 ? 'cc' : '66'),
      });
    }
    WP.charts['topsis-robust-rank'] = new Chart(ctx, {
      type: 'bar',
      data: { labels: names, datasets },
      options: {
        indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { font: { size: 9 }, boxWidth: 10 } },
          tooltip: { callbacks: { label: c => ` ${c.dataset.label}: ${c.parsed.x.toFixed(1)}%` } } },
        scales: {
          x: { stacked: true, max: 100, grid: { color: gridColor() }, title: { display: true, text: '% de muestras', font: { size: 10 } } },
          y: { stacked: true, grid: { display: false }, ticks: { font: { size: 10 } } },
        },
      },
    });
  }

  // Importancia de criterios (¿qué determina al ganador?)
  const clfVal = data.classifier_validation;
  const clfValEl = document.getElementById('topsis-robust-validation');
  if (clfValEl) clfValEl.innerHTML = clfVal
    ? `<span class="text-muted">Validación fuera de muestra (${clfVal.n_splits}-fold CV, n=${clfVal.n_samples}): `
      + `exactitud balanceada Random Forest = <b>${(clfVal.balanced_accuracy_mean*100).toFixed(1)}%</b> ± ${(clfVal.balanced_accuracy_std*100).toFixed(1)}pp · `
      + `regresión logística = <b>${(clfVal.linear_model_balanced_accuracy_mean*100).toFixed(1)}%</b> · `
      + `línea base por mayoría = ${(clfVal.baseline_accuracy_mean*100).toFixed(1)}%. `
      + mlVsLinearVerdict(clfVal.balanced_accuracy_mean - clfVal.linear_model_balanced_accuracy_mean, true)
      + '</span>'
    : '';
  destroyChart('topsis-robust-criteria');
  const ctx2 = document.getElementById('topsis-robust-criteria-chart')?.getContext('2d');
  if (ctx2) {
    if (!data.criteria_importance) {
      ctx2.canvas.parentElement.innerHTML = '<p class="text-muted" style="font-size:.8rem">Una sola alternativa domina el 100% de las muestras — no hay variación de ganador para explicar.</p>';
    } else {
      WP.charts['topsis-robust-criteria'] = new Chart(ctx2, {
        type: 'bar',
        data: {
          labels: data.criteria_importance.map(r => r.label),
          datasets: [{ label: 'Importancia (%)', data: data.criteria_importance.map(r => r.importance_pct),
            backgroundColor: acc3() + '99', borderColor: acc3(), borderWidth: 1, borderRadius: 3 }],
        },
        options: {
          indexAxis: 'y', responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: false },
            tooltip: { callbacks: { label: c => ` ${c.parsed.x.toFixed(1)}%` } } },
          scales: {
            x: { grid: { color: gridColor() }, title: { display: true, text: '% de importancia', font: { size: 10 } } },
            y: { grid: { display: false }, ticks: { font: { size: 10 } } },
          },
        },
      });
    }
  }

  // Tabla resumen
  const tableEl = document.getElementById('topsis-robust-table');
  if (tableEl) {
    const rows = alts.map((a, i) => `<tr>
      <td>${names[i]}</td>
      <td>${a.win_rate_pct.toFixed(1)}%</td>
      <td>${a.rank_mean ?? '—'} ± ${a.rank_std ?? '—'}</td>
      <td>${a.base_rank ?? '—'}</td>
      <td>${a.score_mean?.toFixed(4) ?? '—'}</td>
    </tr>`).join('');
    tableEl.innerHTML = `<table class="data-table" style="font-size:.72rem">
      <thead><tr><th>Alternativa</th><th>% veces #1</th><th>Rank medio ± σ</th><th>Rank con pesos actuales</th><th>Score medio</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }
}

function mcdaAuditEscape(value) {
  return String(value ?? '—').replace(/[&<>'"]/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[ch]);
}

function mcdaAuditNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : '—';
}

function mcdaAuditMoney(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `$${Math.round(number).toLocaleString('en-US')}` : '—';
}

function mcdaAuditCard(title, rows) {
  return `<div style="border:1px solid var(--border);border-radius:8px;padding:.65rem;min-width:260px;flex:1">
    <div style="font-weight:700;margin-bottom:.4rem">${mcdaAuditEscape(title)}</div>
    ${rows.map(([label, value]) => `<div style="display:flex;justify-content:space-between;gap:1rem;padding:.16rem 0;border-bottom:1px dotted var(--border)">
      <span class="text-muted">${mcdaAuditEscape(label)}</span><span style="text-align:right">${value}</span>
    </div>`).join('')}
  </div>`;
}

function mcdaLayoutAuditHtml(a) {
  const spec = a.turbine_specification || {};
  const cap = a.capex_breakdown || {};
  const econ = a.economic_assumptions || {};
  const costBenchmark = MCDA.lastSpatial?.design_cost_benchmark || {};
  const energy = a.energy_balance || {};
  const losses = a.loss_assumptions || {};
  const wakeValidity = a.wake_model_validity || {};
  const layout = a.layout_config || {};
  const installedKw = Number(spec.rated_power_kw || 0) * Number(a.n_aerogeneradores || 0);
  const basePerKw = installedKw > 0 ? Number(a.base_capex_usd || 0) / installedKw : null;
  const cards = [
    mcdaAuditCard('Capacidad y geometría', [
      ['Modelo', mcdaAuditEscape(a.turbina)],
      ['Cantidad final / Nmax', `<b>${mcdaAuditNumber(a.n_aerogeneradores, 0)} / ${mcdaAuditNumber(a.max_n_fisico, 0)}</b>`],
      ['Potencia unitaria', `${mcdaAuditNumber(spec.rated_power_kw, 0)} kW`],
      ['Potencia instalada', `${mcdaAuditNumber(installedKw / 1000, 3)} MW`],
      ['Diámetro / buje', `${mcdaAuditNumber(spec.rotor_diameter_m, 1)} / ${mcdaAuditNumber(spec.hub_height_m, 1)} m`],
      ['Área útil', `${mcdaAuditNumber(a.area_km2, 4)} km²`],
      ['Área efectiva común', `${mcdaAuditNumber(a.effective_area_km2, 6)} km²`],
      ['Exclusiones dentro del predio', `${mcdaAuditNumber(a.area_provenance?.excluded_area_km2, 6)} km²`],
      ['Separación layout', `${mcdaAuditNumber(layout.s_para_d, 2)}D × ${mcdaAuditNumber(layout.s_perp_d, 2)}D`],
      ['Orientación / tipo', `${mcdaAuditNumber(layout.orientation_deg, 1)}° · ${mcdaAuditEscape(layout.layout_type)}`],
      ['Colector MST', `${mcdaAuditNumber(a.collection_length_m, 1)} m`],
    ]),
    mcdaAuditCard('Energía y pérdidas', [
      ['Fuente de producción', mcdaAuditEscape(a.production_source?.source_label || a.production_source?.method || 'Resultado guardado sin detalle de fuente')],
      ['Método', mcdaAuditEscape(a.production_source?.method || '—')],
      ['Periodo meteorológico', mcdaAuditEscape([a.production_source?.period_start, a.production_source?.period_end].filter(Boolean).join(' → ') || '—')],
      ['Registros utilizados / excluidos', `${a.production_source?.rows_used ?? '—'} / ${a.production_source?.rows_excluded ?? '—'}`],
      ['Integración de estelas', a.production_source?.method === 'historical_series_spatial_scaling' ? 'Frecuencias históricas conjuntas de velocidad y dirección; ajuste espacial por turbina' : 'Consultar método de producción'],
      ['AEP bruto teórico', `${mcdaAuditNumber(energy.aep_gross_theoretical_mwh, 1)} MWh`],
      ['AEP neto pre-estela', `${mcdaAuditNumber(energy.aep_net_pre_wake_mwh, 1)} MWh`],
      ['AEP después de estela', `${mcdaAuditNumber(energy.aep_after_wake_operational_mwh, 1)} MWh`],
      ['AEP P50 final', `${mcdaAuditNumber(energy.aep_p50_after_all_losses_mwh, 1)} MWh`],
      ['AEP P90', `<b>${mcdaAuditNumber(energy.aep_p90_mwh, 1)} MWh</b>`],
      ['AEP P90 por área efectiva', `${mcdaAuditNumber(a.aep_p90_mwh_per_effective_km2, 1)} MWh/km²·año`],
      ['AEP P90 específico', `${mcdaAuditNumber(a.aep_p90_mwh_per_installed_mw, 1)} MWh/MW instalado·año`],
      ['Factor de capacidad', `${mcdaAuditNumber(Number(a.factor_capacidad) * 100, 2)}%`],
      ['Estela', `${mcdaAuditNumber(losses.wake_loss_pct, 2)}%`],
      ['Dominio estela', wakeValidity.model === 'gaussian'
        ? `Gaussiano x/D ≥ ${mcdaAuditNumber(wakeValidity.minimum_valid_x_over_d, 3)} · Ct constante`
        : 'Jensen · no aplica umbral gaussiano'],
      ['Indisponibilidad', `${mcdaAuditNumber(losses.availability_loss_pct, 2)}%`],
      ['Eléctricas / curtailment', `${mcdaAuditNumber(losses.electrical_loss_pct, 2)}% / ${mcdaAuditNumber(losses.curtailment_loss_pct, 2)}%`],
      ['Incertidumbre combinada', `${mcdaAuditNumber(a.aep_uncertainty_pct, 2)}%`],
    ]),
    mcdaAuditCard('Adaptación aerodinámica al sitio', [
      ['Fuente temporal', mcdaAuditEscape(
        a.site_fit_source?.method === 'complete_historical_series'
          ? `${a.site_fit_source?.source_label || a.site_fit_source?.source_id || 'Serie histórica activa'} · serie completa`
          : 'Fallback Weibull sintético de 8,760 h'
      )],
      ['Registros válidos / totales', `${mcdaAuditNumber(a.site_series_samples_valid, 0)} / ${mcdaAuditNumber(a.site_series_samples_total, 0)}`],
      ['Velocidad media del sitio', `${mcdaAuditNumber(a.site_mean_speed_ms, 2)} m/s`],
      ['Cp a velocidad media', `<b>${mcdaAuditNumber(a.cp_at_site_mean, 4)}</b>`],
      ['Cp efectivo sobre la serie completa', `<b>${mcdaAuditNumber(a.cp_effective_full_series, 4)}</b>`],
      ['Cp medio durante operación', mcdaAuditNumber(a.cp_effective_operating, 4)],
      ['Cp máximo de la turbina', mcdaAuditNumber(a.cp_max_turbine, 4)],
      ['Cp máximo alcanzado en el sitio', mcdaAuditNumber(a.cp_max_reached_at_site, 4)],
      ['Acceso al pico Cp de la curva', `${mcdaAuditNumber(Number(a.cp_peak_access_ratio) * 100, 2)}%`],
      ['Velocidad de Cp máximo', `${mcdaAuditNumber(a.ws_at_cp_max_ms, 2)} m/s`],
      ['Diferencia |v media − v Cp máx.|', `<b>${mcdaAuditNumber(a.cpmax_speed_mismatch_ms, 2)} m/s</b>`],
      ['Horas en zona ≥95% Cp máximo', `<b>${mcdaAuditNumber(a.hours_near_cpmax, 1)} h/año</b>`],
      ['Tiempo en zona ≥95% Cp máximo', `${mcdaAuditNumber(a.near_cpmax_pct, 2)}%`],
      ['Disponibilidad operativa del recurso', `<b>${mcdaAuditNumber(a.resource_operational_availability_pct, 2)}%</b>`],
      ['Horas operativas equivalentes', `${mcdaAuditNumber(a.resource_operational_hours, 1)} h/año`],
      ['Horas sin generación por recurso', `${mcdaAuditNumber(a.resource_non_generation_hours, 1)} h/año`],
      ['Horas con potencia ≥90% nominal', `<b>${mcdaAuditNumber(a.hours_at_or_above_90pct_rated, 1)} h/año</b>`],
      ['Tiempo con potencia ≥90% nominal', `${mcdaAuditNumber(a.at_or_above_90pct_rated_pct, 2)}%`],
    ]),
    mcdaAuditCard('CAPEX auditable', [
      ['Referencia de diseño', mcdaAuditEscape(costBenchmark.label || econ.cost_model_origin)],
      ['CAPEX instalado benchmark', costBenchmark.total_installed_usd_per_kw == null
        ? '—' : `<b>${mcdaAuditMoney(costBenchmark.total_installed_usd_per_kw)} /kW</b>`],
      ['Precio turbina configurado', `<b>${mcdaAuditMoney(econ.turbine_usd_per_kw)} /kW</b>`],
      ['Participación turbinas', `${mcdaAuditNumber(Number(cap.turbines_pct) * 100, 2)}%`],
      ['Costo unitario turbina', mcdaAuditMoney(cap.turbine_unit_usd)],
      ['Total turbinas', mcdaAuditMoney(cap.turbines_total_usd)],
      ['Obras civiles', mcdaAuditMoney(cap.civil_works_usd)],
      ['Sistema eléctrico/red', mcdaAuditMoney(cap.electrical_usd)],
      ['Instalación', mcdaAuditMoney(cap.installation_usd)],
      ['Ingeniería/permisos', mcdaAuditMoney(cap.engineering_usd)],
      ['Misceláneos/contingencia', mcdaAuditMoney(cap.miscellaneous_usd)],
      ['CAPEX base', `<b>${mcdaAuditMoney(a.base_capex_usd)}</b>`],
      ['Cable colector adicional', mcdaAuditMoney(a.collection_cable_cost_usd)],
      ['CAPEX total', `<b>${mcdaAuditMoney(a.capex_usd)}</b>`],
      ['CAPEX específico', `<b>${mcdaAuditMoney(a.capex_usd_per_installed_kw)} /kW</b>`],
      ['CAPEX base implícito', `<b>${mcdaAuditMoney(basePerKw)} /kW instalado</b>`],
      ['Control suma porcentajes', `${mcdaAuditNumber(Number(cap.pct_total_check) * 100, 2)}%`],
    ]),
    mcdaAuditCard('OPEX y finanzas', [
      ['OPEX configurado', `${mcdaAuditNumber(Number(econ.opex_pct_capex) * 100, 3)}% del CAPEX/año`],
      ['OPEX anual', `<b>${mcdaAuditMoney(a.opex_usd_anual)}</b>`],
      ['OPEX anual por kW', `${mcdaAuditMoney(a.opex_usd_per_installed_kw_year)} /kW-año`],
      ['OPEX vida sin descontar', mcdaAuditMoney(a.opex_lifetime_usd)],
      ['OPEX valor presente', mcdaAuditMoney(a.opex_present_value_usd)],
      ['Precio energía', `${mcdaAuditNumber(econ.price_usd_kwh, 4)} USD/kWh`],
      ['Ingreso año 1', mcdaAuditMoney(a.annual_revenue_year1_usd)],
      ['Tasa / horizonte', `${mcdaAuditNumber(Number(econ.discount_rate) * 100, 2)}% / ${mcdaAuditNumber(econ.project_years, 0)} años`],
      ['VAN / VPN', `<b>${mcdaAuditMoney(a.van_usd)}</b>`],
      ['VAN por área efectiva', `${mcdaAuditMoney(a.van_usd_per_effective_km2)} /km²`],
      ['VAN normalizado', `${mcdaAuditMoney(a.van_usd_per_installed_mw)} /MW instalado`],
      ['Relación beneficio/costo', `${mcdaAuditNumber(a.benefit_cost_ratio, 3)}`],
      ['ROI (VAN/CAPEX)', mcdaAuditNumber(a.roi, 3)],
      ['TIR', a.irr == null ? '—' : `${mcdaAuditNumber(Number(a.irr) * 100, 2)}%`],
      ['PRI / periodo de recuperación', a.payback_years == null ? '> horizonte del proyecto' : `${mcdaAuditNumber(a.payback_years, 2)} años`],
      ['Periodo de recuperación descontado', a.discounted_payback_years == null ? '> horizonte del proyecto' : `<b>${mcdaAuditNumber(a.discounted_payback_years, 2)} años</b>`],
      ['LCOE', `<b>${mcdaAuditNumber(a.lcoe_usd_kwh, 5)} USD/kWh</b>`],
      ['Origen modelo', mcdaAuditEscape(econ.cost_model_origin)],
    ]),
  ].join('');
  const wakeRows = (a.wake_trials || []).map(trial =>
    `<tr><td>${mcdaAuditNumber(trial.n, 0)}</td><td>${mcdaAuditNumber(trial.wake_loss_pct, 3)}%</td></tr>`).join('');
  const cashRows = (a.cashflow_table || []).map(row => `<tr>
    <td>${row.year}</td><td>${mcdaAuditNumber(row.energy_mwh, 1)}</td>
    <td>${mcdaAuditMoney(row.revenue_usd)}</td><td>${mcdaAuditMoney(row.opex_usd)}</td>
    <td>${mcdaAuditMoney(row.decommissioning_usd)}</td><td>${mcdaAuditMoney(row.net_cashflow_usd)}</td>
    <td>${mcdaAuditMoney(row.pv_net_cashflow_usd)}</td><td>${mcdaAuditMoney(row.cumulative_undiscounted_usd)}</td>
  </tr>`).join('');
  return `<div style="padding:.8rem;background:var(--bg-secondary)">
    <div style="display:flex;gap:.65rem;flex-wrap:wrap">${cards}</div>
    <details style="margin-top:.7rem"><summary style="cursor:pointer;font-weight:600">Pruebas de cantidad y estela</summary>
      <table class="tbl"><thead><tr><th>Turbinas</th><th>Pérdida estela</th></tr></thead><tbody>${wakeRows}</tbody></table>
    </details>
    <details style="margin-top:.7rem"><summary style="cursor:pointer;font-weight:600">Flujo de caja anual completo</summary>
      <div style="overflow:auto"><table class="tbl"><thead><tr><th>Año</th><th>Energía MWh</th><th>Ingreso</th><th>OPEX</th><th>Desmant.</th><th>Flujo neto</th><th>VP flujo</th><th>Acumulado</th></tr></thead><tbody>${cashRows}</tbody></table></div>
    </details>
    <details style="margin-top:.7rem"><summary style="cursor:pointer;font-weight:600">Registro completo JSON (auditoría)</summary>
      <pre style="white-space:pre-wrap;max-height:420px;overflow:auto;font-size:.68rem">${mcdaAuditEscape(JSON.stringify(a, null, 2))}</pre>
    </details>
  </div>`;
}

function mcdaToggleAudit(id) {
  const row = document.getElementById(id);
  if (row) row.style.display = row.style.display === 'none' ? '' : 'none';
}

// Display-only ratios: never added to the alternative or sent as ranking weights.
function mcdaInformationalRatios(a) {
  const divide = (value, denominator) => value != null && denominator != null
    && Number.isFinite(Number(value)) && Number.isFinite(Number(denominator))
    && Number(denominator) > 0 ? Number(value) / Number(denominator) : null;
  return {
    van_capex_display: divide(a.van_usd, a.capex_usd),
    van_mw_display: divide(a.van_usd, a.installed_mw),
    aep_p50_mw_display: divide(a.aep_p50_mwh ?? a.aep_mwh, a.installed_mw),
    aep_p90_mw_display: divide(a.aep_p90_mwh, a.installed_mw),
  };
}

function mcdaRenderRanking(r) {
  const body = document.getElementById('mcda-rank-body');
  const ranking = Array.isArray(r && r.ranking) ? r.ranking : [];
  const parkMode = MCDA.scope === 'area';
  const columns = [
    ['cp_effective_full_series', 'Cp efectivo', '—', 4, 1],
    ['hours_near_cpmax', 'Horas ≥95% Cp máx.', 'h/año', 1, 1],
    ['factor_capacidad', 'FC neto', '%', 2, 100],
    ['aep_p90_mwh', 'AEP P90', 'MWh', 1, 1],
    ['van_usd', 'VAN', 'USD', 0, 1],
    ['lcoe_usd_kwh', 'LCOE', 'USD/kWh', 5, 1],
    ['capex_usd_per_installed_kw', 'CAPEX / kW instalado', 'USD/kW', 0, 1],
  ];
  const head = document.querySelector('#mcda-rank-tbl thead');
  const applied = r?.weights || {};
  if (head) head.innerHTML = parkMode
    ? '<tr><th>#</th><th>Sitio</th><th>Turbina</th><th>Score</th>' + columns.map(([key, label, unit]) => {
      const direction = ['lcoe_usd_kwh', 'capex_usd_per_installed_kw'].includes(key) ? '↓' : '↑';
      const weight = applied[key] == null ? 'Sin peso efectivo' : `${(100 * applied[key]).toFixed(1)}% efectivo`;
      return `<th style="min-width:115px">${label} ${direction}<br><small>${unit}</small><br><small>${weight}</small></th>`;
    }).join('') + '</tr>'
    : '<tr><th>#</th><th>Sitio</th><th>Turbina</th><th>Score</th><th>CF</th><th>AEP (MWh)</th><th>VAN (USD)</th><th>LCOE</th><th>ROI</th><th>CAPEX (USD)</th></tr>';
  const columnCount = parkMode ? 4 + columns.length : 10;
  const top = ranking.slice(0, 100);
  if (!top.length) {
    body.innerHTML = `<tr><td colspan="${columnCount}" class="text-muted">Sin resultados. ${mcdaAuditEscape(r?.note || '')}</td></tr>`;
    return;
  }
  body.innerHTML = top.map((a, index) => {
    const medal = a.rank === 1 ? '🥇' : a.rank === 2 ? '🥈' : a.rank === 3 ? '🥉' : a.rank;
    const highlight = a.rank === 1 ? 'style="background:var(--green-dim)"' : '';
    const siteText = a.best_site
      ? `${a.site || ''}<br><span class="text-muted" style="font-size:.72rem">Mejor sitio: ${a.best_site}</span>`
      : `${(a.site||'').slice(0,30)}${(a.site||'').length>30?'…':''}`;
    const layoutInfo = a.n_aerogeneradores != null
      ? `<br><span class="text-muted" style="font-size:.72rem"><b>${a.n_aerogeneradores}/${a.max_n_fisico}</b> aeros (final/Nmax físico) · estela ${Number(a.wake_loss_pct || 0).toFixed(1)}%/${Number(a.max_wake_loss_pct ?? 5).toFixed(1)}% máx. · P90 ${Math.round(a.aep_p90_mwh || 0).toLocaleString()} MWh` +
        `${a.rank_sensitivity_min != null ? ` · rango sensible #${a.rank_sensitivity_min}–${a.rank_sensitivity_max}` : ''}</span>` +
        `<br><button class="btn-secondary" type="button" style="font-size:.68rem;padding:.2rem .45rem;margin-top:.3rem" onclick="mcdaToggleAudit('mcda-audit-${index}')">Ver todos los resultados y fórmulas</button>`
      : '';
    const cpFitInfo = Number.isFinite(Number(a.cp_at_site_mean))
      ? `<br><span class="text-muted" style="font-size:.72rem">Cp(v̄) <b>${Number(a.cp_at_site_mean).toFixed(4)}</b> · Cp efectivo ${Number(a.cp_effective_full_series).toFixed(4)} · disponibilidad recurso ${Number(a.resource_operational_availability_pct).toFixed(1)}% · zona eficiente ${Math.round(Number(a.hours_near_cpmax)).toLocaleString()} h/año</span>`
      : '';
    return `<tr ${highlight}>
      <td><strong>${medal}</strong></td>
      <td title="${a.site||''}">${siteText}</td>
      <td style="min-width:220px;max-width:300px">${a.turbina||''}${parkMode ? '' : cpFitInfo}${layoutInfo}</td>
      <td><strong>${a.topsis_score.toFixed(4)}</strong></td>
      ${parkMode ? columns.map(([key, label, unit, digits, scale, displayKey]) => {
        const value = a[displayKey || key];
        const display = value == null || !Number.isFinite(Number(value))
          ? 'No calculado'
          : (Number(value) * scale).toLocaleString('es-MX', {minimumFractionDigits: digits, maximumFractionDigits: digits});
        return `<td data-criterion="${key}" style="white-space:nowrap;font-variant-numeric:tabular-nums">${display}</td>`;
      }).join('') : `<td>${(a.factor_capacidad*100).toFixed(1)}%</td>
      <td>${Number.isFinite(Number(a.aep_mwh)) ? Math.round(a.aep_mwh).toLocaleString() : 'No calculado'}</td>
      <td>${Number.isFinite(Number(a.van_usd)) ? Math.round(a.van_usd).toLocaleString() : 'No calculado'}</td>
      <td>${Number.isFinite(Number(a.lcoe_usd_kwh)) ? Number(a.lcoe_usd_kwh).toFixed(4) : 'No calculado'}</td>
      <td>${Number.isFinite(Number(a.roi)) ? Number(a.roi).toFixed(2) : 'No calculado'}</td>
      <td>${Number.isFinite(Number(a.capex_usd)) ? Math.round(a.capex_usd).toLocaleString() : 'No calculado'}</td>`}
    </tr><tr id="mcda-audit-${index}" style="display:none"><td colspan="${columnCount}">${mcdaLayoutAuditHtml(a)}</td></tr>`;
  }).join('');
  // Propagate winner to Parque and Tecnoeconómico
  syncTopsisWinnerToModules();
  // Revela las tarjetas de robustez (batch técnico-económico y SMAA de pesos)
  // apenas hay un ranking usable — cubre tanto correr TOPSIS en vivo como
  // restaurar un ranking guardado al importar un proyecto.
  if (top.length) {
    const batchCard = document.getElementById('gsens-batch-card');
    if (batchCard) batchCard.style.display = '';
    const robustCard = document.getElementById('topsis-robust-card');
    if (robustCard) robustCard.style.display = '';
  }
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

  // ── 2. Tecnoeconómico: selector con TODAS las alternativas ya rankeadas
  // por TOPSIS (todas con estela real, sin necesidad de recalcular nada) ──
  const teSelect = document.getElementById('te-turbine');
  if (!teSelect) return;

  const turbineLabel = (key) => {
    const meta = (MCDA.turbines || []).find(t => t.filename === key);
    return meta
      ? `${meta.name || key} — ${meta.rated_power_kw ? (meta.rated_power_kw/1000).toFixed(1)+' MW' : ''} ⌀${meta.rotor_diameter_m||'?'} m`
      : key.replace('.csv', '');
  };
  teSelect.replaceChildren(...items.map(row => {
    const key = row.turbina || row.label;
    return new Option(turbineLabel(key), key, false, false);
  }));

  // Conserva la elección manual del usuario si esa turbina sigue en el ranking;
  // si no, vuelve a la ganadora #1.
  const priorOverride = WP.technoSelectedTopsisTurbine;
  const overrideStillValid = !!priorOverride && items.some(row => (row.turbina || row.label) === priorOverride);
  WP.technoSelectedTopsisTurbine = overrideStillValid ? priorOverride : null;
  const activeRow = overrideStillValid
    ? items.find(row => (row.turbina || row.label) === priorOverride)
    : winner;
  const activeKey = activeRow.turbina || activeRow.label;
  teSelect.value = activeKey;

  if (!WP.technoInputsDirty && !technoCurrentScenario()?.result) {
    const activeCount = Number(activeRow.n_aerogeneradores);
    const teCount = document.getElementById('te-nturbines');
    if (teCount && Number.isFinite(activeCount) && activeCount > 0) {
      teCount.value = String(Math.round(activeCount));
    }
  }

  const renderTopsisBadge = (row) => {
    const badge = document.getElementById('te-turbine-topsis-badge');
    if (!badge) return;
    const key   = row.turbina || row.label;
    const meta  = (MCDA.turbines || []).find(t => t.filename === key);
    const name  = meta?.name || (key || '').replace('.csv', '');
    const score = Number(row.topsis_score || 0).toFixed(4);
    const cf    = row.factor_capacidad != null
      ? ` · CF ${(Number(row.factor_capacidad)*100).toFixed(1)}%` : '';
    const rank  = items.indexOf(row) + 1;
    const medal = rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : `#${rank}`;
    badge.textContent = `${medal} TOPSIS #${rank}: ${name} · Score ${score}${cf}`;
    badge.style.display = '';
  };
  renderTopsisBadge(activeRow);

  // Cambiar de turbina aquí sólo reutiliza la alternativa ya evaluada por
  // TOPSIS (con su propio n_placed y AEP-con-estela) — no dispara Kriging
  // ni una nueva optimización; ver technoParkLayout().
  teSelect.onchange = () => {
    WP.technoSelectedTopsisTurbine = teSelect.value;
    const row = items.find(r => (r.turbina || r.label) === teSelect.value);
    if (!row) return;
    const n = Number(row.n_aerogeneradores);
    const teCount = document.getElementById('te-nturbines');
    if (teCount && Number.isFinite(n) && n > 0) teCount.value = String(Math.round(n));
    renderTopsisBadge(row);
    if (technoCurrentScenario()?.result) {
      calcTechnoEconomic();
    } else {
      toast('Turbina actualizada desde el ranking TOPSIS. Pulsa «Calcular» para ver el análisis financiero.', 'info');
    }
  };
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
    'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}';
  PARQUE.baseLayer = L.tileLayer(tileUrl, { maxZoom: 16, crossOrigin: 'anonymous' })
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

function parquePlotTurbinesOnMap(layoutLatlon, simTurbines, result = PARQUE.optResult) {
  parqueSyncWakeDirection(result);
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
  const direction = parqueModelWindDirection(result);
  if (direction !== null) parqueWakeDirectionCaption(direction);
  if (PARQUE.wakeVisible) parqueRenderWakeCones();
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
    data_source:       WP.activeDataSource,
  };
  const btn = document.getElementById('btn-parque-recalc-edit');
  if (btn) btn.textContent = '⏳ Recalculando…';
  setStatus('Recalculando con layout editado…');
  try {
    const r = await api('/api/farm/simulate', 'POST', body);
    PARQUE.result = r;
    PARQUE.optResult = Object.assign({}, PARQUE.optResult, { simulation: r, layout_latlon: layoutLatLon, turbine_key: turbineKey });
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

function parqueModelWindDirection(result = PARQUE.optResult) {
  for (const raw of [result?.simulation?.dominant_energy_dir_deg,
       result?.layout_config?.dominant_dir_deg, result?.simulation?.dominant_dir_deg]) {
    if (raw !== null && raw !== undefined && Number.isFinite(Number(raw))) {
      return ((Number(raw) % 360) + 360) % 360;
    }
  }
  return null;
}

function parqueSyncWakeDirection(result = PARQUE.optResult) {
  const direction = parqueModelWindDirection(result);
  const input = document.getElementById('parque-wake-dir');
  if (input) {
    input.value = direction === null ? '' : direction;
  }
  return direction;
}

function parqueWakeDirectionCaption(windFrom) {
  const label = document.getElementById('parque-wake-direction-note');
  if (!label) return;
  const model = parqueModelWindDirection();
  const manual = model === null || Math.abs(((windFrom - model + 540) % 360) - 180) > .01;
  const windTo = (windFrom + 180) % 360;
  label.textContent = `${manual ? 'Vista manual' : 'Dirección del cálculo'}: viento desde ${parqueFmt(windFrom, 1)}° → estela hacia ${parqueFmt(windTo, 1)}°. ` +
    'Los conos ilustran una dirección; la energía se calcula con toda la distribución de viento de TOPSIS. El giro de las filas es independiente.';
}

// ── Wake cone visualization ───────────────────────────────────────────────────
function parqueRenderWakeCones() {
  const map = parqueInitMap();
  if (!map || !PARQUE.optResult?.layout_latlon) return;

  if (PARQUE.wakeGroup) { map.removeLayer(PARQUE.wakeGroup); PARQUE.wakeGroup = null; }

  const requestedDirection = parseFloat(document.getElementById('parque-wake-dir')?.value);
  const windFrom = Number.isFinite(requestedDirection)
    ? ((requestedDirection % 360) + 360) % 360 : parqueModelWindDirection();
  if (windFrom === null) {
    const note = document.getElementById('parque-wake-direction-note');
    if (note) note.textContent = 'Sin dirección calculada. Ejecuta TOPSIS y optimiza el layout.';
    return;
  }
  parqueWakeDirectionCaption(windFrom);
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
        const source = r.source === 'market_series'
          ? `Archivo: ${r.filename || r.column}` : `Col: "${r.column}"`;
        badge.textContent = `📊 ${source}  ·  μ: ${parqueFmt(r.mean_mw, 2)} MW  ·  máx: ${parqueFmt(r.max_mw, 2)} MW  ·  ${r.n_records} reg  ·  Δt=${r.dt_h}h`;
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
  const winner = parqueSelectedTopsis();
  if (winner) {
    const height = winner.turbine_specification?.hub_height_m;
    if (height) document.getElementById('parque-ws-h').value = height;
    document.getElementById('parque-n-turbines').value = winner.n_aerogeneradores;
    document.getElementById('parque-n-turbines').max = winner.n_aerogeneradores;
    const ctx = winner.energy_context;
    const limits = winner.layout_constraints;
    if (ctx && limits) {
      for (const [id, value] of [['parque-min-sep-d', limits.min_spacing_d],
        ['parque-row-sep-d', limits.s_para_range[0]], ['parque-col-sep-d', limits.s_perp_range[0]],
        ['parque-max-wake-loss', limits.max_wake_loss_pct], ['parque-kwake', ctx.k_wake],
        ['parque-ct', ctx.ct], ['parque-avail', ctx.availability * 100],
        ['parque-elec', ctx.electrical_losses * 100], ['parque-sectors', ctx.n_sectors]]) {
        document.getElementById(id).value = value;
      }
    }
  }
  parqueUpdateRecommendation();
}

function parqueSelectedTopsis() {
  const key = document.getElementById('parque-turbine')?.value;
  return (MCDA.lastRanking?.ranking || []).find(row => row.turbina === key &&
    Number(row.n_aerogeneradores) > 0 && Array.isArray(row.layout_latlon)) || null;
}

function parqueShowTopsisEnergy(row) {
  const n = Number(row.n_aerogeneradores);
  const total = Number(row.aep_p50_mwh ?? row.aep_mwh);
  document.getElementById('parque-rec-n').textContent = n;
  document.getElementById('parque-rec-aep').textContent = parqueFmt(total / n, 1);
  const basis = document.getElementById('parque-energy-basis');
  if (basis) basis.textContent = `TOPSIS · P50 del parque: ${parqueFmt(total, 1)} MWh/año · ` +
    `Media por turbina: ${parqueFmt(total / n, 1)} MWh/año · ` +
    `Potencia media por turbina: ${parqueFmt(total / n * 1000 / 8760, 1)} kW · ` +
    `Estela: ${parqueFmt(row.wake_loss_pct, 2)}%. Incluye las pérdidas de TOPSIS.`;
}

// ── Recomendación n° turbinas ─────────────────────────────────────────────────
async function parqueUpdateRecommendation() {
  const winner = parqueSelectedTopsis();
  if (winner) { parqueShowTopsisEnergy(winner); return; }
  const basis = document.getElementById('parque-energy-basis');
  if (basis) basis.textContent = 'Estimación independiente para cubrir demanda; incluye estela supuesta del 8%. Ejecuta TOPSIS del parque para usar su base energética.';
  const turbineKey = document.getElementById('parque-turbine')?.value;
  const demand     = parseFloat(document.getElementById('parque-demand')?.value) || 5000;
  if (!turbineKey) return;

  const hubHeight = parseFloat(document.getElementById('parque-ws-h')?.value) || 110;
  const turbineCfg = { builtin_model: turbineKey, hub_height_m: hubHeight };
  // Mean wind speed: use MCDA/kriging polygon mean if available
  let vMean = null, wK = null;
  const hasSpatialWind = typeof MCDA !== 'undefined' && Array.isArray(MCDA.lastAnalysis) && MCDA.lastAnalysis.length;
  if (hasSpatialWind) {
    const v_vals = MCDA.lastAnalysis.map(s => s.v_mean || 0).filter(v => v > 0);
    if (v_vals.length) vMean = v_vals.reduce((a, b) => a + b, 0) / v_vals.length;
    const k_vals = MCDA.lastAnalysis.map(s => s.weibull_K || 0).filter(k => k > 0);
    if (k_vals.length) wK = k_vals.reduce((a, b) => a + b, 0) / k_vals.length;
  }

  try {
    const r = await api('/api/farm/recommend', 'POST', {
      session_id: WP.sessionId,
      data_source: WP.activeDataSource,
      turbine: turbineCfg, demand_mwh: demand,
      ...(Number.isFinite(vMean) ? { v_mean: vMean } : {}),
      ...(Number.isFinite(wK) ? { weibull_k: wK } : {}),
      hub_height_m: hubHeight,
      measurement_height_m: wsH(),
      availability: (parseFloat(document.getElementById('parque-avail')?.value) || 97) / 100,
      electrical_losses: (parseFloat(document.getElementById('parque-elec')?.value) || 3) / 100,
    });
    if (parqueSelectedTopsis() || document.getElementById('parque-turbine')?.value !== turbineKey) return;
    document.getElementById('parque-rec-n').textContent   = r.n_turbines;
    document.getElementById('parque-rec-aep').textContent = r.aep_single_net_mwh?.toLocaleString() || '—';
    const nInp = document.getElementById('parque-n-turbines');
    if (nInp && (!nInp.value || parseInt(nInp.value) === 0)) nInp.value = r.n_turbines;
  } catch (e) { /* silent */ }
}

// ── Optimización y simulación ─────────────────────────────────────────────────
async function parqueOptimizeTopsis(winner) {
  if (!winner.energy_context || !winner.layout_constraints) {
    toast('Ejecuta TOPSIS del parque nuevamente para guardar la base de comparación.', 'warning'); return;
  }
  const ctx = winner.energy_context;
  const limits = winner.layout_constraints;
  const unchanged = [['parque-ws-h', ctx.wind_height_m], ['parque-min-sep-d', limits.min_spacing_d],
    ['parque-row-sep-d', limits.s_para_range[0]], ['parque-col-sep-d', limits.s_perp_range[0]],
    ['parque-max-wake-loss', limits.max_wake_loss_pct], ['parque-kwake', ctx.k_wake],
    ['parque-ct', ctx.ct], ['parque-avail', ctx.availability * 100],
    ['parque-elec', ctx.electrical_losses * 100], ['parque-sectors', ctx.n_sectors]]
    .every(([id, value]) => Math.abs(Number(document.getElementById(id).value) - value) < 1e-6);
  if (!unchanged) {
    toast('Los parámetros cambiaron. Ejecuta TOPSIS nuevamente o selecciona otra vez su alternativa para recuperar la base comparable.', 'warning'); return;
  }
  const currentPolygon = MAPA.parkPoints?.length >= 3 ? MAPA.parkPoints : KRIG.parkPoints;
  const saved = winner.layout_constraints.polygon;
  if (currentPolygon?.length !== saved.length || currentPolygon.some((p, i) =>
      Math.abs(p[0] - saved[i].lat) > 1e-8 || Math.abs(p[1] - saved[i].lon) > 1e-8)) {
    toast('El polígono cambió. Ejecuta TOPSIS nuevamente para comparar el mismo terreno.', 'warning'); return;
  }
  let exclusions;
  try { exclusions = mcdaExclusionPolygons(); } catch (e) { toast(e.message, 'warning'); return; }
  if (JSON.stringify(exclusions) !== JSON.stringify(winner.layout_constraints.exclusions || [])) {
    toast('Las exclusiones cambiaron. Ejecuta TOPSIS nuevamente.', 'warning'); return;
  }
  const progress = document.getElementById('parque-progress-wrap');
  const fill = document.getElementById('parque-progress-fill');
  const label = document.getElementById('parque-progress-text');
  const button = document.getElementById('btn-parque-optimize');
  progress.style.display = ''; fill.style.width = '35%'; button.disabled = true;
  label.textContent = 'Comparando familias, orientaciones y separaciones con la base energética TOPSIS…';
  try {
    const result = await api('/api/farm/refine_topsis', 'POST', {
      alternative: winner,
      n_turbines: Number(document.getElementById('parque-n-turbines').value) || 0,
      layout_type: document.getElementById('parque-layout-type').value || 'auto',
    });
    if (result.error) throw new Error(result.error);
    // A response for an older selection must never replace the current model.
    if (parqueSelectedTopsis() !== winner) return;
    const latestPolygon = MAPA.parkPoints?.length >= 3 ? MAPA.parkPoints : KRIG.parkPoints;
    if (latestPolygon?.length !== saved.length || latestPolygon.some((p, i) =>
        Math.abs(p[0] - saved[i].lat) > 1e-8 || Math.abs(p[1] - saved[i].lon) > 1e-8)) {
      throw new Error('El polígono cambió durante la búsqueda. Ejecuta TOPSIS nuevamente.');
    }
    PARQUE.optResult = result;
    parqueApplyConfiguration(result.comparison.selected_id);
    fill.style.width = '100%';
    toast(`${result.comparison.evaluated_count} layouts evaluados; seleccionada la mayor energía dentro del límite de estela.`, 'success');
    if (WP._qdPipelineReady && QD.active) {
      qdSetStep(4, 'Parque optimizado ✓');
      setTimeout(() => quickDesignContinueAfterParque(PARQUE.optResult), 800);
    }
  } catch (e) {
    document.getElementById('parque-status').textContent = 'No se pudo completar la comparación';
    toast(e.message, 'error');
  } finally {
    progress.style.display = 'none'; label.textContent = ''; button.disabled = false;
  }
}

function parqueRenderComparison() {
  const container = document.getElementById('parque-config-comparison');
  if (!container) return;
  const comparison = PARQUE.optResult?.comparison;
  container.style.display = comparison ? '' : 'none';
  if (!comparison) { container.innerHTML = ''; return; }
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  container.innerHTML = `<div class="card-header"><span class="card-title">Configuraciones · ${esc(PARQUE.optResult.turbine_key || PARQUE.optResult.turbine?.name)}</span></div>
    <div class="card-body"><p class="text-muted">Misma curva, altura, viento, densidad y pérdidas que TOPSIS. Se maximiza P50 dentro de ${parqueFmt(comparison.max_wake_loss_pct, 2)}% de estela.
    ${comparison.same_count ? 'Misma cantidad de turbinas.' : 'Cantidad reducida: el cambio de energía también incluye este efecto.'}</p>
    <div style="overflow-x:auto"><table class="data-table" style="width:100%"><thead><tr>
    <th>Configuración</th><th>N</th><th>Separación / giro</th><th>P50 parque<br>MWh/año</th><th>Media/turbina<br>MWh/año</th><th>Estela %</th><th>Δ energía<br>MWh/año</th><th>Resultado</th>
    </tr></thead><tbody>${comparison.winners.map(row => {
      const cfg = row.layout_config || {};
      const tags = [row.id === comparison.max_energy_id ? 'Mayor energía' : '',
        row.id === comparison.min_wake_id ? 'Menor estela' : '', row.pareto ? 'Pareto' : ''].filter(Boolean).join(' · ');
      const active = row.id === comparison.selected_id;
      return `<tr><td>${esc(row.label)}</td><td>${row.n_placed}</td>
        <td>${parqueFmt(cfg.s_para_d, 1)}D × ${parqueFmt(cfg.s_perp_d, 1)}D / ${parqueFmt(cfg.orientation_deg, 0)}°</td>
        <td>${parqueFmt(row.aep_p50_mwh, 1)}</td><td>${parqueFmt(row.aep_per_turbine_mwh, 1)}</td>
        <td>${parqueFmt(row.wake_loss_pct, 2)}</td><td>${parqueFmt(row.aep_gain_mwh, 1)}</td>
        <td>${esc(tags)} ${active ? '<strong>Aplicada</strong>' : row.feasible
          ? `<button class="btn-secondary" onclick="parqueApplyConfiguration('${esc(row.id)}')">Aplicar</button>` : 'Solo referencia'}</td></tr>`;
    }).join('')}</tbody></table></div>
    <p class="text-muted">${comparison.evaluated_count} layouts evaluados. ${esc(comparison.search_method)}. La búsqueda es aproximada; no certifica un óptimo global. Pareto identifica opciones sin otra evaluada que mejore energía o estela sin empeorar la otra.</p>
    ${comparison.errors.map(e => `<p class="text-muted">${esc(e.family)}: ${esc(e.reason)}</p>`).join('')}</div>`;
}

function parqueApplyConfiguration(id) {
  const result = PARQUE.optResult;
  if (result?.turbine_key && result.turbine_key !== document.getElementById('parque-turbine')?.value) {
    toast('Selecciona la turbina de esta comparación antes de aplicar su layout.', 'warning'); return;
  }
  const row = result?.comparison?.winners.find(c => c.id === id);
  if (!row?.feasible) return;
  PARQUE.optResult = {...result, ...row, comparison: {...result.comparison, selected_id: id}};
  PARQUE.result = row.simulation;
  const sim = row.simulation;
  const layer = document.getElementById('parque-map-layer')?.value || 'wind';
  parqueRenderKrigingLayer(layer);
  parqueRenderPolygon();
  parquePlotTurbinesOnMap(row.layout_latlon, sim.turbines);
  document.getElementById('parque-kpis').style.display = '';
  for (const [key, value, digits] of [['aep', sim.aep_mwh / 1000, 3], ['wl', sim.wake_loss_pct, 2],
       ['cf', sim.cf_pct, 2], ['mw', sim.installed_mw, 3], ['flh', sim.flh, 0], ['dir', parqueModelWindDirection(PARQUE.optResult), 1]]) {
    document.getElementById(`parque-kpi-${key}`).textContent = parqueFmt(value, digits) + (key === 'dir' ? '°' : '');
  }
  document.getElementById('parque-status').textContent = `${row.label} · ${row.n_placed} turbinas · ${sim.installed_mw} MW`;
  document.getElementById('parque-info').textContent = `${result.turbine?.name || result.turbine_key} · ⌀${result.D_m}m`;
  document.getElementById('parque-layout-meta').textContent = `Área: ${parqueFmt(result.area_km2, 3)} km² · Separación mínima: ${parqueFmt(result.min_sep_m, 0)} m`;
  const badge = document.getElementById('parque-layout-badge');
  badge.textContent = row.label; badge.style.display = '';
  document.getElementById('parque-rec-aep').textContent = parqueFmt(row.aep_per_turbine_mwh, 1);
  document.getElementById('parque-rec-n').textContent = row.n_placed;
  document.getElementById('parque-energy-basis').textContent = `${row.label} · P50 del parque: ${parqueFmt(sim.aep_mwh, 1)} MWh/año · Potencia media/turbina: ${parqueFmt(sim.mean_power_per_turbine_kw, 1)} kW. Base energética TOPSIS.`;
  parqueRenderAEPChart(sim.turbines, row.layout_latlon);
  parqueRenderTable(row.layout_latlon, row.wind_at_turbines, sim.turbines);
}

async function parqueOptimize() {
  const topsis = parqueSelectedTopsis();
  if (topsis) { await parqueOptimizeTopsis(topsis); return; }
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
    spatial_sources:   windGrid ? (KRIG.result?.spatial_sources || []) : [],
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
    data_source:       WP.activeDataSource,
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

    PARQUE.optResult = { ...r, turbine_key: turbineKey };
    PARQUE.result    = r.simulation || null;

    renderSavedPark(r, layerKey, turbineKey);

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
  parqueRenderComparison();
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
      <td>${sim ? fmt(sim.aep_gross_mwh, 1) : '—'}</td>
      <td>${sim ? fmt(sim.aep_no_wake_mwh, 1) : '—'}</td>
      <td>${sim ? fmt(sim.aep_mwh, 1) : '—'}</td>
      <td>${sim ? fmt(sim.wake_loss_pct, 2) : '—'}</td>
      <td>${sim ? fmt(sim.cf_pct, 2) : '—'}</td>
    </tr>`;
  }).join('');
}

async function parqueRenderWakeChart(rotorD, savedProfile = null) {
  const rotor = rotorD || (PARQUE.optResult && PARQUE.optResult.D_m) || 80;
  const k  = parseFloat(document.getElementById('parque-kwake')?.value) || 0.075;
  const ct = parseFloat(document.getElementById('parque-ct')?.value)    || 0.8;
  try {
    const r = savedProfile || await api('/api/farm/wake_profile', 'POST', { rotor_d_m: rotor, k_wake: k, ct });
    PARQUE.wakeProfile = r;
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
  parqueRenderComparison();
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
  // Un proyecto importado se abre mostrando su resultado guardado, pero el
  // módulo debe seguir siendo interactivo: sin esto, la turbina/TOPSIS/mapa
  // nunca se inicializaban de nuevo y quedaba imposible modificar nada.
  parqueLoadTurbines();
  parquePopulateTopsisRanking();
  // La configuración comercial gobierna la demanda del diseño. En venta
  // total no debe intentarse inferir una carga desde la serie de viento; para
  // autoconsumo se usa la meta/serie publicada y sólo se conserva el lector
  // heredado cuando todavía no existe una demanda configurada.
  const parkConfig = WP.parkConfiguration;
  const configuredDemand = parkEquivalentDesignInputs().demand_mwh;
  if (parkConfig) {
    const demandInput = document.getElementById('parque-demand');
    if (demandInput) demandInput.value = Math.round(Math.max(0, Number(configuredDemand) || 0));
    const badge = document.getElementById('parque-demand-badge');
    if (badge) {
      badge.textContent = parkConfig.commercialization_mode === 'total_sale'
        ? 'Venta total · demanda del proyecto = 0 MWh/año'
        : `Configuración comercial · ${Math.round(Math.max(0, Number(configuredDemand) || 0)).toLocaleString('es-MX')} MWh/año`;
      badge.style.display = '';
    }
    parqueUpdateRecommendation();
    if (parkConfig.commercialization_mode !== 'total_sale'
        && parkConfig.demand_source === 'market_series'
        && !(Number(configuredDemand) > 0)) {
      parqueLoadDemandFromSession();
    }
  } else {
    parqueLoadDemandFromSession();
  }
  const layerKey = document.getElementById('parque-map-layer')?.value || 'wind';
  if (PARQUE.optResult?.layout_latlon) {
    // renderSavedPark ya pinta capa Kriging + polígono + turbinas + KPIs/tabla;
    // llamarlo aquí (no solo en el primer render tras importar) evita que un
    // resultado existente se quede sin repintar al reactivar el módulo.
    renderSavedPark(PARQUE.optResult, PARQUE.layerKey || layerKey, PARQUE.optResult.turbine_key || PARQUE.optResult.turbine?.name || '');
    if (PARQUE.wakeVisible) parqueRenderWakeCones();
  } else {
    parqueRenderKrigingLayer(layerKey);
    parqueRenderPolygon();
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
  dark:    'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
  voyager: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
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
    maxZoom: (key === 'osm' || key === 'topo') ? 18 : 16,
    subdomains: (key === 'osm' || key === 'topo') ? 'abc' : 'abcd',
    attribution: '© OpenStreetMap · © Esri',
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
});

// ═════════════════════════ MARKET DATA HUB ═════════════════════════════
const MARKET_CATS = ['demand', 'pml'];
const MARKET_LABELS = {
  demand: 'Demanda', pml: 'PML'
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
    await syncParkCommercialFile(category);
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
    await syncParkCommercialFile(category);
    // Reload file list so the new file appears in the selector
    await loadMarketFiles();
    toast(`${MARKET_LABELS[category]}: ${r.filename} subido — ${r.n_rows.toLocaleString()} registros`, 'success');
    setStatus(`${MARKET_LABELS[category]} listo: ${r.filename}`);
  } catch (e) {
    toast(`Error subiendo ${file.name}: ${e.message}`, 'error');
    setStatus('Error al subir archivo de mercado.');
  }
}

async function syncParkCommercialFile(category) {
  if (category === 'pml') {
    await api('/api/pml/load', 'POST', {
      session_id: WP.sessionId,
      preset: 'session_market_pml',
    });
  }
  if (category === 'pml' || category === 'demand') {
    const saved = await saveParkConfiguration({ quiet: true });
    if (!saved) await loadParkConfiguration({ quiet: true });
  }
}

function _renderMarketResult(category, r) {
  WP.marketResults ||= {};
  WP.marketResults[category] = r;
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

}

// ════════════════════════════════════════════════════════════════════════════
//  MCP LAB — selección Globe, ejecución, validación y publicación
// ════════════════════════════════════════════════════════════════════════════
function mcpEsc(value) {
  return String(value ?? '').replace(/[&<>"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[char]));
}

function mcpFmt(value, digits = 3) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : '—';
}

function mcpMethodLabel(value) {
  const method = String(value || '');
  if (method === 'sector_qm_16') return 'MCP cuantílico sectorial · 16 sectores';
  if (method === 'sector_qm_12') return 'MCP cuantílico sectorial · 12 sectores';
  const labels = {
    quantile_mapping: 'Mapeo cuantílico', seasonal_quantile_mapping: 'Mapeo cuantílico estacional',
    linear: 'MCP lineal', variance_ratio: 'Variance Ratio',
    quadratic: 'MCP cuadrático', ml_gradient_boosting: 'Gradient Boosting',
    ml_random_forest: 'Random Forest', ml_ridge: 'Ridge', ml_mlp: 'MLP',
  };
  return labels[method] || method;
}

function bindMCPLab() {
  document.getElementById('btn-mcp-run')?.addEventListener('click', runMCPLab);
  document.getElementById('btn-mcp-publish')?.addEventListener('click', publishMCPRun);
  document.getElementById('btn-mcp-back-globe')?.addEventListener('click', restoreGlobeStage);
  document.getElementById('mcp-target-height')?.addEventListener('change', async () => {
    try {
      await persistMCPTargetHeight();
    } catch (e) {
      toast(`No se pudo guardar la altura objetivo: ${e.message}`, 'error');
    }
  });
  document.querySelector('.nav-btn[data-module="mcp"]')?.addEventListener('click', () => {
    setTimeout(() => loadMCPStudy(WP.importedProject ? WP.mcpStudy : null), 30);
  });
}

async function persistMCPTargetHeight() {
  const input = document.getElementById('mcp-target-height');
  const targetHeight = Number(input?.value);
  if (!Number.isFinite(targetHeight) || targetHeight < 1 || targetHeight > 300) {
    throw new Error('La altura objetivo debe estar entre 1 y 300 m.');
  }
  if (!WP.mcpStudy) return targetHeight;
  if (Math.abs(Number(WP.mcpStudy.target_height_m) - targetHeight) < 1e-9) {
    return targetHeight;
  }
  const study = await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/mcp-study`, 'PUT', {
    observed_station_id: WP.mcpStudy.observed_station_id,
    reference_station_ids: WP.mcpStudy.reference_station_ids,
    poi: WP.mcpStudy.poi,
    radius_km: WP.mcpStudy.radius_km,
    target_height_m: targetHeight,
  });
  WP.mcpStudy = study;
  document.getElementById('mcp-study-revision').textContent = `Revisión ${study.study_revision}`;
  return targetHeight;
}

async function loadMCPStudy(savedStudy = null) {
  if (!WP.sessionId) return;
  if (singleStationMode() || WP.studyMode === 'reanalysis_only') {
    WP.mcpStudy = null;
    document.getElementById('mcp-study-revision').textContent = 'MCP desactivado';
    document.getElementById('btn-mcp-run').disabled = true;
    document.getElementById('mcp-study-observation').innerHTML =
      '<span class="text-muted">Este proyecto usa una fuente temporal sin MCP.</span>';
    document.getElementById('mcp-study-references').innerHTML =
      '<span class="text-muted">Las referencias se usan directamente en el estudio espacial de Diseño.</span>';
    document.getElementById('mcp-progress-message').textContent =
      'Para realizar MCP, regresa a Globe y elige expresamente esa opción con una estación real y referencias virtuales.';
    return;
  }
  try {
    const study = savedStudy || await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/mcp-study`, 'GET');
    WP.mcpStudy = study;
    document.getElementById('mcp-study-revision').textContent = `Revisión ${study.study_revision}`;
    const obs = study.observed_station;
    document.getElementById('mcp-study-observation').innerHTML =
      `<strong>${mcpEsc(obs.display_name)}</strong><br><span class="text-muted">OBS · ${mcpEsc(obs.provider)} · ${mcpFmt(obs.latitude, 3)}, ${mcpFmt(obs.longitude, 3)}${obs.timezone_label ? ` · ${mcpEsc(obs.timezone_label)}` : ''}</span>`;
    document.getElementById('mcp-study-references').innerHTML = (study.reference_stations || []).map(ref =>
      `<span class="mcp-ref-chip">${mcpEsc(ref.provider)} · ${mcpEsc(ref.display_name)} · ${mcpFmt(ref.distance_km, 1)} km
        <button type="button" class="mcp-ref-remove" data-reference-id="${mcpEsc(ref.station_id)}" aria-label="Retirar ${mcpEsc(ref.display_name)}">×</button>
      </span>`
    ).join('') || '—';
    document.querySelectorAll('#mcp-study-references .mcp-ref-remove').forEach(button => {
      button.addEventListener('click', () => removeMCPReference(button.dataset.referenceId));
    });
    document.getElementById('mcp-target-height').value = study.target_height_m || 80;
    document.getElementById('btn-mcp-run').disabled = !(study.reference_stations || []).length;
    document.getElementById('mcp-progress-message').textContent = 'Estudio listo. Ejecuta el análisis MCP cuando quieras.';
  } catch (e) {
    WP.mcpStudy = null;
    document.getElementById('mcp-study-revision').textContent = 'Sin selección';
    document.getElementById('btn-mcp-run').disabled = true;
  }
}

async function removeMCPReference(referenceId) {
  if (!WP.mcpStudy) return;
  const remaining = (WP.mcpStudy.reference_station_ids || []).filter(value => value !== referenceId);
  if (!remaining.length) {
    toast('El estudio MCP debe conservar al menos una referencia.', 'warn');
    return;
  }
  try {
    const study = await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/mcp-study`, 'PUT', {
      observed_station_id: WP.mcpStudy.observed_station_id,
      reference_station_ids: remaining,
      poi: WP.mcpStudy.poi,
      radius_km: WP.mcpStudy.radius_km,
      target_height_m: parseFloat(document.getElementById('mcp-target-height')?.value) || WP.mcpStudy.target_height_m || 80,
    });
    WP.mcpStudy = study;
    await loadMCPStudy();
    toast(`Referencia retirada. Estudio actualizado a revisión ${study.study_revision}.`, 'success');
  } catch (e) {
    toast(`No se pudo retirar la referencia: ${e.message}`, 'error');
  }
}

async function runMCPLab() {
  if (singleStationMode() || WP.studyMode === 'reanalysis_only' || !WP.mcpStudy) return;
  const btn = document.getElementById('btn-mcp-run');
  btn.disabled = true;
  document.getElementById('btn-mcp-publish').disabled = true;
  document.getElementById('mcp-publish-status').textContent = 'No publicado';
  try {
    const targetHeight = await persistMCPTargetHeight();
    const started = await api('/api/mcp/runs', 'POST', {
      session_id: WP.sessionId,
      target_height_m: targetHeight,
      train_year: parseInt(document.getElementById('mcp-train-year').value) || 2018,
      test_year: parseInt(document.getElementById('mcp-test-year').value) || 2019,
      sector_count: parseInt(document.getElementById('mcp-sector-count')?.value) || 16,
    });
    WP.mcpStudy.target_height_m = started.target_height_m;
    WP.mcpStudy.study_revision = started.study_revision;
    document.getElementById('mcp-study-revision').textContent = `Revisión ${started.study_revision}`;
    WP.mcpRunId = started.run_id;
    document.getElementById('mcp-run-status').textContent = 'Ejecutando';
    await pollMCPTask(started.task_id);
  } catch (e) {
    document.getElementById('mcp-run-status').textContent = 'Error';
    document.getElementById('mcp-progress-message').textContent = e.message;
    toast(`MCP no pudo iniciar: ${e.message}`, 'error');
    btn.disabled = false;
  }
}

async function pollMCPTask(taskId) {
  if (singleStationMode() || WP.studyMode === 'reanalysis_only') return;
  try {
    const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`, 'GET');
    document.getElementById('mcp-progress-fill').style.width = `${Math.max(0, Math.min(100, task.progress || 0))}%`;
    document.getElementById('mcp-progress-message').textContent = task.message || 'Procesando…';
    document.getElementById('mcp-run-status').textContent = task.status === 'done' ? 'Terminado' : task.status === 'error' ? 'Error' : 'Ejecutando';
    if (task.status === 'done') {
      WP.mcpRunId = task.result?.run_id || WP.mcpRunId;
      const result = await api(`/api/mcp/runs/${encodeURIComponent(WP.mcpRunId)}?session_id=${encodeURIComponent(WP.sessionId)}`, 'GET');
      renderMCPResult(result);
      document.getElementById('btn-mcp-run').disabled = false;
      document.getElementById('btn-mcp-publish').disabled = false;
      toast('Corrida MCP terminada y lista para revisión.', 'success');
      return;
    }
    if (task.status === 'error') {
      document.getElementById('btn-mcp-run').disabled = false;
      toast(`Corrida MCP fallida: ${task.message}`, 'error');
      return;
    }
    setTimeout(() => pollMCPTask(taskId), 1600);
  } catch (e) {
    document.getElementById('btn-mcp-run').disabled = false;
    toast(`No se pudo consultar la corrida MCP: ${e.message}`, 'error');
  }
}

function renderMCPResult(result) {
  WP.mcpResult = result;
  document.getElementById('mcp-run-status').textContent = 'Completado';
  const published = WP.activeDataSource === `mcp:${WP.mcpRunId}`;
  document.getElementById('mcp-publish-status').textContent = published ? 'Publicado' : 'No publicado';
  document.getElementById('btn-mcp-publish').disabled = published;
  const trainYear = Number(result.config?.train_year);
  const testYear = Number(result.config?.test_year);
  const trainLabel = Number.isFinite(trainYear) ? trainYear : 'entrenamiento';
  const testLabel = Number.isFinite(testYear) ? testYear : 'prueba';
  const studyRefs = result.study?.reference_stations || [];
  const providerByFilename = new Map();
  studyRefs.forEach(ref => (ref.file_ids || []).forEach(name => providerByFilename.set(name, ref.provider)));
  const ranking = result.reference_ranking || [];
  document.getElementById('mcp-ranking-body').innerHTML = ranking.length ? ranking.map(row => `<tr class="${row.rank === 1 ? 'best-row' : ''}">
    <td class="model-name">${row.rank === 1 ? '★ ' : ''}${mcpEsc(row.reference)}</td><td>${mcpEsc(providerByFilename.get(row.reference) || '')}</td>
    <td>${mcpFmt(row.R)}</td><td>${mcpFmt(row.R2)}</td><td>${mcpFmt(row.RMSE)}</td><td>${mcpFmt(row.MAE)}</td><td>${mcpFmt(row.Bias)}</td><td>${row.N ?? '—'}</td>
  </tr>`).join('') : '<tr><td colspan="8">Sin referencias evaluables</td></tr>';

  const modelRows = [];
  const speedSelection = result.speed_model_selection || {};
  const excludedSpeedMethods = new Set(speedSelection.excluded_speed_methods || []);
  (result.mcp_method_comparison || result.mcp_method_comparison_2019 || []).forEach(row => modelRows.push({ family: 'MCP', ...row }));
  (result.ml_model_comparison || result.ml_model_comparison_2019 || []).forEach(row => modelRows.push({ family: 'ML', ...row }));
  if (result.multi_reference?.nnls) modelRows.push({ family: 'Multi-ref', ...result.multi_reference.nnls });
  (result.multi_reference?.ml_comparison || []).forEach(row => modelRows.push({ family: 'Multi-ref ML', ...row }));
  document.getElementById('mcp-models-body').innerHTML = modelRows.length ? modelRows.map(row => {
    const met = row.metrics || {};
    const selectionStatus = row.method === speedSelection.selected_method
      ? '★ Ganador de velocidad'
      : (excludedSpeedMethods.has(row.method) ? 'Sólo diagnóstico sectorial' : row.status);
    return `<tr class="${row.method === speedSelection.selected_method ? 'best-row' : ''}"><td>${row.family}</td><td class="model-name">${mcpEsc(mcpMethodLabel(row.method))}</td><td>${mcpFmt(met.RMSE)}</td><td>${mcpFmt(met.MAE)}</td><td>${mcpFmt(met.R2)}</td><td>${mcpFmt(met.Bias)}</td><td>${mcpFmt(met.delta_weibull_k)}</td><td>${mcpFmt(met.delta_weibull_lambda)}</td><td>${mcpEsc(selectionStatus)}</td></tr>`;
  }).join('') : '<tr><td colspan="9">Sin comparación</td></tr>';

  const forward = result.validation_forward?.metrics || {};
  const reverse = result.validation_reverse?.metrics || {};
  document.getElementById('mcp-validation-summary').innerHTML = `<div class="mcp-kpis">
    <div class="mcp-kpi">${trainLabel} → ${testLabel}<b>RMSE ${mcpFmt(forward.RMSE)} · R² ${mcpFmt(forward.R2)}</b></div>
    <div class="mcp-kpi">Sensibilidad inversa · no usada para estimar<b>${testLabel} → ${trainLabel} · RMSE ${mcpFmt(reverse.RMSE)} · R² ${mcpFmt(reverse.R2)}</b></div>
    <div class="mcp-kpi">Entrenamiento final<b>${trainLabel} exclusivamente · ${testLabel} fuera de muestra</b></div>
    <div class="mcp-kpi">Ganador de velocidad<b>${mcpEsc(mcpMethodLabel(speedSelection.selected_method || result.backtesting_multi_year?.selected_method))}</b></div>
    <div class="mcp-kpi">Validación de distribución<b>KS ${mcpFmt(forward.KS)} · Δk ${mcpFmt(forward.delta_weibull_k)} · Δλ ${mcpFmt(forward.delta_weibull_lambda)} m/s</b></div>
    <div class="mcp-kpi">Rosa y dirección<b>${mcpEsc(result.selected_direction_reference || result.selected_reference)} · ${Number(result.wind_rose_direction_model?.n_sectors || 16)} sectores estacionales</b></div>
    <div class="mcp-kpi">Selección de velocidad<b>Holdout cronológico 75/25 dentro de ${trainLabel} · sin modelo preferido</b></div>
  </div>`;

  const hist = result.historical_reconstruction || {};
  const historicalWeibull = result.weibull_provenance || hist.weibull || {};
  const modelWeibull = historicalWeibull.model_hindcast || {};
  const projectWeibull = historicalWeibull.project_series || {};
  document.getElementById('mcp-hindcast-summary').innerHTML = `
    <div class="mcp-kpi">Periodo<b>${mcpEsc((hist.period_start || '').slice(0,10))} → ${mcpEsc((hist.period_end || '').slice(0,10))}</b></div>
    <div class="mcp-kpi">Horas válidas<b>${Number(hist.valid_long_term_hours || 0).toLocaleString()}</b></div>
    <div class="mcp-kpi">Reconstruido<b>${mcpFmt(hist.reconstructed_share_pct, 1)}%</b></div>
    <div class="mcp-kpi">Referencia de velocidad<b>${mcpEsc(result.selected_reference)}</b></div>
    <div class="mcp-kpi">Referencia de dirección<b>${mcpEsc(result.selected_direction_reference || result.selected_reference)}</b></div>
    <div class="mcp-kpi">Weibull del hindcast modelado<b>k ${mcpFmt(modelWeibull.k)} · λ ${mcpFmt(modelWeibull.lambda)} m/s · MLE</b></div>
    <div class="mcp-kpi">Weibull de la serie del proyecto<b>k ${mcpFmt(projectWeibull.k)} · λ ${mcpFmt(projectWeibull.lambda)} m/s · observado + huecos MCP</b></div>`;
  const oqc = result.quality_control?.observations || {};
  document.getElementById('mcp-qc-summary').innerHTML = `
    <div class="mcp-kpi">Observación válida<b>${Number(oqc.records_valid || oqc.eligible_rows || oqc.valid_rows || 0).toLocaleString()} registros</b></div>
    <div class="mcp-kpi">Reloj de M04<b>${mcpEsc(result.observation_metadata?.timezone_input || 'UTC')} → UTC</b></div>
    <div class="mcp-kpi">Perfil vertical α<b>${mcpFmt(result.vertical_profile?.alpha, 4)}</b></div>
    <div class="mcp-kpi">Referencias evaluadas<b>${ranking.length}</b></div>`;

  const gap = result.visualization?.gap_treatment;
  const gapBox = document.getElementById('mcp-gap-summary');
  const gapBody = document.getElementById('mcp-gap-body');
  if (gap?.yearly?.length && gapBox && gapBody) {
    gapBox.classList.remove('hidden');
    gapBody.innerHTML = gap.yearly.map(row => `<tr>
      <td>${row.year}</td><td>${Number(row.expected_hours).toLocaleString()}</td>
      <td>${Number(row.measured_hours).toLocaleString()}</td><td>${mcpFmt(row.measured_availability_pct, 1)}%</td>
      <td>${Number(row.mcp_filled_hours).toLocaleString()}</td><td>${Number(row.unresolved_hours).toLocaleString()}</td>
    </tr>`).join('');
    const testGap = (gap.monthly || []).find(row => row.year === testYear && row.month === 5);
    const note = document.getElementById('mcp-gap-note');
    if (note) note.textContent = testGap
      ? `Mayo de ${testLabel}: ${Number(testGap.measured_hours).toLocaleString()} h medidas, ${Number(testGap.mcp_filled_hours).toLocaleString()} h reconstruidas por MCP y ${Number(testGap.unresolved_hours).toLocaleString()} h sin resolver. Los valores reconstruidos nunca se etiquetan como medición.`
      : 'Los valores reconstruidos nunca se etiquetan como medición.';
  } else {
    gapBox?.classList.add('hidden');
  }

  renderMCPVisualizations(result);

  const labels = {
    final_report_html: 'Informe HTML', result_json: 'Resultado JSON', run_manifest_json: 'Manifiesto',
    reference_ranking_csv: 'Ranking CSV', historical_reconstruction_csv: 'Hindcast CSV',
    historical_reconstruction_annual_csv: 'Resumen anual', reconstruction_readiness_json: 'Readiness',
    multi_reference_comparison_json: 'Comparación multirreferencia', campaign_sensitivity_csv: 'Sensibilidad de campaña',
    m04_gap_treatment_csv: 'Faltantes M04 CSV', direction_correction_json: 'Corrección direccional JSON',
    direction_reference_ranking_csv: `Ranking direccional ${trainLabel} CSV`,
    certe_training_baseline_json: `Control CERTE ${trainLabel} JSON`,
    measured_turbulence_10min_csv: 'TI CERTE medida 10 min CSV',
    historical_turbulence_csv: 'TI histórica modelada CSV',
    turbulence_summary_json: 'Resumen de turbulencia JSON',
  };
  document.getElementById('mcp-artifacts').innerHTML = Object.entries(labels)
    .filter(([key]) => result.artifacts?.[key])
    .map(([key, label]) => `<a href="/api/mcp/runs/${encodeURIComponent(result.run_id)}/artifacts/${encodeURIComponent(key)}?session_id=${encodeURIComponent(WP.sessionId)}" target="_blank" rel="noopener">⬇ ${label}</a>`).join('');
}

function renderMCPVisualizations(result) {
  const visual = result.visualization?.validation_forward;
  const empty = document.getElementById('mcp-visual-empty');
  const content = document.getElementById('mcp-visual-content');
  if (!visual?.timeseries?.timestamps?.length) {
    empty?.classList.remove('hidden');
    content?.classList.add('hidden');
    return;
  }
  empty?.classList.add('hidden');
  content?.classList.remove('hidden');
  const period = document.getElementById('mcp-visual-period');
  if (period) period.textContent = `${String(visual.period_start || '').slice(0, 10)} → ${String(visual.period_end || '').slice(0, 10)} · ${Number(visual.pairs || 0).toLocaleString()} pares`;

  const measuredColor = '#1D9E75';
  const estimatedColor = '#E59A45';
  const mutedColor = isLight() ? '#64748b' : '#94a3b8';
  const baseOptions = {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { labels: { boxWidth: 12, font: { size: 10 } } } },
    scales: {
      x: { grid: { color: gridColor() }, ticks: { maxTicksLimit: 9, font: { size: 9 } } },
      y: { grid: { color: gridColor() }, ticks: { font: { size: 9 } }, title: { display: true, text: 'm/s' } },
    },
  };
  const putChart = (id, config) => {
    destroyChart(id);
    const canvas = document.getElementById(id);
    if (canvas) WP.charts[id] = new Chart(canvas, config);
  };

  const baseline = result.visualization?.training_observation_baseline || {};
  const rawBaseline = baseline.raw_10min || {};
  const hourlyBaseline = baseline.hourly_circular || {};
  const baselineBox = document.getElementById('mcp-certe-baseline');
  if (rawBaseline.weibull && rawBaseline.wind_rose_36) {
    baselineBox?.classList.remove('hidden');
    const match = baseline.documentary_reference?.matches_raw_10min;
    const baselineKpis = document.getElementById('mcp-certe-baseline-kpis');
    if (baselineKpis) baselineKpis.innerHTML = `
      <div class="mcp-kpi">CERTE original · 10 min<b>k ${mcpFmt(rawBaseline.weibull.k)} · λ ${mcpFmt(rawBaseline.weibull.lambda)} m/s</b></div>
      <div class="mcp-kpi">CERTE sincronizado · 1 h<b>k ${mcpFmt(hourlyBaseline.weibull?.k)} · λ ${mcpFmt(hourlyBaseline.weibull?.lambda)} m/s</b></div>
      <div class="mcp-kpi">Figura CERTE 2018<b>${match ? 'Coincide' : 'Revisar'} · k 1.809 · λ 10.522 m/s</b></div>
      <div class="mcp-kpi">Efecto sobre orientación<b>TV ${mcpFmt(baseline.aggregation_effect?.rose_total_variation_pct_points, 2)} puntos porcentuales</b></div>`;
    if (baselineKpis) baselineKpis.innerHTML += measuredHeightTable(baseline.height_profile);
    _drawWindRose(rawBaseline.wind_rose_36, 'mcp-chart-certe-rose', 'mcp-certe-rose-legend');
    putChart('mcp-chart-certe-weibull', {
      type: 'bar',
      data: { labels: rawBaseline.weibull.labels_ms || [], datasets: [
        { label: 'Frecuencia observada', data: rawBaseline.weibull.empirical_density || [], backgroundColor: measuredColor + '88', borderColor: measuredColor, borderWidth: 1 },
        { type: 'line', label: `Weibull MLE · k ${mcpFmt(rawBaseline.weibull.k)} · λ ${mcpFmt(rawBaseline.weibull.lambda)}`, data: rawBaseline.weibull.weibull_pdf || [], borderColor: isLight() ? '#111827' : '#f8fafc', borderWidth: 2, pointRadius: 0, tension: .25 },
      ] },
      options: { ...baseOptions, scales: {
        x: { grid: { display: false }, title: { display: true, text: 'Velocidad (m/s)' }, ticks: { maxTicksLimit: 12 } },
        y: { grid: { color: gridColor() }, title: { display: true, text: 'Densidad f' }, beginAtZero: true },
      } },
    });
    const baselineNote = document.getElementById('mcp-certe-baseline-note');
    if (baselineNote) baselineNote.textContent = `${baseline.note || ''} La dirección horaria usa media circular seno/coseno; no se promedian grados de forma lineal.`;
  } else {
    baselineBox?.classList.add('hidden');
  }

  putChart('mcp-chart-timeseries', {
    type: 'line',
    data: { labels: visual.timeseries.timestamps.map(value => value.slice(0, 10)), datasets: [
      { label: 'Medido', data: visual.timeseries.measured, borderColor: measuredColor, backgroundColor: measuredColor + '20', pointRadius: 0, borderWidth: 1.4 },
      { label: 'Estimado', data: visual.timeseries.estimated, borderColor: estimatedColor, backgroundColor: estimatedColor + '20', pointRadius: 0, borderWidth: 1.2 },
    ] },
    options: baseOptions,
  });

  const scatterPoints = (visual.scatter.measured || []).map((value, index) => ({ x: value, y: visual.scatter.estimated[index] }));
  const scatterMax = Math.max(1, ...scatterPoints.flatMap(point => [Number(point.x) || 0, Number(point.y) || 0]));
  putChart('mcp-chart-scatter', {
    type: 'scatter',
    data: { datasets: [
      { label: 'Pares horarios', data: scatterPoints, backgroundColor: measuredColor + '55', borderColor: measuredColor, pointRadius: 2 },
      { label: 'Línea 1:1', type: 'line', data: [{x:0,y:0},{x:scatterMax,y:scatterMax}], borderColor: mutedColor, borderDash: [5,4], borderWidth: 1.2, pointRadius: 0 },
    ] },
    options: { ...baseOptions, interaction: { mode: 'nearest', intersect: false }, scales: {
      x: { type: 'linear', min: 0, grid: { color: gridColor() }, title: { display: true, text: 'Medido (m/s)' } },
      y: { type: 'linear', min: 0, grid: { color: gridColor() }, title: { display: true, text: 'Estimado (m/s)' } },
    } },
  });

  putChart('mcp-chart-distribution', {
    type: 'line',
    data: { labels: visual.speed_distribution.labels.map(value => Number(value).toFixed(1)), datasets: [
      { label: 'Medido', data: visual.speed_distribution.measured_pct, borderColor: measuredColor, backgroundColor: measuredColor + '22', fill: true, pointRadius: 0, tension: .2 },
      { label: 'Estimado', data: visual.speed_distribution.estimated_pct, borderColor: estimatedColor, backgroundColor: estimatedColor + '16', fill: true, pointRadius: 0, tension: .2 },
    ] },
    options: { ...baseOptions, scales: {
      x: { grid: { display: false }, title: { display: true, text: 'Velocidad (m/s)' }, ticks: { maxTicksLimit: 10 } },
      y: { grid: { color: gridColor() }, title: { display: true, text: 'Frecuencia (%)' } },
    } },
  });

  putChart('mcp-chart-residuals', {
    type: 'bar',
    data: { labels: visual.residual_distribution.labels.map(value => Number(value).toFixed(1)), datasets: [
      { label: 'Errores', data: visual.residual_distribution.frequency, backgroundColor: '#7C6BC4aa', borderColor: '#7C6BC4', borderWidth: 1 },
    ] },
    options: { ...baseOptions, plugins: { legend: { display: false } }, scales: {
      x: { grid: { display: false }, title: { display: true, text: 'Error (m/s)' }, ticks: { maxTicksLimit: 9 } },
      y: { grid: { color: gridColor() }, title: { display: true, text: 'Frecuencia' } },
    } },
  });

  const monthLabels = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
  putChart('mcp-chart-monthly', {
    type: 'line',
    data: { labels: monthLabels, datasets: [
      { label: 'Medido', data: visual.monthly_profile.measured, borderColor: measuredColor, backgroundColor: measuredColor, pointRadius: 3, tension: .25 },
      { label: 'Estimado', data: visual.monthly_profile.estimated, borderColor: estimatedColor, backgroundColor: estimatedColor, pointRadius: 3, tension: .25 },
    ] },
    options: baseOptions,
  });

  const campaign = (result.campaign_sensitivity || []).filter(row => row.status === 'ok' && Number.isFinite(Number(row.metrics?.RMSE)));
  putChart('mcp-chart-campaign', {
    type: 'line',
    data: { labels: campaign.map(row => `${row.campaign_months} m`), datasets: [
      { label: 'RMSE', data: campaign.map(row => row.metrics.RMSE), borderColor: '#4A7BC8', backgroundColor: '#4A7BC833', fill: true, pointRadius: 4, tension: .2 },
      { label: 'MAE', data: campaign.map(row => row.metrics.MAE), borderColor: '#7C6BC4', backgroundColor: '#7C6BC4', pointRadius: 3, tension: .2 },
    ] },
    options: baseOptions,
  });

  const fullCampaign = result.visualization?.campaign_daily || {};
  putChart('mcp-chart-campaign-full', {
    type: 'line',
    data: { labels: (fullCampaign.timestamps || []).map(value => value.slice(0, 10)), datasets: [
      { label: 'Medido CERTE', data: fullCampaign.measured || [], borderColor: measuredColor, backgroundColor: measuredColor, pointRadius: 0, borderWidth: 1.3, spanGaps: false },
      { label: 'Estimado MCP', data: fullCampaign.estimated || [], borderColor: estimatedColor, backgroundColor: estimatedColor, pointRadius: 0, borderDash: [4, 3], borderWidth: 1.0, spanGaps: true },
      { label: 'Serie combinada', data: fullCampaign.combined || [], borderColor: '#4A7BC8', backgroundColor: '#4A7BC8', pointRadius: 0, borderWidth: 1.7, spanGaps: true },
    ] },
    options: { ...baseOptions, interaction: { mode: 'index', intersect: false } },
  });

  const renderRose = (id, rose, title) => {
    renderDirectionalRoseChart(id, rose, title, true);
  };
  renderRose('mcp-chart-rose-measured', visual.wind_roses?.measured, 'Medido');
  renderRose('mcp-chart-rose-estimated', visual.wind_roses?.estimated, 'Estimado');
  const roseNote = document.getElementById('mcp-rose-note');
  if (roseNote) {
    const mae = visual.wind_roses?.direction_mae_deg;
    roseNote.textContent = visual.wind_roses
      ? `${visual.wind_roses.note || ''}${Number.isFinite(Number(mae)) ? ` · Error direccional medio: ${Number(mae).toFixed(1)}°` : ''}`
      : 'No hay datos de dirección suficientes para construir las rosas de validación.';
  }

  const annual = result.visualization?.annual_hindcast || {};
  putChart('mcp-chart-hindcast', {
    type: 'line',
    data: { labels: annual.years || [], datasets: [
      { label: 'Serie combinada', data: annual.long_term || [], borderColor: '#4A7BC8', backgroundColor: '#4A7BC8', borderWidth: 2, pointRadius: 2, spanGaps: true },
      { label: 'Observado', data: annual.measured || [], borderColor: measuredColor, backgroundColor: measuredColor, borderWidth: 1.5, pointRadius: 3, spanGaps: false },
      { label: 'Modelo', data: annual.estimated || [], borderColor: estimatedColor, backgroundColor: estimatedColor, borderDash: [5,3], borderWidth: 1.4, pointRadius: 1, spanGaps: true },
    ] },
    options: baseOptions,
  });

  const historical = result.visualization?.historical_monthly || {};
  putChart('mcp-chart-historical-full', {
    type: 'line',
    data: { labels: (historical.timestamps || []).map(value => value.slice(0, 7)), datasets: [
      { label: 'Hindcast combinado', data: historical.hindcast || [], borderColor: '#4A7BC8', backgroundColor: '#4A7BC822', pointRadius: 0, borderWidth: 1.5, fill: true, spanGaps: true },
      { label: 'Modelo MCP', data: historical.model || [], borderColor: estimatedColor, backgroundColor: estimatedColor, pointRadius: 0, borderDash: [4, 3], borderWidth: 1.0, spanGaps: true },
      { label: 'Medido CERTE', data: historical.measured || [], borderColor: measuredColor, backgroundColor: measuredColor, pointRadius: 0, borderWidth: 1.8, spanGaps: false },
    ] },
    options: baseOptions,
  });

  const future = result.visualization?.future_climate_25y || {};
  putChart('mcp-chart-future-25y', {
    type: 'line',
    data: { labels: future.years || [], datasets: [
      { label: 'P95', data: future.p95 || [], borderColor: '#7C6BC488', pointRadius: 0, borderWidth: 1 },
      { label: 'P05–P95', data: future.p05 || [], borderColor: '#7C6BC488', backgroundColor: '#7C6BC42d', pointRadius: 0, borderWidth: 1, fill: '-1' },
      { label: 'P50 climatológico', data: future.p50 || [], borderColor: '#4A7BC8', backgroundColor: '#4A7BC8', pointRadius: 2, borderWidth: 2 },
    ] },
    options: baseOptions,
  });
  const futureNote = document.getElementById('mcp-future-note');
  if (futureNote) futureNote.textContent = `${future.note || ''}${Number.isFinite(Number(future.iav_pct)) ? ` · Variabilidad interanual histórica: ${Number(future.iav_pct).toFixed(2)}%.` : ''}`;
}

async function publishMCPRun() {
  if (!WP.mcpRunId || singleStationMode() || WP.studyMode === 'reanalysis_only') return;
  const btn = document.getElementById('btn-mcp-publish');
  btn.disabled = true;
  try {
    const published = await api(`/api/mcp/runs/${encodeURIComponent(WP.mcpRunId)}/publish`, 'POST', { session_id: WP.sessionId });
    invalidateProjectWindResults();
    WP.activeDataSource = published.source_id;
    krig3ActivatePublishedSource();
    WP.windStats = null;
    WP.energyResult = null;
    WP.technoResult = null;
    WP.technoScenarios.integrated = { inputs: null, result: null, leveraged: null };
    WP.technoInputsInitialized = false;
    WP.technoInputsDirty = false;
    _riskData = null;
    ['te-risk-card', 'te-kmeans-card'].forEach(id => {
      const e = document.getElementById(id); if (e) e.style.display = 'none';
    });
    KRIG.calibrationId = null;
    KRIG.calibrationSignature = null;
    KRIG.calibration = null;
    KRIG.result = null;
    MCDA.lastSpatial = null;
    MCDA.lastAnalysis = null;
    MCDA.lastRanking = null;
    PARQUE.result = null;
    PARQUE.optResult = null;
    WP.referenceHeight = published.target_height_m || WP.referenceHeight || 80;
    applyReferenceHeightToUI(WP.referenceHeight);
    document.getElementById('mcp-publish-status').textContent = 'Publicado';
    document.getElementById('mcp-publish-status').className = 'badge badge--success';
    toast('Hindcast MCP publicado como fuente del proyecto.', 'success');
    document.querySelector('.nav-btn[data-module="resumen"]')?.click();
    if (typeof runResumenPipeline === 'function') setTimeout(() => runResumenPipeline(), 250);
  } catch (e) {
    toast(`No se pudo publicar: ${e.message}`, 'error');
    btn.disabled = false;
  }
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
  sourceChoice: '', singleReference: null, areaKey: null,
  catalogStatus: 'idle', catalogPromise: null, preparing: false,
  sites: [],
  visibleSites: [],
  master: null,
  observedStation: null,
  mcpReferences: new Set(),
  studyMode: null,
  mcpCatalog: null,
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
  dark:    { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
             attr: '© Esri · © OpenStreetMap contributors', subdomains: '' },
  voyager: { url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
             attr: '© Esri · © OpenStreetMap contributors', subdomains: '' },
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
  document.getElementById('globe-source-choice')?.addEventListener('change', e => {
    GLOBE.sourceChoice = e.target.value;
    globeRenderStationList();
  });
  document.getElementById('globe-single-reference')?.addEventListener('change', e => {
    GLOBE.singleReference = e.target.value || null;
    globeUpdateContinueState();
  });
  document.getElementById('btn-globe-continue-single')?.addEventListener('click', continueGlobeToSingleStation);
  document.getElementById('btn-globe-retry-catalog')?.addEventListener('click', async () => {
    GLOBE.catalogStatus = 'idle';
    await globePreloadSites();
  });
  document.getElementById('btn-globe-continue-mcp')?.addEventListener('click', continueGlobeToMCP);
  document.getElementById('btn-globe-continue-reanalysis')?.addEventListener('click', continueGlobeToReanalysis);

  // Auto-advance loader → app after 2.4s
  setTimeout(() => switchToGlobeApp(), 2400);
  // Click loader to advance early
  document.getElementById('globe-loader')?.addEventListener('click', () => switchToGlobeApp());

  // Imported snapshots already contain the stations and their statistics.
  if (!WP.importedProject) globePreloadSites();
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
  if (GLOBE.catalogPromise) return GLOBE.catalogPromise;
  GLOBE.catalogStatus = 'loading';
  globeUpdateContinueState();
  GLOBE.catalogPromise = globeLoadCatalog();
  try { await GLOBE.catalogPromise; }
  finally { GLOBE.catalogPromise = null; }
}

async function globeLoadCatalog() {
  const yearSel = document.getElementById('krig3-year');
  const year = yearSel ? (parseInt(yearSel.value) || 2018) : 2018;

  // Catálogo MCP tipificado: una observación local y referencias ERA5/MERRA-2.
  try {
    const rl = await api(`/api/mcp/catalog?session_id=${encodeURIComponent(WP.sessionId)}`, 'GET');
    GLOBE.mcpCatalog = rl;
    const normalize = row => ({
      ...row,
      filename: row.primary_file_id || row.file_ids?.[0] || row.station_id,
      lat: Number(row.latitude), lon: Number(row.longitude),
      source: String(row.provider || '').toLowerCase(),
    });
    GLOBE.sites = [...(rl.observations || []).map(normalize), ...(rl.references || []).map(normalize)];
    GLOBE.sitesLight = true;
    GLOBE.catalogStatus = 'ready';
    if (GLOBE.poi) await globeFilterByRadius();
    updateGlobeStationsBadge();
    console.log(`[globe] ${GLOBE.sites.length} puntos físicos (light) listos`);
  } catch (e) {
    GLOBE.catalogStatus = 'error';
    globeRenderStationList();
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
      // Conserva coordenadas, rol y procedencia del catálogo tipificado. El
      // endpoint heredado sólo aporta estadísticas y deriva sus coordenadas
      // del nombre de archivo, que puede ser un alias geográfico incorrecto.
      GLOBE.sites = GLOBE.sites.map(s => s.data_kind === 'observed'
        ? s
        : ({ ...(byName.get(s.filename) || {}), ...s }));
      GLOBE.sitesLight = false;
      updateGlobeStationsBadge();
      console.log(`[globe] enriquecimiento completo (año ${year})`);
    }
  } catch (e) {
    console.warn('[globe] enriquecimiento falló (pines siguen visibles):', e);
  }
}

function globeSiteKey(site) {
  return site?.station_id || site?.filename || '';
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
    el.textContent = `${n} puntos meteorológicos disponibles`;
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
        const isMaster = (GLOBE.master === globeSiteKey(s));
        const hitR = isMaster ? 24 : 18;
        // hitbox vertical: rectángulo desde la base hacia arriba
        const dx = mx - c[0];
        const dy = my - (c[1] - 9);   // centroide del icono está por encima de la base
        if (Math.abs(dx) <= hitR && dy <= hitR && dy >= -16) {
          handleGlobeStationPick(globeSiteKey(s));
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
      const isMaster = (GLOBE.master === globeSiteKey(s));
      const isSelected = GLOBE.mcpReferences.has(globeSiteKey(s));
      drawTurbineIcon(ctx, c[0], c[1], isMaster, isLight, GLOBE.poiHaloPhase, s.provider, isSelected);
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
function drawTurbineIcon(ctx, x, y, isMaster, isLight, phase, provider = '', isSelected = false) {
  const scale = isMaster ? 1.45 : 1.0;
  const mastH = 18 * scale;
  const mastW = 2.4 * scale;
  const hubR  = 3.2 * scale;
  const bladeL = 11 * scale;
  const providerKey = String(provider || '').toUpperCase();
  const refDark = isSelected ? '#0F6E56' : (providerKey === 'ERA5' ? '#0F5FA8' : '#5B3F99');
  const refLight = isSelected ? '#8FE3C5' : (providerKey === 'ERA5' ? '#82BFFF' : '#C4B5FD');

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
    mastGrad.addColorStop(0,    refDark);
    mastGrad.addColorStop(0.45, refLight);
    mastGrad.addColorStop(1,    refDark);
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
      bladeGrad.addColorStop(1, refDark);
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
  hubGrad.addColorStop(1, isMaster ? '#B45309' : refDark);
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
  if (GLOBE.preparing) return;
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
  if (GLOBE.preparing) return;
  GLOBE.areaKey = null;
  GLOBE.sourceChoice = '';
  GLOBE.singleReference = null;
  GLOBE.poi = null;
  GLOBE.visibleSites = [];
  GLOBE.master = null;
  GLOBE.observedStation = null;
  GLOBE.mcpReferences.clear();
  GLOBE.studyMode = null;
  document.getElementById('btn-globe-clear-poi')?.classList.remove('is-visible');
  document.getElementById('globe-panel')?.classList.remove('is-open');
  const status = document.getElementById('globe-status');
  if (status) status.textContent = 'Marca un sitio en el mapa o busca una dirección';
  leafletClearAll();
  globeUpdateContinueState();
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
  if (GLOBE.catalogStatus === 'idle' && !WP.importedProject) await globePreloadSites();
  if (!GLOBE.poi) return;
  const km = Math.max(1, parseFloat(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM);
  const inside = [];
  GLOBE.sites.forEach(s => {
    const d = globeHaversineKm(GLOBE.poi.lat, GLOBE.poi.lon, s.lat, s.lon);
    if (d <= km) inside.push({ ...s, _dist: d });
  });
  inside.sort((a, b) => a._dist - b._dist);
  GLOBE.visibleSites = inside;
  const areaKey = `${GLOBE.poi.lat},${GLOBE.poi.lon}|${km}|${inside.map(globeSiteKey).join(',')}`;
  if (GLOBE.areaKey && GLOBE.areaKey !== areaKey) GLOBE.sourceChoice = '';
  GLOBE.areaKey = areaKey;
  const keys = new Set(inside.map(globeSiteKey));
  GLOBE.mcpReferences = new Set([...GLOBE.mcpReferences].filter(key => keys.has(key)));
  if (!keys.has(GLOBE.master)) { GLOBE.master = null; GLOBE.observedStation = null; }
  if (!keys.has(GLOBE.singleReference)) GLOBE.singleReference = null;
  const observed = inside.filter(site => site.data_kind === 'observed');
  if (observed.length === 1) {
    GLOBE.observedStation = observed[0];
    GLOBE.master = globeSiteKey(observed[0]);
  }
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
    const message = GLOBE.catalogStatus === 'loading' ? 'Cargando estaciones…'
      : GLOBE.catalogStatus === 'error' ? 'No se pudo cargar el catálogo. Reintenta para comprobar las estaciones del área.'
      : 'Agrega una estación real o virtual para continuar, o amplía el radio.';
    list.innerHTML = `<div class="globe-panel__empty">${message}</div>`;
    if (title) title.textContent = '0 puntos';
    const sub = document.getElementById('globe-panel-sub');
    if (sub) sub.textContent = 'No hay estaciones observadas ni referencias de reanálisis dentro del radio actual.';
    globeUpdateContinueState();
    return;
  }
  if (title) title.textContent = `${GLOBE.visibleSites.length} puntos cercanos`;
  const observed = GLOBE.visibleSites.filter(s => s.data_kind === 'observed');
  const references = GLOBE.visibleSites.filter(s => s.data_kind === 'reanalysis');
  const sub = document.getElementById('globe-panel-sub');
  const row = s => {
    const key = globeSiteKey(s);
    const isMaster = (GLOBE.master === key);
    const isSelected = GLOBE.sourceChoice === 'single_reference' ? GLOBE.singleReference === key : GLOBE.mcpReferences.has(key);
    const src = (s.provider || s.source || '').toUpperCase();
    const aliasCount = Number(s.alias_count || s.file_ids?.length || 1);
    const coordNote = s.coordinate_status === 'corrected_filename_mismatch' ? ' · coordenada interna verificada' : '';
    const aliasNote = aliasCount > 1 ? ` · ${aliasCount} archivos agrupados` : '';
    return `<div class="globe-station ${isMaster ? 'is-master is-observed' : ''} ${isSelected ? 'is-selected' : ''}" data-site-id="${key}">
      <div class="globe-station__star">${isMaster ? '★' : (isSelected ? '✓' : '○')}</div>
      <div class="globe-station__body">
        <div class="globe-station__name">${s.display_name || s.filename}</div>
        <div class="globe-station__meta">${s.data_kind === 'observed' ? 'OBS · ' : ''}${src} · ${s.lat.toFixed(5)}°, ${s.lon.toFixed(5)}°${s.state ? ' · ' + s.state : ''}${coordNote}${aliasNote}</div>
      </div>
      <div class="globe-station__dist">${s._dist.toFixed(0)} km</div>
    </div>`;
  };
  list.innerHTML = `<div class="globe-panel__section">Estación observada principal</div>` +
    (observed.length ? observed.map(row).join('') : '<div class="globe-panel__empty">No hay observaciones en el radio. Puedes cargar una propia.</div>') +
    `<div class="globe-panel__section">Referencias ERA5 / MERRA-2</div>` +
    (references.length ? references.map(row).join('') : '<div class="globe-panel__empty">No hay referencias en el radio.</div>');
  list.querySelectorAll('.globe-station').forEach(el => {
    el.addEventListener('click', () => handleGlobeStationPick(el.dataset.siteId));
  });
  globeUpdateContinueState();
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

async function handleGlobeStationPick(identifier) {
  if (!identifier || GLOBE.preparing) return;
  const site = GLOBE.visibleSites.find(s => globeSiteKey(s) === identifier) ||
               GLOBE.sites.find(s => globeSiteKey(s) === identifier);
  if (!site) return;
  if (site.data_kind === 'observed') {
    const deselect = GLOBE.master === identifier && globeAreaOptions(GLOBE.visibleSites).observed.length > 1;
    GLOBE.master = deselect ? null : identifier;
    GLOBE.observedStation = deselect ? null : site;
  } else if (GLOBE.sourceChoice === 'single_reference') {
    GLOBE.singleReference = identifier;
  } else if (GLOBE.mcpReferences.has(identifier)) {
    GLOBE.mcpReferences.delete(identifier);
  } else {
    GLOBE.mcpReferences.add(identifier);
  }
  globeRenderStationList();
}

function globeAreaOptions(sites) {
  const observed = sites.filter(s => s.data_kind === 'observed');
  const references = sites.filter(s => s.data_kind === 'reanalysis');
  return { observed, references, kind: observed.length
    ? (references.length ? 'mixed' : 'observed') : (references.length ? 'virtual' : 'empty') };
}

function globeUpdateContinueState() {
  const { observed, references, kind } = globeAreaOptions(GLOBE.visibleSites);
  const available = !!GLOBE.poi && (WP.importedProject || GLOBE.catalogStatus === 'ready');
  const choice = document.getElementById('globe-source-choice');
  const single = document.getElementById('globe-single-reference');
  const sub = document.getElementById('globe-panel-sub');
  const options = kind === 'mixed'
    ? [['mcp', 'Realizar MCP'], ['observed_only', 'Usar solo la estación real']]
    : kind === 'virtual'
    ? [['reanalysis_only', `Procesar el conjunto (${references.length} virtuales; mínimo 3)`], ['single_reference', 'Elegir una sola referencia virtual']]
    : kind === 'observed' ? [['observed_only', 'Usar estación real observada']] : [];
  if (!options.some(([value]) => value === GLOBE.sourceChoice)) GLOBE.sourceChoice = '';
  if (kind === 'observed') GLOBE.sourceChoice = 'observed_only';
  if (choice) {
    choice.innerHTML = '<option value="">Elige cómo usar los datos del área…</option>' + options.map(([value, label]) =>
      `<option value="${value}" ${value === 'reanalysis_only' && references.length < 3 ? 'disabled' : ''}>${label}</option>`).join('');
    choice.value = GLOBE.sourceChoice;
    choice.style.display = available && options.length > 1 ? '' : 'none';
    choice.disabled = GLOBE.preparing;
  }
  if (references.length === 1) GLOBE.singleReference = globeSiteKey(references[0]);
  if (single) {
    single.innerHTML = '<option value="">Selecciona una referencia virtual…</option>' + references.map(site =>
      `<option value="${mcpEsc(globeSiteKey(site))}">${mcpEsc(site.display_name || site.filename)}</option>`).join('');
    single.value = GLOBE.singleReference || '';
    single.style.display = available && GLOBE.sourceChoice === 'single_reference' ? '' : 'none';
    single.disabled = GLOBE.preparing;
  }
  if (sub) sub.textContent = !available
    ? (GLOBE.catalogStatus === 'error' ? 'El catálogo no está disponible. Reintenta la carga.' : 'Selecciona un área; espera a que termine la carga del catálogo.')
    : kind === 'empty' ? 'Agrega una estación real o virtual para continuar.'
    : kind === 'mixed' ? '¿Deseas realizar MCP o usar solo una estación real como fuente temporal?'
    : kind === 'observed' ? (observed.length === 1 ? 'La estación real se usará automáticamente como fuente observada del proyecto.' : 'Elige una estación real de la lista como fuente observada del proyecto.')
    : references.length < 3 ? 'Elige una referencia virtual, o agrega más estaciones para procesar un conjunto de al menos tres.'
    : '¿Deseas procesar las virtuales del área o elegir una sola referencia?';
  const configure = (id, show, ready, label) => {
    const button = document.getElementById(id);
    if (!button) return;
    button.style.display = available && show ? '' : 'none';
    button.disabled = !ready || GLOBE.preparing;
    button.textContent = GLOBE.preparing ? 'Preparando fuente…' : label;
  };
  const hasObserved = observed.some(site => globeSiteKey(site) === GLOBE.master);
  configure('btn-globe-continue-mcp', GLOBE.sourceChoice === 'mcp', hasObserved && GLOBE.mcpReferences.size > 0,
    'Continuar a MCP Lab →');
  configure('btn-globe-continue-reanalysis', GLOBE.sourceChoice === 'reanalysis_only', references.length >= 3,
    `Preparar recurso con ${references.length} virtuales →`);
  configure('btn-globe-continue-single', ['observed_only', 'single_reference'].includes(GLOBE.sourceChoice),
    GLOBE.sourceChoice === 'observed_only' ? hasObserved : references.some(site => globeSiteKey(site) === GLOBE.singleReference),
    GLOBE.sourceChoice === 'observed_only' ? 'Continuar con estación real (observed) →' : 'Continuar con referencia virtual →');
  const retry = document.getElementById('btn-globe-retry-catalog');
  if (retry) retry.style.display = GLOBE.catalogStatus === 'error' ? '' : 'none';
}

function globeSetPreparing(value) {
  GLOBE.preparing = value;
  ['globe-radius', 'btn-globe-open-upload', 'btn-globe-clear-poi'].forEach(id => {
    const element = document.getElementById(id);
    if (element) element.disabled = value;
  });
  globeUpdateContinueState();
}

function invalidateProjectWindResults() {
  WP.windSourceEpoch = (WP.windSourceEpoch || 0) + 1;
  for (const key of ['windStats', 'advancedResult', 'energyResult', 'technoResult', 'qcResult',
    'hybridResult', 'energySimulation', 'farmResult', 'farmLayout', 'turbineComparison']) WP[key] = null;
  WP.technoScenarios.integrated = { inputs: null, result: null, leveraged: null };
  WP.technoInputsInitialized = false;
  WP.technoInputsDirty = false;
  _riskData = null;
  MCDA.lastSpatial = null; MCDA.lastAnalysis = null; MCDA.lastRanking = null;
  PARQUE.result = null; PARQUE.optResult = null; PARQUE.wakeProfile = null;
  MAPA.result = null; CLUSTER.result = null;
  KRIG.result = null; KRIG.task_id = null;
  krig3InvalidateCalibration('cambió la fuente temporal del proyecto');
  Object.keys(WP.charts || {}).forEach(id => { WP.charts[id]?.destroy?.(); delete WP.charts[id]; });
  ['te-risk-card', 'te-kmeans-card', 'adv-kpi-row', 'adv-hub-row'].forEach(id => {
    const element = document.getElementById(id); if (element) element.style.display = 'none';
  });
}

async function continueGlobeToSingleStation() {
  if (GLOBE.preparing || !GLOBE.poi) return;
  const stationId = GLOBE.sourceChoice === 'observed_only' ? GLOBE.master
    : GLOBE.sourceChoice === 'single_reference' ? GLOBE.singleReference : null;
  const station = GLOBE.visibleSites.find(site => globeSiteKey(site) === stationId);
  if (!station) return;
  const targetHeight = Number(WP.referenceHeight) || Number(station.measurement_height_m) || 80;
  const areaKey = GLOBE.areaKey;
  globeSetPreparing(true);
  try {
    const study = await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/single-station-study`, 'PUT', {
      station_id: stationId, poi: GLOBE.poi,
      radius_km: Number(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM,
      target_height_m: targetHeight,
    });
    if (areaKey !== GLOBE.areaKey) return;
    invalidateProjectWindResults();
    WP.singleStationStudy = study;
    WP.studyMode = study.study_mode; GLOBE.studyMode = study.study_mode;
    WP.activeDataSource = study.source_id;
    WP.mcpStudy = null; WP.mcpRunId = null; WP.mcpResult = null; WP.reanalysisStudy = null;
    WP.referenceHeight = study.target_height_m;
    WP.referenceMethod = 'hellmann';
    WP.meta = { display_name: study.published_source.label, format: study.study_mode,
      latitude: station.lat, longitude: station.lon, wind_height_m: study.target_height_m,
      data_source: study.source_id, station_id: stationId,
      date_range: [study.published_source.period_start, study.published_source.period_end],
      n_rows: study.published_source.rows };
    applyReferenceHeightToUI(WP.referenceHeight);
    KRIG.guidedMcp = false;
    KRIG.sites = []; KRIG.selected.clear(); KRIG.principal = null;
    KRIG.poi = { ...study.poi }; KRIG._appliedGlobePoi = null;
    const mode = document.getElementById('krig3-generation-mode');
    if (mode) mode.value = 'mcp_advanced';
    krig3SyncGenerationMode();
    collapseGlobeStage();
    document.querySelector('.nav-btn[data-module="resumen"]')?.click();
    toast(`Fuente temporal: ${study.published_source.label}. Kriging conserva sus estaciones espaciales.`, 'success');
    if (typeof runResumenPipeline === 'function') setTimeout(() => runResumenPipeline(), 250);
  } catch (e) { toast(`No se pudo preparar la estación: ${e.message}`, 'error'); }
  finally { globeSetPreparing(false); }
}

async function continueGlobeToMCP() {
  if (GLOBE.preparing || GLOBE.sourceChoice !== 'mcp' || !GLOBE.observedStation || !GLOBE.mcpReferences.size) return;
  globeSetPreparing(true);
  const radius = parseFloat(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM;
  const inheritedHeight = Number(WP.referenceHeight);
  const initialTargetHeight = Number.isFinite(inheritedHeight) && inheritedHeight >= 1 && inheritedHeight <= 300
    ? inheritedHeight : 80;
  try {
    const study = await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/mcp-study`, 'PUT', {
      observed_station_id: globeSiteKey(GLOBE.observedStation),
      reference_station_ids: [...GLOBE.mcpReferences],
      poi: GLOBE.poi || { lat: GLOBE.observedStation.lat, lon: GLOBE.observedStation.lon },
      radius_km: radius,
      target_height_m: initialTargetHeight,
    });
    invalidateProjectWindResults();
    WP.singleStationStudy = null;
    WP.mcpStudy = study;
    WP.studyMode = 'mcp_observed';
    GLOBE.studyMode = 'mcp_observed';
    if (GLOBE.mcpReferences.size === 1) toast('Una sola referencia es válida; se recomiendan varias para comparar.', 'warn');
    collapseGlobeStage();
    document.querySelector('.nav-btn[data-module="mcp"]')?.click();
    await loadMCPStudy();
  } catch (e) {
    toast(`No se pudo crear el estudio MCP: ${e.message}`, 'error');
  } finally { globeSetPreparing(false); }
}

async function continueGlobeToReanalysis() {
  if (GLOBE.preparing || GLOBE.sourceChoice !== 'reanalysis_only' || !GLOBE.poi || globeAreaOptions(GLOBE.visibleSites).observed.length) return;
  const references = GLOBE.visibleSites.filter(site => site.data_kind === 'reanalysis');
  if (references.length < 3) {
    toast('Amplía el radio: se requieren al menos tres referencias ERA5/MERRA-2.', 'warn');
    return;
  }
  const radius = parseFloat(document.getElementById('globe-radius')?.value) || GLOBE_RADIUS_DEFAULT_KM;
  const inheritedHeight = Number(WP.referenceHeight);
  const targetHeight = Number.isFinite(inheritedHeight) && inheritedHeight >= 1 && inheritedHeight <= 300
    ? inheritedHeight : 80;
  globeSetPreparing(true);
  const actionButton = document.getElementById('btn-globe-continue-reanalysis');
  if (actionButton) {
    actionButton.disabled = true;
    actionButton.textContent = `Preparando ${references.length} series ERA5/MERRA-2…`;
  }
  try {
    const study = await api(`/api/sessions/${encodeURIComponent(WP.sessionId)}/reanalysis-study`, 'PUT', {
      reference_station_ids: references.map(globeSiteKey),
      poi: GLOBE.poi,
      radius_km: radius,
      target_height_m: targetHeight,
    });
    invalidateProjectWindResults();
    WP.singleStationStudy = null;
    WP.reanalysisStudy = study;
    WP.studyMode = 'reanalysis_only';
    GLOBE.studyMode = 'reanalysis_only';
    WP.activeDataSource = study.active_data_source || study.source_id;
    WP.mcpStudy = null;
    WP.mcpRunId = null;
    WP.referenceHeight = study.target_height_m || targetHeight;
    WP.meta = {
      display_name: 'Recurso de reanálisis del área',
      format: 'reanalysis_area',
      reanalysis_type: 'ERA5 + MERRA-2',
      latitude: study.poi.lat,
      longitude: study.poi.lon,
      wind_height_m: WP.referenceHeight,
      station_count: study.reference_stations?.length || references.length,
      aggregation_method: study.published_source?.aggregation_method,
    };
    applyReferenceHeightToUI(WP.referenceHeight);
    WP.windStats = null;
    WP.energyResult = null;
    WP.technoResult = null;
    WP.technoScenarios.integrated = { inputs: null, result: null, leveraged: null };
    WP.technoInputsInitialized = false;
    WP.technoInputsDirty = false;
    _riskData = null;
    ['te-risk-card', 'te-kmeans-card'].forEach(id => {
      const e = document.getElementById(id); if (e) e.style.display = 'none';
    });
    MCDA.lastSpatial = null;
    MCDA.lastAnalysis = null;
    MCDA.lastRanking = null;
    PARQUE.result = null;
    PARQUE.optResult = null;
    KRIG.guidedMcp = false;
    KRIG.sites = [];
    KRIG.selected.clear();
    KRIG.principal = null;
    KRIG.result = null;
    KRIG.poi = { ...GLOBE.poi };
    KRIG._appliedGlobePoi = null;
    krig3InvalidateCalibration('se cambió a un estudio sin estación observada');
    const mode = document.getElementById('krig3-generation-mode');
    if (mode) mode.value = 'mcp_advanced';
    const manual = document.getElementById('krig3-manual-mode');
    if (manual) manual.checked = false;
    const radiusInput = document.getElementById('krig3-poi-radius');
    if (radiusInput) radiusInput.value = String(radius);
    krig3SyncGenerationMode();
    collapseGlobeStage();
    document.querySelector('.nav-btn[data-module="resumen"]')?.click();
    toast(`Recurso del área preparado con ${references.length} referencias ERA5/MERRA-2. MCP se omitió.`, 'success');
    if (typeof runResumenPipeline === 'function') setTimeout(() => runResumenPipeline(), 250);
  } catch (e) {
    toast(`No se pudo crear el estudio de reanálisis: ${e.message}`, 'error');
  } finally { globeSetPreparing(false); }
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
        row('AEP bruto (sin pérdidas, sin estela)', sim.aep_gross_mwh ? (Number(sim.aep_gross_mwh)/1000).toFixed(3) : '—', 'GWh/año') +
        row('AEP s/estela (con disp. + pérd. eléc.)', sim.aep_no_wake_mwh ? (Number(sim.aep_no_wake_mwh)/1000).toFixed(3) : '—', 'GWh/año') +
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

  const openModal  = () => {
    const lat = document.getElementById('globe-upload-lat');
    const lon = document.getElementById('globe-upload-lon');
    if (lat && GLOBE.poi) lat.value = GLOBE.poi.lat.toFixed(5);
    if (lon && GLOBE.poi) lon.value = GLOBE.poi.lon.toFixed(5);
    modal?.classList.add('is-open');
  };
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
  if (GLOBE.preparing) return;
  const lat = parseFloat(document.getElementById('globe-upload-lat')?.value);
  const lon = parseFloat(document.getElementById('globe-upload-lon')?.value);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    toast('Define coordenadas válidas para el archivo.', 'warn'); return;
  }
  const kind = source === 'observed' ? 'observed' : 'reanalysis';
  const provider = source === 'era5' ? 'ERA5' : source === 'merra2' ? 'MERRA2' : 'USER';
  const form = new FormData();
  form.append('session_id', WP.sessionId);
  form.append('file', file);
  form.append('data_kind', kind);
  form.append('provider', provider);
  form.append('display_name', document.getElementById('globe-upload-name')?.value || file.name);
  form.append('latitude', lat);
  form.append('longitude', lon);
  form.append('measurement_height_m', document.getElementById('globe-upload-height')?.value || 80);
  form.append('timezone', document.getElementById('globe-upload-timezone')?.value || 'UTC');
  try {
    const response = await apiFD('/api/mcp/uploads', form);
    const row = response.station;
    const site = { ...row, filename: row.primary_file_id || row.file_ids?.[0] || row.station_id, lat: Number(row.latitude), lon: Number(row.longitude), source: String(row.provider || '').toLowerCase() };
    GLOBE.sites = GLOBE.sites.filter(item => globeSiteKey(item) !== row.station_id);
    GLOBE.sites.push(site);
    if (kind === 'observed') {
      GLOBE.observedStation = site;
      GLOBE.master = row.station_id;
    } else {
      GLOBE.mcpReferences.add(row.station_id);
    }
    document.getElementById('globe-modal')?.classList.remove('is-open');
    await globeFilterByRadius();
    toast(`${row.display_name} añadido como ${kind === 'observed' ? 'estación observada' : 'referencia de reanálisis del área'}`, response.warning ? 'warn' : 'success');
  } catch (e) {
    toast(`No se pudo registrar el archivo: ${e.message}`, 'error');
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
      if (id.startsWith('chart-')) {
        try { WP.charts[id]?.destroy?.(); } catch (e) {}
        WP.charts[id] = null;
      }
    });
  }

  // 4) Veredicto y header del Resumen
  const vBox  = document.getElementById('resumen-verdict');
  const vVal  = document.getElementById('resumen-verdict-value');
  const vSub  = document.getElementById('resumen-verdict-sub');
  const vCta  = document.getElementById('btn-go-to-park-config');
  const title = document.getElementById('resumen-site-title');
  const sub   = document.getElementById('resumen-site-sub');
  vBox?.classList.remove('is-good', 'is-marginal', 'is-poor');
  if (vVal) vVal.textContent = '—';
  if (vSub) vSub.textContent = 'Calcula el análisis para ver la evaluación del recurso';
  vCta?.setAttribute('disabled', '');
  if (title) title.textContent = '— sin datos —';
  if (sub)   sub.textContent   = 'Usa una estación real con MCP o un recurso de área ERA5/MERRA-2 sin MCP.';

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

  setStatus?.('Globe reiniciado — selecciona un área y decide si existe una estación real.');
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
  // Debe ser idempotente. Aunque el estado ya diga "leaflet", todavía puede
  // faltar sincronizar la clase visual o inicializar el mapa (p. ej. al importar).
  GLOBE.mode = mode;
  const app = document.getElementById('globe-app');
  app?.classList.toggle('is-mode-leaflet', mode === 'leaflet');
  document.querySelectorAll('#globe-modeswitch .globe-mode__btn').forEach(b => {
    b.classList.toggle('is-active', b.dataset.mode === mode);
  });
  if (mode === 'leaflet') {
    initGlobeLeaflet();
    GLOBE.autoSpin = false;
    requestAnimationFrame(() => {
      GLOBE.leafletMap?.invalidateSize();
      leafletRenderPoi();
      leafletRenderStations();
    });
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
    const isMaster = (GLOBE.master === globeSiteKey(s));
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
    m.on('click', () => handleGlobeStationPick(globeSiteKey(s)));
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
  if (!GLOBE.leafletMap) {
    toast('No fue posible inicializar el mapa 2D de precisión.', 'error');
    return;
  }
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
  document.getElementById('btn-go-to-park-config')?.addEventListener('click', () => {
    const parkConfigBtn = document.querySelector('.nav-btn[data-module="park-config"]');
    parkConfigBtn?.click();
  });
  // Configuración del parque concentra únicamente los dos archivos requeridos:
  // demanda (cuando hay autoconsumo) y PML. FX y tasa son campos manuales.
  const movements = [
    ['#market-demand-select', 'park-demand-file-slot'],
    ['#market-pml-select', 'park-pml-file-slot'],
  ];
  movements.forEach(([sel, destinationId]) => {
    const select = document.querySelector(sel);
    const destination = document.getElementById(destinationId);
    if (!select || !destination) return;
    const row = select.closest('.market-data-row');
    if (!row || row.dataset.movedToParkConfig === '1') return;
    row.dataset.movedToParkConfig = '1';
    row.classList.remove('mb-3');
    destination.appendChild(row);
  });

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
  krig3ActivatePublishedSource();
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
  krig3PullProjectParams();

  // Asegura que los sitios estén cargados
  if (typeof KRIG !== 'undefined' && KRIG && (!KRIG.sites?.length) && typeof krig3LoadSites === 'function') {
    try { await krig3LoadSites(); } catch (e) {}
  }

  // Marcar POI usando la lógica existente de Kriging (solo primera vez).
  if (typeof krig3PlacePoi === 'function') {
    await krig3PlacePoi({ lat: GLOBE.poi.lat, lng: GLOBE.poi.lon });
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

function parkMarketTrajectory(category) {
  if (category !== 'pml') return null;
  const stored = WP.parkConfiguration?.market_models?.pml?.annual;
  return Array.isArray(stored) && stored.length
    ? stored.map(Number).filter(Number.isFinite) : null;
}

function parkEquivalentDesignInputs() {
  const config = WP.parkConfiguration || {};
  const configuredHorizon = Number(config.project_horizon_years);
  const horizon = Number.isFinite(configuredHorizon) ? configuredHorizon : null;
  const priceSeries = horizon == null
    ? [] : (parkMarketTrajectory('pml') || []).slice(0, horizon);
  const baseRate = Number(config.base_interest_rate_pct);
  const baseFx = Number(config.base_fx_mxn_usd);
  const rate = Number.isFinite(baseRate) && baseRate >= 0 ? baseRate / 100 : null;
  const fx = Number.isFinite(baseFx) && baseFx > 0 ? baseFx : null;
  const weights = rate == null ? []
    : Array.from({ length: Math.max(priceSeries.length, 1) }, (_, index) =>
      1 / Math.pow(1 + rate, index + 1));
  const priceRaw = priceSeries.length && weights.length
    ? priceSeries.reduce((sum, value, index) => sum + value * weights[index], 0) /
      weights.slice(0, priceSeries.length).reduce((sum, value) => sum + value, 0)
    : null;
  const price = priceRaw == null ? null
    : _pmlToUsdKwh(priceRaw, fx, config.market_models?.pml?.unit);
  const demand = config.commercialization_mode !== 'total_sale'
    ? Number(config.demand_series?.annual_mwh) || Number(config.annual_demand_mwh) || 0
    : 0;
  return {
    demand_mwh: demand,
    tasa: rate,
    price_usd_kwh: price,
    price_mxn_per_usd: fx,
    trajectories: {
      pml: priceSeries,
      horizon_years: horizon,
      configuration_revision: config.configuration_revision || 0,
      commercialization_mode: config.commercialization_mode || 'total_sale',
      equivalence_method: 'fixed_pml_discounted_with_scalar_rate',
    },
  };
}

function krig3PullProjectParams() {
  if (typeof WP === 'undefined') return;
  const equivalent = parkEquivalentDesignInputs();
  const tasa = Number(WP.parkConfiguration?.base_interest_rate_pct);
  if (Number.isFinite(tasa) && tasa >= 0) {
    ['krig3-tasa', 'mcda-tasa'].forEach(id => {
      const input = document.getElementById(id);
      if (input) input.value = tasa.toFixed(2);
    });
  }
  const fx = Number(WP.parkConfiguration?.base_fx_mxn_usd);
  if (Number.isFinite(fx) && fx > 0) {
    ['krig3-fx', 'mcda-fx'].forEach(id => {
      const input = document.getElementById(id);
      if (input) input.value = fx.toFixed(2);
    });
  }
  if (Number.isFinite(equivalent.price_usd_kwh)) {
    ['krig3-price', 'mcda-price'].forEach(id => {
      const input = document.getElementById(id);
      if (input) input.value = equivalent.price_usd_kwh.toFixed(4);
    });
  }
  const demand = Math.max(0, Number(equivalent.demand_mwh) || 0);
  ['krig3-demand', 'mcda-demand', 'parque-demand', 'te-demand'].forEach(id => {
    const input = document.getElementById(id);
    if (input) input.value = Math.round(demand).toString();
  });
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
    const source = WP.windStats?.data_source;
    if (source?.kind === 'mcp') parts.push(`Reconstrucción histórica MCP · ${source.label || source.source_id}`);
    else if (source?.label) parts.push(`${source.kind === 'reanalysis' ? 'Referencia virtual' : 'Fuente observada'} · ${source.label}`);
    if (singleStationMode() && source) {
      parts.push(`Fuente temporal: ${source.source_id} · sin MCP`);
      if (source.period_start && source.period_end) parts.push(windSourcePeriodText(source));
      if (source.file_ids?.length) parts.push(`Archivos: ${source.file_ids.join(', ')}`);
      if (source.transformations?.some(step => step.method === 'hellmann')) parts.push(`Hellmann: ${source.source_height_m} → ${source.target_height_m} m`);
    }
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
  const vCta  = document.getElementById('btn-go-to-park-config');
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

function renderSavedOverview(r) {
  refreshResourcePeriod();
  const period = document.getElementById('overview-source-period');
  if (period) {
    const source = r?.data_source;
    const overview = r?.overview;
    period.textContent = source && overview
      ? `${source.label || source.source_id} · ${source.period_selection === 'full_available_record' ? 'Todo el registro disponible · ' : ''}${windSourcePeriodText(source)}${source.years?.length ? ' · Años: ' + source.years.join(', ') : ''}${overview.time_resolution_min ? ' · Registros de ' + overview.time_resolution_min + ' min' : ''}`
      : '';
  }
    renderOverviewKPIs(r.overview);
    renderDailyTS(r.overview?.daily_series);
    renderHistogramOverview(r.histogram);
    renderMClimOverview(r.climatology);
    renderHourlyOverview(r.diurnal);
    renderPowerDensityChart(r.overview);
    document.getElementById('overview-status').textContent = 'Listo';
}

function renderSavedWind(r) {
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
}

function renderSavedAdvanced(r) {
    // KPI row
    const kpiRow = document.getElementById('adv-kpi-row');
    kpiRow.style.display = '';
    const turbulence = r.turbulence || {};
    const measuredTI  = turbulence.measured_10min || {};
    const hourlyVar   = turbulence.hourly_variability || r.wind_speed_variability || {};
    const historicalTI = turbulence.historical_model || {};
    const hasMeasuredTI = measuredTI.available === true;
    const ti = hasMeasuredTI ? measuredTI : hourlyVar;
    const sh = r.shear;
    const rp = r.return_periods;

    const _set = (id, val) => { const e = document.getElementById(id); if (e) e.textContent = val; };
    _set('adv-kpi-ti',    hasMeasuredTI && ti?.ti_overall != null ? (ti.ti_overall * 100).toFixed(1) + '%' : '—');
    _set('adv-kpi-ti15',  hasMeasuredTI && ti?.ti_15ms    != null ? (ti.ti_15ms    * 100).toFixed(1) + '%' : '—');
    _set('adv-kpi-source', hasMeasuredTI ? 'CERTE 10 min' : 'Sin TI medida');
    _set('adv-kpi-source-sub', hasMeasuredTI
      ? `Entrenamiento ${measuredTI.training_year} · ${Number(measuredTI.logger_pair_coverage_pct || 0).toFixed(1)}% cobertura nativa`
      : 'La serie horaria sólo aporta variabilidad');
    _set('adv-kpi-alpha', sh?.alpha != null ? sh.alpha.toFixed(3) : '—');
    _set('adv-kpi-v50',   rp?.v_50year != null ? rp.v_50year + ' m/s' : '—');

    const badgeSource = document.getElementById('adv-badge-ti-source');
    if (badgeSource) {
      badgeSource.textContent = hasMeasuredTI ? 'Medido · CERTE 10 min' : 'Proxy · horario';
      badgeSource.className = `cbadge ${hasMeasuredTI ? 'g' : 'r'}`;
      badgeSource.title = ti?.note || r.ti_warning || '';
    }
    _set('adv-ti-chart-title', hasMeasuredTI
      ? 'TI medida CERTE vs velocidad'
      : 'Variabilidad interhoraria vs velocidad · no es TI');
    const provenanceTitle = document.getElementById('adv-ti-provenance-title');
    const provenanceText  = document.getElementById('adv-ti-provenance');
    if (provenanceTitle) provenanceTitle.textContent = hasMeasuredTI
      ? `Turbulencia medida CERTE ${measuredTI.training_year}`
      : 'No hay turbulencia medida en la fuente activa';
    if (provenanceText) provenanceText.textContent = hasMeasuredTI
      ? `Se usan ${Number(measuredTI.N || 0).toLocaleString()} ventanas nativas de ${measuredTI.native_resolution_minutes || 10} minutos a ${measuredTI.measurement_height_m || 80} m. TI = σ₁₀min / V̄₁₀min. La serie MCP horaria permanece separada y la clase IEC no se asigna automáticamente.`
      : `${measuredTI.reason || 'No se encontraron media y desviación estándar nativas.'} ${r.ti_warning || ''}`;
    _set('adv-badge-alpha',  `α = ${sh?.alpha?.toFixed(3) || '—'}`);
    _set('adv-badge-z0',     `z₀ = ${sh?.z0 ?? '—'} m`);
    _set('adv-badge-method', rp?.method || 'Gumbel');
    if (r.sectors?.dominant_sectors) {
      const bd = document.getElementById('wind-badge-dom');
      if (bd) bd.textContent = `Dom: ${r.sectors.dominant_sectors.join(', ')}`;
    }

    renderTIChart(ti, hasMeasuredTI);
    renderHistoricalTurbulenceChart(historicalTI);
    renderTurbulenceTraceability(measuredTI, historicalTI, hourlyVar);
    renderShearChart(sh);
    renderHubKPIs(sh?.hub);
    populateSectorTable(r.sectors);
    renderReturnChart(rp);
    populateReturnTable(rp);

}

function renderSavedHybrid(r) {
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

}

function renderSavedEnergy(r, simCfg) {
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

    renderPowerCurveChart(r.turbine, r.timeseries, r.n_turbines);
    renderMonthlyEnergyChart(r.monthly);
    renderPowerTimeseries(r.timeseries);
    renderSensitivityChart(r.sensitivity || []);
    populateTurbineFicha(r.turbine, r.summary, simCfg);

}

function renderSavedQC(r) {
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

}

function renderSavedPark(r, layerKey, turbineKey) {
  const statusEl = document.getElementById('parque-status');
    // Actualizar mapa
    parqueRenderKrigingLayer(layerKey);
    parqueRenderPolygon();
    parquePlotTurbinesOnMap(r.layout_latlon, r.simulation?.turbines || null, r);

    // KPIs
    const sim = r.simulation;
    statusEl.textContent = sim
      ? `${r.n_placed} turbinas · ${r.installed_mw} MW · ${sim.hours?.toLocaleString()||'—'} h`
      : `${r.n_placed} turbinas colocadas (sin serie temporal)`;

    // Layout config badge (tresbolillo / alineado / spacings / direction)
    const lcfg = r.layout_config;
    const lBadge = document.getElementById('parque-layout-badge');
    if (lBadge && lcfg) {
      const typeLabel = ({tresbolillo: '⬡ Tresbolillo', staggered: '⬡ Tresbolillo',
        alineado: '▦ Alineado', aligned: '▦ Alineado',
        topsis_improved: '★ TOPSIS mejorado'})[lcfg.layout_type] || '⚙ GSP';
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
      // Preserve the same direction used when the wake cones were drawn.
      parqueSyncWakeDirection(r);

      document.getElementById('parque-kpis').style.display = '';
      document.getElementById('parque-kpi-aep').textContent = parqueFmt(Number(sim.aep_mwh) / 1000, 3, '0.000');
      document.getElementById('parque-kpi-wl').textContent  = parqueFmt(sim.wake_loss_pct, 2, '0.00');
      document.getElementById('parque-kpi-cf').textContent  = parqueFmt(sim.cf_pct, 2, '0.00');
      document.getElementById('parque-kpi-mw').textContent  = parqueFmt(sim.installed_mw ?? r.installed_mw, 3, '0.000');
      document.getElementById('parque-kpi-flh').textContent = parqueFmt(sim.flh, 0, '0');
      document.getElementById('parque-kpi-dir').textContent = parqueFmt(parqueModelWindDirection(r), 1) + '°';
      parqueRenderAEPChart(sim.turbines, r.layout_latlon);
      parqueRenderTable(r.layout_latlon, r.wind_at_turbines, sim.turbines);
    } else {
      document.getElementById('parque-kpis').style.display = 'none';
      parqueRenderTable(r.layout_latlon, r.wind_at_turbines, null);
    }
}

// Only render stored outputs here. Never invoke run/optimize/pipeline handlers.
async function renderSavedProjectResults() {
  const failures = [];
  const show = async (label, fn) => {
    try { await fn(); } catch (error) { failures.push(label); console.error(label, error); }
  };
  for (const [category, result] of Object.entries(WP.marketResults || {})) {
    if (MARKET_CATS.includes(category)) await show('Mercado', () => _renderMarketResult(category, result));
  }
  if (WP.farmLayout) await show('Layout', () => WP.restoreFarmLayout?.(WP.farmLayout));
  if (WP.farmResult) await show('Simulación del parque', () => renderSavedFarm(WP.farmResult));
  if (WP.turbineComparison) await show('Comparación de turbinas', () => renderSavedTurbineComparison(WP.turbineComparison));
  if (WP.qcResult) await show('Calidad', () => renderSavedQC(WP.qcResult));
  if (WP.windStats) {
    await show('Resumen', () => renderSavedOverview(WP.windStats));
    await show('Viento', () => renderSavedWind(WP.windStats));
  }
  if (WP.advancedResult) await show('Recurso avanzado', () => renderSavedAdvanced(WP.advancedResult));
  if (WP.energyResult?.summary) await show('Energía', () => renderSavedEnergy(WP.energyResult, WP.energySimulation || {}));
  if (WP.hybridResult) await show('Híbrido', () => renderSavedHybrid(WP.hybridResult));
  if (WP.mcpResult) await show('MCP', () => renderMCPResult(WP.mcpResult));
  if (KRIG.calibration) await show('Calibración', () => krig3RenderCalibration(KRIG.calibration));
  if (KRIG.result) await show('Kriging', () => {
    document.querySelectorAll('.krig3-tab-phys').forEach(el => { el.style.display = KRIG.result.physical ? '' : 'none'; });
    document.querySelectorAll('.krig3-tab-mcp').forEach(el => { el.style.display = KRIG.result.calibration_id && KRIG.result[el.dataset.layer] ? '' : 'none'; });
    krig3SetLayer(KRIG.layerKey || 'wind');
    krig3PopulateCurveSelect();
    krig3RenderCurve();
    krig3ZoomToPrincipal();
    document.getElementById('krig3-status').textContent = `${KRIG.result.n_sites || KRIG.sites.length} sitios · resultado guardado`;
  });
  if (MAPA.result) await show('Mapa', () => mapaRenderVariable(MAPA.var || 'speed'));
  if (PARQUE.optResult) await show('Parque', () => {
    parqueInitMap();
    renderSavedPark(PARQUE.optResult, PARQUE.layerKey || 'wind', PARQUE.optResult.turbine?.name || '');
  });
  if (PARQUE.wakeProfile) await show('Estelas', () => parqueRenderWakeChart(PARQUE.optResult?.D_m, PARQUE.wakeProfile));
  if (WP.technoScenarios?.integrated?.result) await show('Economía', renderTechnoScenarioResult);
  if (_riskData) await show('Riesgo', () => renderRiskSection(_riskData));
  updateResumenHeader();
  updateResumenVerdict();
  if (failures.length) toast(`Resultados guardados; no se pudieron mostrar: ${failures.join(', ')}`, 'warning');
}

function renderSavedFarm(r) {
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

}

function renderSavedTurbineComparison(r) {
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

}

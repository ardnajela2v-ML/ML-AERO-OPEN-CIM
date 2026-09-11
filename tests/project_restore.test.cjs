const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/aero.js'), 'utf8');
function context() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { id, value: '', style: {}, textContent: '', innerHTML: '',
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      querySelector() { return element(id + '-child'); }, querySelectorAll() { return []; } });
    return elements.get(id);
  };
  const sandbox = { console, setTimeout() {}, requestAnimationFrame() {},
    document: { addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; }, getElementById: element },
    window: { addEventListener() {} },
    Chart: { defaults: { font: {}, plugins: { legend: { labels: {} }, tooltip: {} } }, register() {} },
    fetch() { throw new Error('Restoration must not run network computations'); } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { sandbox, element, run: code => vm.runInContext(code, sandbox) };
}
test('snapshot includes all completed module data, no charts or runtime objects', () => {
  const { run } = context();
  run(`WP.advancedResult = {turbulence: [1,2]}; WP.hybridResult = {annual: [3]};
    WP.mcpResult = {run_id:'saved'}; WP.farmResult = {aep_mwh:123};
    PARQUE.wakeProfile = {deficit_pct:[12]}; MAPA.result = {speed:[5]};
    _riskData = {base:{npv_usd:123}}; KRIG.selected = new Set(['station']);`);
  const state = JSON.parse(run('JSON.stringify(collectProjectClientState())'));
  assert.equal(state.snapshot_version, 2);
  assert.deepEqual(state.wp.advancedResult.turbulence, [1, 2]);
  assert.equal(state.wp.mcpResult.run_id, 'saved');
  assert.equal(state.wp.farmResult.aep_mwh, 123);
  assert.deepEqual(state.parque.wakeProfile.deficit_pct, [12]);
  assert.deepEqual(state.krig.selected, ['station']);
  assert.equal(state.risk.base.npv_usd, 123);
  assert.equal(state.wp.charts, undefined);
  assert.equal(state.wp.sessionId, undefined);
});
test('saved outputs are rendered without rerunning any module', async () => {
  const { run, sandbox } = context();
  sandbox.seen = [];
  const names = ['renderSavedOverview','renderSavedWind','renderSavedAdvanced','renderSavedEnergy',
    'renderSavedHybrid','renderSavedQC','renderMCPResult','krig3RenderCalibration','krig3RenderLayer',
    'krig3RenderCurve','krig3PopulateCurveSelect','krig3ZoomToPrincipal','mapaRenderVariable','parqueInitMap','renderSavedPark','parqueRenderWakeChart',
    'renderTechnoScenarioResult','renderRiskSection','updateResumenHeader','updateResumenVerdict'];
  names.forEach(name => run(`${name} = (...args) => seen.push('${name}');`));
  run(`WP.windStats={}; WP.advancedResult={}; WP.energyResult={summary:{}}; WP.hybridResult={};
    WP.qcResult={}; WP.mcpResult={}; KRIG.calibration={}; KRIG.result={}; MAPA.result={};
    PARQUE.optResult={}; PARQUE.wakeProfile={}; WP.technoScenarios.integrated.result={}; _riskData={};`);
  await run('renderSavedProjectResults()');
  names.forEach(name => assert.ok(sandbox.seen.includes(name), name));
});
test('park rendering restores numerical KPIs from the snapshot', () => {
  const { run, element } = context();
  for (const name of ['parqueRenderKrigingLayer','parqueRenderPolygon','parquePlotTurbinesOnMap','parqueRenderAEPChart','parqueRenderTable']) {
    run(`${name} = () => {};`);
  }
  run(`renderSavedPark({n_placed:2, installed_mw:4, area_km2:1, D_m:80, min_sep_m:400,
    n_candidates:12, layout_latlon:[[16,-95],[16.1,-95]], turbine:{name:'Saved turbine'},
    simulation:{aep_mwh:12000, wake_loss_pct:5, cf_pct:34.25, flh:3000, installed_mw:4, turbines:[]}}, 'wind', '');`);
  assert.equal(element('parque-kpi-aep').textContent, '12.000');
  assert.equal(element('parque-kpi-cf').textContent, '34.25');
  assert.equal(element('parque-info').textContent, 'Saved turbine · ⌀80m');
});

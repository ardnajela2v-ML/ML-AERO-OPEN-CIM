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
    fetch() { throw new Error('Unexpected independent energy request'); } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { sandbox, element, run: code => vm.runInContext(code, sandbox) };
}
test('recommendation uses selected TOPSIS park energy divided by count without new forecast', async () => {
  const {run, element} = context();
  element('parque-turbine').value = 'winner.csv';
  run(`MCDA.lastRanking = {ranking:[{turbina:'winner.csv', n_aerogeneradores:4,
    layout_latlon:[[0,0]], aep_p50_mwh:20000, wake_loss_pct:3}]};`);
  await run('parqueUpdateRecommendation()');
  assert.equal(element('parque-rec-n').textContent, 4);
  assert.equal(element('parque-rec-aep').textContent, '5000.0');
  assert.match(element('parque-energy-basis').textContent, /20000.0 MWh\/año/);
  assert.match(element('parque-energy-basis').textContent, /570.8 kW/);
});
test('applying a family updates map, energy and active comparison together', () => {
  const {run, sandbox, element} = context();
  sandbox.plotted = null;
  element('parque-turbine').value = 'winner.csv';
  run(`parqueRenderKrigingLayer = parqueRenderPolygon = parqueRenderAEPChart = () => {};
    parquePlotTurbinesOnMap = points => plotted = points;
    PARQUE.optResult = {turbine_key:'winner.csv', D_m:90, area_km2:1, min_sep_m:270,
      comparison:{max_energy_id:'layout-1', min_wake_id:'layout-1', max_wake_loss_pct:5,
      same_count:true, errors:[], winners:[{id:'layout-1', label:'Tresbolillo', feasible:true,
      n_placed:2, layout_latlon:[[1,2],[3,4]], layout_xy_m:[[0,0],[500,0]], aep_p50_mwh:12000,
      aep_per_turbine_mwh:6000, wake_loss_pct:2, aep_gain_mwh:500, layout_config:{},
      simulation:{aep_mwh:12000, installed_mw:4, wake_loss_pct:2, cf_pct:34.2, flh:3000,
      dominant_dir_deg:270, mean_power_per_turbine_kw:684.9, turbines:[]}}]}};`);
  run("parqueApplyConfiguration('layout-1')");
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.plotted)), [[1,2],[3,4]]);
  assert.equal(element('parque-kpi-aep').textContent, '12.000');
  assert.equal(element('parque-rec-aep').textContent, '6000.0');
  assert.match(element('parque-config-comparison').innerHTML, /Aplicada/);
  assert.equal(run('PARQUE.result.aep_mwh'), 12000);
});
test('comparison dispatches selected ranked model rather than an unrelated wind session', async () => {
  const {run, element} = context();
  element('parque-turbine').value = 'selected.csv';
  run(`MCDA.lastRanking={ranking:[{turbina:'first.csv', n_aerogeneradores:3, layout_latlon:[]},
    {turbina:'selected.csv', n_aerogeneradores:2, layout_latlon:[]}]};
    parqueOptimizeTopsis = async row => { window.dispatched = row.turbina; };`);
  await run('parqueOptimizeManual()');
  assert.equal(run('window.dispatched'), 'selected.csv');
});

test('saved layouts from the old search require regeneration before ranking', () => {
  const {run} = context();
  run("MCDA.scope='area'; MCDA.lastSpatial={analysis_kind:'farm_layout_candidates_v1', alternatives:[]};");
  assert.equal(run('mcdaNeedsLayoutRegeneration()'), true);
  run("MCDA.lastSpatial.layout_search_revision='shared_geometry_v1';");
  assert.equal(run('mcdaNeedsLayoutRegeneration()'), false);
});

test('map synchronization uses energetic NNW, preserves north zero and ignores grid rotation', () => {
  const {run, element} = context();
  element('parque-wake-dir').value = '270';
  run(`PARQUE.optResult={layout_config:{orientation_deg:90, dominant_dir_deg:337.5},
    simulation:{dominant_energy_dir_deg:337.5, dominant_dir_deg:340}};
    parqueSyncWakeDirection();`);
  assert.equal(element('parque-wake-dir').value, 337.5);
  run('PARQUE.optResult.simulation.dominant_energy_dir_deg = 0; parqueSyncWakeDirection();');
  assert.equal(element('parque-wake-dir').value, 0);
  run('PARQUE.optResult = {}; parqueSyncWakeDirection();');
  assert.equal(element('parque-wake-dir').value, '');
});

test('NNW cones point SSE and north zero points south, not east', () => {
  const {run, element, sandbox} = context();
  sandbox.polygons = [];
  sandbox.L = {layerGroup: () => ({addTo() {}}), polygon: points => {
    sandbox.polygons.push(points); return {addTo() {}};
  }};
  run(`parqueInitMap = () => ({removeLayer(){}});
    PARQUE.optResult={layout_latlon:[[16,-95]], D_m:90, simulation:{dominant_energy_dir_deg:337.5}};
    PARQUE.turbineMarkers=[];`);
  element('parque-wake-dir').value = '337.5';
  run('parqueRenderWakeCones()');
  let far = sandbox.polygons[0].slice(2);
  assert.ok((far[0][0] + far[1][0]) / 2 < 16, 'downwind south');
  assert.ok((far[0][1] + far[1][1]) / 2 > -95, 'downwind east');
  assert.match(element('parque-wake-direction-note').textContent, /337.5° → estela hacia 157.5°/);
  sandbox.polygons.length=0;
  element('parque-wake-dir').value='0';
  run('parqueRenderWakeCones()');
  far = sandbox.polygons[0].slice(2);
  assert.ok((far[0][0]+far[1][0])/2 < 16);
  assert.equal((far[0][1]+far[1][1])/2, -95);
  assert.match(element('parque-wake-direction-note').textContent, /Vista manual/);
});

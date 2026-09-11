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
  await run('parqueOptimize()');
  assert.equal(run('window.dispatched'), 'selected.csv');
});

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

test('main park action generates candidates even when a ranking exists', async () => {
  const {run} = context();
  run(`MCDA.lastRanking={ranking:[{turbina:'old.csv'}]};
    mcdaRunAnalysis=async()=>{window.generated=true};
    parqueOptimizeManual=async()=>{throw Error('legacy path')};`);
  await run('parqueOptimize()');
  assert.equal(run('window.generated'),true);
});
test('TOPSIS cannot fall back to isolated turbine ranking before layout generation', async () => {
  const {run}=context();
  run(`toast=()=>{}; MCDA.scope='area'; MCDA.lastAnalysis=[{turbines:[]}];
    api=async()=>{throw Error('should not call API')};`);
  await run('mcdaRankNow()');
  assert.equal(run('MCDA.lastRanking'),null);
});
test('ranking only sends evaluated alternatives and never current geometry controls', async () => {
  const {run}=context();
  run(`MCDA.lastSpatial={analysis_kind:'farm_layout_candidates_v1',alternatives:[{turbina:'a.csv',layout_xy_m:[[1,2]]}]};
    mcdaActiveAhp=()=>null; mcdaGetWeights=()=>({van_usd:1});
    mcdaRenderRanking=()=>{};mcdaRenderSpatialMarkers=()=>{};mcdaRenderMiniMap=()=>{};
    api=async(path,method,body)=>{window.request={path,body};return {ranking:[]}};`);
  await run('mcdaRankNow()');
  assert.equal(run('window.request.path'),'/api/multicriteria/rank');
  assert.equal(run('window.request.body.alternatives[0].layout_xy_m[0][0]'),1);
  assert.equal(run('window.request.body.polygon'),undefined);
});
test('changing inputs discards a pending ranking response', async () => {
  const {run,sandbox}=context();
  sandbox.pending=new Promise(resolve=>sandbox.complete=resolve);
  run(`MCDA.lastSpatial={analysis_kind:'farm_layout_candidates_v1',alternatives:[]};
    mcdaActiveAhp=()=>null;mcdaGetWeights=()=>({});mcdaRenderRanking=()=>{};
    api=()=>pending;`);
  const ranking=run('mcdaRankNow()');
  run('mcdaInvalidateLayouts()');
  sandbox.complete({ranking:[{turbina:'stale.csv'}]});
  await ranking;
  assert.equal(run('MCDA.lastRanking'),null);
});
test('opening park does not request an independent demand heuristic', async () => {
  const {run,element}=context();
  element('parque-turbine').value='some.csv';
  run(`api=async()=>{throw Error('independent request')};`);
  await run('parqueUpdateRecommendation()');
  assert.equal(element('parque-rec-n').textContent,'—');
});
test('generation completion leaves ranking empty and presents candidates', async () => {
  const {run}=context();
  run(`MCDA.taskId='generation';MCDA.scope='area';toast=()=>{};
    mcdaBuildWeightSliders=()=>{};mcdaRenderRanking=()=>{};
    mcdaRenderSpatialMarkers=()=>{};mcdaRenderMiniMap=()=>{};
    api=async()=>({status:'done',result:{analysis_kind:'farm_layout_candidates_v1',alternatives:[],sites:[],ranking:null}});`);
  await run('mcdaPollStatus()');
  assert.equal(run('MCDA.lastRanking'),null);
  assert.equal(run('MCDA.taskId'),null);
  assert.equal(run('mcdaHasLayoutCandidates()'),true);
});
test('financial evaluation inherits the ranked layout without manual optimization', () => {
  const {run}=context();
  run(`MCDA.lastSpatial={analysis_kind:'farm_layout_candidates_v1'};
    MCDA.lastRanking={ranking:[{turbina:'winner.csv',n_aerogeneradores:3,aep_p50_mwh:15000,wake_loss_pct:2}]};
    PARQUE.optResult={turbine_key:'old.csv',n_placed:99};PARQUE.result={aep_mwh:999};`);
  assert.equal(run('technoParkLayout().turbine_key'),'winner.csv');
  assert.equal(run('technoParkLayout().aep_mwh'),15000);
  run('MCDA.lastRanking=null');
  assert.equal(run('technoParkLayout()'),null);
});
test('generation request uses the dedicated route and configured constraints, not AHP', async () => {
  const {run,element}=context();
  element('parque-max-slope').value='12';element('parque-kwake').value='0.06';
  element('parque-grid-limit').value='10';element('parque-avail').value='97';element('parque-elec').value='0';
  run(`krig3InitMap=()=>{};KRIG.sites=[{}];KRIG.selected=new Set(['a','b','c']);
    mcdaUpdateScopeUi=()=>{};mcdaCurrentAreaBounds=()=>null;
    mcdaParkPolygon=()=>[{lat:1,lon:1},{lat:1,lon:2},{lat:2,lon:1}];
    mcdaSelectedTurbines=()=>['a.csv'];
    parkEquivalentDesignInputs=()=>({price_usd_kwh:.08,tasa:.08,price_mxn_per_usd:20});
    WP.parkConfiguration={project_horizon_years:20};
    mcdaGetWeights=()=>({van_usd:1});mcdaExclusionPolygons=()=>[];mcdaReceptorPoints=()=>[];
    mcdaRememberLayoutWeights=()=>{};mcdaRenderRanking=()=>{};mcdaClearSpatialMarkers=()=>{};
    projectHasSpatialSource=()=>false;mcdaPollStatus=()=>{};wsH=()=>100;
    api=async(path,method,body)=>{window.request={path,body};return {task_id:'x'}};`);
  await run('mcdaRunAnalysis()');
  assert.equal(run('window.request.path'),'/api/farm/layouts/generate');
  assert.equal(run('window.request.body.weights'),undefined);
  assert.equal(run('window.request.body.ahp_assessment'),undefined);
  assert.equal(run('window.request.body.max_slope_deg'),12);
  assert.equal(run('window.request.body.k_wake'),.06);
  assert.equal(run('window.request.body.grid_limit_mw'),10);
  assert.equal(run('window.request.body.electrical_losses'),0);
});

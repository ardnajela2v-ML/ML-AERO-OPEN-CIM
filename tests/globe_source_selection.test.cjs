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
      setAttribute() {}, getAttribute() { return null; },
      appendChild() {}, remove() {},
      querySelector() { return element(id + '-child'); }, querySelectorAll() { return []; } });
    return elements.get(id);
  };
  const sandbox = { console, setTimeout() {}, requestAnimationFrame() {},
    document: { addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
      getElementById: element, createElement: () => element(`__created_${elements.size}`) },
    window: { addEventListener() {} },
    Chart: { defaults: { font: {}, plugins: { legend: { labels: {} }, tooltip: {} } }, register() {} },
    fetch() { throw new Error('Restoration must not run network computations'); } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { sandbox, element, run: code => vm.runInContext(code, sandbox) };
}

function area(ctx, realCount, virtualCount) {
  ctx.run(`GLOBE.catalogStatus='ready'; GLOBE.poi={lat:16,lon:-95};
    GLOBE.visibleSites = [...Array.from({length:${realCount}}, (_,i)=>({station_id:'o'+i,data_kind:'observed',lat:16,lon:-95,display_name:'Real '+i})),
    ...Array.from({length:${virtualCount}}, (_,i)=>({station_id:'v'+i,data_kind:'reanalysis',lat:16,lon:-95,display_name:'Virtual '+i}))];
    GLOBE.sites=GLOBE.visibleSites;`);
}
test('area decisions depend on available stations, not current selection', () => {
  for (const [real, virtual, kind] of [[0,0,'empty'],[1,0,'observed'],[2,0,'observed'],[0,1,'virtual'],[0,2,'virtual'],[0,3,'virtual'],[1,3,'mixed']]) {
    const ctx=context(); area(ctx,real,virtual);
    assert.equal(ctx.run('globeAreaOptions(GLOBE.visibleSites).kind'),kind);
    ctx.run('globeUpdateContinueState()');
    assert.equal(ctx.run('GLOBE.sourceChoice'),kind==='observed'?'observed_only':'');
    if (kind==='virtual' && virtual<3) assert.match(ctx.element('globe-source-choice').innerHTML,/value="reanalysis_only" disabled/);
  }
});
test('single real is automatic, multiple reals require selection, radius removes stale picks', async () => {
  const ctx=context(); area(ctx,1,0);ctx.element('globe-radius').value='200';
  ctx.run('globeRenderStationList=()=>globeUpdateContinueState(); globeUpdateStatus=()=>{};');
  await ctx.run('globeFilterByRadius()');
  assert.equal(ctx.run('GLOBE.master'),'o0');
  assert.equal(ctx.element('btn-globe-continue-single').disabled,false);
  ctx.run(`GLOBE.poi={lat:0,lon:0};GLOBE.mcpReferences.add('outside');GLOBE.singleReference='outside';`);
  await ctx.run('globeFilterByRadius()');
  assert.equal(ctx.run('GLOBE.master'),null);
  assert.equal(ctx.run('GLOBE.mcpReferences.size'),0);
  assert.equal(ctx.run('GLOBE.singleReference'),null);
  const multiple=context(); area(multiple,2,0);multiple.run('globeUpdateContinueState()');
  assert.equal(multiple.element('btn-globe-continue-single').disabled,true);
});
test('catalog errors and loading never offer an empty-area continuation', () => {
  const ctx=context();area(ctx,0,0);
  for (const status of ['loading','error']) {
    ctx.run(`GLOBE.catalogStatus='${status}';globeUpdateContinueState()`);
    assert.equal(ctx.element('btn-globe-continue-single').style.display,'none');
    assert.doesNotMatch(ctx.element('globe-panel-sub').textContent,/Agrega una estación/);
  }
  assert.equal(ctx.element('btn-globe-retry-catalog').style.display,'');
});
test('single virtual can be selected and source choice survives snapshot', () => {
  const ctx=context();area(ctx,0,2);
  ctx.run(`GLOBE.sourceChoice='single_reference';GLOBE.singleReference='v1';
    WP.studyMode='single_reference';WP.singleStationStudy={station_id:'v1'};globeUpdateContinueState();`);
  assert.equal(ctx.element('btn-globe-continue-single').disabled,false);
  const snapshot=JSON.parse(ctx.run('JSON.stringify(collectProjectClientState())'));
  assert.equal(snapshot.globe.sourceChoice,'single_reference');
  assert.equal(snapshot.wp.singleStationStudy.station_id,'v1');
});
test('spatial invalidation keeps temporal source and multiple available stations', () => {
  const ctx=context();
  ctx.run(`WP.studyMode='observed_only';WP.activeDataSource='observed';
    KRIG.sites=[{filename:'real'},{filename:'era5'},{filename:'merra'}];KRIG.selected=new Set(['real','era5','merra']);
    KRIG.result={old:true};PARQUE.result={old:true};KRIG.task_id='old';
    krig3InvalidateCalibration('changed nodes');`);
  assert.equal(ctx.run('WP.activeDataSource'),'observed');
  assert.equal(ctx.run('KRIG.sites.length'),3);
  assert.equal(ctx.run('KRIG.selected.size'),3);
  assert.equal(ctx.run('KRIG.result'),null);
  assert.equal(ctx.run('PARQUE.result'),null);
  assert.equal(ctx.run('KRIG.task_id'),null);
  assert.equal(ctx.run('krig3AdvancedMcpRequested()'),false);
});
test('an old spatial response is discarded after changing stations', async () => {
  const ctx=context();
  let resolve; ctx.sandbox.pending = new Promise(r=>resolve=r);
  ctx.run(`api=()=>pending;KRIG.task_id='old';KRIG.spatialEpoch=1;`);
  const polling=ctx.run('krig3Poll()');
  ctx.run('krig3InvalidateSpatialResults()');
  resolve({status:'done',result:{old:true}});await polling;
  assert.equal(ctx.run('KRIG.result'),null);
});
test('source switch discards pending API response', async () => {
  const ctx=context();let resolve;
  ctx.sandbox.fetch=()=>new Promise(r=>resolve=r);
  const pending=ctx.run(`api('/api/wind/stats','POST',{})`);
  ctx.run('WP.windSourceEpoch=1');
  resolve({ok:true,json:async()=>({old:true})});
  await assert.rejects(pending,/La fuente cambió/);
});

test('Globe activates a single source without MCP in both 3D and 2D', async () => {
  for (const mode of ['globe','leaflet']) {
    const ctx=context();area(ctx,1,2);ctx.element('globe-radius').value='200';
    ctx.sandbox.requests=[];
    ctx.run(`GLOBE.mode='${mode}';GLOBE.sourceChoice='observed_only';GLOBE.master='o0';
      applyReferenceHeightToUI=()=>{};collapseGlobeStage=()=>{};toast=()=>{};
      api=async (url,method,body)=>{requests.push({url,method,body});return {
        source_id:'observed',study_mode:'observed_only',target_height_m:80,
        poi:{lat:16,lon:-95},published_source:{label:'Real 0',rows:72}}};`);
    await ctx.run('continueGlobeToSingleStation()');
    assert.equal(ctx.run('WP.activeDataSource'),'observed');
    assert.equal(ctx.run('WP.studyMode'),'observed_only');
    assert.equal(ctx.run('WP.mcpRunId'),null);
    assert.equal(ctx.run('GLOBE.preparing'),false);
    assert.equal(ctx.sandbox.requests.length,1);
    assert.match(ctx.sandbox.requests[0].url,/single-station-study$/);
    assert.equal(ctx.sandbox.requests[0].body.station_id,'o0');
  }
});

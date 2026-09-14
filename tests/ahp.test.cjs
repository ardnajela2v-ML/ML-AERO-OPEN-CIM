const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/aero.js'), 'utf8');
function setup() {
  const elements = new Map();
  const pairs = [];
  for(let i=0;i<8;i++) for(let j=i+1;j<8;j++) pairs.push({dataset:{pair:`${i}-${j}`},value:''});
  const sliders = Array.from({length:8},(_,i)=>({dataset:{crit:`c${i}`},value:'0.125',nextElementSibling:{textContent:''}}));
  const el = id => {
    if(!elements.has(id)) elements.set(id,{value:'',innerHTML:'',textContent:'',style:{},hidden:false,disabled:false,
      classList:{add(){},remove(){},toggle(){}},querySelector(){return null;}});
    return elements.get(id);
  };
  const sandbox = {console,setTimeout(){},requestAnimationFrame(){},
    document:{addEventListener(){},getElementById:el,querySelector(){return null;},
      querySelectorAll(q){return q==='.mcda-ahp-pair'?pairs:q==='.mcda-w'?sliders:[];}},
    window:{addEventListener(){}},Chart:{defaults:{font:{},plugins:{legend:{labels:{}},tooltip:{}}},register(){}}};
  vm.createContext(sandbox);vm.runInContext(source,sandbox);
  const run = code => vm.runInContext(code,sandbox);
  run(`toast = () => {}; MCDA.defaults = {layout_weights:Object.fromEntries(Array.from({length:8},(_,i)=>['c'+i,.125])),
    layout_labels:Object.fromEntries(Array.from({length:8},(_,i)=>['c'+i,'Criterion '+i])),layout_directions:{}};
    MCDA.layoutWeights={...MCDA.defaults.layout_weights}; MCDA.weightSchema='effective-area-v1';`);
  return {el,pairs,sliders,sandbox,run};
}
function result() {
  return {schema:'layout-ahp-v1',consistent:true,criteria:Array.from({length:8},(_,i)=>`c${i}`),
    weights:Object.fromEntries(Array.from({length:8},(_,i)=>[`c${i}`,i===0?9/16:1/16])),
    matrix:Array.from({length:8},(_,i)=>Array.from({length:8},(_,j)=>(i===0?9:1)/(j===0?9:1))),
    cr:0,ci:0,ri:1.41,lambda_max:8,cr_threshold:.1,author:'Synthetic test only',objective:'Test',rationale:'Test'};
}
test('all 28 comparisons start unanswered; draft edits cannot apply weights',()=>{
  const {run,el,pairs}=setup();run('mcdaRenderAhp()');
  assert.equal((el('mcda-ahp-pairs').innerHTML.match(/class="form-select mcda-ahp-pair"/g)||[]).length,28);
  assert.ok(pairs.every(p=>p.value===''));
  assert.equal(el('btn-mcda-ahp-apply').disabled,true);
  pairs[0].value='3';run('mcdaAhpEdited()');
  assert.equal(run('MCDA.ahpDraft.pairs["0-1"]'),'3');
  assert.equal(run('MCDA.layoutWeights.c0'),.125);
});
test('missing judgments never become equality or trigger API',async()=>{
  const {run,sandbox}=setup();run('mcdaRenderAhp()');
  sandbox.calls=0;run('api=async()=>{calls++;};');
  await run('mcdaCalculateAhp()');assert.equal(sandbox.calls,0);
});
test('AHP weights above .5 preserve precision; manual edit changes provenance',async()=>{
  const {run,sandbox,el,sliders}=setup();sandbox.assessment=result();
  run('MCDA.ahpResult=assessment; MCDA.ahpDraft={criteria:assessment.criteria,pairs:{}};');
  await run('mcdaApplyAhp()');
  assert.equal(run('mcdaGetWeights().c0'),9/16);
  assert.equal(run('mcdaActiveAhp().author'),'Synthetic test only');
  assert.match(el('mcda-weights').innerHTML,/max="1" step="any"/);
  const state=JSON.parse(run('JSON.stringify(collectProjectClientState().mcda)'));
  assert.equal(state.weightSource,'ahp');assert.equal(state.ahpApplied.weights.c0,9/16);
  sliders[0].value='0.55';run('mcdaRememberLayoutWeights(true)');
  assert.equal(run('mcdaActiveAhp()'),null);assert.equal(run('mcdaGetWeights().c0'),.55);
  assert.equal(run('MCDA.ahpApplied.weights.c0'),9/16);
});
test('inconsistent result cannot apply even if action called directly',async()=>{
  const {run,sandbox}=setup();sandbox.assessment={...result(),consistent:false};
  run('MCDA.ahpResult=assessment');await run('mcdaApplyAhp()');
  assert.equal(run('MCDA.weightSource'),'reference');
});
test('editing while calculation is pending discards stale result',async()=>{
  const {run,sandbox,pairs,el}=setup();run('mcdaRenderAhp()');pairs.forEach(p=>p.value='1');
  for(const field of ['author','objective','rationale'])el(`mcda-ahp-${field}`).value='Test';
  sandbox.pending=new Promise(resolve=>{sandbox.finish=resolve;});
  run('api=()=>pending');const task=run('mcdaCalculateAhp()');
  pairs[0].value='3';run('mcdaAhpEdited()');sandbox.finish(result());await task;
  assert.equal(run('MCDA.ahpResult'),null);
});

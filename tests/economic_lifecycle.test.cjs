const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/aero.js'), 'utf8');
function setup(loss, escalation, inflation) {
  const values = { 'te-price-escalation': escalation, 'te-inflation': inflation };
  const sandbox = { console, setTimeout() {}, requestAnimationFrame() {},
    document: { addEventListener() {}, querySelectorAll() { return []; },
      getElementById(id) { return { value: values[id] ?? '' }; } },
    window: { addEventListener() {} },
    Chart: { defaults: { font: {}, plugins: { legend: { labels: {} }, tooltip: {} } }, register() {} } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  vm.runInContext(`WP.technoFinalLoss = {enabled:true, pct:${loss}}`, sandbox);
  return sandbox;
}
function baseAnalysis() {
  const a = { project_years:25, capex:{total_capex_usd:1000000}, annual_opex_usd:30000,
    annual_energy_kwh:2000000, annual_energy_mwh:2000, annual_revenue_usd:200000,
    energy_price_usd_kwh:.1, annual_inflation_pct:0, discount_rate_pct:8,
    degradation_pct_per_year:.3, decommissioning_pct_capex:3, decommissioning_usd:30000,
    variable_params_used:false, cashflow_table:[] };
  for (let year=1; year<=25; year++) {
    const energy = 2000 * .997 ** (year-1);
    const terminal = year===25 ? 30000 : 0;
    a.cashflow_table.push({year, energy_mwh:energy, price_usd_kwh:.1,
      revenue:energy*100, opex:30000, decommissioning:terminal,
      cashflow:energy*100-30000-terminal});
  }
  return a;
}
for (const [loss, escalation, inflation] of [[10,0,0], [0,3,0], [0,0,2], [10,3,2]]) {
  test(`adjusted lifecycle retains discounted costs and energy (${loss}, ${escalation}, ${inflation})`, () => {
    const sandbox = setup(loss, escalation, inflation);
    const original = baseAnalysis();
    sandbox.input = original;
    const before = JSON.stringify(original);
    const result = vm.runInContext('technoAdjustedAnalysis(input)', sandbox);
    let costs=1000000, energyPv=0, revenuePv=0, npv=-1000000;
    for (let yr=1; yr<=25; yr++) {
      const energy=2000000 * .997**(yr-1) * (1-loss/100);
      const revenue=energy*.1*(1+escalation/100)**(yr-1);
      const opex=30000*(1+inflation/100)**(yr-1);
      const terminal=yr===25?30000:0;
      const df=1.08**yr;
      costs+=(opex+terminal)/df; energyPv+=energy/df; revenuePv+=revenue/df;
      npv+=(revenue-opex-terminal)/df;
      assert.ok(Math.abs(result.cashflow_table[yr-1].cashflow-(revenue-opex-terminal)) < 1e-6);
      assert.equal(result.cashflow_table[yr-1].decommissioning, terminal);
    }
    assert.equal(result.lcoe_usd_kwh, Math.round(costs/energyPv*10000)/10000);
    assert.equal(result.b_c_ratio, Math.round(revenuePv/costs*10000)/10000);
    assert.equal(result.npv_usd, Math.round(npv));
    assert.equal(JSON.stringify(original), before);
  });
}
test('unmodified scenario returns original analysis', () => {
  const sandbox=setup(0,0,0); sandbox.input=baseAnalysis();
  assert.equal(vm.runInContext('technoAdjustedAnalysis(input) === input',sandbox),true);
});

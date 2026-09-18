const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/aero.js'), 'utf8');

function context() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { id, value: '', checked: false, style: {}, textContent: '', innerHTML: '',
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      setAttribute() {}, getAttribute() { return null; }, disabled: false,
      querySelector() { return element(id + '-child'); }, querySelectorAll() { return []; } });
    return elements.get(id);
  };
  const sandbox = { console, setTimeout() {}, requestAnimationFrame() {},
    document: { addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
      getElementById: element, createElement: () => element(`__created_${elements.size}`) },
    window: { addEventListener() {} },
    Chart: { defaults: { font: {}, plugins: { legend: { labels: {} }, tooltip: {} } }, register() {} },
    fetch() { throw new Error('krig3SyncGenerationMode must not run network computations'); } };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { sandbox, element, run: code => vm.runInContext(code, sandbox) };
}

test('the "Continuidad ... -> Diseño" panel names the actual active source, not always "MCP"', () => {
  const { run, element } = context();
  element('krig3-generation-mode').value = 'mcp_advanced';

  run(`WP.activeDataSource = 'mcp:run-1'; WP.studyMode = 'mcp_observed'; krig3SyncGenerationMode();`);
  assert.equal(element('krig3-downscaling-title').textContent, 'Continuidad MCP → Diseño');
  assert.equal(element('krig3-raw-method-note').style.display, '');

  run(`WP.activeDataSource = 'reanalysis:node-1'; WP.studyMode = 'single_reference'; krig3SyncGenerationMode();`);
  assert.equal(element('krig3-downscaling-title').textContent, 'Continuidad de referencia virtual → Diseño');
  assert.doesNotMatch(element('krig3-method-note').textContent, /MCP publicado/);
  assert.equal(element('krig3-raw-method-note').style.display, 'none');

  run(`WP.activeDataSource = 'reanalysis-area:r1'; WP.studyMode = 'reanalysis_only'; krig3SyncGenerationMode();`);
  assert.equal(element('krig3-downscaling-title').textContent, 'Continuidad de reanálisis del área → Diseño');
  assert.equal(element('krig3-raw-method-note').style.display, 'none');

  run(`WP.activeDataSource = 'observed'; WP.studyMode = 'observed_only'; krig3SyncGenerationMode();`);
  assert.equal(element('krig3-downscaling-title').textContent, 'Continuidad de estación real → Diseño');
  assert.equal(element('krig3-raw-method-note').style.display, 'none');
});

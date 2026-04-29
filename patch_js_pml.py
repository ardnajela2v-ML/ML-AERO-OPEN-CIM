js_file = 'static/js/aero.js'
with open(js_file, 'r') as f:
    c = f.read()

# Fix populateForecastStudioSelect for PML
import re

pattern_pop = re.compile(r'(function populateForecastStudioSelect\(key\) \{[\s\S]*?if \(!cache \|\| !cache\.fitted \|\| Object\.keys\(cache\.fitted\)\.length === 0\) \{)')
match_pop = pattern_pop.search(c)
if match_pop:
    new_code = match_pop.group(1).replace(
        "if (!cache || !cache.fitted || Object.keys(cache.fitted).length === 0) {",
        """
  let targetCache = cache;
  if (key === 'pml' && cache && cache.sides) {
    // PML uses sides, we will just use the first available side (venta usually)
    const sideKey = (cache.sides && cache.sides.venta) ? 'venta' : Object.keys(cache.sides)[0];
    targetCache = cache.sides[sideKey];
  }

  if (!targetCache || !targetCache.fitted || Object.keys(targetCache.fitted).length === 0) {"""
    )
    c = c.replace(match_pop.group(1), new_code)
    
    # Also replace instances of `cache.fitted` with `targetCache.fitted` inside populateForecastStudioSelect
    pattern_pop2 = re.compile(r'for \(const modelName of Object\.keys\(cache\.fitted\)\) \{[\s\S]*?loadForecastFromCache\(key\);\n  \}')
    match_pop2 = pattern_pop2.search(c)
    if match_pop2:
        new_code2 = match_pop2.group(0).replace('cache.fitted', 'targetCache.fitted').replace('cache.best_model', 'targetCache.best_model')
        c = c.replace(match_pop2.group(0), new_code2)

# Fix loadForecastFromCache for PML
pattern_load = re.compile(r'(function loadForecastFromCache\(key\) \{[\s\S]*?const m = cache\.fitted\[modelName\];)')
match_load = pattern_load.search(c)
if match_load:
    new_code_load = match_load.group(1).replace(
        "const m = cache.fitted[modelName];",
        """
  let targetCache = cache;
  if (key === 'pml' && cache.sides) {
    const sideKey = (cache.sides && cache.sides.venta) ? 'venta' : Object.keys(cache.sides)[0];
    targetCache = cache.sides[sideKey];
  }
  const m = targetCache.fitted[modelName];"""
    )
    c = c.replace(match_load.group(1), new_code_load)

# Fix full_true, full_years for interest and FX
pattern_load2 = re.compile(r'(const full_true = cache\.history_annual;\n\s+const full_years = cache\.history_years;\n\s+const train_len = y_pred\.length;\n\s+y_true = full_true\.slice\(full_true\.length - train_len\);\n\s+labels = full_years\.slice\(full_years\.length - train_len\)\.map\(y => y\.toString\(\)\);\n\s+\} else \{)')
match_load2 = pattern_load2.search(c)
if match_load2:
    new_code_load2 = match_load2.group(1).replace(
        "const full_true = cache.history_annual;",
        "const full_true = targetCache.history_annual;"
    ).replace(
        "const full_years = cache.history_years;",
        "const full_years = targetCache.history_years;"
    )
    c = c.replace(match_load2.group(1), new_code_load2)

# Fix y_true, labels for demand and PML
pattern_load3 = re.compile(r'(y_true = cache\.history_annual;\n\s+labels = cache\.history_t \? cache\.history_t\.map\(t => \'t=\' \+ t\) : y_true\.map\(\(_, i\) => \'t=\' \+ i\);\n\s+\})')
match_load3 = pattern_load3.search(c)
if match_load3:
    new_code_load3 = match_load3.group(1).replace(
        "y_true = cache.history_annual;",
        "y_true = targetCache.history_annual;"
    ).replace(
        "labels = cache.history_t ? cache.history_t",
        "labels = targetCache.history_t ? targetCache.history_t"
    )
    c = c.replace(match_load3.group(1), new_code_load3)

with open(js_file, 'w') as f:
    f.write(c)

print("PML and targetCache patched.")

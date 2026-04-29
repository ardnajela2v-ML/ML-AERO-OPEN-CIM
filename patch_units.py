js_file = 'static/js/aero.js'
with open(js_file, 'r') as f:
    c = f.read()

# Replace hardcoded m/s with a dynamic unit inside renderForecastMainChart
c = c.replace(
    "function renderForecastMainChart(r, prefix='fs-wind') {",
    "function renderForecastMainChart(r, prefix='fs-wind') {"
)

# We need to find the specific lines and replace them.
# The tooltip label
c = c.replace(
    "label: (item) => `${item.dataset.label}: ${item.formattedValue} m/s`,",
    "label: (item) => `${item.dataset.label}: ${item.formattedValue} ${r.unit || 'm/s'}`,",
)

# The Y-axis title
c = c.replace(
    "y: { grid: { color: gridColor() }, title: { display: true, text: 'm/s' }, ticks:{font:{size:9}} }",
    "y: { grid: { color: gridColor() }, title: { display: true, text: r.unit || 'm/s' }, ticks:{font:{size:9}} }"
)

# Scatter plot X-axis
c = c.replace(
    "x: { grid: { color: gridColor() }, title: { display: true, text: 'Observado (m/s)' } },",
    "x: { grid: { color: gridColor() }, title: { display: true, text: `Observado (${r.unit || 'm/s'})` } },"
)

# Scatter plot Y-axis
c = c.replace(
    "y: { grid: { color: gridColor() }, title: { display: true, text: 'Predicho (m/s)' } }",
    "y: { grid: { color: gridColor() }, title: { display: true, text: `Predicho (${r.unit || 'm/s'})` } }"
)

# In loadForecastFromCache, add the unit mapping
# Find the end of `const r = {` block
import re
pattern = re.compile(r'(const r = \{[\s\S]*?stability: \[\]\n  \};)')
match = pattern.search(c)
if match:
    new_r = match.group(1).replace(
        "stability: []",
        "stability: [],\n    unit: key === 'demand' ? 'GWh' : key === 'pml' ? 'USD/MWh' : key === 'interest' ? '%' : key === 'fx' ? 'MXN/USD' : 'm/s'"
    )
    # Also, for demand we need to divide by 1000 since history_annual is in MWh
    y_true_replace = """
  if (key === 'demand') {
    y_true = y_true.map(v => v / 1000);
    y_pred = y_pred.map(v => v / 1000);
  }
  
  const r = {"""
    
    c = c.replace(match.group(1), new_r)
    c = c.replace("const r = {", y_true_replace)


with open(js_file, 'w') as f:
    f.write(c)

print("Units patched.")

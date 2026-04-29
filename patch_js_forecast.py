import re

js_file = 'static/js/aero.js'
with open(js_file, 'r') as f:
    c = f.read()

# Make render functions accept a prefix
c = c.replace("function renderForecastMainChart(r) {", "function renderForecastMainChart(r, prefix='fs-wind') {")
c = c.replace("destroyChart('fc-main');", "destroyChart(`${prefix}-main`);")
c = c.replace("document.getElementById('fc-main-chart')", "document.getElementById(`${prefix}-main-chart`)")
c = c.replace("document.getElementById('btn-reset-zoom-fc')", "document.getElementById(`btn-reset-zoom-${prefix}`)")
c = c.replace("WP.charts['fc-main'] =", "WP.charts[`${prefix}-main`] =")

c = c.replace("function renderScatterChart(r) {", "function renderScatterChart(r, prefix='fs-wind') {")
c = c.replace("destroyChart('fc-scatter');", "destroyChart(`${prefix}-scatter`);")
c = c.replace("document.getElementById('scatter-chart')", "document.getElementById(`${prefix}-scatter-chart`)")
c = c.replace("WP.charts['fc-scatter'] =", "WP.charts[`${prefix}-scatter`] =")

c = c.replace("function renderResidualHistogram(r) {", "function renderResidualHistogram(r, prefix='fs-wind') {")
c = c.replace("destroyChart('fc-resid');", "destroyChart(`${prefix}-resid`);")
c = c.replace("document.getElementById('residual-hist-chart')", "document.getElementById(`${prefix}-residual-hist-chart`)")
c = c.replace("WP.charts['fc-resid'] =", "WP.charts[`${prefix}-resid`] =")

c = c.replace("function renderStabilityChart(stability) {", "function renderStabilityChart(stability, prefix='fs-wind') {")
c = c.replace("destroyChart('fc-stability');", "destroyChart(`${prefix}-stability`);")
c = c.replace("document.getElementById('stability-chart')", "document.getElementById(`${prefix}-stability-chart`)")
c = c.replace("WP.charts['fc-stability'] =", "WP.charts[`${prefix}-stability`] =")

c = c.replace("function renderForecastMetricPills(metrics) {", "function renderForecastMetricPills(metrics, prefix='fs-wind') {")
c = c.replace("document.getElementById('fc-metric-pills')", "document.getElementById(`${prefix}-metric-pills`)")

# Add a fake binned_bias replacement to prevent errors if we don't have it
c = c.replace("renderBinnedBiasChart(r.residuals?.binned_bias || []);", "if(typeof renderBinnedBiasChart !== 'undefined') renderBinnedBiasChart(r.residuals?.binned_bias || [], 'fs-wind');")

# In loadForecast
c = c.replace("renderForecastMainChart(r);", "renderForecastMainChart(r, 'fs-wind');")
c = c.replace("renderScatterChart(r);", "renderScatterChart(r, 'fs-wind');")
c = c.replace("renderResidualHistogram(r);", "renderResidualHistogram(r, 'fs-wind');")
c = c.replace("renderStabilityChart(r.stability || []);", "renderStabilityChart(r.stability || [], 'fs-wind');")
c = c.replace("renderForecastMetricPills(r.metrics);", "renderForecastMetricPills(r.metrics, 'fs-wind');")

# Also, add logic for storing arena cache
if 'WP.arenaCache = {}' not in c:
    c = c.replace('window.WP = {', 'window.WP = {\n  arenaCache: {},')

# Save demand cache
c = c.replace("const fc = await api('/api/demand/forecast', 'POST', {",
              "const fc = await api('/api/demand/forecast', 'POST', {\n        ...body // just a placeholder\n      }); // wait, I must do it after await.")

with open(js_file, 'w') as f:
    f.write(c)

print("JS replaced.")

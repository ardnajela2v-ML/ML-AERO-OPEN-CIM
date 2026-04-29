import numpy as np
import time
from core.forecasting import fit_all_trend_models

t = np.arange(8760)
y = np.random.rand(8760) * 100

start = time.time()
res = fit_all_trend_models(t, y, models=['linear', 'random_forest', 'gradient_boost', 'mlp'])
print("Fast models took", time.time() - start)

try:
    start = time.time()
    res = fit_all_trend_models(t, y, models=['holt_winters'])
    print("Holt-Winters took", time.time() - start)
except Exception as e:
    print("HW failed", e)


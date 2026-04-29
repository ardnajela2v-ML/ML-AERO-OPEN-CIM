import re

with open('core/forecasting.py', 'r') as f:
    c = f.read()

# Create a fast hourly fit
new_func = """
def fit_all_hourly_models(t, y, models=None):
    \"\"\"Ajusta modelos de serie de tiempo horaria, optimizado para 8760 puntos.\"\"\"
    keys = ['linear', 'holt_winters', 'mean_reversion'] if models is None else models
    out = {}
    for k in keys:
        fitter = TREND_FITTERS.get(k)
        if fitter is None:
            continue
        try:
            res = fitter(t, y)
            yhat = res['fitted']
            out[k] = {
                'kind':    res['kind'],
                'params':  res['params'],
                'metrics': {
                    'r2':   _r2(y, yhat),
                    'rmse': _rmse(y, yhat),
                    'mae':  _mae(y, yhat),
                    'mape': _mape(y, yhat),
                },
                'fitted': yhat.tolist(),
            }
        except Exception:
            pass
    return out
"""
if "fit_all_hourly_models" not in c:
    c += "\n" + new_func

with open('core/forecasting.py', 'w') as f:
    f.write(c)

print("forecasting.py patched.")

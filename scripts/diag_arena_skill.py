"""
Diagnóstico de Model Arena: mide R², Skill vs persistencia y Skill vs climatología
para los dos archivos del usuario en horizontes 1, 6, 24 h.

Uso:
    python scripts/diag_arena_skill.py
"""
import os
import sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.data import DataLoader, FeatureEngineer
from core.models import ModelArena, BaselineModels
from core.validate import compute_all_metrics

FILES = [
    ("A2018 MERRA2 (hourly)",
     os.path.join(ROOT, "DATOS DE METEREOLOGICOS DE parques",
                  "A2018Windnavigator_MERRA2_N16,550_W94,955.txt")),
    ("CERTE 2018 (10-min mast)",
     os.path.join(ROOT, "DATOS DE METEREOLOGICOS DE parques",
                  "CERTEnew2018Windnavigator_MERRA2_N16,555_W94,955.txt")),
]

HORIZONS = [1, 6, 24]
MODELS_TO_RUN = ['persistence', 'climatology', 'linear_regression',
                 'random_forest', 'ridge']


def rho1(y):
    """Autocorrelación de lag 1."""
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    if len(y) < 3:
        return float('nan')
    return float(np.corrcoef(y[:-1], y[1:])[0, 1])


def run_file(label, path):
    print(f"\n{'='*78}\n{label}\n  {path}\n{'='*78}")
    if not os.path.exists(path):
        print("  ❌ Archivo no encontrado")
        return

    loader = DataLoader()
    df, meta = loader.load_file(path, source_hint='auto')
    print(f"  Formato detectado : {meta.get('format')}")
    print(f"  Filas             : {len(df)}")
    print(f"  Columnas          : {list(df.columns)}")
    print(f"  Rango temporal    : {df.index.min()} → {df.index.max()}")
    print(f"  Resolución mediana: "
          f"{(df.index.to_series().diff().median().total_seconds()/60):.1f} min")

    if 'wind_speed' not in df.columns:
        print("  ❌ No hay columna 'wind_speed' después del loader")
        return

    ws = df['wind_speed'].dropna()
    print(f"  wind_speed        : n={len(ws)}  "
          f"media={ws.mean():.2f}  std={ws.std():.2f}  "
          f"ρ(1h)={rho1(ws):.3f}  ρ(24h)={np.corrcoef(ws[:-24], ws[24:])[0,1]:.3f}")

    fe = FeatureEngineer()
    feat = fe.build(df, target_col='wind_speed', horizons_h=HORIZONS)

    for h in HORIZONS:
        X, y, names, idx = feat[h]
        X_tr, X_te, y_tr, y_te, idx_tr, idx_te = fe.train_test_split_temporal(
            X, y, idx, test_frac=0.2)
        print(f"\n  ── Horizonte h={h}h ──  "
              f"n_features={len(names)}  n_train={len(y_tr)}  n_test={len(y_te)}")

        # Var del y_test (para comparar R² explícitamente)
        var_te = float(np.var(y_te))
        mse_pers_ref = float(np.mean(
            (y_te - BaselineModels.persistence(y_tr, y_te, h))**2))
        print(f"     Var(y_test)={var_te:.3f}  MSE(persistence)={mse_pers_ref:.3f}  "
              f"R²(persistence)={1-mse_pers_ref/var_te:.3f}")

        arena = ModelArena()
        res = arena.train_all(X_tr, y_tr, X_te, y_te,
                              idx_te, names, horizon_h=h,
                              enabled_models=MODELS_TO_RUN)

        rows = []
        for name, r in res.items():
            if 'metrics' not in r:
                continue
            m = r['metrics']
            rows.append((name, m['rmse'], m['r2'],
                         m.get('skill_vs_persistence'),
                         m.get('skill_vs_climatology')))
        rows.sort(key=lambda x: x[1])

        print(f"     {'Modelo':20s} {'RMSE':>7s} {'R²':>7s} "
              f"{'Skill_pers':>11s} {'Skill_clim':>11s}")
        for n, rm, r2, sp, sc in rows:
            sp_s = f"{sp*100:>9.1f}%" if sp is not None else "    —    "
            sc_s = f"{sc*100:>9.1f}%" if sc is not None else "    —    "
            print(f"     {n:20s} {rm:7.3f} {r2:7.3f}  {sp_s}  {sc_s}")


if __name__ == "__main__":
    for label, p in FILES:
        try:
            run_file(label, p)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"  ❌ Error: {e}")

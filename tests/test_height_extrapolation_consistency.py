from __future__ import annotations

import inspect
import unittest

import numpy as np
import pandas as pd

from core import kriging as kriging_module
from core import wind_stats as wind_stats_module
from core.energy import PowerCurve, WindToPowerEngine, extrapolate_wind_log


class LogLawDefaultZ0ConsistencyTests(unittest.TestCase):
    """core/kriging.py, core/wind_stats.py y core/energy.py implementan la
    misma ley logarítmica por separado. Antes de este fix, extrapolate_wind_log
    y WindToPowerEngine.run() asumían z0=0.001 (mar abierto/costa) por
    defecto mientras kriging.Z0 y wind_stats usaban 0.03 (campo abierto) —
    un mismo sitio daba velocidades distintas según qué motor lo procesara.
    Ver docs de la auditoría de tesis 2026-09-14, hallazgo 5."""

    def test_energy_log_default_matches_kriging_and_wind_stats(self):
        default_z0 = inspect.signature(extrapolate_wind_log).parameters['z0'].default
        self.assertEqual(default_z0, kriging_module.Z0)
        # wind_stats._log/_calc_hub hardcodean alpha, z0 = 0.143, 0.03 en
        # compute_wind_profile (línea ~581); no está expuesto como constante
        # de módulo, así que se fija aquí el valor esperado explícitamente.
        self.assertEqual(default_z0, 0.03)

    def test_energy_log_formula_matches_kriging_site_wind_stats(self):
        v_ref, z_ref, z_tgt = 7.5, 50.0, 80.0
        expected = v_ref * (np.log(z_tgt / kriging_module.Z0) /
                             np.log(z_ref / kriging_module.Z0))
        got = extrapolate_wind_log(v_ref, z_ref, z_tgt)
        self.assertAlmostEqual(got, expected, places=9)

    def test_wind_to_power_engine_log_extrapolation_uses_open_terrain_default(self):
        turbine = PowerCurve.from_builtin('V150_4.5')
        engine = WindToPowerEngine(turbine, n_turbines=1)
        df = pd.DataFrame({'wind_speed': [7.5] * 5})
        df_sim, _ = engine.run(df, ws_col='wind_speed', ws_height_m=50.0,
                               extrapolation='log')
        expected = extrapolate_wind_log(7.5, 50.0, turbine.hub_height_m)
        self.assertAlmostEqual(float(df_sim['ws_hub'].iloc[0]), expected, places=9)


if __name__ == '__main__':
    unittest.main()

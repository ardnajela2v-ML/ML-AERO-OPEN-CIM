from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from core.wind_stats import _calc_hub


class HubPowerDensityTests(unittest.TestCase):
    """Regresión: la densidad de potencia debe ser ½ρ·E[v³], no ½ρ·(E[v])³.
    Ambas fórmulas coinciden sólo si la serie tiene varianza cero; con
    variabilidad real E[v³] > (E[v])³ (Energy Pattern Factor > 1), así que
    la fórmula incorrecta subestima sistemáticamente la PD mostrada en la
    UI. Ver core/wind_stats.py:_calc_hub."""

    def test_power_density_uses_mean_of_cubes(self):
        rng = np.random.default_rng(42)
        ws = pd.Series(rng.weibull(2.0, size=8760) * 7.0)
        v_ref = float(ws.mean())

        result = _calc_hub(z_hub=80.0, z_ref=50.0, V_ref=v_ref,
                            alpha=0.15, z0_eff=0.03, ws_series=ws)

        sf_hell = result['sf_hellmann']
        ws_hub = ws * sf_hell
        rho = 1.225

        expected_pd = 0.5 * rho * float(np.mean(np.power(ws_hub, 3)))
        wrong_pd_cube_of_mean = 0.5 * rho * (v_ref * sf_hell) ** 3

        self.assertAlmostEqual(result['pd_hub_hellmann'], expected_pd, places=1)
        # E[v^3] > (E[v])^3 para cualquier serie con varianza > 0 (Jensen).
        self.assertGreater(result['pd_hub_hellmann'], wrong_pd_cube_of_mean)

    def test_zero_variance_series_makes_both_formulas_agree(self):
        ws = pd.Series([7.0] * 100)
        result = _calc_hub(z_hub=80.0, z_ref=50.0, V_ref=7.0,
                            alpha=0.15, z0_eff=0.03, ws_series=ws)
        sf_hell = result['sf_hellmann']
        rho = 1.225
        expected = 0.5 * rho * (7.0 * sf_hell) ** 3
        self.assertAlmostEqual(result['pd_hub_hellmann'], expected, delta=0.1)


if __name__ == '__main__':
    unittest.main()

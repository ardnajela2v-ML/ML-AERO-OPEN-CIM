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

class NativeObservationOverviewTests(unittest.TestCase):
    def test_pressure_is_pa_internally_and_hpa_for_display(self):
        from core.wind_stats import compute_site_overview
        frame = pd.DataFrame({'wind_speed': [10.0] * 12, 'temperature': [25.68] * 12,
                              'pressure': [99552.0] * 12},
                             index=pd.date_range('2018-01-01', periods=12, freq='10min'))
        overview = compute_site_overview(frame)
        self.assertAlmostEqual(overview['rho_used'], 1.16, delta=0.02)
        self.assertGreater(overview['power_density_w_m2'], 570)
        self.assertEqual(overview['pressure_mean'], 995.52)
        self.assertEqual(overview['pressure_mean_unit'], 'hPa')

    def test_ten_minute_records_are_not_counted_as_hours(self):
        from core.wind_stats import compute_site_overview, compute_extreme_events
        frame = pd.DataFrame({'wind_speed': [16.0] * 12},
                             index=pd.date_range('2018-01-01', periods=12, freq='10min'))
        # Drop one timestamp: a missing observation must not count as exposure.
        frame = frame.drop(frame.index[5])
        overview = compute_site_overview(frame, rho=1.2)
        self.assertEqual(overview['time_resolution_min'], 10)
        self.assertEqual(overview['h_above_15ms'], 1.83)
        self.assertEqual(overview['pct_above_15ms'], 100)
        monthly = compute_extreme_events(frame, thresholds=[15])['series'][0]
        self.assertEqual(monthly['total'], 1.83)
        self.assertEqual(monthly['counts'][0], 1.83)

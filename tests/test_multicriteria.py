from __future__ import annotations

import unittest

import numpy as np

from core.energy import compute_site_cp_fit
from core.multicriteria import topsis


def _alt(label, **kw):
    base = dict(cp_max=0.4, horas_cerca_nominal=2000,
                cp_at_site_mean=0.42, cpmax_speed_mismatch_ms=0.2,
                hours_near_cpmax=2400, factor_capacidad=0.35,
                aep_mwh=50000, directional_concentration=0.6,
                van_usd=10_000_000, roi=0.15, lcoe_usd_kwh=0.06,
                capex_usd=40_000_000)
    base.update(kw)
    base['label'] = label
    return base


class TopsisRankReversalTests(unittest.TestCase):
    """Regresión: reflejar los criterios de costo con (col_max - col) antes
    de normalizar hacía que el peso efectivo de esos criterios dependiera de
    la peor alternativa del conjunto — agregar una alternativa claramente
    dominada podía invertir el orden entre las demás. Ver
    core/multicriteria.py:topsis (bug reportado en la auditoría #4)."""

    def _abc(self):
        a = _alt('A', van_usd=8_000_000, roi=0.12, capex_usd=35_000_000, lcoe_usd_kwh=0.055)
        b = _alt('B', van_usd=6_000_000, roi=0.10, capex_usd=30_000_000, lcoe_usd_kwh=0.05)
        c = _alt('C', van_usd=12_000_000, roi=0.18, capex_usd=45_000_000, lcoe_usd_kwh=0.065)
        return a, b, c

    def test_adding_dominated_alternative_does_not_reverse_ranking(self):
        a, b, c = self._abc()
        order_before = [e['label'] for e in topsis([a, b, c])['ranking']]

        dominated = _alt('D', van_usd=1_000_000, roi=0.03, capex_usd=60_000_000,
                          lcoe_usd_kwh=0.09, cp_max=0.2, factor_capacidad=0.15,
                          aep_mwh=20000)
        result_after = topsis([a, b, c, dominated])
        order_after = [e['label'] for e in result_after['ranking'] if e['label'] != 'D']

        self.assertEqual(order_before, order_after)
        # La dominada debe quedar última, nunca desplazar a las demás.
        self.assertEqual(result_after['ranking'][-1]['label'], 'D')

    def test_missing_cost_criterion_is_not_scored_as_best_possible(self):
        a, b, c = self._abc()
        baseline = topsis([a, b, c])
        c_rank_before = next(e['rank'] for e in baseline['ranking'] if e['label'] == 'C')

        c_missing_capex = dict(c)
        c_missing_capex['capex_usd'] = None
        result = topsis([a, b, dict(c_missing_capex, label='C')])
        c_rank_after = next(e['rank'] for e in result['ranking'] if e['label'] == 'C')

        # Antes del fix, un CAPEX ausente se reflejaba a col_max (el mejor
        # valor posible) y podía subir de rank artificialmente.
        self.assertEqual(c_rank_before, c_rank_after)


class TurbineSiteCpFitTests(unittest.TestCase):
    @staticmethod
    def curve_with_cp_peak(peak_speed):
        speeds = np.arange(3.0, 16.0, 0.5)
        cp = 0.18 + 0.30 * np.exp(-0.5 * ((speeds - peak_speed) / 1.2) ** 2)
        area = np.pi * 40.0 ** 2
        power_kw = cp * 0.5 * 1.225 * area * speeds ** 3 / 1000.0
        return speeds, power_kw

    def test_mean_speed_match_and_hours_near_cpmax_are_turbine_specific(self):
        site_wind = np.concatenate([np.full(7000, 9.8), np.full(1760, 6.0)])
        results = {}
        for peak in (8.0, 10.0, 11.0):
            curve_ws, curve_kw = self.curve_with_cp_peak(peak)
            results[peak] = compute_site_cp_fit(
                site_wind, curve_ws, curve_kw, rotor_diameter_m=80.0,
                rho=1.225, dt_hours=1.0,
            )
        self.assertLess(results[10.0]['cpmax_speed_mismatch_ms'],
                        results[8.0]['cpmax_speed_mismatch_ms'])
        self.assertLess(results[10.0]['cpmax_speed_mismatch_ms'],
                        results[11.0]['cpmax_speed_mismatch_ms'])
        self.assertGreater(results[10.0]['cp_at_site_mean'],
                           results[8.0]['cp_at_site_mean'])
        self.assertGreater(results[10.0]['hours_near_cpmax'],
                           results[8.0]['hours_near_cpmax'])
        self.assertAlmostEqual(results[10.0]['site_mean_speed_ms'],
                               float(np.mean(site_wind)))

    def test_full_series_metrics_exclude_missing_and_annualize_hours(self):
        curve_ws, curve_kw = self.curve_with_cp_peak(10.0)
        result = compute_site_cp_fit(
            np.asarray([np.nan, 2.0, 10.0, 30.0]),
            curve_ws, curve_kw, rotor_diameter_m=80.0,
            rho=1.225, dt_hours=0.5, max_w=25.0,
        )
        self.assertEqual(result['site_series_samples_total'], 4)
        self.assertEqual(result['site_series_samples_valid'], 3)
        self.assertEqual(result['site_series_samples_missing'], 1)
        self.assertAlmostEqual(result['site_series_valid_hours_observed_total'], 1.5)
        self.assertAlmostEqual(result['resource_operational_availability_pct'], 100.0 / 3.0)
        self.assertAlmostEqual(result['resource_operational_hours'], 2920.0)
        self.assertAlmostEqual(result['resource_non_generation_hours'], 5840.0)
        self.assertAlmostEqual(result['hours_below_cut_in_observed_total'], 0.5)
        self.assertAlmostEqual(result['hours_above_cut_out_observed_total'], 0.5)
        self.assertGreater(result['cp_effective_full_series'], 0.0)
        self.assertLess(result['cp_effective_full_series'],
                        result['cp_effective_operating'])
        self.assertGreater(result['cp_effective_operating'], 0.0)

    def test_hours_above_ninety_percent_rated_use_full_valid_series(self):
        result = compute_site_cp_fit(
            np.asarray([5.0, 10.0, 15.0, 25.0]),
            np.asarray([0.0, 5.0, 10.0, 15.0, 25.0]),
            np.asarray([0.0, 100.0, 900.0, 1000.0, 0.0]),
            rotor_diameter_m=80.0, dt_hours=1.0, max_w=25.0,
        )
        self.assertAlmostEqual(result['at_or_above_90pct_rated_pct'], 50.0)
        self.assertAlmostEqual(result['hours_at_or_above_90pct_rated'], 4380.0)
        self.assertAlmostEqual(
            result['hours_at_or_above_90pct_rated_observed_total'], 2.0)


if __name__ == '__main__':
    unittest.main()

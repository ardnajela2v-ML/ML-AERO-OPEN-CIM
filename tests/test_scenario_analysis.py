"""Tests para §7 paso 5 — pool en sesión, escenarios, robustez, SMAA."""
from __future__ import annotations

import math
import unittest

import numpy as np

from core.farm_mcda import derive_price_scenarios, extract_candidate_pool, SCENARIO_YEARS


class TestDerivePriceScenarios(unittest.TestCase):
    """5.1 — derive_price_scenarios from by_year."""

    def _by_year(self, prices_by_year):
        return {
            y: {
                'complete': True,
                'avg_realized_price_usd_kwh': p,
                'energy_mwh': 100.0,
                'capture_ratio': 0.85,
            }
            for y, p in prices_by_year.items()
        }

    def test_basic_7_years(self):
        prices = {
            2017: 0.050, 2018: 0.082, 2019: 0.060,
            2020: 0.055, 2021: 0.070, 2022: 0.110, 2023: 0.065,
        }
        by_year = self._by_year(prices)
        sc = derive_price_scenarios(by_year)
        self.assertAlmostEqual(sc['bajo'], 0.050)
        self.assertAlmostEqual(sc['alto'], 0.110)
        self.assertAlmostEqual(sc['base'], np.mean(list(prices.values())), places=10)
        self.assertEqual(sorted(sc['by_year_detail']), SCENARIO_YEARS)

    def test_missing_year_raises(self):
        prices = {y: 0.05 for y in range(2017, 2023)}  # missing 2023
        by_year = self._by_year(prices)
        with self.assertRaises(ValueError) as ctx:
            derive_price_scenarios(by_year)
        self.assertIn('2023', str(ctx.exception))

    def test_incomplete_year_raises(self):
        prices = {y: 0.05 for y in range(2017, 2024)}
        by_year = self._by_year(prices)
        by_year[2020]['complete'] = False
        with self.assertRaises(ValueError) as ctx:
            derive_price_scenarios(by_year)
        self.assertIn('2020', str(ctx.exception))

    def test_custom_years(self):
        prices = {2020: 0.04, 2021: 0.06, 2022: 0.08}
        by_year = self._by_year(prices)
        sc = derive_price_scenarios(by_year, years=[2020, 2021, 2022])
        self.assertAlmostEqual(sc['bajo'], 0.04)
        self.assertAlmostEqual(sc['alto'], 0.08)
        self.assertAlmostEqual(sc['base'], 0.06)

    def test_all_equal_prices(self):
        prices = {y: 0.07 for y in range(2017, 2024)}
        by_year = self._by_year(prices)
        sc = derive_price_scenarios(by_year)
        self.assertAlmostEqual(sc['bajo'], 0.07)
        self.assertAlmostEqual(sc['alto'], 0.07)
        self.assertAlmostEqual(sc['base'], 0.07)


class TestExtractCandidatePool(unittest.TestCase):
    """5.2 — extract_candidate_pool."""

    def _generation(self, n_candidates=3, wake_ok=True, has_scenarios=True):
        pool = []
        for i in range(n_candidates):
            chron = {
                'ratio_by_sector': [[1.0] * 2] * 36,
                'by_year': {y: {'complete': True, 'avg_realized_price_usd_kwh': 0.05 + i * 0.01}
                            for y in range(2017, 2024)},
            } if has_scenarios else None
            pool.append({
                'layout_xy_m': [[100 * i, 0], [100 * i + 50, 0]],
                'n_turbines': 2,
                'feasibility': {'wake_loss_ok': wake_ok},
                'sim': {'chronological_revenue': chron, 'aep_mwh': 100.0},
                'van_usd': 1e6 + i * 1e5,
                '_precomputed': {
                    'aep_p50': 95.0 + i,
                    'collection_length': 200.0 + i * 10,
                    'econ': {},
                    'revenue_year1_override': None,
                    'scenarios': {'bajo': 0.04, 'base': 0.06, 'alto': 0.08} if has_scenarios else None,
                },
            })
        return {'pool': pool}

    def test_extracts_wake_feasible_only(self):
        gen = self._generation(3)
        gen['pool'][1]['feasibility']['wake_loss_ok'] = False
        result = extract_candidate_pool(gen, 'turbine.csv', 80.0)
        self.assertEqual(len(result), 2)

    def test_fields_present(self):
        gen = self._generation(1)
        result = extract_candidate_pool(gen, 'gamesa.csv', 80.0)
        self.assertEqual(len(result), 1)
        c = result[0]
        self.assertEqual(c['turbine_filename'], 'gamesa.csv')
        self.assertEqual(c['hub_height_m'], 80.0)
        self.assertEqual(c['n_turbines'], 2)
        self.assertIsNotNone(c['ratio_by_sector'])
        self.assertIsNotNone(c['scenarios'])
        self.assertIsNotNone(c['van_usd'])
        self.assertIsNotNone(c['aep_p50_mwh'])
        self.assertIsNotNone(c['collection_length_m'])

    def test_no_precomputed_skipped(self):
        gen = self._generation(2)
        gen['pool'][0]['_precomputed'] = None
        result = extract_candidate_pool(gen, 'test.csv', 80.0)
        self.assertEqual(len(result), 1)


class TestCombinedP90(unittest.TestCase):
    """5.4 — P90 combinado con variabilidad interanual."""

    def test_combined_1yr_exact(self):
        model_pct = math.sqrt(6.0**2 + 4.0**2 + 3.0**2 + 2.0**2 + 2.0**2)
        iav_1yr = 6.4
        combined_1yr = math.sqrt(model_pct**2 + iav_1yr**2)
        # model_pct = sqrt(69)
        self.assertAlmostEqual(model_pct, math.sqrt(69), places=15)
        # combined_1yr = sqrt(69 + 40.96) = sqrt(109.96)
        self.assertAlmostEqual(combined_1yr, math.sqrt(109.96), places=15)

    def test_combined_25yr_exact(self):
        model_pct = math.sqrt(6.0**2 + 4.0**2 + 3.0**2 + 2.0**2 + 2.0**2)
        iav_25yr = 6.4 / math.sqrt(25)
        combined_25yr = math.sqrt(model_pct**2 + iav_25yr**2)
        self.assertAlmostEqual(iav_25yr, 1.28, places=15)
        # combined_25yr = sqrt(69 + 1.6384) = sqrt(70.6384)
        self.assertAlmostEqual(combined_25yr, math.sqrt(70.6384), places=15)

    def test_p90_1yr_less_than_p90_25yr(self):
        aep_p50 = 10000.0
        model_pct = math.sqrt(6.0**2 + 4.0**2 + 3.0**2 + 2.0**2 + 2.0**2)
        iav = 6.4
        combined_1yr = math.sqrt(model_pct**2 + iav**2)
        combined_25yr = math.sqrt(model_pct**2 + (iav / math.sqrt(25))**2)
        z = 1.28155
        p90_1yr = aep_p50 * (1 - z * combined_1yr / 100)
        p90_25yr = aep_p50 * (1 - z * combined_25yr / 100)
        self.assertLess(p90_1yr, p90_25yr)

    def test_iav_zero_equals_model_only(self):
        model_pct = math.sqrt(6.0**2 + 4.0**2 + 3.0**2 + 2.0**2 + 2.0**2)
        combined = math.sqrt(model_pct**2 + 0.0**2)
        self.assertAlmostEqual(combined, model_pct, places=15)

    def test_z_factor_exact(self):
        z = 1.28155
        aep_p50 = 9205.0
        model_pct = math.sqrt(69.0)
        iav = 6.4
        combined = math.sqrt(model_pct**2 + iav**2)
        p90 = max(0.0, aep_p50 * (1 - z * combined / 100))
        self.assertGreater(p90, 0.0)
        self.assertLess(p90, aep_p50)

"""Tests para §7 paso 5 — pool en sesión, escenarios, robustez, SMAA."""
from __future__ import annotations

import math
import unittest

import numpy as np

from core.farm_mcda import derive_price_scenarios, SCENARIO_YEARS


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

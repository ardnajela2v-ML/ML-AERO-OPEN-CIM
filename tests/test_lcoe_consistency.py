from __future__ import annotations

import unittest

import numpy as np

from core.energy import TechnoEconomicAnalyzer, PowerCurve
from core.cost_model import CostModel
from core.kriging import turbine_site_economics


def _annuity(rate: float, years: int) -> float:
    return (1 - (1 + rate) ** -years) / rate


class LcoeDiscountingTests(unittest.TestCase):
    """Regresión: LCOE debe ser SIEMPRE PV(costos)/PV(energía) — nunca la
    suma sin descontar (CAPEX + ΣOPEX)/Σenergía. La auditoría encontró tres
    definiciones distintas conviviendo (farm_mcda.py correcta; energy.py y
    kriging.py sin descontar, con -41% a -48% de sesgo). Ver
    core/energy.py:TechnoEconomicAnalyzer.analyze y
    core/kriging.py:turbine_site_economics."""

    def test_energy_py_lcoe_matches_present_value_formula(self):
        turbine = PowerCurve.from_builtin('V150_4.5')
        n_turbines = 10
        cost_model = CostModel.from_defaults()
        analyzer = TechnoEconomicAnalyzer(cost_model=cost_model)

        capex = analyzer.capex_breakdown(turbine, n_turbines)
        total_capex = capex['total_capex_usd']
        annual_opex = cost_model.opex_annual_usd(total_capex)
        annual_energy_kwh = 100_000_000
        rate, years = 0.08, 25

        result = analyzer.analyze(turbine, n_turbines, annual_energy_kwh,
                                   energy_price_usd_kwh=0.06,
                                   discount_rate_pct=rate * 100,
                                   project_years=years)

        annuity = _annuity(rate, years)
        pv_energy = sum(annual_energy_kwh * 0.997 ** (year - 1) / (1 + rate) ** year
                        for year in range(1, years + 1))
        expected_lcoe = ((total_capex + annual_opex * annuity
                          + total_capex * 0.03 / (1 + rate) ** years) / pv_energy)
        undiscounted_bug = ((total_capex + annual_opex * years) /
                             (annual_energy_kwh * years))

        self.assertAlmostEqual(result['lcoe_usd_kwh'], expected_lcoe, places=4)
        # El LCOE correcto (descontado) debe ser mayor que el de la fórmula
        # sin descontar — descontar castiga la energía futura más de lo que
        # castiga el CAPEX inicial (que ya está en t=0 sin descuento).
        self.assertGreater(result['lcoe_usd_kwh'], undiscounted_bug)

    def test_kriging_py_lcoe_matches_present_value_formula(self):
        n = 8760
        ws_hub = np.full(n, 8.0)
        rho = np.full(n, 1.15)
        curve_x = np.array([0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 20, 25])
        curve_y = np.array([0, 0, 50, 150, 300, 500, 800, 1200, 1700, 2200,
                             2500, 2500, 2500, 2500, 0])
        rate, years = 0.08, 25

        res = turbine_site_economics(
            ws_hub, rho, curve_x, curve_y, rotor_m=100, p_nom_kw=2500,
            demand_mwh=200_000, tasa=rate, price_usd_kwh=0.06, anios=years,
        )

        ctot = res['capex_usd']
        cm = res['opex_usd_anual']
        ekwh = res['aep_mwh'] * 1000.0
        annuity = _annuity(rate, years)
        pv_energy = sum(ekwh * 0.997 ** (year - 1) / (1 + rate) ** year
                        for year in range(1, years + 1))
        expected_lcoe = (ctot + cm * annuity + ctot * 0.03 / (1 + rate) ** years) / pv_energy
        undiscounted_bug = (ctot + cm * years) / (ekwh * years)

        self.assertAlmostEqual(res['lcoe_usd_kwh'], expected_lcoe, places=4)
        self.assertGreater(res['lcoe_usd_kwh'], undiscounted_bug)


if __name__ == '__main__':
    unittest.main()

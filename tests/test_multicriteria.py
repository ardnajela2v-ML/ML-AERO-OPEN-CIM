from __future__ import annotations

import unittest

from core.multicriteria import topsis


def _alt(label, **kw):
    base = dict(cp_max=0.4, horas_cerca_nominal=2000, factor_capacidad=0.35,
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


if __name__ == '__main__':
    unittest.main()

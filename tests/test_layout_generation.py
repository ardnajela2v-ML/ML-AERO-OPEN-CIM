import unittest

import numpy as np

from core.energy import PowerCurve
from core.farm import simulate_wind_farm
from core.layout_generation import (
    LayoutConstraints,
    generate_feasible_layouts_grid,
    select_technology_champion,
)
from core.multicriteria import LAYOUT_WEIGHTS, rank_layout_alternatives


class LayoutGenerationTests(unittest.TestCase):
    """core.layout_generation: paso 6 del framework de tesis (generación de
    layouts factibles), separado de la selección de campeón y de TOPSIS.
    """

    def setUp(self):
        self.turbine = PowerCurve.from_file('aerogeneradores/gamesa21_p2000_r45.csv')
        self.polygon = [
            {'lat': 16.500, 'lon': -95.000}, {'lat': 16.500, 'lon': -94.990},
            {'lat': 16.510, 'lon': -94.990}, {'lat': 16.510, 'lon': -95.000},
        ]
        self.ws = np.full(200, 8.0)
        self.wd = np.full(200, 270.0)

    def _evaluate_layout(self, points):
        return simulate_wind_farm(points, self.turbine, self.ws, self.wd,
                                  wake_model='jensen', k_wake=0.06, ct=0.8)

    def _generate(self, max_wake_loss_pct=15.0, **overrides):
        kwargs = dict(
            polygon=self.polygon, turbine=self.turbine, ws=self.ws, wd=self.wd,
            wind_grid=None, fallback_wind_ms=8.0, rho=1.225, exclusions=None,
            boundary_setback_m=0.0, min_spacing_d=3.0, s_para_range=(3, 4, 5),
            s_perp_range=(3, 4), wake_model='jensen', k_wake=0.06, ct=0.8,
            evaluate_layout=self._evaluate_layout,
            constraints=LayoutConstraints(max_wake_loss_pct=max_wake_loss_pct),
            candidate_limit_per_n=0,
        )
        kwargs.update(overrides)
        return generate_feasible_layouts_grid(**kwargs)

    def test_returns_multiple_n_not_just_max(self):
        generation = self._generate()
        distinct_n = {c['n_turbines'] for c in generation['pool']}
        self.assertGreater(len(distinct_n), 1,
                           'debe explorar más de un N, no colapsar a uno solo')
        self.assertGreater(generation['max_n_fit'], max(distinct_n) - 1)

    def test_all_families_are_explicitly_represented_in_pool(self):
        # Ninguna familia debe quedar fuera del pool por un ranking de score
        # implícito -- Alineado, Tresbolillo y Greedy (vértice) deben
        # aparecer como candidatos explícitos y auditables (family_summary /
        # layout_config['family']), no una decisión silenciosa de cuál
        # puntuó mejor.
        generation = self._generate()
        families = {c['layout_config'].get('family') for c in generation['pool']}
        self.assertEqual(families, {'aligned', 'staggered', 'greedy'})
        for family in ('aligned', 'staggered', 'greedy'):
            self.assertIn(family, generation['max_n_fit_by_family'])
            self.assertGreater(generation['max_n_fit_by_family'][family], 0)

    def test_pool_entries_respect_min_spacing(self):
        generation = self._generate()
        D = self.turbine.rotor_diameter_m
        min_sep_m = 3.0 * D
        for candidate in generation['pool']:
            points = np.asarray(candidate['layout_xy_m'], dtype=float)
            for i in range(len(points)):
                for j in range(i + 1, len(points)):
                    distance = np.linalg.norm(points[i] - points[j])
                    self.assertGreaterEqual(
                        distance, min_sep_m - 1e-6,
                        f'turbinas más cerca que la separación mínima en N={candidate["n_turbines"]}')

    def test_feasibility_flags_match_wake_limit(self):
        generation = self._generate()
        for candidate in generation['pool']:
            expected_ok = candidate['wake_loss_pct'] <= 15.0 + 1e-9
            self.assertEqual(candidate['feasibility']['feasible'], expected_ok)
            self.assertEqual(candidate['feasibility']['wake_loss_ok'], expected_ok)

    def test_explicit_n_range_is_honoured_without_auto_stop(self):
        generation = self._generate(n_range=(1, 2))
        distinct_n = {c['n_turbines'] for c in generation['pool']}
        self.assertTrue(distinct_n.issubset({1, 2}))

    def test_noise_gate_rejects_candidates_that_exceed_receptor_limit(self):
        # Receptor pegado al polígono, límite muy estricto: todo candidato
        # generado debe quedar marcado no factible por ruido.
        receptor = {'lat': 16.500, 'lon': -95.0005, 'type': 'vivienda', 'period': 'night'}
        generation = self._generate(
            max_wake_loss_pct=99.0,
            constraints=LayoutConstraints(
                max_wake_loss_pct=99.0, receptor_points=[receptor],
                noise_limit_dba={'day': 55.0, 'night': 20.0}),
        )
        self.assertGreater(len(generation['pool']), 0)
        self.assertTrue(all(not c['feasibility']['noise_ok'] for c in generation['pool']))
        self.assertTrue(all('ruido_excede_limite' in c['feasibility']['infeasibility_reasons']
                            for c in generation['pool']))

    def test_shadow_flicker_gate_rejects_candidates_that_exceed_receptor_limit(self):
        # Polígono pequeño (~300m) para que CUALQUIER posición de la única
        # turbina factible quede, por construcción, dentro del radio de
        # influencia (~10D=900m) de un receptor justo al lado -- así el
        # resultado no depende de dónde exactamente caiga la turbina.
        tiny_polygon = [
            {'lat': 16.5000, 'lon': -95.0000}, {'lat': 16.5000, 'lon': -94.9973},
            {'lat': 16.5027, 'lon': -94.9973}, {'lat': 16.5027, 'lon': -95.0000},
        ]
        receptor = {'lat': 16.5013, 'lon': -95.0015, 'type': 'vivienda'}
        generation = self._generate(
            polygon=tiny_polygon, max_wake_loss_pct=99.0, n_range=(1, 1),
            constraints=LayoutConstraints(
                max_wake_loss_pct=99.0, receptor_points=[receptor],
                shadow_flicker_limit_hours_year=0.001),  # límite casi imposible de cumplir
        )
        self.assertGreater(len(generation['pool']), 0)
        self.assertTrue(all(c.get('shadow_flicker_evaluation') is not None for c in generation['pool']))
        self.assertTrue(all(not c['feasibility']['shadow_flicker_ok'] for c in generation['pool']))
        self.assertTrue(all('shadow_flicker_excede_limite' in c['feasibility']['infeasibility_reasons']
                            for c in generation['pool']))

    def test_noise_repair_succeeds_and_moves_turbine_when_room_allows(self):
        # Receptor cerca de donde cae la única turbina factible (N=1), con
        # amplio espacio libre en el polígono en la dirección opuesta: la
        # reparación (mover la turbina, revalidando ruido Y estela en cada
        # paso) debe lograr que el candidato termine factible, con una
        # geometría DISTINTA a la original.
        receptor = {'lat': 16.50722, 'lon': -94.9894, 'type': 'vivienda', 'period': 'night'}
        generation = self._generate(
            max_wake_loss_pct=15.0, n_range=(1, 1),
            constraints=LayoutConstraints(
                max_wake_loss_pct=15.0, receptor_points=[receptor],
                noise_limit_dba={'day': 55.0, 'night': 40.0}),
        )
        # N=1 ahora corre por cada familia (aligned/staggered/greedy) por
        # separado -- el pool trae una entrada por familia que sí colocó su
        # única turbina, no una sola global. Todas deben terminar factibles
        # por ruido, pero no todas necesariamente vía reparación: greedy
        # siembra en el vértice más a barlovento, que puede caer ya lejos
        # del receptor sin necesitar mover nada.
        self.assertGreater(len(generation['pool']), 0)
        any_repaired = False
        for candidate in generation['pool']:
            self.assertTrue(candidate['feasibility']['noise_ok'])
            self.assertTrue(candidate['feasibility']['feasible'])
            self.assertLessEqual(candidate['noise_evaluation']['max_receptor_dba'], 40.0 + 1e-6)
            if candidate['repaired_for']:
                self.assertIn('noise', candidate['repaired_for'])
                self.assertNotAlmostEqual(candidate['layout_xy_m'][0][0], 529.9547182713359, places=1)
                any_repaired = True
        self.assertTrue(any_repaired, 'al menos una familia debía necesitar la reparación de ruido')

    def test_noise_evaluation_stays_consistent_with_layout_when_repair_fails(self):
        # Regresión: cuando la reparación NO logra cumplir el límite dentro
        # del presupuesto de pasos, noise_evaluation debe seguir
        # describiendo la MISMA posición reportada en layout_xy_m -- nunca
        # el Lp de un movimiento intentado y luego descartado.
        # Turbina ya pegada al borde del polígono más lejano del receptor:
        # no hay hacia dónde moverla, y el límite es demasiado estricto
        # para lograrse con lo poco que sí se puede mover -- la reparación
        # debe fallar de forma determinista.
        receptor = {'lat': 16.500, 'lon': -95.000, 'type': 'vivienda', 'period': 'night'}
        generation = self._generate(
            max_wake_loss_pct=15.0, n_range=(1, 1),
            constraints=LayoutConstraints(
                max_wake_loss_pct=15.0, receptor_points=[receptor],
                noise_limit_dba={'day': 55.0, 'night': 25.0}),
        )
        candidate = generation['pool'][0]
        self.assertFalse(candidate['feasibility']['noise_ok'])
        self.assertEqual(candidate['repaired_for'], [])
        # El Lp reportado debe corresponder EXACTAMENTE a layout_xy_m tal
        # como quedó en el candidato (sin mover), recalculado de forma
        # independiente -- si la reparación descartada hubiera contaminado
        # noise_evaluation (el bug corregido), este valor no coincidiría.
        from core.acoustic_noise import evaluate_noise_constraint
        rx, ry = self._receptor_xy_for(receptor)
        direct = evaluate_noise_constraint(
            candidate['layout_xy_m'], self.turbine,
            [{'x': rx, 'y': ry, 'lat': receptor['lat'], 'lon': receptor['lon'],
              'type': receptor['type'], 'period': receptor['period']}],
            {'day': 55.0, 'night': 25.0})
        self.assertAlmostEqual(direct['max_receptor_dba'], candidate['noise_evaluation']['max_receptor_dba'], places=1)

    def _receptor_xy_for(self, receptor):
        from core.farm import _latlon_to_xy
        lats = [p['lat'] for p in self.polygon]
        lons = [p['lon'] for p in self.polygon]
        lat0 = sum(lats) / len(lats)
        lon0 = sum(lons) / len(lons)
        rx, ry = _latlon_to_xy([receptor['lat']], [receptor['lon']], lat0, lon0)
        return rx[0], ry[0]

    def test_noise_gate_is_a_noop_without_receptor_points(self):
        generation = self._generate()  # constraints por defecto: sin receptores
        self.assertTrue(all(c['feasibility']['noise_ok'] for c in generation['pool']))
        self.assertTrue(all(c.get('noise_evaluation') is None for c in generation['pool']))

    def test_zero_max_n_when_nothing_fits(self):
        tiny_polygon = [
            {'lat': 16.5000, 'lon': -95.0000}, {'lat': 16.5000, 'lon': -94.99999},
            {'lat': 16.50001, 'lon': -94.99999}, {'lat': 16.50001, 'lon': -95.0000},
        ]
        with self.assertRaises(ValueError):
            self._generate(polygon=tiny_polygon)


class SelectTechnologyChampionTests(unittest.TestCase):
    """Unit tests puros sobre la regla de selección de campeón, sin correr
    ninguna simulación real -- solo la lógica de core.layout_generation.
    select_technology_champion.
    """

    @staticmethod
    def _candidate(n_turbines, aep_mwh, wake_loss_pct, van_usd=None, feasible=True, reasons=None):
        candidate = {
            'n_turbines': n_turbines, 'wake_loss_pct': wake_loss_pct,
            'sim': {'aep_mwh': aep_mwh},
            'feasibility': {
                'wake_loss_ok': feasible, 'slope_ok': True,
                'noise_ok': True, 'shadow_flicker_ok': True,
                'feasible': feasible,
                'infeasibility_reasons': reasons or ([] if feasible else ['wake_loss_excede_limite']),
            },
        }
        if van_usd is not None:
            candidate['van_usd'] = van_usd
        return candidate

    def test_default_rule_picks_max_van_among_feasible(self):
        # AEP más alto (14) no gana: su VAN es peor (más CAPEX que ingreso
        # marginal) -- exactamente el caso que justifica usar VAN y no AEP.
        pool = [
            self._candidate(10, aep_mwh=5000.0, wake_loss_pct=4.0, van_usd=2_000_000.0),
            self._candidate(12, aep_mwh=5800.0, wake_loss_pct=6.0, van_usd=2_500_000.0),  # champion
            self._candidate(14, aep_mwh=6100.0, wake_loss_pct=9.0, van_usd=1_800_000.0),
        ]
        result = select_technology_champion(pool)
        self.assertEqual(result['champion']['n_turbines'], 12)
        self.assertEqual(result['rule'], 'max_van_feasible')

    def test_picks_max_value_among_feasible_with_explicit_value_key(self):
        pool = [
            self._candidate(10, aep_mwh=5000.0, wake_loss_pct=4.0),
            self._candidate(12, aep_mwh=5800.0, wake_loss_pct=6.0),  # champion
            self._candidate(14, aep_mwh=1000.0, wake_loss_pct=1.0, feasible=False),
        ]
        result = select_technology_champion(pool, value_key='aep_mwh')
        self.assertEqual(result['champion']['n_turbines'], 12)
        self.assertEqual(result['pool_size'], 3)
        self.assertEqual(result['n_feasible'], 2)

    def test_tie_break_prefers_lower_wake_loss(self):
        pool = [
            self._candidate(10, aep_mwh=5000.0, wake_loss_pct=4.5),
            self._candidate(11, aep_mwh=5000.0, wake_loss_pct=2.0),  # same AEP, less wake
        ]
        result = select_technology_champion(pool, value_key='aep_mwh')
        self.assertEqual(result['champion']['n_turbines'], 11)

    def test_require_feasibility_keys_selects_wake_only_champion_ignoring_noise(self):
        # Flujo de decisión ruido/flicker (discutido con el usuario): el
        # campeón "estela + VAN" puede violar ruido y aun así ser elegido
        # con require_feasibility_keys=('wake_loss_ok',) -- la evaluación
        # ambiental se reporta aparte, no bloquea esta selección primaria.
        noisy_but_best_van = self._candidate(12, aep_mwh=5800.0, wake_loss_pct=6.0, van_usd=3_000_000.0)
        noisy_but_best_van['feasibility']['noise_ok'] = False
        noisy_but_best_van['feasibility']['feasible'] = False
        noisy_but_best_van['feasibility']['infeasibility_reasons'] = ['ruido_excede_limite']
        quiet_but_worse_van = self._candidate(10, aep_mwh=5000.0, wake_loss_pct=4.0, van_usd=2_000_000.0)
        pool = [noisy_but_best_van, quiet_but_worse_van]

        wake_only = select_technology_champion(pool, require_feasibility_keys=('wake_loss_ok',))
        self.assertEqual(wake_only['champion']['n_turbines'], 12)

        full = select_technology_champion(pool)  # default: exige también ruido/flicker/pendiente
        self.assertEqual(full['champion']['n_turbines'], 10)

    def test_returns_none_when_pool_fully_infeasible(self):
        pool = [
            self._candidate(10, aep_mwh=5000.0, wake_loss_pct=9.0, feasible=False,
                            reasons=['wake_loss_excede_limite']),
            self._candidate(11, aep_mwh=6000.0, wake_loss_pct=11.0, feasible=False,
                            reasons=['wake_loss_excede_limite']),
        ]
        result = select_technology_champion(pool)
        self.assertIsNone(result['champion'])
        self.assertEqual(result['n_feasible'], 0)
        self.assertEqual(result['rejected_by_reason'], {'wake_loss_excede_limite': 2})


class RankLayoutAlternativesChampionCompatibilityTests(unittest.TestCase):
    """core.multicriteria.rank_layout_alternatives debe aceptar filas
    is_champion=True sin exigirles n_used==n_optimo_estela, y seguir
    aplicando la validación vieja a filas que no traen ese campo (análisis
    guardados antes de este cambio).
    """

    @staticmethod
    def _alternative(label, turbina, n_aerogeneradores, scale=1.0, **overrides):
        directions = {
            'lcoe_usd_kwh', 'capex_usd', 'capex_usd_per_installed_kw',
            'opex_lifetime_usd', 'opex_usd_per_installed_kw_year',
            'wake_loss_pct', 'aep_uncertainty_pct', 'curtailment_loss_pct',
            'collection_length_m', 'civil_complexity_index', 'payback_years',
            'discounted_payback_years',
        }
        row = {'label': label, 'feasible': True, 'turbina': turbina,
              'n_aerogeneradores': n_aerogeneradores, 'max_n_fisico': 20}
        for key in LAYOUT_WEIGHTS:
            row[key] = 1.0 / scale if key in directions else scale
        row.update(overrides)
        return row

    def test_champion_rows_do_not_need_exact_n_optimo_estela(self):
        # n_aerogeneradores no coincide con ningún n_optimo_estela declarado
        # (de hecho no se declara) -- la fila vieja habría sido rechazada por
        # 'layout_no_usa_cantidad_optima'; una fila is_champion debe pasar.
        alt = self._alternative('champion-a', 'modelo_a.csv', 11, scale=2.0,
                                is_champion=True)
        result = rank_layout_alternatives([alt])
        self.assertEqual(result['n'], 1)
        self.assertEqual(result['ranking'][0]['n_aerogeneradores'], 11)

    def test_legacy_rows_without_is_champion_keep_old_validation(self):
        partial = self._alternative('partial', 'modelo_a.csv', 9, scale=3.0,
                                    n_optimo_estela=10)
        result = rank_layout_alternatives([partial])
        self.assertEqual(result['n'], 0)
        self.assertEqual(result['excluded'][0]['reason'], 'layout_no_usa_cantidad_optima')

    def test_two_champions_of_different_technologies_both_rank(self):
        a = self._alternative('champion-a', 'modelo_a.csv', 11, scale=2.0, is_champion=True)
        b = self._alternative('champion-b', 'modelo_b.csv', 8, scale=1.5, is_champion=True)
        result = rank_layout_alternatives([a, b])
        self.assertEqual(result['n'], 2)
        self.assertEqual({row['turbina'] for row in result['ranking']},
                         {'modelo_a.csv', 'modelo_b.csv'})


if __name__ == '__main__':
    unittest.main()
